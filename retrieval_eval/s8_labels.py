"""S8 Part D: label schema, validation, judge A/B agreement, merge, audit check and final qrels.

Command (via tasks.py): s8-merge-labels. Reads ONLY sheets and label files (never pool_key, runs
or item maps), so it cannot report anything per system. Prints COUNTS ONLY. Outputs are write-once.

Stages (each needs the previous one):
  1. A + B labels      -> agreement_ab.json (kappa binary + quadratic, raw agreement, 4x4 confusion,
                          bootstrap CI over queries)
  2. + frozen audit    -> audit_check.json (kappa binary Shyam vs A/B consensus; PASS >= 0.60 else STOP);
                          on PASS: disputes.csv, referee_sheet.csv, referee_ids.txt
  3. + referee labels  -> qrels.tsv (accepted = min(A, B); referee items = referee grade) + qrels_meta.json
"""
import argparse
from collections import Counter, defaultdict
import csv
import io
import json
from pathlib import Path

import numpy as np

from retrieval_eval import qrels as qrels_io
from retrieval_eval.s8_runs import SHEET_COLUMNS, bytes_sha, output_dir, sheet_csv, write_once

csv.field_size_limit(2 ** 31 - 1)

THRESHOLD = 2  # binary relevance: grade >= 2
GRADES = ('0', '1', '2', '3')
CLAUSE_STATES = ('answers', 'partial', 'mentions_only', 'unrelated')
JURISDICTION_MATCH = ('yes', 'no', 'not_applicable')
UNSURE = ('yes', 'no')
LABEL_FIELDS = ('item_id', 'query_asks', 'clause_states', 'jurisdiction_match', 'evidence_span',
                'grade', 'unsure', 'note')
REQUIRED_FIELDS = tuple(f for f in LABEL_FIELDS if f != 'query_asks')
OPTIONAL_FIELDS = ('query_asks', 'timestamp')
JUDGE_FIELDS = ('query_asks', 'clause_states', 'jurisdiction_match', 'evidence_span', 'grade', 'unsure', 'note')
DISPUTE_COLUMNS = (['item_id', 'reason', 'qid', 'query', 'source_doc', 'clause_id', 'title']
                   + [f'{j}_{f}' for j in ('a', 'b') for f in JUDGE_FIELDS])
AUDIT_PASS = 0.60
BOOTSTRAP = 10_000
SEED = 20261020
JUDGE_SHEET_GLOB = 'labels_s8_sheet_*.csv'


# ---------------------------------------------------------------- reading + validation
def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def read_sheet(path):
    columns, rows = read_csv(path)
    if columns != SHEET_COLUMNS:
        raise ValueError(f'{Path(path).name}: sheet columns differ from SHEET_COLUMNS')
    return rows


def validate_label(row, where):
    errors = []
    if not row.get('item_id', '').strip():
        errors.append('empty item_id')
    if row.get('grade', '').strip() not in GRADES:
        errors.append(f"grade {row.get('grade')!r} not in 0..3")
    if row.get('clause_states', '').strip() not in CLAUSE_STATES:
        errors.append(f"clause_states {row.get('clause_states')!r}")
    if row.get('jurisdiction_match', '').strip() not in JURISDICTION_MATCH:
        errors.append(f"jurisdiction_match {row.get('jurisdiction_match')!r}")
    if row.get('unsure', '').strip() not in UNSURE:
        errors.append(f"unsure {row.get('unsure')!r} not yes/no")
    if errors:
        raise ValueError(f'{where}: ' + '; '.join(errors))
    return {f: (row.get(f) or '').strip() if f in ('item_id', 'grade', 'clause_states',
                                                   'jurisdiction_match', 'unsure') else (row.get(f) or '')
            for f in (*LABEL_FIELDS, 'timestamp')}


def read_labels(paths, who):
    """Labels from one or more CSV files -> {item_id: row}; every item exactly once."""
    labels = {}
    for path in paths:
        path = Path(path)
        columns, rows = read_csv(path)
        missing = [f for f in REQUIRED_FIELDS if f not in columns]
        unknown = [c for c in columns if c not in LABEL_FIELDS and c not in OPTIONAL_FIELDS]
        if missing or unknown:
            raise ValueError(f'{who} {path.name}: missing columns {missing}, unknown columns {unknown}')
        for n, row in enumerate(rows, start=2):
            label = validate_label(row, f'{who} {path.name} line {n}')
            if label['item_id'] in labels:
                raise ValueError(f"{who}: item {label['item_id']} labelled more than once")
            label['_file'] = path.name
            labels[label['item_id']] = label
    return labels


def judge_files(folder):
    files = sorted(Path(folder).glob(JUDGE_SHEET_GLOB))
    if not files:
        raise ValueError(f'no {JUDGE_SHEET_GLOB} in {folder}')
    return files


def check_coverage(labels, expected_ids, who):
    expected = set(expected_ids)
    missing, extra = expected - set(labels), set(labels) - expected
    if missing or extra:
        raise ValueError(f'{who}: {len(missing)} item(s) unlabelled, {len(extra)} unknown item id(s)')


def check_files_match_qid(labels, sheet_by_id, who):
    """labels_s8_sheet_<qid>.csv may only hold that query's items."""
    for item_id, label in labels.items():
        qid = label['_file'][len('labels_s8_sheet_'):-len('.csv')]
        if sheet_by_id[item_id]['qid'] != qid:
            raise ValueError(f"{who}: {label['_file']} holds an item of another query")


# ---------------------------------------------------------------- agreement
def confusion(pairs, k=4):
    m = np.zeros((k, k), dtype=np.int64)
    for a, b in pairs:
        m[a, b] += 1
    return m


def binary_confusion(m, threshold=THRESHOLD):
    lo, hi = slice(0, threshold), slice(threshold, None)
    return np.array([[m[lo, lo].sum(), m[lo, hi].sum()], [m[hi, lo].sum(), m[hi, hi].sum()]])


def kappa_from_confusion(m, weights='none'):
    """Cohen kappa from a k x k confusion matrix; NaN when chance agreement is 1 (undefined)."""
    m = np.asarray(m, dtype=float)
    k, n = m.shape[0], m.sum()
    if n == 0:
        return float('nan')
    i, j = np.indices((k, k))
    w = 1 - (i - j) ** 2 / (k - 1) ** 2 if weights == 'quadratic' else (i == j).astype(float)
    po = (w * m).sum() / n
    pe = (w * np.outer(m.sum(1), m.sum(0))).sum() / n ** 2
    return float('nan') if np.isclose(pe, 1.0) else float((po - pe) / (1 - pe))


def raw_agreement(m):
    m = np.asarray(m)
    return float(np.trace(m) / m.sum()) if m.sum() else float('nan')


def bootstrap_ci(per_qid, stat, n=BOOTSTRAP, seed=SEED):
    """Percentile 95% CI, resampling QUERIES with replacement; per_qid = {qid: 4x4 confusion}."""
    qids = sorted(per_qid)
    stack = np.stack([per_qid[q] for q in qids]).reshape(len(qids), -1)
    draws = np.random.default_rng(seed).integers(len(qids), size=(n, len(qids)))
    counts = np.zeros((n, len(qids)), dtype=np.int64)
    np.add.at(counts, (np.repeat(np.arange(n), len(qids)), draws.ravel()), 1)
    mats = (counts @ stack).reshape(n, 4, 4)
    values = np.array([stat(mm) for mm in mats])
    ok = values[~np.isnan(values)]
    lo, hi = (np.percentile(ok, [2.5, 97.5]) if len(ok) else (float('nan'), float('nan')))
    return dict(lo=round(float(lo), 4), hi=round(float(hi), 4), draws=n, undefined=int(n - len(ok)), seed=seed)


def stats_block(m):
    b = binary_confusion(m)
    return dict(n=int(m.sum()), raw_exact=round(raw_agreement(m), 4), raw_binary=round(raw_agreement(b), 4),
                kappa_binary=_r(kappa_from_confusion(b)), kappa_quadratic=_r(kappa_from_confusion(m, 'quadratic')),
                confusion_4x4_rows_A_cols_B=m.tolist())


def _r(x):
    return None if np.isnan(x) else round(x, 4)


def agreement(sheet_rows, a, b, *, bootstrap=BOOTSTRAP, seed=SEED):
    per_qid = defaultdict(lambda: np.zeros((4, 4), dtype=np.int64))
    for row in sheet_rows:
        per_qid[row['qid']][int(a[row['item_id']]['grade']), int(b[row['item_id']]['grade'])] += 1
    total = sum(per_qid.values())
    result = stats_block(total)
    result['ci95_kappa_binary'] = bootstrap_ci(dict(per_qid), lambda m: kappa_from_confusion(binary_confusion(m)),
                                               bootstrap, seed)
    result['ci95_kappa_quadratic'] = bootstrap_ci(dict(per_qid), lambda m: kappa_from_confusion(m, 'quadratic'),
                                                  bootstrap, seed)
    result['queries'] = len(per_qid)
    return result


# ---------------------------------------------------------------- merge + audit
def is_rel(grade):
    return int(grade) >= THRESHOLD


def merge(sheet_rows, a, b):
    """Same side of the threshold -> accepted with the LOWER grade; opposite sides -> dispute."""
    accepted, disputes = {}, []
    for row in sheet_rows:
        ga, gb = a[row['item_id']]['grade'], b[row['item_id']]['grade']
        if is_rel(ga) == is_rel(gb):
            accepted[row['item_id']] = min(int(ga), int(gb))
        else:
            disputes.append(row['item_id'])
    return accepted, disputes


def audit_check(audit, accepted, threshold_disputes):
    """Shyam (blind) vs A/B consensus on audit items that have a consensus (accepted)."""
    on_accepted = [i for i in sorted(audit) if i in accepted]
    b = np.zeros((2, 2), dtype=np.int64)
    disagree = []
    for item_id in on_accepted:
        s, c = is_rel(audit[item_id]['grade']), is_rel(accepted[item_id])
        b[int(s), int(c)] += 1
        if s != c:
            disagree.append(item_id)
    kappa = kappa_from_confusion(b)
    flag = 'PASS' if not np.isnan(kappa) and kappa >= AUDIT_PASS else 'STOP'
    return dict(audit_items=len(audit), on_accepted=len(on_accepted),
                on_disputes=len(set(audit) & set(threshold_disputes)),
                kappa_binary=_r(kappa), raw_binary=round(raw_agreement(b), 4) if b.sum() else None,
                confusion_2x2_rows_shyam_cols_consensus=b.tolist(), disagreements=len(disagree),
                rule=f'kappa_binary >= {AUDIT_PASS} -> PASS else STOP', flag=flag), disagree


def dispute_rows(sheet_by_id, a, b, reasons):
    rows = []
    for item_id, reason in reasons:
        s = sheet_by_id[item_id]
        row = dict(item_id=item_id, reason=reason, qid=s['qid'], query=s['query'], source_doc=s['source_doc'],
                   clause_id=s['clause_id'], title=s['title'])
        for j, labels in (('a', a), ('b', b)):
            row.update({f'{j}_{f}': labels[item_id][f] for f in JUDGE_FIELDS})
        rows.append(row)
    return rows


def csv_text(rows, columns):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(columns), lineterminator='\r\n')
    writer.writeheader()
    writer.writerows(rows)
    return '﻿' + stream.getvalue()


def read_disputes(path):
    columns, rows = read_csv(path)
    if columns != DISPUTE_COLUMNS:
        raise ValueError(f'{Path(path).name}: not a disputes.csv')
    return rows


def qrels_text(rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=qrels_io.FIELDS, delimiter='\t', lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


# ---------------------------------------------------------------- audit freeze
def load_audit_freeze(freeze_path, audit_labels_path):
    freeze_path = Path(freeze_path)
    if not freeze_path.exists():
        raise PermissionError(f'{freeze_path.name} missing: run s8-freeze-audit first')
    freeze = json.loads(freeze_path.read_text(encoding='utf-8'))
    if audit_labels_path is not None and bytes_sha(audit_labels_path) != freeze['labels_sha256']:
        raise PermissionError('audit labels file differs from the frozen one (sha mismatch)')
    return freeze


# ---------------------------------------------------------------- pipeline
def write_json(path, value):
    return write_once(path, json.dumps(value, indent=2, sort_keys=True) + '\n')


def run_merge(*, sheet, a_files, b_files, out, audit_ids=None, audit_file=None, freeze_path=None,
              referee_file=None, bootstrap=BOOTSTRAP, _test_skip_freeze=False):
    """Returns a counts-only summary; writes what the available inputs allow (write-once)."""
    out = Path(out)
    sheet_rows = read_sheet(sheet)
    sheet_by_id = {r['item_id']: r for r in sheet_rows}
    a, b = read_labels(a_files, 'judge A'), read_labels(b_files, 'judge B')
    for who, labels in (('judge A', a), ('judge B', b)):
        check_coverage(labels, sheet_by_id, who)
        check_files_match_qid(labels, sheet_by_id, who)
    accepted, threshold_disputes = merge(sheet_rows, a, b)
    inputs = dict(sheet_sha256=bytes_sha(sheet),
                  a_files={Path(p).name: bytes_sha(p) for p in a_files},
                  b_files={Path(p).name: bytes_sha(p) for p in b_files})
    ab = agreement(sheet_rows, a, b, bootstrap=bootstrap)
    ab.update(inputs=inputs, accepted=len(accepted), threshold_disputes=len(threshold_disputes),
              grade_counts=dict(A=_grade_counts(a), B=_grade_counts(b)),
              unsure=dict(A=sum(l['unsure'] == 'yes' for l in a.values()),
                          B=sum(l['unsure'] == 'yes' for l in b.values())))
    states = {'agreement_ab.json': write_json(out / 'agreement_ab.json', ab)}
    summary = dict(stage=1, items=len(sheet_rows), ab=_brief(ab), states=states)
    if audit_file is None:
        return summary

    if not _test_skip_freeze:
        freeze = load_audit_freeze(freeze_path, audit_file)
    else:
        freeze = dict(labels_sha256=bytes_sha(audit_file), frozen_at='TEST-ONLY 1970-01-01T00:00:00')
    audit = read_labels([audit_file], 'audit')
    check_coverage(audit, audit_ids, 'audit')
    check, disagree = audit_check(audit, accepted, threshold_disputes)
    check.update(audit_labels_sha256=freeze['labels_sha256'], audit_frozen_at=freeze['frozen_at'])
    states['audit_check.json'] = write_json(out / 'audit_check.json', check)
    summary.update(stage=2, audit={k: check[k] for k in ('audit_items', 'on_accepted', 'on_disputes',
                                                          'kappa_binary', 'disagreements', 'flag')})
    if check['flag'] != 'PASS':
        summary['stopped'] = 'audit STOP: no referee list, no qrels'
        return summary

    reasons = [(i, 'threshold_split') for i in threshold_disputes]
    reasons += [(i, 'audit_disagrees') for i in disagree]
    order = {r['item_id']: n for n, r in enumerate(sheet_rows)}
    reasons.sort(key=lambda r: order[r[0]])
    referee_ids = [i for i, _ in reasons]
    states['disputes.csv'] = write_once(out / 'disputes.csv',
                                        csv_text(dispute_rows(sheet_by_id, a, b, reasons), DISPUTE_COLUMNS))
    states['referee_sheet.csv'] = write_once(out / 'referee_sheet.csv',
                                             sheet_csv([sheet_by_id[i] for i in referee_ids]))
    states['referee_ids.txt'] = write_once(out / 'referee_ids.txt', ''.join(i + '\n' for i in referee_ids))
    summary.update(stage=2, referee=dict(Counter(r for _, r in reasons)) | {'total': len(referee_ids)})
    if referee_file is None:
        return summary

    referee = read_labels([referee_file], 'referee')
    check_coverage(referee, referee_ids, 'referee')
    day = freeze['frozen_at'][:10] if not _test_skip_freeze else '1970-01-01'
    rows, source = [], Counter()
    for row in sheet_rows:
        item_id = row['item_id']
        if item_id in referee:
            grade, judge = int(referee[item_id]['grade']), 'referee'
        else:
            grade, judge = accepted[item_id], 'AB_min'
        source[judge] += 1
        rows.append(dict(qid=row['qid'], clause_id=row['clause_id'], grade=str(grade), judge=judge, date=day))
    qrels_io.validate(rows)
    states['qrels.tsv'] = write_once(out / 'qrels.tsv', qrels_text(rows))
    meta = dict(pairs=len(rows), source=dict(source), grades=dict(Counter(r['grade'] for r in rows)),
                relevant=sum(int(r['grade']) >= THRESHOLD for r in rows),
                referee_sha256=bytes_sha(referee_file), audit_labels_sha256=freeze['labels_sha256'], **inputs)
    states['qrels_meta.json'] = write_json(out / 'qrels_meta.json', meta)
    summary.update(stage=3, qrels={k: meta[k] for k in ('pairs', 'source', 'grades', 'relevant')})
    return summary


def _grade_counts(labels):
    return {g: sum(l['grade'] == g for l in labels.values()) for g in GRADES}


def _brief(ab):
    return {k: ab[k] for k in ('n', 'raw_exact', 'raw_binary', 'kappa_binary', 'kappa_quadratic',
                               'ci95_kappa_binary', 'ci95_kappa_quadratic', 'accepted', 'threshold_disputes')}


def main(argv=None):
    out = output_dir()
    parser = argparse.ArgumentParser(prog='tasks.py s8-merge-labels', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['s8-merge-labels'])
    parser.add_argument('--a', required=True, help="judge A output/ folder (labels_s8_sheet_<qid>.csv)")
    parser.add_argument('--b', required=True, help="judge B output/ folder")
    parser.add_argument('--audit', help='Shyam audit labels CSV (needs audit_freeze.json)')
    parser.add_argument('--referee', help='referee labels CSV (needs audit PASS)')
    parser.add_argument('--sheet', default=str(out / 'sheet' / 's8_sheet.csv'))
    parser.add_argument('--audit-dir', default=str(out / 'audit'))
    parser.add_argument('--out', default=str(out / 'labels'))
    args = parser.parse_args(argv)
    if args.referee and not args.audit:
        parser.error('--referee needs --audit')
    audit_dir = Path(args.audit_dir)
    audit_ids = None
    if args.audit:
        audit_ids = (audit_dir / 'audit_ids.txt').read_text(encoding='utf-8').split()
    summary = run_merge(sheet=args.sheet, a_files=judge_files(args.a), b_files=judge_files(args.b), out=args.out,
                        audit_ids=audit_ids, audit_file=args.audit, freeze_path=audit_dir / 'audit_freeze.json',
                        referee_file=args.referee)
    print('merge: ' + json.dumps(summary, indent=1, sort_keys=True))
    for name in summary['states']:
        print(f'{Path(args.out) / name}  sha256={bytes_sha(Path(args.out) / name)}')
    return 0 if 'stopped' not in summary else 2


if __name__ == '__main__':
    raise SystemExit(main())
