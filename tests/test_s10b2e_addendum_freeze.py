"""A2 freeze on fake specs files; no real addendum, gold or sealed inputs."""
from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest

from eval.s10 import addendum_freeze as AF

REPO = Path(__file__).resolve().parents[1]
REL = 'specs/S10_PREREG_ADDENDUM_A2.md'
NOW = '2026-11-17T12:00:00+00:00'
PRIVATE = 'PRIVATE-ADDENDUM-CONTENT'


@pytest.fixture(autouse=True)
def fake_only(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path.resolve().is_relative_to((REPO / 'data/gold/s10').resolve()):
            raise AssertionError('real S10 access forbidden')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guarded)


@pytest.fixture
def root(tmp_path):
    assert tmp_path.resolve().is_relative_to(REPO.resolve())
    return tmp_path / 'fake-addendum-root'


def sha(raw):
    return hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()


def put(root, relative=REL, raw=None):
    raw = raw if raw is not None else (PRIVATE + '\r\nsecond line\r\n').encode('utf-8')
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def go(root, *, file=REL, expected):
    out, err = [], []
    code = AF.main(['--file', file, '--expected-sha', expected], root=root, now=NOW,
                   out=out.append, err=err.append)
    assert PRIVATE not in '\n'.join(out + err)
    return code, out, err


def test_specs_accepted_lf_manifest_and_input_unchanged(root):
    raw = put(root)
    code, out, err = go(root, expected=sha(raw))
    assert code == 0 and err == []
    manifest_raw = (root / AF.MANIFEST_REL).read_bytes()
    manifest = json.loads(manifest_raw)
    lf = raw.replace(b'\r\n', b'\n')
    assert manifest == dict(file=REL, sha256_lf=sha(raw), bytes=len(lf), lines=2, created_at=NOW)
    assert datetime.fromisoformat(manifest['created_at']).utcoffset().total_seconds() == 0
    assert b'\r' not in manifest_raw and PRIVATE not in manifest_raw.decode('utf-8')
    assert (root / REL).read_bytes() == raw
    assert out == [f'bytes={len(lf)} lines=2 sha256_lf={sha(raw)}',
                   f'{AF.MANIFEST_REL} sha256_lf={sha(manifest_raw)}']


@pytest.mark.parametrize('raw,want_bytes,want_lines', [
    (b'one\r\ntwo\r\n', 8, 2),  # 3+1+3+1 LF bytes; two terminated lines.
    (b'one\ntwo', 7, 2),         # 3+1+3 bytes; second line has no terminator.
    (b'x', 1, 1), (b'', 0, 0),
])
def test_manifest_hand_computed_counts(raw, want_bytes, want_lines):
    manifest = AF.build_manifest(REL, raw, now=NOW)
    assert manifest['bytes'] == want_bytes and manifest['lines'] == want_lines
    assert manifest['sha256_lf'] == sha(raw)


def test_data_path_refused_before_read(root, monkeypatch):
    raw = put(root, 'data/fake_a2.md')
    forbidden = root / 'data/fake_a2.md'
    original = Path.read_bytes
    attempts = []

    def guarded(path):
        if path.resolve() == forbidden.resolve():
            attempts.append(path)
            raise AssertionError('data input must not be read')
        return original(path)

    monkeypatch.setattr(Path, 'read_bytes', guarded)
    code, out, err = go(root, file='data/fake_a2.md', expected=sha(raw))
    assert code == 2 and out == [] and err and attempts == []
    assert not (root / AF.MANIFEST_REL).exists()


@pytest.mark.parametrize('file', ['data/a2.md', '.env', 'specs/.env',
                                  'specs/holdout_profiles.yaml', 'specs/profiles_draft.yaml',
                                  'specs/jid_map/anything.md', 'specs/jid_map.json',
                                  'specs/S8_sealed.md', '../outside.md', 'absolute'])
def test_disallowed_paths_write_nothing(root, file):
    file = str(root / REL) if file == 'absolute' else file
    code, out, err = go(root, file=file, expected='0' * 64)
    assert code == 2 and out == [] and err
    assert not root.exists()


@pytest.mark.parametrize('target', ['data', 'outside-root'])
def test_symlink_resolution_outside_specs_is_refused(root, monkeypatch, target):
    put(root, 'data/fake.md', b'fake\n')
    alias = root / REL
    original = Path.resolve

    def redirected(path, *args, **kwargs):
        # Simulate a redirected specs path without creating a Windows symlink.
        if path == alias:
            return original(root / 'data/fake.md' if target == 'data' else root.parent / 'fake-outside.md')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', redirected)
    assert go(root, expected=sha(b'fake\n'))[0] == 2
    assert not (root / AF.MANIFEST_REL).exists()


def test_output_resolution_outside_root_is_refused(root, monkeypatch):
    raw = put(root)
    destination = root / AF.MANIFEST_REL
    original = Path.resolve

    def redirected(path, *args, **kwargs):
        if path == destination:
            return original(root.parent / 'outside-manifest.json')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', redirected)
    assert go(root, expected=sha(raw))[0] == 2
    assert not destination.exists()
    assert not (root.parent / 'outside-manifest.json').exists()


def test_sha_mismatch_writes_nothing(root):
    raw = put(root)
    code, out, err = go(root, expected='0' * 64)
    assert code == 2 and out == []
    assert err == ['ERROR: sha256 (LF) mismatch; nothing written']
    assert not (root / AF.MANIFEST_REL).exists()
    assert (root / REL).read_bytes() == raw


@pytest.mark.parametrize('expected', ['bad', 'A' * 64, 'a' * 63])
def test_invalid_sha_format(root, expected):
    put(root)
    assert go(root, expected=expected)[0] == 2
    assert not (root / AF.MANIFEST_REL).exists()


def test_missing_specs_file_is_input_error(root):
    assert go(root, expected='a' * 64)[0] == 2
    assert not root.exists()


def test_second_run_refuses_and_preserves_manifest(root):
    raw = put(root)
    assert go(root, expected=sha(raw))[0] == 0
    before = (root / AF.MANIFEST_REL).read_bytes()
    code, out, err = go(root, expected=sha(raw))
    assert code == 3 and out == [] and 'write-once' in err[0]
    assert (root / AF.MANIFEST_REL).read_bytes() == before


def test_existing_output_precedes_input_read(root, monkeypatch):
    put(root, AF.MANIFEST_REL, b'keep\n')

    def forbidden(*args):
        raise AssertionError('existing output must be checked before inputs')

    monkeypatch.setattr(AF, 'checked_input_path', forbidden)
    assert go(root, expected='0' * 64)[0] == 3
    assert (root / AF.MANIFEST_REL).read_bytes() == b'keep\n'


def test_exclusive_create_race_preserves_other_writer(root, monkeypatch):
    raw = put(root)
    original = Path.open

    def race(path, mode='r', *args, **kwargs):
        if path == root / AF.MANIFEST_REL and mode == 'x':
            with original(path, 'xb') as stream:
                stream.write(b'other writer\n')
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', race)
    code, out, err = go(root, expected=sha(raw))
    assert code == 3 and out == [] and 'write-once' in err[0]
    assert (root / AF.MANIFEST_REL).read_bytes() == b'other writer\n'


@pytest.mark.parametrize('args', [[], ['--file', REL], ['--expected-sha', 'a' * 64]])
def test_required_cli_arguments(root, args):
    with pytest.raises(SystemExit) as exc:
        AF.main(args, root=root)
    assert exc.value.code == 2 and not root.exists()
