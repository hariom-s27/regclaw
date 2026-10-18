"""R11 E2 scorer, ready for hand gold; no scores are supplied by this module.

Input JSON maps clause ids to lists of {type, start, end, canonical}. Offsets
are zero-based Python character offsets, end exclusive, in the original text.
Gold and prediction id sets must agree, including clauses with zero entities.
Resolve ambiguous repeated mentions to explicit offsets during gold review;
never silently choose their first occurrence. Canonicals pass through the
unchanged S4 normaliser. CLI requires explicit completed-gold acknowledgement.

Matching is maximum-credit one-to-one within each clause. Strict: exact span
and type; exact: exact span ignoring type; partial: exact=1, overlap=.5,
ignoring type; type: any overlap and correct type=1. Wrong overlapping matches
are INC (zero credit); unmatched gold/predictions are MIS/SPU. Canonical is a
per-clause set of (type, canonical), independent of spans. Empty denominators
score zero. Macro averages all five fixed types, including absent types.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from ner.checks import ENTITY_TYPES
from ner.normalise import Normaliser

SEED = 20261018
SCHEMES = ('strict', 'exact', 'partial', 'type', 'canonical')


def validate(rows):
    normaliser = Normaliser()
    result = {}
    if not rows:
        raise ValueError('No annotated clauses')
    for cid, entities in rows.items():
        result[cid] = []
        for e in entities:
            if (e['type'] not in ENTITY_TYPES or type(e['start']) is not int
                    or type(e['end']) is not int or not 0 <= e['start'] < e['end']
                    or not isinstance(e['canonical'], str) or not e['canonical'].strip()):
                raise ValueError(f'Invalid typed span in {cid}')
            canonical, _ = normaliser.canonical(e['canonical'], e['type'], enum_canonical=e['canonical'])
            result[cid].append(dict(e, canonical=canonical))
    return result


def counts(gold, predicted, scheme):
    if scheme == 'canonical':
        g = {(e['type'], e['canonical']) for e in gold}
        p = {(e['type'], e['canonical']) for e in predicted}
        return Counter(COR=len(g & p), INC=0, PAR=0, MIS=len(g-p), SPU=len(p-g),
                       credit=len(g & p), gold=len(g), predicted=len(p))
    values = Counter(COR=0, INC=0, PAR=0, MIS=len(gold), SPU=len(predicted),
                     credit=0, gold=len(gold), predicted=len(predicted))
    if not gold or not predicted:
        return values
    weights = np.zeros((len(gold), len(predicted)))
    credits = weights.copy()
    for i, g in enumerate(gold):
        for j, p in enumerate(predicted):
            overlap = max(g['start'], p['start']) < min(g['end'], p['end'])
            exact = (g['start'], g['end']) == (p['start'], p['end'])
            same_type = g['type'] == p['type']
            credit = (float(exact and same_type) if scheme == 'strict' else
                      float(exact) if scheme == 'exact' else
                      (1. if exact else .5 if overlap else 0.) if scheme == 'partial' else
                      float(overlap and same_type))
            credits[i, j] = credit
            # Credit dominates the count of zero-credit overlap matches. This
            # avoids greedy/order-sensitive failures in nested/crossed spans.
            weights[i, j] = credit * (len(gold)+len(predicted)+1) + float(overlap)
    gi, pi = linear_sum_assignment(-weights)
    for i, j in zip(gi, pi):
        if weights[i, j] == 0:
            continue
        credit = credits[i, j]
        values['COR' if credit == 1 else 'PAR' if credit == .5 else 'INC'] += 1
        values['credit'] += credit
        values['MIS'] -= 1
        values['SPU'] -= 1
    return values


def prf(c):
    precision = c['credit']/c['predicted'] if c['predicted'] else 0.
    recall = c['credit']/c['gold'] if c['gold'] else 0.
    return dict(precision=precision, recall=recall,
                f1=2*precision*recall/(precision+recall) if precision+recall else 0.)


def score(gold, predicted, *, resamples=1000, seed=SEED):
    """Micro/per-type/macro P/R/F1 plus clause-bootstrap percentile CI95."""
    if set(gold) != set(predicted):
        raise ValueError('Gold and prediction clause ids differ')
    if resamples < 1:
        raise ValueError('At least one bootstrap resample is required')
    gold, predicted = validate(gold), validate(predicted)
    ids = sorted(gold)
    buckets = ('micro', *ENTITY_TYPES)
    cached = {scheme: {bucket: [] for bucket in buckets} for scheme in SCHEMES}
    for scheme in SCHEMES:
        for cid in ids:
            for bucket in buckets:
                g = [e for e in gold[cid] if bucket == 'micro' or e['type'] == bucket]
                p = [e for e in predicted[cid] if bucket == 'micro' or e['type'] == bucket]
                cached[scheme][bucket].append(counts(g, p, scheme))

    def aggregate(indices):
        output = {}
        for scheme in SCHEMES:
            measures = {}
            for bucket in buckets:
                total = Counter()
                for i in indices:
                    total.update(cached[scheme][bucket][i])
                measures[bucket] = dict(**prf(total), counts=dict(total))
            measures['macro'] = {metric: float(np.mean([measures[k][metric] for k in ENTITY_TYPES]))
                                 for metric in ('precision', 'recall', 'f1')}
            output[scheme] = measures
        return output

    point = aggregate(range(len(ids)))
    rng = np.random.default_rng(seed)
    draws = [aggregate(rng.integers(0, len(ids), size=len(ids))) for _ in range(resamples)]
    for scheme in SCHEMES:
        for bucket, measures in point[scheme].items():
            measures['ci95'] = {metric: np.percentile([d[scheme][bucket][metric] for d in draws], [2.5, 97.5]).tolist()
                                for metric in ('precision', 'recall', 'f1')}
    return dict(clauses=len(ids), resamples=resamples, seed=seed, metrics=point,
                method='Clause bootstrap, percentile CI95; five-type macro; empty denominators=0',
                caveat='Small gold sets yield wide intervals; single annotator is not inter-annotator agreement.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gold', required=True)
    parser.add_argument('--predictions', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--completed-hand-gold', action='store_true')
    args = parser.parse_args(argv)
    if not args.completed_hand_gold:
        parser.error('Scoring requires completed hand gold, never the blind blank sheet')
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
    result = score(read(args.gold), read(args.predictions))
    Path(args.output).write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
