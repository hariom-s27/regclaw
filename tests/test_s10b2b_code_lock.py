"""S10b2b-14: code lock script on a FAKE root, plus a read-only check of the real manifest once it exists."""
import json
from pathlib import Path

import pytest

import _netguard
from eval.s10 import code_lock as CL
from eval.s10.prereg_freeze import compute

REPO = Path(__file__).resolve().parents[1]
NOW = '2026-11-16T00:00:00+00:00'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')


def fake_root(tmp_path):
    for k, rel in enumerate(CL.LOCKED):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(f'# fake module {k}\r\nVALUE = {k}\r\n'.encode('utf-8'))
    return tmp_path


def shas(root):
    return {rel: compute((root / rel).read_bytes())['sha256_lf'] for rel in CL.LOCKED}


def args_for(sha_map):
    out = []
    for rel, sha in sha_map.items():
        out += ['--expected-sha', f'{rel}={sha}']
    return out


def run(root, argv):
    lines, errs = [], []
    code = CL.main(argv, root=root, now=NOW, out=lines.append, err=errs.append)
    return code, lines, errs


def test_locked_list_is_exactly_the_nine_files():
    assert CL.LOCKED == ('eval/s10/judge_output.py', 'eval/s10/judge_runner.py', 'eval/s10/metrics.py',
                         'eval/s10/agreement.py', 'eval/s10/merge.py', 'eval/s10/gate.py', 'eval/s10/draws.py',
                         'eval/s10/sheets.py', 'eval/s10/diagnostics.py')
    assert 'eval/s10/judge_kit.py' not in CL.LOCKED and 'eval/s10/code_lock.py' not in CL.LOCKED


def test_show_is_read_only(tmp_path):
    root = fake_root(tmp_path)
    code, lines, _ = run(root, ['--show'])
    assert code == 0 and len(lines) == 9
    assert lines[0] == (f'{CL.LOCKED[0]}: sha256_lf={shas(root)[CL.LOCKED[0]]} '
                        f'bytes_lf={compute((root / CL.LOCKED[0]).read_bytes())["bytes_lf"]} lines=2')
    assert not (root / CL.MANIFEST_REL).exists()


def test_write_once_with_all_expected(tmp_path):
    root = fake_root(tmp_path)
    code, lines, _ = run(root, args_for(shas(root)))
    assert code == 0 and lines[0].startswith(f'written {CL.MANIFEST_REL} (9 files')
    raw = (root / CL.MANIFEST_REL).read_bytes()
    assert b'\r' not in raw
    m = json.loads(raw.decode('utf-8'))
    assert set(m) == {'format', 'created_at', 'python_version', 'files'}
    assert m['format'] == 's10b2b-code-lock.v1' and m['created_at'] == NOW
    assert list(m['files']) == list(CL.LOCKED)
    for rel in CL.LOCKED:
        assert m['files'][rel] == compute((root / rel).read_bytes())
    code, _, errs = run(root, args_for(shas(root)))
    assert code == 3 and errs[0].startswith('REFUSED') and (root / CL.MANIFEST_REL).read_bytes() == raw


def test_mismatch_writes_nothing(tmp_path):
    root = fake_root(tmp_path)
    s = shas(root)
    s[CL.LOCKED[4]] = '0' * 64
    code, _, errs = run(root, args_for(s))
    assert code == 1 and 'nothing written' in errs[0] and not (root / CL.MANIFEST_REL).exists()


@pytest.mark.parametrize('bad', ['missing_one', 'extra', 'duplicate', 'syntax', 'none'])
def test_bad_expected_writes_nothing(tmp_path, bad):
    root = fake_root(tmp_path)
    s = shas(root)
    argv = args_for(s)
    if bad == 'missing_one':
        argv = argv[:-2]
    elif bad == 'extra':
        argv += ['--expected-sha', f'eval/s10/judge_kit.py={"a" * 64}']
    elif bad == 'duplicate':
        argv += argv[:2]
    elif bad == 'syntax':
        argv[1] = argv[1].replace('=', ':')
    else:
        argv = []
    if bad == 'none':
        with pytest.raises(SystemExit):
            run(root, argv)
    else:
        assert run(root, argv)[0] == 2
    assert not (root / CL.MANIFEST_REL).exists()


def test_missing_locked_file(tmp_path):
    root = fake_root(tmp_path)
    s = shas(root)
    (root / CL.LOCKED[-1]).unlink()
    code, _, errs = run(root, args_for(s))
    assert code == 2 and CL.LOCKED[-1] in errs[0] and not (root / CL.MANIFEST_REL).exists()


def test_crlf_and_lf_give_the_same_sha(tmp_path):
    root = fake_root(tmp_path)
    before = shas(root)
    for rel in CL.LOCKED:
        p = root / rel
        p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))
    assert shas(root) == before


# ---- read-only check of the real manifest (enforced once it exists) -------------------------------------
def test_real_manifest_matches_files():
    path = REPO / CL.MANIFEST_REL
    if not path.is_file():
        pytest.skip('s10b2b_code_lock.json not written yet (Part C lock pending)')
    m = json.loads(path.read_text(encoding='utf-8'))
    assert set(m) == {'format', 'created_at', 'python_version', 'files'}
    assert m['format'] == 's10b2b-code-lock.v1'
    assert list(m['files']) == list(CL.LOCKED)
    for rel, info in m['files'].items():
        assert compute((REPO / rel).read_bytes()) == info, f'{rel} changed after the code lock'
