"""Normaliser v1.1: conservative last-word singularisation, versioned (ADR 0027)."""
import json

import pytest

from goldset.score_entities import score
from ner.client import FakeClient, RawResult
from ner.extract import run_pipeline
from ner.normalise import NORMALISER_VERSION, Normaliser, singular
from ner.reclean import CacheOnlyClient, reclean


@pytest.mark.parametrize('mention, expected', [
    ('heart valves', 'heart valve'), ('guidewires', 'guidewire'), ('vena cava filters', 'vena cava filter'),
    ('catheters', 'catheter'), ('bodies', 'body'), ('boxes', 'box'), ('switches', 'switch'),
    ('brushes', 'brush'), ('analysis', 'analysis'), ('lens', 'lens'), ('glass', 'glass'),
    ('series', 'series'), ('gas', 'gas'), ('apparatus', 'apparatus'), ('species', 'species'),
    ('diagnosis', 'diagnosis'), ('mass', 'mass'), ('orthopedics', 'orthopedics'), ('stimulus', 'stimulus'),
    ('prostheses', 'prosthesis'), ('analyses', 'analysis'), ('diagnoses', 'diagnosis'), ('cases', 'case'),
    ('doses', 'dose'), ('bases', 'base'), ('sizes', 'size'), ('glasses', 'glass'), ('classes', 'class'),
    ('themselves', 'themselves'), ('dies', 'die'), ('CEMS', 'cems'), ('SCVs', 'scv'), ('IVDs', 'ivd'),
    ('EtO CEMS', 'eto cems'), ('uncemented prostheses', 'uncemented prosthesis'),
    ('lenses', 'lens'), ('contact lenses', 'contact lens'), ('VARIANTS AND ACCESSORIES', 'variants and accessories'),
])
def test_v11_device_types(mention, expected):
    assert Normaliser().canonical(mention, 'device_types')[0] == expected


def test_only_the_last_word_changes():
    n = Normaliser()
    assert n.canonical('dialysis catheters', 'device_types')[0] == 'dialysis catheter'
    assert n.canonical('glass syringes', 'device_types')[0] == 'glass syringe'
    assert n.canonical('lens holders', 'DeviceType')[0] == 'lens holder'
    assert n.canonical('vascular grafts', 'device_types')[0] == 'vascular graft'
    assert n.canonical('CEMS monitors', 'device_types')[0] == 'cems monitor'


def test_acronym_guard_uses_raw_case_of_the_unexpanded_word():
    n = Normaliser()
    # Without raw upper case the word is an ordinary plural.
    assert n.canonical('cems', 'device_types')[0] == 'cem'
    text = 'continuous emission monitoring systems (CEMS) are used.'
    assert n.canonical('CEMS', 'device_types', text)[0] == 'continuous emission monitoring system'


def test_lifecycle_and_never_chemicals():
    n = Normaliser()
    assert n.canonical('clinical investigations', 'lifecycle_phases')[0] == 'clinical investigation'
    assert n.canonical('cobalt-chrome alloys', 'chemicals')[0] == 'cobalt-chrome alloys'
    assert n.canonical('heart valves', 'regulatory_actors')[0] == 'heart valves'


def test_singulars_map_first_and_v10_unchanged():
    assert singular('devices') == 'device'
    assert Normaliser().canonical('implantable devices', 'device_types')[0] == 'implantable device'
    old = Normaliser(version='1.0')
    assert NORMALISER_VERSION == '1.1' and old.version == '1.0'
    assert old.canonical('heart valves', 'device_types')[0] == 'heart valves'
    assert old.canonical('catheters', 'device_types')[0] == 'catheter'
    with pytest.raises(ValueError, match='Unknown normaliser version'):
        Normaliser(version='2.0')


def test_scorer_keeps_canonicals_under_both_versions():
    # The scorer passes canonical as enum_canonical, so it never re-singularises:
    # a v1.1 gain must come from the re-cleaned ner.json, and gold is unchanged.
    gold = {'a': [{'type': 'device_types', 'canonical': 'heart valve'}]}
    f1 = lambda pred, v: score(gold, {'a': [{'type': 'device_types', 'canonical': pred}]}, schemes=('canonical',),
                               resamples=1, version=v)['metrics']['canonical']['micro']['f1']
    assert [f1('heart valves', v) for v in ('1.0', '1.1')] == [0, 0]
    assert [f1('heart valve', v) for v in ('1.0', '1.1')] == [1, 1]
    assert score(gold, gold, schemes=('canonical',), resamples=1, version='1.0')['normaliser_version'] == '1.0'


ENTITY = dict(mention='heart valves', canonical='heart valves', evidence='heart valves')


def _cached_run(tmp_path):
    clause = dict(id='x:1', doc='x', text='Testing of heart valves is required.')
    data = {'entities': {'biological_effects': [], 'chemicals': [], 'device_types': [ENTITY],
                         'regulatory_actors': [], 'lifecycle_phases': [], 'keywords': []}}
    client = FakeClient({'x:1': RawResult(data, model='gpt-5-mini')})
    client.provider, client.model = 'azure_openai', 'gpt-5-mini'
    run_pipeline([clause], client, root=tmp_path, normaliser_version='1.0')
    return clause, tmp_path / 'data/runs/azure_openai/s4'


def test_reclean_writes_new_folder_without_calls(tmp_path):
    clause, source = _cached_run(tmp_path)
    before = {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}
    assert json.loads((source/'ner.json').read_text())[0]['entities']['device_types'][0]['canonical'] == 'heart valves'
    out = source / 'norm_v1.1'
    client = CacheOnlyClient()
    rows, summary = reclean([clause], client, cache_path=source/'ner_cache.json', output_dir=out, root=tmp_path)
    assert client.calls == 0 and summary['calls'] == 0 and summary['normaliser_version'] == '1.1'
    assert rows[0]['entities']['device_types'][0]['canonical'] == 'heart valve'
    assert rows[0]['normaliser_version'] == '1.1'
    assert json.loads((out/'run_summary.json').read_text())['normaliser_version'] == '1.1'
    assert {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()} == before
    with pytest.raises(FileExistsError):
        reclean([clause], CacheOnlyClient(), cache_path=source/'ner_cache.json', output_dir=out, root=tmp_path)


def test_reclean_aborts_before_any_live_call(tmp_path):
    clause, source = _cached_run(tmp_path)
    client = CacheOnlyClient(effort='high')
    with pytest.raises(RuntimeError, match='not cached'):
        reclean([clause], client, cache_path=source/'ner_cache.json', output_dir=tmp_path/'new', root=tmp_path)
    assert client.calls == 0 and not (tmp_path/'new').exists()


def test_explicit_output_dir_must_not_be_the_run_folder(tmp_path):
    clause, source = _cached_run(tmp_path)
    with pytest.raises(ValueError, match='must differ'):
        run_pipeline([clause], CacheOnlyClient(), root=tmp_path, output_dir=source)
