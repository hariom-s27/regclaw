"""Thirteen live graph audits; dry-run plans supply explicit thresholds."""
import json

from graph.build_graph import ENTITY_LABELS, NODE_LABELS

ENTITY_LIST = repr(list(ENTITY_LABELS.values()))
CHECKS = (
    ('Counts by label/type', '''MATCH (n) UNWIND labels(n) AS name
        RETURN 'nodes' AS kind, name, count(*) AS value UNION ALL
        MATCH ()-[r]->() RETURN 'relationships' AS kind, type(r) AS name, count(*) AS value''', 'within +/-5% of plan'),
    ('Orphan clauses', 'MATCH (c:Clause) WHERE NOT (c)-[:BELONGS_TO]->(:Standard) RETURN count(c) AS value', '0'),
    ('Standards without Module except stubs', '''MATCH (s:Standard) WHERE NOT (s)-[:BELONGS_TO]->(:Module)
        AND EXISTS { MATCH (:Clause)-[:BELONGS_TO]->(s) } RETURN count(s) AS value''', '0 non-stubs'),
    ('Clauses without MENTIONS', '''MATCH (c:Clause) RETURN count(c) AS total,
        sum(CASE WHEN NOT (c)-[:MENTIONS]->() THEN 1 ELSE 0 END) AS value''', 'share <=0.10; otherwise WARN'),
    ('Never-mentioned entities', f'''MATCH (n) WHERE any(l IN labels(n) WHERE l IN {ENTITY_LIST})
        AND NOT (:Clause)-[:MENTIONS]->(n) RETURN count(n) AS value''', '0'),
    ('Entity degree distribution', f'''UNWIND {ENTITY_LIST} AS label MATCH (n) WHERE label IN labels(n)
        OPTIONAL MATCH (:Clause)-[r:MENTIONS]->(n) WITH label, n, count(r) AS degree
        RETURN label, percentileCont(degree, 0.5) AS p50, percentileCont(degree, 0.9) AS p90,
        max(degree) AS maximum''', 'max <200; otherwise WARN stop-entity review'),
    ('Name collisions and singleton share', f'''UNWIND {ENTITY_LIST} AS label MATCH (n) WHERE label IN labels(n)
        WITH label, toLower(trim(n.name)) AS normalised, collect(n.name) AS names,
        sum(CASE WHEN size([(n)<-[:MENTIONS]-(c:Clause) | c]) = 1 THEN 1 ELSE 0 END) AS singletons
        RETURN label, sum(CASE WHEN size(names)>1 THEN 1 ELSE 0 END) AS value,
        toFloat(sum(singletons))/sum(size(names)) AS singleton_share''', '0 collisions; singleton share reported'),
    ('Empty text or null number', '''MATCH (c:Clause) WHERE c.text IS NULL OR trim(c.text) = '' OR c.number IS NULL
        RETURN count(c) AS value''', '0'),
    ('Duplicate MENTIONS', '''MATCH (c:Clause)-[r:MENTIONS]->(n) WITH c, n, count(r) AS edges
        WHERE edges > 1 RETURN coalesce(sum(edges-1),0) AS value''', '0'),
    ('Self references', '''MATCH (c:Clause)-[:BELONGS_TO]->(s:Standard)<-[:REFERENCES]-(c)
        RETURN count(c) AS value''', '0; otherwise WARN requires justification'),
    ('Orphan LifecyclePhase', 'MATCH (n:LifecyclePhase) WHERE NOT (n)--() RETURN count(n) AS value', '0'),
    ('Uniqueness constraints', 'SHOW CONSTRAINTS YIELD labelsOrTypes, properties, type RETURN labelsOrTypes, properties, type', 'all 9 uniqueness constraints'),
    ('Idempotent reload', 'RETURN $reload_nodes AS nodes_created, $reload_relationships AS relationships_created', '0 nodes, 0 relationships; must be measured'),
)


def evaluate(index, rows, plan, reload_counts=None):
    if index == 0:
        actual = {kind: {} for kind in ('nodes', 'relationships')}
        for r in rows:
            actual[r['kind']][r['name']] = r['value']
        gaps = {f'{kind}.{name}': actual[kind].get(name, 0) - expected
                for kind, values in ((k, plan[k]) for k in actual) for name, expected in values.items()
                if actual[kind].get(name, 0) != expected}
        extras = {f'{k}.{n}': v for k, values in actual.items() for n, v in values.items() if n not in plan[k]}
        gaps.update(extras)
        outside = any(abs(actual[k].get(n, 0)-v) > .05*v for k in actual for n, v in plan[k].items())
        return {'counts': actual, 'unexplained_gaps': gaps}, 'FAIL' if outside or extras else 'WARN' if gaps else 'PASS'
    if index == 3:
        r = rows[0]
        share = r['value'] / r['total'] if r['total'] else 0
        return {**r, 'share': share}, 'PASS' if share <= .1 else 'WARN'
    if index == 5:
        return rows, 'WARN' if any(r['maximum'] >= 200 for r in rows) else 'PASS'
    if index == 6:
        return rows, 'FAIL' if any(r['value'] for r in rows) else 'PASS'
    if index == 11:
        present = {(label, tuple(r['properties'])) for r in rows if 'UNIQUENESS' in r['type']
                   for label in r['labelsOrTypes']}
        missing = [label for label in NODE_LABELS if (label, ('id' if label in ('Clause', 'Standard') else 'name',)) not in present]
        return {'missing': missing, 'constraints': rows}, 'FAIL' if missing else 'PASS'
    if index == 12:
        return reload_counts, 'WARN' if reload_counts is None else 'FAIL' if any(reload_counts.values()) else 'PASS'
    value = rows[0]['value']
    return value, ('WARN' if index == 9 else 'FAIL') if value else 'PASS'


def run(driver, plan, reload_counts=None):
    output = []
    print('check | value | threshold | status')
    for i, (name, query, threshold) in enumerate(CHECKS):
        params = {} if i != 12 else {'reload_nodes': (reload_counts or {}).get('nodes_created'),
                                     'reload_relationships': (reload_counts or {}).get('relationships_created')}
        result = driver.execute_query(query, database_='neo4j', **params)
        records = result.records if hasattr(result, 'records') else result[0]
        rows = [dict(r) for r in records]
        value, status = evaluate(i, rows, plan, reload_counts)
        print(f'{name} | {json.dumps(value)} | {threshold} | {status}')
        output.append(dict(check=name, value=value, threshold=threshold, status=status))
    return output
