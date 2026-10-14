import copy
import csv
import json
from pathlib import Path
import threading

import jsonschema
import pytest

from ner.checks import ENTITY_TYPES, SCHEMA, check_response, drift, valid_stop_reason
from ner.client import FakeClient, RawResult, request
from ner.extract import run_pipeline
from ner.normalise import Normaliser, local_abbreviations, review_candidates
from ner.stdref import ASTM_RE, ISO_RE, USP_RE, norm, references


def response(kind=None, mention='', canonical='', evidence=''):
    data = {'entities': {k: [] for k in [*ENTITY_TYPES, 'keywords']}}
    if kind:
        data['entities'][kind] = [dict(mention=mention, canonical=canonical, evidence=evidence)]
    return data


def clause(cid='x:1', text='Each manufacturer shall test ethylene oxide (EO) in catheters.'):
    return {'id': cid, 'doc': 'x', 'text': text}


@pytest.mark.parametrize('text,expected', [
    ('ISO 10993-7:2008/Amd 1:2019', ['ISO 10993-7:2008/Amd 1:2019']),
    ('EN ISO 10993-7:2008+A1:2022', ['EN ISO 10993-7:2008+A1:2022']),
    ('EN ISO 13485:2016+A11:2021', ['EN ISO 13485:2016+A11:2021']),
    ('ISO 7001:2007/Amd 1:2013/Cor 1:2014', ['ISO 7001:2007/Amd 1:2013/Cor 1:2014']),
    ('ISO/TS 15011-6:2012/Cor 1:2012', ['ISO/TS 15011-6:2012/Cor 1:2012']),
    ('ISO/IEC TR 11801-9909:2020/Cor 1:2024', ['ISO/IEC TR 11801-9909:2020/Cor 1:2024']),
    ('ISO 11135:2014/Amd 1:2018', ['ISO 11135:2014/Amd 1:2018']),
    ('per ISO 10993-1 and ISO 14971', ['ISO 10993-1', 'ISO 14971']),
    ('ISO 10993‑5:2009 (non-breaking hyphen)', ['ISO 10993-5:2009']),
    ('see ISO 10993-\n18:2020', ['ISO 10993-18:2020']),
    ('IEC 60601-1:2005+A1:2012', ['IEC 60601-1:2005+A1:2012']),
    ('ASTM F1980-21', ['ASTM F1980-21']), ('ASTM F2129-19a', ['ASTM F2129-19a']),
    ('ASTM D4169-16(2022)', ['ASTM D4169-16(2022)']),
    ('USP-NF ⟨88⟩', ['USP-NF ⟨88⟩']), ('USP 39 NF34, <87>', ['USP 39 NF34, <87>']),
    ('USP <87>', ['USP <87>']), ('USP Class VI', []),
    ('ISO 9001-certified supplier', ['ISO 9001']), ('at 10:30 the ISO lab', []),
])
def test_twenty_regex_strings(text, expected):
    assert [m[0] for r in (ISO_RE, ASTM_RE, USP_RE) for m in r.finditer(norm(text))] == expected


def test_canonical_ids_and_shorthand():
    assert references('ISO 10993-1, -5 and -10')[0] == ['ISO 10993-1', 'ISO 10993-5', 'ISO 10993-10']
    assert references('Parts 5, 10 and 23 of ISO 10993')[0] == ['ISO 10993-5', 'ISO 10993-10', 'ISO 10993-23']
    assert references('ISO 10993 series')[0] == ['ISO 10993']
    assert references('21 CFR 820.30; Recognition No. 2-258')[0] == []
    assert references('EN ISO 10993-7:2008+A1:2022; ISO 10993-7')[0] == ['ISO 10993-7']
    assert references('ASTM F1980-21; USP-NF ⟨88⟩')[0] == ['ASTM F1980', 'USP <88>']


@pytest.mark.parametrize('kind,mention,canonical,evidence,text,reason', [
    ('chemicals', 'nickel', 'nickel', 'nickel', 'There is ethylene oxide.', 'mention_grounding'),
    ('chemicals', 'ethylene oxide', 'ethylene oxide', 'There is', 'There is ethylene oxide.', 'evidence_grounding'),
    ('chemicals', 'ethylene oxide', 'ethylene oxide', 'invented ethylene oxide', 'There is ethylene oxide.', 'evidence_grounding'),
    ('device_types', 'devices', 'device', 'devices', 'These devices comply.', 'blocklist'),
    ('chemicals', 'ISO 10993-7', 'iso 10993-7', 'ISO 10993-7', 'ISO 10993-7 applies.', 'standard_as_entity'),
    ('biological_effects', 'irritation', 'made_up', 'irritation', 'irritation test', 'enum_invalid'),
])
def test_rejections(kind, mention, canonical, evidence, text, reason):
    checked, rejects, review, grounding = check_response(response(kind, mention, canonical, evidence), text, Normaliser())
    assert checked['entities'][kind] == []
    assert rejects[0]['reason'] == reason
    assert grounding[kind]['rejected'] == (1 if 'grounding' in reason else 0)


def test_schema_validation_and_enum_coercion():
    d = response('biological_effects', 'irritation', 'IRRITATION', 'irritation')
    checked, *_ = check_response(d, 'irritation', Normaliser())
    assert checked['entities']['biological_effects'][0]['canonical'] == 'irritation'
    d['extra'] = 1
    with pytest.raises(jsonschema.ValidationError): check_response(d, 'irritation', Normaliser())
    with pytest.raises(jsonschema.ValidationError): check_response({'entities': {}}, '', Normaliser())


def test_legitimacy_without_alias_or_definition(tmp_path):
    aliases = tmp_path / 'aliases.csv'
    aliases.write_text('alias,canonical_name,type,safe_global,source,added_by,added_on,note\n')
    _, _, review, _ = check_response(response('chemicals', 'EO', 'ethylene oxide', 'EO'), 'EO remains.', Normaliser(aliases))
    assert review[0]['reason'] == 'unsupported_expansion'


def test_duplicate_keywords_and_cross_type():
    d = response('chemicals', 'EO', 'ethylene oxide', 'EO')
    d['entities']['chemicals'] *= 2
    d['entities']['lifecycle_phases'] = [dict(mention='EO', canonical='sterilization', evidence='EO')]
    d['entities']['keywords'] = ['ETHYLENE OXIDE', 'LIMITS', 'limits'] + [f'word{i}' for i in range(10)]
    checked, rejects, review, _ = check_response(d, 'EO sterilization', Normaliser())
    assert len(checked['entities']['chemicals']) == 1
    assert rejects[0]['reason'] == 'duplicate'
    assert len(checked['entities']['keywords']) == 8
    assert checked['entities']['keywords'][0] == 'limits'
    assert any(p['reason'] == 'cross_type_collision' for p in review)


def test_run_drift():
    old = {'chemicals': {'mean': 1, 'empty_rate': .4}}
    assert not drift({'chemicals': {'mean': 1.25, 'empty_rate': .4}}, old)
    assert drift({'chemicals': {'mean': 1.26, 'empty_rate': .4}}, old)[0]['metric'] == 'mean'
    assert drift({'chemicals': {'mean': 0, 'empty_rate': .8}}, {'chemicals': {'mean': 0, 'empty_rate': 0}})


def test_pipeline_drift_stops_summary(tmp_path, capsys):
    fake = FakeClient({'x:1': RawResult(response('chemicals', 'EO', 'ethylene oxide', 'EO'))})
    run_pipeline([clause()], fake, root=tmp_path)
    _, summary = run_pipeline([clause()], FakeClient(), root=tmp_path, force=True)
    assert summary['drift_alerts']
    assert 'stopping summary for inspection' in capsys.readouterr().out


def test_normaliser():
    n = Normaliser()
    assert n.canonical('sensitisation', 'biological_effects')[0] == 'sensitization'
    assert n.canonical('devices', 'device_types')[0] == 'device'
    assert n.canonical('EG', 'chemicals')[0] == 'eg'
    assert n.canonical('EG', 'chemicals', 'ethylene glycol (EG)')[0] == 'ethane-1,2-diol'
    assert n.canonical('EO', 'chemicals', 'ethylene oxide (EO)')[0] == 'ethylene oxide'
    assert n.canonical('Ni', 'chemicals')[0] == 'ni'
    assert n.canonical('FDA', 'regulatory_actors')[0] == 'competent authority'
    assert n.canonical('the Agency', 'regulatory_actors')[0] == 'competent authority'
    assert local_abbreviations('residual ethylene oxide (EO)') == {'eo': 'ethylene oxide'}
    assert n.canonical('acids', 'chemicals')[0] == 'acids'
    proposals = review_candidates('implantable devices', {'implantable device'}, 'device_types')
    assert proposals and proposals[0]['accept'] == ''
    assert n.canonical('implantable devices', 'device_types')[0] == 'implantable device'
    assert len(n.rows) == 45 and all('CAS ' not in r['canonical_name'] for r in n.rows)


def test_pipeline_order_cache_errors_and_warmup(tmp_path):
    clauses = [clause('x:'+str(i)) for i in range(5)]
    fixtures = {clauses[0]['id']: RawResult(response('chemicals', 'EO', 'ethylene oxide', 'EO')),
                clauses[2]['id']: RawResult(response(), 'refusal')}
    class WarmupClient(FakeClient):
        def __init__(self):
            super().__init__(fixtures); self.first_completed = False
        def extract(self, c, **kwargs):
            if c['id'] != 'x:0': assert self.first_completed
            r = super().extract(c, **kwargs)
            if c['id'] == 'x:0': self.first_completed = True
            return r
    client = WarmupClient()
    rows, summary = run_pipeline(clauses, client, root=tmp_path)
    assert [r['id'] for r in rows] == [c['id'] for c in clauses]
    assert rows[2]['status'] == 'error' and rows[2]['id'] == 'x:2'
    assert rows[0]['entities']['chemicals'][0]['canonical'] == 'ethylene oxide'
    assert client.calls == 5
    # Successful cache entries make no calls; error rows retry.
    run_pipeline([clauses[i] for i in (0, 1, 3, 4)], client, root=tmp_path)
    assert client.calls == 5
    assert (tmp_path / 'data/ner_rejects.csv').exists()
    assert (tmp_path / 'data/alias_review.csv').exists()
    captured = client.requests[0]
    assert captured['output_config']['format']['schema'] == SCHEMA
    assert captured['output_config']['effort'] == 'low'
    assert 'temperature' not in captured and 'tool_choice' not in captured
    assert captured['system'][0]['cache_control'] == {'type': 'ephemeral'}
    assert 'cache_control' not in captured['messages'][0]
    assert captured['messages'][0]['content'] == f'<clause id="x:0" source="x">{clauses[0]["text"]}</clause>'


def test_stop_retry_schema_retry_strict_tool(tmp_path):
    c = clause()
    fake = FakeClient({'x:1': [RawResult(response(), 'max_tokens'), RawResult(response())]})
    rows, _ = run_pipeline([c], fake, root=tmp_path)
    assert rows[0]['status'] == 'stub'
    assert [r['max_tokens'] for r in fake.requests] == [4000, 8000]
    fake = FakeClient({'x:1': [RawResult({'bad': True}), RawResult(response())]})
    run_pipeline([c], fake, root=tmp_path, force=True)
    assert fake.calls == 2
    fake = FakeClient({'x:1': RawResult(response(), 'tool_use')}, mode='strict_tool')
    rows, _ = run_pipeline([c], fake, root=tmp_path, force=True)
    assert rows[0]['status'] == 'stub'
    assert fake.requests[0]['tools'][0]['strict'] is True
    assert valid_stop_reason('tool_use', 'strict_tool')
    assert not valid_stop_reason('tool_use')
    assert not valid_stop_reason('refusal')


@pytest.mark.parametrize('status', [429, 529])
def test_rate_backoff(tmp_path, status):
    class Busy(Exception): status_code = status
    fake = FakeClient({'x:1': [Busy(), Busy(), RawResult(response())]})
    sleeps = []
    rows, _ = run_pipeline([clause()], fake, root=tmp_path, sleep=sleeps.append)
    assert sleeps == [1, 2] and rows[0]['status'] == 'stub'


def test_recorded_fixture_and_cost(tmp_path):
    path = Path(__file__).with_name('fixtures') / 'ner/grounded.json'
    fake = FakeClient({'x:1': path})
    rows, summary = run_pipeline([clause()], fake, root=tmp_path)
    assert rows[0]['entities']['device_types'][0]['canonical'] == 'catheter'
    assert summary['cost_estimate_usd'] == pytest.approx((10*.1+20*.5)/1_000_000)


def test_failed_retries_preserve_id(tmp_path):
    fake = FakeClient({'x:1': RawResult(response(), 'max_tokens')})
    rows, _ = run_pipeline([clause()], fake, root=tmp_path)
    assert rows[0]['id'] == 'x:1' and rows[0]['status'] == 'error'
    assert fake.calls == 2
    fake = FakeClient({'x:1': RawResult({'invalid': True})})
    rows, _ = run_pipeline([clause()], fake, root=tmp_path, force=True)
    assert rows[0]['status'] == 'error' and fake.calls == 2


def test_anthropic_adapter_without_sdk_or_api(monkeypatch):
    import sys
    from types import SimpleNamespace
    from ner.client import AnthropicClient
    class OutputConfig: __annotations__ = {'effort': str, 'format': dict}
    class Tool: __annotations__ = {'strict': bool}
    class Messages:
        def create(self, *, max_tokens, messages, model, output_config=None): pass
    sdk = SimpleNamespace(__version__='synthetic', resources=SimpleNamespace(messages=SimpleNamespace(Messages=Messages)))
    monkeypatch.setitem(sys.modules, 'anthropic', sdk)
    monkeypatch.setitem(sys.modules, 'anthropic.types', SimpleNamespace(OutputConfigParam=OutputConfig, ToolParam=Tool))
    captured = []
    def create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type='text', text=json.dumps(response()))],
                               stop_reason='end_turn', model='recorded-model',
                               usage=SimpleNamespace(model_dump=lambda: {'input_tokens': 1}))
    client = AnthropicClient(sdk_client=SimpleNamespace(messages=SimpleNamespace(create=create)))
    result = client.extract(clause())
    assert result.model == 'recorded-model'
    assert captured[0]['output_config']['effort'] == 'low'
    assert captured[0]['output_config']['format']['type'] == 'json_schema'
    OutputConfig.__annotations__ = {}
    with pytest.raises(NotImplementedError, match='lacks verified'):
        AnthropicClient(sdk_client=object())


def test_exact_schema_prompt_and_regex_source():
    # Review-time source parity; CI need not carry untracked specs.
    from ner.prompt import SYSTEM_PROMPT
    import re
    research = Path(__file__).parents[1] / 'specs/S4_entity_extraction_research.md'
    if not research.exists():
        assert SYSTEM_PROMPT.count('EXAMPLE ') == 3
        return
    source = research.read_text(encoding='utf-8')
    expected_schema = re.search(r'```json\n(.*?)\n```', source.split('## B. Recommended schema')[1], re.S)[1]
    assert Path(__file__).parents[1].joinpath('ner/schema.json').read_text(encoding='utf-8') == expected_schema + '\n'
    expected_prompt = re.search(r'```text\n(.*?)\n```', source.split('## D. System prompt template')[1], re.S)[1]
    assert SYSTEM_PROMPT == expected_prompt
    source_regex = Path(__file__).parents[1].joinpath('specs/stdref_regex.py').read_text(encoding='utf-8').split('\ntests =')[0]
    assert Path(__file__).parents[1].joinpath('ner/stdref.py').read_text(encoding='utf-8').startswith(source_regex)
