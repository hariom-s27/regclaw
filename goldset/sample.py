"""Seeded stratification and blind annotation exports; never invent scarce labels."""
import csv
import json
from pathlib import Path
import random
import re
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
LABELS = ('SHALL', 'SHOULD', 'MAY', 'NONE')
DOC_TYPES = ('FDA', 'EU', 'INDIA', 'EPA', 'IMDRF')


def doc_type(doc):
    if doc.startswith('fda-'):
        return 'FDA'
    return {'eu-mdr': 'EU', 'in-mdr': 'INDIA', 'in-ep': 'INDIA',
            'epa-63o': 'EPA', 'imdrf-n47': 'IMDRF'}[doc]


def allocate(sizes, target=50, floor=6):
    """Constrained proportional allocation using largest remainders.

    Solve proportional quotas subject to floors, use largest remainders,
    then cap scarce cells and give their shortfalls to the largest pool.
    """
    target = min(target, sum(sizes.values()))
    nonempty = [k for k in sizes if sizes[k]]
    baseline = floor * len(nonempty)
    if baseline > target and sum(min(floor, sizes[k]) for k in nonempty) > target:
        raise ValueError('DESIGN CONFLICT: floors exceed the available row target')
    if baseline > target:
        # A scarce entire row may not even support all nominal floors.
        if target == sum(sizes.values()):
            return dict(sizes)
        raise ValueError('Infeasible floors')
    quotas, active, seats = {}, set(nonempty), target
    while active:
        total = sum(sizes[k] for k in active)
        low = {k for k in active if seats * sizes[k] / total < floor}
        if not low:
            quotas.update({k: seats * sizes[k] / total for k in active})
            break
        for k in low:
            quotas[k] = floor
        seats -= floor * len(low)
        active -= low
    result = {k: int(quotas.get(k, 0)) for k in sizes}
    for k in sorted(nonempty, key=lambda k: (-(quotas[k] - result[k]), -sizes[k], k))[:target-sum(result.values())]:
        result[k] += 1
    shortfall = sum(max(0, result[k] - sizes[k]) for k in sizes)
    result = {k: min(result[k], sizes[k]) for k in sizes}
    for k in sorted(nonempty, key=lambda k: (-sizes[k], k)):
        move = min(shortfall, sizes[k] - result[k])
        result[k] += move
        shortfall -= move
    return result


def normalised(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', text).casefold()).strip()


def char_grams(text):
    t = normalised(text)
    return {t[i:i+5] for i in range(max(0, len(t)-4))} or ({t} if t else set())


def jaccard(a, b):
    return len(a & b) / len(a | b) if a or b else 0.0


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run(root=ROOT):
    root = Path(root)
    clauses = json.loads((root / 'data/clauses.json').read_text(encoding='utf-8'))
    weak = {r['id']: r for r in json.loads((root / 'data/weak_labels.json').read_text(encoding='utf-8'))}
    pools = {(label, kind): [] for label in LABELS for kind in DOC_TYPES}
    for c in sorted(clauses, key=lambda c: c['id']):
        if c['text'].strip():
            pools[weak[c['id']]['weak_label'], doc_type(c['doc'])].append(c['id'])
    rng = random.Random(20261015)
    selected, allocation, by_label = [], [], {}
    for label in LABELS:
        sizes = {kind: len(pools[label, kind]) for kind in DOC_TYPES}
        counts = allocate(sizes)
        ids = []
        for kind in DOC_TYPES:
            ids.extend(rng.sample(pools[label, kind], counts[kind]))
            allocation.append(dict(weak_label=label, doc_type=kind, pool_size=sizes[kind], sampled=counts[kind]))
        by_label[label] = ids
        selected.extend(ids)
        if len(ids) < 50:
            print(f'DESIGN CONFLICT: {label} has only {len(ids)} eligible clauses; target 50, shortfall {50-len(ids)}.')
    gold = set(selected)
    train = sorted(c['id'] for c in clauses if c['text'].strip() and c['id'] not in gold
                   and not weak[c['id']]['needs_review'])
    retest_rng = random.Random(20261016)
    retest = [cid for i, label in enumerate(LABELS)
              for cid in retest_rng.sample(by_label[label], min(len(by_label[label]), 13 if i < 2 else 12))]
    folder = root / 'goldset'
    for name, ids in [('gold_ids', sorted(gold)), ('train_ids', train), ('retest_ids', sorted(retest))]:
        write_csv(folder / (name + '.csv'), ['id'], [{'id': i} for i in ids])
    write_csv(folder / 'allocation.csv', ['weak_label', 'doc_type', 'pool_size', 'sampled'], allocation)
    cmap = {c['id']: c for c in clauses}
    grams = {cid: char_grams(cmap[cid]['text']) for cid in sorted(gold | set(train))}
    flags = []
    for gid in sorted(gold):
        for tid in train:
            a, b = grams[gid], grams[tid]
            if min(len(a), len(b)) < .8 * max(len(a), len(b)):
                continue
            score = jaccard(a, b)
            if score >= .8:
                flags.append(dict(gold_id=gid, train_id=tid, jaccard=round(score, 8)))
    write_csv(folder / 'near_dup_flags.csv', ['gold_id', 'train_id', 'jaccard'], flags)
    random.Random(20261017).shuffle(selected)
    blind = [dict(clause_id=cid, text=cmap[cid]['text'], label='', mixed='', restated_reg='',
                  low_confidence='', note='') for cid in selected]
    write_csv(root / 'data/gold/gold_round1.csv', list(blind[0]), blind)
    (root / 'data/gold/gold_round1_labelstudio.json').write_text(
        json.dumps([{'data': {'clause_id': r['clause_id'], 'text': r['text']}} for r in blind], indent=2), encoding='utf-8')
    for row in allocation:
        print(f"{row['weak_label']:6} {row['doc_type']:5} {row['pool_size']:4} -> {row['sampled']}")
    print(f'gold={len(gold)} train={len(train)} retest={len(retest)} near_dup_pairs={len(flags)}')
    return allocation, flags


if __name__ == '__main__':
    run()
