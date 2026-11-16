"""Freeze the first Audit A labels as a write-once, content-free sha manifest.

Run from the repo root:
  python -m eval.s10.label_freeze --labeller hariom --expected-sha <64 lowercase hex>
The only labellers are hariom and rater2; the prefix is fixed at 150 rows.
Labels are read as bytes and decoded in memory, never rewritten or copied out.
Only aggregate label/confidence counts are printed; they are never persisted.
First/last label times are the earliest/latest instants, expressed in UTC.
ISO timestamps must include a UTC offset; missing offsets fail the timestamp check.

Exit codes: 0 written; 1 LF sha mismatch; 2 invalid input, argument or I/O error;
3 manifest exists (write-once, including an exclusive-create race).
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import sys

from standards.ruleset import normalised_sha

from .draws import load_audit_rows
from .label_tool import FIELDS, validate_label

ROOT = Path(__file__).resolve().parents[2]
LABELLERS = ('hariom', 'rater2')
UPTO = 150
FORMAT = 's10-labels-manifest.v1'
LABELS_REL = 'data/gold/s10/labels_{}_auditA.csv'
MANIFEST_REL = 'eval/s10/manifests/auditA_labels_{}_150.json'
AUDIT_REL = 'data/gold/s10/audit_a_list.csv'
BUILD_REL = 'eval/s10/manifests/s10b1_build.json'
CODEBOOK_REL = 'eval/s10/codebook/CODEBOOK_v1.0.md'
CODEBOOK_MANIFEST_REL = 'eval/s10/manifests/codebook_v1.json'
CODE_LOCK_REL = 'eval/s10/manifests/s10b2b_code_lock.json'


class FreezeError(ValueError):
    """The argument is a fixed check name, never input content."""


def parse_sha(value):
    if not re.fullmatch(r'[0-9a-f]{64}', value):
        raise argparse.ArgumentTypeError('expected-sha must be 64 lowercase hexadecimal characters')
    return value


def checked_audit(root):
    try:
        rows = load_audit_rows(root)
        manifest = json.loads((root / BUILD_REL).read_text(encoding='utf-8'))
        sha = manifest['files'][AUDIT_REL]['sha256_lf']
        if len(rows) < UPTO or not re.fullmatch(r'[0-9a-f]{64}', sha):
            raise ValueError
        return rows[:UPTO], sha
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FreezeError('audit-list') from exc


def checked_rows(raw, audit_rows):
    try:
        reader = csv.reader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
        header = next(reader, [])
        records = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise FreezeError('CSV decoding') from exc
    if tuple(header) != FIELDS:
        raise FreezeError('CSV header')
    if len(records) != UPTO:
        raise FreezeError('data-row count')
    if any(len(record) != len(FIELDS) for record in records):
        raise FreezeError('CSV structure')
    rows = [dict(zip(FIELDS, record)) for record in records]
    cells = [(row['j_id'], row['item']) for row in rows]
    if len(set(cells)) != len(cells):
        raise FreezeError('duplicate-cell')
    want = [(str(i), row['j_id'], row['item']) for i, row in enumerate(audit_rows, 1)]
    if [(row['order'], row['j_id'], row['item']) for row in rows] != want:
        raise FreezeError('audit-list positions')
    labels = Counter(not_applicable=0, recommended=0, mandatory=0)
    confidences = Counter(low=0, medium=0, high=0)
    for row in rows:
        try:
            valid = validate_label(row)
        except (ValueError, TypeError) as exc:
            raise FreezeError('label validation') from exc
        labels[valid['label']] += 1
        confidences[valid['confidence']] += 1
    timestamps = []
    for row in rows:
        try:
            stamp = datetime.fromisoformat(row['timestamp'])
            if stamp.utcoffset() is None:
                raise ValueError
            timestamps.append(stamp.astimezone(timezone.utc))
        except (ValueError, TypeError, OverflowError) as exc:
            raise FreezeError('timestamp') from exc
    return min(timestamps).isoformat(), max(timestamps).isoformat(), labels, confidences


def codebook_sha(root):
    try:
        manifest = json.loads((root / CODEBOOK_MANIFEST_REL).read_text(encoding='utf-8'))
        sha = manifest['files'][CODEBOOK_REL]['sha256_lf']
        if not re.fullmatch(r'[0-9a-f]{64}', sha):
            raise ValueError
        return sha
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FreezeError('codebook manifest') from exc


def main(argv=None, *, root=ROOT, now=None, out=print, err=None) -> int:
    err = err or (lambda message: print(message, file=sys.stderr))
    parser = argparse.ArgumentParser(prog='python -m eval.s10.label_freeze', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--labeller', choices=LABELLERS, required=True)
    parser.add_argument('--expected-sha', type=parse_sha, required=True)
    args = parser.parse_args(argv)
    root = Path(root)
    labels_rel = LABELS_REL.format(args.labeller)
    manifest_rel = MANIFEST_REL.format(args.labeller)
    destination = root / manifest_rel
    if destination.exists():
        err(f'REFUSED: manifest already exists (write-once): {manifest_rel}')
        return 3
    try:
        raw = (root / labels_rel).read_bytes()
    except OSError:
        err('ERROR: labels-file check failed; nothing written')
        return 2
    sha = normalised_sha(raw)
    if sha != args.expected_sha:
        err('ERROR: sha256 (LF) mismatch; nothing written')
        err(f'  expected: {args.expected_sha}')
        err(f'  computed: {sha}')
        return 1
    try:
        audit_rows, audit_sha = checked_audit(root)
        first, last, labels, confidences = checked_rows(raw, audit_rows)
        book_sha = codebook_sha(root)
        try:
            lock_sha = normalised_sha((root / CODE_LOCK_REL).read_bytes())
        except OSError as exc:
            raise FreezeError('code-lock manifest') from exc
    except FreezeError as exc:
        err(f'ERROR: {exc} check failed; nothing written')
        return 2
    manifest = dict(
        format=FORMAT,
        labeller=args.labeller,
        file=labels_rel,
        upto=UPTO,
        sha256_lf=sha,
        bytes_lf=len(raw.replace(b'\r\n', b'\n')),
        data_rows=UPTO,
        first_label_utc=first,
        last_label_utc=last,
        audit_list_sha256_lf=audit_sha,
        codebook_sha256_lf=book_sha,
        code_lock_manifest_sha256_lf=lock_sha,
        created_at=now or datetime.now(timezone.utc).isoformat(),
    )
    text = json.dumps(manifest, indent=2) + '\n'
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
    except FileExistsError:
        err(f'REFUSED: manifest already exists (write-once): {manifest_rel}')
        return 3
    except OSError:
        err('ERROR: manifest-write check failed')
        return 2
    out(f'written {manifest_rel}: sha256_lf={sha} bytes_lf={manifest["bytes_lf"]} '
        f'data_rows={UPTO} last_label_utc={last}')
    out('per-label counts: ' + ' '.join(f'{key}={labels[key]}' for key in sorted(labels)))
    out('per-confidence counts: ' + ' '.join(f'{key}={confidences[key]}' for key in sorted(confidences)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
