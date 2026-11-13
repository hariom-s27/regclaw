"""Freeze the S10 pre-registration: write a write-once sha manifest (stdlib only).

The pre-registration (specs/S10_PREREGISTRATION.md) is local and git-ignored; the committed
manifest eval/s10/manifests/prereg_v1.json is the public proof of which version was frozen.
The sha is taken over the file bytes with CRLF -> LF, in memory; the file is never rewritten
and its content is never printed or copied into the manifest.

Run from the repo root:  python -m eval.s10.prereg_freeze
Exit codes: 0 written · 1 sha mismatch · 2 spec missing · 3 manifest exists (write-once).
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from standards.ruleset import normalised_sha

ROOT = Path(__file__).resolve().parents[2]
SPEC_REL = 'specs/S10_PREREGISTRATION.md'
MANIFEST_REL = 'eval/s10/manifests/prereg_v1.json'
EXPECTED_SHA256_LF = '84934b357da1fceff619a40d0a8595ad32d271d22ae734ed11fb0a8d8988eefe'
MANIFEST_FORMAT = 's10-prereg-manifest.v1'
VERSION = 'v1.0'
APPROVED = '2026-11-14'
DECISIONS = 'S10P-01..S10P-29 (D20)'


def compute(raw: bytes) -> dict:
    """sha256 / byte count / line count of the CRLF->LF normalised bytes."""
    lf = raw.replace(b'\r\n', b'\n')
    lines = lf.count(b'\n') + (1 if lf and not lf.endswith(b'\n') else 0)
    return dict(sha256_lf=normalised_sha(raw), bytes_lf=len(lf), lines=lines)


def build_manifest(stats: dict, now=None) -> dict:
    return dict(format=MANIFEST_FORMAT, file=SPEC_REL, sha256_lf=stats['sha256_lf'],
                bytes_lf=stats['bytes_lf'], lines=stats['lines'], version=VERSION,
                approved=APPROVED, decisions=DECISIONS,
                created_at=now or datetime.now(timezone.utc).isoformat())


def main(root=ROOT, expected=EXPECTED_SHA256_LF, now=None) -> int:
    root = Path(root)
    spec, manifest_path = root / SPEC_REL, root / MANIFEST_REL
    if manifest_path.exists():
        print(f'REFUSED: manifest already exists (write-once): {MANIFEST_REL}', file=sys.stderr)
        return 3
    if not spec.is_file():
        print(f'ERROR: pre-registration not found: {SPEC_REL}', file=sys.stderr)
        return 2
    stats = compute(spec.read_bytes())
    if stats['sha256_lf'] != expected:
        print('ERROR: sha256 (LF) mismatch; nothing written', file=sys.stderr)
        print(f'  expected: {expected}', file=sys.stderr)
        print(f'  computed: {stats["sha256_lf"]}', file=sys.stderr)
        return 1
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(build_manifest(stats, now), indent=2) + '\n')
    print(f'written {MANIFEST_REL}: sha256_lf={stats["sha256_lf"]} '
          f'bytes_lf={stats["bytes_lf"]} lines={stats["lines"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
