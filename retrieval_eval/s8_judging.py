"""S8 Part D1: blind audit sample, audit freeze and the two isolated judge kits (offline, 0 API calls).

Commands (via tasks.py): s8-audit-sample, s8-freeze-audit, s8-make-kits, s8-check-kits.
Reads only the blind sheet / practice sheet (never pool_key, runs or item maps). Write-once outputs.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import random
from pathlib import Path

from retrieval_eval.s8_labels import check_coverage, read_labels, read_sheet
from retrieval_eval.s8_runs import ROOT, bytes_sha, output_dir, sheet_csv, write_once

AUDIT_SEED = 20261020
AUDIT_TARGET = 200
AUDIT_MIN = 8
AUDIT_LABELS = 'labels_shyam_audit.csv'
KIT_ROOTS = (Path('D:/s8_judgeA'), Path('D:/s8_judgeB'))
INSTRUCTIONS = Path(__file__).with_name('s8_judge_instructions.md')
GUIDELINE_PLACEHOLDER = 'guideline v0.9 for calibration; to be replaced by v1.0\n'
KIT_FILES = ('GUIDELINE.md', 'JUDGE_INSTRUCTIONS.md', 'practice/practice_sheet.csv')
KIT_DIRS = ('practice', 'eval', 'output')


def instructions_bytes(path=INSTRUCTIONS):
    """LF-normalised, so a CRLF checkout (core.autocrlf) cannot change the kit's bytes."""
    return Path(path).read_bytes().replace(b'\r\n', b'\n')


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


def freeze_audit(labels_path, folder, now=None):
    """Record the completed audit labels' sha + timestamp (write-once). Validates full coverage first."""
    folder = Path(folder)
    ids = (folder / 'audit_ids.txt').read_text(encoding='utf-8').split()
    labels = read_labels([labels_path], 'audit')
    check_coverage(labels, ids, 'audit')
    if (folder / 'audit_freeze.json').exists():
        raise FileExistsError('audit_freeze.json exists; the audit is already frozen')
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec='seconds')
    record = dict(labels_file=Path(labels_path).name, labels_sha256=bytes_sha(labels_path), items=len(labels),
                  frozen_at=stamp, audit_sheet_sha256=bytes_sha(folder / 'audit_sheet.csv'))
    write_once(folder / 'audit_freeze.json', json.dumps(record, indent=2, sort_keys=True) + '\n')
    return record


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


def make_kits(roots, practice_sheet, instructions=INSTRUCTIONS, repo=ROOT):
    check_roots(roots, repo)
    for root in map(Path, roots):
        if root.exists() and any(root.iterdir()) and check_kit(root, bytes_sha(practice_sheet), None):
            raise FileExistsError(f'{root} exists with content that is not a valid kit')
        for d in KIT_DIRS:
            (root / d).mkdir(parents=True, exist_ok=True)
        if not (root / 'GUIDELINE.md').exists():  # the user later replaces the placeholder with v1.0
            write_once(root / 'GUIDELINE.md', GUIDELINE_PLACEHOLDER)
        write_once(root / 'JUDGE_INSTRUCTIONS.md', instructions_bytes(instructions))
        write_once(root / 'practice' / 'practice_sheet.csv', Path(practice_sheet).read_bytes())
    return {str(r): kit_tree(r) for r in roots}


def kit_tree(root):
    root = Path(root)
    entries = []
    for p in root.rglob('*'):
        rel = p.relative_to(root).as_posix()
        entries.append(dict(path=rel + '/', sha256=None) if p.is_dir() else dict(path=rel, sha256=bytes_sha(p)))
    return sorted(entries, key=lambda e: e['path'])


def check_kit(root, practice_sha, per_query_dir, instructions=INSTRUCTIONS):
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
            pass
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


def check_kits(roots, practice_sheet, per_query_dir, repo=ROOT):
    check_roots(roots, repo)
    result = {str(r): check_kit(r, bytes_sha(practice_sheet), per_query_dir) for r in roots}
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
    freeze = sub.add_parser('s8-freeze-audit', help='Freeze the completed audit labels (sha + timestamp)')
    freeze.add_argument('--labels', default=str(out / 'audit' / AUDIT_LABELS))
    freeze.add_argument('--folder', default=str(out / 'audit'))
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
    if args.command == 's8-freeze-audit':
        print('audit_freeze: ' + json.dumps(freeze_audit(args.labels, args.folder), indent=1, sort_keys=True))
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
