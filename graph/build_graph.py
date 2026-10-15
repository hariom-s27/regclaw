"""Batched MERGE loader and identical offline count plan. No full-text index."""
import argparse
import json
import os
from pathlib import Path

import yaml

from ner.merge import ENTITY_FIELDS, LABELS, indexed

ROOT = Path(__file__).resolve().parents[1]
ENTITY_LABELS = {'biological_effects': 'BiologicalEffect', 'chemicals': 'Chemical',
                 'device_types': 'DeviceType', 'regulatory_actors': 'RegulatoryActor',
                 'lifecycle_phases': 'LifecyclePhase'}
NODE_LABELS = ('Module', 'Standard', 'Clause', 'LifecyclePhase', 'RegulatoryAuthority',
               'Chemical', 'BiologicalEffect', 'DeviceType', 'RegulatoryActor')
REL_TYPES = ('BELONGS_TO', 'MENTIONS', 'REGULATED_BY', 'REFERENCES')


def node_query(label):
    if label not in NODE_LABELS:
        raise ValueError(f'Label not allowed: {label}')
    key = 'id' if label in ('Clause', 'Standard') else 'name'
    return f'UNWIND $rows AS row MERGE (n:{label} {{{key}: row.{key}}}) SET n += row'


def constraint_queries():
    return [f'CREATE CONSTRAINT {label.lower()}_unique IF NOT EXISTS FOR (n:{label}) REQUIRE n.'
            + ('id' if label in ('Clause', 'Standard') else 'name') + ' IS UNIQUE' for label in NODE_LABELS]


REL_QUERIES = {
    'BELONGS_TO': '''UNWIND $rows AS row
        MATCH (a) WHERE a.id = row.source AND
          ((row.source_label = 'Clause' AND a:Clause) OR (row.source_label = 'Standard' AND a:Standard))
        MATCH (b) WHERE (row.target_label = 'Standard' AND b:Standard AND b.id = row.target)
          OR (row.target_label = 'Module' AND b:Module AND b.name = row.target)
        MERGE (a)-[:BELONGS_TO]->(b)''',
    'MENTIONS': '''UNWIND $rows AS row MATCH (a:Clause {id: row.source})
        MATCH (b) WHERE b.name = row.target AND row.label IN labels(b)
        MERGE (a)-[:MENTIONS]->(b)''',
    'REGULATED_BY': '''UNWIND $rows AS row MATCH (a:Standard {id: row.source})
        MATCH (b:RegulatoryAuthority {name: row.target}) MERGE (a)-[:REGULATED_BY]->(b)''',
    'REFERENCES': '''UNWIND $rows AS row MATCH (a:Clause {id: row.source})
        MATCH (b:Standard {id: row.target}) MERGE (a)-[:REFERENCES]->(b)''',
}


def plan_graph(clauses, standards):
    cmap, smap = indexed(clauses, 'clauses'), indexed(standards, 'standards')
    docs = sorted({c['doc'] for c in clauses})
    if set(docs) - smap.keys():
        raise ValueError(f'Missing standards metadata: {set(docs) - smap.keys()}')
    nodes = {label: {} for label in NODE_LABELS}
    rels = {kind: set() for kind in REL_TYPES}
    nodes['Module']['Biocompatibility'] = {'name': 'Biocompatibility'}
    for doc in docs:
        s = smap[doc]
        if set(s) != {'id', 'number', 'title', 'edition', 'authority'}:
            raise ValueError(f'{doc}: invalid metadata fields')
        if s['authority'] not in {'FDA', 'EU', 'CDSCO', 'IMDRF', 'EPA'}:
            raise ValueError('Invalid regulatory authority')
        nodes['Standard'][doc] = {k: s[k] for k in ('id', 'number', 'title', 'edition')}
        nodes['RegulatoryAuthority'][s['authority']] = {'name': s['authority']}
        rels['BELONGS_TO'].add(('Standard', doc, 'Module', 'Biocompatibility'))
        rels['REGULATED_BY'].add((doc, s['authority']))
    refs_to_docs = {smap[doc]['number']: doc for doc in docs}
    for cid, c in cmap.items():
        obligation = c.get('obligation', 'NONE')
        if obligation not in LABELS:
            raise ValueError(f'{cid}: invalid obligation')
        nodes['Clause'][cid] = {k: c[k] for k in ('id', 'number', 'title', 'text')}
        nodes['Clause'][cid]['obligation'] = obligation
        rels['BELONGS_TO'].add(('Clause', cid, 'Standard', c['doc']))
        entities = c.get('entities', {})
        if set(entities) - set(ENTITY_FIELDS):
            raise ValueError('Entity type outside the fixed allow-list')
        for kind, values in entities.items():
            if not isinstance(values, list) or any(not isinstance(n, str) or not n.strip() for n in values):
                raise ValueError('Expected canonical entity strings')
            if kind == 'keywords':
                continue  # Contract has no Keyword label.
            label = ENTITY_LABELS[kind]
            for name in set(values):
                nodes[label][name] = {'name': name}
                rels['MENTIONS'].add((cid, label, name))
        for ref in set(c.get('iso_refs', [])):
            target = ref if ref in nodes['Standard'] else refs_to_docs.get(ref, ref)
            if target not in nodes['Standard']:
                nodes['Standard'][target] = {'id': target, 'number': target}
            rels['REFERENCES'].add((cid, target))
    edges = {
        'BELONGS_TO': [dict(source_label=a, source=b, target_label=c, target=d) for a, b, c, d in sorted(rels['BELONGS_TO'])],
        'MENTIONS': [dict(source=a, label=b, target=c) for a, b, c in sorted(rels['MENTIONS'])],
        'REGULATED_BY': [dict(source=a, target=b) for a, b in sorted(rels['REGULATED_BY'])],
        'REFERENCES': [dict(source=a, target=b) for a, b in sorted(rels['REFERENCES'])],
    }
    return {'nodes': {k: list(v.values()) for k, v in nodes.items()}, 'relationships': edges,
            'stub_standards': len(nodes['Standard']) - len(docs)}


def counts(plan):
    nodes = {k: len(v) for k, v in plan['nodes'].items()}
    rels = {k: len(v) for k, v in plan['relationships'].items()}
    return {'nodes': nodes, 'relationships': rels, 'total_nodes': sum(nodes.values()),
            'total_relationships': sum(rels.values()), 'stub_standards': plan['stub_standards']}


def load(driver, plan, batch_size=1000):
    if not 1 <= batch_size <= 1000:
        raise ValueError('Batch size must be 1..1000')
    # Validate the entire plan before executing any mutation.
    if set(plan['nodes']) != set(NODE_LABELS) or set(plan['relationships']) != set(REL_TYPES):
        raise ValueError('Unknown node label or relationship type')
    if any(r['label'] not in ENTITY_LABELS.values() for r in plan['relationships']['MENTIONS']):
        raise ValueError('MENTIONS target outside allow-list')
    created = {'nodes_created': 0, 'relationships_created': 0}
    for query in constraint_queries():
        driver.execute_query(query, database_='neo4j')
    statements = [(node_query(label), rows) for label, rows in plan['nodes'].items()]
    statements += [(REL_QUERIES[kind], rows) for kind, rows in plan['relationships'].items()]
    for query, rows in statements:
        for start in range(0, len(rows), batch_size):
            result = driver.execute_query(query, rows=rows[start:start+batch_size], database_='neo4j')
            summary = result.summary if hasattr(result, 'summary') else result[1]
            for key in created:
                created[key] += getattr(summary.counters, key)
    return created


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--load', action='store_true')
    parser.add_argument('--from-clauses', action='store_true')
    parser.add_argument('--input')
    parser.add_argument('--standards', default=ROOT / 'graph/standards.yaml')
    parser.add_argument('--verify-idempotent', action='store_true')
    parser.add_argument('--checks', action='store_true')
    args = parser.parse_args(argv)
    if args.dry_run and (args.verify_idempotent or args.checks):
        parser.error('--verify-idempotent and --checks require --load')
    path = args.input or ROOT / ('data/clauses.json' if args.from_clauses else 'data/enriched.json')
    rows = json.loads(Path(path).read_text(encoding='utf-8'))
    if args.from_clauses:
        rows = [{k: v for k, v in c.items() if k not in ('entities', 'iso_refs', 'obligation')} for c in rows]
    plan = plan_graph(rows, yaml.safe_load(Path(args.standards).read_text(encoding='utf-8')))
    print(json.dumps(counts(plan), indent=2))
    if args.dry_run:
        return 0
    from dotenv import load_dotenv
    from neo4j import GraphDatabase
    load_dotenv(ROOT / '.env')
    with GraphDatabase.driver(os.environ['NEO4J_URI'], auth=(os.environ.get('NEO4J_USER', 'neo4j'), os.environ['NEO4J_PASSWORD']),
                              notifications_disabled_classifications=['UNRECOGNIZED']) as driver:
        print('load:', load(driver, plan))
        reload_counts = None
        if args.verify_idempotent or args.checks:
            reload_counts = load(driver, plan)
            print('reload:', reload_counts)
            if args.verify_idempotent and not args.checks and any(reload_counts.values()):
                raise RuntimeError('Idempotent reload created nodes/relationships')
        if args.checks:
            from graph.checks import run
            checks = run(driver, counts(plan), reload_counts)
            return int(any(r['status'] == 'FAIL' for r in checks))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
