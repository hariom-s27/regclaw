"""G3 checks: adapter HTTP-log ordering (G3-02) and the real retriever's worst case (G3-03).

In-test fakes only: no network, no database, no paid call. Library constructors that would open a
real client or connection are replaced by functions that fail the test.
"""
import json
import sys
from types import SimpleNamespace as NS

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

import _netguard
from eval.s10 import frozen_candidates as FC
from llm.azure_openai import AzureOpenAIAdapter
from test_s10c1_part_a import request, sdk, status_error


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    monkeypatch.setenv('AZURE_OPENAI_S10_DEPLOYMENT', 'FAKE-classify')
    monkeypatch.setenv('AZURE_OPENAI_S8_DEPLOYMENT', 'FAKE-retrieve')
    for key in ('AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS', 'AZURE_OPENAI_S10_TIMEOUT_S',
                'AZURE_OPENAI_S10_REASONING_EFFORT', 'AZURE_OPENAI_S8_REASONING_EFFORT',
                'ANTHROPIC_API_KEY', 'AZURE_OPENAI_API_KEY', 'OPENAI_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    _netguard.block_network(monkeypatch, 'G3 tests forbid network')
    sleeps = []
    monkeypatch.setattr('llm.azure_openai.time.sleep', sleeps.append)
    return sleeps


class NoHeadersError(Exception):
    """Has .response, but the response object has no .headers attribute."""

    def __init__(self, status=None):
        super().__init__('FAKE error without headers')
        self.response = object()
        if status is not None:
            self.status_code = status


def classify_raises(transport, expected):
    client = AzureOpenAIAdapter('S10', sdk_client=transport)
    with pytest.raises(expected) as caught:
        client.classify(request())
    return client, caught.value


# ---------- G3-02: the HTTP-log row is written before headers are read ----------

@pytest.mark.parametrize('kind', ['retryable', 'not_retryable'])
def test_adapter_logs_row_when_response_has_no_headers(offline, kind):
    error = NoHeadersError(503 if kind == 'retryable' else None)
    transport, calls = sdk(error)
    client, raised = classify_raises(transport, NoHeadersError)
    assert raised is error
    if kind == 'retryable':
        # 503 retries exactly as before: 3 sends, 3 rows, 2 back-off sleeps, then the same error.
        assert len(calls) == 3 and len(offline) == 2
        assert [row['status'] for row in client.last_http_log] == [503, 503, 503]
    else:
        assert len(calls) == 1 and offline == []
        assert [row['status'] for row in client.last_http_log] == ['NoHeadersError']
    assert all(row['finish_reason'] is None for row in client.last_http_log)


def test_adapter_429_rows_and_retries_unchanged(offline):
    transport, calls = sdk(status_error(429))
    client, raised = classify_raises(transport, APIStatusError)
    assert raised.status_code == 429
    assert len(calls) == 3 and offline == [17.0, 17.0]
    assert [row['status'] for row in client.last_http_log] == [429, 429, 429]

    offline.clear()
    transport, calls = sdk(status_error(429), 'stop')
    client = AzureOpenAIAdapter('S10', sdk_client=transport)
    response = client.classify(request())
    assert len(calls) == 2 and offline == [17.0]
    assert [row['status'] for row in response.provenance['http_log']] == [429, 'ok']


def test_adapter_connection_error_rows_and_retries_unchanged(offline):
    error = APIConnectionError(request=httpx.Request('POST', 'https://fake.invalid'))
    transport, calls = sdk(error)
    client, raised = classify_raises(transport, APIConnectionError)
    assert raised is error
    assert len(calls) == 3 and len(offline) == 2
    assert [row['status'] for row in client.last_http_log] == ['APIConnectionError'] * 3


# ---------- G3-03: the REAL HybridRetriever.retrieve, worst case = CANDIDATE_RESERVE ----------

def test_real_hybrid_retrieve_worst_case_equals_candidate_reserve(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp('gthreeretrieve')
    assert 's8' not in str(root).casefold(), "temp root contains 's8'; choose a --basetemp without 's8'"

    def forbidden(*args, **kwargs):
        raise AssertionError('real client or connection forbidden')

    # Library edges only; nothing inside retrieval/hybrid_retriever.py is patched.
    import dotenv
    import openai
    import anthropic
    import psycopg
    monkeypatch.setattr(dotenv, 'load_dotenv', lambda *args, **kwargs: False)
    monkeypatch.setattr(openai, 'OpenAI', forbidden)
    monkeypatch.setattr(anthropic, 'Anthropic', forbidden)
    monkeypatch.setattr(psycopg, 'connect', forbidden)
    monkeypatch.setitem(sys.modules, 'neo4j', NS(unit_of_work=lambda **kw: lambda fn: fn,
                                                 GraphDatabase=NS(driver=forbidden)))
    monkeypatch.setitem(sys.modules, 'pgvector', NS(Vector=lambda vector: vector))
    from retrieval.hybrid_retriever import HybridRetriever
    from embed.embed import AzureOpenAIEmbedder, EmbeddingConfig, MODEL
    from embed.store import DIMS

    clause = 'FAKE-doc:1'
    queries, chat_calls, embedding_calls = [], [], []

    def execute_query(query, params=None, **kwargs):
        queries.append(query)
        if query.startswith('EXPLAIN '):
            return [], NS(query_type='r'), None
        if 'fulltext.queryNodes' in query:
            return [{'clause_id': clause}], None, None
        if 'RETURN sid' in query:
            return [], None, None
        if 'UNWIND $ids' in query:
            return [dict(id=clause, number='1', title='FAKE', text='FAKE text', obligation='SHALL',
                         standard='FAKE-doc')], None, None
        raise RuntimeError('FAKE schema introspection unavailable')

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute_read(self, work):
            return work(NS(run=lambda query, params: []))   # 0 rows -> feedback -> next attempt

    def pg_execute(query, params=()):
        if not isinstance(query, str):
            rows = [(DIMS, 1)]
        elif 'format_type' in query:
            rows = [(f'vector({DIMS})',)]
        else:
            rows = [(clause, 0.9)]
        return NS(fetchone=lambda: rows[0], fetchall=lambda: rows)

    driver = NS(execute_query=execute_query, session=lambda **kw: Session(), close=lambda: None)
    pg = NS(execute=pg_execute, close=lambda: None)

    def chat(**kwargs):
        chat_calls.append(kwargs)
        # Worst case per attempt: 800 truncated, 1600 accepted but returns 0 rows.
        finish = 'length' if len(chat_calls) % 2 else 'stop'
        return NS(model='FAKE-cypher', choices=[NS(finish_reason=finish, message=NS(
            content=json.dumps(dict(cypher='MATCH (c:Clause) RETURN c.id AS clause_id', params=[])),
            refusal=None))], usage=NS(prompt_tokens=1, completion_tokens=1))

    def embed(**kwargs):
        embedding_calls.append(kwargs)
        if len(embedding_calls) < 5:
            raise status_error(429)
        return NS(model=MODEL, data=[NS(index=0, embedding=[1.0] + [0.0] * (DIMS - 1))],
                  usage=NS(prompt_tokens=1))

    identity = root / 'identity' / 'embed_identity.json'
    identity.parent.mkdir(parents=True)
    with identity.open('x', encoding='utf-8') as stream:
        json.dump(dict(provider='azure_openai', deployment=MODEL, model=MODEL, dims=DIMS, model_version='1',
                       input_version='v1', clause_vectors=1), stream)
    (root / 'cache').mkdir()
    llm = AzureOpenAIAdapter('s8_cypher', sdk_client=NS(chat=NS(completions=NS(create=chat))))
    embedder = AzureOpenAIEmbedder(client=NS(embeddings=NS(create=embed)),
                                   config=EmbeddingConfig(provider='azure_openai', deployment=MODEL,
                                                          model_version='1'),
                                   encoding=NS(encode=lambda text, **kwargs: [1]), sleep=lambda seconds: None)
    retriever = HybridRetriever(cache_file=root / 'cache' / FC.CYPHER_CACHE_NAME, manifest_path=identity,
                                confirmation_path=root / 'identity' / 'confirmed.json',
                                driver=driver, pg=pg, llm=llm, embedder=embedder)
    ledger = root / 'data/runs/gthree-fake' / FC.CALL_LEDGER_NAME
    global_path = root / FC.GLOBAL_LEDGER_REL
    counter = FC.RetrievalRequests(retriever, ledger, global_path)
    assert counter.query_reserve == FC.CANDIDATE_RESERVE == 11

    hits, run = retriever.retrieve('FAKE device biological evaluation ISO 10993', top_k=30)

    assert [hit.clause_id for hit in hits] == [clause] and run.filter_path == 'fulltext_fallback'
    assert run.attempts == 3 and retriever.llm is llm and llm.role == 's8_cypher'
    assert len(chat_calls) == 6 and len(embedding_calls) == 5
    assert [call['max_completion_tokens'] for call in chat_calls] == [4000, 8000] * 3
    assert counter.counts() == (6, 5)
    assert len(chat_calls) + len(embedding_calls) == FC.CANDIDATE_RESERVE
    assert FC.ledger_total(ledger) == FC.ledger_total(global_path) == FC.CANDIDATE_RESERVE
    assert [(r['kind'], r['amount']) for r in FC.read_call_ledger(global_path)] == [('reserve', 11), ('settle', 0)]
    assert sum(q.startswith('EXPLAIN ') for q in queries) == 3
