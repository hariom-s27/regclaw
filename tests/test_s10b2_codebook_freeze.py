"""S10b2 codebook freeze (S9), on a FAKE repo root in the pytest temp folder only."""
import json

import pytest

from eval.s10 import codebook_freeze as CF
from eval.s10 import judge_kit as JK
from eval.s10 import label_tool as LT
from standards.ruleset import normalised_sha

LEAK = 'LEAKWORD'
NOW = '2026-11-16T00:00:00+00:00'


def make_root(tmp_path):
    root = tmp_path / 'root'
    for k, rel in enumerate(CF.FROZEN):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        eol = '\r\n' if k % 2 == 0 else '\n'                      # mix CRLF and LF files
        p.write_bytes(f'# fake {k} {LEAK}{eol}line two{eol}'.encode('utf-8'))
    return root


def go(root, *args):
    out, err = [], []
    code = CF.main(list(args), root=root, now=NOW, out=out.append, err=err.append)
    text = '\n'.join(out + err)
    assert LEAK not in text and 'line two' not in text
    return code, out, err


def manifest(root):
    return json.loads((root / CF.MANIFEST_REL).read_text(encoding='utf-8'))


def test_written_shape(tmp_path):
    root = make_root(tmp_path)
    code, out, err = go(root)
    assert code == 0 and not err and len(out) == 1 + len(CF.FROZEN)
    m = manifest(root)
    assert list(m) == ['format', 'version', 'template_version', 'files', 'created_at']
    assert m['format'] == 's10-codebook-manifest.v1' and m['version'] == 'v1.0' and m['created_at'] == NOW
    assert m['template_version'] == JK.TEMPLATE_VERSION == 's10-judge-prompt.v1'
    assert list(m['files']) == list(CF.FROZEN) and len(CF.FROZEN) == 6
    for rel, info in m['files'].items():
        raw = (root / rel).read_bytes()
        assert info['sha256_lf'] == normalised_sha(raw) and info['lines'] == 2
        assert info['bytes_lf'] == len(raw.replace(b'\r\n', b'\n'))
    assert LEAK not in (root / CF.MANIFEST_REL).read_text(encoding='utf-8')


def test_frozen_list_exact():
    assert CF.FROZEN == ('eval/s10/codebook/CODEBOOK_v1.0.md', 'eval/s10/codebook/JUDGE_INSTRUCTIONS_v1.0.md',
                         'eval/s10/codebook/judge_output.schema.json', 'eval/s10/judge_kit.py',
                         'eval/s10/judge_config.yaml', 'specs/S10_PREREG_ADDENDUM_A1.md')
    assert CF.MANIFEST_REL == JK.CODEBOOK_V1_MANIFEST_REL == LT.CODEBOOK_V1_MANIFEST_REL
    assert CF.FROZEN[0] == LT.CODEBOOK_REL


def test_readers_accept_the_manifest(tmp_path):
    root = make_root(tmp_path)
    assert go(root)[0] == 0
    assert JK.check_eval_manifest(root)['template_version'] == JK.TEMPLATE_VERSION
    LT.check_codebook_v1((root / LT.CODEBOOK_REL).read_bytes(), root / CF.MANIFEST_REL)
    (root / 'eval/s10/judge_config.yaml').write_text('changed: true\n', encoding='utf-8')
    with pytest.raises(JK.KitRefused, match='sha differs'):
        JK.check_eval_manifest(root)
    (root / LT.CODEBOOK_REL).write_text('# changed\n', encoding='utf-8')
    with pytest.raises(PermissionError, match='sha differs'):
        LT.check_codebook_v1((root / LT.CODEBOOK_REL).read_bytes(), root / CF.MANIFEST_REL)


def test_write_once(tmp_path):
    root = make_root(tmp_path)
    assert go(root)[0] == 0
    before = (root / CF.MANIFEST_REL).read_bytes()
    code, out, err = go(root)
    assert code == 3 and not out and 'write-once' in err[0]
    assert (root / CF.MANIFEST_REL).read_bytes() == before


@pytest.mark.parametrize('rel', CF.FROZEN)
def test_missing_file_refused(tmp_path, rel):
    root = make_root(tmp_path)
    (root / rel).unlink()
    code, out, err = go(root)
    assert code == 2 and not out and err == [f'ERROR: file(s) to freeze not found; nothing written: {rel}']
    assert not (root / CF.MANIFEST_REL).exists()


def test_expected_sha(tmp_path):
    root = make_root(tmp_path)
    cb, cfg = CF.FROZEN[0], CF.FROZEN[4]
    good_cb = normalised_sha((root / cb).read_bytes())
    code, out, err = go(root, '--expected-sha', f'{cb}={"0" * 64}', '--expected-sha', f'{cfg}={"1" * 64}')
    assert code == 1 and not out and not (root / CF.MANIFEST_REL).exists()
    assert err[0] == 'ERROR: sha256 (LF) mismatch; nothing written' and len(err) == 3
    assert err[1] == f'  {cb}: expected {"0" * 64} computed {good_cb}'
    code, out, err = go(root, '--expected-sha', f'{cb}={good_cb.upper()}')
    assert code == 0 and manifest(root)['files'][cb]['sha256_lf'] == good_cb


@pytest.mark.parametrize('arg', ['nonsense', 'eval/s10/judge_output.py=' + 'a' * 64,
                                 'eval/s10/judge_runner.py=' + 'a' * 64, CF.FROZEN[0] + '=abc',
                                 CF.FROZEN[0] + '=' + 'g' * 64])
def test_bad_expected_arg(tmp_path, arg):
    root = make_root(tmp_path)
    code, out, err = go(root, '--expected-sha', arg)
    assert code == 2 and not out and err[0].startswith('ERROR: bad --expected-sha')
    assert not (root / CF.MANIFEST_REL).exists()


def test_expected_twice_refused(tmp_path):
    root = make_root(tmp_path)
    pair = f'{CF.FROZEN[1]}={"a" * 64}'
    code, _, err = go(root, '--expected-sha', pair, '--expected-sha', pair)
    assert code == 2 and 'given twice' in err[0]
