"""Deterministic annotation samples and dependency-free segmentation scoring."""
import argparse
from collections import defaultdict
import csv
import json
import math
from pathlib import Path
import random
import re

from .ingest import ROOT, csv_write, json_write, run

FIELDS = ['line_id', 'doc', 'page', 'text', 'pred_role', 'pred_number', 'pred_parent_number',
          'gold_role', 'gold_number', 'gold_parent_number']
ROLES = {'clause_start', 'continuation', 'header_footer', 'toc', 'footnote', 'table', 'noise', 'page_number'}
CONTENT = {'clause_start', 'continuation', 'table'}


def sample(root=ROOT):
    root = Path(root)
    results, _ = run(root)
    rng = random.Random(42)
    pages_report, rows = {}, []
    for doc, result in sorted(results.items()):
        lines, pages = result['lines'], result['stats']['pages']
        random_pages = rng.sample(range(1, pages + 1), min(3, pages))
        table_pages = sorted({c['page'] for c in result['clauses'] if re.search(r'\.T\d+(?:~\d+)?$', c['number'])})
        annex_pages = sorted({l['page'] for l in lines if l['pred_role'] == 'clause_start' and
                              re.match(r'^(Annex|Attachment|Appendix|Schedule)\b', l['pred_number'])})
        footnotes = defaultdict(int)
        for l in lines:
            if l['pred_role'] == 'footnote':
                footnotes[l['page']] += len(l['text'])
        if table_pages:
            candidates, reason = table_pages, 'table clause'
        elif annex_pages:
            candidates, reason = annex_pages, 'annex/attachment/schedule start'
        else:
            candidates = sorted(range(1, pages + 1), key=lambda p: (-footnotes[p], p))
            reason = 'most footnote characters'
        hard = candidates[0]
        if hard in random_pages and pages >= 4:
            random_pages.remove(hard)
            remaining = [p for p in range(1, pages + 1) if p not in random_pages and p != hard]
            random_pages.append(rng.choice(remaining))
        selected = sorted(set(random_pages + [hard]))
        pages_report[doc] = {'pages': selected, 'random_pages': random_pages, 'seed': 42,
                             'hard_page': hard, 'hard_page_reason': reason,
                             'hard_page_footnote_chars': footnotes[hard]}
        for line in lines:
            if line['page'] in selected:
                rows.append(dict(line, gold_role='', gold_number='', gold_parent_number=''))
        print(f'{doc}: {selected}; hard {hard}: {reason}')
    csv_write(root / 'data/parse_eval/sample_lines.csv', FIELDS, rows)
    json_write(root / 'data/parse_eval/sample_pages.json', pages_report)
    print(f'CSV rows: {len(rows)} (gold columns empty)')
    return pages_report, rows


def wilson(successes, total):
    if not total:
        return [0.0, 1.0]
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z*z / total
    centre = (p + z*z/(2*total)) / denominator
    margin = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / denominator
    return [max(0, centre-margin), min(1, centre+margin)]


def proportion(numerator, denominator):
    return {'value': numerator / denominator if denominator else 0.0,
            'numerator': numerator, 'denominator': denominator, 'ci95': wilson(numerator, denominator)}


def boundary_matches(predicted, gold, tolerance=0):
    """Monotone one-to-one maximum matching, so a gold boundary is never reused."""
    i = j = 0
    pairs = []
    while i < len(predicted) and j < len(gold):
        if abs(predicted[i] - gold[j]) <= tolerance:
            pairs.append((predicted[i], gold[j])); i += 1; j += 1
        elif predicted[i] < gold[j]:
            i += 1
        else:
            j += 1
    return pairs


def segments(roles):
    segment = 0
    ids = []
    for i, role in enumerate(roles):
        if i and role == 'clause_start':
            segment += 1
        ids.append(segment)
    return ids


def counts(rows):
    """Score contiguous labelled runs separately; never bridge missing gold or page gaps."""
    groups = defaultdict(list)
    for row in rows:
        groups[(row['doc'], str(row['page']))].append(row)
    totals = defaultdict(int)
    windows = []
    for page_rows in groups.values():
        runs, current = [], []
        for row in page_rows:
            if not row['gold_role'].strip():
                totals['unlabelled'] += 1
                if current:
                    runs.append(current); current = []
                continue
            if row['gold_role'] not in ROLES or row['pred_role'] not in ROLES:
                raise ValueError(f"Invalid role at {row['line_id']}")
            current.append(row)
        if current:
            runs.append(current)
        for run_rows in runs:
            pred = [i for i, r in enumerate(run_rows) if r['pred_role'] == 'clause_start']
            gold = [i for i, r in enumerate(run_rows) if r['gold_role'] == 'clause_start']
            pairs = boundary_matches(pred, gold)
            totals['pred'] += len(pred); totals['gold'] += len(gold); totals['tp'] += len(pairs)
            totals['tp1'] += len(boundary_matches(pred, gold, 1))
            totals['parent'] += sum(run_rows[p]['pred_parent_number'] == run_rows[g]['gold_parent_number'] for p, g in pairs)
            totals['number'] += sum(run_rows[p]['pred_number'] == run_rows[g]['gold_number'] for p, g in pairs)
            for row in run_rows:
                totals['labelled'] += 1
                totals['hf_pred'] += row['pred_role'] == 'header_footer'
                totals['hf_gold'] += row['gold_role'] == 'header_footer'
                totals['hf_tp'] += row['pred_role'] == row['gold_role'] == 'header_footer'
                n = len(row['text'])
                if row['gold_role'] in CONTENT:
                    totals['content_chars'] += n
                    totals['content_lines'] += 1
                    if row['pred_role'] in CONTENT:
                        totals['kept_chars'] += n
                        totals['kept_lines'] += 1
                    else:
                        totals['lost_lines'] += 1
            gold_ids = segments([r['gold_role'] for r in run_rows])
            lengths = [gold_ids.count(s) for s in set(gold_ids)]
            windows.append((run_rows, gold_ids, lengths))
    segment_lengths = [n for _, _, sizes in windows for n in sizes]
    mean_length = sum(segment_lengths) / len(segment_lengths) if segment_lengths else 0
    # Round half up to an integer line distance.
    k = max(1, math.floor(mean_length / 2 + .5))
    totals['k'] = k
    for run_rows, gold_ids, _ in windows:
        pred_ids = segments([r['pred_role'] for r in run_rows])
        for i in range(len(run_rows) - k):
            totals['windows'] += 1
            totals['pk_errors'] += (gold_ids[i] == gold_ids[i+k]) != (pred_ids[i] == pred_ids[i+k])
            totals['wd_errors'] += (gold_ids[i+k] - gold_ids[i]) != (pred_ids[i+k] - pred_ids[i])
    return dict(totals)


def metrics(c):
    c = defaultdict(int, c)
    result = {'labelled_rows': c['labelled'], 'unlabelled_rows': c['unlabelled'],
              'lost_lines': c['lost_lines'], 'k': c['k']}
    for suffix, tp in [('', c['tp']), ('_pm1', c['tp1'])]:
        result['boundary_precision'+suffix] = proportion(tp, c['pred'])
        result['boundary_recall'+suffix] = proportion(tp, c['gold'])
        # F1 = 2TP/(pred+gold); CI is on that proportion (a descriptive Wilson interval).
        result['boundary_f1'+suffix] = proportion(2*tp, c['pred'] + c['gold'])
    for name, numerator, denominator in [
        ('Pk', c['pk_errors'], c['windows']), ('WindowDiff', c['wd_errors'], c['windows']),
        ('parent_accuracy', c['parent'], c['tp']), ('number_accuracy', c['number'], c['tp']),
        ('header_footer_precision', c['hf_tp'], c['hf_pred']),
        ('header_footer_recall', c['hf_tp'], c['hf_gold']),
        ('coverage', c['kept_chars'], c['content_chars']),
        ('line_coverage', c['kept_lines'], c['content_lines']),
    ]:
        result[name] = proportion(numerator, denominator)
    return result


def score(path=None, root=ROOT):
    root = Path(root)
    path = Path(path) if path else root / 'data/parse_eval/sample_lines.csv'
    with path.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    groups = defaultdict(list)
    for row in rows:
        groups[row['doc']].append(row)
    per_doc = {doc: counts(rs) for doc, rs in sorted(groups.items())}
    aggregate = defaultdict(int)
    for c in per_doc.values():
        for key, value in c.items():
            if key != 'k':
                aggregate[key] += value
    output = {'per_doc': {doc: metrics(c) for doc, c in per_doc.items()},
              'micro': metrics(aggregate), 'ci_note': 'Wilson 95%; F1 intervals descriptive; character observations are correlated.',
              'k_note': 'Half mean gold segment length, rounded half up per doc; micro pools doc windows.'}
    print('doc | boundary P/R/F1 | +/-1 F1 | Pk | WindowDiff | parent | number | header/footer P/R | coverage | lost | unlabelled')
    for doc, m in list(output['per_doc'].items()) + [('micro', output['micro'])]:
        def v(name):
            x = m[name]
            return f"{x['value']:.3f}[{x['ci95'][0]:.3f},{x['ci95'][1]:.3f}]" if x['denominator'] else 'n/a'
        print(' | '.join([doc, '/'.join(v('boundary_'+x) for x in ('precision', 'recall', 'f1')), v('boundary_f1_pm1'),
                          v('Pk'), v('WindowDiff'), v('parent_accuracy'), v('number_accuracy'),
                          v('header_footer_precision') + '/' + v('header_footer_recall'), v('coverage'),
                          str(m['lost_lines']), str(m['unlabelled_rows'])]))
    json_write(root / 'data/parse_eval/scores.json', output)
    return output


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['sample', 'score'])
    p.add_argument('--csv')
    args = p.parse_args(argv)
    return sample() if args.command == 'sample' else score(args.csv)


if __name__ == '__main__':
    main()
