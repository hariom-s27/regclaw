"""Provider contracts use fake transports only, without credentials or network."""
import copy
import json
import socket
from types import SimpleNamespace as NS

import jsonschema
import pytest

from llm.azure_openai import AzureOpenAIAdapter
from llm import provider
from ner.client import request, FakeClient
from ner.checks import SCHEMA
from ner.extract import run_pipeline
from standards.classifier import OUTPUT_SCHEMA, make_request, FakeLLM
from standards.data_loader import load_data
from standards.demo import FakeRetriever, example_profiles
from standards.engine import evaluate
from standards.profile import DeviceProfile
from standards.tool import get_applicable_standards


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for key in ('ANTHROPIC_API_KEY', 'AZURE_OPENAI_API_KEY', 'OPENAI_API_KEY',
                'AZURE_OPENAI_S4_REASONING_EFFORT', 'AZURE_OPENAI_S10_REASONING_EFFORT'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('AZURE_OPENAI_S4_DEPLOYMENT', 'deployment-extract')
    monkeypatch.setenv('AZURE_OPENAI_S10_DEPLOYMENT', 'deployment-explain')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    def deny(*args, **kwargs):
        raise AssertionError('Network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', deny)


def clause():
    return dict(id='fixture:1', doc='fixture', text='The manufacturer evaluates ethylene oxide.')


def empty_entities():
    return copy.deepcopy(FakeClient().default['data'])


def sdk(data=None, *, finish='stop', refusal=None, model='gpt-snapshot', usage=True):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return NS(model=model, choices=[NS(finish_reason=finish,
            message=NS(content=json.dumps(data), refusal=refusal))],
            usage=NS(prompt_tokens=120, completion_tokens=60) if usage else None)
    return NS(chat=NS(completions=NS(create=create))), calls


def standards_request():
    profile = DeviceProfile.model_validate(example_profiles()['E2'])
    data = load_data()
    return make_request(profile, data, evaluate(profile, data), [])


@pytest.mark.parametrize('mode', ['structured', 'strict_tool'])
def test_s4_request_mapping_preserves_prompt_schema_and_input(mode):
    client = AzureOpenAIAdapter('S4', mode=mode, sdk_client=object())
    original = request(clause(), mode=mode)
    before = copy.deepcopy(original)
    original['temperature'], original['top_p'] = 0, .9
    mapped = client.map_request(original)
    assert mapped['model'] == 'deployment-extract'
    assert mapped['messages'][0] == dict(role='system', content=before['system'][0]['text'])
    assert mapped['messages'][1:] == before['messages']
    assert mapped['response_format'] == dict(type='json_schema', json_schema=dict(
        name='emit_entities' if mode == 'strict_tool' else 'entities', strict=True, schema=SCHEMA))
    assert mapped['max_completion_tokens'] == before['max_tokens']
    assert mapped['reasoning_effort'] == 'low'
    assert set(mapped) == {'model', 'messages', 'max_completion_tokens', 'reasoning_effort', 'response_format'}
    assert {k: v for k, v in original.items() if k not in {'temperature', 'top_p'}} == before


def test_s10_mapping_and_effort_override(monkeypatch):
    req = standards_request()
    client = AzureOpenAIAdapter('S10', sdk_client=object())
    mapped = client.map_request(req)
    assert mapped['reasoning_effort'] == 'high'
    assert mapped['max_completion_tokens'] == 3 * req['max_tokens']
    assert mapped['model'] == 'deployment-explain'
    assert mapped['response_format']['json_schema']['schema'] == OUTPUT_SCHEMA
    assert mapped['messages'][0]['content'] == req['system']
    monkeypatch.setenv('AZURE_OPENAI_S10_REASONING_EFFORT', 'medium')
    medium = AzureOpenAIAdapter('S10', sdk_client=object())
    assert medium.effort == 'medium'
    assert medium.map_request(req)['max_completion_tokens'] == 2 * req['max_tokens']


@pytest.mark.parametrize('finish,refusal,expected', [
    ('stop', None, 'end_turn'), ('length', None, 'max_tokens'),
    ('content_filter', None, 'refusal'), ('stop', 'Declined', 'refusal'),
    ('tool_calls', None, 'tool_calls'), (None, None, 'unavailable')])
def test_response_stop_usage_and_provenance(finish, refusal, expected):
    transport, calls = sdk(empty_entities(), finish=finish, refusal=refusal)
    result = AzureOpenAIAdapter('S4', sdk_client=transport).extract(clause())
    assert result.stop_reason == expected and result.finish_reason == finish
    assert result.data == empty_entities()
    assert result.usage == dict(input_tokens=120, output_tokens=60)
    assert result.provenance == dict(provider='azure_openai', model='deployment-extract',
        model_version='gpt-snapshot', provisional=True, reasoning_effort='low')
    assert result.latency_s >= 0 and len(calls) == 1


def test_malformed_json_and_absent_usage():
    transport, _ = sdk(None, usage=False)
    result = AzureOpenAIAdapter('S4', sdk_client=transport).extract(clause())
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(result.data, SCHEMA)
    assert result.usage == {}


def test_provider_selection_and_anthropic_default(monkeypatch):
    import ner.client
    import standards.classifier
    monkeypatch.delenv('LLM_PROVIDER', raising=False)
    monkeypatch.setattr(ner.client, 'AnthropicClient', lambda **kw: ('anthropic-S4', kw))
    monkeypatch.setattr(standards.classifier, 'AnthropicLLM', lambda: 'anthropic-S10')
    for value in (None, 'anthropic'):
        if value:
            monkeypatch.setenv('LLM_PROVIDER', value)
        assert provider.make_client('S4') == ('anthropic-S4', dict(effort='low', mode='structured'))
        assert provider.make_client('S10') == 'anthropic-S10'
    monkeypatch.setenv('LLM_PROVIDER', 'azure_openai')
    monkeypatch.setattr('llm.azure_openai.AzureOpenAIAdapter', lambda role, **kw: ('azure', role))
    assert provider.make_client('S4') == ('azure', 'S4')
    assert provider.make_client('S10') == ('azure', 'S10')
    monkeypatch.setenv('LLM_PROVIDER', 'invalid')
    with pytest.raises(ValueError):
        provider.provider_name()


def test_stub_and_keys_do_not_activate_network(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    monkeypatch.setenv('LLM_PROVIDER', 'azure_openai')
    assert not provider.live_configured()
    monkeypatch.setenv('REGCLAW_STUB', '0')
    assert not provider.live_configured()


def test_live_key_selection_preserves_anthropic_default(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '0')
    monkeypatch.delenv('LLM_PROVIDER', raising=False)
    monkeypatch.setenv('AZURE_OPENAI_API_KEY', 'offline-fixture')
    assert not provider.live_configured()
    monkeypatch.setenv('LLM_PROVIDER', 'azure_openai')
    assert provider.live_configured()
    monkeypatch.setenv('REGCLAW_STUB', '1')
    assert not provider.live_configured()


def test_s10_automatic_provider_selection_without_anthropic_key(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '0')
    monkeypatch.setenv('LLM_PROVIDER', 'azure_openai')
    monkeypatch.setenv('AZURE_OPENAI_API_KEY', 'offline-fixture')
    fake = FakeLLM()
    selected = []
    def select(role):
        selected.append(role)
        return fake
    monkeypatch.setattr('standards.tool.make_client', select)
    result = get_applicable_standards(example_profiles()['E2'], FakeRetriever())
    assert selected == ['S10'] and len(fake.requests) == 1
    assert result['provenance']['llm_status'] == 'ok'


def test_live_anthropic_provenance_is_final():
    metadata = provider.provenance(NS(provider='anthropic', model='claude-role', effort='low'), 'snapshot')
    assert metadata == dict(provider='anthropic', model='claude-role', model_version='snapshot',
                            reasoning_effort='low', provisional=False)


def test_s4_paths_cache_and_default_fixture_regression(tmp_path):
    transport, calls = sdk(empty_entities())
    client = AzureOpenAIAdapter('S4', sdk_client=transport)
    rows, summary = run_pipeline([clause()], client, root=tmp_path)
    assert rows[0]['status'] == 'ok' and rows[0]['provenance']['provisional']
    assert summary['provenance']['model_version'] == 'gpt-snapshot'
    assert (tmp_path / 'data/runs/azure_openai/s4/ner.json').exists()
    assert not (tmp_path / 'data/ner.json').exists()
    run_pipeline([clause()], client, root=tmp_path)
    assert len(calls) == 1
    stub_rows, stub_summary = run_pipeline([clause()], FakeClient(), root=tmp_path)
    assert 'provenance' not in stub_rows[0] and 'provenance' not in stub_summary
    assert (tmp_path / 'data/ner.json').exists()


def test_s10_real_guard_provider_output_and_rule_floor(monkeypatch, tmp_path):
    monkeypatch.setattr(provider, 'ROOT', tmp_path)
    transport, _ = sdk(dict(standards=[], notes=[]))
    result = get_applicable_standards(example_profiles()['E2'], FakeRetriever(),
        llm=AzureOpenAIAdapter('S10', sdk_client=transport))
    assert result['provenance']['llm_status'] == 'ok'
    assert result['provenance']['reasoning_effort'] == 'high'
    assert result['provenance']['provider'] == 'azure_openai'
    assert result['provenance']['usage']['input_tokens'] == 120
    assert any(p['label'] == 'mandatory' for p in result['standards'])
    saved = list((tmp_path / 'data/runs/azure_openai/s10').glob('*.json'))
    assert len(saved) == 1 and json.loads(saved[0].read_text()) == result


def test_existing_spans_include_raw_finish_and_summary(monkeypatch):
    from contextlib import contextmanager
    from observability.run_summary import summarize, render
    spans = []
    @contextmanager
    def capture(name, **attributes):
        row = dict(name=name, **attributes)
        spans.append(row)
        yield NS(update=lambda **kw: row.update(kw))
    monkeypatch.setattr('llm.azure_openai.stage', capture)
    transport, _ = sdk(dict(standards=[], notes=[]))
    AzureOpenAIAdapter('S10', sdk_client=transport).classify(standards_request())
    assert spans[0]['name'] == 'standards.classify'
    assert spans[0]['metadata']['finish_reason'] == 'stop'
    assert spans[0]['metadata']['model_version'] == 'gpt-snapshot'
    assert spans[0]['usage_details'] == dict(input=120, output=60)
    summary = summarize([dict(trace_id='one', type='GENERATION', **spans[0])], 1)
    assert summary.provenance[0]['provisional']
    assert 'azure_openai' in render(summary)


@pytest.mark.parametrize('role,retry_status,expected_calls', [
    ('S4', 429, 1), ('S10', 408, 3), ('S10', 409, 3), ('S10', 429, 3),
    ('S10', 529, 3), ('S10', 400, 1)])
def test_retry_bounds_and_long_retry_after(monkeypatch, role, retry_status, expected_calls):
    import httpx
    from openai import APIStatusError
    delays, calls = [], []
    monkeypatch.setattr('llm.azure_openai.time.sleep', delays.append)
    response = httpx.Response(retry_status, headers={'retry-after': '180'},
                              request=httpx.Request('POST', 'https://fixture.invalid'))
    error = APIStatusError('offline fixture', response=response, body={})
    def create(**kw):
        calls.append(kw)
        raise error
    client = AzureOpenAIAdapter(role, sdk_client=NS(chat=NS(completions=NS(create=create))))
    with pytest.raises(APIStatusError) as caught:
        client._send({})
    assert caught.value is error and len(calls) == expected_calls
    assert delays == [180] * (expected_calls - 1)


def test_sdk_azure_v1_url_deployment_and_retry_configuration(monkeypatch):
    import httpx
    from openai import OpenAI
    captured = []
    def respond(req):
        captured.append((str(req.url), json.loads(req.content)))
        return httpx.Response(200, json=dict(id='fixture', object='chat.completion', created=0,
            model='snapshot', choices=[dict(index=0, finish_reason='stop',
            message=dict(role='assistant', content=json.dumps(empty_entities())))],
            usage=dict(prompt_tokens=1, completion_tokens=2, total_tokens=3)))
    transport = httpx.Client(transport=httpx.MockTransport(respond))
    client = OpenAI(base_url='https://fixture.openai.azure.com/openai/v1/',
                    api_key='offline-fixture', max_retries=0, http_client=transport)
    result = AzureOpenAIAdapter('S4', sdk_client=client).extract(clause())
    assert result.stop_reason == 'end_turn'
    assert captured[0][0] == 'https://fixture.openai.azure.com/openai/v1/chat/completions'
    assert captured[0][1]['model'] == 'deployment-extract'
    client.close()
