"""S10 eval-set file formats on the FAKE-1..3 fixtures (agreed item 4)."""
import copy
from pathlib import Path

import pytest

import _netguard
from eval.s10.formats import (FormatError, GOLD_SCHEMA, derived_flags, read_yaml, validate_gold,
                              validate_profile_set)
from standards.data_loader import load_data
from standards.profile import DeviceProfile

FIX = Path(__file__).parent / 'fixtures' / 's10_eval'
DATA = load_data()
PROFILES = read_yaml(FIX / 'fake_profiles.yaml')
GOLD = read_yaml(FIX / 'fake_gold.yaml')


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')


def test_fake_fixtures_validate_and_are_obviously_fake():
    profiles = validate_profile_set(PROFILES, DATA)
    assert sorted(profiles) == ['FAKE-1', 'FAKE-2', 'FAKE-3']
    assert all('not a real device' in e['profile']['device_name'] for e in profiles.values())
    gold = validate_gold(GOLD, profiles, DATA)
    assert len(gold) == 9 and {pid for pid, _ in gold} == set(profiles)
    assert all(g['source']['clause_id'].startswith('fda-iso10993-1:FAKE.') for g in gold.values())
    strata = {e['stratum'] for e in profiles.values()}
    assert 'trap' in strata


def test_flags_derivation_covers_every_flag():
    flags = set()
    for e in PROFILES['profiles']:
        flags |= derived_flags(DeviceProfile.model_validate(e['profile']))
    assert flags == {'ethylene_oxide', 'absorbable', 'nano', 'repeat_use', 'multi_component', 'attachment_g_material'}


def _profiles(mutate):
    doc = copy.deepcopy(PROFILES)
    mutate(doc)
    return doc


@pytest.mark.parametrize('mutate,match', [
    (lambda d: d['profiles'][0].update(stratum='S9'), 'profile set'),
    (lambda d: d['profiles'][0].update(split='test'), 'profile set'),
    (lambda d: d['profiles'][0].update(split='holdout'), 'differs from file split'),
    (lambda d: d['profiles'][0].update(extra='x'), 'profile set'),
    (lambda d: d['profiles'][1].update(id='FAKE-1'), 'duplicate profile id'),
    (lambda d: d['profiles'][0].update(duration='C'), 'engine-derived'),
    (lambda d: d['profiles'][0].update(contact_category='implant/blood'), 'matches no component'),
    (lambda d: d['profiles'][0].update(flags=[]), 'declared flags'),
    (lambda d: d['profiles'][1].update(flags=['ethylene_oxide']), 'declared flags'),
    (lambda d: d['profiles'][0]['profile'].update(framework='ISO_10993_1_2025'), 'FDA_2023_ATTACHMENT_A'),
    (lambda d: d['profiles'][0]['profile'].update(colour='red'), 'invalid DeviceProfile'),
    (lambda d: d.update(format='other'), 'profile set'),
])
def test_bad_profile_sets_rejected(mutate, match):
    with pytest.raises(FormatError, match=match):
        validate_profile_set(_profiles(mutate), DATA)


def _gold(mutate):
    doc = copy.deepcopy(GOLD)
    mutate(doc)
    return doc


@pytest.mark.parametrize('mutate,match', [
    (lambda d: d['labels'][0].update(text='clause text must never be stored'), 'gold'),
    (lambda d: d['labels'][0]['source'].update(text='clause text'), 'gold'),
    (lambda d: d['labels'][0].update(label='required'), 'gold'),
    (lambda d: d['labels'][0].update(item='Cyto'), 'not an ISO part'),          # endpoint, not part
    (lambda d: d['labels'][0].update(profile_id='FAKE-9'), 'unknown profile'),
    (lambda d: d['labels'][0]['source'].update(doc='fda-made-up'), 'gold'),
    (lambda d: d['labels'][0]['source'].update(clause_id='eu-mdr:Annex I.10.4'), "must start with 'fda-iso10993-1:'"),
    (lambda d: d['labels'].append(copy.deepcopy(d['labels'][0])), 'duplicate gold cell'),
    (lambda d: d.update(split='holdout'), 'gold split'),
])
def test_bad_gold_rejected(mutate, match):
    profiles = validate_profile_set(PROFILES, DATA)
    with pytest.raises(FormatError, match=match):
        validate_gold(_gold(mutate), profiles, DATA)


def test_gold_schema_has_no_text_field():
    entry = GOLD_SCHEMA['properties']['labels']['items']
    assert entry['additionalProperties'] is False and 'text' not in entry['properties']
    assert entry['properties']['source']['additionalProperties'] is False


def test_device_specific_standard_is_a_valid_gold_item():
    doc = _profiles(lambda d: d['profiles'][0]['profile'].update(device_specific_standards=['ISO 9394']))
    profiles = validate_profile_set(doc, DATA)
    gold = _gold(lambda d: d['labels'][0].update(item='ISO 9394'))
    assert ('FAKE-1', 'ISO 9394') in validate_gold(gold, profiles, DATA)
