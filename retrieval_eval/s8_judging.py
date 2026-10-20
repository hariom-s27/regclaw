"""S8 Part D: blind audit sample, interleaved audit order, audit freeze and the two isolated judge kits
(offline, 0 API calls).

Commands (via tasks.py): s8-audit-sample, s8-audit-order, s8-freeze-audit, s8-make-kits, s8-check-kits.
Reads only the blind sheet / practice sheet (never pool_key, runs or item maps). Write-once outputs.
The audit order (Addendum 2 section 5) uses item_id + qid only; the freeze accepts only a gap-free
prefix of that order with N >= 100.
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
import random
from pathlib import Path
import statistics

from retrieval_eval.pool import read_queries
from retrieval_eval.s8_labels import read_labels, read_sheet
from retrieval_eval.s8_runs import QUERIES, ROOT, bytes_sha, output_dir, sheet_csv, write_once

AUDIT_SEED = 20261020
AUDIT_TARGET = 200
AUDIT_MIN = 8
AUDIT_LABELS = 'labels_shyam_audit.csv'
ORDER_SEED = 20261021
AUDIT_ORDER = 'audit_order.txt'
AUDIT_N_MIN = 100
KIT_ROOTS = (Path('D:/s8_judgeA'), Path('D:/s8_judgeB'))
INSTRUCTIONS = Path(__file__).with_name('s8_judge_instructions.md')
GUIDELINE = Path(__file__).with_name('S8_RELEVANCE_GUIDELINE_v1.0.1.md')  # in force (Addendum 2, note 1)
KIT_FILES = ('GUIDELINE.md', 'JUDGE_INSTRUCTIONS.md', 'practice/practice_sheet.csv')
KIT_DIRS = ('practice', 'eval', 'output')


def instructions_bytes(path=INSTRUCTIONS):
    """LF-normalised, so a CRLF checkout (core.autocrlf) cannot change the kit's bytes."""
    return Path(path).read_bytes().replace(b'\r\n', b'\n')


def guideline_bytes(path=GUIDELINE):
    """The guideline in force (v1.0.1), LF-normalised like the instructions."""
    return instructions_bytes(path)


# ---------------------------------------------------------------- audit sample
def audit_allocation(sizes, target=AUDIT_TARGET, minimum=AUDIT_MIN):
    """Proportional per-query sample size, at least `minimum` (capped by the query's size)."""
    total = sum(sizes.values())
    return {q: min(n, max(minimum, round(target * n / total))) for q, n in sorted(sizes.items())}


def draw_audit(id_qid, seed=AUDIT_SEED, target=AUDIT_TARGET, minimum=AUDIT_MIN):
    """id_qid: [(item_id, qid)] -- the ONLY information the draw sees."""
    by_qid = defaultdict(list)
    for item_id, qid in id_qid:
        by_qid[qid].append(item_id)
    alloc = audit_allocation({q: len(ids) for q, ids in by_qid.items()}, target, minimum)
    rng = random.Random(seed)
    chosen = set()
    for qid in sorted(by_qid):
        chosen.update(rng.sample(sorted(by_qid[qid]), alloc[qid]))
    return chosen, alloc


def build_audit(sheet_rows, seed=AUDIT_SEED):
    chosen, alloc = draw_audit([(r['item_id'], r['qid']) for r in sheet_rows], seed)
    rules = {r['common_rules'] for r in sheet_rows if r['common_rules']}
    if len(rules) != 1:
        raise ValueError('expected exactly one common_rules text in the sheet')
    rules = rules.pop()
    rows, seen = [], set()
    for r in sheet_rows:  # sheet order; common_rules on the first audit row per query (sheet convention)
        if r['item_id'] in chosen:
            rows.append({**r, 'common_rules': rules if r['qid'] not in seen else ''})
            seen.add(r['qid'])
    return rows, alloc


def run_audit_sample(sheet_path, folder, seed=AUDIT_SEED):
    rows, alloc = build_audit(read_sheet(sheet_path), seed)
    folder = Path(folder)
    states = {'audit_sheet.csv': write_once(folder / 'audit_sheet.csv', sheet_csv(rows)),
              'audit_ids.txt': write_once(folder / 'audit_ids.txt', ''.join(r['item_id'] + '\n' for r in rows))}
    return dict(items=len(rows), per_query=alloc, seed=seed, sheet_sha256=bytes_sha(sheet_path), states=states)


# ---------------------------------------------------------------- audit order (Addendum 2 section 5)
def read_id_qid(path):
    """[(item_id, qid)] from a sheet CSV; every other column is dropped."""
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return [(row['item_id'], row['qid']) for row in csv.DictReader(f)]


def queries_order(path=QUERIES):
    """Query ids in queries.yaml order (R1, R2, R3, Q1 ... Q13)."""
    return [q['qid'] for q in read_queries(path)]


def audit_order(id_qid, qid_order, seed=ORDER_SEED):
    """Within each query: sorted ids shuffled with one Random(seed), queries taken in qid_order.
    Then round-robin over qid_order, one item per query per round; a query that runs out is skipped."""
    by_qid = defaultdict(list)
    for item_id, qid in id_qid:
        by_qid[qid].append(item_id)
    unknown = set(by_qid) - set(qid_order)
    if unknown:
        raise ValueError(f'qid(s) not in the query order: {sorted(unknown)}')
    rng = random.Random(seed)
    queues = []
    for qid in qid_order:
        ids = sorted(by_qid.get(qid, []))
        rng.shuffle(ids)
        queues.append(ids)
    order = []
    for n in range(max(map(len, queues), default=0)):
        order.extend(q[n] for q in queues if n < len(q))
    return order


def prefix_counts(order, qid_of, n=AUDIT_N_MIN):
    """Items per query among the first n positions, in first-appearance order."""
    counts = Counter(qid_of[i] for i in order[:n])
    return {q: counts[q] for q in dict.fromkeys(qid_of[i] for i in order)}


def run_audit_order(folder, qid_order=None, seed=ORDER_SEED):
    folder = Path(folder)
    ids = (folder / 'audit_ids.txt').read_text(encoding='utf-8').split()
    id_qid = read_id_qid(folder / 'audit_sheet.csv')
    if [i for i, _ in id_qid] != ids:
        raise ValueError('audit_sheet.csv item ids differ from audit_ids.txt')
    qid_order = queries_order() if qid_order is None else list(qid_order)
    order = audit_order(id_qid, qid_order, seed)
    state = write_once(folder / AUDIT_ORDER, ''.join(i + '\n' for i in order))
    return dict(items=len(order), seed=seed, query_order=qid_order, state=state,
                sha256=bytes_sha(folder / AUDIT_ORDER),
                first_100_per_query=prefix_counts(order, dict(id_qid), AUDIT_N_MIN))


def read_order(path):
    order = Path(path).read_text(encoding='utf-8').split()
    if len(set(order)) != len(order):
        raise ValueError(f'{Path(path).name}: duplicate item ids')
    return order


def median_save_gap(timestamps):
    """Median seconds between consecutive saves, all save timestamps sorted by time (Addendum 2 section 5)."""
    times = sorted(datetime.fromisoformat(s) for s in timestamps if s)
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    return statistics.median(gaps) if gaps else None


def freeze_audit(labels_path, folder, order, now=None, minimum=AUDIT_N_MIN):
    """Freeze the audit labels (write-once). They must be exactly the first N items of the audit order,
    N >= minimum, every field valid. Records N, the shas, a timestamp and the median save gap."""
    folder = Path(folder)
    ids = (folder / 'audit_ids.txt').read_text(encoding='utf-8').split()
    order_ids = read_order(order)
    if sorted(order_ids) != sorted(ids):
        raise ValueError('the audit order is not a permutation of audit_ids.txt')
    labels = read_labels([labels_path], 'audit')
    unknown = set(labels) - set(order_ids)
    if unknown:
        raise ValueError(f'{len(unknown)} labelled item(s) not in the audit order')
    n = len(labels)
    gap = next((k for k, i in enumerate(order_ids[:n]) if i not in labels), None)
    if gap is not None:
        raise ValueError(f'labels are not a gap-free prefix of the order: position {gap + 1} is unlabelled '
                         f'while {n} item(s) are labelled')
    if n < minimum:
        raise ValueError(f'only {n} item(s) labelled; the minimum is {minimum}')
    if (folder / 'audit_freeze.json').exists():
        raise FileExistsError('audit_freeze.json exists; the audit is already frozen')
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec='seconds')
    record = dict(labels_file=Path(labels_path).name, labels_sha256=bytes_sha(labels_path), n=n, minimum=minimum,
                  order_file=Path(order).name, order_sha256=bytes_sha(order), order_items=len(order_ids),
                  frozen_at=stamp, audit_sheet_sha256=bytes_sha(folder / 'audit_sheet.csv'),
                  audit_ids_sha256=bytes_sha(folder / 'audit_ids.txt'),
                  median_seconds_per_item=median_save_gap(l['timestamp'] for l in labels.values()),
                  median_rule='median gap between consecutive saves, all save timestamps sorted by time',
                  timestamps=sum(bool(l['timestamp']) for l in labels.values()))
    write_once(folder / 'audit_freeze.json', json.dumps(record, indent=2, sort_keys=True) + '\n')
    return record


def frozen_prefix(freeze_path):
    """The N frozen audit item ids: the first N of the order file, whose sha must match the freeze."""
    freeze_path = Path(freeze_path)
    freeze = json.loads(freeze_path.read_text(encoding='utf-8'))
    order = freeze_path.parent / freeze['order_file']
    if bytes_sha(order) != freeze['order_sha256']:
        raise PermissionError('audit order file differs from the frozen one (sha mismatch)')
    return read_order(order)[:freeze['n']]


def frozen_labels_path(freeze_path):
    """The labels path an existing freeze protects (resolved next to the freeze file), else None."""
    freeze_path = Path(freeze_path)
    if not freeze_path.exists():
        return None
    return (freeze_path.parent / json.loads(freeze_path.read_text(encoding='utf-8'))['labels_file']).resolve()


# ---------------------------------------------------------------- judge kits
def _inside(path, other):
    try:
        Path(path).resolve().relative_to(Path(other).resolve())
        return True
    except ValueError:
        return False


def check_roots(roots, repo=ROOT):
    roots = [Path(r) for r in roots]
    for r in roots:
        if _inside(r, repo) or _inside(repo, r):
            raise ValueError(f'kit root {r} overlaps the repo')
    for r in roots:
        for o in roots:
            if r is not o and _inside(r, o):
                raise ValueError(f'kit roots {r} and {o} overlap')


def make_kits(roots, practice_sheet, instructions=INSTRUCTIONS, repo=ROOT, guideline=GUIDELINE):
    check_roots(roots, repo)
    for root in map(Path, roots):
        if root.exists() and any(root.iterdir()) and check_kit(root, bytes_sha(practice_sheet), None,
                                                               guideline=guideline):
            raise FileExistsError(f'{root} exists with content that is not a valid kit')
        for d in KIT_DIRS:
            (root / d).mkdir(parents=True, exist_ok=True)
        if not (root / 'GUIDELINE.md').exists():  # an existing GUIDELINE.md is never overwritten
            write_once(root / 'GUIDELINE.md', guideline_bytes(guideline))
        write_once(root / 'JUDGE_INSTRUCTIONS.md', instructions_bytes(instructions))
        write_once(root / 'practice' / 'practice_sheet.csv', Path(practice_sheet).read_bytes())
    return {str(r): kit_tree(r) for r in roots}


def kit_tree(root):
    """Paths + shas; files under output/ (judge labels) are listed by NAME only, never read."""
    root = Path(root)
    entries = []
    for p in root.rglob('*'):
        rel = p.relative_to(root).as_posix()
        if p.is_dir():
            entries.append(dict(path=rel + '/', sha256=None))
        else:
            entries.append(dict(path=rel, sha256=None if rel.startswith('output/') else bytes_sha(p)))
    return sorted(entries, key=lambda e: e['path'])


def check_kit(root, practice_sha, per_query_dir, instructions=INSTRUCTIONS, guideline=GUIDELINE):
    """Problems (empty list = clean). Only the allowed files may exist."""
    root, problems = Path(root), []
    if not root.is_dir():
        return [f'{root} missing']
    per_query = {p.name: bytes_sha(p) for p in Path(per_query_dir).glob('s8_sheet_*.csv')} if per_query_dir else {}
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root).as_posix()
        parts = rel.split('/')
        if p.is_dir():
            if rel not in KIT_DIRS:
                problems.append(f'unexpected folder {rel}')
        elif rel == 'GUIDELINE.md':
            if p.read_bytes() != guideline_bytes(guideline):
                problems.append('GUIDELINE.md differs from the guideline in force v1.0.1')
        elif rel == 'JUDGE_INSTRUCTIONS.md':
            if p.read_bytes() != instructions_bytes(instructions):
                problems.append('JUDGE_INSTRUCTIONS.md differs from the repo text')
        elif rel == 'practice/practice_sheet.csv':
            if bytes_sha(p) != practice_sha:
                problems.append('practice_sheet.csv sha differs from the repo practice sheet')
        elif len(parts) == 2 and parts[0] == 'eval':
            if per_query.get(parts[1]) != bytes_sha(p):
                problems.append(f'eval/{parts[1]} is not a per-query sheet copy')
        elif len(parts) == 2 and parts[0] == 'output' and parts[1].startswith('labels_') and parts[1].endswith('.csv'):
            pass
        else:
            problems.append(f'unexpected file {rel}')
    for name in (*KIT_FILES, *KIT_DIRS):
        if not (root / name).exists():
            problems.append(f'missing {name}')
    return problems


def check_kits(roots, practice_sheet, per_query_dir, repo=ROOT, guideline=GUIDELINE):
    check_roots(roots, repo)
    result = {str(r): check_kit(r, bytes_sha(practice_sheet), per_query_dir, guideline=guideline) for r in roots}
    guides = {bytes_sha(Path(r) / 'GUIDELINE.md') for r in roots if (Path(r) / 'GUIDELINE.md').exists()}
    if len(guides) > 1:
        for r in roots:
            result[str(r)].append('GUIDELINE.md differs between kits')
    return result


# ---------------------------------------------------------------- CLI
def main(argv=None):
    out = output_dir()
    parser = argparse.ArgumentParser(prog='tasks.py', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    audit = sub.add_parser('s8-audit-sample', help='~200 blind audit items, stratified by query (write-once)')
    audit.add_argument('--sheet', default=str(out / 'sheet' / 's8_sheet.csv'))
    audit.add_argument('--folder', default=str(out / 'audit'))
    order = sub.add_parser('s8-audit-order', help='Fixed interleaved audit order (item_id + qid only; write-once)')
    order.add_argument('--folder', default=str(out / 'audit'))
    freeze = sub.add_parser('s8-freeze-audit', help='Freeze a gap-free prefix (N >= 100) of the audit labels')
    freeze.add_argument('--labels', default=str(out / 'audit' / AUDIT_LABELS))
    freeze.add_argument('--folder', default=str(out / 'audit'))
    freeze.add_argument('--order', default=str(out / 'audit' / AUDIT_ORDER))
    for name, text in (('s8-make-kits', 'Create the two isolated judge kits'),
                       ('s8-check-kits', 'Check the judge kits hold only the allowed files')):
        kit = sub.add_parser(name, help=text)
        kit.add_argument('--roots', nargs=2, default=[str(r) for r in KIT_ROOTS])
        kit.add_argument('--practice', default=str(out / 'practice' / 'practice_sheet.csv'))
        kit.add_argument('--per-query', default=str(out / 'sheet' / 'per_query'))
    args = parser.parse_args(argv)

    if args.command == 's8-audit-sample':
        result = run_audit_sample(args.sheet, args.folder)
        print('audit: ' + json.dumps(result, indent=1, sort_keys=True))
        for name in ('audit_sheet.csv', 'audit_ids.txt'):
            print(f'{Path(args.folder) / name}  sha256={bytes_sha(Path(args.folder) / name)}')
        return 0
    if args.command == 's8-audit-order':
        result = run_audit_order(args.folder)
        print('audit_order: ' + json.dumps(result, indent=1))
        print(f"{Path(args.folder) / AUDIT_ORDER}  sha256={result['sha256']}")
        return 0
    if args.command == 's8-freeze-audit':
        record = freeze_audit(args.labels, args.folder, args.order)
        print('audit_freeze: ' + json.dumps(record, indent=1, sort_keys=True))
        return 0
    if args.command == 's8-make-kits':
        print(f'practice_sheet.csv  sha256={bytes_sha(args.practice)}')
        trees = make_kits(args.roots, args.practice)
    else:
        trees = {r: kit_tree(r) for r in args.roots if Path(r).is_dir()}
    for root, entries in trees.items():
        print(root)
        for e in entries:
            print(f"  {e['path']}" + (f"  sha256={e['sha256']}" if e['sha256'] else ''))
    problems = check_kits(args.roots, args.practice, args.per_query)
    print('check: ' + json.dumps(problems, indent=1))
    return 0 if not any(problems.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
