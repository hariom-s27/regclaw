"""R9 golden cases, explicit design conflicts, invariants and adversarial guards."""
import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys

from hypothesis import given, settings, strategies as st
import jsonschema
import pytest
from pydantic import ValidationError

from standards.classifier import AnthropicLLM, FakeLLM, LLMResponse, make_request
from standards.crosscheck import crosscheck
from standards.data_loader import ENDPOINTS, load_data, validate
from standards.demo import FakeRetriever, example_profiles
from standards.engine import RANK, evaluate
from standards.guard import guard
from standards.profile import DeviceProfile
from standards.tool import get_applicable_standards

DATA = load_data()
EXAMPLES = example_profiles()
CORE = [f'ISO 10993-{n}' for n in [1, 5, 10, 23, 11, 3, 6, 4, 12, 17, 18, 7]]
GOLD = {
    'E1': {p: ('not_applicable' if p in {'ISO 10993-4', 'ISO 10993-7'} else 'mandatory') for p in CORE},
    'E2': {p: ('not_applicable' if p in {'ISO 10993-3', 'ISO 10993-4', 'ISO 10993-6', 'ISO 10993-11', 'ISO 10993-7'} else 'mandatory') for p in CORE},
    'E3': {p: 'mandatory' for p in CORE},
    'E4': {p: ('not_applicable' if p in {'ISO 10993-4', 'ISO 10993-7'} else 'mandatory') for p in CORE},
}
GOLD['E1'].update({'ISO 10993-13': 'recommended', 'ISO 10993-15': 'recommended'})
GOLD['E3']['ISO 10993-13'] = 'recommended'
GOLD['E4']['ISO 9394'] = 'mandatory'


def labels(result):
    return {p['part']: p['label'] for p in result['parts']}


def endpoint_labels(result):
    return {ep: label for p in result['parts'] for ep, label in p['endpoints'].items()}


@pytest.mark.parametrize('key', list(EXAMPLES))
def test_golden_expressible_labels(key):
    result = evaluate(EXAMPLES[key], DATA)
    actual = labels(result)
    expected = GOLD[key]
    assert {p: actual[p] for p in expected} == expected
    if key == 'E2':
        assert any('Attachment G' in n for n in result['notes'])
    if key == 'E3':
        assert any('bacterial endotoxin' in n for n in result['notes'])
    if key == 'E4':
        assert result['parts'][0]['part'] == 'ISO 9394'


@pytest.mark.parametrize('key', list(EXAMPLES))
def test_exact_r9_golden(key):
    actual = labels(evaluate(EXAMPLES[key], DATA))
    assert {p: actual[p] for p in GOLD[key]} == GOLD[key]


@pytest.mark.parametrize('bad', [dict(unknown=True), dict(framework='FDA'), dict(target_markets=['us']),
    dict(labeled_non_pyrogenic='true'), dict(components=[]), dict(device_name=None)])
def test_strict_profile(bad):
    with pytest.raises(ValidationError):
        DeviceProfile.model_validate({**EXAMPLES['E1'], **bad})


def test_profile_nested_and_readonly():
    for patch in [{'extra': True}, {'derived': {}}, {'exposure': {'pattern': 'single', 'hours_per_use': float('inf')}},
                  {'exposure': {'pattern': 'intermittent_repeat', 'uses_per_patient': 1.5}}, {'materials': []}]:
        profile = copy.deepcopy(EXAMPLES['E1'])
        profile['components'][0].update(patch)
        with pytest.raises(ValidationError):
            DeviceProfile.model_validate(profile)


def test_data_validation_and_provenance():
    for mutate in [lambda d: d['matrix']['rows'][0]['cells']['Cyto']['provenance'].update(verified_by_human=True),
                   lambda d: d['matrix']['rows'].pop(),
                   lambda d: d['matrix']['rows'][0]['cells'].pop('Cyto'),
                   lambda d: d['part_map']['parts'][0].update(edition='UNVERIFIED')]:
        bad = copy.deepcopy(DATA)
        mutate(bad)
        with pytest.raises((ValueError, jsonschema.ValidationError)):
            validate(bad)
    assert len(DATA['data_version']) == 4
    for filename, digest in DATA['data_version'].items():
        assert hashlib.sha256((Path('standards/data')/filename).read_bytes()).hexdigest() == digest


def test_matrix_duration_monotonicity_no_exceptions():
    rows = DATA['matrix']['rows']
    for i in range(0, 24, 3):
        sets = [{ep for ep, cell in row['cells'].items() if cell['consider']} for row in rows[i:i+3]]
        assert sets[0] <= sets[1] <= sets[2]


def test_pdf_crosscheck_all_cells_and_mismatch_detection():
    # Independent compact transcription of Attachment A; no local corpus needed.
    bits = ['11100000000', '11100000000', '11100000000', '11100000000', '11111101000', '11111111010',
            '11111000000', '11111101000', '11111111011', '11111000100', '11111100100', '11111111111',
            '11111000000', '11111111000', '11111111011', '11111010100', '11111111100', '11111111111',
            '11111000000', '11111111000', '11111111011', '11111011100', '11111111100', '11111111111']
    categories = ['Surface device'] * 3 + ['External communicating device'] * 3 + ['Implant device'] * 2
    sites = ['Intact skin', 'Mucosal membrane', 'Breached or compromised surface', 'Blood path, indirect',
             'Tissue+/bone/ dentin', 'Circulating blood', 'Tissue+/bone', 'Blood']
    lines = []
    for i, row in enumerate(bits):
        symbols = ['X' if b == '1' else '' for b in row]
        if i == 15:
            symbols[6] = 'X^'
        lines.append(' | '.join([categories[i//3], sites[i//3], 'ABC'[i%3], *symbols, '', '']))
    clauses = [dict(doc='fda-iso10993-1', id='fda-iso10993-1:Attachment A.T1', number='Attachment A.T1',
                    text='\n'.join(lines), page=49)]
    result = crosscheck(clauses, DATA)
    assert result['cells'] == 264 and not result['mismatches']
    bad = copy.deepcopy(DATA)
    bad['matrix']['rows'][0]['cells']['Cyto']['consider'] = False
    assert crosscheck(clauses, bad)['mismatches'][0]['endpoint'] == 'Cyto'
    with pytest.raises(ValueError):
        crosscheck([], DATA)


def test_purity_and_determinism():
    profile, data = copy.deepcopy(EXAMPLES['E1']), copy.deepcopy(DATA)
    before = json.dumps([profile, data], sort_keys=True)
    digest = lambda: hashlib.sha256(json.dumps(evaluate(profile, data), sort_keys=True).encode()).hexdigest()
    assert digest() == digest()
    assert json.dumps([profile, data], sort_keys=True) == before


@st.composite
def components(draw, *, coherent=False):
    rows = DATA['matrix']['rows']
    row = draw(st.sampled_from(rows))
    site = row['site'] if coherent else draw(st.sampled_from(list({r['site'] for r in rows})))
    return dict(name=draw(st.sampled_from(['a', 'b', 'c'])), contact_nature=row['nature'], contact_site=site,
        exposure=dict(pattern=draw(st.sampled_from(['single', 'continuous', 'intermittent_repeat'])),
                      hours_per_use=draw(st.floats(min_value=0, max_value=2000, allow_nan=False, allow_infinity=False)),
                      uses_per_patient=draw(st.integers(1, 100)), contact_days=draw(st.integers(0, 100))),
        sterilization=draw(st.sampled_from(['gamma', 'steam', 'ethylene_oxide'])),
        extracorporeal_circuit=draw(st.booleans()), contacts_csf_lymph_or_cardiovascular=draw(st.booleans()),
        materials=[dict(name='material', material_class=draw(st.sampled_from(['polymer', 'metal_alloy', 'ceramic', 'other'])),
                        novel=draw(st.booleans()), absorbable_or_degradable=draw(st.booleans()), submicron_or_nano=draw(st.booleans()))])


PROPERTY = settings(max_examples=100, derandomize=True, database=None, deadline=None)


@PROPERTY
@given(components())
def test_property_adding_eo_only_changes_part7_for_sterile_devices(comp):
    comp['sterilization'] = 'gamma'
    profile = dict(device_name='test', components=[comp])
    before = labels(evaluate(profile, DATA))
    comp['sterilization'] = 'ethylene_oxide'
    after = labels(evaluate(profile, DATA))
    assert after['ISO 10993-7'] == 'mandatory'
    assert {k: v for k, v in before.items() if k != 'ISO 10993-7'} == {k: v for k, v in after.items() if k != 'ISO 10993-7'}


@PROPERTY
@given(components(), st.sampled_from(['non_sterile', 'gamma', 'e_beam', 'steam', 'vhp', 'other', 'ethylene_oxide']))
def test_property_adding_eo_never_removes_or_downgrades_any_part(comp, sterilization):
    comp['sterilization'] = sterilization
    profile = dict(device_name='test', components=[comp])
    before = labels(evaluate(profile, DATA))
    comp['sterilization'] = 'ethylene_oxide'
    after = labels(evaluate(profile, DATA))
    assert before.keys() <= after.keys()
    assert all(RANK[after[p]] >= RANK[label] for p, label in before.items())


def test_eo_sterile_status_retains_rule7():
    profile = copy.deepcopy(EXAMPLES['E4'])
    comp = profile['components'][0]
    comp.update(sterilization='non_sterile', contacts_csf_lymph_or_cardiovascular=True)
    comp['exposure'] = dict(pattern='single', hours_per_use=1)
    before = labels(evaluate(profile, DATA))
    comp['sterilization'] = 'ethylene_oxide'
    after = labels(evaluate(profile, DATA))
    assert {k for k in before if before[k] != after[k]} == {'ISO 10993-7', 'ISO 10993-11'}
    assert after['ISO 10993-11'] == 'mandatory'


def test_unlisted_contact_combination_uses_conservative_union():
    profile = copy.deepcopy(EXAMPLES['E2'])
    comp = profile['components'][0]
    comp['contact_site'] = 'blood'
    result = evaluate(profile, DATA)
    assert result['framework_row_unverified']
    assert any('conservative union at duration A' in a for a in result['assumptions'])
    expected = {ep for r in DATA['matrix']['rows'] if r['duration'] == 'A'
                for ep, cell in r['cells'].items() if cell['consider']}
    assert all(endpoint_labels(result)[ep] == 'mandatory' for ep in expected)


def test_r08_derivation_and_missing_triggers_are_visible_in_tool_response():
    profile = copy.deepcopy(EXAMPLES['E3'])
    profile['target_markets'] = ['EU']
    comp = profile['components'][0]
    comp['contact_mode'] = 'indirect'
    comp['materials'][0]['novel'] = True
    result = get_applicable_standards(profile, FakeRetriever(), llm=FakeLLM(), data=DATA)
    sample = bypart(result)['ISO 10993-12']
    assert sample['label'] == 'mandatory'
    assert any('chemical characterisation uses extraction' in r and 'extraction planned=True' in r
               for r in sample['rule_trace'])
    for trigger in ['chemical release', 'immune-effect plausibility', 'invasiveness']:
        assert any(trigger in a for a in result['assumptions'])


@PROPERTY
@given(st.lists(components(), min_size=1, max_size=4))
def test_property_component_order(comps):
    a = evaluate(dict(device_name='test', components=comps), DATA)
    b = evaluate(dict(device_name='test', components=list(reversed(comps))), DATA)
    assert a == b


@PROPERTY
@given(components(coherent=True), st.sampled_from([24., 720., 721.]))
def test_property_longer_duration_never_removes_endpoint(comp, hours):
    comp['exposure'] = dict(pattern='single', hours_per_use=hours)
    profile = dict(device_name='test', components=[comp])
    before = endpoint_labels(evaluate(profile, DATA))
    comp['exposure']['hours_per_use'] = max(hours, 721.)
    after = endpoint_labels(evaluate(profile, DATA))
    assert all(RANK[after[ep]] >= RANK[label] for ep, label in before.items())


@PROPERTY
@given(st.lists(components(), min_size=1, max_size=4))
def test_property_device_union(comps):
    together = labels(evaluate(dict(device_name='test', components=comps), DATA))
    separate = [labels(evaluate(dict(device_name='test', components=[comp]), DATA)) for comp in comps]
    assert together == {p: max([d[p] for d in separate], key=RANK.get) for p in together}


@PROPERTY
@given(st.lists(components(), min_size=1, max_size=4), st.sampled_from(['FDA_2023_ATTACHMENT_A', 'ISO_10993_1_2018', 'ISO_10993_1_2025']))
def test_property_any_valid_profile(comps, framework):
    result = evaluate(dict(device_name='test', components=comps, framework=framework), DATA)
    assert set(labels(result).values()) <= set(RANK)


@pytest.mark.parametrize('hours,expected', [(0, 'A'), (24, 'A'), (24.1, 'B'), (720, 'B'), (720.1, 'C')])
def test_duration_boundaries(hours, expected):
    profile = copy.deepcopy(EXAMPLES['E1'])
    profile['components'][0]['exposure'] = dict(pattern='single', hours_per_use=hours)
    from standards.engine import Context, rule_02
    p = DeviceProfile.model_validate(profile)
    c = Context(p, p.components[0], DATA)
    rule_02(c)
    assert c.duration == expected


def test_missing_exposure_and_noncontact():
    profile = copy.deepcopy(EXAMPLES['E1'])
    profile['components'][0]['exposure'] = {'pattern': 'intermittent_repeat'}
    assert any('longer class C' in a for a in evaluate(profile, DATA)['assumptions'])
    profile['components'][0]['contact_nature'] = 'non_contact'
    profile['components'][0]['sterilization'] = 'ethylene_oxide'
    result = evaluate(profile, DATA)
    assert set(labels(result).values()) == {'not_applicable'}
    assert any('no-contact' in n for n in result['notes'])


def test_2025_anchors_and_unverified_fallback():
    profile = copy.deepcopy(EXAMPLES['E2'])
    profile['framework'] = 'ISO_10993_1_2025'
    comp = profile['components'][0]
    comp['exposure']['contact_days'] = 1
    assert not evaluate(profile, DATA)['framework_row_unverified']
    comp['exposure']['contact_days'] = 2
    assert evaluate(profile, DATA)['framework_row_unverified']
    comp['contact_site'] = 'blood_path_indirect'
    assert evaluate(profile, DATA)['framework_row_unverified']


def test_evidence_does_not_lower_labels_and_notes():
    profile = copy.deepcopy(EXAMPLES['E3'])
    before = labels(evaluate(profile, DATA))
    profile['target_markets'] = ['EU']
    profile['intended_population'] = ['pregnant']
    comp = profile['components'][0]
    comp['existing_evidence'] = dict(equivalent_marketed_device=True)
    comp['materials'][0].update(novel=True, submicron_or_nano=True)
    result = evaluate(profile, DATA)
    assert labels(result) == before
    assert all(p['suggested_route'] == 'leverage existing data' for p in result['parts'])
    assert all(any(rule in n for n in result['notes']) for rule in ['R06', 'R07', 'R09', 'R12', 'R15'])


def decision(part='ISO 10993-1', label='mandatory', reason='Evaluate this endpoint.', ids=None):
    return dict(part=part, label=label, reason=reason, clause_ids=ids or [],
                unsupported_by_corpus=False, superseded_edition=False)


def guarded(items, *, stop='end_turn', raw=None):
    rules = evaluate(EXAMPLES['E2'], DATA)
    response = LLMResponse(raw if raw is not None else dict(standards=items, notes=[]), stop)
    return guard(rules, response, DATA, [dict(clause_id='fda-iso10993-1:III.A')])


def bypart(result):
    return {p['part']: p for p in result['standards']}


def test_guard_downgrade_blocked_and_logged():
    result = guarded([decision(label='not_applicable')])
    assert bypart(result)['ISO 10993-1']['label'] == 'mandatory'
    assert any(e.get('llm_label') == 'not_applicable' for e in result['disagreements'])


def test_guard_upgrade_allowed_and_logged():
    result = guarded([decision('ISO 10993-4', 'mandatory')])
    assert bypart(result)['ISO 10993-4']['label'] == 'mandatory'
    assert any(e.get('rule_label') == 'not_applicable' for e in result['disagreements'])


def test_guard_unknown_part_dropped():
    result = guarded([decision('ISO FAKE-999')])
    assert 'ISO FAKE-999' not in bypart(result)
    assert any(e['type'] == 'unknown_part' for e in result['disagreements'])


def test_guard_unretrieved_removed_and_case_normalised():
    result = guarded([decision(ids=['invented:1', 'FDA-ISO10993-1:iii.a'])])
    row = bypart(result)['ISO 10993-1']
    assert row['clause_ids'] == ['fda-iso10993-1:III.A']
    assert not row['unsupported_by_corpus']
    assert bypart(guarded([decision(ids=['invented:1'])]))['ISO 10993-1']['unsupported_by_corpus']


@pytest.mark.parametrize('phrase', ['test required', 'must be tested', 'compliant', 'approved'])
def test_guard_banned_phrase_replaced(phrase):
    result = guarded([decision(reason='This is ' + phrase.upper())])
    assert bypart(result)['ISO 10993-1']['reason'] == DATA['labels']['labels']['mandatory']
    assert any(e['type'] == 'banned_wording' for e in result['disagreements'])


@pytest.mark.parametrize('stop', ['refusal', 'max_tokens', 'tool_use', 'pause_turn'])
def test_guard_stop_reason_fallback(stop):
    result = guarded([decision(label='not_applicable')], stop=stop)
    assert result['llm_status'] == 'fallback'
    assert bypart(result)['ISO 10993-1']['label'] == 'mandatory'
    assert all(p['unsupported_by_corpus'] for p in result['standards'])


@pytest.mark.parametrize('raw', ['not-json', '{}', '[]', '{"standards":[],"notes":[],"extra":1}'])
def test_guard_schema_fallback(raw):
    assert guarded([], raw=raw)['llm_status'] == 'fallback'


def test_guard_omission_and_duplicates_keep_floor():
    result = guarded([decision('ISO 10993-4', 'mandatory'), decision('ISO 10993-4', 'not_applicable')])
    assert bypart(result)['ISO 10993-4']['label'] == 'mandatory'
    assert bypart(result)['ISO 10993-1']['label'] == 'mandatory'
    assert any(e['type'] == 'duplicate_part' for e in result['disagreements'])


@pytest.mark.parametrize('fixture', [RuntimeError('unavailable'), None])
def test_tool_unavailable_llm_fallback(fixture):
    llm = FakeLLM(fixture) if fixture is not None else None
    result = get_applicable_standards(EXAMPLES['E1'], FakeRetriever(), llm=llm, data=DATA)
    assert result['provenance']['llm_status'] == 'fallback'
    assert result['caveat'] == DATA['labels']['caveat']


def test_fake_request_contract_and_provenance():
    fake = FakeLLM()
    result = get_applicable_standards(EXAMPLES['E4'], FakeRetriever(), llm=fake, data=DATA, generated_at='2026-10-15T00:00:00+00:00')
    request = fake.requests[0]
    assert request['model'] == 'claude-sonnet-5-5'
    assert request['output_config']['format']['type'] == 'json_schema'
    assert 'temperature' not in request and 'tool_choice' not in request
    payload = json.loads(request['messages'][0]['content'])
    assert all(k in payload for k in ['rule_labels', 'matrix_rows', 'part_map', 'clauses'])
    assert result['standards'][0]['part'] == 'ISO 9394'
    assert result['provenance']['llm_status'] == 'ok'
    assert result['provenance']['model'] == 'FakeLLM (no API)'
    assert len(result['provenance']['prompt_sha256']) == 64


@pytest.mark.parametrize('stop', ['end_turn', 'refusal', 'max_tokens'])
def test_sdk_request_contract_with_real_types(stop):
    import inspect
    from unittest.mock import create_autospec
    from anthropic import Anthropic
    from anthropic.resources.messages import Messages
    from anthropic.types import Message, TextBlock, Usage, OutputConfigParam, JSONOutputFormatParam
    client = create_autospec(Anthropic, instance=True)
    client.messages = create_autospec(Messages, instance=True)
    payload = dict(standards=[decision()], notes=[])
    client.messages.create.return_value = Message(
        id='msg_fixture', content=[TextBlock(type='text', text=json.dumps(payload))],
        model='claude-sonnet-5-5', role='assistant', type='message', stop_reason=stop,
        usage=Usage(input_tokens=1, output_tokens=1))
    result = get_applicable_standards(EXAMPLES['E2'], FakeRetriever(), llm=AnthropicLLM(client), data=DATA)
    request = client.messages.create.call_args.kwargs
    inspect.signature(Messages.create).bind(client.messages, **request)
    assert 'output_config' in inspect.signature(Messages.create).parameters
    assert {'format', 'effort'} <= OutputConfigParam.__annotations__.keys()
    assert {'schema', 'type'} <= JSONOutputFormatParam.__annotations__.keys()
    from standards.classifier import OUTPUT_SCHEMA
    assert request['model'] == 'claude-sonnet-5-5'
    assert request['output_config'] == dict(format=dict(type='json_schema', schema=OUTPUT_SCHEMA), effort='low')
    assert 'temperature' not in request and 'tool_choice' not in request
    assert result['provenance']['llm_status'] == ('ok' if stop == 'end_turn' else 'fallback')
    assert bypart(result)['ISO 10993-12']['label'] == 'mandatory'
    if stop != 'end_turn':
        assert dict(type='fallback', stop_reason=stop) in result['provenance']['disagreements']


@pytest.mark.asyncio
@pytest.mark.parametrize('use_fake', [True, False])
async def test_mcp_v1_engine_with_injected_retriever(monkeypatch, use_fake):
    from fastmcp import Client
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'mcp'))
    try:
        server = importlib.import_module('server')
    finally:
        sys.path.pop(0)
    monkeypatch.setattr(server, 'STUB', True)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    retriever = FakeRetriever([dict(clause_id='fda-iso10993-1:III.A', text='Evaluation', doc='fda-iso10993-1')])
    fake = FakeLLM() if use_fake else None
    backend_class = server.Backend
    monkeypatch.setattr(server, 'Backend', lambda: backend_class(standards_retriever=retriever, standards_llm=fake))
    async with Client(server.mcp) as client:
        result = await client.call_tool('get_applicable_standards', dict(device_profile=EXAMPLES['E1']))
    body = result.structured_content
    assert body['provenance']['llm_status'] == ('ok' if use_fake else 'fallback')
    assert bypart(body)['ISO 10993-12']['label'] == 'mandatory'
    assert body['framework'] == 'FDA_2023_ATTACHMENT_A'
    assert 'assumptions' in body and retriever.requests
    if not use_fake:
        assert all(p['reason'] == DATA['labels']['labels'][p['label']] for p in body['standards'])
