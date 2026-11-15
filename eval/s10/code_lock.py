"""S10b2b (S10b2b-14): lock the S10b analysis code with a write-once sha manifest (stdlib + repo helpers).

Writes eval/s10/manifests/s10b2b_code_lock.json:
  {format, created_at, python_version, files: {rel: {sha256_lf, bytes_lf, lines}}}
over exactly the LOCKED files (sha over the bytes with CRLF -> LF, in memory; files are never rewritten and their
content is never printed or copied). judge_kit.py is not here: the codebook freeze (codebook_v1.json) covers it.

Run from the repo root:
  python -m eval.s10.code_lock --show                          read-only: print sha256_lf / bytes_lf / lines
  python -m eval.s10.code_lock --expected-sha REL=SHA ...      one REL=SHA for EVERY locked file
Exit codes: 0 shown / written · 1 sha mismatch (nothing written) · 2 bad or missing argument, file missing
(nothing written) · 3 manifest exists (write-once).
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys

from .prereg_freeze import compute

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_REL = 'eval/s10/manifests/s10b2b_code_lock.json'
MANIFEST_FORMAT = 's10b2b-code-lock.v1'
LOCKED = (
    'eval/s10/judge_output.py',
    'eval/s10/judge_runner.py',
    'eval/s10/metrics.py',
    'eval/s10/agreement.py',
    'eval/s10/merge.py',
    'eval/s10/gate.py',
    'eval/s10/draws.py',
    'eval/s10/sheets.py',
    'eval/s10/diagnostics.py',
)


def parse_expected(pairs):
    """['REL=SHA', ...] -> {rel: sha}; ValueError on bad syntax, a rel outside LOCKED, a duplicate or a gap."""
    out = {}
    for pair in pairs or []:
        rel, sep, sha = pair.partition('=')
        rel, sha = rel.strip(), sha.strip().lower()
        if not sep or rel not in LOCKED or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            raise ValueError(f'bad --expected-sha (want REL=SHA with REL one of the locked files): {rel or pair!r}')
        if rel in out:
            raise ValueError(f'--expected-sha given twice for {rel}')
        out[rel] = sha
    missing = [rel for rel in LOCKED if rel not in out]
    if missing:
        raise ValueError('--expected-sha is required for every locked file; missing: ' + ', '.join(missing))
    return out


def build_manifest(stats, now=None, python_version=None):
    return dict(format=MANIFEST_FORMAT, created_at=now or datetime.now(timezone.utc).isoformat(),
                python_version=python_version or platform.python_version(),
                files={rel: stats[rel] for rel in LOCKED})


def main(argv=None, *, root=ROOT, now=None, out=print, err=None) -> int:
    err = err or (lambda s: print(s, file=sys.stderr))
    ap = argparse.ArgumentParser(prog='python -m eval.s10.code_lock', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--show', action='store_true', help='read-only: print the stats of every locked file')
    mode.add_argument('--expected-sha', action='append', metavar='REL=SHA')
    args = ap.parse_args(argv)
    root = Path(root)
    missing = [rel for rel in LOCKED if not (root / rel).is_file()]
    if missing:
        err('ERROR: file(s) to lock not found; nothing written: ' + ', '.join(missing))
        return 2
    stats = {rel: compute((root / rel).read_bytes()) for rel in LOCKED}
    if args.show:
        for rel in LOCKED:
            s = stats[rel]
            out(f'{rel}: sha256_lf={s["sha256_lf"]} bytes_lf={s["bytes_lf"]} lines={s["lines"]}')
        return 0
    manifest_path = root / MANIFEST_REL
    if manifest_path.exists():
        err(f'REFUSED: manifest already exists (write-once): {MANIFEST_REL}')
        return 3
    try:
        expected = parse_expected(args.expected_sha)
    except ValueError as exc:
        err(f'ERROR: {exc}; nothing written')
        return 2
    bad = [rel for rel in LOCKED if stats[rel]['sha256_lf'] != expected[rel]]
    if bad:
        err('ERROR: sha256 (LF) mismatch; nothing written')
        for rel in bad:
            err(f'  {rel}: expected {expected[rel]} computed {stats[rel]["sha256_lf"]}')
        return 1
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(build_manifest(stats, now), indent=2) + '\n')
    out(f'written {MANIFEST_REL} ({len(LOCKED)} files, python {platform.python_version()})')
    for rel in LOCKED:
        out(f'  {rel}: sha256_lf={stats[rel]["sha256_lf"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
