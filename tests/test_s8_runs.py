"""S8 Part C runner, pool, blind sheet and pre-labeller: fakes only, no services, no network."""
from contextlib import contextmanager
import csv
import hashlib
import io
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace as NS

import pytest
import yaml

from embed.embed import EmbeddingBatch, MODEL
from retrieval.hybrid_retriever import HybridRetriever
from retrieval.query_embeddings import EXPECTED
from retrieval_eval import s8_runs as m

ROOT = Path(__file__).resolve().parents[1]
FROZEN_SHA = '83d7813873d6d9e47a732db8778f68c18cfb4654834acfa16ca6847457affc7c'
FORBIDDEN_COLUMNS = {'system', 'systems', 'arm', 'variant', 'rank', 'best_rank', 'score', 'top_k',
                     'obligation', 'prompt_version', 'limit', 'repeat', 'stage'}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    for key in ('AZURE_OPENAI_S8_DEPLOYMENT', 'AZURE_OPENAI_S8_REASONING_EFFORT', 'LLM_PROVIDER'):
        monkeypatch.delenv(key, raising=False)
    def deny(*a, **kw):
        raise AssertionError('Network forbidden in S8 C1 tests')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setitem(sys.modules, 'pgvector', NS(Vector=lambda v: v))
    monkeypatch.setitem(sys.modules, 'neo4j', NS(unit_of_work=lambda **kw: lambda fn: fn))


def frozen_queries():
    return yaml.safe_load((ROOT / 'retrieval_eval/queries.yaml').read_text(encoding='utf-8'))


def synthetic_query(i):
    return dict(qid=f'SYN{i}', query=f'synthetic question {i}', rephrase_b=f'synthetic formal rephrase {i}',
                origin='test', type='test', jurisdiction='EU' if i == 1 else None, expected_sources=['syn-*'],
                description='synthetic', narrative=dict(grade3=f'synthetic top {i}', grade2=f'synthetic partial {i}',
                                                        grade1_traps=f'synthetic trap {i}'))


RULES = {name: f'synthetic rule text for {name}' for name in m.JUDGING_RULES}
IDS = ['s:1', 's:1.1', 's:2', 's:3', 's:4', 's:5', 's:late']
SCORES = {'s:1': .9, 's:1.1': .89, 's:2': .8, 's:3': .7, 's:4': .6, 's:5': .5, 's:late': .4}
CLAUSES = [dict(id=cid, doc='syn-doc', number=cid.split(':')[1], title=f'Synthetic title {cid}',
                text=f'Synthetic clause body {cid} about sterile devices.', obligation='SYNTHLABEL')
           for cid in IDS]


# ---------------------------------------------------------------- fakes
class FakeCypherLLM:
    provider, model, effort, role = 'azure_openai', 'gpt-5-mini', 'low', 's8_cypher'

    def __init__(self, early_calls):
        self.calls, self.early_calls = 0, early_calls

    def generate(self, request):
        self.calls += 1
        late = self.calls > self.early_calls
        if request['prompt_version'] == '1.0':
            where = 'c.obligation = $ob OR c.id = $late' if late else 'c.obligation = $ob AND toLower(c.text) CONTAINS $t'
            params = [dict(name='ob', value='SHALL'), dict(name='late' if late else 't', value='x')]
        else:
            where = 'c.id = $late' if late else 'toLower(c.text) CONTAINS $t'
            params = [dict(name='late' if late else 't', value='x')]
        return dict(data=dict(cypher=f'MATCH (c:Clause) WHERE {where} RETURN DISTINCT c.id AS clause_id',
                              params=params), stop_reason='end_turn',
                    provenance=dict(provider='azure_openai', role='s8_cypher', response_model='synthetic'),
                    usage=dict(input_tokens=100, output_tokens=20))


class FakeHelperLLM:
    provider, model, effort = 'azure_openai', 'gpt-5-mini', 'low'

    def __init__(self, role, data):
        self.role, self.data, self.requests = role, data, []

    def generate(self, request):
        self.requests.append(request)
        data = self.data(request) if callable(self.data) else dict(self.data)
        return dict(data=data, stop_reason='end_turn', provenance=dict(role=self.role),
                    usage=dict(input_tokens=50, output_tokens=10))


class FakeEmbedder:
    provider, model, dimensions, deployment, model_version = 'azure_openai', MODEL, 1536, MODEL, '1'

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return EmbeddingBatch([[1.] + [0.] * 1535], 3, MODEL)


def fake_driver(graph):
    def execute_query(sql, params=None, **kw):
        graph.append(sql)
        if 'UNWIND labels(n)' in sql:
            raise RuntimeError('synthetic: use fallback schema')
        if sql.startswith('EXPLAIN'):
            return [], NS(query_type='r'), None
        if sql == 'MATCH (c:Clause) RETURN c.id AS clause_id':
            return [{'clause_id': i} for i in IDS], None, None
        if 'RETURN sid' in sql:
            return [{'sid': 's:1', 'nid': 's:1.1', 'rel': 'child'}] if 's:1' in params['seeds'] else [], None, None
        if 'UNWIND $ids AS cid' in sql:
            return [dict(id=i, number='', title='', text='', obligation='', standard='') for i in params['ids']], None, None
        raise AssertionError(f'unexpected graph query {sql[:40]}')

    def rows_for(query, params):
        if 'LIMIT 5' in query:
            return []  # forces a feedback retry -> replay miss in replay-only mode
        if 'late' in params:
            ids = ['s:late', 's:1']
        elif 'obligation' in query:
            ids = ['s:1', 's:2']
        else:
            ids = ['s:1', 's:2', 's:3', 's:4']
        if ' LIMIT ' in query:
            ids = ids[:int(query.rsplit('LIMIT', 1)[1])]
        return [{'clause_id': i} for i in ids]

    @contextmanager
    def session(**kw):
        yield NS(execute_read=lambda work: work(NS(run=lambda q, p: rows_for(q, p))))
    return NS(execute_query=execute_query, session=session, close=lambda: None)


def fake_pg():
    def execute(sql, params):
        if not isinstance(sql, str):
            return NS(fetchone=lambda: (1536, 1143))
        if 'format_type' in sql:
            return NS(fetchone=lambda: ('vector(1536)',))
        ids = [i for i in params[1] if i in SCORES]
        if 'ORDER BY' in sql:
            rows = sorted(((i, SCORES[i]) for i in ids), key=lambda r: (-r[1], r[0]))[:params[3]]
        else:
            rows = [(i, SCORES[i]) for i in ids]
        return NS(fetchall=lambda: rows)
    return NS(execute=execute, close=lambda: None)


def identity(tmp_path):
    path = tmp_path / 'embed_identity.json'
    path.write_text(json.dumps({**EXPECTED, 'model_version': '1', 'input_version': 'v1', 'clause_vectors': 1143}))
    return path


class Env:
    """Two synthetic queries, 2 generations, real retriever code on fake services."""

    def __init__(self, tmp_path, *, budget=1000, generations=2):
        self.tmp, self.out = tmp_path, tmp_path / 'c'
        self.queries = [synthetic_query(1), synthetic_query(2)]
        self.generations = generations
        self.early = len(m.ARMS) * len(self.queries) * len(m.GEN_VARIANTS)  # gen-0 calls
        self.cypher = FakeCypherLLM(self.early)
        self.hyde = FakeHelperLLM('s8_hyde', lambda req: {'passage': 'hypothetical ' + req['messages'][0]['content']})
        self.embedder = FakeEmbedder()
        self.graph = []
        self.hyde_system, self.hyde_record = m.load_frozen(m.HYDE_PROMPT, m.HYDE_FREEZE, m.HYDE_SCHEMA)
        self.budget = budget

    def retriever(self, *, replay_only=False, budget=None):
        ledger = m.Ledger(self.out / 'cost_ledger.jsonl')
        llm = m.MeteredClient(self.cypher, ledger, self.budget if budget is None else budget,
                              replay_only=replay_only, sleep=lambda s: None)
        embedder = m.MeteredEmbedder(self.embedder, ledger, 0 if replay_only else 60)
        r = HybridRetriever(driver=fake_driver(self.graph), pg=fake_pg(), llm=llm, embedder=embedder,
                            manifest_path=identity(self.tmp), cache_file=self.out / 'cypher_cache.jsonl')
        if replay_only:
            r.cypher_cache = m.ReplayOnlyCache(r.cypher_cache.path, self.out / 'replay_misses.jsonl')
        hyde = m.MeteredClient(self.hyde, ledger, 0 if replay_only else 16, replay_only=replay_only,
                               sleep=lambda s: None)
        return r, hyde, ledger

    def generate(self, **kw):
        r, hyde, ledger = self.retriever(**kw)
        result = m.generate(r, self.queries, self.out, hyde_client=hyde, hyde_system=self.hyde_system,
                            hyde_record=self.hyde_record, generations=self.generations)
        return result, ledger

    def pool(self):
        r, hyde, ledger = self.retriever(replay_only=True)
        return m.run_pool(r, self.queries, CLAUSES, self.out, hyde_record=self.hyde_record, hyde_client=hyde), ledger


def snapshot(folder):
    return {p.name: p.read_bytes() for p in sorted(Path(folder).glob('*.jsonl'))}


# ---------------------------------------------------------------- C1-1
def test_queries_sha_is_line_ending_independent(tmp_path):
    raw = (ROOT / 'retrieval_eval/queries.yaml').read_bytes().replace(b'\r\n', b'\n')
    crlf = tmp_path / 'queries.yaml'
    crlf.write_bytes(raw.replace(b'\n', b'\r\n'))
    assert hashlib.sha256(crlf.read_bytes()).hexdigest() != FROZEN_SHA
    assert m.file_sha(crlf) == m.file_sha(ROOT / 'retrieval_eval/queries.yaml') == FROZEN_SHA


# ---------------------------------------------------------------- frozen prompts
@pytest.mark.parametrize('prompt,freeze,schema,role', [
    (m.HYDE_PROMPT, m.HYDE_FREEZE, m.HYDE_SCHEMA, 's8_hyde'),
    (m.PRELABEL_PROMPT, m.PRELABEL_FREEZE, m.PRELABEL_SCHEMA, 's8_prelabel'),
])
def test_helper_prompts_match_freeze_and_do_not_leak_queries(prompt, freeze, schema, role):
    text, record = m.load_frozen(prompt, freeze, schema)
    assert record['status'] == 'FROZEN' and record['role'] == role
    assert record['effort'] == 'low' and record['provisional_model'] == 'gpt-5-mini'
    assert record['schema'] == schema
    queries = frozen_queries()['queries']
    lowered = text.casefold()
    leaked = [i for i, q in enumerate(queries) if q['query'].casefold() in lowered
              or q['rephrase_b'].casefold() in lowered]
    assert not leaked, f'Frozen query/rephrase leakage at row indices {leaked}'


def test_changed_prompt_fails_freeze(tmp_path):
    copy = tmp_path / 'prompt.md'
    copy.write_bytes(m.HYDE_PROMPT.read_bytes() + b'extra rule\n')
    with pytest.raises(RuntimeError, match='freeze record'):
        m.load_frozen(copy, m.HYDE_FREEZE, m.HYDE_SCHEMA)


def test_prelabel_schema_field_order_and_zero_shot():
    assert m.PRELABEL_FIELDS == ('query_asks', 'clause_states', 'jurisdiction_match', 'evidence_span', 'grade')
    props = m.PRELABEL_SCHEMA['properties']
    assert props['clause_states']['enum'] == ['answers', 'partial', 'mentions_only', 'unrelated']
    assert props['jurisdiction_match']['enum'] == ['yes', 'no', 'not_applicable']
    assert props['grade']['enum'] == [0, 1, 2, 3]
    assert 'confidence' not in json.dumps(m.PRELABEL_SCHEMA)
    text, record = m.load_frozen(m.PRELABEL_PROMPT, m.PRELABEL_FREEZE, m.PRELABEL_SCHEMA)
    assert record['shots'] == 0 and record['items_per_call'] == 1
    assert 'choose grade 1' in text and "query's words without answering" in text
    assert 'confidence' not in text.casefold() and 'judges' not in text.replace('human judges', '')


# ---------------------------------------------------------------- plan
def test_generation_plan_is_640_with_gen0_first():
    queries = [synthetic_query(i) for i in range(16)]
    plan = m.generation_plan(queries)
    assert len(plan) == 640 == len({m.slot_key(s) for s in plan})
    assert {s['arm'] for s in plan} == {'v1', 'v1.1'} and {s['variant'] for s in plan} == {'a', 'b'}
    first = plan[:2 * 16 * 2]
    assert all(s['gen_index'] == 0 for s in first) and all(s['gen_index'] > 0 for s in plan[len(first):])
    assert {s['prompt_version'] for s in plan if s['arm'] == 'v1'} == {'1.0'}


# ---------------------------------------------------------------- v1-noob
TAIL = ' RETURN DISTINCT c.id AS clause_id'


@pytest.mark.parametrize('where,status,expected', [
    ('WHERE c.obligation = $ob AND toLower(c.text) CONTAINS $t', 'rewritten',
     'WHERE true AND toLower(c.text) CONTAINS $t'),
    ('WHERE toLower(c.text) CONTAINS $t AND c . obligation=$level', 'rewritten',
     'WHERE toLower(c.text) CONTAINS $t AND true'),
    ('WHERE c.obligation = $ob', 'rewritten', 'WHERE true'),
    ('WHERE (toLower(c.text) CONTAINS $a OR toLower(c.title) CONTAINS $a) AND c.obligation = $ob', 'rewritten',
     'WHERE (toLower(c.text) CONTAINS $a OR toLower(c.title) CONTAINS $a) AND true'),
    ('WHERE c.obligation = $ob OR toLower(c.text) CONTAINS $t', 'n/a', None),
    ('WHERE toLower(c.text) CONTAINS $t OR c.obligation = $ob', 'n/a', None),
    ('WHERE c.obligation = $ob AND toLower(c.text) CONTAINS $t OR c.id = $i', 'n/a', None),
    ('WHERE (c.obligation = $ob AND toLower(c.text) CONTAINS $t)', 'n/a', None),
    ('WHERE toLower(c.text) CONTAINS $t AND (c.obligation = $ob OR c.id = $i)', 'n/a', None),
    ('WHERE NOT c.obligation = $ob', 'n/a', None),
    ('WHERE EXISTS { MATCH (c)-[:BELONGS_TO]->(s:Standard) WHERE c.obligation = $ob }', 'n/a', None),
    ('WHERE c.obligation = $a AND c.obligation = $b', 'n/a', None),
    ("WHERE c.obligation = 'SHALL'", 'n/a', None),
    ('WHERE c.obligation IN $levels', 'n/a', None),
    ('WHERE $ob = c.obligation', 'n/a', None),
    ('WHERE toLower(c.text) CONTAINS $t', 'equal_v1', None),
])
def test_noob_rewrite_rule(where, status, expected):
    got_status, rewritten, reason = m.noob_rewrite('MATCH (c:Clause) ' + where + TAIL)
    assert got_status == status, reason
    assert rewritten == (None if expected is None else 'MATCH (c:Clause) ' + expected + TAIL)


def test_noob_ignores_obligation_inside_strings_and_fallback_paths():
    assert m.noob_rewrite("MATCH (c:Clause) WHERE c.title = '.obligation' RETURN c.id AS clause_id")[0] == 'equal_v1'
    fulltext = "CALL db.index.fulltext.queryNodes('clause_text_ft', $q) YIELD node, score RETURN node.id AS clause_id"
    assert m.noob_rewrite(fulltext)[0] == 'equal_v1' and m.noob_rewrite('')[0] == 'equal_v1'


# ---------------------------------------------------------------- end to end on fakes
def test_generate_pool_replay_and_gen0_only(tmp_path):
    env = Env(tmp_path)
    result, ledger = env.generate()
    assert result['stopped'] is None and result['slots_written'] == 16
    assert env.cypher.calls == 16 and len(env.hyde.requests) == 2 and env.embedder.calls == 6
    gens = m.read_jsonl(env.out / 'generations.jsonl')
    assert len(gens) == 16 and all(g['status'] == 'ok' for g in gens)
    assert all(g['obligation_filter'] == (g['arm'] == 'v1') for g in gens)
    noob = {(n['variant'], n['gen_index'], n['qid']): n['status'] for n in m.read_jsonl(env.out / 'noob.jsonl')}
    assert len(noob) == 8 and {s for (v, g, q), s in noob.items() if g == 0} == {'rewritten'}
    assert {s for (v, g, q), s in noob.items() if g == 1} == {'n/a'}  # obligation inside OR
    for request in env.hyde.requests:  # HyDE sees only the variant-a text
        assert request['messages'] == [{'role': 'user', 'content': request['messages'][0]['content']}]
        content = request['messages'][0]['content']
        assert content.startswith('Question: synthetic question ') and 'rephrase' not in content
        assert 'synthetic top' not in content and 'Synthetic clause' not in content
    report = m.generation_report(gens, m.read_jsonl(env.out / 'noob.jsonl'), env.queries)
    assert report['v1']['stability']['SYN1/a'] == dict(gens=2, cypher=2, candidate_sets=2, obligation_gens=2)
    assert report['v1-noob']['gen0'] == {'rewritten': 4}

    before = snapshot(env.out)
    pooled, pool_ledger = env.pool()
    json.dumps(pooled)  # the CLI prints this summary
    assert env.cypher.calls == 16 and env.embedder.calls == 6 and len(env.hyde.requests) == 2
    assert pooled['states'] == dict.fromkeys(('runs.jsonl', 'pool_key.jsonl', 'pool.jsonl'), 'written')
    assert snapshot(env.out)['cypher_cache.jsonl'] == before['cypher_cache.jsonl']
    runs = m.read_jsonl(env.out / 'runs.jsonl')
    assert all(r['gen_index'] == 0 for r in runs)
    keys = {(r['system'], r['arm'], r['variant'], r['top_k']) for r in runs if r.get('limit') is None}
    for arm in ('v1', 'v1.1', 'v1-noob'):
        for variant in 'abc':
            assert {('pipeline', arm, variant, 10), ('pipeline', arm, variant, 20)} <= keys
    assert {('vector_all', None, 'a', 20), ('bm25', None, 'b', 20), ('hyde', None, 'c', 20)} <= keys
    limit_runs = [r for r in runs if r['system'] == 'limit']
    assert len(limit_runs) == 2 * len(m.LIMITS) * m.LIMIT_REPEATS * len(m.TOP_KS)
    assert {r['status'] for r in limit_runs if r['limit'] == 5} == {'replay_miss'}
    assert {r['status'] for r in limit_runs if r['limit'] != 5} == {'ok'}
    assert (env.out / 'replay_misses.jsonl').exists()
    v1a = next(r for r in runs if (r['system'], r['arm'], r['variant'], r['top_k']) == ('pipeline', 'v1', 'a', 10))
    assert v1a['ranked_ids'] == ['s:1', 's:2'] and v1a['neighbour_ids'] == ['s:1.1']
    assert v1a['final_ids'] == v1a['ranked_ids'] + v1a['neighbour_ids']
    pool = m.read_jsonl(env.out / 'pool.jsonl')
    assert pool and all(set(row) == {'qid', 'clause_id'} for row in pool)
    # Only generation 1 Cypher found s:late; no pipeline / LIMIT run (all gen 0) may contain it.
    assert not any('s:late' in (r['final_ids'] or []) for r in runs if r['system'] in ('pipeline', 'limit'))
    assert any('s:late' in g['candidate_ids'] for g in gens if g['gen_index'] == 1)
    assert pooled['pool']['total'] == len(pool)
    key = m.read_jsonl(env.out / 'pool_key.jsonl')
    assert [(k['qid'], k['clause_id']) for k in key] == [(p['qid'], p['clause_id']) for p in pool]
    assert {e['stage'] for k in key for e in k['provenance']} == {'ranked', 'neighbour'}

    # Resumability: a rerun of both commands makes 0 calls and reproduces every file.
    snap = snapshot(env.out)
    again, _ = env.generate()
    assert again.get('slots_written', 0) == 0 and env.cypher.calls == 16 and env.embedder.calls == 6
    repooled, _ = env.pool()
    assert repooled['states'] == dict.fromkeys(('runs.jsonl', 'pool_key.jsonl', 'pool.jsonl'), 'unchanged')
    after = snapshot(env.out)
    assert {k: v for k, v in after.items() if k != 'replay_misses.jsonl'} == \
        {k: v for k, v in snap.items() if k != 'replay_misses.jsonl'}
    assert env.cypher.calls == 16 and len(env.hyde.requests) == 2


def test_budget_stop_and_resume_without_duplicates(tmp_path):
    env = Env(tmp_path, budget=3)
    result, ledger = env.generate()
    assert result['stopped'] and 'budget' in result['stopped']
    assert env.cypher.calls == 3 and ledger.count('s8_cypher') == 3
    assert len(m.read_jsonl(env.out / 'generations.jsonl')) == 3
    env.budget = 1000
    result, ledger = env.generate()
    assert result['stopped'] is None and env.cypher.calls == 16 and ledger.count('s8_cypher') == 16
    gens = m.read_jsonl(env.out / 'generations.jsonl')
    assert len(gens) == len({m.slot_key(g) for g in gens}) == 16


def test_rate_limit_backoff_uses_retry_after_and_counts_requests(tmp_path):
    class RateLimited(Exception):
        status_code = 429
        response = NS(headers={'retry-after': '7'})
    inner = FakeHelperLLM('s8_hyde', {'passage': 'x'})
    calls = {'n': 0}
    real = inner.generate
    def flaky(request):
        calls['n'] += 1
        if calls['n'] == 1:
            raise RateLimited()
        return real(request)
    inner.generate = flaky
    slept = []
    ledger = m.Ledger(tmp_path / 'ledger.jsonl')
    client = m.MeteredClient(inner, ledger, 5, sleep=slept.append, pacer=m.Pacer(sleep=slept.append))
    assert client.generate({'x': 1})['data'] == {'passage': 'x'}
    assert slept == [7.0] and [r['status'] for r in ledger.rows] == ['rate_limited', 'ok']
    with pytest.raises(m.BudgetExceeded):
        m.MeteredClient(inner, ledger, 2, sleep=slept.append).generate({'x': 1})
    assert m.ReplayMiss.__mro__[1].__name__ == 'CypherGenerationError'


def test_pacer_waits_for_tpm_window():
    now = [0.0]
    slept = []
    def sleep(s):
        slept.append(s)
        now[0] += s
    pacer = m.Pacer(rpm=100, tpm=1000, clock=lambda: now[0], sleep=sleep)
    pacer.record(850)
    pacer.wait(100)
    assert slept and now[0] >= 60


def test_pool_union_covers_k10_k20_and_limit_runs_gen0_only():
    queries = [synthetic_query(1)]
    def run(system, top_k, ids, **extra):
        return dict(qid='SYN1', system=system, arm='v1', variant='a', top_k=top_k, gen_index=0, status='ok',
                    ranked_ids=ids, neighbour_ids=[], final_ids=ids, **extra)
    runs = [run('pipeline', 10, ['s:1', 's:only10']), run('pipeline', 20, ['s:1', 's:2']),
            run('limit', 10, ['s:limit'], limit=5, repeat=0),
            dict(run('pipeline', 10, ['s:x']), status='n/a', final_ids=None)]
    pool, key = m.pool_from_runs(runs, queries)
    assert {p['clause_id'] for p in pool} == {'s:1', 's:only10', 's:2', 's:limit'}
    assert len(next(k for k in key if k['clause_id'] == 's:1')['provenance']) == 2
    with pytest.raises(ValueError, match='gen_index 0'):
        m.pool_from_runs([dict(runs[0], gen_index=1)], queries)
    assert m.included_pairs(key, depth=1) == [('SYN1', 's:1'), ('SYN1', 's:limit')]


def test_write_once_never_overwrites(tmp_path):
    path = tmp_path / 'pool.jsonl'
    assert m.write_once(path, 'a\n') == 'written' and m.write_once(path, 'a\n') == 'unchanged'
    with pytest.raises(FileExistsError):
        m.write_once(path, 'b\n')
    assert path.read_text() == 'a\n'


# ---------------------------------------------------------------- sheet
def test_blind_sheet_columns_ids_and_determinism():
    queries = [synthetic_query(1), synthetic_query(2)]
    pairs = [('SYN1', c) for c in IDS[:5]] + [('SYN2', c) for c in IDS[2:]]
    rows, item_map = m.build_sheet(queries, RULES, CLAUSES, pairs)
    assert not FORBIDDEN_COLUMNS & set(m.SHEET_COLUMNS)
    assert all(list(row) == m.SHEET_COLUMNS for row in rows) and len(rows) == len(pairs)
    for row in rows:
        cid = row['clause_id']
        assert row['item_id'].startswith('S8-') and cid not in row['item_id']
        assert hashlib.sha256(cid.encode()).hexdigest()[:10] not in row['item_id']
        assert row['grade'] == row['note'] == ''
    assert len({r['item_id'] for r in rows}) == len(rows)
    for qid in ('SYN1', 'SYN2'):
        block = [r for r in rows if r['qid'] == qid]
        assert block[0]['common_rules'] and not any(r['common_rules'] for r in block[1:])
    assert [r['clause_id'] for r in rows if r['qid'] == 'SYN1'] != sorted(IDS[:5])  # shuffled
    again, again_map = m.build_sheet(queries, RULES, CLAUSES, pairs)
    assert again == rows and again_map == item_map
    assert {(i['qid'], i['clause_id']) for i in item_map} == set(pairs)
    text = m.sheet_csv(rows)
    assert text.startswith('﻿')
    header = next(csv.reader(io.StringIO(text.lstrip('﻿'))))
    assert header == m.SHEET_COLUMNS
    with pytest.raises(ValueError, match='missing'):
        m.build_sheet(queries, RULES, CLAUSES, [('SYN1', 's:unknown')])


# ---------------------------------------------------------------- pre-labels
def test_prelabel_request_is_blind_and_replays(tmp_path):
    out = tmp_path / 'c'
    queries = [synthetic_query(1)]
    pairs = [('SYN1', 's:1'), ('SYN1', 's:2')]
    rows, item_map = m.build_sheet(queries, RULES, CLAUSES, pairs)
    m.write_once(out / 'sheet' / 'item_map.jsonl', m.jsonl_text(item_map))
    system, record = m.load_frozen(m.PRELABEL_PROMPT, m.PRELABEL_FREEZE, m.PRELABEL_SCHEMA)
    data = dict(query_asks='synthetic', clause_states='partial', jurisdiction_match='no',
                evidence_span='none', grade=1)
    fake = FakeHelperLLM('s8_prelabel', data)
    ledger = m.Ledger(out / 'cost_ledger.jsonl')
    client = m.MeteredClient(fake, ledger, 3, sleep=lambda s: None)
    result = m.run_prelabels(queries, RULES, CLAUSES, out, client, system, record)
    assert result['labelled'] == 2 and result['grade_distribution'] == {1: 2} and len(fake.requests) == 2
    for request in fake.requests:
        assert set(request) == {'system', 'messages', 'max_tokens', 'output_config'}
        assert request['output_config']['format']['schema'] == m.PRELABEL_SCHEMA
        content = request['messages'][0]['content']
        for leak in ('SYNTHLABEL', 'pipeline', 'vector_all', 'bm25', 'hyde', 'v1-noob', 'top_k',
                     'rank:', 'score:', 'item_id', 'S8-', 'clause_id', 'obligation:'):
            assert leak not in content
        assert 'CLAUSE:' in content and 'synthetic trap 1' in content and RULES['jurisdiction'] in content
    labels = m.read_jsonl(out / 'prelabels.jsonl')
    assert all(set(m.PRELABEL_FIELDS) <= set(row) and 'reason' not in row for row in labels)
    before = (out / 'prelabels.jsonl').read_bytes()
    replay = m.run_prelabels(queries, RULES, CLAUSES, out, client, system, record)
    assert replay['replayed'] == 2 and len(fake.requests) == 2 and (out / 'prelabels.jsonl').read_bytes() == before


# ---------------------------------------------------------------- roles
# Golden request shas of the pre-Addendum-3 adapter (HEAD 0f7105d), synthetic inputs.
OLD_GOLDEN = {
    ('s8_cypher', '1.0', 800): 'bab500ad0fafeab5a94e34c9f22981d3cae753e61517a9593b1275a36781e26b',
    ('s8_cypher', '1.0', 1600): '4142e7183c0df27244f95e281ee10e6e6025550cbb2a3ec140057978371dcb98',
    ('s8_cypher', '1.1', 800): 'dca0077ebdd23c9bbd71ccc7c733c3bb20f204044632433bd0b7eb482e796de4',
    ('s8_cypher', '1.1', 1600): '2d509511d0b58df3c11f90747a8b5e675633dc569f2d24ecc3108ef7941505f6',
}


def mapped_sha(mapped):
    return hashlib.sha256(json.dumps(mapped, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def test_s8_cypher_azure_request_changes_only_the_token_cap():
    """The only intended change vs 0f7105d (Addendum 3) is max_completion_tokens:
    800 -> 4,000 and 1,600 -> 8,000. Everything else in the request is byte-identical."""
    from llm.azure_openai import AzureOpenAIAdapter
    from llm.cypher import request
    from retrieval.hybrid_retriever import SCHEMA_FALLBACK, render_prompt
    adapter = AzureOpenAIAdapter('s8_cypher', sdk_client=NS())
    new_golden = {('1.0', 800): '34338af0c53042292011bf8b76bf8ab9ba053f572e02cbecf250f47b455d72de',
                  ('1.0', 1600): '8a8b4900dae0991d388c483d8ad0c346466c2727db27e6ade307f3db47246f1e',
                  ('1.1', 800): '0fbaf40507fe53742e2495adf76410b3e6508f7251cc5110a12d189b44143e5f',
                  ('1.1', 1600): '53ab365e1c1455049113742d65225d88e88641235542876aaea8ee208008d9ae'}
    for (version, budget), expected in new_golden.items():
        req = request(render_prompt(SCHEMA_FALLBACK, version),
                      [dict(role='user', content='Question: synthetic question')], max_tokens=budget)
        mapped = adapter.map_request(req)
        assert mapped['max_completion_tokens'] == 5 * budget
        assert mapped_sha(mapped) == expected
        assert mapped_sha({**mapped, 'max_completion_tokens': budget}) == OLD_GOLDEN[('s8_cypher', version, budget)]


def test_s4_s10_and_prelabel_azure_requests_are_byte_identical(monkeypatch):
    from llm.azure_openai import AzureOpenAIAdapter
    from ner.client import request as ner_request
    from standards.classifier import MODEL, OUTPUT_SCHEMA, PROMPT
    monkeypatch.setenv('AZURE_OPENAI_S4_DEPLOYMENT', 'synthetic-s4')
    monkeypatch.setenv('AZURE_OPENAI_S10_DEPLOYMENT', 'synthetic-s10')
    for key in ('AZURE_OPENAI_S4_REASONING_EFFORT', 'AZURE_OPENAI_S10_REASONING_EFFORT'):
        monkeypatch.delenv(key, raising=False)
    clause = dict(id='syn:1', doc='syn-doc', text='Synthetic clause text.')
    s10 = dict(model=MODEL, max_tokens=8000, system=PROMPT,
               messages=[{'role': 'user', 'content': json.dumps({'synthetic': True})}],
               output_config={'format': {'type': 'json_schema', 'schema': OUTPUT_SCHEMA}, 'effort': 'low'})
    cases = [
        (AzureOpenAIAdapter('S4', mode='structured', sdk_client=NS()), ner_request(clause, mode='structured'),
         '7cafd8949399a40e591196b92edbc81313690b579ff2172c14edb33067706448'),
        (AzureOpenAIAdapter('S4', mode='strict_tool', sdk_client=NS()), ner_request(clause, mode='strict_tool'),
         '1f3cea1d441ef1ce7922cbae6be6ac057d41e6a6d55ce0593f3c4f085a116fb3'),
        (AzureOpenAIAdapter('S10', sdk_client=NS()), s10,
         '37a63e1c1229efc81ef513f8ed710962e711217fbade9de4231ffcddae23b24e'),
        (AzureOpenAIAdapter('s8_prelabel', sdk_client=NS()),
         m.structured_request('synthetic system', 'synthetic', m.PRELABEL_SCHEMA, 800),
         '4c19d0096796017a9a2e7b4b1c65ac5c94582bce464a765e07223990e5cefd21'),
    ]
    for adapter, req, expected in cases:
        assert mapped_sha(adapter.map_request(req)) == expected, adapter.role


@pytest.mark.parametrize('effort', ['minimal', 'low', 'medium', 'high'])
@pytest.mark.parametrize('role,scaled', [('s8_cypher', True), ('s8_hyde', True), ('s8_prelabel', False)])
def test_s8_completion_budget_per_role(monkeypatch, effort, role, scaled):
    from llm.azure_openai import AzureOpenAIAdapter
    monkeypatch.setenv('AZURE_OPENAI_S8_REASONING_EFFORT', effort)
    adapter = AzureOpenAIAdapter(role, sdk_client=NS())
    effort_scale = {'minimal': 1, 'low': 1, 'medium': 2, 'high': 3}[effort]
    for budget in (800, 1600):
        req = m.structured_request('synthetic system', 'synthetic', m.HYDE_SCHEMA, budget)
        assert adapter.map_request(req)['max_completion_tokens'] == budget * (5 if scaled else effort_scale)


def test_s8_provenance_records_the_real_cap():
    from llm.azure_openai import AzureOpenAIAdapter
    def create(**kw):
        return NS(model='synthetic', usage=None,
                  choices=[NS(finish_reason='stop', message=NS(content='{"passage": "x"}', refusal=None))])
    sdk = NS(chat=NS(completions=NS(create=create)))
    req = m.structured_request('synthetic system', 'synthetic', m.HYDE_SCHEMA, 800)
    assert AzureOpenAIAdapter('s8_hyde', sdk_client=sdk).generate(req)['provenance']['max_completion_tokens'] == 4000
    assert 'max_completion_tokens' not in AzureOpenAIAdapter('s8_prelabel', sdk_client=sdk).generate(req)['provenance']


def test_run_family_is_c2():
    assert m.FAMILY == 'c2'
    assert m.output_dir() == ROOT / 'data' / 'runs' / 'azure_openai' / 's8' / 'c2'


class TruncatingCypherLLM(FakeCypherLLM):
    """Always truncates the v1 prompt for SYN2 variant a: a terminal failure under the stop guard."""

    def generate(self, request):
        content = request['messages'][-1]['content']
        if request['prompt_version'] == '1.0' and 'synthetic question 2' in content:
            self.calls += 1
            return dict(data={}, stop_reason='max_tokens', provenance=dict(role='s8_cypher'),
                        usage=dict(input_tokens=100, output_tokens=4000))
        return super().generate(request)


def test_terminal_failure_is_a_system_failure_with_empty_runs(tmp_path):
    env = Env(tmp_path, generations=1)
    env.cypher = TruncatingCypherLLM(early_calls=10 ** 6)
    env.generate()
    gens = m.read_jsonl(env.out / 'generations.jsonl')
    failed = [g for g in gens if g['status'] == 'generation_error']
    assert [(g['arm'], g['qid'], g['variant']) for g in failed] == [('v1', 'SYN2', 'a')]
    calls = env.cypher.calls
    env.pool()
    assert env.cypher.calls == calls  # never regenerated
    runs = [r for r in m.read_jsonl(env.out / 'runs.jsonl') if r['qid'] == 'SYN2']
    failures = [r for r in runs if r['status'] == 'system_failure']
    assert failures and all(r['ranked_ids'] == r['neighbour_ids'] == r['final_ids'] == [] for r in failures)
    assert {(r['system'], r['arm'], r['variant']) for r in failures} == {
        ('pipeline', 'v1', 'a'), ('pipeline', 'v1', 'c'), ('pipeline', 'v1-noob', 'a'),
        ('pipeline', 'v1-noob', 'c'), ('limit', 'v1', 'a')}
    assert len([r for r in failures if r['system'] == 'limit']) == len(m.LIMITS) * m.LIMIT_REPEATS * len(m.TOP_KS)
    assert not any(r['status'] == 'replay_miss' and r['system'] == 'pipeline' for r in runs)
    ok = {(r['system'], r['arm'], r['variant']) for r in runs if r['status'] == 'ok'}
    assert {('pipeline', 'v1', 'b'), ('pipeline', 'v1.1', 'a'), ('vector_all', None, 'a')} <= ok


@pytest.mark.parametrize('role,name,span', [('s8_hyde', 'hyde_passage', 'hyde.generate'),
                                            ('s8_prelabel', 'prelabel', 'prelabel.generate')])
def test_helper_roles_share_s8_deployment_and_effort(monkeypatch, role, name, span):
    from llm import azure_openai, provider
    calls = []
    def create(**kw):
        calls.append(kw)
        return NS(model='synthetic-azure-version', usage=None,
                  choices=[NS(finish_reason='stop', message=NS(content='{"passage": "x"}', refusal=None))])
    adapter = azure_openai.AzureOpenAIAdapter(role, sdk_client=NS(chat=NS(completions=NS(create=create))))
    assert (adapter.model, adapter.effort) == ('gpt-5-mini', 'low')
    result = adapter.generate(m.structured_request('synthetic system', 'synthetic', m.HYDE_SCHEMA, 800))
    assert calls[0]['response_format']['json_schema']['name'] == name and calls[0]['reasoning_effort'] == 'low'
    assert result['provenance']['role'] == role and result['provenance']['provisional'] is True
    assert azure_openai.SPAN_NAMES[role] == span and azure_openai.SPAN_NAMES['s8_cypher'] == 'cypher.generate'
    monkeypatch.setenv('AZURE_OPENAI_S8_DEPLOYMENT', 'synthetic-deployment')
    monkeypatch.setenv('AZURE_OPENAI_S8_REASONING_EFFORT', 'medium')
    assert (azure_openai.AzureOpenAIAdapter(role, sdk_client=NS()).model,
            azure_openai.AzureOpenAIAdapter(role, sdk_client=NS()).effort) == ('synthetic-deployment', 'medium')
    monkeypatch.setenv('LLM_PROVIDER', 'anthropic')
    with pytest.raises(ValueError, match='provisional Azure evaluation helper'):
        provider.make_client(role)


# ---------------------------------------------------------------- per-query sheets + practice
def read_csv(path):
    return list(csv.DictReader(io.StringIO(Path(path).read_text(encoding='utf-8-sig'))))


def test_sheet_writes_per_query_csvs_with_same_columns_and_rows(tmp_path):
    env = Env(tmp_path, generations=1)
    env.generate()
    env.pool()
    result = m.run_sheet(env.queries, RULES, CLAUSES, env.out)
    folder = env.out / 'sheet'
    main = read_csv(folder / 's8_sheet.csv')
    assert result['items'] == len(main) and set(result['per_query']) == {'SYN1', 'SYN2'}
    union = []
    for q in env.queries:
        path = folder / 'per_query' / f"s8_sheet_{q['qid']}.csv"
        assert path.read_bytes().startswith('﻿'.encode('utf-8'))
        rows = read_csv(path)
        assert rows and list(rows[0]) == m.SHEET_COLUMNS and {r['qid'] for r in rows} == {q['qid']}
        assert rows[0]['common_rules'] and not any(r['common_rules'] for r in rows[1:])
        union += rows
    assert union == main
    again = m.run_sheet(env.queries, RULES, CLAUSES, env.out)
    assert set(again['states'].values()) - {'skipped (openpyxl not installed)'} == {'unchanged'}


def test_practice_questions_are_never_eval_queries():
    frozen = frozen_queries()['queries']
    practice = m.practice_queries(frozen)
    assert [p['qid'] for p in practice] == ['P1', 'P2', 'P3'] and all(p['jurisdiction'] is None for p in practice)
    assert practice[0]['query'] == 'cytotoxicity test requirements for wound dressings'
    assert practice[2]['narrative']['grade2'] == "requires an investigator's brochure or names part of its content"
    clash = [dict(synthetic_query(9), rephrase_b='Mandatory pyrogenicity testing for infusion sets')]
    with pytest.raises(ValueError, match='overlaps'):
        m.practice_queries(clash)


def test_practice_sheet_is_vector_all_top5_outside_the_pool(tmp_path):
    env = Env(tmp_path)
    folder = tmp_path / 'practice'
    ledger = m.Ledger(folder / 'cost_ledger.jsonl')
    llm = m.MeteredClient(env.cypher, ledger, 0, replay_only=True)
    r = HybridRetriever(driver=fake_driver(env.graph), pg=fake_pg(), llm=llm,
                        embedder=m.MeteredEmbedder(env.embedder, ledger, 3),
                        manifest_path=identity(tmp_path), cache_file=folder / 'cypher_cache.jsonl')
    result = m.run_practice(r, env.queries, RULES, CLAUSES, folder)
    assert result['items'] == 15 and result['per_query'] == {'P1': 5, 'P2': 5, 'P3': 5}
    assert env.embedder.calls == 3 and env.cypher.calls == 0 and ledger.count('embedding') == 3
    rows = read_csv(folder / 'practice_sheet.csv')
    assert list(rows[0]) == m.SHEET_COLUMNS and all(row['item_id'].startswith('S8P-') for row in rows)
    assert all(row['jurisdiction'] == '' for row in rows) and not (tmp_path / 'c' / 'pool.jsonl').exists()
    again = m.run_practice(r, env.queries, RULES, CLAUSES, folder)
    assert set(again['states'].values()) == {'unchanged'} and env.embedder.calls == 3
