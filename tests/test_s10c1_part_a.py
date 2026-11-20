"""S10c Part A contracts on in-test fakes; no services or real eval files."""
import builtins
import copy
import io
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import pytest
import yaml
from openai import APIConnectionError, APIStatusError, APITimeoutError

import _netguard
import standards.tool
from eval.s10 import formats as F
from eval.s10 import frozen_candidates as FC
from eval.s10 import runner as R
from eval.s10 import split_guard as SG
from eval.s10 import tagger as T
from llm import provider
from llm.azure_openai import AzureOpenAIAdapter, S8_ROLES
from standards.ruleset import normalised_sha

CHECK_RULESET = FC.ruleset.check_ruleset


@pytest.fixture
def tmp_path(tmp_path_factory):
    # Test-name suffixes can accidentally form 's8', which the cache guard rejects.
    root = tmp_path_factory.mktemp('partaworld')
    assert 's8' not in str(root).casefold(), "partaworld root contains 's8'; choose a --basetemp without 's8'"
    return root


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    monkeypatch.setenv('AZURE_OPENAI_S4_DEPLOYMENT', 'FAKE-extract')
    monkeypatch.setenv('AZURE_OPENAI_S10_DEPLOYMENT', 'FAKE-classify')
    monkeypatch.setenv('AZURE_OPENAI_S8_DEPLOYMENT', 'FAKE-retrieve')
    for key in ('AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS', 'AZURE_OPENAI_S10_TIMEOUT_S',
                'AZURE_OPENAI_S4_REASONING_EFFORT', 'AZURE_OPENAI_S10_REASONING_EFFORT',
                'AZURE_OPENAI_S8_REASONING_EFFORT', 'REGCLAW_RUN_DIR',
                'ANTHROPIC_API_KEY', 'AZURE_OPENAI_API_KEY', 'OPENAI_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    _netguard.block_network(monkeypatch, 'Part A network forbidden')
    sleeps = []
    monkeypatch.setattr('llm.azure_openai.time.sleep', sleeps.append)
    monkeypatch.setattr(standards.tool, 'save_record', lambda *a, **kw: None)

    def deny_loader(*args, **kwargs):
        raise AssertionError('Part A requires injected fake data and retrievers')

    monkeypatch.setattr('standards.data_loader.load_data', deny_loader)
    monkeypatch.setattr(F, 'load_data', deny_loader)
    monkeypatch.setattr(FC, '_live_retriever_factory', deny_loader)
    root = Path(__file__).resolve().parents[1]

    def guarded(real):
        def opened(file, *args, **kwargs):
            if isinstance(file, (str, os.PathLike)):
                path = Path(file).resolve()
                if path.name == '.env':
                    raise AssertionError('Part A cannot open environment files')
                if not path.is_relative_to(tmp_path.resolve()) and any(
                        path.is_relative_to(folder) for folder in (root / 'data', root / 'standards/data')):
                    raise AssertionError('Part A cannot open real data')
            return real(file, *args, **kwargs)
        return opened

    monkeypatch.setattr(builtins, 'open', guarded(builtins.open))
    monkeypatch.setattr(io, 'open', guarded(io.open))
    return sleeps


@pytest.fixture
def fake_data():
    # Deliberately small invented matrix and part map, built without loading YAML.
    parts = [('ISO 10993-1', 'Framework'), ('ISO 10993-5', 'Cyto'),
             ('ISO 10993-9', 'Degradation')]
    return dict(matrix=dict(rows=[dict(nature='surface', site='intact_skin', duration='A',
                                      cells=dict(Cyto=dict(consider=True)))]),
                anchors=dict(rows=[]),
                part_map=dict(parts=[dict(part=p, endpoints=[ep], edition='FAKE', edition_verified=False)
                                     for p, ep in parts]),
                labels=dict(labels={label: 'FAKE evaluation rationale.' for label in F.LABELS},
                            banned_phrases=[], caveat='FAKE test data.'), data_version={'fake': 'in-test'})


def profile_doc(split='dev'):
    profile = dict(device_name='FAKE device from this test', components=[dict(
        name='FAKE component', contact_nature='surface', contact_site='intact_skin',
        exposure=dict(pattern='single', hours_per_use=2.0),
        materials=[dict(name='FAKE material', material_class='other')], sterilization='non_sterile')])
    row = dict(id='FAKE-A', stratum='S1', split=split, contact_category='surface/intact_skin',
               duration='A', flags=[], profile=profile)
    return dict(format=F.PROFILES_FORMAT, split=split, profiles=[row])


def gold_doc(version=2, split='dev'):
    row = dict(profile_id='FAKE-A', item='ISO 10993-5', label='mandatory')
    if version == 1:
        row['source'] = dict(doc='fda-iso10993-1', clause_id='fda-iso10993-1:FAKE')
        return dict(format=F.GOLD_FORMAT, split=split, labels=[row])
    return dict(format=F.GOLD_FORMAT_V2, split=split,
                reference=dict(method='jury median with boundary routing (S10P-04); 0 routed',
                               merge_sha256_lf='a' * 64, export_sha256_lf='b' * 64), labels=[row])


def write_new(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
    return path


def write_profiles(tmp_path, split='dev'):
    return write_new(tmp_path / 'P.yaml', yaml.safe_dump(profile_doc(split)))


def request():
    return dict(system='FAKE system', messages=[dict(role='user', content='FAKE input')], max_tokens=50,
                output_config=dict(format=dict(schema=dict(type='object', additionalProperties=False,
                                                           properties={}))))


def sdk(*steps):
    pending, calls = list(steps), []

    def create(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        step = pending.pop(0) if len(pending) > 1 else pending[0]
        if isinstance(step, Exception):
            raise step
        return NS(model='FAKE-snapshot', choices=[NS(finish_reason=step, message=NS(
            content=json.dumps(dict(standards=[], notes=[])), refusal=None))],
            usage=NS(prompt_tokens=11, completion_tokens=7,
                     completion_tokens_details=NS(reasoning_tokens=3),
                     prompt_tokens_details=NS(cached_tokens=2)))

    return NS(chat=NS(completions=NS(create=create))), calls


def status_error(status, headers=None):
    response = httpx.Response(status, headers=headers or {'retry-after': '17'},
                              request=httpx.Request('POST', 'https://fake.invalid'))
    return APIStatusError('FAKE response', response=response, body={})


def timeout_error():
    return APITimeoutError(request=httpx.Request('POST', 'https://fake.invalid'))


def test_timeout_is_sent_once_recorded_and_raised(monkeypatch, offline):
    monkeypatch.setenv('AZURE_OPENAI_S10_TIMEOUT_S', '600')
    error = timeout_error()
    transport, calls = sdk(error)
    client = AzureOpenAIAdapter('S10', sdk_client=transport)
    with pytest.raises(APITimeoutError) as caught:
        client.classify(request())
    assert caught.value is error and len(calls) == 1 and offline == []
    assert calls[0]['timeout'] == 600.0
    assert client.last_http_log == [dict(status='APITimeoutError', finish_reason=None,
                                        max_completion_tokens=150, timeout_s=600.0)]


def test_timeout_ignores_retry_true_header(offline):
    error = timeout_error()
    error.response = httpx.Response(503, headers={'x-should-retry': 'true', 'retry-after': '1'})
    transport, calls = sdk(error)
    client = AzureOpenAIAdapter('S10', sdk_client=transport)
    with pytest.raises(APITimeoutError):
        client.classify(request())
    assert len(calls) == 1 and offline == []
    assert client.last_http_log[0]['status'] == 'APITimeoutError'


@pytest.mark.parametrize('steps,expected', [((429, 'stop'), 2), ((429, 429, 429, 'stop'), 3)])
def test_429_keeps_retry_bound_and_retry_after(offline, steps, expected):
    transport, calls = sdk(*(status_error(s) if isinstance(s, int) else s for s in steps))
    client = AzureOpenAIAdapter('S10', sdk_client=transport)
    if expected == 3:
        with pytest.raises(APIStatusError):
            client.classify(request())
    else:
        client.classify(request())
    assert len(calls) == expected and offline == [17.0] * (expected - 1)
    assert len(client.last_http_log) == expected


@pytest.mark.parametrize('recover', [False, True])
def test_other_connection_error_still_retries(offline, recover):
    error = APIConnectionError(request=httpx.Request('POST', 'https://fake.invalid'))
    transport, calls = sdk(error, 'stop') if recover else sdk(error)
    client = AzureOpenAIAdapter('S10', sdk_client=transport)
    if recover:
        client.classify(request())
    else:
        with pytest.raises(APIConnectionError):
            client.classify(request())
    assert len(calls) == (2 if recover else 3)
    assert len(offline) == len(calls) - 1
    assert client.last_http_log[0]['status'] == 'APIConnectionError'


@pytest.mark.parametrize('status', [408, 409, 503])
def test_other_retryable_statuses_keep_retry_policy(status, offline):
    transport, calls = sdk(status_error(status, {'retry-after-ms': '1500'}), 'stop')
    AzureOpenAIAdapter('S10', sdk_client=transport).classify(request())
    assert len(calls) == 2 and offline == [1.5]


def test_s10_timeout_doubles_only_for_truncation_rerun(monkeypatch):
    monkeypatch.setenv('AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS', '1000')
    monkeypatch.setenv('AZURE_OPENAI_S10_TIMEOUT_S', '600')
    transport, calls = sdk(status_error(429), 'length', status_error(503), 'stop')
    result = AzureOpenAIAdapter('S10', sdk_client=transport).classify(request())
    assert [c['timeout'] for c in calls] == [600.0, 600.0, 1200.0, 1200.0]
    assert [c['max_completion_tokens'] for c in calls] == [1000, 1000, 2000, 2000]
    assert [h['timeout_s'] for h in result.provenance['http_log']] == [600, 600, 1200, 1200]
    assert [a['timeout_s'] for a in result.provenance['attempts']] == [600, 1200]
    assert [a['http_attempts'] for a in result.provenance['attempts']] == [2, 2]
    assert result.provenance['http_attempts'] == 4


def test_timeout_on_truncation_rerun_is_not_retried(monkeypatch, offline):
    monkeypatch.setenv('AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS', '1000')
    monkeypatch.setenv('AZURE_OPENAI_S10_TIMEOUT_S', '600')
    transport, calls = sdk('length', timeout_error())
    client = AzureOpenAIAdapter('S10', sdk_client=transport)
    with pytest.raises(APITimeoutError):
        client.classify(request())
    assert [c['timeout'] for c in calls] == [600, 1200] and offline == []
    assert [h['status'] for h in client.last_http_log] == ['ok', 'APITimeoutError']


def test_unset_timeout_preserves_requests_and_logs(monkeypatch):
    monkeypatch.setenv('AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS', '1000')
    transport, calls = sdk('length', 'stop')
    result = AzureOpenAIAdapter('S10', sdk_client=transport).classify(request())
    assert all('timeout' not in c for c in calls)
    assert all('timeout_s' not in a for a in result.provenance['attempts'])
    assert result.provenance['http_log'] == [dict(status='ok', finish_reason='length', max_completion_tokens=1000),
                                              dict(status='ok', finish_reason='stop', max_completion_tokens=2000)]
    assert {k: v for k, v in calls[0].items() if k != 'max_completion_tokens'} == {
        k: v for k, v in calls[1].items() if k != 'max_completion_tokens'}


@pytest.mark.parametrize('role', ['S4', *S8_ROLES])
def test_s4_and_s8_behavior_is_unchanged(monkeypatch, offline, role):
    monkeypatch.setenv('AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS', '64000')
    monkeypatch.setenv('AZURE_OPENAI_S10_TIMEOUT_S', '600')
    transport, calls = sdk('length')
    client = AzureOpenAIAdapter(role, sdk_client=transport)
    result = client._complete(request(), 'FAKE.generation')
    assert len(calls) == 1 and 'timeout' not in calls[0]
    assert calls[0]['max_completion_tokens'] == (250 if role in ('s8_cypher', 's8_hyde') else 50)
    expected = dict(provider='azure_openai', model=client.model, model_version='FAKE-snapshot',
                    provisional=True, reasoning_effort='low')
    if role in S8_ROLES:
        expected.update(role=role, requested_model=client.model, response_model='FAKE-snapshot', effort='low')
    if role in ('s8_cypher', 's8_hyde'):
        expected['max_completion_tokens'] = 250
    assert result['provenance'] == expected
    transport, calls = sdk(status_error(429))
    client = AzureOpenAIAdapter(role, sdk_client=transport)
    with pytest.raises(APIStatusError):
        client._complete(request(), 'FAKE.generation')
    assert len(calls) == 1 and offline == []


def test_timeout_becomes_runner_error_with_rule_fallback(fake_data):
    transport, calls = sdk(timeout_error())
    client = AzureOpenAIAdapter('S10', sdk_client=transport, effort='medium')
    retriever = NS(retrieve=lambda *a, **kw: ([], None))
    record = R.run_one(profile_doc()['profiles'][0], R.Setting('medium', 'medium'), 1,
                       client, retriever, fake_data)
    assert record['llm_detail'] == 'error' and record['llm_status'] == 'fallback'
    assert record['llm_error'] == 'APITimeoutError' and record['http_attempts'] == len(calls) == 1
    assert record['final'] == record['rule'] and record['llm'] is None


def test_gold_v2_is_accepted(fake_data):
    profiles = F.validate_profile_set(profile_doc(), fake_data)
    doc = gold_doc()
    assert F.validate_gold(doc, profiles, fake_data) == {('FAKE-A', 'ISO 10993-5'): doc['labels'][0]}


def test_gold_v1_behavior_is_unchanged(fake_data):
    profiles = F.validate_profile_set(profile_doc(), fake_data)
    doc = gold_doc(1)
    assert F.validate_gold(doc, profiles, fake_data)[('FAKE-A', 'ISO 10993-5')] == doc['labels'][0]
    missing = copy.deepcopy(doc)
    missing['labels'][0].pop('source')
    with pytest.raises(F.FormatError, match='gold:'):
        F.validate_gold(missing, profiles, fake_data)
    doc['labels'][0]['source']['clause_id'] = 'eu-mdr:FAKE'
    with pytest.raises(F.FormatError, match='must start'):
        F.validate_gold(doc, profiles, fake_data)


@pytest.mark.parametrize('level', ['top', 'reference', 'entry'])
@pytest.mark.parametrize('change', ['extra', 'missing'])
def test_gold_v2_rejects_extra_and_missing_fields(fake_data, level, change):
    doc = gold_doc()
    target = {'top': doc, 'reference': doc['reference'], 'entry': doc['labels'][0]}[level]
    if change == 'extra':
        target['unapproved'] = 'FAKE extra'
    else:
        target.pop({'top': 'reference', 'reference': 'method', 'entry': 'label'}[level])
    with pytest.raises(F.FormatError, match='gold:'):
        F.validate_gold(doc, F.validate_profile_set(profile_doc(), fake_data), fake_data)


@pytest.mark.parametrize('field,value', [('method', 'other method'), ('merge_sha256_lf', 'short'),
                                        ('export_sha256_lf', 'z' * 64), ('export_sha256_lf', 'b' * 64 + '\n')])
def test_gold_v2_rejects_invalid_reference(fake_data, field, value):
    doc = gold_doc()
    doc['reference'][field] = value
    with pytest.raises(F.FormatError, match='gold:'):
        F.validate_gold(doc, F.validate_profile_set(profile_doc(), fake_data), fake_data)


@pytest.mark.parametrize('version', [1, 2])
@pytest.mark.parametrize('change,match', [('profile', 'unknown profile'), ('item', 'not an ISO part'),
                                         ('duplicate', 'duplicate gold cell'), ('split', 'gold split')])
def test_gold_versions_share_referential_checks(fake_data, version, change, match):
    doc = gold_doc(version)
    if change == 'profile':
        doc['labels'][0]['profile_id'] = 'FAKE-absent'
    elif change == 'item':
        doc['labels'][0]['item'] = 'FAKE absent part'
    elif change == 'duplicate':
        doc['labels'].append(copy.deepcopy(doc['labels'][0]))
    else:
        doc['split'] = 'holdout'
    with pytest.raises(F.FormatError, match=match):
        F.validate_gold(doc, F.validate_profile_set(profile_doc(), fake_data), fake_data)


@pytest.mark.parametrize('version', [1, 2])
def test_gold_versions_accept_declared_device_standard(fake_data, version):
    profile = profile_doc()
    profile['profiles'][0]['profile']['device_specific_standards'] = ['fda-knee']
    profiles = F.validate_profile_set(profile, fake_data)
    doc = gold_doc(version)
    doc['labels'][0]['item'] = 'fda-knee'
    assert ('FAKE-A', 'fda-knee') in F.validate_gold(doc, profiles, fake_data)


@pytest.mark.parametrize('profile_split,gold_split', [('dev', 'holdout'), ('holdout', 'holdout')])
def test_v2_holdout_requires_flag_and_manifest(tmp_path, fake_data, profile_split, gold_split):
    p = write_profiles(tmp_path, profile_split)
    g = write_new(tmp_path / 'G.yaml', yaml.safe_dump(gold_doc(split=gold_split)))
    with pytest.raises(SG.HoldoutRefused, match='sealed'):
        SG.load_eval_files(p, g, data=fake_data)
    with pytest.raises(SG.HoldoutRefused, match='manifest missing'):
        SG.load_eval_files(p, g, final_holdout=True, data=fake_data)
    if profile_split == gold_split:
        manifest = tmp_path / 'fake_seal.json'
        SG.seal_holdout([p, g], manifest, now='FAKE-time')
        assert SG.load_eval_files(p, g, final_holdout=True, manifest_path=manifest, data=fake_data)[0] == 'holdout'


def test_tags_cli_builds_write_once(tmp_path, fake_data, capsys):
    p, out = write_profiles(tmp_path), tmp_path / 'tags' / 'T.json'
    args = ['build', '--profiles', str(p), '--out', str(out)]
    assert T.main(args, data=fake_data) == 0
    before = out.read_bytes()
    assert b'\r' not in before
    assert T.load_tags(out) == {('FAKE-A', 'ISO 10993-1'): 'always_rule',
                                ('FAKE-A', 'ISO 10993-5'): 'matrix_settled',
                                ('FAKE-A', 'ISO 10993-9'): 'judgement'}
    output = capsys.readouterr().out
    assert normalised_sha(before) in output and 'FAKE-A' not in output and 'FAKE device' not in output
    assert T.main(args, data=fake_data) == 3 and out.read_bytes() == before


def test_tags_cli_refuses_holdout(tmp_path, fake_data):
    p, out = write_profiles(tmp_path, 'holdout'), tmp_path / 'T.json'
    assert T.main(['build', '--profiles', str(p), '--out', str(out)], data=fake_data) == 2
    assert not out.exists()


class FakeRetriever:
    def __init__(self, cache_file, *, fail=False, append=True, embedding_cache=None):
        self.cypher_cache = NS(path=Path(cache_file))
        self.query_embeddings = NS(cache=NS(path=Path(embedding_cache) if embedding_cache
                                          else Path(cache_file).with_name(FC.EMBEDDING_CACHE_NAME)),
                                   embedder=NS(api_requests=0, attempts=5))
        self.llm = NS(last_http_log=[], generate=self.generate)
        self.max_retries = 2
        self.fail, self.append = fail, append
        self.requests, self.closed = [], False

    def retrieve(self, query, top_k):
        self.requests.append((query, top_k))
        if self.append:
            self.llm.generate({'FAKE': 'request'})
            self.query_embeddings.embedder.api_requests += 1
            for path in (self.cypher_cache.path, self.query_embeddings.cache.path):
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('a' if path.exists() else 'x', encoding='utf-8', newline='\n') as stream:
                    stream.write(json.dumps({'FAKE': 'new request'}) + '\n')
        print('FAKE private dependency stdout')
        logging.getLogger('FAKE.dependency').warning('FAKE private query %s', query)
        if self.fail:
            raise RuntimeError('FAKE private exception content')
        return [dict(clause_id='FAKE:clause', doc='FAKE-doc', edition='FAKE-edition',
                     text='FAKE private candidate text')], None

    def generate(self, request):
        self.llm.last_http_log.append(dict(status='ok'))
        return dict(provenance=dict(http_attempts=1))

    def close(self):
        self.closed = True


def candidate_args(tmp_path, monkeypatch, split='dev', cache_parts=('cache',)):
    monkeypatch.setattr(provider, 'ROOT', tmp_path)
    monkeypatch.setenv('REGCLAW_RUN_DIR', 'data/runs/s10c1-fake')
    p = write_profiles(tmp_path, split)
    write_new(tmp_path / FC.PROFILES_MANIFEST_REL, json.dumps(dict(
        format='s10-profiles-manifest.v1', files={'dev_profiles.yaml': dict(
            sha256_lf=normalised_sha(p.read_bytes()))})))
    expected = {}
    for rel in FC.PROVENANCE_FILES:
        path = write_new(tmp_path / rel, 'FAKE provenance\r\n')
        if rel.startswith('standards/data/'):
            expected[path.name] = normalised_sha(path.read_bytes())
    monkeypatch.setattr(FC.ruleset, 'RULESET_FILES_SHA256', expected)
    monkeypatch.setattr(FC.ruleset, 'check_ruleset', lambda: CHECK_RULESET(tmp_path / 'standards/data'))
    args = ['build', '--profiles', str(p), '--cache-dir', str(tmp_path.joinpath(*cache_parts)),
            '--out', str(tmp_path / 'C.json'), '--manifest', str(tmp_path / 'M.json')]
    return args, tmp_path / 'data/runs/s10c1-fake' / FC.CALL_LEDGER_NAME


def test_candidates_cli_refuses_holdout_before_factory(tmp_path, fake_data, monkeypatch):
    args, ledger = candidate_args(tmp_path, monkeypatch, 'holdout')
    made = []
    assert FC.main(args, data=fake_data, retriever_factory=lambda **kw: made.append(kw)) == 2
    assert made == [] and not ledger.exists() and not (tmp_path / 'cache').exists()


@pytest.mark.parametrize('parts', [('data', 'runs', 'azure_openai', 'cache'), ('s8-cache',)])
def test_candidates_cli_refuses_guarded_cache_paths(tmp_path, fake_data, monkeypatch, parts):
    args, ledger = candidate_args(tmp_path, monkeypatch, cache_parts=parts)
    made = []
    assert FC.main(args, data=fake_data, retriever_factory=lambda **kw: made.append(kw)) == 2
    assert made == [] and not ledger.exists() and not tmp_path.joinpath(*parts).exists()


@pytest.mark.parametrize('env', [None, 'data/runs', 'data/runs/azure_openai/s10', 'data/runs/s8-fake', '../outside'])
def test_candidates_cli_requires_valid_ledger_location(tmp_path, fake_data, monkeypatch, env):
    args, _ = candidate_args(tmp_path, monkeypatch)
    if env is None:
        monkeypatch.delenv('REGCLAW_RUN_DIR')
    else:
        monkeypatch.setenv('REGCLAW_RUN_DIR', env)
    made = []
    assert FC.main(args, data=fake_data, retriever_factory=lambda **kw: made.append(kw)) == 2
    assert made == [] and not (tmp_path / 'cache').exists()


@pytest.mark.parametrize('existing', ['C.json', 'M.json'])
def test_candidates_cli_outputs_are_write_once(tmp_path, fake_data, monkeypatch, existing):
    args, ledger = candidate_args(tmp_path, monkeypatch)
    path = write_new(tmp_path / existing, 'FAKE existing output\n')
    before, made = path.read_bytes(), []
    monkeypatch.setattr(FC.ruleset, 'check_ruleset', lambda: pytest.fail('ruleset read before output refusal'))
    assert FC.main(args, data=fake_data, retriever_factory=lambda **kw: made.append(kw)) == 3
    assert made == [] and path.read_bytes() == before and not ledger.exists()


@pytest.mark.parametrize('append', [False, True])
def test_candidates_cli_counts_cache_deltas_and_ledger(tmp_path, fake_data, monkeypatch, capsys, append):
    args, ledger = candidate_args(tmp_path, monkeypatch)
    FC.append_call_ledger(ledger, step='pilot', calls=4, now='FAKE-before')
    ledger_before = ledger.read_bytes()
    cache_before = {}
    for name in (FC.CYPHER_CACHE_NAME, FC.EMBEDDING_CACHE_NAME):
        path = write_new(tmp_path / 'cache' / name, json.dumps({'FAKE': 'previous row'}) + '\n')
        cache_before[name] = path.read_bytes()
    made = []

    def factory(**kwargs):
        made.append(FakeRetriever(**kwargs, append=append))
        return made[-1]

    assert FC.main(args, data=fake_data, retriever_factory=factory, now='FAKE-after') == 0
    expected = 2 if append else 0
    assert FC.ledger_total(ledger) == 4 + expected
    assert ledger.read_bytes().startswith(ledger_before)
    rows = [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines()]
    assert rows[-1] == dict(step='candidates', calls=expected, timestamp='FAKE-after')
    assert made[0].closed and made[0].requests == [(FC.tool_query('FAKE device from this test'), 30)]
    for name, before in cache_before.items():
        assert (tmp_path / 'cache' / name).read_bytes().startswith(before)
    manifest = json.loads((tmp_path / 'M.json').read_text(encoding='utf-8'))
    assert manifest['file_sha256'] == normalised_sha((tmp_path / 'C.json').read_bytes())
    replay = FC.FrozenRetriever(tmp_path / 'C.json', manifest_path=tmp_path / 'M.json',
                                profiles=F.validate_profile_set(profile_doc(), fake_data))
    assert replay.retrieve(FC.tool_query('FAKE device from this test'))[0][0]['text'] == 'FAKE private candidate text'
    output = capsys.readouterr().out
    assert f'retrieval_calls={expected}' in output
    assert f'cypher_requests={int(append)} embedding_requests={int(append)}' in output
    assert normalised_sha((tmp_path / 'M.json').read_bytes()) in output


def test_candidates_cli_accounts_for_failed_build(tmp_path, fake_data, monkeypatch, capsys):
    args, ledger = candidate_args(tmp_path, monkeypatch)
    made = []

    def factory(**kwargs):
        made.append(FakeRetriever(**kwargs, fail=True))
        return made[-1]

    assert FC.main(args, data=fake_data, retriever_factory=factory, now='FAKE-failure') == 2
    assert FC.ledger_total(ledger) == 2 and made[0].closed
    assert not (tmp_path / 'C.json').exists() and not (tmp_path / 'M.json').exists()
    output = capsys.readouterr().out
    assert 'retrieval_calls=2' in output and 'RuntimeError' in output and 'private' not in output


@pytest.mark.parametrize('fail', [False, True])
def test_candidates_cli_prints_only_counts_and_shas(tmp_path, fake_data, monkeypatch, capsys, fail):
    args, _ = candidate_args(tmp_path, monkeypatch)
    before = logging.root.manager.disable
    assert FC.main(args, data=fake_data,
                   retriever_factory=lambda **kw: FakeRetriever(**kw, fail=fail)) == (2 if fail else 0)
    output = capsys.readouterr()
    assert 'FAKE' not in output.out and 'private' not in output.out and output.err == ''
    assert logging.root.manager.disable == before


def test_candidates_cli_rejects_invalid_ledger_before_factory(tmp_path, fake_data, monkeypatch):
    args, ledger = candidate_args(tmp_path, monkeypatch)
    write_new(ledger, json.dumps(dict(step='FAKE', calls=True, timestamp='FAKE')) + '\n')
    made = []
    assert FC.main(args, data=fake_data, retriever_factory=lambda **kw: made.append(kw)) == 2
    assert made == [] and not (tmp_path / 'cache').exists()


def test_candidates_cli_checks_factory_cache_paths(tmp_path, fake_data, monkeypatch):
    args, ledger = candidate_args(tmp_path, monkeypatch)
    made = []

    def factory(**kwargs):
        made.append(FakeRetriever(**kwargs, embedding_cache=tmp_path / 'other' / FC.EMBEDDING_CACHE_NAME))
        return made[-1]

    assert FC.main(args, data=fake_data, retriever_factory=factory) == 2
    assert made[0].requests == [] and made[0].closed and FC.ledger_total(ledger) == 0
    assert not (tmp_path / 'C.json').exists()


def test_part_a_uses_only_injected_fake_inputs(tmp_path, fake_data, monkeypatch):
    args, _ = candidate_args(tmp_path, monkeypatch)
    assert os.environ['REGCLAW_STUB'] == '1'
    assert T.main(['build', '--profiles', args[2], '--out', str(tmp_path / 'T.json')], data=fake_data) == 0
    assert FC.main(args, data=fake_data, retriever_factory=FakeRetriever) == 0


def candidate_snapshot(root):
    return {p.relative_to(root): p.read_bytes() if p.is_file() else None for p in root.rglob('*')}


@pytest.mark.parametrize('name', tuple(FC.ruleset.RULESET_FILES_SHA256))
def test_candidate_ruleset_mismatch_precedes_inputs_and_writes(tmp_path, fake_data, monkeypatch, capsys, name):
    args, _ = candidate_args(tmp_path, monkeypatch)
    (tmp_path / 'standards/data' / name).write_bytes(b'PRIVATE changed rule\n')
    monkeypatch.setattr(FC, 'verify_dev_profiles', lambda *a: pytest.fail('profiles read before ruleset check'))
    before = candidate_snapshot(tmp_path)
    assert FC.main(args, data=fake_data,
                   retriever_factory=lambda **kw: pytest.fail('retriever constructed')) == 2
    assert capsys.readouterr().out == 'ruleset_mismatch count=1\n'
    assert candidate_snapshot(tmp_path) == before


@pytest.mark.parametrize('kind', ['missing', 'malformed', 'format', 'dev-entry', 'sha', 'changed-profile'])
def test_candidate_profiles_freeze_refuses_before_calls_or_writes(tmp_path, fake_data, monkeypatch, capsys, kind):
    args, _ = candidate_args(tmp_path, monkeypatch)
    path = tmp_path / FC.PROFILES_MANIFEST_REL
    if kind in ('malformed', 'format', 'dev-entry', 'sha'):
        doc = json.loads(path.read_bytes())
        if kind == 'format':
            doc['format'] = 'PRIVATE invalid'
        elif kind == 'dev-entry':
            doc['files'] = {'P.yaml': doc['files']['dev_profiles.yaml']}
        elif kind == 'sha':
            doc['files']['dev_profiles.yaml']['sha256_lf'] = '0' * 64
        path.write_text('PRIVATE invalid {' if kind == 'malformed' else json.dumps(doc), encoding='utf-8')
    elif kind == 'changed-profile':
        p = Path(args[args.index('--profiles') + 1])
        p.write_bytes(p.read_bytes() + b'\n# PRIVATE changed\n')
    if kind == 'missing':
        original = Path.read_bytes
        def missing(p):
            if p == path:
                raise FileNotFoundError('PRIVATE missing manifest')
            return original(p)
        monkeypatch.setattr(Path, 'read_bytes', missing)
    def snapshot():
        result = {}
        for p in tmp_path.rglob('*'):
            if p.is_file():
                with p.open('rb') as stream:
                    result[p.relative_to(tmp_path)] = stream.read()
            else:
                result[p.relative_to(tmp_path)] = None
        return result
    before = snapshot()
    assert FC.main(args, data=fake_data,
                   retriever_factory=lambda **kw: pytest.fail('retriever constructed')) == 2
    diagnostic = 'profiles_sha' if kind in ('sha', 'changed-profile') else 'profiles_manifest'
    assert capsys.readouterr().out == diagnostic + ' count=1\n'
    assert snapshot() == before


def test_candidate_completion_records_lf_profile_and_all_provenance_hashes(tmp_path, fake_data, monkeypatch):
    args, ledger = candidate_args(tmp_path, monkeypatch)
    path = Path(args[args.index('--profiles') + 1])
    path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
    assert FC.main(args, data=fake_data, retriever_factory=FakeRetriever) == 0
    doc = json.loads((ledger.parent / FC.CANDIDATE_RUN_NAME).read_bytes())
    assert doc['profiles_sha256_lf'] == normalised_sha(path.read_bytes())
    assert set(doc['provenance_files']) == set(FC.PROVENANCE_FILES)
    for rel, info in doc['provenance_files'].items():
        assert info == dict(sha256_lf=normalised_sha((tmp_path / rel).read_bytes()))
    assert 'FAKE' not in json.dumps(doc) and 'PRIVATE' not in json.dumps(doc)


def test_candidate_missing_provenance_refuses_before_factory(tmp_path, fake_data, monkeypatch, capsys):
    args, _ = candidate_args(tmp_path, monkeypatch)
    original = Path.read_bytes
    def missing(path):
        if path == tmp_path / 'data/embed_identity.json':
            raise FileNotFoundError('PRIVATE identity')
        return original(path)
    before = candidate_snapshot(tmp_path)
    monkeypatch.setattr(Path, 'read_bytes', missing)
    assert FC.main(args, data=fake_data,
                   retriever_factory=lambda **kw: pytest.fail('retriever constructed')) == 2
    monkeypatch.setattr(Path, 'read_bytes', original)
    assert capsys.readouterr().out == 'candidate_provenance count=1\n'
    assert candidate_snapshot(tmp_path) == before
