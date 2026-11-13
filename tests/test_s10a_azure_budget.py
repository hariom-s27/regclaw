"""S10-09 v2 (cap, timeout, provenance, truncation re-run), S10-25 (explicit effort) and HTTP-attempt
counting for the Azure S10 adapter. Fake SDK objects only: no credentials, no network."""
import json
import socket
from types import SimpleNamespace as NS

import pytest

import standards.tool
from eval.s10 import runner as R
from llm.azure_openai import MAX_COMPLETION_CAP, AzureOpenAIAdapter
from standards.classifier import make_request
from standards.data_loader import load_data
from standards.demo import FakeRetriever, example_profiles
from standards.engine import evaluate
from standards.profile import DeviceProfile

CAP_ENV, TIMEOUT_ENV = 'AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS', 'AZURE_OPENAI_S10_TIMEOUT_S'
EFFORTS = {'minimal': 8000, 'low': 8000, 'medium': 16000, 'high': 24000}     # today's 8,000 x scale
OK_PAYLOAD = dict(standards=[], notes=[])


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for key in ('ANTHROPIC_API_KEY', 'AZURE_OPENAI_API_KEY', 'OPENAI_API_KEY', CAP_ENV, TIMEOUT_ENV,
                'AZURE_OPENAI_S4_REASONING_EFFORT', 'AZURE_OPENAI_S10_REASONING_EFFORT'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('AZURE_OPENAI_S4_DEPLOYMENT', 'deployment-extract')
    monkeypatch.setenv('AZURE_OPENAI_S10_DEPLOYMENT', 'deployment-explain')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    monkeypatch.setenv('REGCLAW_STUB', '1')

    def deny(*args, **kwargs):
        raise AssertionError('Network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(standards.tool, 'save_record', lambda *a, **k: None)   # nothing persisted under data/


def standards_request():
    profile = DeviceProfile.model_validate(example_profiles()['E2'])
    data = load_data()
    return make_request(profile, data, evaluate(profile, data), [])


def response(finish='stop', data=OK_PAYLOAD, details=True):
    usage = NS(prompt_tokens=120, completion_tokens=60,
               completion_tokens_details=NS(reasoning_tokens=40) if details else None,
               prompt_tokens_details=NS(cached_tokens=7) if details else None)
    return NS(model='gpt-snapshot', usage=usage,
              choices=[NS(finish_reason=finish, message=NS(content=json.dumps(data), refusal=None))])


def scripted(*steps):
    """Fake SDK: each step is a finish_reason string or an Exception to raise."""
    calls, steps = [], list(steps)

    def create(**kwargs):
        calls.append(kwargs)
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        if isinstance(step, Exception):
            raise step
        return response(step)
    return NS(chat=NS(completions=NS(create=create))), calls


def status_error(status):
    import httpx
    from openai import APIStatusError
    resp = httpx.Response(status, headers={'retry-after': '1'}, request=httpx.Request('POST', 'https://fixture.invalid'))
    return APIStatusError('offline fixture', response=resp, body={})


# ---- (a) cap ----
@pytest.mark.parametrize('effort,expected', sorted(EFFORTS.items()))
def test_cap_unset_keeps_the_scaled_budget(effort, expected):
    client = AzureOpenAIAdapter('S10', sdk_client=object(), effort=effort)
    assert client.cap is None
    assert client.map_request(standards_request())['max_completion_tokens'] == expected


@pytest.mark.parametrize('effort', sorted(EFFORTS))
def test_cap_set_is_the_same_cap_at_every_effort(monkeypatch, effort):
    monkeypatch.setenv(CAP_ENV, '64000')
    client = AzureOpenAIAdapter('S10', sdk_client=object(), effort=effort)
    mapped = client.map_request(standards_request())
    assert mapped['max_completion_tokens'] == 64000 and mapped['reasoning_effort'] == effort


@pytest.mark.parametrize('value', ['0', '-5', '128001', 'abc', '1.5'])
def test_bad_cap_is_refused(monkeypatch, value):
    monkeypatch.setenv(CAP_ENV, value)
    with pytest.raises(ValueError, match=CAP_ENV):
        AzureOpenAIAdapter('S10', sdk_client=object())


def test_cap_env_does_not_touch_other_roles(monkeypatch):
    monkeypatch.setenv(CAP_ENV, '64000')
    monkeypatch.setenv(TIMEOUT_ENV, '30')
    from ner.client import request
    client = AzureOpenAIAdapter('S4', sdk_client=object())
    req = request(dict(id='fixture:1', doc='fixture', text='Synthetic clause.'), mode='structured')
    assert client.cap is None and client.timeout is None
    assert client.map_request(req)['max_completion_tokens'] == req['max_tokens']


# ---- S10-25 explicit effort ----
def test_explicit_effort_wins_and_default_is_the_env(monkeypatch):
    assert AzureOpenAIAdapter('S10', sdk_client=object()).effort == 'high'
    monkeypatch.setenv('AZURE_OPENAI_S10_REASONING_EFFORT', 'medium')
    assert AzureOpenAIAdapter('S10', sdk_client=object()).effort == 'medium'
    assert AzureOpenAIAdapter('S10', sdk_client=object(), effort='low').effort == 'low'
    with pytest.raises(ValueError):
        AzureOpenAIAdapter('S10', sdk_client=object(), effort='maximum')


# ---- (b) timeout ----
def capture_openai(monkeypatch):
    import openai
    made = []
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: made.append(kw) or NS())
    monkeypatch.setenv('AZURE_OPENAI_ENDPOINT', 'https://fixture.invalid')
    monkeypatch.setenv('AZURE_OPENAI_API_KEY', 'offline-fixture')
    return made


def test_timeout_is_passed_only_for_s10_and_only_when_set(monkeypatch):
    made = capture_openai(monkeypatch)
    AzureOpenAIAdapter('S10')
    assert 'timeout' not in made[-1] and made[-1]['max_retries'] == 0          # unset: nothing changes
    monkeypatch.setenv(TIMEOUT_ENV, '900')
    AzureOpenAIAdapter('S10')
    assert made[-1]['timeout'] == 900.0
    AzureOpenAIAdapter('S4')
    assert 'timeout' not in made[-1]


@pytest.mark.parametrize('value', ['0', '-1', 'soon', 'inf'])
def test_bad_timeout_is_refused(monkeypatch, value):
    monkeypatch.setenv(TIMEOUT_ENV, value)
    with pytest.raises(ValueError, match=TIMEOUT_ENV):
        AzureOpenAIAdapter('S10', sdk_client=object())


# ---- (c) provenance ----
def test_s10_provenance_records_what_was_sent_and_returned():
    transport, calls = scripted('stop')
    result = AzureOpenAIAdapter('S10', sdk_client=transport, effort='medium').classify(standards_request())
    p = result.provenance
    assert p['reasoning_effort'] == 'medium' == calls[0]['reasoning_effort']
    assert p['max_completion_tokens'] == 16000 == calls[0]['max_completion_tokens']
    assert (p['finish_reason'], p['output_tokens'], p['reasoning_tokens'], p['cached_input_tokens']) == ('stop', 60, 40, 7)
    assert p['latency_s'] >= 0 and p['http_attempts'] == 1
    assert p['http_log'] == [dict(status='ok', finish_reason='stop', max_completion_tokens=16000)]
    assert len(p['attempts']) == 1 and p['attempts'][0]['usage']['output_tokens'] == 60
    assert result.stop_reason == 'end_turn' and result.finish_reason == 'stop'
    json.dumps(p)                                                    # provenance stays JSON-serialisable


def test_other_roles_provenance_is_unchanged():
    transport, _ = scripted('stop')
    from ner.client import request
    client = AzureOpenAIAdapter('S4', sdk_client=transport)
    result = client._complete(request(dict(id='f:1', doc='f', text='Synthetic.'), mode='structured'), 'ner.extract')
    assert result['provenance'] == dict(provider='azure_openai', model='deployment-extract',
                                        model_version='gpt-snapshot', provisional=True, reasoning_effort='low')


# ---- (d) truncation re-run ----
def test_truncation_gives_exactly_one_rerun_at_twice_the_cap(monkeypatch):
    monkeypatch.setenv(CAP_ENV, '1000')
    transport, calls = scripted('length', 'stop')
    result = AzureOpenAIAdapter('S10', sdk_client=transport).classify(standards_request())
    assert [c['max_completion_tokens'] for c in calls] == [1000, 2000]
    assert {k: v for k, v in calls[0].items() if k != 'max_completion_tokens'} == \
           {k: v for k, v in calls[1].items() if k != 'max_completion_tokens'}      # only the cap changes
    p = result.provenance
    assert [(a['max_completion_tokens'], a['finish_reason']) for a in p['attempts']] == [(1000, 'length'), (2000, 'stop')]
    assert all(a['usage'] == dict(input_tokens=120, output_tokens=60, reasoning_tokens=40, cache_read_input_tokens=7)
               for a in p['attempts'])
    assert p['http_attempts'] == 2 and p['max_completion_tokens'] == 2000 and result.stop_reason == 'end_turn'


def test_second_truncation_is_not_rerun_again(monkeypatch):
    monkeypatch.setenv(CAP_ENV, '1000')
    transport, calls = scripted('length')
    result = AzureOpenAIAdapter('S10', sdk_client=transport).classify(standards_request())
    assert len(calls) == 2 and result.stop_reason == 'max_tokens' and result.finish_reason == 'length'
    assert [a['finish_reason'] for a in result.provenance['attempts']] == ['length', 'length']


def test_rerun_cap_never_exceeds_128000(monkeypatch):
    monkeypatch.setenv(CAP_ENV, '100000')
    transport, calls = scripted('length', 'stop')
    AzureOpenAIAdapter('S10', sdk_client=transport).classify(standards_request())
    assert [c['max_completion_tokens'] for c in calls] == [100000, MAX_COMPLETION_CAP] == [100000, 128000]


def test_no_rerun_when_the_cap_is_already_128000(monkeypatch):
    monkeypatch.setenv(CAP_ENV, '128000')
    transport, calls = scripted('length', 'stop')
    result = AzureOpenAIAdapter('S10', sdk_client=transport).classify(standards_request())
    assert len(calls) == 1 and len(result.provenance['attempts']) == 1 and result.stop_reason == 'max_tokens'


def test_no_rerun_when_the_cap_env_is_unset():
    transport, calls = scripted('length', 'stop')
    result = AzureOpenAIAdapter('S10', sdk_client=transport).classify(standards_request())
    assert len(calls) == 1 and calls[0]['max_completion_tokens'] == 24000 and result.stop_reason == 'max_tokens'


# ---- HTTP attempts (transport retries + re-run) ----
def test_http_attempts_count_a_429_then_200(monkeypatch):
    monkeypatch.setattr('llm.azure_openai.time.sleep', lambda s: None)
    transport, calls = scripted(status_error(429), 'stop')
    result = AzureOpenAIAdapter('S10', sdk_client=transport).classify(standards_request())
    p = result.provenance
    assert len(calls) == 2 == p['http_attempts']
    assert [(h['status'], h['finish_reason']) for h in p['http_log']] == [(429, None), ('ok', 'stop')]
    assert len(p['attempts']) == 1 and p['attempts'][0]['http_attempts'] == 2


def test_http_attempts_add_retries_and_the_truncation_rerun(monkeypatch):
    monkeypatch.setattr('llm.azure_openai.time.sleep', lambda s: None)
    monkeypatch.setenv(CAP_ENV, '1000')
    transport, calls = scripted(status_error(429), 'length', status_error(503), 'stop')
    result = AzureOpenAIAdapter('S10', sdk_client=transport).classify(standards_request())
    p = result.provenance
    assert len(calls) == 4 == p['http_attempts']
    assert [(h['status'], h['finish_reason'], h['max_completion_tokens']) for h in p['http_log']] == [
        (429, None, 1000), ('ok', 'length', 1000), (503, None, 2000), ('ok', 'stop', 2000)]
    assert [a['http_attempts'] for a in p['attempts']] == [2, 2]


def test_failed_call_still_exposes_its_http_log(monkeypatch):
    monkeypatch.setattr('llm.azure_openai.time.sleep', lambda s: None)
    transport, calls = scripted(status_error(429))
    client = AzureOpenAIAdapter('S10', sdk_client=transport)
    with pytest.raises(Exception):
        client.classify(standards_request())
    assert len(calls) == 3 and [h['status'] for h in client.last_http_log] == [429, 429, 429]


# ---- S10-25 in the runner: one client per setting, mapped request carries the setting's effort ----
FIX = __import__('pathlib').Path(__file__).parent / 'fixtures' / 's10_eval'


def run_azure(tmp_path, factory, settings):
    from eval.s10.split_guard import load_eval_files
    _, profiles, _ = load_eval_files(FIX / 'fake_profiles.yaml')
    return R.run(profiles, settings, 1, factory, retriever=FakeRetriever(), out_dir=tmp_path / 'out')


def test_runner_sends_each_settings_effort_in_the_mapped_request(tmp_path):
    sent = {}

    def factory(setting):
        transport, sent[setting.name] = scripted('stop')
        return AzureOpenAIAdapter('S10', sdk_client=transport, effort=setting.effort)

    settings = [R.Setting('medium', 'medium'), R.Setting('high', 'high')]
    records = run_azure(tmp_path, factory, settings)
    for name in ('medium', 'high'):
        assert len(sent[name]) == 3 and {c['reasoning_effort'] for c in sent[name]} == {name}
    assert all(r['effort_effective'] == r['setting'] == r['llm_provenance']['reasoning_effort'] for r in records)
    assert all(r['http_attempts'] == 1 and r['llm_detail'] == 'ok' for r in records)


def test_runner_refuses_a_shared_azure_client_with_the_wrong_effort(tmp_path):
    transport, calls = scripted('stop')
    shared = AzureOpenAIAdapter('S10', sdk_client=transport)                    # env default: high
    with pytest.raises(ValueError, match='one client per setting'):
        run_azure(tmp_path, lambda s: shared, [R.Setting('medium', 'medium'), R.Setting('high', 'high')])
    assert calls == [] and not (tmp_path / 'out' / 'runs.jsonl').exists()


def test_azure_client_factory_passes_the_settings_effort(monkeypatch):
    made = capture_openai(monkeypatch)
    client = R.azure_client_factory(R.Setting('medium', 'medium'))
    assert client.effort == 'medium' and client.role == 'S10' and len(made) == 1
