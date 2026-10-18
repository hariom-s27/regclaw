"""B1 stage-1 contracts: synthetic outputs, temporary caches, no services."""
import copy
import hashlib
import json
from pathlib import Path
import socket
from types import SimpleNamespace as NS

import pytest
import yaml

from llm import provider
from llm.azure_openai import AzureOpenAIAdapter
from llm.cypher import AnthropicCypherClient, OUTPUT_SCHEMA, parse_output, request
from retrieval.cypher_cache import JsonlCache, generation_key, sha
from retrieval.hybrid_retriever import (
    CypherGenerationError, FEW_SHOTS, HybridRetriever, RunLog, SCHEMA_FALLBACK,
    canonical_schema, introspect_schema, prompt_hashes, render_fewshots, render_prompt,
)

QUERY = 'MATCH (c:Clause) RETURN DISTINCT c.id AS clause_id'
DATA = dict(cypher=QUERY, params=[dict(name='term', value='synthetic')])


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    for key in ('LLM_PROVIDER', 'CYPHER_MODEL', 'AZURE_OPENAI_S8_DEPLOYMENT',
                'AZURE_OPENAI_S8_REASONING_EFFORT'):
        monkeypatch.delenv(key, raising=False)
    def deny(*a, **kw):
        raise AssertionError('No network in B1')
    monkeypatch.setattr(socket.socket, 'connect', deny)


def anthropic_sdk(stops=('end_turn',), data=DATA):
    calls = []
    stops = iter(stops)
    def create(**kw):
        calls.append(copy.deepcopy(kw))
        return NS(content=[NS(type='text', text=json.dumps(data))],
                  stop_reason=next(stops), model='synthetic-version', usage=None)
    return NS(messages=NS(create=create)), calls


def stage1(tmp_path, *, stops=('end_turn',), data=DATA, fresh=False):
    r = HybridRetriever.__new__(HybridRetriever)
    sdk, calls = anthropic_sdk(stops, data)
    r.llm = AnthropicCypherClient(sdk)
    r.cypher_cache = JsonlCache(tmp_path / 'cypher_cache.jsonl')
    r.fresh, r.schema = fresh, SCHEMA_FALLBACK
    r.labels, r.rels, r.max_retries, r.db = {'Clause'}, set(), 0, 'neo4j'
    r._explain_is_read_only = lambda *a: None
    r._run_ids = lambda *a: ['synthetic:2', 'synthetic:1', 'synthetic:2']
    return r, calls


def read_rows(r):
    return [json.loads(line) for line in r.cypher_cache.path.read_text().splitlines()]


@pytest.mark.parametrize('selected', ['anthropic', 'azure_openai'])
def test_provider_request_schema_without_sampling_or_tools(monkeypatch, selected):
    monkeypatch.setenv('LLM_PROVIDER', selected)
    req = request('synthetic system', [dict(role='user', content='synthetic question')])
    if selected == 'anthropic':
        sdk, calls = anthropic_sdk()
        monkeypatch.setattr('anthropic.Anthropic', lambda **kw: sdk)
    else:
        calls = []
        def create(**kw):
            calls.append(kw)
            return NS(model='synthetic-azure-version', usage=None,
                      choices=[NS(finish_reason='stop', message=NS(content=json.dumps(DATA), refusal=None))])
        sdk = NS(chat=NS(completions=NS(create=create)))
        monkeypatch.setattr('openai.OpenAI', lambda **kw: sdk)
        monkeypatch.setenv('AZURE_OPENAI_ENDPOINT', 'https://offline.invalid')
        monkeypatch.setenv('AZURE_OPENAI_API_KEY', 'synthetic-key')
    client = provider.make_client('s8_cypher')
    result = client.generate(req)
    mapped = calls[0]
    assert not {'temperature', 'tool_choice', 'tools'} & mapped.keys()
    if selected == 'anthropic':
        assert mapped['output_config']['format'] == dict(type='json_schema', schema=OUTPUT_SCHEMA)
        assert mapped['model'] == 'claude-haiku-5-5'
    else:
        assert mapped['response_format']['json_schema'] == dict(name='cypher', strict=True, schema=OUTPUT_SCHEMA)
        assert mapped['model'] == 'gpt-5-mini' and mapped['reasoning_effort'] == 'low'
    details = result['provenance']
    assert details['provider'] == selected and details['role'] == 's8_cypher'
    assert details['requested_model'] == client.model and details['response_model']
    assert details['effort'] == client.effort and details['provisional'] == (selected != 'anthropic')
    assert req == request('synthetic system', [dict(role='user', content='synthetic question')])


def test_anthropic_default_and_env_overrides(monkeypatch):
    sdk, _ = anthropic_sdk()
    monkeypatch.setattr('anthropic.Anthropic', lambda **kw: sdk)
    assert isinstance(provider.make_client('s8_cypher'), AnthropicCypherClient)
    monkeypatch.setenv('CYPHER_MODEL', 'synthetic-haiku-deployment')
    assert provider.make_client('s8_cypher').model == 'synthetic-haiku-deployment'
    monkeypatch.setenv('AZURE_OPENAI_S8_DEPLOYMENT', 'synthetic-gpt-deployment')
    monkeypatch.setenv('AZURE_OPENAI_S8_REASONING_EFFORT', 'medium')
    client = AzureOpenAIAdapter('s8_cypher', sdk_client=object())
    assert (client.model, client.effort) == ('synthetic-gpt-deployment', 'medium')


def test_params_array_validation_and_duplicate_rejection():
    assert parse_output(DATA) == (QUERY, {'term': 'synthetic'})
    with pytest.raises(ValueError, match='Duplicate'):
        parse_output(dict(cypher=QUERY, params=DATA['params'] * 2))
    import jsonschema
    for bad in (dict(cypher=QUERY, params={}), dict(cypher=QUERY, params=[], extra=True),
                dict(cypher=QUERY, params=[dict(name='x', value=2)])):
        with pytest.raises(jsonschema.ValidationError):
            parse_output(bad)


@pytest.mark.parametrize('stops,success,budgets', [
    (['max_tokens', 'end_turn'], True, [800, 1600]),
    (['max_tokens', 'max_tokens'], False, [800, 1600]),
    (['refusal'], False, [800]), (['unknown'], False, [800]),
])
def test_stop_guards_and_one_budget_retry(tmp_path, stops, success, budgets):
    r, calls = stage1(tmp_path, stops=stops)
    run = RunLog('synthetic question')
    if success:
        assert r.stage1_filter(run.query, run, None) == ['synthetic:2', 'synthetic:1']
    else:
        with pytest.raises(CypherGenerationError):
            r.stage1_filter(run.query, run, None)
    assert [c['max_tokens'] for c in calls] == budgets
    assert len(read_rows(r)) == len(budgets)  # One line per generation, including stops.


def test_replay_fresh_append_and_generation_indices(tmp_path):
    r, calls = stage1(tmp_path, stops=['end_turn'] * 4)
    def run(index=0):
        log = RunLog('synthetic question', qid='SYN1', variant='b', gen_index=index)
        return r.stage1_filter(log.query, log, None), log
    first, log = run()
    before = r.cypher_cache.path.read_bytes()
    assert len(calls) == 1
    assert run()[0] == first and len(calls) == 1
    assert r.cypher_cache.path.read_bytes() == before
    r.fresh = True
    run()
    assert len(calls) == 2 and r.cypher_cache.path.read_bytes().startswith(before)
    r.fresh = False
    run(1)
    assert len(calls) == 3
    run(0)
    assert len(calls) == 3  # Scored generation is always index zero.
    rows = read_rows(r)
    assert len(rows) == 3 and [row['gen_index'] for row in rows] == [0, 0, 1]
    required = {'qid', 'variant', 'query_text', 'query_text_sha', 'gen_index',
                'prompt_template_sha', 'fewshot_sha', 'rendered_prompt_sha', 'schema_sha',
                'provider', 'role', 'requested_model', 'response_model', 'effort', 'timestamp',
                'cypher', 'params', 'attempt_no', 'validator_outcome', 'fallback_path'}
    assert required <= rows[0].keys()
    assert log.cache_key == rows[0]['key']


@pytest.mark.parametrize('field', ['qid', 'variant', 'query_text', 'query_text_sha', 'gen_index',
    'prompt_template_sha', 'fewshot_sha', 'rendered_prompt_sha', 'schema_sha',
    'provider', 'role', 'requested_model', 'effort'])
def test_every_key_field_is_required_for_replay(tmp_path, field):
    key = generation_key('synthetic query', qid='SYN1', variant='a', gen_index=0,
                         hashes=prompt_hashes(SCHEMA_FALLBACK), provider='anthropic',
                         requested_model='synthetic-haiku', effort=None)
    cache = JsonlCache(tmp_path / 'cypher_cache.jsonl')
    cache.append(dict(key=key, reusable=True))
    assert cache.find(key)
    changed = {**key, field: 'changed'}
    assert cache.find(changed) is None


def test_query_text_change_including_adhoc_hash_collision_cannot_replay(tmp_path):
    base = dict(qid='adhoc:same-key', variant='a', gen_index=0,
                hashes=prompt_hashes(SCHEMA_FALLBACK), provider='anthropic', requested_model='h', effort=None)
    first = generation_key('synthetic first', **base)
    second = generation_key('synthetic second', **base)
    cache = JsonlCache(tmp_path / 'cache.jsonl')
    cache.append(dict(key=first, reusable=True))
    assert cache.find(second) is None


def test_schema_order_is_canonical_including_obligations():
    nodes = [{'label': 'Clause', 'keys': ['text', 'id', 'obligation']}, {'label': 'Chemical', 'keys': ['name']}]
    patterns = [{'s': 'Clause', 't': 'MENTIONS', 'e': 'Chemical'}, {'s': 'Clause', 't': 'REFERENCES', 'e': 'Standard'}]
    def schema(reverse):
        values = [nodes, patterns, [{'v': ['SHOULD', 'NONE', 'SHALL', 'MAY']}]]
        if reverse:
            values = [list(reversed(nodes)), list(reversed(patterns)), [{'v': ['MAY', 'SHALL', 'NONE', 'SHOULD']}]]
            values[0] = [{**n, 'keys': list(reversed(n['keys']))} for n in values[0]]
        responses = iter(values)
        return introspect_schema(NS(execute_query=lambda *a, **kw: (next(responses), None, None)))
    assert schema(False) == schema(True)
    assert prompt_hashes(schema(False)) == prompt_hashes(schema(True))
    assert "'NONE'" in SCHEMA_FALLBACK and '(:Clause)-[:MENTIONS]->(:LifecyclePhase)' in SCHEMA_FALLBACK
    assert canonical_schema(SCHEMA_FALLBACK) == SCHEMA_FALLBACK


def test_prompt_does_not_contain_frozen_queries_or_rephrases():
    path = Path(__file__).resolve().parents[1] / 'retrieval_eval/queries.yaml'
    frozen = path.read_bytes()
    assert hashlib.sha256(frozen).hexdigest().startswith('83d78138')
    queries = yaml.safe_load(frozen)['queries']
    assert len(queries) == 16
    rendered = (render_prompt(SCHEMA_FALLBACK) + render_fewshots()).casefold()
    # Deliberately avoid including any frozen text in assertion output.
    leaked = [i for i, row in enumerate(queries) if any(row[k].casefold() in rendered for k in ('query', 'rephrase_b'))]
    assert not leaked, f'Frozen query/rephrase leakage at row indices {leaked}'
    assert len(FEW_SHOTS) == 5
    assert "The value 'other' on RegulatoryActor / LifecyclePhase / BiologicalEffect is a catch-all; filtering on it is not selective.".casefold() in rendered


def test_rejected_query_replays_generation_but_still_validates(tmp_path):
    r, calls = stage1(tmp_path, data={'cypher': 'DELETE c RETURN c.id AS clause_id', 'params': []})
    r.driver = NS(execute_query=lambda *a, **kw: ([{'clause_id': 'synthetic:1'}], None, None))
    for _ in range(2):
        run = RunLog('synthetic question')
        assert r.stage1_filter(run.query, run, None) == ['synthetic:1']
        assert run.filter_path == 'fulltext_fallback'
    assert len(calls) == 1 and read_rows(r)[0]['validator_outcome'] == 'CypherRejected'
