"""Code lock tests use fake module bytes; real-manifest check is read-only."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import platform

import pytest

from eval.s10 import second_rater as SR

REPO = Path(__file__).resolve().parents[1]
NOW = '2026-11-17T12:00:00+00:00'
LOCKED = ('eval/s10/second_rater.py', 'eval/s10/label_quality.py',
          'eval/s10/census.py', 'eval/s10/addendum_freeze.py')
SOURCES = (b'# fake a\r\nX = 1\r\n', b'# fake b\nX = 2\n', b'# fake c\r\nX = 3', b'')
NORMALISED = (b'# fake a\nX = 1\n', b'# fake b\nX = 2\n', b'# fake c\nX = 3', b'')


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
    return tmp_path / 'fake-code-lock-root'


def put(root, relative, raw):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def setup(root, *, omit=None):
    for relative, raw in zip(LOCKED, SOURCES):
        if relative != omit:
            put(root, relative, raw)


def go(root):
    out, err = [], []
    code = SR.main(['lock'], root=root, now=NOW, out=out.append, err=err.append)
    assert '# fake' not in '\n'.join(out + err)
    return code, out, err


def independent_stats(raw):
    lf = raw.replace(b'\r\n', b'\n')
    # splitlines independently counts the four fixture variants, including empty/no final LF.
    return dict(sha256_lf=hashlib.sha256(lf).hexdigest(), bytes_lf=len(lf), lines=len(lf.splitlines()))


def test_exact_file_list_and_fake_lock_contents(root):
    assert SR.CODE_LOCK_FILES == LOCKED
    setup(root)
    code, out, err = go(root)
    assert code == 0 and err == []
    raw = (root / SR.CODE_LOCK_REL).read_bytes()
    doc = json.loads(raw)
    assert set(doc) == {'format', 'created_at', 'python_version', 'files'}
    assert doc['format'] == SR.CODE_LOCK_FORMAT and doc['created_at'] == NOW
    assert doc['python_version'] == platform.python_version()
    assert datetime.fromisoformat(doc['created_at']).utcoffset().total_seconds() == 0
    assert list(doc['files']) == list(LOCKED)
    # First two modules: 8+1+5+1=15 LF bytes and 2 lines; third: 8+1+5=14 and 2;
    # fourth is empty: 0 bytes and 0 lines.
    for relative, normalised, count, lines in zip(LOCKED, NORMALISED, (15, 15, 14, 0), (2, 2, 2, 0)):
        assert doc['files'][relative] == dict(sha256_lf=hashlib.sha256(normalised).hexdigest(),
                                             bytes_lf=count, lines=lines)
    assert b'\r' not in raw and b'# fake' not in raw
    assert out[0] == 'files=4'
    assert out[-1] == f'{SR.CODE_LOCK_REL} sha256_lf={hashlib.sha256(raw).hexdigest()}'
    for relative, source in zip(LOCKED, SOURCES):
        assert (root / relative).read_bytes() == source


def test_second_lock_refuses_before_reading_sources(root, monkeypatch):
    setup(root)
    assert go(root)[0] == 0
    before = (root / SR.CODE_LOCK_REL).read_bytes()

    def forbidden(*args):
        raise AssertionError('existing lock must be checked before source reads')

    monkeypatch.setattr(SR, 'code_lock_stats', forbidden)
    code, out, err = go(root)
    assert code == 3 and out == [] and 'write-once' in err[0]
    assert (root / SR.CODE_LOCK_REL).read_bytes() == before


@pytest.mark.parametrize('missing', LOCKED)
def test_missing_module_writes_nothing(root, missing):
    setup(root, omit=missing)
    code, out, err = go(root)
    assert code == 2 and out == [] and err
    assert not (root / SR.CODE_LOCK_REL).exists()


def test_lf_and_crlf_stats_match_without_rewriting(root):
    setup(root)
    first = SR.code_lock_stats(root)
    for relative, raw in zip(LOCKED, NORMALISED):
        put(root, relative, raw)
    assert SR.code_lock_stats(root) == first
    assert first == {relative: independent_stats(source) for relative, source in zip(LOCKED, SOURCES)}


def test_manifest_builder_requires_exact_set():
    stats = {relative: independent_stats(source) for relative, source in zip(LOCKED, SOURCES)}
    with pytest.raises(SR.FriendError):
        SR.build_code_lock_manifest(dict(stats, extra={}), now=NOW)
    missing = dict(stats)
    missing.pop(LOCKED[-1])
    with pytest.raises(SR.FriendError):
        SR.build_code_lock_manifest(missing, now=NOW)


def test_path_resolution_error_is_content_free(root, monkeypatch):
    setup(root)
    original = Path.resolve
    target = root / LOCKED[0]

    def fail(path, *args, **kwargs):
        if path == target:
            raise RuntimeError('PRIVATE-PATH-CONTENT')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', fail)
    code, out, err = go(root)
    assert code == 2 and out == []
    assert err == ['ERROR: input or output check failed; files_written=0']
    assert not (root / SR.CODE_LOCK_REL).exists()


def test_source_redirection_refused_before_read(root, monkeypatch):
    setup(root)
    original = Path.resolve
    target = root / LOCKED[0]

    def redirected(path, *args, **kwargs):
        if path == target:
            return original(root / 'data/forbidden.py')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', redirected)
    assert go(root)[0] == 2
    assert not (root / SR.CODE_LOCK_REL).exists()


def test_exclusive_create_race_preserves_other_writer(root, monkeypatch):
    setup(root)
    original = Path.open

    def race(path, mode='r', *args, **kwargs):
        if path == root / SR.CODE_LOCK_REL and mode == 'x':
            with original(path, 'xb') as stream:
                stream.write(b'other writer\n')
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', race)
    code, out, err = go(root)
    assert code == 3 and out == [] and 'write-once' in err[0]
    assert (root / SR.CODE_LOCK_REL).read_bytes() == b'other writer\n'


def test_output_resolution_outside_root_is_refused(root, monkeypatch):
    setup(root)
    destination = root / SR.CODE_LOCK_REL
    original = Path.resolve

    def redirected(path, *args, **kwargs):
        if path == destination:
            return original(root.parent / 'outside-lock.json')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', redirected)
    assert go(root)[0] == 2
    assert not destination.exists()
    assert not (root.parent / 'outside-lock.json').exists()


def test_real_manifest_matches_only_the_four_approved_modules():
    path = REPO / SR.CODE_LOCK_REL
    if not path.is_file():
        pytest.skip('code lock pending explicit approval')
    doc = json.loads(path.read_bytes())
    assert set(doc) == {'format', 'created_at', 'python_version', 'files'}
    assert doc['format'] == SR.CODE_LOCK_FORMAT
    assert list(doc['files']) == list(LOCKED)
    assert isinstance(doc['python_version'], str) and doc['python_version']
    assert datetime.fromisoformat(doc['created_at']).utcoffset().total_seconds() == 0
    # Never follow manifest-supplied paths: iterate the approved constants after checking the list.
    for relative in LOCKED:
        assert doc['files'][relative] == independent_stats((REPO / relative).read_bytes())
