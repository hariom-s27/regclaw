"""Label freeze tests use synthetic files beneath tmp_path, never real inputs."""
import csv
from datetime import datetime, timedelta, timezone
import io
import json

import pytest

from eval.s10 import label_freeze as LF
from eval.s10.label_tool import FIELDS
from standards.ruleset import normalised_sha

LEAK = 'LEAKWORD'
NOW = '2026-11-16T12:00:00+00:00'
KEYS = {
    'format', 'labeller', 'file', 'upto', 'sha256_lf', 'bytes_lf', 'data_rows',
    'first_label_utc', 'last_label_utc', 'audit_list_sha256_lf', 'codebook_sha256_lf',
    'code_lock_manifest_sha256_lf', 'created_at',
}
LABELS = ('not_applicable', 'recommended', 'mandatory')
CONFIDENCES = ('low', 'medium', 'high')


def write_csv(path, fields, rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\r\n')
    writer.writeheader()
    writer.writerows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(stream.getvalue().encode('utf-8-sig'))


def labels_path(root, labeller='hariom'):
    return root / LF.LABELS_REL.format(labeller)


def manifest_path(root, labeller='hariom'):
    return root / LF.MANIFEST_REL.format(labeller)


def make_root(tmp_path, labeller='hariom'):
    root = tmp_path / 'root'
    audit = [dict(order=i, j_id=f'fake-device-{LEAK}', item=f'fake-item-{i:03d}')
             for i in range(1, 152)]
    write_csv(root / LF.AUDIT_REL, ('order', 'j_id', 'item'), audit)
    manifests = root / 'eval/s10/manifests'
    manifests.mkdir(parents=True)
    (root / LF.BUILD_REL).write_text(json.dumps({'files': {
        LF.AUDIT_REL: {'sha256_lf': normalised_sha((root / LF.AUDIT_REL).read_bytes())},
    }}), encoding='utf-8')
    (root / LF.CODEBOOK_MANIFEST_REL).write_text(json.dumps({'files': {
        LF.CODEBOOK_REL: {'sha256_lf': 'a' * 64},
    }}), encoding='utf-8')
    (root / LF.CODE_LOCK_REL).write_bytes(b'{\r\n  "format": "fake-code-lock", "files": {}\r\n}\r\n')
    start = datetime(2026, 11, 16, tzinfo=timezone.utc)
    rows = [dict(cell, label=LABELS[i % 3], confidence=CONFIDENCES[i % 3],
                 reason=f'fake reason {LEAK}', timestamp=(start + timedelta(seconds=i)).isoformat())
            for i, cell in enumerate(audit[:150])]
    write_csv(labels_path(root, labeller), FIELDS, rows)
    return root, rows, audit


def go(root, capsys, *, labeller='hariom', expected=None):
    path = labels_path(root, labeller)
    expected = normalised_sha(path.read_bytes()) if expected is None else expected
    code = LF.main(['--labeller', labeller, '--expected-sha', expected], root=root, now=NOW)
    captured = capsys.readouterr()
    assert LEAK not in captured.out + captured.err
    return code, captured.out, captured.err


def assert_refused(root, capsys, check):
    before = labels_path(root).read_bytes()
    code, out, err = go(root, capsys)
    assert code == 2 and not out
    assert err == f'ERROR: {check} check failed; nothing written\n'
    assert not manifest_path(root).exists()
    assert labels_path(root).read_bytes() == before


@pytest.mark.parametrize('labeller', ['hariom', 'rater2'])
def test_success_exact_shape_and_no_label_values(tmp_path, capsys, labeller):
    root, rows, _ = make_root(tmp_path, labeller)
    source = labels_path(root, labeller)
    before = source.read_bytes()
    assert before.startswith(b'\xef\xbb\xbf') and b'\r\n' in before
    code, out, err = go(root, capsys, labeller=labeller)
    assert code == 0 and not err
    raw = manifest_path(root, labeller).read_bytes()
    doc = json.loads(raw)
    assert set(doc) == KEYS and len(doc) == 13
    assert doc['format'] == 's10-labels-manifest.v1'
    assert doc['labeller'] == labeller and doc['file'] == LF.LABELS_REL.format(labeller)
    assert doc['upto'] == doc['data_rows'] == 150
    assert doc['sha256_lf'] == normalised_sha(before)
    assert doc['bytes_lf'] == len(before.replace(b'\r\n', b'\n'))
    assert doc['first_label_utc'] == rows[0]['timestamp']
    assert doc['last_label_utc'] == rows[-1]['timestamp']
    assert doc['audit_list_sha256_lf'] == normalised_sha((root / LF.AUDIT_REL).read_bytes())
    assert doc['codebook_sha256_lf'] == 'a' * 64
    assert doc['code_lock_manifest_sha256_lf'] == normalised_sha((root / LF.CODE_LOCK_REL).read_bytes())
    assert doc['created_at'] == NOW
    assert not any(value in LABELS + CONFIDENCES for value in doc.values())
    assert not {'label', 'confidence', 'reason', 'cell_id', 'j_id', 'counts'} & set(doc)
    assert LEAK not in raw.decode('utf-8') and 'fake-item-' not in raw.decode('utf-8')
    assert b'\r\n' not in raw and source.read_bytes() == before
    assert LF.MANIFEST_REL.format(labeller) in out
    for key in ('sha256_lf', 'bytes_lf', 'data_rows', 'last_label_utc'):
        assert f'{key}={doc[key]}' in out
    for value in LABELS + CONFIDENCES:
        assert f'{value}=50' in out
    assert len(out.splitlines()) == 3


def test_write_once(tmp_path, capsys):
    root, _, _ = make_root(tmp_path)
    assert go(root, capsys)[0] == 0
    before = manifest_path(root).read_bytes()
    code, out, err = go(root, capsys)
    assert code == 3 and not out and 'write-once' in err
    assert manifest_path(root).read_bytes() == before


def test_sha_mismatch_prints_only_expected_and_computed(tmp_path, capsys):
    root, _, _ = make_root(tmp_path)
    actual = normalised_sha(labels_path(root).read_bytes())
    code, out, err = go(root, capsys, expected='0' * 64)
    assert code == 1 and not out and not manifest_path(root).exists()
    assert err == ('ERROR: sha256 (LF) mismatch; nothing written\n'
                   f'  expected: {"0" * 64}\n  computed: {actual}\n')


@pytest.mark.parametrize('count', [149, 151])
def test_raw_row_count(tmp_path, capsys, count):
    root, rows, _ = make_root(tmp_path)
    records = rows[:count] if count < 150 else rows + [dict(rows[-1])]
    write_csv(labels_path(root), FIELDS, records)
    assert_refused(root, capsys, 'data-row count')


def test_duplicate_cell(tmp_path, capsys):
    root, rows, _ = make_root(tmp_path)
    rows[-1].update(j_id=rows[0]['j_id'], item=rows[0]['item'])
    write_csv(labels_path(root), FIELDS, rows)
    assert_refused(root, capsys, 'duplicate-cell')


@pytest.mark.parametrize('change', ['row-order', 'order-value', 'outside-prefix'])
def test_audit_positions(tmp_path, capsys, change):
    root, rows, audit = make_root(tmp_path)
    if change == 'row-order':
        rows[0], rows[1] = rows[1], rows[0]
    elif change == 'order-value':
        rows[0]['order'] = 2
    else:
        rows[-1].update(audit[-1])
    write_csv(labels_path(root), FIELDS, rows)
    assert_refused(root, capsys, 'audit-list positions')


@pytest.mark.parametrize('field,value', [
    ('label', LEAK), ('confidence', LEAK), ('reason', ''),
    ('reason', f'{LEAK}\nsecond line'), ('reason', LEAK * 100),
])
def test_label_validation_never_echoes_input(tmp_path, capsys, field, value):
    root, rows, _ = make_root(tmp_path)
    rows[0][field] = value
    write_csv(labels_path(root), FIELDS, rows)
    assert_refused(root, capsys, 'label validation')


def test_bad_header(tmp_path, capsys):
    root, _, _ = make_root(tmp_path)
    write_csv(labels_path(root), FIELDS[::-1], [])
    assert_refused(root, capsys, 'CSV header')


def test_bad_timestamp(tmp_path, capsys):
    root, rows, _ = make_root(tmp_path)
    rows[0]['timestamp'] = LEAK
    write_csv(labels_path(root), FIELDS, rows)
    assert_refused(root, capsys, 'timestamp')


def test_first_and_last_are_chronological_utc(tmp_path, capsys):
    root, rows, _ = make_root(tmp_path)
    rows[0]['timestamp'] = '2026-11-17T06:00:00+05:30'
    rows[-1]['timestamp'] = '2026-11-15T23:00:00Z'
    write_csv(labels_path(root), FIELDS, rows)
    assert go(root, capsys)[0] == 0
    doc = json.loads(manifest_path(root).read_text(encoding='utf-8'))
    assert doc['first_label_utc'] == '2026-11-15T23:00:00+00:00'
    assert doc['last_label_utc'] == '2026-11-17T00:30:00+00:00'


def test_timestamp_without_offset_refused(tmp_path, capsys):
    root, rows, _ = make_root(tmp_path)
    rows[0]['timestamp'] = '2026-11-15T23:00:00'
    write_csv(labels_path(root), FIELDS, rows)
    assert_refused(root, capsys, 'timestamp')


def test_changed_audit_list_refused(tmp_path, capsys):
    root, _, _ = make_root(tmp_path)
    with (root / LF.AUDIT_REL).open('ab') as stream:
        stream.write(LEAK.encode('utf-8'))
    assert_refused(root, capsys, 'audit-list')


@pytest.mark.parametrize('rel,check', [
    (LF.BUILD_REL, 'audit-list'), (LF.CODEBOOK_MANIFEST_REL, 'codebook manifest'),
])
def test_bad_dependency_manifest(tmp_path, capsys, rel, check):
    root, _, _ = make_root(tmp_path)
    (root / rel).write_text(LEAK, encoding='utf-8')
    assert_refused(root, capsys, check)


def test_bad_encoding(tmp_path, capsys):
    root, _, _ = make_root(tmp_path)
    labels_path(root).write_bytes(b'\xff' + LEAK.encode('utf-8'))
    assert_refused(root, capsys, 'CSV decoding')


def test_extra_csv_column(tmp_path, capsys):
    root, _, _ = make_root(tmp_path)
    path = labels_path(root)
    text = path.read_text(encoding='utf-8-sig').splitlines()
    text[1] += ',' + LEAK
    path.write_bytes(('\r\n'.join(text) + '\r\n').encode('utf-8-sig'))
    assert_refused(root, capsys, 'CSV structure')


@pytest.mark.parametrize('args', [
    ['--labeller', 'hariom'],
    ['--expected-sha', 'a' * 64],
    ['--labeller', 'hariom', '--expected-sha', 'A' * 64],
    ['--labeller', 'hariom', '--expected-sha', 'a' * 63],
    ['--labeller', 'hariom', '--expected-sha', 'g' * 64],
    ['--labeller', 'other', '--expected-sha', 'a' * 64],
    ['--labeller', 'hariom', '--expected-sha', 'a' * 64, '--upto', '149'],
])
def test_cli_requires_fixed_arguments(tmp_path, capsys, args):
    with pytest.raises(SystemExit) as exc:
        LF.main(args, root=tmp_path)
    assert exc.value.code == 2
    assert not list(tmp_path.iterdir())
    assert LEAK not in capsys.readouterr().err
