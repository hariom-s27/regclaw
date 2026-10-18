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

S4b offset-free path: the adjudicated CSV gold has no offsets, and first
occurrences are never chosen silently, so it is scored with
schemes=('canonical',) only; entities there need no start/end. Gold canonicals
always pass through the normaliser. Predictions: after = ner.json canonical
(normalise=True); before = raw model canonical, surface() only (normalise=False).
`version` picks the normaliser (ADR 0027): score a v1.0 run with version='1.0'.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np
from scipy.optimize import linear_sum_assignment

from ner.checks import ENTITY_TYPES, ENUMS
from ner.normalise import NORMALISER_VERSION, Normaliser, surface

SEED = 20261018
SCHEMES = ('strict', 'exact', 'partial', 'type', 'canonical')


def validate(rows, *, spans=True, normalise=True, version=NORMALISER_VERSION):
    normaliser = Normaliser(version=version)
    result = {}
    if not rows:
        raise ValueError('No annotated clauses')
    for cid, entities in rows.items():
        result[cid] = []
        for e in entities:
            if (e['type'] not in ENTITY_TYPES
                    or spans and (type(e.get('start')) is not int or type(e.get('end')) is not int
                                  or not 0 <= e['start'] < e['end'])
                    or not isinstance(e['canonical'], str) or not e['canonical'].strip()):
                raise ValueError(f'Invalid typed span in {cid}')
            if normalise:
                canonical, _ = normaliser.canonical(e['canonical'], e['type'], enum_canonical=e['canonical'])
            else:
                canonical = surface(e['canonical']).replace('_', ' ')
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


def score(gold, predicted, *, resamples=1000, seed=SEED, schemes=SCHEMES, normalise=True,
          version=NORMALISER_VERSION):
    """Micro/per-type/macro P/R/F1 plus clause-bootstrap percentile CI95.

    schemes=('canonical',) needs no offsets; normalise applies to predictions only."""
    if set(gold) != set(predicted):
        raise ValueError('Gold and prediction clause ids differ')
    if resamples < 1:
        raise ValueError('At least one bootstrap resample is required')
    if not schemes or set(schemes) - set(SCHEMES):
        raise ValueError('Unknown scheme')
    spans = set(schemes) != {'canonical'}
    gold = validate(gold, spans=spans, version=version)
    predicted = validate(predicted, spans=spans, normalise=normalise, version=version)
    ids = sorted(gold)
    buckets = ('micro', *ENTITY_TYPES)
    cached = {scheme: {bucket: [] for bucket in buckets} for scheme in schemes}
    for scheme in schemes:
        for cid in ids:
            for bucket in buckets:
                g = [e for e in gold[cid] if bucket == 'micro' or e['type'] == bucket]
                p = [e for e in predicted[cid] if bucket == 'micro' or e['type'] == bucket]
                cached[scheme][bucket].append(counts(g, p, scheme))

    def aggregate(indices):
        output = {}
        for scheme in schemes:
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
    for scheme in schemes:
        for bucket, measures in point[scheme].items():
            measures['ci95'] = {metric: np.percentile([d[scheme][bucket][metric] for d in draws], [2.5, 97.5]).tolist()
                                for metric in ('precision', 'recall', 'f1')}
    return dict(clauses=len(ids), resamples=resamples, seed=seed, normaliser_version=version, metrics=point,
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


def _micro_canonical_f1(per_clause, indices):
    total = Counter()
    for i in indices:
        total.update(per_clause[i])
    return prf(total)['f1']


def paired_diff_ci(gold, a, b, *, resamples=1000, seed=SEED, normalise_a=True, normalise_b=True,
                   version_a=NORMALISER_VERSION, version_b=NORMALISER_VERSION):
    """Micro canonical F1 of b - a, both on the same resampled clause indices.

    Each side's gold and predictions use that side's normaliser version."""
    if not set(gold) == set(a) == set(b):
        raise ValueError('Gold and prediction clause ids differ')
    gold_a, gold_b = validate(gold, spans=False, version=version_a), validate(gold, spans=False, version=version_b)
    a = validate(a, spans=False, normalise=normalise_a, version=version_a)
    b = validate(b, spans=False, normalise=normalise_b, version=version_b)
    ids = sorted(gold)
    ca = [counts(gold_a[c], a[c], 'canonical') for c in ids]
    cb = [counts(gold_b[c], b[c], 'canonical') for c in ids]
    point = _micro_canonical_f1(cb, range(len(ids))) - _micro_canonical_f1(ca, range(len(ids)))
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(resamples):
        idx = rng.integers(0, len(ids), size=len(ids))
        draws.append(_micro_canonical_f1(cb, idx) - _micro_canonical_f1(ca, idx))
    return dict(diff=point, ci95=np.percentile(draws, [2.5, 97.5]).tolist(),
                share_positive=float(np.mean(np.array(draws) > 0)), resamples=resamples, seed=seed,
                versions=dict(a=version_a, b=version_b))


def top_errors(gold, predicted, n=10, *, normalise=True, version=NORMALISER_VERSION):
    """Per type, the most frequent false-positive and false-negative canonical names."""
    gold = validate(gold, spans=False, version=version)
    predicted = validate(predicted, spans=False, normalise=normalise, version=version)
    fp, fn = {k: Counter() for k in ENTITY_TYPES}, {k: Counter() for k in ENTITY_TYPES}
    for cid in gold:
        g = {(e['type'], e['canonical']) for e in gold[cid]}
        p = {(e['type'], e['canonical']) for e in predicted.get(cid, [])}
        for kind, name in p - g:
            fp[kind][name] += 1
        for kind, name in g - p:
            fn[kind][name] += 1
    return {k: dict(false_positives=fp[k].most_common(n), false_negatives=fn[k].most_common(n))
            for k in ENTITY_TYPES}


CELL = re.compile(r'^(?P<mention>.+?)\s*=>\s*(?P<canonical>.+)$')


def load_gold_csv(path, texts):
    """Adjudicated gold CSV -> {clause_id: [{type, canonical}]}; no offsets.

    Checks: completed notes, 'mention => canonical; ...' cells, enum validity, and
    surface(mention) is a substring of surface(clause text). Any failure raises."""
    with Path(path).open(encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f))
    gold, problems = {}, []
    for row in rows:
        cid = row['clause_id']
        if cid in gold:
            problems.append(f'{cid}: duplicate row')
        if cid not in texts:
            problems.append(f'{cid}: not in clauses')
            continue
        if 'complete' not in (row.get('notes') or ''):
            problems.append(f'{cid}: notes not complete')
        text = surface(texts[cid])
        gold[cid] = []
        for kind in ENTITY_TYPES:
            for item in filter(None, (x.strip() for x in (row[kind] or '').split(';'))):
                m = CELL.match(item)
                if not m:
                    problems.append(f'{cid}/{kind}: bad cell item')
                    continue
                mention, canonical = m['mention'].strip(), m['canonical'].strip()
                if surface(mention) not in text:
                    problems.append(f'{cid}/{kind}: mention not in clause text')
                if ENUMS[kind] and surface(canonical).replace(' ', '_') not in ENUMS[kind]:
                    problems.append(f'{cid}/{kind}: invalid enum canonical {canonical!r}')
                gold[cid].append({'type': kind, 'canonical': canonical})
    if problems:
        raise ValueError('Gold validation failed: ' + '; '.join(problems))
    return gold


def load_predictions(path, ids, *, stage='after', effort=None, texts=None):
    """Model entities for `ids` only -> {clause_id: [{type, canonical}]}.

    after = ner.json canonical; before = raw cached model canonical. Rows outside
    `ids` are filtered by id before any value is read."""
    ids = set(ids)
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    result = {}
    if stage == 'after':
        for row in data:
            if row['id'] in ids:
                result[row['id']] = [{'type': k, 'canonical': e['canonical']}
                                     for k in ENTITY_TYPES for e in row['entities'].get(k, [])]
    elif stage == 'before':
        for key in data:
            parts = key.split('|')
            if parts[0] not in ids or effort and parts[-1] != effort:
                continue
            if texts is not None and parts[1] != hashlib.sha256(texts[parts[0]].encode()).hexdigest():
                continue
            if parts[0] in result:
                raise ValueError(f'Several cache entries for {parts[0]}')
            entities = data[key]['data']['entities']
            result[parts[0]] = [{'type': k, 'canonical': e['canonical']}
                                for k in ENTITY_TYPES for e in entities.get(k, [])]
    else:
        raise ValueError(f'Unknown stage {stage}')
    if set(result) != ids:
        raise ValueError(f'{len(ids - set(result))} ids missing from {path}')
    return result


def ledger_cost(path, ids, effort):
    """Cost and call latency per clause over every call (retries included)."""
    ids = set(ids)
    rows = [r for r in json.loads(Path(path).read_text(encoding='utf-8'))
            if r['clause_id'] in ids and r['effort'] == effort]
    if {r['clause_id'] for r in rows} != ids:
        raise ValueError(f'Ledger {path} lacks some ids')
    cost, latency = sum(r['cost_estimate_usd'] for r in rows), sum(r['latency_s'] for r in rows)
    return dict(clauses=len(ids), calls=len(rows), cost_usd=cost, cost_per_clause_usd=cost/len(ids),
                latency_s=latency, latency_per_clause_s=latency/len(ids))


S4 = Path('data/runs/azure_openai/s4')
NOT_COMPUTED = 'not computed: no reviewed offsets in gold or predictions'
LABELS = {'before': 'raw model canonical, surface() only', 'after': 'ner.json canonical'}
MANIFEST = Path('goldset/freeze_manifest.json')
SENSITIVITY_LABEL = 'sensitivity: excludes the 1 clause whose gold row was exposed'


HASH_METHOD = 'sha256 of LF-normalised text for tracked files; raw bytes for untracked data/ files'


def sha256_file(path):
    """Freeze hash: raw bytes under data/ (untracked); CRLF->LF elsewhere (git-tracked, portable)."""
    data = Path(path).read_bytes()
    if not Path(path).as_posix().startswith('data/'):
        data = data.replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()


def verify_manifest(path=MANIFEST, *, version=NORMALISER_VERSION, required=()):
    """Check every frozen file sha (see HASH_METHOD) and the normaliser version.

    `required` paths must be listed in the manifest. Raises on any mismatch."""
    manifest = json.loads(Path(path).read_text(encoding='utf-8'))
    problems = []
    if manifest.get('hash_method') != HASH_METHOD:
        problems.append(f"hash method: {manifest.get('hash_method')!r}")
    if manifest.get('normaliser_version') != NORMALISER_VERSION or version != NORMALISER_VERSION:
        problems.append(f"normaliser version: manifest {manifest.get('normaliser_version')!r}, "
                        f'code {NORMALISER_VERSION!r}, requested {version!r}')
    files = manifest.get('files') or {}
    for name in sorted(files):
        if not Path(name).is_file():
            problems.append(f'{name}: missing')
        elif sha256_file(name) != files[name]:
            problems.append(f'{name}: sha256 differs')
    listed = {Path(n).as_posix() for n in files}
    problems += [f'{Path(r).as_posix()}: not in the manifest' for r in required if Path(r).as_posix() not in listed]
    if problems:
        raise ValueError('Freeze manifest check failed; final holdout scoring aborted: ' + '; '.join(problems))
    return manifest


def s4b_main(argv=None):
    """Score the dev split of the S4 GPT runs against offset-free adjudicated gold.

    --final-holdout scores the holdout split instead, only after every freeze
    manifest sha and the normaliser version verify; output is aggregate-only."""
    parser = argparse.ArgumentParser(description=s4b_main.__doc__)
    parser.add_argument('--gold', default='data/gold/entity_gold_final_v1.1.csv')
    parser.add_argument('--ids', default='goldset/entity_gold_ids.csv')
    parser.add_argument('--clauses', default='data/clauses.json')
    parser.add_argument('--low', nargs=3, metavar=('NER', 'CACHE', 'LEDGER'),
                        default=[S4/'phase2/ner.json', S4/'ner_cache.json', S4/'phase2/call_ledger.json'])
    parser.add_argument('--high', nargs=3, metavar=('NER', 'CACHE', 'LEDGER'),
                        default=[S4/'high/ner.json', S4/'high/ner_cache.json', S4/'call_ledger.json'])
    parser.add_argument('--output', default=S4/'s4b_scores_dev.json')
    parser.add_argument('--normaliser-version', choices=('1.0', '1.1'), default=NORMALISER_VERSION)
    parser.add_argument('--baseline-low', metavar='NER', help='low ner.json to diff against (v - baseline)')
    parser.add_argument('--baseline-version', choices=('1.0', '1.1'), default='1.0')
    parser.add_argument('--completed-hand-gold', action='store_true')
    parser.add_argument('--final-holdout', action='store_true', help='score the sealed holdout after the manifest check')
    parser.add_argument('--manifest', default=MANIFEST)
    args = parser.parse_args(argv)
    if not args.completed_hand_gold:
        parser.error('Scoring requires completed hand gold, never the blind blank sheet')
    target, manifest = 'dev', None
    if args.final_holdout:
        if args.baseline_low:
            parser.error('--final-holdout does not take --baseline-low')
        if Path(args.output).exists():
            parser.error('--final-holdout never overwrites an existing output')
        # Verify before any gold or prediction is read.
        manifest = verify_manifest(args.manifest, version=args.normaliser_version,
                                   required=(args.gold, args.ids, args.clauses, *args.low[:2], *args.high[:2]))
        target = 'holdout'
    with Path(args.ids).open(encoding='utf-8', newline='') as f:
        split = {r['clause_id']: r['split'] for r in csv.DictReader(f)}
    eval_ids = sorted(c for c, s in split.items() if s == target)
    if manifest and len(eval_ids) != manifest['holdout_clauses']:
        raise ValueError('Holdout id count differs from the manifest')
    texts = {c['id']: c['text'] for c in json.loads(Path(args.clauses).read_text(encoding='utf-8'))
             if c['id'] in split}
    gold = load_gold_csv(args.gold, texts)
    if set(gold) != set(split):
        raise ValueError('Gold ids differ from the id list')
    gold_eval = {c: gold[c] for c in eval_ids}
    version = args.normaliser_version
    out = dict(split=target, clauses=len(eval_ids), normaliser_version=version,
               gold_validation=dict(rows=len(gold), ids_match=True, mentions=sum(map(len, gold.values())),
                                    checks='cell format, enum validity, surface() substring: all pass'),
               labels=LABELS, primary='canonical', strict=NOT_COMPUTED, exact=NOT_COMPUTED,
               partial=NOT_COMPUTED, type=NOT_COMPUTED, scores={}, diff_high_minus_low={},
               errors_low={}, cost={})
    preds = {}
    for run, (ner, cache, ledger) in (('low', args.low), ('high', args.high)):
        preds[run, 'after'] = load_predictions(ner, eval_ids)
        preds[run, 'before'] = load_predictions(cache, eval_ids, stage='before', effort=run, texts=texts)
        for stage in ('after', 'before'):
            out['scores'][f'{run}_{stage}'] = score(gold_eval, preds[run, stage], schemes=('canonical',),
                                                    normalise=stage == 'after', version=version)
        out['cost'][run] = ledger_cost(ledger, eval_ids, run)
    for stage in ('after', 'before'):
        n = stage == 'after'
        out['diff_high_minus_low'][stage] = paired_diff_ci(gold_eval, preds['low', stage], preds['high', stage],
                                                           normalise_a=n, normalise_b=n,
                                                           version_a=version, version_b=version)
        if not manifest:  # holdout output stays aggregate-only
            out['errors_low'][stage] = top_errors(gold_eval, preds['low', stage], normalise=n, version=version)
    if manifest:
        del out['errors_low']
        out['manifest'] = dict(path=Path(args.manifest).as_posix(), sha256=sha256_file(args.manifest), verified=True)
        excluded = sorted(manifest['sensitivity_exclusion'])
        if not set(excluded) <= set(eval_ids):
            raise ValueError('Sensitivity exclusion lists ids outside the holdout')
        keep = [c for c in eval_ids if c not in excluded]
        sub = lambda rows: {c: rows[c] for c in keep}
        out['sensitivity'] = dict(
            label=SENSITIVITY_LABEL, excluded=excluded, clauses=len(keep),
            scores={f'{run}_{stage}': score(sub(gold_eval), sub(preds[run, stage]), schemes=('canonical',),
                                            normalise=stage == 'after', version=version)
                    for run in ('low', 'high') for stage in ('after', 'before')},
            diff_high_minus_low={stage: paired_diff_ci(sub(gold_eval), sub(preds['low', stage]), sub(preds['high', stage]),
                                                       normalise_a=stage == 'after', normalise_b=stage == 'after',
                                                       version_a=version, version_b=version)
                                 for stage in ('after', 'before')})
    if args.baseline_low:
        out['diff_low_vs_baseline'] = dict(
            baseline=str(args.baseline_low), **paired_diff_ci(
                gold_eval, load_predictions(args.baseline_low, eval_ids), preds['low', 'after'],
                version_a=args.baseline_version, version_b=version))
    Path(args.output).write_text(json.dumps(out, indent=2)+'\n', encoding='utf-8')
    print(f'wrote {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(s4b_main(sys.argv[2:]) if sys.argv[1:2] == ['s4b'] else main())
