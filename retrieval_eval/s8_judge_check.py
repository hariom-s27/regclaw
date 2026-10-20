"""S8 Part D: judge-output validator, per-judge freeze and run-to-run comparison (offline, 0 API calls).

Commands (via tasks.py):
  s8-validate-judge --judge A|B|C [--root PATH] [--require-all]
      kit eval/ copies byte-identical to the per-query sheets; per output file: exact header, one row per
      sheet item, allowed values, evidence_span copied from the clause (whitespace-normalised,
      case-sensitive). Writes a NEW labels/validate_<X>_<UTC>.json (counts + failing item_ids only).
  s8-freeze-judge --judge A|B|C --model TEXT [--root PATH]
      validator with --require-all; every file must PASS (warnings allowed). Write-once
      labels/judge_<X>_freeze.json with the sha256 of each output file; s8-merge-labels refuses
      a judge whose files differ from its freeze.
  s8-compare-runs --a DIR --b DIR --name NAME
      two output folders of the SAME judge: agreement on common items (aggregate only).
      Writes a NEW labels/compare_<NAME>_<UTC>.json.

Reads judge output files and sheets only (never pool_key, runs or item maps); judge folders are read-only.
Prints counts and item_ids only, never a grade, clause state, span or note of an item.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import numpy as np

from retrieval_eval.s8_labels import (CLAUSE_STATES, GRADES, JURISDICTION_MATCH, LABEL_FIELDS, UNSURE, _r,
                                      binary_confusion, confusion, kappa_from_confusion, normalise_ws, raw_agreement,
                                      read_csv, read_sheet)
from retrieval_eval.s8_runs import bytes_sha, output_dir, write_once

KIT_DEFAULTS = {'A': Path('D:/s8_judgeA'), 'B': Path('D:/s8_judgeB'), 'C': Path('D:/s8_judgeC')}
ALLOWED = {'clause_states': CLAUSE_STATES, 'jurisdiction_match': JURISDICTION_MATCH, 'grade': GRADES,
           'unsure': UNSURE}
SPAN_MAX_WORDS, QUERY_ASKS_MAX_WORDS, NOTE_MAX_WORDS = 30, 20, 25  # guideline v1.0.1 output format (warnings)
OUTPUT_NAME = 'labels_s8_sheet_{qid}.csv'
SHEET_NAME = 's8_sheet_{qid}.csv'
FREEZE_NAME = 'judge_{judge}_freeze.json'
NAME_RE = re.compile(r'^[A-Za-z0-9_-]+$')


def utc_stamp(now=None):
    return (now or datetime.now(timezone.utc)).strftime('%Y%m%dT%H%M%SZ')


def qids_from(per_query_dir):
    """The qids that have a source-of-truth per-query sheet (16: Q1-Q13, R1-R3)."""
    names = sorted(Path(per_query_dir).glob(SHEET_NAME.format(qid='*')))
    if not names:
        raise ValueError(f'no per-query sheets in {per_query_dir}')
    return [p.name[len('s8_sheet_'):-len('.csv')] for p in names]


def _words(text):
    return len((text or '').split())


# ---------------------------------------------------------------- A. validator
def check_kit_eval(root, per_query_dir, qids):
    """Problems (empty = clean): each kit eval/ sheet must be byte-identical to the source of truth."""
    problems = []
    for qid in qids:
        name = SHEET_NAME.format(qid=qid)
        kit = Path(root) / 'eval' / name
        if not kit.is_file():
            problems.append(f'eval/{name} missing')
        elif bytes_sha(kit) != bytes_sha(Path(per_query_dir) / name):
            problems.append(f'eval/{name} sha256 differs from the source-of-truth sheet')
    return problems


def validate_file(path, sheet_rows):
    """Counts + failing item_ids for one judge output file (never field values)."""
    columns, rows = read_csv(path)
    text_of = {r['item_id']: r['text'] for r in sheet_rows}
    result = dict(file=Path(path).name, header_ok=columns == list(LABEL_FIELDS), rows=len(rows))
    if not result['header_ok']:
        result.update(status='FAIL', fail_reasons=['header is not exactly: ' + ', '.join(LABEL_FIELDS)])
        return result
    ids = [(row.get('item_id') or '').strip() for row in rows]
    seen = Counter(ids)
    invalid = {c: [] for c in ALLOWED}
    bad_shape, empty_span, not_found = [], [], []
    none_spans = span_long = asks_long = note_long = 0
    for item_id, row in zip(ids, rows):
        if None in row or any(v is None for v in row.values()):  # more / fewer cells than the header
            bad_shape.append(item_id)
            continue
        for column, allowed in ALLOWED.items():
            if row[column].strip() not in allowed:
                invalid[column].append(item_id)
        span = row['evidence_span']
        if not span.strip():
            empty_span.append(item_id)
        elif span.strip().lower() == 'none':
            none_spans += 1
        else:
            span_long += _words(span) > SPAN_MAX_WORDS
            if item_id in text_of and normalise_ws(span) not in normalise_ws(text_of[item_id]):
                not_found.append(item_id)
        asks_long += _words(row['query_asks']) > QUERY_ASKS_MAX_WORDS
        note_long += _words(row['note']) > NOTE_MAX_WORDS
    sheet_ids = [r['item_id'] for r in sheet_rows]
    known = set(sheet_ids)
    missing = [i for i in sheet_ids if i not in seen]
    duplicates = sorted(i for i, n in seen.items() if n > 1)
    unknown = sorted({i for i in ids if i not in known})
    in_file = list(dict.fromkeys(i for i in ids if i in known))
    order_differs = in_file != [i for i in sheet_ids if i in seen]
    grades = Counter(row['grade'].strip() for row in rows if row.get('grade') is not None)
    invalid = {c: v for c, v in invalid.items() if v}
    if bad_shape:
        invalid['_row_cells'] = bad_shape
    result.update(
        missing=len(missing), duplicates=len(duplicates), unknown=len(unknown),
        invalid_values=sum(len(v) for v in invalid.values()), quote_not_found=len(not_found),
        empty_span=len(empty_span), none_spans=none_spans, grade_counts={g: grades[g] for g in GRADES},
        failing_item_ids=dict(missing=missing, duplicates=duplicates, unknown=unknown, invalid_values=invalid,
                              empty_span=empty_span, quote_not_found=not_found),
        warnings=dict(order_differs=order_differs, none_spans=none_spans, evidence_span_over_30_words=span_long,
                      query_asks_over_20_words=asks_long, note_over_25_words=note_long))
    fails = [k for k in ('missing', 'duplicates', 'unknown', 'invalid_values', 'empty_span', 'quote_not_found')
             if result[k]]
    result.update(status='FAIL' if fails else 'PASS', fail_reasons=fails)
    return result


def validate_judge(judge, root, per_query_dir, require_all=False, now=None):
    """The full report (dict); overall PASS / PENDING (no FAIL, some files not written yet) / FAIL."""
    root, per_query_dir = Path(root), Path(per_query_dir)
    qids = qids_from(per_query_dir)
    kit = check_kit_eval(root, per_query_dir, qids)
    files = {}
    for qid in qids:
        path = root / 'output' / OUTPUT_NAME.format(qid=qid)
        if not path.is_file():
            files[qid] = dict(file=path.name, status='FAIL' if require_all else 'pending',
                              fail_reasons=['missing file (--require-all)'] if require_all else [])
            continue
        files[qid] = validate_file(path, read_sheet(per_query_dir / SHEET_NAME.format(qid=qid)))
    status = Counter(f['status'] for f in files.values())
    overall = 'FAIL' if kit or status['FAIL'] else ('PENDING' if status['pending'] else 'PASS')
    return dict(judge=judge, root=str(root), require_all=require_all,
                generated_at=(now or datetime.now(timezone.utc)).isoformat(timespec='seconds'),
                kit_eval_problems=kit, files=files, overall=overall,
                counts=dict(PASS=status['PASS'], FAIL=status['FAIL'], pending=status['pending'], files=len(qids)),
                sheet_sha256={q: bytes_sha(per_query_dir / SHEET_NAME.format(qid=q)) for q in qids})


def write_report(report, labels_dir, now=None):
    path = Path(labels_dir) / f"validate_{report['judge']}_{utc_stamp(now)}.json"
    if path.exists():
        raise FileExistsError(f'{path.name} exists; reports are never overwritten')
    write_once(path, json.dumps(report, indent=2, sort_keys=True) + '\n')
    return path


TABLE = ('qid', 'rows', 'missing', 'dup', 'unknown', 'invalid', 'quote_nf', 'empty_span', 'none_spans',
         'g0', 'g1', 'g2', 'g3', 'status')


def table_lines(report):
    lines = [' '.join(f'{h:>10}' for h in TABLE)]
    for qid, f in report['files'].items():
        if 'missing' not in f:  # pending, missing with --require-all, or a bad header
            cells = [qid, f.get('rows', '-'), *['-'] * 11, f['status']]
        else:
            g = f['grade_counts']
            cells = [qid, f['rows'], f['missing'], f['duplicates'], f['unknown'], f['invalid_values'],
                     f['quote_not_found'], f['empty_span'], f['none_spans'], g['0'], g['1'], g['2'], g['3'],
                     f['status']]
        lines.append(' '.join(f'{c!s:>10}' for c in cells))
    c = report['counts']
    lines.append(f"OVERALL judge {report['judge']}: {report['overall']}  (PASS {c['PASS']}, FAIL {c['FAIL']}, "
                 f"pending {c['pending']} of {c['files']}; kit eval problems {len(report['kit_eval_problems'])})")
    return lines


def warning_lines(report):
    lines = [f'kit: {p}' for p in report['kit_eval_problems']]
    for qid, f in report['files'].items():
        for reason in f.get('fail_reasons', []):
            ids = f.get('failing_item_ids', {}).get(reason)
            lines.append(f'FAIL {qid} {reason}: ' + (json.dumps(ids) if ids else ''))
        warn = {k: v for k, v in f.get('warnings', {}).items() if v}
        if warn:
            lines.append(f'warn {qid} ' + json.dumps(warn, sort_keys=True))
    return lines


# ---------------------------------------------------------------- B. freeze
def freeze_judge(judge, model, root, per_query_dir, labels_dir, now=None):
    """Write-once judge_<X>_freeze.json; refuses unless every one of the files PASSes."""
    if not str(model).strip():
        raise ValueError('--model needs the exact model text')
    labels_dir = Path(labels_dir)
    target = labels_dir / FREEZE_NAME.format(judge=judge)
    if target.exists():
        raise FileExistsError(f'{target.name} exists; judge {judge} is already frozen')
    report = validate_judge(judge, root, per_query_dir, require_all=True, now=now)
    report_path = write_report(report, labels_dir, now)
    if report['overall'] != 'PASS':
        raise PermissionError(f"judge {judge}: validation {report['overall']} ({report['counts']}); "
                              f'not frozen (report {report_path.name})')
    out = Path(root) / 'output'
    names = {qid: f['file'] for qid, f in report['files'].items()}
    record = dict(judge=judge, model=str(model).strip(),
                  frozen_at=(now or datetime.now(timezone.utc)).isoformat(timespec='seconds'),
                  validate_report=report_path.name, root=str(root),
                  files={name: bytes_sha(out / name) for name in names.values()},
                  counts={names[q]: dict(rows=f['rows'], grade_counts=f['grade_counts'], none_spans=f['none_spans'],
                                         warnings=f['warnings']) for q, f in report['files'].items()})
    write_once(target, json.dumps(record, indent=2, sort_keys=True) + '\n')
    return record, target


def check_judge_freezes(files_by_judge, freeze_dir):
    """s8-merge-labels gate: every judge given needs its freeze, with the same file set and shas."""
    for judge, paths in files_by_judge.items():
        path = Path(freeze_dir) / FREEZE_NAME.format(judge=judge)
        if not path.exists():
            raise PermissionError(f'{path.name} missing: run s8-freeze-judge --judge {judge} first')
        frozen = json.loads(path.read_text(encoding='utf-8'))['files']
        given = {Path(p).name: Path(p) for p in paths}
        if set(given) != set(frozen):
            raise PermissionError(f'judge {judge}: files differ from {path.name} (not given: '
                                  f'{sorted(set(frozen) - set(given))}, not frozen: {sorted(set(given) - set(frozen))})')
        for name in sorted(given):
            if bytes_sha(given[name]) != frozen[name]:
                raise PermissionError(f'judge {judge}: {name} differs from {path.name} (sha256 mismatch)')


# ---------------------------------------------------------------- D. compare runs
def read_run(folder):
    """{item_id: grade int} over labels_s8_sheet_*.csv, plus the ids whose grade is invalid."""
    files = sorted(Path(folder).glob(OUTPUT_NAME.format(qid='*')))
    if not files:
        raise ValueError(f'no {OUTPUT_NAME.format(qid="*")} in {folder}')
    grades, invalid, seen = {}, set(), set()
    for path in files:
        columns, rows = read_csv(path)
        if 'item_id' not in columns or 'grade' not in columns:
            raise ValueError(f'{path.name}: needs item_id and grade columns')
        for row in rows:
            item_id = (row.get('item_id') or '').strip()
            if item_id in seen:
                raise ValueError(f'{Path(folder).name}: item {item_id} appears more than once')
            seen.add(item_id)
            grade = (row.get('grade') or '').strip()
            if grade in GRADES:
                grades[item_id] = int(grade)
            else:
                invalid.add(item_id)
    return grades, invalid, len(files)


def compare_runs(a_dir, b_dir, name, now=None):
    if not NAME_RE.match(name or ''):
        raise ValueError('--name: letters, digits, _ and - only')
    a, a_bad, a_files = read_run(a_dir)
    b, b_bad, b_files = read_run(b_dir)
    common = sorted(set(a) & set(b))
    m = confusion([(a[i], b[i]) for i in common]) if common else np.zeros((4, 4), dtype=np.int64)
    bm = binary_confusion(m)
    return dict(name=name, a=str(a_dir), b=str(b_dir), generated_at=(now or datetime.now(timezone.utc)).isoformat(
                    timespec='seconds'),
                files_a=a_files, files_b=b_files, items_a=len(a) + len(a_bad), items_b=len(b) + len(b_bad),
                n=len(common), only_a=len((set(a) | a_bad) - (set(b) | b_bad)),
                only_b=len((set(b) | b_bad) - (set(a) | a_bad)),
                excluded_invalid_grade=len(((set(a) | a_bad) & (set(b) | b_bad)) - set(common)),
                exact_agreement=_r(raw_agreement(m)) if common else None,
                binary_agreement=_r(raw_agreement(bm)) if common else None,
                kappa_binary=_r(kappa_from_confusion(bm)), kappa_quadratic=_r(kappa_from_confusion(m, 'quadratic')),
                threshold='relevant = grade >= 2',
                confusion_4x4_rows_a_cols_b=m.tolist(), confusion_2x2_rows_a_cols_b=bm.tolist())


def write_compare(result, labels_dir, now=None):
    path = Path(labels_dir) / f"compare_{result['name']}_{utc_stamp(now)}.json"
    if path.exists():
        raise FileExistsError(f'{path.name} exists; reports are never overwritten')
    write_once(path, json.dumps(result, indent=2, sort_keys=True) + '\n')
    return path


# ---------------------------------------------------------------- CLI
def main(argv=None):
    out = output_dir()
    parser = argparse.ArgumentParser(prog='tasks.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    for name, text in (('s8-validate-judge', 'Validate one judge kit output (read-only)'),
                       ('s8-freeze-judge', 'Freeze one judge output (all files must PASS; write-once)')):
        p = sub.add_parser(name, help=text)
        p.add_argument('--judge', required=True, choices=sorted(KIT_DEFAULTS))
        p.add_argument('--root', help='kit root (default D:\\s8_judge<X>)')
        p.add_argument('--per-query', default=str(out / 'sheet' / 'per_query'))
        p.add_argument('--labels-dir', default=str(out / 'labels'))
        if name == 's8-validate-judge':
            p.add_argument('--require-all', action='store_true', help='a missing output file is a FAIL')
        else:
            p.add_argument('--model', required=True, help='the exact model text of this judge')
    cmp = sub.add_parser('s8-compare-runs', help='Agreement between two runs of the same judge (aggregate)')
    cmp.add_argument('--a', required=True)
    cmp.add_argument('--b', required=True)
    cmp.add_argument('--name', required=True)
    cmp.add_argument('--labels-dir', default=str(out / 'labels'))
    args = parser.parse_args(argv)

    if args.command == 's8-compare-runs':
        result = compare_runs(args.a, args.b, args.name)
        path = write_compare(result, args.labels_dir)
        print('compare: ' + json.dumps(result, indent=1, sort_keys=True))
        print(f'{path}  sha256={bytes_sha(path)}')
        return 0
    root = Path(args.root) if args.root else KIT_DEFAULTS[args.judge]
    if args.command == 's8-freeze-judge':
        record, path = freeze_judge(args.judge, args.model, root, args.per_query, args.labels_dir)
        print(f"frozen judge {args.judge}: {len(record['files'])} files, report {record['validate_report']}")
        print(f'{path}  sha256={bytes_sha(path)}')
        return 0
    report = validate_judge(args.judge, root, args.per_query, args.require_all)
    path = write_report(report, args.labels_dir)
    for line in (*table_lines(report), *warning_lines(report)):
        print(line)
    print(f'{path}  sha256={bytes_sha(path)}')
    return 1 if report['overall'] == 'FAIL' else 0


if __name__ == '__main__':
    raise SystemExit(main())
