"""Freeze an approved A2 text file under specs/ as a write-once LF manifest.

Run later: python -m eval.s10.addendum_freeze --file specs/S10_PREREG_ADDENDUM_A2.md
           --expected-sha <expected LF sha>
No input content is printed or copied. Exit codes: 0 written, 2 input error,
3 output exists. Input and output paths must remain inside the repository.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

from standards.ruleset import normalised_sha

from .prereg_freeze import compute

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_REL = 'eval/s10/manifests/addendum_a2.json'
SEALED_NAMES = frozenset({'holdout_profiles.yaml', 'profiles_draft.yaml', 'jid_map.json', 'jid_map'})


class AddendumError(ValueError):
    """Fixed check names only; never include file content."""


def _protected(parts):
    return any(part.casefold() in SEALED_NAMES or part.casefold().startswith(('.env', 's8')) for part in parts)


def checked_input_path(root, file_rel):
    """Validate lexical and resolved paths before reading any input bytes."""
    root = Path(root).resolve()
    relative = Path(file_rel)
    if (relative.anchor or relative.drive or ':' in str(file_rel) or '..' in relative.parts
            or len(relative.parts) < 2 or relative.parts[0].casefold() != 'specs'
            or _protected(relative.parts)):
        raise AddendumError('input path check failed')
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise AddendumError('input path check failed')
    resolved = path.relative_to(root)
    if len(resolved.parts) < 2 or resolved.parts[0].casefold() != 'specs' or _protected(resolved.parts):
        raise AddendumError('input path check failed')
    if not path.is_file():
        raise AddendumError('input file check failed')
    return path, resolved.as_posix()


def build_manifest(file_rel, raw, *, now=None):
    stats = compute(raw)
    return dict(file=file_rel, sha256_lf=stats['sha256_lf'], bytes=stats['bytes_lf'],
                lines=stats['lines'], created_at=now or datetime.now(timezone.utc).isoformat())


def main(argv=None, *, root=ROOT, now=None, out=print, err=None):
    err = err or (lambda message: print(message, file=sys.stderr))
    parser = argparse.ArgumentParser(prog='python -m eval.s10.addendum_freeze', description=__doc__)
    parser.add_argument('--file', required=True)
    parser.add_argument('--expected-sha', required=True)
    args = parser.parse_args(argv)
    root = Path(root).resolve()
    destination = root / MANIFEST_REL
    created = False
    try:
        if destination.exists() or destination.is_symlink():
            err('REFUSED: output exists (write-once)')
            return 3
        if not destination.resolve().is_relative_to(root):
            raise AddendumError('output path check failed')
        if not re.fullmatch(r'[0-9a-f]{64}', args.expected_sha):
            raise AddendumError('expected sha format check failed')
        path, relative = checked_input_path(root, args.file)
        raw = path.read_bytes()
        if normalised_sha(raw) != args.expected_sha:
            err('ERROR: sha256 (LF) mismatch; nothing written')
            return 2
        doc = build_manifest(relative, raw, now=now)
        text = json.dumps(doc, indent=2, ensure_ascii=False) + '\n'
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('x', encoding='utf-8', newline='\n') as stream:
            created = True
            stream.write(text)
    except FileExistsError:
        err('REFUSED: output exists (write-once)')
        return 3
    except (OSError, ValueError, TypeError, RuntimeError):
        err(f'ERROR: addendum input or output check failed; files_created={int(created)}')
        return 2
    out(f'bytes={doc["bytes"]} lines={doc["lines"]} sha256_lf={doc["sha256_lf"]}')
    out(f'{MANIFEST_REL} sha256_lf={normalised_sha(text.encode("utf-8"))}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
