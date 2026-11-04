"""Holdout seal (agreed item 5): refused without --final-holdout or with a missing/mismatched manifest."""
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
    p.write_text(yaml.safe_dump(prof, sort_keys=False), encoding='utf-8')
    g.write_text(yaml.safe_dump(gold, sort_keys=False), encoding='utf-8')
    if seal:
        SG.seal_holdout([p, g], now='2026-11-04T00:00:00+00:00')
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
        f.write_bytes(f.read_bytes().replace(b'\n', b'\r\n'))
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
