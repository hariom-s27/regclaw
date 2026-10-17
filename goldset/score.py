"""Paired annotation/model scoring. Empty annotation templates are not gold."""
import argparse
import csv
import hashlib
import json
import math
import random
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

from goldset.sample import LABELS, ROOT, doc_type


def read_gold(path):
    path = Path(path)
    if path.suffix.lower() == '.json':
        tasks = json.loads(path.read_text(encoding='utf-8'))
        rows = []
        for task in tasks:
            annotations = [a for a in task.get('annotations', []) if not a.get('was_cancelled')]
            if len(annotations) != 1:
                raise ValueError('Expected one completed annotation per Label Studio task')
            labels = [r['value']['choices'][0] for r in annotations[0]['result'] if r.get('from_name') == 'label']
            if len(labels) != 1:
                raise ValueError('Expected one label per task')
            rows.append({'clause_id': task['data']['clause_id'], 'label': labels[0]})
    else:
        with path.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
    result = {}
    for row in rows:
        cid, label = row['clause_id'], row['label'].strip()
        if cid in result or label not in LABELS:
            raise ValueError(f'Duplicate id or unfilled/invalid label: {cid}')
        result[cid] = label
    if not result:
        raise ValueError('No completed gold labels')
    return result


def report(gold, predicted, weights=None):
    return {'per_class': classification_report(gold, predicted, labels=list(LABELS), output_dict=True,
                                               sample_weight=weights, zero_division=0),
            'macro_f1': float(f1_score(gold, predicted, labels=list(LABELS), average='macro', sample_weight=weights, zero_division=0)),
            'accuracy': float(accuracy_score(gold, predicted, sample_weight=weights)),
            'confusion_matrix': confusion_matrix(gold, predicted, labels=list(LABELS), sample_weight=weights).tolist()}


def _f1_batch(gold, predicted):
    scores = []
    for label in LABELS:
        tp = ((gold == label) & (predicted == label)).sum(axis=1)
        denom = (gold == label).sum(axis=1) + (predicted == label).sum(axis=1)
        scores.append(np.divide(2 * tp, denom, out=np.zeros(len(tp), dtype=float), where=denom != 0))
    return np.mean(scores, axis=0)


def ci(values):
    return np.percentile(values, [2.5, 97.5]).tolist()


def paired_comparison(gold, regex, model, resamples=10000, seed=20261015):
    g, r, m = map(np.asarray, (gold, regex, model))
    indices = np.random.default_rng(seed).integers(0, len(g), size=(resamples, len(g)))
    delta = _f1_batch(g[indices], m[indices]) - _f1_batch(g[indices], r[indices])
    bounds = ci(delta)
    fixes = int(((r != g) & (m == g)).sum())
    breaks = int(((r == g) & (m != g)).sum())
    n = fixes + breaks
    exact = min(1.0, 2 * sum(math.comb(n, k) for k in range(min(fixes, breaks)+1)) / 2**n) if n else 1.0
    return {'delta_macro_f1': report(g, m)['macro_f1'] - report(g, r)['macro_f1'],
            'bootstrap_resamples': resamples, 'seed': seed, 'ci95': bounds,
            'decision': 'above 0' if bounds[0] > 0 else 'below 0' if bounds[1] < 0 else 'straddles 0',
            'fixes_breaks': {'both_correct': int(((r == g) & (m == g)).sum()), 'fixes': fixes,
                             'breaks': breaks, 'both_wrong': int(((r != g) & (m != g)).sum())},
            'mcnemar_exact_p': exact}


def agreement_values(a, b):
    """Rows are resamples; nominal alpha uses finite-population disagreement."""
    observed = (a == b).mean(axis=1)
    pa = np.stack([(a == label).mean(axis=1) for label in LABELS], axis=1)
    pb = np.stack([(b == label).mean(axis=1) for label in LABELS], axis=1)
    expected = (pa * pb).sum(axis=1)
    kappa = np.divide(observed - expected, 1 - expected, out=np.full(len(a), np.nan), where=expected < 1)
    counts = (pa + pb) * a.shape[1]
    total = 2 * a.shape[1]
    de = 1 - (counts * (counts - 1)).sum(axis=1) / (total * (total - 1))
    alpha = 1 - np.divide(1 - observed, de, out=np.full(len(a), np.nan), where=de > 0)
    return observed, kappa, alpha


def retest_report(round1, round2, resamples=10000, seed=20261016):
    if not round2 or round2.keys() - round1.keys():
        raise ValueError('Retest ids must be a nonempty subset of round 1')
    ids = sorted(round2)
    a, b = np.array([round1[i] for i in ids]), np.array([round2[i] for i in ids])
    indices = np.random.default_rng(seed).integers(0, len(ids), size=(resamples, len(ids)))
    values = agreement_values(a[None, :], b[None, :])
    boot = agreement_values(a[indices], b[indices])
    result = {'n': len(ids), 'seed': seed, 'bootstrap_resamples': resamples}
    for name, point, samples in zip(('raw_agreement', 'cohen_kappa', 'krippendorff_alpha'), values, boot):
        valid = samples[np.isfinite(samples)]
        result[name] = {'value': float(point[0]) if np.isfinite(point[0]) else None,
                        'ci95': ci(valid) if len(valid) else None,
                        'undefined_resamples': int(len(samples)-len(valid))}
    alpha = result['krippendorff_alpha']['value']
    result['decision'] = 'undefined' if alpha is None else 'rely' if alpha >= .8 else 'tentative' if alpha >= .667 else 'fix the guideline'
    return result


def agreement_report(a_path, b_path, gold_path, labels_path, ids_path, resamples=5000, seed=1):
    """Validate the locked round and reuse the retest math for two annotators."""
    def rows(path):
        with Path(path).open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.DictReader(stream))

    gold, exported = rows(gold_path), rows(labels_path)
    fields = ['clause_id', 'text', 'label', 'mixed', 'restated_reg', 'low_confidence', 'note', 'source']
    if len(gold) != 184 or list(gold[0]) != fields:
        raise ValueError('Expected 184 gold rows and the exact gold_final columns')
    gold_map = read_gold(gold_path)
    ids = [r['id'] for r in rows(ids_path)]
    if len(ids) != len(set(ids)) or set(ids) != gold_map.keys():
        raise ValueError('Gold ids do not equal gold_ids.csv')
    for row in gold + exported:
        if any(row[key] not in ('0', '1') for key in ('mixed', 'restated_reg', 'low_confidence')):
            raise ValueError('Flags must be 0/1')
    keys = ['clause_id', 'label', 'mixed', 'restated_reg', 'low_confidence', 'source']
    project = lambda data: {r['clause_id']: tuple(r[k] for k in keys) for r in data}
    read_gold(labels_path)  # Reject duplicates before projecting.
    if project(gold) != project(exported):
        raise ValueError('Tracked label export differs from gold_final')
    a_rows, b_rows = rows(a_path), rows(b_path)
    a, b = read_gold(a_path), read_gold(b_path)
    for data, mapping in ((a_rows, a), (b_rows, b)):
        if mapping.keys() != gold_map.keys() or [int(r['row']) for r in data] != list(range(1, 185)):
            raise ValueError('Annotators must contain the gold ids in numbered rows 1..184')
    if [r['clause_id'] for r in a_rows] != [r['clause_id'] for r in b_rows]:
        raise ValueError('Annotator row order differs')
    remaining = {r['clause_id']: b[r['clause_id']] for r in b_rows[20:]}
    # Explain the expected Claude intervals without changing the retest defaults.
    rng_check = {}
    for name, data in [('all', a_rows), ('rows_21_184', a_rows[20:])]:
        order = [r['clause_id'] for r in data]
        x, y = np.array([a[i] for i in order]), np.array([b[i] for i in order])
        rng = random.Random(seed)
        indices = np.array([[rng.randrange(len(order)) for _ in order] for _ in range(resamples)])
        rng_check[name] = {'cohen_kappa_ci95': ci(agreement_values(x[indices], y[indices])[1])}
    paths = [a_path, b_path, gold_path, labels_path, ids_path]
    return {'validation': {'rows': len(gold), 'ids_match': True, 'export_matches': True,
                           'label_counts': dict(Counter(gold_map.values())),
                           'source_counts': dict(Counter(r['source'] for r in gold)),
                           'label_disagreements': sum(a[i] != b[i] for i in a)},
            'sha256': {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths},
            'all': retest_report(a, b, resamples, seed),
            'rows_21_184': retest_report(a, remaining, resamples, seed),
            'python_rng_original_order_check': {'method': 'random.Random(seed).randrange; original CSV order',
                                                'seed': seed, 'resamples': resamples, **rng_check},
            'method': 'Paired row bootstrap; sorted clause ids within subset; nominal alpha; percentile CI'}


def score(gold, weak, allocation, predictions=None, flagged=()):
    weak_map = {r['id']: r for r in weak}
    cells = {(r['weak_label'], r['doc_type']): (int(r['pool_size']), int(r['sampled'])) for r in allocation}
    if gold.keys() - weak_map.keys():
        raise ValueError('Unknown gold ids')
    if predictions is not None and gold.keys() - predictions.keys():
        raise ValueError('Missing model predictions for gold ids')
    result = {'labels': list(LABELS), 'flagged_gold_count': len(gold.keys() & set(flagged)), 'subsets': {}}
    for name, ids in [('all', sorted(gold)), ('without_near_duplicates', sorted(gold.keys() - set(flagged)))]:
        if not ids:
            result['subsets'][name] = {'n': 0}
            continue
        g, r = [gold[i] for i in ids], [weak_map[i]['weak_label'] for i in ids]
        weights = []
        for i in ids:
            n, sampled = cells[(weak_map[i]['weak_label'], doc_type(i.split(':')[0]))]
            if sampled <= 0:
                raise ValueError('Gold id belongs to an unsampled cell')
            weights.append(n / sampled)
        out = {'n': len(ids), 'regex': report(g, r), 'regex_corpus_weighted': report(g, r, weights)}
        if predictions is not None:
            m = [predictions[i] for i in ids]
            out.update(legalbert=report(g, m), legalbert_corpus_weighted=report(g, m, weights),
                       paired=paired_comparison(g, r, m))
        result['subsets'][name] = out
    return result


def print_tables(result):
    for subset, data in result['subsets'].items():
        print(f"{subset}: n={data['n']}")
        for system in ('regex', 'legalbert', 'regex_corpus_weighted', 'legalbert_corpus_weighted'):
            if system not in data:
                continue
            scores = data[system]
            print(f'{system} | class | precision | recall | f1 | gold support')
            for label in LABELS:
                row = scores['per_class'][label]
                print(f"{system} | {label} | {row['precision']:.6f} | {row['recall']:.6f} | {row['f1-score']:.6f} | {row['support']}")
            print(f"macro-F1={scores['macro_f1']:.6f} accuracy={scores['accuracy']:.6f}")
            print('confusion matrix: rows=gold, columns=predicted | ' + ' | '.join(LABELS))
            for label, row in zip(LABELS, scores['confusion_matrix']):
                print(label + ' | ' + ' | '.join(map(str, row)))
        if 'paired' in data:
            paired = data['paired']
            print(f"paired delta macro-F1={paired['delta_macro_f1']:.6f} | CI95={paired['ci95']} | {paired['decision']}")
            print('fixes/breaks | ' + json.dumps(paired['fixes_breaks']))
            print(f"exact McNemar p={paired['mcnemar_exact_p']}")
    if 'retest' in result:
        print('retest | measure | value | bootstrap CI95')
        for name in ('raw_agreement', 'cohen_kappa', 'krippendorff_alpha'):
            row = result['retest'][name]
            print(f"retest | {name} | {row['value']} | {row['ci95']}")
        print('retest decision:', result['retest']['decision'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('gold', nargs='?')
    parser.add_argument('--agreement', nargs=2, metavar=('A', 'B'))
    parser.add_argument('--gold-final', default=ROOT / 'data/gold/gold_final.csv')
    parser.add_argument('--labels-export', default=ROOT / 'goldset/gold_labels_round1.csv')
    parser.add_argument('--gold-ids', default=ROOT / 'goldset/gold_ids.csv')
    parser.add_argument('--weak', default=ROOT / 'data/weak_labels.json')
    parser.add_argument('--predictions')
    parser.add_argument('--allocation', default=ROOT / 'goldset/allocation.csv')
    parser.add_argument('--near-dup-flags', default=ROOT / 'goldset/near_dup_flags.csv')
    parser.add_argument('--retest')
    parser.add_argument('--output', default=ROOT / 'data/gold/scores.json')
    args = parser.parse_args(argv)
    if args.agreement:
        result = agreement_report(*args.agreement, args.gold_final, args.labels_export, args.gold_ids)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    if not args.gold:
        parser.error('gold is required unless --agreement is used')
    gold = read_gold(args.gold)
    read_csv = lambda p: list(csv.DictReader(Path(p).open(encoding='utf-8-sig', newline='')))
    predictions = None
    if args.predictions:
        from ner.merge import indexed
        preds = indexed(json.loads(Path(args.predictions).read_text(encoding='utf-8')), 'predictions')
        predictions = {i: r['label'] for i, r in preds.items()}
        if any(label not in LABELS for label in predictions.values()):
            raise ValueError('Invalid prediction label')
    result = score(gold, json.loads(Path(args.weak).read_text(encoding='utf-8')), read_csv(args.allocation),
                   predictions, {r['gold_id'] for r in read_csv(args.near_dup_flags)})
    if args.retest:
        result['retest'] = retest_report(gold, read_gold(args.retest))
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print_tables(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
