"""S10-17 cell tagger on hand-made FAKE profiles (never real eval profiles).

One settled cell and one judgement cell per rule type the tagger relies on, the approved order
of checks, and the self-check: with no trigger, the counterfactual label equals the engine label."""
import json

import pytest

from eval.s10 import tagger as T
from standards.data_loader import load_data
from standards.engine import evaluate

DATA = load_data()
ALWAYS = ['ISO 10993-1', 'ISO 10993-12', 'ISO 10993-17', 'ISO 10993-18']
P5, P3, P4, P6, P7, P9, P11 = (f'ISO 10993-{n}' for n in (5, 3, 4, 6, 7, 9, 11))


def comp(nature='surface', site='intact_skin', hours=2, cls='other', ster='non_sterile', name='FAKE part', **extra):
    material = dict(name='FAKE material', material_class=cls, **extra.pop('material', {}))
    return dict(name=name, contact_nature=nature, contact_site=site, exposure=dict(pattern='single', hours_per_use=hours),
                materials=[material], sterilization=ster, **extra)


def prof(*components, **extra):
    return dict(device_name='FAKE device (not a real device)', components=list(components) or [comp()], **extra)


def tags(profile):
    return T.tag_profile(profile, DATA)


def engine(profile):
    return {p['part']: p['label'] for p in evaluate(profile, DATA)['parts']}


def test_always_rule_parts_are_the_four_r08_parts():
    assert sorted(T.always_rule_parts(DATA)) == sorted(ALWAYS)
    assert T.ALWAYS_RULE_ENDPOINTS == {'Framework', 'TRA', 'Chemical', 'Sample'}


def test_trigger_free_profile_baseline():
    t = tags(prof())
    assert set(t.values()) == set(T.TAGS)
    assert all(t[p] == 'always_rule' for p in ALWAYS)
    assert all(t[p] == 'matrix_settled' for p in (P5, P3, P4, P6, P11))     # matrix columns, row decides
    assert all(t[p] == 'judgement' for p in (P7, P9, 'ISO 10993-16', 'ISO/TS 10993-20', 'ISO 11737-3'))  # no column


@pytest.mark.parametrize('hours', [2, 100, 9000])                # duration A, B, C
def test_self_check_counterfactual_equals_engine_without_triggers(hours):
    profile = prof(comp(hours=hours))
    assert T.inexpressible_features(T._profile(profile), DATA) == []
    assert T.counterfactual_labels(profile, DATA) == engine(profile)        # every part


@pytest.mark.parametrize('nature,site', [(r['nature'], r['site']) for r in DATA['matrix']['rows'] if r['duration'] == 'A'])
@pytest.mark.parametrize('hours', [2, 100, 9000])
def test_matrix_only_parts_agree_with_the_engine_on_every_matrix_row(nature, site, hours):
    profile = prof(comp(nature, site, hours))
    cf, eng, t = T.counterfactual_labels(profile, DATA), engine(profile), tags(profile)
    for part in (P5, 'ISO 10993-10', 'ISO 10993-23', P6, P4):               # no trigger rule touches these labels
        assert cf[part] == eng[part] and t[part] == 'matrix_settled'
    assert all(cf[p] == eng[p] == 'mandatory' and t[p] == 'always_rule' for p in ALWAYS)


# ---- one judgement cell per trigger rule; the settled neighbour stays settled ----
def test_r07_pyrogenicity_trigger_changes_part_11_only():
    t = tags(prof(labeled_non_pyrogenic=True))
    assert t[P11] == 'judgement' and t[P5] == 'matrix_settled'              # engine mandatory, row says not applicable


def test_r05_site_trigger_makes_part_3_judgement():
    profile = prof(comp(site='mucosal_membrane', hours=100))
    assert engine(profile)[P3] == 'recommended'
    t = tags(profile)
    assert t[P3] == 'judgement' and t[P9] == 'judgement' and t[P5] == 'matrix_settled' and t[P6] == 'matrix_settled'


def test_r04_extracorporeal_footnote():
    blood = dict(nature='externally_communicating', site='circulating_blood', ster='gamma')
    without = tags(prof(comp(extracorporeal_circuit=False, **blood)))
    with_circuit = tags(prof(comp(extracorporeal_circuit=True, **blood)))
    assert without[P3] == 'judgement'                 # R04 removes the row's genotoxicity; R05 leaves recommended
    assert with_circuit[P3] == 'matrix_settled'       # engine label equals the row
    assert without[P4] == with_circuit[P4] == 'matrix_settled'


def test_r09_sterilant_and_degradation_parts_have_no_matrix_column():
    t = tags(prof(comp(ster='ethylene_oxide', cls='polymer', material=dict(absorbable_or_degradable=True))))
    assert t[P7] == 'judgement' and t[P9] == 'judgement' and t['ISO 10993-13'] == 'judgement'
    assert t[P5] == 'matrix_settled' and t['ISO 10993-1'] == 'always_rule'


def test_r14_device_specific_standard_is_always_judgement():
    t = tags(prof(device_specific_standards=['fake-device-standard', P5, 'ISO 10993-1']))
    assert t['fake-device-standard'] == 'judgement'
    assert t[P5] == 'judgement'                       # step 1 beats the matrix check
    assert t['ISO 10993-1'] == 'judgement'            # step 1 beats always_rule
    assert t['ISO 10993-17'] == 'always_rule' and t['ISO 10993-10'] == 'matrix_settled'


def test_multi_component_contact_makes_matrix_cells_judgement():
    two = prof(comp(name='FAKE a'), comp('implant', 'tissue_bone', 9000, ster='gamma', name='FAKE b'))
    t = tags(two)
    assert all(t[p] == 'judgement' for p in (P5, P6, P3, P11))
    assert all(t[p] == 'always_rule' for p in ALWAYS)


def test_extra_non_contact_component_does_not_unsettle_the_contacting_one():
    t = tags(prof(comp(name='FAKE a'), comp('non_contact', name='FAKE housing')))
    assert t[P5] == 'matrix_settled' and t['ISO 10993-1'] == 'always_rule'


def test_non_contact_only_profile_has_no_always_rule_and_no_settled_cell():
    assert set(tags(prof(comp('non_contact'))).values()) == {'judgement'}


# ---- features the engine cannot express: every cell judgement, except always_rule (step 2 first) ----
@pytest.mark.parametrize('extra,feature', [
    (dict(intended_population=['pediatric']), 'special_population'),
    (dict(intended_population=['adult', 'pregnant']), 'special_population'),
    (dict(intended_population=['neonate_infant']), 'special_population'),
])
def test_special_population_makes_the_profile_judgement(extra, feature):
    profile = prof(**extra)
    assert T.inexpressible_features(T._profile(profile), DATA) == [feature]
    t = tags(profile)
    assert all(t[p] == 'always_rule' for p in ALWAYS)
    assert {v for k, v in t.items() if k not in ALWAYS} == {'judgement'}


@pytest.mark.parametrize('population', [[], ['adult']])
def test_adult_or_empty_population_is_not_special(population):
    profile = prof(intended_population=population)
    assert T.inexpressible_features(T._profile(profile), DATA) == [] and tags(profile)[P5] == 'matrix_settled'


@pytest.mark.parametrize('material,feature', [(dict(novel=True), 'novel_material'),
                                              (dict(submicron_or_nano=True), 'nano_material')])
def test_novel_or_nano_material_makes_the_profile_judgement(material, feature):
    profile = prof(comp(material=material))
    assert T.inexpressible_features(T._profile(profile), DATA) == [feature]
    t = tags(profile)
    assert t[P5] == 'judgement' and t['ISO 10993-18'] == 'always_rule' and 'matrix_settled' not in t.values()


def test_missing_exposure_assumed_long_term_makes_the_profile_judgement():
    c = comp()
    c['exposure'] = dict(pattern='single')
    profile = prof(c)
    assert T.inexpressible_features(T._profile(profile), DATA) == ['missing_exposure']
    t = tags(profile)
    assert t[P5] == 'judgement' and t['ISO 10993-12'] == 'always_rule' and 'matrix_settled' not in t.values()


def test_other_framework_has_no_settled_cell():
    t = tags(prof(framework='ISO_10993_1_2018'))
    assert 'matrix_settled' not in t.values() and t['ISO 10993-1'] == 'always_rule'


# ---- tags file ----
def test_build_write_and_load_tags(tmp_path):
    profiles = {'FAKE-B': dict(profile=prof(labeled_non_pyrogenic=True)), 'FAKE-A': dict(profile=prof())}
    doc = T.build_tags(profiles, DATA)
    assert doc['format'] == 's10-cell-tags.v1' and [r['profile_id'] for r in doc['tags']][0] == 'FAKE-A'
    assert 'FAKE device' not in json.dumps(doc)                          # ids, items and tags only
    path = T.write_tags(tmp_path / 'tags.json', doc)
    assert b'\r' not in path.read_bytes()
    loaded = T.load_tags(path, required=[('FAKE-A', P5), ('FAKE-B', P11)])
    assert loaded[('FAKE-A', P11)] == 'matrix_settled' and loaded[('FAKE-B', P11)] == 'judgement'
    with pytest.raises(T.TagError, match='no tag'):
        T.load_tags(path, required=[('FAKE-C', P5)])


@pytest.mark.parametrize('doc', [
    dict(format='other', tags=[]),
    dict(format='s10-cell-tags.v1', tags=[dict(profile_id='A', item='x', tag='settled')]),
    dict(format='s10-cell-tags.v1', tags=[dict(profile_id='A', item='x', tag='judgement', note='extra')]),
    dict(format='s10-cell-tags.v1', tags=[dict(profile_id='A', item='x', tag='judgement')] * 2),
])
def test_bad_tags_files_are_refused(tmp_path, doc):
    path = tmp_path / 'tags.json'
    path.write_text(json.dumps(doc), encoding='utf-8')
    with pytest.raises(T.TagError):
        T.load_tags(path)
