"""Validate exact fields, values, and unchanged source clauses."""
import argparse
from collections import Counter
import json
from pathlib import Path

from ner.merge import CLAUSE_FIELDS, ENRICHED_FIELDS, ENTITY_FIELDS, LABELS, ROOT, indexed
from ner.stdref import references


def strings(values):
    return isinstance(values, list) and all(isinstance(v, str) and v.strip() for v in values) and values == sorted(set(values))


def validate(rows, clauses):
    actual, source = indexed(rows, 'enriched'), indexed(clauses, 'clauses')
    if actual.keys() != source.keys():
        raise ValueError('enriched ids do not match clauses')
    for cid, row in actual.items():
        if set(row) != ENRICHED_FIELDS:
            raise ValueError(f'{cid}: unexpected field set')
        if any(row[k] != source[cid][k] for k in CLAUSE_FIELDS):
            raise ValueError(f'{cid}: source clause changed')
        if row['obligation'] not in LABELS:
            raise ValueError(f'{cid}: invalid obligation')
        if not strings(row['obligation_flags']) or not strings(row['iso_refs']):
            raise ValueError(f'{cid}: invalid flags/references')
        if any(references(ref)[0] != [ref] for ref in row['iso_refs']):
            raise ValueError(f'{cid}: noncanonical reference')
        if not isinstance(row['entities'], dict) or set(row['entities']) != set(ENTITY_FIELDS):
            raise ValueError(f'{cid}: invalid entity fields')
        if any(not strings(v) or any('_' in s for s in v) for v in row['entities'].values()):
            raise ValueError(f'{cid}: invalid canonical entity strings')
    result = {'clauses': len(rows), 'obligations': dict(Counter(r['obligation'] for r in rows)),
              'empty_entities': sum(not any(r['entities'].values()) for r in rows)}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('enriched', nargs='?', default=ROOT / 'data/enriched.json')
    parser.add_argument('--clauses', default=ROOT / 'data/clauses.json')
    args = parser.parse_args(argv)
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
    print('VALID:', json.dumps(validate(read(args.enriched), read(args.clauses))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
