"""S10b2 (S9): freeze codebook v1.0 and the judge setup: write a write-once sha manifest (stdlib + repo helpers).

Writes eval/s10/manifests/codebook_v1.json, the shape judge_kit.check_eval_manifest and label_tool read:
  {format, version, template_version, files: {rel: {sha256_lf, bytes_lf, lines}}, created_at}
over exactly these files (sha over the bytes with CRLF -> LF, in memory; files are never rewritten and
their content is never printed or copied into the manifest):
  eval/s10/codebook/CODEBOOK_v1.0.md, eval/s10/codebook/JUDGE_INSTRUCTIONS_v1.0.md,
  eval/s10/codebook/judge_output.schema.json, eval/s10/judge_kit.py, eval/s10/judge_config.yaml,
  specs/S10_PREREG_ADDENDUM_A1.md (git-ignored; Hariom places it).

Run from the repo root:  python -m eval.s10.codebook_freeze [--expected-sha REL=SHA ...]
Exit codes: 0 written · 1 sha mismatch · 2 file missing or bad argument · 3 manifest exists (write-once).
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from .judge_kit import TEMPLATE_VERSION
from .prereg_freeze import compute

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_REL = 'eval/s10/manifests/codebook_v1.json'
MANIFEST_FORMAT = 's10-codebook-manifest.v1'
VERSION = 'v1.0'
FROZEN = (
    'eval/s10/codebook/CODEBOOK_v1.0.md',
    'eval/s10/codebook/JUDGE_INSTRUCTIONS_v1.0.md',
    'eval/s10/codebook/judge_output.schema.json',
    'eval/s10/judge_kit.py',
    'eval/s10/judge_config.yaml',
    'specs/S10_PREREG_ADDENDUM_A1.md',
)


def parse_expected(pairs):
    """['REL=SHA', ...] -> {rel: sha}; ValueError on bad syntax or a rel outside FROZEN."""
    out = {}
    for pair in pairs or []:
        rel, sep, sha = pair.partition('=')
        rel, sha = rel.strip(), sha.strip().lower()
        if not sep or rel not in FROZEN or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            raise ValueError(f'bad --expected-sha (want REL=SHA with REL one of the frozen files): {rel or pair!r}')
        if rel in out:
            raise ValueError(f'--expected-sha given twice for {rel}')
        out[rel] = sha
    return out


def build_manifest(stats, now=None):
    return dict(format=MANIFEST_FORMAT, version=VERSION, template_version=TEMPLATE_VERSION,
                files={rel: stats[rel] for rel in FROZEN},
                created_at=now or datetime.now(timezone.utc).isoformat())


def main(argv=None, *, root=ROOT, now=None, out=print, err=None) -> int:
    err = err or (lambda s: print(s, file=sys.stderr))
    ap = argparse.ArgumentParser(prog='python -m eval.s10.codebook_freeze', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--expected-sha', action='append', default=[], metavar='REL=SHA')
    args = ap.parse_args(argv)
    root = Path(root)
    manifest_path = root / MANIFEST_REL
    if manifest_path.exists():
        err(f'REFUSED: manifest already exists (write-once): {MANIFEST_REL}')
        return 3
    try:
        expected = parse_expected(args.expected_sha)
    except ValueError as exc:
        err(f'ERROR: {exc}')
        return 2
    missing = [rel for rel in FROZEN if not (root / rel).is_file()]
    if missing:
        err('ERROR: file(s) to freeze not found; nothing written: ' + ', '.join(missing))
        return 2
    stats = {rel: compute((root / rel).read_bytes()) for rel in FROZEN}
    bad = [rel for rel, sha in expected.items() if stats[rel]['sha256_lf'] != sha]
    if bad:
        err('ERROR: sha256 (LF) mismatch; nothing written')
        for rel in bad:
            err(f'  {rel}: expected {expected[rel]} computed {stats[rel]["sha256_lf"]}')
        return 1
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(build_manifest(stats, now), indent=2) + '\n')
    out(f'written {MANIFEST_REL} (template_version {TEMPLATE_VERSION}, {len(FROZEN)} files)')
    for rel in FROZEN:
        s = stats[rel]
        out(f'  {rel}: sha256_lf={s["sha256_lf"]} bytes_lf={s["bytes_lf"]} lines={s["lines"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
