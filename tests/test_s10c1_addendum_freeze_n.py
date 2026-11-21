"""Numbered addendum freezing on invented specs inside guarded temporary roots."""
import builtins
import hashlib
import io
import json
import os
from pathlib import Path

import pytest

import _netguard
from eval.s10 import addendum_freeze_n as A

STAMP = '2026-11-20T00:00:00+00:00'
PRIVATE = b'PRIVATE invented addendum\r\nsecond line\r\n'


@pytest.fixture
def root(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp('numberedworld')
    assert 's8' not in str(root).casefold()
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'numbered freezer network forbidden')
    repo = Path(__file__).resolve().parents[1]

    def guarded(real):
        def opened(file, mode='r', *args, **kwargs):
            if isinstance(file, (str, os.PathLike)):
                path = Path(file).resolve()
                if any(p.casefold().startswith('.env') or p.casefold() in
                       {'holdout_profiles.yaml', 'profiles_draft.yaml', 'jid_map.json', 'jid_map'}
                       for p in path.parts):
                    raise AssertionError('forbidden input')
                if not path.is_relative_to(root) and any(path.is_relative_to(folder) for folder in
                                                        (repo / 'data', repo / 'standards/data')):
                    raise AssertionError('real data forbidden')
                if any(c in mode for c in 'wax+') and not path.is_relative_to(root):
                    raise AssertionError('write outside fake root')
            return real(file, mode, *args, **kwargs)
        return opened

    monkeypatch.setattr(builtins, 'open', guarded(builtins.open))
    monkeypatch.setattr(io, 'open', guarded(io.open))
    return root


def put(root, number=4, raw=PRIVATE):
    path = root / f'specs/S10_PREREG_ADDENDUM_A{number}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def invoke(root, number=4, file=None, sha=None):
    out, err = [], []
    file = file if file is not None else f'specs/S10_PREREG_ADDENDUM_A{number}.md'
    sha = sha if sha is not None else hashlib.sha256(PRIVATE.replace(b'\r\n', b'\n')).hexdigest()
    code = A.main(['--number', str(number), '--file', file, '--expected-sha', sha],
                  root=root, now=STAMP, out=out.append, err=err.append)
    assert 'PRIVATE' not in '\n'.join(out + err)
    return code, out, err


def snapshot(root):
    return {p.relative_to(root): p.read_bytes() if p.is_file() else None for p in root.rglob('*')}


@pytest.mark.parametrize('number', [4, 5, 17])
@pytest.mark.parametrize('raw', [PRIVATE, PRIVATE.replace(b'\r\n', b'\n'), b'', b'one\ntwo'])
def test_numbered_freeze_exact_fields_and_lf_counts(root, number, raw):
    source = put(root, number, raw)
    lf = raw.replace(b'\r\n', b'\n')
    sha = hashlib.sha256(lf).hexdigest()
    code, out, err = invoke(root, number, sha=sha)
    assert code == 0 and err == [] and len(out) == 2
    dest = root / f'eval/s10/manifests/addendum_a{number}.json'
    result = dest.read_bytes()
    assert json.loads(result) == dict(file=f'specs/S10_PREREG_ADDENDUM_A{number}.md',
        sha256_lf=sha, bytes=len(lf), lines=lf.count(b'\n') + int(bool(lf) and not lf.endswith(b'\n')),
        created_at=STAMP)
    assert source.read_bytes() == raw and b'\r' not in result and b'PRIVATE' not in result
    assert not (root / 'eval/s10/manifests/addendum_a2.json').exists()


@pytest.mark.parametrize('number', [-1, 0, 1, 2, 3])
def test_old_numbers_refuse_without_reads_or_writes(root, monkeypatch, number):
    before = snapshot(root)
    monkeypatch.setattr(A, 'checked_input_path', lambda *a: pytest.fail('input inspected'))
    assert invoke(root, number) == (2, [], ['addendum_number count=1'])
    assert snapshot(root) == before


@pytest.mark.parametrize('file', ['specs/S10_PREREG_ADDENDUM_A5.md', 'specs/other.md',
    'data/S10_PREREG_ADDENDUM_A4.md', '../specs/S10_PREREG_ADDENDUM_A4.md',
    'specs/sub/S10_PREREG_ADDENDUM_A4.md', '.env', 'specs/holdout_profiles.yaml'])
def test_filename_number_and_path_mismatch_refuse_before_read(root, monkeypatch, file):
    before = snapshot(root)
    monkeypatch.setattr(A, 'checked_input_path', lambda *a: pytest.fail('input inspected'))
    assert invoke(root, file=file) == (2, [], ['addendum_filename count=1'])
    assert snapshot(root) == before


@pytest.mark.parametrize('sha,diagnostic', [('INVALID', 'expected_sha'), ('A' * 64, 'expected_sha'),
                                          ('0' * 64, 'addendum_sha')])
def test_bad_sha_preserves_input_and_writes_nothing(root, sha, diagnostic):
    put(root)
    before = snapshot(root)
    assert invoke(root, sha=sha) == (2, [], [diagnostic + ' count=1'])
    assert snapshot(root) == before


def test_existing_manifest_precedes_input_read_and_preserves_bytes(root, monkeypatch):
    path = root / 'eval/s10/manifests/addendum_a4.json'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'FAKE existing manifest\n')
    before = snapshot(root)
    monkeypatch.setattr(A, 'checked_input_path', lambda *a: pytest.fail('input inspected'))
    assert invoke(root) == (3, [], ['output_exists count=1'])
    assert snapshot(root) == before


@pytest.mark.parametrize('target', ['data', 'outside', 'different-name', 'output-outside'])
def test_resolved_path_boundaries_refuse_without_writes(root, monkeypatch, target):
    source = put(root)
    dest = root / 'eval/s10/manifests/addendum_a4.json'
    real = Path.resolve

    def redirected(path, *args, **kwargs):
        if target == 'output-outside' and path == dest:
            return root.parent / 'outside.json'
        if target != 'output-outside' and path == source:
            return {'data': root / 'data/fake.md', 'outside': root.parent / 'fake.md',
                    'different-name': root / 'specs/S10_PREREG_ADDENDUM_A5.md'}[target]
        return real(path, *args, **kwargs)

    before = snapshot(root)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'resolve', redirected)
        assert invoke(root)[0] == 2
    assert snapshot(root) == before


@pytest.mark.parametrize('args', [[], ['--number', '4'], ['--file', 'specs/x.md'],
    ['--number', 'four', '--file', 'specs/S10_PREREG_ADDENDUM_A4.md', '--expected-sha', '0' * 64]])
def test_required_cli_arguments_and_integer_number(root, args):
    before = snapshot(root)
    with pytest.raises(SystemExit) as exc:
        A.main(args, root=root)
    assert exc.value.code == 2 and snapshot(root) == before


def test_exclusive_create_preserves_a_competing_manifest(root, monkeypatch):
    put(root)
    destination = root / 'eval/s10/manifests/addendum_a4.json'
    real = Path.open

    def raced(path, mode='r', *args, **kwargs):
        if path == destination and mode == 'x':
            with real(path, 'xb') as stream:
                stream.write(b'FAKE competing manifest\n')
        return real(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', raced)
    assert invoke(root) == (3, [], ['output_exists count=1'])
    assert destination.read_bytes() == b'FAKE competing manifest\n'
