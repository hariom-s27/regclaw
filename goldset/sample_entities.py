"""Freeze source-family x within-family character-length terciles before S4.

Largest-remainder allocation with two slots per stratum ensures every stratum
appears in both splits. Sorted ids break length ties; seed is 20261018.
Existing freezes are never overwritten. Only ids/strata are tracked.
"""
import csv
import hashlib
import json
from pathlib import Path
import random
from collections import defaultdict, Counter

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261018


def family(doc):
    return ('FDA' if doc.startswith('fda-') else 'EU MDR' if doc == 'eu-mdr'
            else 'IMDRF' if doc.startswith('imdrf-') else 'India' if doc.startswith('in-')
            else 'EPA' if doc.startswith('epa-') else None)


def allocate(sizes, total, minimum):
    result = {k: minimum for k in sorted(sizes)}
    while sum(result.values()) < total:
        key = max((k for k in result if result[k] < sizes[k]),
                  key=lambda k: (sizes[k] * total / sum(sizes.values()) - result[k], k))
        result[key] += 1
    return result


def freeze(root=ROOT):
    root = Path(root)
    path = root / 'goldset/entity_gold_ids.csv'
    if path.exists():
        raise FileExistsError('Entity eval ids already frozen; refusing overwrite')
    clauses = json.loads((root / 'data/clauses.json').read_text(encoding='utf-8'))
    fixtures = '\n'.join(f.read_text(encoding='utf-8') for f in sorted((root / 'tests/fixtures').rglob('*')) if f.is_file())
    excluded = {c['id'] for c in clauses if c['id'] in fixtures}
    excluded.add(next(c['id'] for c in clauses if c['doc'] == 'fda-iso10993-1' and 100 <= len(c['text']) <= 800))
    groups, strata = defaultdict(list), defaultdict(list)
    for c in clauses:
        if c['id'] not in excluded:
            groups[family(c['doc'])].append(c)
    for source, group in sorted(groups.items()):
        if source is None:
            raise ValueError('Unknown source family')
        ordered = sorted(group, key=lambda c: (len(c['text']), c['id']))
        for i, c in enumerate(ordered):
            strata[source, ('short', 'medium', 'long')[min(2, 3*i//len(ordered))]].append(c['id'])
    sizes = {k: len(v) for k, v in strata.items()}
    counts = allocate(sizes, 50, 2)
    dev = allocate(counts, 30, 1)
    rng, rows = random.Random(SEED), []
    for key in sorted(strata):
        chosen = rng.sample(strata[key], counts[key])
        rng.shuffle(chosen)
        for i, cid in enumerate(chosen):
            rows.append(dict(clause_id=cid, split='dev' if i < dev[key] else 'holdout',
                             source=key[0], length_tercile=key[1]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['clause_id', 'split', 'source', 'length_tercile'])
        writer.writeheader(); writer.writerows(sorted(rows, key=lambda r: r['clause_id']))
    print('entity_gold_ids.csv sha256:', hashlib.sha256(path.read_bytes()).hexdigest())
    print('source/split:', dict(Counter((r['source'], r['split']) for r in rows)))
    return rows


if __name__ == '__main__':
    freeze()
