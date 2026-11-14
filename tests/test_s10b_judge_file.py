"""S10b-05/06 judge file, seal and ledger, on FAKE profile files only (no real files)."""
import json
from pathlib import Path

import pytest
import yaml

from eval.s10 import judge_file as JF
from eval.s10.audit_list import FAMILY_ITEMS
from eval.s10.split_guard import verify_holdout
from standards.ruleset import normalised_sha

PARTS = [i for f in ('F2', 'F1', 'F3', 'F4') for i in FAMILY_ITEMS[f]]   # 20 fake-ordered parts
NOW = '2026-11-14T00:00:00+00:00'


def fake_row(pid, split, knee=False, evidence=False):
    comp = dict(name='FAKE part', contact_nature='surface', contact_site='intact_skin',
                exposure=dict(pattern='single', hours_per_use=2),
                materials=[dict(name='FAKE polymer', material_class='polymer')], sterilization='non_sterile')
    if evidence:
        comp['existing_evidence'] = dict(equivalent_marketed_device=True)
    profile = dict(device_name=f'Fake eval gadget no {int(pid[2:])}', components=[comp])
    if knee:
        profile['device_specific_standards'] = ['fda-knee']
    return dict(id=pid, stratum='S1', split=split, contact_category='surface/intact_skin', duration='A',
                flags=['attachment_g_material'], comment='made-up comment', profile=profile)


def practice_doc(names=None):
    names = names or ['Quixotic band thing', 'Zephyr tube item', 'Mauve splint object', 'Ochre mesh piece']
    return dict(format='s10-practice-profiles.v1', split='practice', profiles=[
        dict(id=f'PR-{i}', split='practice', contact_category='surface/intact_skin', duration='A', flags=[],
             profile=dict(device_name=n, components=[])) for i, n in enumerate(names, start=1)])


def setup(root: Path, practice_names=None, tamper_holdout=False):
    def put(rel, text):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
        return normalised_sha(text.encode('utf-8'))
    dev = [fake_row(f'P-{i:03d}', 'dev', knee=(i == 1)) for i in range(1, 11)]
    ho = [fake_row(f'P-{i:03d}', 'holdout', knee=(i in (12, 13)), evidence=(i == 14)) for i in range(11, 31)]
    dev_sha = put(JF.DEV_REL, yaml.safe_dump(dict(format='s10-eval-profiles.v1', split='dev', profiles=dev)))
    ho_text = yaml.safe_dump(dict(format='s10-eval-profiles.v1', split='holdout', profiles=ho))
    ho_sha = put(JF.HOLDOUT_REL, ho_text)
    if tamper_holdout:
        put(JF.HOLDOUT_REL, ho_text + '# changed\n')
    pr_sha = put(JF.PRACTICE_REL, yaml.safe_dump(practice_doc(practice_names)))
    put(JF.PARTS_REL, yaml.safe_dump(dict(version=1, parts=[dict(part=p) for p in PARTS])))
    put(JF.PROFILES_MANIFEST_REL, json.dumps(dict(files={
        'dev_profiles.yaml': dict(sha256_lf=dev_sha), 'holdout_profiles.yaml': dict(sha256_lf=ho_sha)})))
    return pr_sha


@pytest.fixture
def reads(monkeypatch):
    """Count every read of the fake holdout file (helper + Path read methods)."""
    count = {'helper': 0, 'path': 0}
    real_helper, real_bytes, real_text = JF._read_holdout_once, Path.read_bytes, Path.read_text

    def helper(path):
        count['helper'] += 1
        return real_helper(path)

    def rb(self, *a, **k):
        count['path'] += self.name == 'holdout_profiles.yaml'
        return real_bytes(self, *a, **k)

    def rt(self, *a, **k):
        count['path'] += self.name == 'holdout_profiles.yaml'
        return real_text(self, *a, **k)
    monkeypatch.setattr(JF, '_read_holdout_once', helper)
    monkeypatch.setattr(Path, 'read_bytes', rb)
    monkeypatch.setattr(Path, 'read_text', rt)
    return count


def written(root):
    return sorted(rel for rel in JF.OUTPUTS if (root / rel).exists())


def test_happy_path(tmp_path, reads):
    pr_sha = setup(tmp_path)
    lines = []
    assert JF.run(tmp_path, now=NOW, practice_sha=pr_sha, out=lines.append) == 0
    assert reads == {'helper': 1, 'path': 1}
    assert written(tmp_path) == sorted(JF.OUTPUTS)
    text = (tmp_path / JF.JUDGE_REL).read_text(encoding='utf-8')
    doc = yaml.safe_load(text)
    entries = doc['entries']
    assert [e['j_id'] for e in entries] == [f'J-{i:03d}' for i in range(1, 31)]
    for e in entries:
        assert sorted(e) == ['contact_category', 'duration', 'items', 'j_id', 'profile']
        assert e['items'][:20] == PARTS and len(e['items']) in (20, 21)
    assert sum(len(e['items']) == 21 for e in entries) == 3
    for word in ('existing_evidence', 'stratum', 'split', 'comment', 'flags', 'P-0', 'derived'):
        assert word not in text
    assert 'id:' not in text.replace('j_id:', '')
    # map sealed; ledger complete; nothing printed from profiles
    verify_holdout([tmp_path / JF.MAP_REL], tmp_path / JF.MAP_MANIFEST_REL)
    ledger = json.loads((tmp_path / JF.LEDGER_REL).read_text(encoding='utf-8'))
    assert ledger['outcome'] == 'written' and ledger['reason'] == JF.REASON
    assert ledger['timestamp_utc'] == NOW
    ho = [f for f in ledger['files_read'] if f['path'] == JF.HOLDOUT_REL][0]
    assert ho['reads'] == 1 and ho['sha_check'] == 'match'
    assert [o['path'] for o in ledger['outputs_written']] == [JF.JUDGE_REL, JF.MAP_REL, JF.MAP_MANIFEST_REL]
    assert ledger['counts']['duplicate_hits'] == 0 and ledger['counts']['entries'] == 30
    assert 'holdout sha: match' in lines
    assert not any('gadget' in l or 'P-0' in l for l in lines)


def test_seeded_assignment_is_deterministic(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    for root in (a, b):
        JF.run(root, now=NOW, practice_sha=setup(root), out=lambda s: None)
    ja = json.loads((a / JF.MAP_REL).read_text(encoding='utf-8'))['map']
    jb = json.loads((b / JF.MAP_REL).read_text(encoding='utf-8'))['map']
    assert ja == jb == JF.assign_jids([f'P-{i:03d}' for i in range(1, 31)])
    assert sorted(ja.values()) == [f'P-{i:03d}' for i in range(1, 31)]
    assert list(ja.values()) != sorted(ja.values())


def test_duplicate_name_writes_ledger_only(tmp_path, reads):
    names = ['Fake eval gadget no 17','Zephyr tube item', 'Mauve splint object', 'Ochre mesh piece']
    pr_sha = setup(tmp_path, practice_names=names)
    lines = []
    assert JF.run(tmp_path, now=NOW, practice_sha=pr_sha, out=lines.append) == 1
    assert written(tmp_path) == [JF.LEDGER_REL]
    ledger = json.loads((tmp_path / JF.LEDGER_REL).read_text(encoding='utf-8'))
    assert ledger['outcome'] == 'aborted: duplicate names' and ledger['counts']['duplicate_hits'] >= 1
    assert reads['helper'] == 1
    assert not any('gadget' in l for l in lines)


def test_holdout_sha_mismatch_writes_ledger_only(tmp_path, reads, monkeypatch):
    pr_sha = setup(tmp_path, tamper_holdout=True)
    monkeypatch.setattr(JF, 'parse_eval', lambda raw, split: (_ for _ in ()).throw(AssertionError('parsed'))
                        if split == 'holdout' else None)
    lines = []
    assert JF.run(tmp_path, now=NOW, practice_sha=pr_sha, out=lines.append) == 1
    assert written(tmp_path) == [JF.LEDGER_REL]
    assert 'holdout sha: mismatch' in lines
    ledger = json.loads((tmp_path / JF.LEDGER_REL).read_text(encoding='utf-8'))
    assert ledger['outcome'].startswith('aborted: holdout sha mismatch')
    assert reads['helper'] == 1


def test_practice_sha_must_match(tmp_path, reads):
    setup(tmp_path)
    assert JF.run(tmp_path, now=NOW, practice_sha='0' * 64, out=lambda s: None) == 1
    assert written(tmp_path) == [] and reads == {'helper': 0, 'path': 0}


def test_existing_output_refused_before_any_read(tmp_path, reads):
    pr_sha = setup(tmp_path)
    (tmp_path / JF.LEDGER_REL).write_text('{}', encoding='utf-8')
    assert JF.run(tmp_path, now=NOW, practice_sha=pr_sha, out=lambda s: None) == 3
    assert written(tmp_path) == [JF.LEDGER_REL] and reads == {'helper': 0, 'path': 0}


def test_judge_profile_drops_existing_evidence_only():
    prof = fake_row('P-999', 'dev', evidence=True)['profile']
    out = JF.judge_profile(prof)
    assert 'existing_evidence' not in out['components'][0]
    assert 'existing_evidence' in prof['components'][0]          # input not mutated
    assert out['device_name'] == prof['device_name']
