"""Holdout seal (agreed item 5): refused without --final-holdout or with a missing/mismatched manifest."""
import builtins
import io
import json
from pathlib import Path

import pytest
import yaml

import _netguard
from eval.s10 import split_guard as SG
from eval.s10.runner import main

FIX = Path(__file__).parent / 'fixtures' / 's10_eval'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')


def windows_text_open(real):
    """Windows text mode on any OS: a text-mode write with newline=None turns '\n' into '\r\n' (os.linesep on
    Windows). Reads, binary writes and newline='' writes are unchanged, as on Windows."""
    def opened(file, mode='r', buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
        if 'b' not in mode and any(c in mode for c in 'wax+') and newline is None:
            newline = '\r\n'
        return real(file, mode, buffering, encoding, errors, newline, closefd, opener)
    return opened


@pytest.fixture(autouse=True, params=['native', 'windows_text_mode'])
def line_endings(request, monkeypatch):
    """Every test here runs twice: with this OS's text mode, and with Windows text mode simulated (the laptop:
    core.autocrlf=true, CRLF text writes). Path.write_text / Path.open reach io.open; open() is builtins.open."""
    if request.param == 'windows_text_mode':
        monkeypatch.setattr(io, 'open', windows_text_open(io.open))
        monkeypatch.setattr(builtins, 'open', windows_text_open(builtins.open))
    return request.param


def to_crlf(raw: bytes) -> bytes:
    """CRLF copy built from LF-normalised bytes: correct whether the input is already CRLF (Windows) or LF."""
    return raw.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')


def make_split(tmp_path, split='holdout', seal=True):
    """Fake holdout files built in tmp_path from the FAKE fixtures (never real data)."""
    prof = yaml.safe_load((FIX / 'fake_profiles.yaml').read_text(encoding='utf-8'))
    gold = yaml.safe_load((FIX / 'fake_gold.yaml').read_text(encoding='utf-8'))
    prof['split'] = gold['split'] = split
    for e in prof['profiles']:
        e['split'] = split
    folder = tmp_path / split
    folder.mkdir()
    p, g = folder / f'{split}_profiles.yaml', folder / f'{split}_gold.yaml'
    # hashed by the seal: explicit LF bytes on every OS
    p.write_bytes(yaml.safe_dump(prof, sort_keys=False).encode('utf-8'))
    g.write_bytes(yaml.safe_dump(gold, sort_keys=False).encode('utf-8'))
    if seal:
        SG.seal_holdout([p, g], p.parent / SG.MANIFEST_NAME, now='2026-11-04T00:00:00+00:00')
    return p, g


def test_dev_needs_no_flag():
    split, profiles, gold = SG.load_eval_files(FIX / 'fake_profiles.yaml', FIX / 'fake_gold.yaml')
    assert split == 'dev' and len(profiles) == 3 and len(gold) == 9


def test_holdout_refused_without_flag(tmp_path):
    p, g = make_split(tmp_path)
    with pytest.raises(SG.HoldoutRefused, match='sealed'):
        SG.load_eval_files(p, g)
    with pytest.raises(SG.HoldoutRefused, match='sealed'):
        SG.load_eval_files(p)                    # running (no gold) is gated too


def test_holdout_accepted_with_flag_and_valid_manifest(tmp_path):
    p, g = make_split(tmp_path)
    split, profiles, gold = SG.load_eval_files(p, g, final_holdout=True)
    assert split == 'holdout' and len(gold) == 9


def test_holdout_refused_when_manifest_missing(tmp_path):
    p, g = make_split(tmp_path, seal=False)
    with pytest.raises(SG.HoldoutRefused, match='manifest missing'):
        SG.load_eval_files(p, g, final_holdout=True)


def test_holdout_refused_when_a_byte_changes_after_sealing(tmp_path):
    p, g = make_split(tmp_path)
    g.write_text(g.read_text(encoding='utf-8').replace('not_applicable', 'recommended', 1), encoding='utf-8')
    with pytest.raises(SG.HoldoutRefused, match='does not match'):
        SG.load_eval_files(p, g, final_holdout=True)


def test_holdout_refused_when_file_not_in_manifest(tmp_path):
    p, g = make_split(tmp_path)
    manifest = p.parent / SG.MANIFEST_NAME
    data = json.loads(manifest.read_text())
    del data['files'][g.name]
    manifest.write_text(json.dumps(data))
    with pytest.raises(SG.HoldoutRefused, match='not listed'):
        SG.load_eval_files(p, g, final_holdout=True)


def test_holdout_refused_on_bad_manifest_format(tmp_path):
    p, g = make_split(tmp_path)
    (p.parent / SG.MANIFEST_NAME).write_text('{"format": "other", "files": {}}')
    with pytest.raises(SG.HoldoutRefused, match='wrong format'):
        SG.load_eval_files(p, g, final_holdout=True)


def test_crlf_copy_of_sealed_file_still_verifies(tmp_path):
    p, g = make_split(tmp_path)
    for f in (p, g):
        crlf = to_crlf(f.read_bytes())
        assert b'\r\n' in crlf and b'\r\r\n' not in crlf
        f.write_bytes(crlf)
    assert SG.load_eval_files(p, g, final_holdout=True)[0] == 'holdout'


def test_holdout_row_inside_dev_file_refused(tmp_path):
    prof = yaml.safe_load((FIX / 'fake_profiles.yaml').read_text(encoding='utf-8'))
    prof['profiles'][2]['split'] = 'holdout'
    path = tmp_path / 'mixed.yaml'
    path.write_text(yaml.safe_dump(prof), encoding='utf-8')
    with pytest.raises(SG.HoldoutRefused, match='sealed'):
        SG.load_eval_files(path)


def test_mixed_split_refused_even_with_flag(tmp_path):
    p, _ = make_split(tmp_path)
    with pytest.raises(SG.HoldoutRefused):
        SG.load_eval_files(p, FIX / 'fake_gold.yaml', final_holdout=True)   # dev gold not in manifest


def test_flag_on_dev_files_refused(tmp_path):
    p, g = make_split(tmp_path, split='dev')
    with pytest.raises(SG.HoldoutRefused, match='not the holdout split'):
        SG.load_eval_files(p, g, final_holdout=True)


def test_cli_refuses_holdout_and_writes_nothing(tmp_path):
    p, g = make_split(tmp_path)
    out = tmp_path / 'out'
    with pytest.raises(SG.HoldoutRefused):
        main(['--profiles', str(p), '--gold', str(g), '--out', str(out), '--repeats', '1'])
    assert not out.exists()


def test_cli_scores_holdout_with_flag(tmp_path):
    p, g = make_split(tmp_path)
    out = tmp_path / 'out'
    assert main(['--profiles', str(p), '--gold', str(g), '--out', str(out), '--repeats', '1', '--n-boot', '20',
                 '--final-holdout']) == 0
    assert json.loads((out / 'summary.json').read_text())['provenance']['split'] == 'holdout'


# ---- S10-27: write-once manifests, explicit path, several manifests ----
NOW = '2026-11-13T00:00:00+00:00'


def two_manifests(tmp_path):
    """Profiles sealed first, gold sealed later into a second manifest (no file hashed twice)."""
    p, g = make_split(tmp_path, seal=False)
    m1 = SG.seal_holdout([p], p.parent / 'profiles_manifest.json', now=NOW)
    m2 = SG.seal_holdout([g], p.parent / 'gold_manifest.json', now=NOW)
    return p, g, m1, m2


def test_seal_refuses_to_overwrite_a_manifest(tmp_path):
    p, g = make_split(tmp_path)
    manifest = p.parent / SG.MANIFEST_NAME
    before = manifest.read_bytes()
    with pytest.raises(SG.HoldoutRefused, match='write-once'):
        SG.seal_holdout([p, g], manifest, now=NOW)
    assert manifest.read_bytes() == before


def test_seal_needs_an_explicit_manifest_path(tmp_path):
    p, g = make_split(tmp_path, seal=False)
    with pytest.raises(TypeError):
        SG.seal_holdout([p, g])
    assert not (p.parent / SG.MANIFEST_NAME).exists()


def test_gold_sealed_later_in_a_second_manifest(tmp_path):
    p, g, m1, m2 = two_manifests(tmp_path)
    assert json.loads(m1.read_text())['files'].keys() == {p.name}
    split, _, gold = SG.load_eval_files(p, g, final_holdout=True, manifest_path=[m1, m2])
    assert split == 'holdout' and len(gold) == 9
    with pytest.raises(SG.HoldoutRefused, match='not listed'):
        SG.load_eval_files(p, g, final_holdout=True, manifest_path=[m1])
    with pytest.raises(SG.HoldoutRefused, match='manifest missing'):
        SG.load_eval_files(p, g, final_holdout=True, manifest_path=[m1, p.parent / 'absent.json'])


def test_file_listed_in_two_manifests_is_refused(tmp_path):
    p, g, m1, m2 = two_manifests(tmp_path)
    m3 = SG.seal_holdout([p, g], p.parent / 'both_manifest.json', now=NOW)
    with pytest.raises(SG.HoldoutRefused, match='more than one'):
        SG.load_eval_files(p, g, final_holdout=True, manifest_path=[m1, m3])


@pytest.mark.parametrize('flag', ['--holdout-manifest', '--manifest'])
def test_cli_takes_repeatable_holdout_manifest(tmp_path, flag):
    p, g, m1, m2 = two_manifests(tmp_path)
    out = tmp_path / 'out'
    args = ['--profiles', str(p), '--gold', str(g), '--out', str(out), '--repeats', '1', '--n-boot', '20',
            '--final-holdout']
    with pytest.raises(SG.HoldoutRefused):
        main(args + [flag, str(m1)])                      # gold not listed in the only manifest given
    assert not out.exists()
    assert main(args + [flag, str(m1), flag, str(m2)]) == 0
    assert json.loads((out / 'summary.json').read_text())['provenance']['split'] == 'holdout'
