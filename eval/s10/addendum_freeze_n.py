"""Freeze an approved numbered addendum (A4 onward) into a write-once manifest."""
import argparse
import json
from pathlib import Path
import re
import sys

from standards.ruleset import normalised_sha
from .addendum_freeze import AddendumError, ROOT, build_manifest, checked_input_path


def main(argv=None, *, root=ROOT, now=None, out=print, err=None):
    err = err or (lambda message: print(message, file=sys.stderr))
    parser = argparse.ArgumentParser(prog='python -m eval.s10.addendum_freeze_n', description=__doc__)
    parser.add_argument('--number', required=True, type=int)
    parser.add_argument('--file', required=True)
    parser.add_argument('--expected-sha', required=True)
    args = parser.parse_args(argv)
    try:
        if args.number < 4:
            raise AddendumError('addendum_number')
        root = Path(root).resolve()
        relative = f'specs/S10_PREREG_ADDENDUM_A{args.number}.md'
        manifest_rel = f'eval/s10/manifests/addendum_a{args.number}.json'
        destination = root / manifest_rel
        if destination.exists() or destination.is_symlink():
            raise FileExistsError
        if not destination.resolve().is_relative_to(root):
            raise AddendumError('output_path')
        if Path(args.file).as_posix() != relative:
            raise AddendumError('addendum_filename')
        if not re.fullmatch(r'[0-9a-f]{64}', args.expected_sha):
            raise AddendumError('expected_sha')
        path, resolved_relative = checked_input_path(root, args.file)
        if resolved_relative != relative:
            raise AddendumError('addendum_filename')
        raw = path.read_bytes()
        if normalised_sha(raw) != args.expected_sha:
            raise AddendumError('addendum_sha')
        doc = build_manifest(relative, raw, now=now)
        text = json.dumps(doc, indent=2, ensure_ascii=False) + '\n'
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
    except FileExistsError:
        err('output_exists count=1')
        return 3
    except AddendumError as exc:
        err(f'{exc} count=1')
        return 2
    except (OSError, ValueError, TypeError, RuntimeError):
        err('input_or_output_validation count=1')
        return 2
    out(f'bytes={doc["bytes"]} lines={doc["lines"]} sha256_lf={doc["sha256_lf"]}')
    out(f'{manifest_rel} sha256_lf={normalised_sha(text.encode("utf-8"))}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
