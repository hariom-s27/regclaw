"""B1 ranking/artifact/identity checks with fake graph, pgvector and SDKs."""
from contextlib import contextmanager
import json
import socket
import sys
from types import SimpleNamespace as NS

import pytest

from embed.embed import EmbeddingBatch, MODEL
from retrieval.hybrid_retriever import Hit, HybridRetriever, RunLog, SCHEMA_FALLBACK, has_obligation_filter
from retrieval.query_embeddings import EmbeddingIdentityError, EXPECTED, QueryEmbeddings, check_identity
from retrieval.setup import setup_fulltext_index


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    def deny(*a, **kw):
        raise AssertionError('Network forbidden in B1')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setitem(sys.modules, 'pgvector', NS(Vector=lambda v: v))
    monkeypatch.setitem(sys.modules, 'neo4j', NS(unit_of_work=lambda **kw: lambda fn: fn))


class FakeEmbedder:
    provider, model, dimensions = 'azure_openai', MODEL, 1536

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        assert len(texts) == 1
        return EmbeddingBatch([[1.] + [0.] * 1535])


def manifest(tmp_path, **overrides):
    path = tmp_path / 'embed_manifest.json'
    path.write_text(json.dumps({**EXPECTED, **overrides}))
    return path


def identity_pg(column='vector(1536)'):
    calls = []
    def execute(sql, params):
        calls.append((sql, params))
        return NS(fetchone=lambda: (column,) if column else None)
    return NS(execute=execute), calls


def test_matching_manifest_query_and_column_pass(tmp_path):
    pg, calls = identity_pg()
    assert check_identity(FakeEmbedder(), manifest(tmp_path), pg) == EXPECTED
    assert calls[0][0].startswith('SELECT ') and calls[0][1] == ('clause_embeddings', 'embedding')


@pytest.mark.parametrize('field,value', [('model', 'wrong-model'), ('dimensions', 3072), ('provider', 'openai')])
@pytest.mark.parametrize('side', ['manifest', 'query'])
def test_wrong_identity_aborts_before_column_query(tmp_path, side, field, value):
    pg, calls = identity_pg()
    embedder = FakeEmbedder()
    overrides = {field: value} if side == 'manifest' else {}
    if side == 'query':
        setattr(embedder, field, value)
    with pytest.raises(EmbeddingIdentityError, match='identity mismatch'):
        check_identity(embedder, manifest(tmp_path, **overrides), pg)
    assert not calls and embedder.calls == 0


@pytest.mark.parametrize('kind', ['absent', 'missing_provider', 'missing_model', 'missing_dimensions', 'legacy_list'])
def test_missing_identity_aborts(tmp_path, kind):
    path = tmp_path / 'manifest.json'
    if kind != 'absent':
        identity = dict(EXPECTED)
        if kind.startswith('missing_'):
            identity.pop(kind.removeprefix('missing_'))
        path.write_text(json.dumps([] if kind == 'legacy_list' else identity))
    pg, calls = identity_pg()
    with pytest.raises(EmbeddingIdentityError, match='identity missing'):
        check_identity(FakeEmbedder(), path, pg)
    assert not calls


@pytest.mark.parametrize('column', [None, 'vector', 'vector(3072)'])
def test_column_dimension_mismatch_aborts(tmp_path, column):
    pg, _ = identity_pg(column)
    with pytest.raises(EmbeddingIdentityError, match='column identity mismatch'):
        check_identity(FakeEmbedder(), manifest(tmp_path), pg)


def test_query_embedding_factory_cache_and_append_only(tmp_path, monkeypatch):
    from retrieval import query_embeddings
    fake = FakeEmbedder()
    made = []
    def make():
        made.append(True)
        return fake
    monkeypatch.setattr(query_embeddings, 'make_embedder', make)
    path = tmp_path / 'query_embedding_cache.jsonl'
    first = QueryEmbeddings(path)
    vector, key = first.get('synthetic query')
    before = path.read_bytes()
    second = QueryEmbeddings(path)
    assert second.get('synthetic query') == (vector, key)
    assert len(made) == 2 and fake.calls == 1 and path.read_bytes() == before
    second.get('different synthetic query')
    assert fake.calls == 2 and path.read_bytes().startswith(before)
    fake.model = 'synthetic-other-model'
    second.get('synthetic query')
    fake.provider = 'synthetic-other-provider'
    second.get('synthetic query')
    assert fake.calls == 4


def test_invalid_query_vector_is_not_cached(tmp_path):
    fake = FakeEmbedder()
    fake.embed = lambda _: EmbeddingBatch([[1., 0.]])
    cache = QueryEmbeddings(tmp_path / 'cache.jsonl', fake)
    with pytest.raises(ValueError, match='1536'):
        cache.get('synthetic')
    assert not cache.cache.path.exists()


def full_retriever(tmp_path, monkeypatch, *, missing_vector=False, fulltext_error=False):
    from llm.cypher import AnthropicCypherClient
    from llm import provider
    from retrieval import hybrid_retriever as hybrid
    calls = dict(llm=0, graph=[], sql=[])
    def create(**kw):
        calls['llm'] += 1
        return NS(model='synthetic-haiku', stop_reason='end_turn', usage=None,
                  content=[NS(type='text', text=json.dumps(dict(
                      cypher='MATCH (c:Clause) WHERE c.obligation = $ob RETURN DISTINCT c.id AS clause_id',
                      params=[dict(name='ob', value='SHALL')])) )])
    client = AnthropicCypherClient(NS(messages=NS(create=create)))
    monkeypatch.setattr(hybrid, 'make_client', lambda role: client if role == 's8_cypher' else None)
    def execute_query(sql, params=None, **kw):
        calls['graph'].append(sql)
        assert 'CREATE' not in sql
        if 'UNWIND labels(n)' in sql:
            raise RuntimeError('synthetic fallback schema')
        if sql.startswith('EXPLAIN'):
            return [], NS(query_type='r'), None
        if 'fulltext.queryNodes' in sql:
            raise RuntimeError('synthetic missing index')
        if sql == 'MATCH (c:Clause) RETURN c.id AS clause_id':
            return [{'clause_id': 's:1'}, {'clause_id': 's:2'}], None, None
        if 'RETURN sid' in sql:
            return [{'sid': 's:1', 'nid': 's:1.1', 'rel': 'child'}], None, None
        return [dict(id=i, number=i.split(':')[1], title='', text='', obligation='SHALL', standard='s')
                for i in params['ids']], None, None
    @contextmanager
    def session(**kw):
        def execute_read(work):
            if fulltext_error:
                raise RuntimeError('synthetic generated fulltext failure')
            return work(NS(run=lambda *a: [{'clause_id': 's:1'}, {'clause_id': 's:2'}]))
        yield NS(execute_read=execute_read)
    driver = NS(execute_query=execute_query, session=session, close=lambda: None)
    def pg_execute(sql, params):
        calls['sql'].append(sql)
        if 'format_type' in sql:
            return NS(fetchone=lambda: ('vector(1536)',))
        rows = [('s:1', .9), ('s:2', .8)] if 'ORDER BY' in sql else ([] if missing_vector else [('s:1.1', .85)])
        return NS(fetchall=lambda: rows)
    pg = NS(execute=pg_execute, close=lambda: None)
    embedder = FakeEmbedder()
    r = HybridRetriever(driver=driver, pg=pg, embedder=embedder, manifest_path=manifest(tmp_path),
                        cache_file=tmp_path / 'cypher_cache.jsonl', max_retries=0)
    return r, embedder, calls


def test_end_to_end_artifacts_replay_and_tie_breaker(tmp_path, monkeypatch):
    r, embedder, calls = full_retriever(tmp_path, monkeypatch)
    first, run = r.retrieve('synthetic requirement', qid='SYN1')
    second, replay = r.retrieve('synthetic requirement', qid='SYN1')
    assert first == second and calls['llm'] == embedder.calls == 1
    assert run.stage1_candidate_ids == ['s:1', 's:2'] and run.candidate_count == 2
    assert run.fallback_path == 'text2cypher' and run.obligation_filter
    assert run.params == {'ob': 'SHALL'} and run.cache_key['gen_index'] == 0
    assert run.stage2_ranked == [('s:1', .9), ('s:2', .8)]
    assert run.stage3_added == [('s:1.1', .85, 'neighbour:child:s:1')]
    assert run.final_ids == ['s:1', 's:2', 's:1.1'] and run.query_embedding_key == replay.query_embedding_key
    assert run.embedding_provider == 'azure_openai' and run.embedding_model == MODEL
    assert run.generations[0]['role'] == 's8_cypher' and replay.generations[0]['cache_hit']
    json.dumps(run.to_dict())
    ranked_sql = [sql for sql in calls['sql'] if 'ORDER BY' in sql]
    assert ranked_sql and all('ORDER BY embedding <=> %s, clause_id LIMIT %s' in sql for sql in ranked_sql)
    assert all('embedding IS NOT NULL' in sql for sql in calls['sql'] if 'ANY' in sql)


def test_missing_fulltext_index_reaches_all_clauses(tmp_path, monkeypatch):
    r, _, calls = full_retriever(tmp_path, monkeypatch, fulltext_error=True)
    _, run = r.retrieve('synthetic requirement')
    assert run.stage1_candidate_ids == ['s:1', 's:2']
    assert run.fallback_path == run.filter_path == 'all_clauses'
    assert not run.obligation_filter and run.params == {}
    assert any('full-text fallback failed' in warning for warning in run.warnings)
    assert any('fulltext.queryNodes' in sql for sql in calls['graph'])
    cached = json.loads(r.cypher_cache.path.read_text().splitlines()[0])
    assert cached['fallback_path'] == 'all_clauses'


def test_no_vector_neighbour_excluded_even_for_zero_seed(tmp_path, monkeypatch):
    r, _, _ = full_retriever(tmp_path, monkeypatch, missing_vector=True)
    assert r.stage3_expand([1.] * 1536, [Hit('s:1', 0.)], 2, 5, .85) == []
    assert r.stage3_skipped_no_vector == 1
    _, run = r.retrieve('synthetic requirement')
    assert run.stage3_skipped_no_vector == 1 and not run.stage3_added


@pytest.mark.parametrize('cypher,expected', [
    ('MATCH (c:Clause) WHERE c.obligation = $ob RETURN c.id AS clause_id', True),
    ('MATCH (x:Clause) WHERE x.obligation = $ob RETURN x.id AS clause_id', True),
    ("MATCH (c:Clause) WHERE c.text = '.obligation' RETURN c.id AS clause_id // c.obligation", False),
])
def test_obligation_filter_three_synthetic_queries(cypher, expected):
    assert has_obligation_filter(cypher) is expected


def test_identity_rechecked_before_any_retrieval_work(tmp_path, monkeypatch):
    r, fake, calls = full_retriever(tmp_path, monkeypatch)
    fake.provider = 'openai'
    with pytest.raises(EmbeddingIdentityError):
        r.retrieve('synthetic query')
    assert fake.calls == calls['llm'] == 0
    assert not any('ORDER BY' in sql for sql in calls['sql'])


def test_setup_is_explicit_and_task_dispatches_without_live_access(monkeypatch):
    import tasks
    from retrieval import setup
    calls = []
    setup_fulltext_index(NS(execute_query=lambda *a, **kw: calls.append((a, kw))), 'synthetic')
    assert len(calls) == 1 and calls[0][0][0].startswith('CREATE FULLTEXT INDEX clause_text_ft IF NOT EXISTS')
    monkeypatch.setattr(setup, 'main', lambda args: calls.append(args) or 0)
    monkeypatch.setattr(sys, 'argv', ['tasks.py', 'retrieval-setup', '--database', 'synthetic'])
    assert tasks.main() == 0 and calls[-1] == ['--database', 'synthetic']
