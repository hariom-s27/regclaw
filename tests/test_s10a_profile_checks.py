"""S10 profile contamination checks, seeded split and write-once seal, on FAKE rows only."""
import json

import pytest
import yaml

from eval.s10 import profile_checks as PC
from eval.s10.formats import STRATA, validate_profile_set
from eval.s10.split_guard import HoldoutRefused, verify_holdout

REFS = ['Total knee implant', 'Skin electrode', 'Knee X', 'test']
PROMPT = 'Classify the made-up gadget for unit seven. Use polymer where it helps.'


def row(i, stratum='S1', name=None, comment='Made-up row for a unit test.'):
    entry = dict(id=f'FAKE-{i:03d}', stratum=stratum, split='dev', contact_category='surface/intact_skin',
                 duration='A', flags=['attachment_g_material'],
                 profile=dict(device_name=name or f'Zorblat widget number {i}', components=[dict(
                     name=f'FAKE part {i}', contact_nature='surface', contact_site='intact_skin',
                     exposure=dict(pattern='single', hours_per_use=2),
                     materials=[dict(name='FAKE polymer', material_class='polymer')], sterilization='non_sterile')]))
    if comment is not None:
        entry['comment'] = comment
    return entry


def fake_doc():
    strata = [s for s in STRATA for _ in range(6 if s == 'trap' else 3)]
    return dict(format='s10-eval-profiles.v1', split='dev', profiles=[row(i + 1, s) for i, s in enumerate(strata)])


def checks(rows, prompt=PROMPT):
    return PC.check_rows(rows, REFS, prompt)[0]


@pytest.fixture
def draft(tmp_path):
    path = tmp_path / 'gold' / 'profiles_draft.yaml'
    path.parent.mkdir()
    path.write_text(yaml.safe_dump(fake_doc(), sort_keys=False), encoding='utf-8')
    return path


def cli(draft, tmp_path, *extra, lines=None):
    return PC.main([str(draft), *extra], references=REFS, prompt=PROMPT, out=(lines if lines is not None else []).append)


def write_args(tmp_path):
    return ['--write', '--seal', str(tmp_path / 'gold' / 'holdout_manifest.json'),
            '--manifest-out', str(tmp_path / 'manifests' / 'profiles_v1.json')]


# ---- check (a): names ----
@pytest.mark.parametrize('name', ['skin electrode', 'Skin-Electrode!', 'SKIN  ELECTRODE', 'Skin electrodes',
                                  'Knee X', 'Knee X revision system', 'Cemented total knee implant'])
def test_equal_or_near_equal_names_fail_check_a(name):
    assert checks([row(1, name=name)])['a'] == ['FAKE-001']


@pytest.mark.parametrize('name', ['Zorblat widget', 'Shoulder spacer', 'Blood sugar test strip', 'Kneeling pad'])
def test_other_names_pass_check_a(name):
    assert checks([row(1, name=name)])['a'] == []          # a one-word reference ("test") only matches as a whole name


def test_one_word_reference_still_matches_the_whole_name():
    assert checks([row(1, name='Test')])['a'] == ['FAKE-001']


# ---- check (b): ISO part patterns and endpoint words ----
@pytest.mark.parametrize('text', ['needs ISO 10993 review', 'see 10993-5', 'Cytotoxicity screened', 'skin Irritation',
                                  'sensitization', 'genotoxic', 'carcinogenicity', 'after implantation',
                                  'hemocompatibility', 'non-pyrogenic', 'systemic toxicity'])
def test_banned_words_fail_check_b_in_name_or_comment(text):
    assert checks([row(1, comment=text)])['b'] == ['FAKE-001']
    assert checks([row(2, name=f'Zorblat {text}')])['b'] == ['FAKE-002']


def test_clean_text_passes_check_b():
    assert checks([row(1, comment='Implanted for about one year.')])['b'] == []


# ---- check (c): profile text inside the PROMPT ----
def test_device_name_or_comment_inside_the_prompt_fails_check_c():
    assert checks([row(1, name='Made-up gadget')])['c'] == ['FAKE-001']
    assert checks([row(2, comment='UNIT seven')])['c'] == ['FAKE-002']
    assert checks([row(3)])['c'] == []


@pytest.mark.parametrize('comment', [None, '', '   ', '...', 'Use'])       # missing, empty, blank, punctuation, < 4 chars
def test_empty_or_tiny_comment_never_fails_check_c(comment):
    assert checks([row(1, comment=comment)]) == dict(a=[], b=[], c=[])


def test_empty_device_name_is_not_a_substring_hit():
    assert checks([row(1, name='  ')], prompt='')['c'] == [] and checks([row(1)], prompt='')['c'] == []


def test_component_and_material_names_are_info_only():
    bad = row(1)
    bad['profile']['components'][0]['name'] = 'made-up gadget'
    bad['profile']['components'][0]['materials'][0]['name'] = 'Polymer'
    fails, info = PC.check_rows([bad, row(2)], REFS, PROMPT)
    assert fails == dict(a=[], b=[], c=[])
    assert info == dict(component_names_in_prompt=1, material_names_in_prompt=1)


def test_reference_names_cover_demo_smoke_and_test_fakes():
    names = PC.reference_names()
    for expected in ('Total knee implant', 'Skin electrode', 'Central venous catheter', 'Contact lens', 'Knee X',
                     'FAKE-1 test patch (not a real device)', 'FAKE device (not a real device)'):
        assert expected in names


# ---- split ----
def test_split_is_seeded_and_has_the_expected_counts():
    doc = fake_doc()
    dev = PC.split_ids(doc['profiles'])
    assert dev == PC.split_ids(list(reversed(doc['profiles'])))          # row order does not matter
    assert dev != PC.split_ids(doc['profiles'], seed='other-seed')
    dev_doc, holdout_doc = PC.split_docs(doc, dev)
    assert PC.count_table(dict(dev=dev_doc, holdout=holdout_doc)) == PC.EXPECTED
    assert len(dev_doc['profiles']) == 10 and len(holdout_doc['profiles']) == 20
    assert {r['split'] for r in dev_doc['profiles']} == {'dev'} and {r['split'] for r in holdout_doc['profiles']} == {'holdout'}
    assert {r['split'] for r in doc['profiles']} == {'dev'}              # the input document is not changed
    validate_profile_set(dev_doc)
    validate_profile_set(holdout_doc)
    ids = [r['id'] for r in holdout_doc['profiles']]
    assert ids == sorted(ids) and not set(ids) & dev


def test_split_refuses_a_stratum_that_is_too_small():
    with pytest.raises(PC.ProfileCheckError, match='S1'):
        PC.split_ids([row(1, 'S1')])


# ---- CLI ----
def test_dry_run_writes_nothing_and_prints_counts_only(draft, tmp_path):
    lines = []
    assert cli(draft, tmp_path, '--dry-run', lines=lines) == 0
    assert sorted(p.name for p in draft.parent.iterdir()) == ['profiles_draft.yaml']
    text = '\n'.join(lines)
    assert 'check (a): pass 30 / fail 0' in text and 'check (c): pass 30 / fail 0' in text and 'Zorblat' not in text


def test_write_seals_once_and_the_manifest_has_no_content(draft, tmp_path):
    lines = []
    assert cli(draft, tmp_path, *write_args(tmp_path), lines=lines) == 0
    gold = draft.parent
    assert sorted(p.name for p in gold.iterdir()) == ['dev_profiles.yaml', 'holdout_manifest.json',
                                                      'holdout_profiles.yaml', 'profiles_draft.yaml']
    assert b'\r' not in (gold / 'holdout_profiles.yaml').read_bytes()
    verify_holdout([gold / 'holdout_profiles.yaml', draft], gold / 'holdout_manifest.json')
    with pytest.raises(HoldoutRefused):
        verify_holdout([gold / 'dev_profiles.yaml'], gold / 'holdout_manifest.json')     # dev is not sealed
    raw = (tmp_path / 'manifests' / 'profiles_v1.json').read_text(encoding='utf-8')
    manifest = json.loads(raw)
    assert manifest['seed'] == 'S10-split-v1' and manifest['counts'] == PC.EXPECTED and manifest['python']
    assert {k: (v['rows'], v['sealed']) for k, v in manifest['files'].items()} == {
        'dev_profiles.yaml': (10, False), 'holdout_profiles.yaml': (20, True), 'profiles_draft.yaml': (30, True)}
    assert all(len(v['sha256_lf']) == 64 for v in manifest['files'].values())
    assert 'Zorblat' not in raw and 'FAKE-0' not in raw and 'Zorblat' not in '\n'.join(lines)
    # second run: refused before anything is read or written
    before = {p.name: p.read_bytes() for p in gold.iterdir()}
    assert cli(draft, tmp_path, *write_args(tmp_path)) == 4
    assert {p.name: p.read_bytes() for p in gold.iterdir()} == before


@pytest.mark.parametrize('existing', ['dev_profiles.yaml', 'holdout_profiles.yaml', 'holdout_manifest.json'])
def test_write_refuses_to_start_if_any_output_exists(draft, tmp_path, existing):
    (draft.parent / existing).write_text('old', encoding='utf-8')
    lines = []
    assert cli(draft, tmp_path, *write_args(tmp_path), lines=lines) == 4
    assert (draft.parent / existing).read_text(encoding='utf-8') == 'old' and 'write-once' in lines[0]
    assert not (tmp_path / 'manifests').exists()


def test_failed_check_stops_before_any_write(tmp_path):
    doc = fake_doc()
    doc['profiles'][4]['profile']['device_name'] = 'Skin electrode'
    path = tmp_path / 'profiles_draft.yaml'
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding='utf-8')
    lines = []
    assert cli(path, tmp_path, *write_args(tmp_path), lines=lines) == 2
    assert [p.name for p in tmp_path.iterdir()] == ['profiles_draft.yaml']
    assert any('check (a): pass 29 / fail 1 | ids: FAKE-005' in line for line in lines)
    assert 'Skin electrode' not in '\n'.join(lines)


def test_error_after_writing_does_not_seal_or_delete(draft, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise ValueError('secret detail that must not be printed')
    monkeypatch.setattr(PC, 'validate_profile_set', lambda doc, data=None: boom() if doc['split'] == 'holdout' else {
        e['id']: e for e in doc['profiles']})
    lines = []
    assert cli(draft, tmp_path, *write_args(tmp_path), lines=lines) == 3
    names = sorted(p.name for p in draft.parent.iterdir())
    assert names == ['dev_profiles.yaml', 'holdout_profiles.yaml', 'profiles_draft.yaml']      # kept, no seal manifest
    assert not (tmp_path / 'manifests').exists()
    assert 'ValueError' in lines[-1] and 'secret detail' not in lines[-1]
