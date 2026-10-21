"""S8 Part D: label schema, validation, judge agreement, merge, audit check and final qrels.

Command (via tasks.py): s8-merge-labels. Reads ONLY sheets and label files (never pool_key, runs
or item maps), so it cannot report anything per system. Prints COUNTS ONLY. Outputs are write-once.

Modes (Addendum 2 note 3):
  three_judge         --a --b --c: all three on the same side of the threshold -> accepted, final = MEDIAN;
                      any split -> dispute. An item where exactly one judge's row is missing or invalid
                      (after the one re-run) uses the two-judge rule; the count is reported. More than 2% of
                      C's rows missing/invalid -> refused (use the note 3 section 6 fallback). Items a judge
                      freeze lists as excluded (s8-freeze-judge --exclude-items) are such items: that judge's
                      rows are ignored, the other two decide (excluded_by_judge counts).
  two_judge_fallback  --a --b [--c] --two-judge-fallback REASON: Addendum 2 + notes 1-2 for every item
                      (same side -> LOWER grade; split -> dispute); C, if given, is a secondary comparison only.
  two_judge           --a --b only: stage 1 only (stage 2 needs --c or the fallback with its reason).

Stages (each needs the previous one):
  1. judge labels      -> agreement_abc.json (pairwise Cohen kappa binary + quadratic, Fleiss kappa binary)
                          or agreement_ab.json (two-judge modes); bootstrap CIs over queries
  2. + frozen audit    -> (audit set = the N frozen items, a gap-free prefix of audit_order.txt)
                          audit_check.json (primary kappa binary author vs the three-judge consensus, items
                          decided by the two-judge rule excluded and counted (note 3 section 4); secondary
                          without the author's unsure items; PASS >= 0.60 on the primary else STOP);
                          on PASS: disputes.csv, referee_sheet.csv, referee_ids.txt
  3. + referee labels  -> qrels.tsv + qrels_meta.json (note 2 sections 2-3, note 3 section 3):
                          threshold_split -> referee grade (unsure=yes -> median of three / lower of two);
                          audit_disagrees -> consensus stands unless override_quote (found in the clause
                          text) AND override_rule (1-16); every referee item needs a reason_code.
"""
import argparse
from collections import Counter, defaultdict
import csv
import io
from itertools import combinations
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
JUDGES = ('A', 'B', 'C')
DISPUTE_COLUMNS = (['item_id', 'reason', 'qid', 'query', 'source_doc', 'clause_id', 'title']
                   + [f'{j}_{f}' for j in ('a', 'b', 'c') for f in JUDGE_FIELDS])
REFEREE_FIELDS = ('reason_code', 'override_quote', 'override_rule')  # referee labels only
REASON_CODES = ('clear_reading', 'guideline_gap', 'unclear_text')  # Addendum 2 section 6, adapted to 3 judges
GUIDELINE_RULES = range(1, 17)  # guideline v1.0.1 rules 1-16
CATEGORIES = ('accepted_median', 'accepted_lower', 'dispute_referee', 'dispute_unsure_median',
              'dispute_unsure_lower', 'auditor_disagrees_kept', 'auditor_override_evidence')
C_MISSING_MAX = 0.02  # note 3 section 6: > 2% of C's rows missing/invalid -> two-judge fallback
AUDIT_PASS = 0.60
BOOTSTRAP = 10_000
SEED = 20261020
JUDGE_SHEET_GLOB = 'labels_s8_sheet_*.csv'
REPORT_SENTENCE = ('The author, a non-specialist, audited {n} items blind; the LLM consensus was not overridden '
                   'without quoted clause text and a named guideline rule.')


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


def validate_label(row, where, extra=()):
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
    label = {f: (row.get(f) or '').strip() if f in ('item_id', 'grade', 'clause_states',
                                                    'jurisdiction_match', 'unsure') else (row.get(f) or '')
             for f in (*LABEL_FIELDS, 'timestamp')}
    label.update({f: (row.get(f) or '') if f == 'override_quote' else (row.get(f) or '').strip() for f in extra})
    return label


def normalise_ws(text):
    return ' '.join(text.split())


def quote_in_text(quote, text):
    """Whitespace-normalised, case-sensitive substring test (note 2 section 2a)."""
    return normalise_ws(quote) in normalise_ws(text)


def validate_referee(label, where, clause_text):
    """reason_code required; override_rule empty or 1..16; a non-empty override_quote must be in the clause."""
    errors = []
    if label.get('reason_code', '') not in REASON_CODES:
        errors.append(f"reason_code {label.get('reason_code', '')!r} not one of {'|'.join(REASON_CODES)}")
    rule = label.get('override_rule', '')
    if rule and not (rule.isascii() and rule.isdigit() and int(rule) in GUIDELINE_RULES):
        errors.append(f'override_rule {rule!r} not a guideline rule 1..16')
    quote = label.get('override_quote', '')
    if quote.strip() and not quote_in_text(quote, clause_text):
        errors.append('override_quote not found in the clause text (whitespace-normalised, case-sensitive)')
    if errors:
        raise ValueError(f'{where}: ' + '; '.join(errors))


def read_labels(paths, who, extra=()):
    """Labels from one or more CSV files -> {item_id: row}; every item exactly once.
    `extra` columns (the referee fields) are allowed and returned ('' when absent)."""
    labels = {}
    for path in paths:
        path = Path(path)
        columns, rows = read_csv(path)
        missing = [f for f in REQUIRED_FIELDS if f not in columns]
        unknown = [c for c in columns if c not in LABEL_FIELDS and c not in OPTIONAL_FIELDS and c not in extra]
        if missing or unknown:
            raise ValueError(f'{who} {path.name}: missing columns {missing}, unknown columns {unknown}')
        for n, row in enumerate(rows, start=2):
            label = validate_label(row, f'{who} {path.name} line {n}', extra)
            if label['item_id'] in labels:
                raise ValueError(f"{who}: item {label['item_id']} labelled more than once")
            label['_file'] = path.name
            labels[label['item_id']] = label
    return labels


def read_judge(paths, who, sheet_by_id, excluded=()):
    """Tolerant judge read (note 3 section 3): returns (valid {item_id: label}, problems {item_id: (file, kind)}).
    Row-level problems (an invalid field, a missing item) are returned; file-level problems (columns,
    empty/unknown item id, an item twice, an item in another query's file) raise. Rows of `excluded` items
    (the judge's freeze) are ignored and are neither labels nor problems."""
    valid, problems, seen = {}, {}, set()
    for path in map(Path, paths):
        columns, rows = read_csv(path)
        missing = [f for f in REQUIRED_FIELDS if f not in columns]
        unknown = [c for c in columns if c not in LABEL_FIELDS and c not in OPTIONAL_FIELDS]
        if missing or unknown:
            raise ValueError(f'{who} {path.name}: missing columns {missing}, unknown columns {unknown}')
        qid = path.name[len('labels_s8_sheet_'):-len('.csv')]
        for n, row in enumerate(rows, start=2):
            where = f'{who} {path.name} line {n}'
            item_id = (row.get('item_id') or '').strip()
            if item_id in excluded:
                continue
            if item_id not in sheet_by_id:
                raise ValueError(f'{where}: ' + ('unknown item id' if item_id else 'empty item_id'))
            if item_id in seen:
                raise ValueError(f'{who}: item {item_id} labelled more than once')
            seen.add(item_id)
            if sheet_by_id[item_id]['qid'] != qid:
                raise ValueError(f'{who}: {path.name} holds an item of another query')
            try:
                label = validate_label(row, where)
            except ValueError:
                problems[item_id] = (path.name, 'invalid')
                continue
            label['_file'] = path.name
            valid[item_id] = label
    for item_id, row in sheet_by_id.items():
        if item_id not in seen and item_id not in excluded:
            problems[item_id] = (f"labels_s8_sheet_{row['qid']}.csv", 'missing')
    return valid, problems


def problem_counts(problems):
    """Counts only (no field values): missing / invalid, and per file (the file to re-run)."""
    kinds = Counter(kind for _, kind in problems.values())
    return dict(missing=kinds['missing'], invalid=kinds['invalid'],
                files=dict(sorted(Counter(f for f, _ in problems.values()).items())))


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


def fleiss_item(k, raters=3):
    """Per-item sums for binary Fleiss kappa: [1 item, P_i, k] where k of `raters` judges say relevant."""
    return np.array([1.0, (k * (k - 1) + (raters - k) * (raters - k - 1)) / (raters * (raters - 1)), k])


def fleiss_from_sums(s, raters=3):
    """Binary Fleiss kappa from summed [items, sum P_i, sum k]; NaN when undefined."""
    n, sum_p, sum_k = (float(x) for x in s)
    if n == 0:
        return float('nan')
    p1 = sum_k / (raters * n)
    pe = p1 ** 2 + (1 - p1) ** 2
    return float('nan') if np.isclose(pe, 1.0) else float((sum_p / n - pe) / (1 - pe))


def fleiss_kappa_binary(ratings, raters=3):
    """ratings: [(grade, grade, grade)] -> binary (>= 2) Fleiss kappa."""
    total = sum((fleiss_item(sum(is_rel(g) for g in r), raters) for r in ratings), np.zeros(3))
    return fleiss_from_sums(total, raters)


def raw_agreement(m):
    m = np.asarray(m)
    return float(np.trace(m) / m.sum()) if m.sum() else float('nan')


def bootstrap_ci(per_qid, stat, n=BOOTSTRAP, seed=SEED):
    """Percentile 95% CI, resampling QUERIES with replacement; per_qid = {qid: per-query sums}
    (a 4x4 or 2x2 confusion, or the Fleiss sums); stat maps the summed array to a number."""
    qids = sorted(per_qid)
    if not qids:
        return dict(lo=None, hi=None, draws=n, undefined=n, seed=seed)
    stack = np.stack([np.asarray(per_qid[q]) for q in qids])
    shape = stack.shape[1:]
    draws = np.random.default_rng(seed).integers(len(qids), size=(n, len(qids)))
    counts = np.zeros((n, len(qids)), dtype=np.int64)
    np.add.at(counts, (np.repeat(np.arange(n), len(qids)), draws.ravel()), 1)
    mats = (counts @ stack.reshape(len(qids), -1)).reshape(n, *shape)
    values = np.array([stat(mm) for mm in mats])
    ok = values[~np.isnan(values)]
    lo, hi = (np.percentile(ok, [2.5, 97.5]) if len(ok) else (float('nan'), float('nan')))
    return dict(lo=round(float(lo), 4), hi=round(float(hi), 4), draws=n, undefined=int(n - len(ok)), seed=seed)


def stats_block(m, rows='A', cols='B'):
    b = binary_confusion(m)
    return {'n': int(m.sum()), 'raw_exact': _r(raw_agreement(m)), 'raw_binary': _r(raw_agreement(b)),
            'kappa_binary': _r(kappa_from_confusion(b)), 'kappa_quadratic': _r(kappa_from_confusion(m, 'quadratic')),
            f'confusion_4x4_rows_{rows}_cols_{cols}': m.tolist(),
            f'confusion_2x2_rows_{rows}_cols_{cols}': b.tolist()}


def _r(x):
    return None if np.isnan(x) else round(x, 4)


def agreement(sheet_rows, a, b, *, bootstrap=BOOTSTRAP, seed=SEED, names=('A', 'B')):
    """Cohen kappa for one judge pair, on items where both have a valid row."""
    per_qid = defaultdict(lambda: np.zeros((4, 4), dtype=np.int64))
    for row in sheet_rows:
        if row['item_id'] in a and row['item_id'] in b:
            per_qid[row['qid']][int(a[row['item_id']]['grade']), int(b[row['item_id']]['grade'])] += 1
    total = sum(per_qid.values(), np.zeros((4, 4), dtype=np.int64))
    result = stats_block(total, *names)
    result['ci95_kappa_binary'] = bootstrap_ci(dict(per_qid), lambda m: kappa_from_confusion(binary_confusion(m)),
                                               bootstrap, seed)
    result['ci95_kappa_quadratic'] = bootstrap_ci(dict(per_qid), lambda m: kappa_from_confusion(m, 'quadratic'),
                                                  bootstrap, seed)
    result['queries'] = len(per_qid)
    return result


def agreement3(sheet_rows, judges, *, bootstrap=BOOTSTRAP, seed=SEED):
    """Note 3 section 5: pairwise Cohen kappa for every pair + binary Fleiss kappa across the three."""
    pairs = {f'{x}-{y}': agreement(sheet_rows, judges[x], judges[y], bootstrap=bootstrap, seed=seed, names=(x, y))
             for x, y in combinations(judges, 2)}
    per_qid = defaultdict(lambda: np.zeros(3))
    all_same = 0
    for row in sheet_rows:
        if all(row['item_id'] in labels for labels in judges.values()):
            sides = [is_rel(labels[row['item_id']]['grade']) for labels in judges.values()]
            per_qid[row['qid']] += fleiss_item(sum(sides), len(judges))
            all_same += len(set(sides)) == 1
    total = sum(per_qid.values(), np.zeros(3))
    fleiss = dict(n=int(total[0]), raters=len(judges), kappa_binary=_r(fleiss_from_sums(total, len(judges))),
                  raw_all_same_side=round(all_same / total[0], 4) if total[0] else None,
                  ci95_kappa_binary=bootstrap_ci(dict(per_qid), lambda s: fleiss_from_sums(s, len(judges)),
                                                 bootstrap, seed),
                  queries=len(per_qid))
    return dict(pairs=pairs, fleiss=fleiss)


# ---------------------------------------------------------------- merge + audit
def is_rel(grade):
    return int(grade) >= THRESHOLD


def decide(sheet_rows, judges):
    """judges = {'A': labels, 'B': labels[, 'C': labels]}; labels may lack items (missing/invalid rows).
    Three valid rows -> three-judge rule (median); two -> two-judge rule (lower); fewer -> refused.
    Returns {item_id: dict(rule, used, grades, tag, fallback_grade, consensus)}; consensus None = split."""
    letters = tuple(judges)
    decisions, short = {}, {}
    for row in sheet_rows:
        item_id = row['item_id']
        used = tuple(j for j in letters if item_id in judges[j])
        if len(used) < 2:
            short[item_id] = [j for j in letters if j not in used]
            continue
        grades = [int(judges[j][item_id]['grade']) for j in used]
        if len(used) == 3:
            rule, tag, fallback = 'three_judge', 'ABC_median', sorted(grades)[1]
        else:
            rule, tag, fallback = 'two_judge', ''.join(used) + '_min', min(grades)
        same = len({is_rel(g) for g in grades}) == 1
        decisions[item_id] = dict(rule=rule, used=used, grades=grades, tag=tag, fallback_grade=fallback,
                                  consensus=fallback if same else None)
    if short:  # no decision rule for these: refused before anything is written
        listed = '; '.join(f"{i} (no valid row from {', '.join(js)})" for i, js in short.items())
        raise ValueError(f'{len(short)} item(s) have fewer than two valid judge rows: {listed} '
                         '— re-run a judge on these items')
    return decisions


def merge(sheet_rows, a, b, c=None):
    """Same side -> accepted (two judges: the LOWER grade; three: the MEDIAN); a split -> dispute."""
    decisions = decide(sheet_rows, {'A': a, 'B': b} | ({'C': c} if c is not None else {}))
    accepted = {i: d['consensus'] for i, d in decisions.items() if d['consensus'] is not None}
    disputes = [r['item_id'] for r in sheet_rows if decisions[r['item_id']]['consensus'] is None]
    return accepted, disputes


def audit_check(audit, accepted, threshold_disputes, qid_of=None, two_rule=(), bootstrap=BOOTSTRAP, seed=SEED):
    """The author (blind) vs the consensus on audit items that have one (accepted).
    Primary kappa: those decided by all judges in force (note 3 section 4: all three on the same side); items
    decided by the two-judge rule are left out and counted. Secondary: the primary set without the author's
    unsure=yes items (note 2 section 4). PASS/STOP on the primary only. Audit disagreements (sent to the
    referee) cover every audit item with a consensus, two-judge-rule items included."""
    two = set(two_rule)
    with_consensus = [i for i in sorted(audit) if i in accepted]
    on_accepted = [i for i in with_consensus if i not in two]
    sure = [i for i in on_accepted if audit[i]['unsure'] != 'yes']
    disagree = [i for i in with_consensus if is_rel(audit[i]['grade']) != is_rel(accepted[i])]

    def block(ids):
        per_qid = defaultdict(lambda: np.zeros((2, 2), dtype=np.int64))
        for item_id in ids:
            per_qid[qid_of[item_id] if qid_of else ''][int(is_rel(audit[item_id]['grade'])),
                                                       int(is_rel(accepted[item_id]))] += 1
        b = sum(per_qid.values(), np.zeros((2, 2), dtype=np.int64))
        ci = bootstrap_ci(dict(per_qid), kappa_from_confusion, bootstrap, seed) if qid_of else None
        return b, kappa_from_confusion(b), ci

    b, kappa, ci = block(on_accepted)
    b2, kappa2, ci2 = block(sure)
    flag = 'PASS' if not np.isnan(kappa) and kappa >= AUDIT_PASS else 'STOP'
    return dict(audit_items=len(audit), on_accepted=len(on_accepted),
                on_two_judge_rule=len(set(with_consensus) & two),
                on_disputes=len(set(audit) & set(threshold_disputes)),
                kappa_binary=_r(kappa), raw_binary=round(raw_agreement(b), 4) if b.sum() else None,
                ci95_kappa_binary=ci, confusion_2x2_rows_shyam_cols_consensus=b.tolist(),
                secondary=dict(excludes='audit items the author marked unsure=yes', n=len(sure),
                               kappa_binary=_r(kappa2), ci95_kappa_binary=ci2,
                               confusion_2x2_rows_shyam_cols_consensus=b2.tolist()),
                disagreements=len(disagree), disagreements_on_two_judge_rule=len(set(disagree) & two),
                primary_excludes='audit items decided by the two-judge rule (note 3 section 4)',
                audit_mode='check_only (note 2)',
                rule=f'primary kappa_binary >= {AUDIT_PASS} -> PASS else STOP', flag=flag,
                report_sentence=REPORT_SENTENCE.format(n=len(audit))), disagree


def dispute_rows(sheet_by_id, judges, reasons):
    """judges = {'A': labels, ...} in force; a judge without a valid row on an item gets blank fields."""
    rows = []
    for item_id, reason in reasons:
        s = sheet_by_id[item_id]
        row = dict(item_id=item_id, reason=reason, qid=s['qid'], query=s['query'], source_doc=s['source_doc'],
                   clause_id=s['clause_id'], title=s['title'])
        for j in JUDGES:
            label = judges.get(j, {}).get(item_id)
            row.update({f'{j.lower()}_{f}': label[f] if label else '' for f in JUDGE_FIELDS})
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


# ---------------------------------------------------------------- stage 3
def final_grade(decision, reason, referee):
    """(grade, qrels judge, category) for one item (note 2 sections 2-3, note 3 section 3)."""
    three = decision['rule'] == 'three_judge'
    if reason == 'threshold_split':
        if referee['unsure'] == 'yes':
            return (decision['fallback_grade'], decision['tag'],
                    'dispute_unsure_median' if three else 'dispute_unsure_lower')
        return int(referee['grade']), 'referee', 'dispute_referee'
    if reason == 'audit_disagrees':  # unsure -> default: the consensus stands, as unsure -> median on splits (C4)
        if referee['unsure'] == 'no' and referee['override_quote'].strip() and referee['override_rule']:
            return int(referee['grade']), 'referee', 'auditor_override_evidence'
        return decision['consensus'], decision['tag'], 'auditor_disagrees_kept'
    return decision['consensus'], decision['tag'], 'accepted_median' if three else 'accepted_lower'


def _right_counts(counter):
    return {'right': counter['right'], 'judged': counter['judged']}


# ---------------------------------------------------------------- pipeline
def write_json(path, value):
    return write_once(path, json.dumps(value, indent=2, sort_keys=True) + '\n')


def run_merge(*, sheet, a_files, b_files, out, c_files=None, fallback_reason=None, audit_ids=None, audit_file=None,
              freeze_path=None, referee_file=None, bootstrap=BOOTSTRAP, freeze_dir=None, _test_skip_freeze=False,
              _test_skip_judge_freeze=False):
    """Returns a counts-only summary; writes what the available inputs allow (write-once).
    With a freeze, the audit set is the frozen prefix (audit_ids is used only by the test-only path).
    Before stage 1, every judge given needs judge_<X>_freeze.json in freeze_dir (default: out) with the
    same files and sha256s (s8-freeze-judge)."""
    out = Path(out)
    if fallback_reason is not None and not str(fallback_reason).strip():
        raise ValueError('--two-judge-fallback needs a reason (note 3 section 6: the fallback is logged with it)')
    mode = 'two_judge_fallback' if fallback_reason else ('three_judge' if c_files else 'two_judge')
    if audit_file is not None and mode == 'two_judge':
        raise PermissionError('stage 2 needs judge C (--c) or --two-judge-fallback REASON '
                              '(Addendum 2 note 3 sections 3 and 6)')
    files = {'A': a_files, 'B': b_files} | ({'C': c_files} if c_files else {})
    excluded = {}
    if not _test_skip_judge_freeze:  # every judge given must match its judge_<X>_freeze.json (S8 D4v)
        from retrieval_eval.s8_judge_check import check_judge_freezes
        excluded = check_judge_freezes(files, out if freeze_dir is None else freeze_dir)
    sheet_rows = read_sheet(sheet)
    sheet_by_id = {r['item_id']: r for r in sheet_rows}
    for j, ids in excluded.items():
        if set(ids) - set(sheet_by_id):
            raise ValueError(f'judge {j}: the freeze excludes {len(set(ids) - set(sheet_by_id))} item(s) not in the sheet')
    read = {j: read_judge(f, f'judge {j}', sheet_by_id, excluded.get(j, ())) for j, f in files.items()}
    labels = {j: valid for j, (valid, _) in read.items()}
    c_rate = ((len(read['C'][1]) + len(excluded.get('C', ()))) / len(sheet_rows)
              if 'C' in read and sheet_rows else None)
    if mode == 'three_judge' and c_rate > C_MISSING_MAX:
        raise ValueError(f"judge C: {len(read['C'][1])} of {len(sheet_rows)} rows missing or invalid "
                         f'({c_rate:.1%} > 2%): re-run once; if still above 2%, use --two-judge-fallback REASON '
                         '(note 3 section 6)')
    in_force = JUDGES if mode == 'three_judge' else ('A', 'B')
    force = {j: labels[j] for j in in_force}
    decisions = decide(sheet_rows, force)
    accepted = {i: d['consensus'] for i, d in decisions.items() if d['consensus'] is not None}
    threshold_disputes = [r['item_id'] for r in sheet_rows if decisions[r['item_id']]['consensus'] is None]
    two_rule = [i for i, d in decisions.items() if mode == 'three_judge' and d['rule'] == 'two_judge']
    inputs = dict(sheet_sha256=bytes_sha(sheet), **{f'{j.lower()}_files': {Path(p).name: bytes_sha(p) for p in f}
                                                     for j, f in files.items()})
    common = dict(mode=mode, judges_in_force=list(in_force), judges_read=list(files), fallback_reason=fallback_reason,
                  accepted=len(accepted), threshold_disputes=len(threshold_disputes),
                  two_judge_rule_items_missing_row=len(two_rule),
                  excluded_by_judge={j: len(excluded.get(j, ())) for j in files},
                  row_problems={j: problem_counts(p) for j, (_, p) in read.items()},
                  c_missing_or_invalid_rate=None if c_rate is None else round(c_rate, 4),
                  grade_counts={j: _grade_counts(l) for j, l in labels.items()},
                  unsure={j: sum(x['unsure'] == 'yes' for x in l.values()) for j, l in labels.items()}, inputs=inputs)
    if mode == 'three_judge':
        name, agr = 'agreement_abc.json', agreement3(sheet_rows, force, bootstrap=bootstrap)
    else:
        name, agr = 'agreement_ab.json', agreement(sheet_rows, labels['A'], labels['B'], bootstrap=bootstrap)
        if 'C' in labels:  # note 3 section 6: C only as a secondary comparison
            agr['secondary_c'] = {f'{x}-C': agreement(sheet_rows, labels[x], labels['C'], bootstrap=bootstrap,
                                                      names=(x, 'C')) for x in ('A', 'B')}
    agr.update(common)
    states = {name: write_json(out / name, agr)}
    summary = dict(stage=1, items=len(sheet_rows), mode=mode, agreement=_brief(agr), states=states)
    if audit_file is None:
        return summary

    if not _test_skip_freeze:
        from retrieval_eval.s8_judging import frozen_prefix
        freeze = load_audit_freeze(freeze_path, audit_file)
        audit_ids = frozen_prefix(freeze_path)  # the audit set = the N frozen items (gap-free prefix)
    else:
        freeze = dict(labels_sha256=bytes_sha(audit_file), frozen_at='TEST-ONLY 1970-01-01T00:00:00')
    audit = read_labels([audit_file], 'audit')
    check_coverage(audit, audit_ids, 'audit')
    check, disagree = audit_check(audit, accepted, threshold_disputes, {i: r['qid'] for i, r in sheet_by_id.items()},
                                  two_rule, bootstrap)
    check.update(audit_labels_sha256=freeze['labels_sha256'], audit_frozen_at=freeze['frozen_at'],
                 audit_n=freeze.get('n'), audit_order_sha256=freeze.get('order_sha256'), mode=mode,
                 judges_in_force=list(in_force), fallback_reason=fallback_reason)
    states['audit_check.json'] = write_json(out / 'audit_check.json', check)
    summary.update(stage=2, audit={k: check[k] for k in ('audit_items', 'on_accepted', 'on_disputes',
                                                          'kappa_binary', 'disagreements', 'flag')}
                   | {'kappa_binary_secondary': check['secondary']['kappa_binary']})
    if check['flag'] != 'PASS':
        summary['stopped'] = 'audit STOP: no referee list, no qrels'
        return summary

    reasons = [(i, 'threshold_split') for i in threshold_disputes]
    reasons += [(i, 'audit_disagrees') for i in disagree]
    order = {r['item_id']: n for n, r in enumerate(sheet_rows)}
    reasons.sort(key=lambda r: order[r[0]])
    referee_ids = [i for i, _ in reasons]
    states['disputes.csv'] = write_once(out / 'disputes.csv',
                                        csv_text(dispute_rows(sheet_by_id, force, reasons), DISPUTE_COLUMNS))
    states['referee_sheet.csv'] = write_once(out / 'referee_sheet.csv',
                                             sheet_csv([sheet_by_id[i] for i in referee_ids]))
    states['referee_ids.txt'] = write_once(out / 'referee_ids.txt', ''.join(i + '\n' for i in referee_ids))
    summary.update(stage=2, referee=dict(Counter(r for _, r in reasons)) | {'total': len(referee_ids)})
    if referee_file is None:
        return summary

    referee = read_labels([referee_file], 'referee', REFEREE_FIELDS)
    check_coverage(referee, referee_ids, 'referee')
    for item_id in referee_ids:  # any bad reason_code / rule / quote refuses the whole file
        validate_referee(referee[item_id], f'referee item {item_id}', sheet_by_id[item_id]['text'])
    reason_of = dict(reasons)
    day = freeze['frozen_at'][:10] if not _test_skip_freeze else '1970-01-01'
    rows, source, categories = [], Counter(), Counter({c: 0 for c in CATEGORIES})
    right = {j: Counter() for j in in_force}
    by_reason = {r: {j: Counter() for j in in_force} for r in ('threshold_split', 'audit_disagrees')}
    flipped = 0
    for row in sheet_rows:
        item_id = row['item_id']
        reason = reason_of.get(item_id)
        grade, judge, category = final_grade(decisions[item_id], reason, referee.get(item_id))
        source[judge] += 1
        categories[category] += 1
        if category == 'auditor_override_evidence' and is_rel(grade) != is_rel(decisions[item_id]['consensus']):
            flipped += 1
        if reason:  # per-judge correctness: the judge's binary side equals the final grade's side
            for j in in_force:
                if item_id in force[j]:
                    ok = is_rel(force[j][item_id]['grade']) == is_rel(grade)
                    for counter in (right[j], by_reason[reason][j]):
                        counter['judged'] += 1
                        counter['right'] += ok
        rows.append(dict(qid=row['qid'], clause_id=row['clause_id'], grade=str(grade), judge=judge, date=day))
    qrels_io.validate(rows)
    states['qrels.tsv'] = write_once(out / 'qrels.tsv', qrels_text(rows))
    meta = dict(pairs=len(rows), source=dict(source), categories=dict(categories),
                grades=dict(Counter(r['grade'] for r in rows)),
                relevant=sum(int(r['grade']) >= THRESHOLD for r in rows),
                mode=mode, judges_in_force=list(in_force), fallback_reason=fallback_reason,
                two_judge_rule_items_missing_row=len(two_rule), excluded_by_judge=common['excluded_by_judge'],
                referee_items=len(referee_ids),
                reason_codes={c: sum(referee[i]['reason_code'] == c for i in referee_ids) for c in REASON_CODES},
                judge_right={j: _right_counts(c) for j, c in right.items()},
                judge_right_by_reason={r: {j: _right_counts(c) for j, c in d.items()} for r, d in by_reason.items()},
                judge_right_rule="the judge's binary side (grade >= 2) equals the final grade's side, on referee items",
                override_flipped_side=flipped,
                referee_sha256=bytes_sha(referee_file), audit_labels_sha256=freeze['labels_sha256'], **inputs)
    states['qrels_meta.json'] = write_json(out / 'qrels_meta.json', meta)
    summary.update(stage=3, qrels={k: meta[k] for k in ('pairs', 'source', 'categories', 'grades', 'relevant',
                                                         'reason_codes', 'judge_right')})
    return summary


def _grade_counts(labels):
    return {g: sum(l['grade'] == g for l in labels.values()) for g in GRADES}


def _brief(agr):
    keys = ('accepted', 'threshold_disputes', 'two_judge_rule_items_missing_row', 'excluded_by_judge')
    if 'pairs' in agr:
        return {k: agr[k] for k in keys} | {
            'fleiss_kappa_binary': agr['fleiss']['kappa_binary'], 'fleiss_ci95': agr['fleiss']['ci95_kappa_binary'],
            'pairs': {p: {k: v[k] for k in ('n', 'kappa_binary', 'kappa_quadratic')} for p, v in agr['pairs'].items()}}
    return {k: agr[k] for k in ('n', 'raw_exact', 'raw_binary', 'kappa_binary', 'kappa_quadratic',
                                'ci95_kappa_binary', 'ci95_kappa_quadratic', *keys)}


def main(argv=None):
    out = output_dir()
    parser = argparse.ArgumentParser(prog='tasks.py s8-merge-labels', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['s8-merge-labels'])
    parser.add_argument('--a', required=True, help="judge A output/ folder (labels_s8_sheet_<qid>.csv)")
    parser.add_argument('--b', required=True, help="judge B output/ folder")
    parser.add_argument('--c', help="judge C output/ folder (three-judge protocol, note 3)")
    parser.add_argument('--two-judge-fallback', metavar='REASON',
                        help='note 3 section 6 fallback: two-judge protocol for all items, logged with REASON')
    parser.add_argument('--audit', help='Shyam audit labels CSV (needs audit_freeze.json)')
    parser.add_argument('--referee', help='referee labels CSV (needs audit PASS)')
    parser.add_argument('--sheet', default=str(out / 'sheet' / 's8_sheet.csv'))
    parser.add_argument('--audit-dir', default=str(out / 'audit'))
    parser.add_argument('--out', default=str(out / 'labels'))
    args = parser.parse_args(argv)
    if args.referee and not args.audit:
        parser.error('--referee needs --audit')
    audit_dir = Path(args.audit_dir)
    summary = run_merge(sheet=args.sheet, a_files=judge_files(args.a), b_files=judge_files(args.b), out=args.out,
                        c_files=judge_files(args.c) if args.c else None, fallback_reason=args.two_judge_fallback,
                        audit_file=args.audit, freeze_path=audit_dir / 'audit_freeze.json',
                        referee_file=args.referee)
    print('merge: ' + json.dumps(summary, indent=1, sort_keys=True))
    for name in summary['states']:
        print(f'{Path(args.out) / name}  sha256={bytes_sha(Path(args.out) / name)}')
    return 0 if 'stopped' not in summary else 2


if __name__ == '__main__':
    raise SystemExit(main())
