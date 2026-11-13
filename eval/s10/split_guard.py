"""Holdout seal for the S10 eval set.

Dev files load with no flag. Any file whose split (file-level or any row) is `holdout`
is REFUSED unless `final_holdout=True` (CLI --final-holdout) AND a sha manifest exists
and matches every holdout file byte-for-byte (CRLF->LF normalised). The manifest is
checked BEFORE the files are parsed. Manifests are write-once; several may be given, and
every holdout file must be listed in exactly one of them. A file mixing dev and holdout rows is always refused.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from standards.ruleset import normalised_sha

from .formats import validate_gold, validate_profile_set

MANIFEST_FORMAT = 's10-holdout-manifest.v1'
MANIFEST_NAME = 'holdout_manifest.json'


class HoldoutRefused(PermissionError):
    """Scoring or running the sealed holdout split was refused."""


def file_sha(path) -> str:
    return normalised_sha(Path(path).read_bytes())


def seal_holdout(files, manifest_path, *, now=None):
    """Write the sha manifest for the holdout files (run once, when Shyam seals the set).

    `manifest_path` is explicit and WRITE-ONCE: an existing manifest is never overwritten.
    Files sealed later (e.g. the gold) go into a second manifest; see verify_holdout."""
    files = [Path(f) for f in files]
    manifest_path = Path(manifest_path)
    if manifest_path.exists():
        raise HoldoutRefused(f'holdout manifest already exists (write-once): {manifest_path}')
    manifest = dict(format=MANIFEST_FORMAT,
                    sealed_at=now or datetime.now(timezone.utc).isoformat(),
                    files={f.name: file_sha(f) for f in files})
    with manifest_path.open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest_path


def _manifest_paths(manifest_path):
    if isinstance(manifest_path, (str, Path)):
        return [Path(manifest_path)]
    return [Path(p) for p in manifest_path]


def verify_holdout(files, manifest_path):
    """Raise HoldoutRefused unless every file is listed in EXACTLY ONE of the given manifests
    (one path or a list of paths) with a matching sha. Returns the manifest(s) read."""
    manifests = []
    for path in _manifest_paths(manifest_path):
        if not path.is_file():
            raise HoldoutRefused(f'holdout sha manifest missing: {path}')
        try:
            manifest = json.loads(path.read_text(encoding='utf-8'))
        except ValueError as exc:
            raise HoldoutRefused(f'holdout manifest unreadable: {path}') from exc
        if manifest.get('format') != MANIFEST_FORMAT or not isinstance(manifest.get('files'), dict):
            raise HoldoutRefused('holdout manifest has the wrong format')
        manifests.append(manifest)
    if not manifests:
        raise HoldoutRefused('holdout sha manifest missing: none given')
    for f in map(Path, files):
        listed = [m['files'][f.name] for m in manifests if f.name in m['files']]
        if not listed:
            raise HoldoutRefused(f'{f.name} is not listed in the holdout manifest')
        if len(listed) > 1:
            raise HoldoutRefused(f'{f.name} is listed in more than one holdout manifest')
        if file_sha(f) != listed[0]:
            raise HoldoutRefused(f'{f.name} sha does not match the holdout manifest (file changed after sealing)')
    return manifests[0] if len(manifests) == 1 else manifests


def _splits(doc, rows_key):
    splits = {doc.get('split')} if isinstance(doc, dict) else {None}
    for row in (doc.get(rows_key) or []) if isinstance(doc, dict) else []:
        if isinstance(row, dict) and 'split' in row:
            splits.add(row['split'])
    return splits


def _declared_split(path, rows_key):
    """Read the split markers only (no validation, nothing scored)."""
    doc = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    return doc, _splits(doc, rows_key)


def load_eval_files(profiles_path, gold_path=None, *, final_holdout=False, manifest_path=None, data=None):
    """Load and validate an eval set behind the split guard.

    Returns (split, profiles {id: entry}, gold {(id, item): entry} or None).
    """
    paths = [Path(profiles_path)] + ([Path(gold_path)] if gold_path else [])
    manifest_path = manifest_path if manifest_path else paths[0].parent / MANIFEST_NAME   # one path or a list
    if final_holdout:
        # Seal first: sha check before anything is parsed.
        verify_holdout(paths, manifest_path)
    docs, splits = [], set()
    for path, key in zip(paths, ['profiles', 'labels']):
        doc, s = _declared_split(path, key)
        docs.append(doc)
        splits |= s
    if 'holdout' in splits and not final_holdout:
        raise HoldoutRefused('holdout split is sealed: pass --final-holdout (with a valid sha manifest) to score it')
    if len(splits) != 1 or splits - {'dev', 'holdout'}:
        raise HoldoutRefused(f'eval files must hold exactly one split (dev or holdout); found {sorted(map(str, splits))}')
    split = splits.pop()
    if final_holdout and split != 'holdout':
        raise HoldoutRefused('--final-holdout given but the files are not the holdout split')
    profiles = validate_profile_set(docs[0], data)
    gold = validate_gold(docs[1], profiles, data) if gold_path else None
    return split, profiles, gold


__all__ = ['HoldoutRefused', 'seal_holdout', 'verify_holdout', 'load_eval_files']
