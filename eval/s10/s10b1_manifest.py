"""S10b-09: committed build manifest for S10b1 (LF sha256 + counts; no content).

Hashes: judge_file_v1.yaml, audit_a_list.csv, practice_profiles.yaml, CODEBOOK_v0.1.md,
jid_map_manifest.json, holdout_access_ledger.json. Never reads jid_map.json or any profile set
other than the practice file. Refuses unless the ledger outcome is "written".
Writes eval/s10/manifests/s10b1_build.json (write-once).

Run from the repo root:  python -m eval.s10.s10b1_manifest
Exit codes: 0 written · 2 input error · 3 manifest exists.
"""
import csv
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from standards.ruleset import normalised_sha

ROOT = Path(__file__).resolve().parents[2]
SEED = 'S10b-v1'
MANIFEST_FORMAT = 's10b1-build-manifest.v1'
MANIFEST_REL = 'eval/s10/manifests/s10b1_build.json'
JUDGE_REL = 'data/gold/s10/judge_file_v1.yaml'
AUDIT_REL = 'data/gold/s10/audit_a_list.csv'
PRACTICE_REL = 'data/gold/s10/practice_profiles.yaml'
CODEBOOK_REL = 'eval/s10/codebook/CODEBOOK_v0.1.md'
MAP_MANIFEST_REL = 'data/gold/s10/jid_map_manifest.json'
LEDGER_REL = 'data/gold/s10/holdout_access_ledger.json'
FILES = (JUDGE_REL, AUDIT_REL, PRACTICE_REL, CODEBOOK_REL, MAP_MANIFEST_REL, LEDGER_REL)


def _lines(raw: bytes) -> int:
    lf = raw.replace(b'\r\n', b'\n')
    return lf.count(b'\n') + (1 if lf and not lf.endswith(b'\n') else 0)


def counts_for(rel, raw: bytes) -> dict:
    text = raw.decode('utf-8-sig')
    if rel == JUDGE_REL:
        doc = yaml.safe_load(text)
        return dict(entries=len(doc['entries']), items_total=sum(len(e['items']) for e in doc['entries']))
    if rel == AUDIT_REL:
        rows = list(csv.DictReader(io.StringIO(text, newline='')))
        return dict(rows=len(rows), jids=len({r['j_id'] for r in rows}))
    if rel == PRACTICE_REL:
        return dict(profiles=len(yaml.safe_load(text)['profiles']))
    if rel == CODEBOOK_REL:
        return dict(lines=_lines(raw))
    if rel == MAP_MANIFEST_REL:
        return dict(files_sealed=len(json.loads(text)['files']))
    doc = json.loads(text)   # ledger
    return dict(files_read=len(doc['files_read']), outputs_written=len(doc['outputs_written']),
                outcome=doc['outcome'])


def run(root=ROOT, now=None, out=print) -> int:
    root = Path(root)
    dest = root / MANIFEST_REL
    if dest.exists():
        out(f'REFUSED (write-once): already exists: {MANIFEST_REL}')
        return 3
    missing = [rel for rel in FILES if not (root / rel).is_file()]
    if missing:
        out('ERROR: missing: ' + ', '.join(missing))
        return 2
    files = {}
    try:
        for rel in FILES:
            raw = (root / rel).read_bytes()
            files[rel] = dict(sha256_lf=normalised_sha(raw), **counts_for(rel, raw))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        out(f'ERROR: {type(exc).__name__} while reading inputs; nothing written')
        return 2
    if files[LEDGER_REL]['outcome'] != 'written':
        out('ERROR: ledger outcome is not "written"; nothing written')
        return 2
    manifest = dict(format=MANIFEST_FORMAT, seed=SEED, files=files,
                    date=now or datetime.now(timezone.utc).isoformat())
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(manifest, indent=2) + '\n')
    for rel, info in files.items():
        extra = ' '.join(f'{k}={v}' for k, v in info.items() if k != 'sha256_lf')
        out(f'{rel}: sha256_lf={info["sha256_lf"]} {extra}')
    out(f'written {MANIFEST_REL}')
    return 0


def main() -> int:
    return run()


if __name__ == '__main__':
    sys.exit(main())
