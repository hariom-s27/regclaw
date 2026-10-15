import math
import sys
from types import ModuleType, SimpleNamespace

import pytest

from embed.embed import FakeEmbedder, OpenAIEmbedder, dry_run, pack, prepare, reconcile, run, text_hash
from embed.store import CREATE_TABLE, DIMS, INDEX_SQL, MemoryStore, PostgresStore, RANK_SQL, UPSERT_SQL, assert_no_ann


class WordEncoding:
    def encode(self, text, **kwargs):
        return text.split()


def clause(text='Body', title='Title'):
    return dict(id='x:1', title=title, text=text)


def test_whole_corpus_guard_before_embed_or_store():
    with pytest.raises(ValueError, match='STOP.*8,191'):
        prepare([clause('word ' * 9000)], WordEncoding())
    with pytest.raises(ValueError, match='empty'):
        prepare([clause('', '')], WordEncoding())
    fake, store = FakeEmbedder(), MemoryStore()
    with pytest.raises(ValueError, match='STOP'):
        run([dict(clause_id='x', input='hi', token_count=9000)], fake, store)
    assert fake.calls == 0 and not store.rows
    with pytest.raises(ValueError, match='Duplicate'):
        rows = prepare([clause()], WordEncoding())
        run(rows + rows, fake, store)
    with pytest.raises(ValueError, match='empty'):
        run([dict(clause_id='x', input='', token_count=1)], fake, store)


def test_both_request_packing_limits():
    small = [dict(token_count=1) for _ in range(2050)]
    assert [len(b) for b in pack(small)] == [2048, 2]
    large = [dict(token_count=8191) for _ in range(100)]
    batches = list(pack(large))
    assert sum(map(len, batches)) == 100
    assert all(len(b) <= 2048 and sum(r['token_count'] for r in b) <= 300000 for b in batches)
    assert [len(b) for b in batches] == [36, 36, 28]


def test_fake_norm_hash_skip_and_missing_vector_repair():
    rows = prepare([clause()], WordEncoding())
    fake, store = FakeEmbedder(), MemoryStore()
    manifest, result = run(rows, fake, store)
    assert result['embedded'] == 1 and fake.calls == 1
    vector = store.rows['x:1']
    assert len(vector) == DIMS and math.sqrt(sum(x*x for x in vector)) == pytest.approx(1)
    assert FakeEmbedder().embed(['Title\nBody']).vectors[0] == vector
    again, result = run(rows, fake, store, manifest)
    assert result['skipped_unchanged'] == 1 and fake.calls == 1
    assert again[0]['action'] == 'skipped_unchanged'
    assert text_hash('Title\nBody') != text_hash('Title\nBody', fake.model)
    _, result = run(rows, fake, MemoryStore(), manifest)
    assert result['embedded'] == 1 and fake.calls == 2
    assert dry_run(rows)['estimated_cost_usd'] == pytest.approx(2 * .02 / 1_000_000)
    result = reconcile({'x:1', 'missing'}, store)
    assert result['clauses_without_vector'] == ['missing'] and result['norms_approximately_one']


def test_openai_order_retry_limits_and_no_dimensions_parameter():
    calls, sleeps = [], []
    vector = [1.0] + [0.0] * (DIMS-1)

    class RetryError(Exception):
        status_code = 429

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RetryError()
        return SimpleNamespace(data=[SimpleNamespace(index=1, embedding=vector), SimpleNamespace(index=0, embedding=[-x for x in vector])],
                               usage=SimpleNamespace(prompt_tokens=2))
    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    embedder = OpenAIEmbedder(client, WordEncoding(), sleep=sleeps.append)
    result = embedder.embed(['a', 'b'])
    assert result.vectors[0][0] == -1 and result.vectors[1][0] == 1
    assert result.prompt_tokens == 2 and sleeps == [1]
    assert set(calls[-1]) == {'model', 'input'}
    for texts in ([''], ['word ' * 9000], ['a'] * 2049, ['word ' * 8000] * 38):
        with pytest.raises(ValueError):
            embedder.embed(texts)
    assert len(calls) == 2


@pytest.mark.parametrize('status,retries', [(500, 3), (503, 3), (400, 1)])
def test_openai_retry_failure_is_bounded(status, retries):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        error = RuntimeError('fake failure')
        error.status_code = status
        raise error
    embedder = OpenAIEmbedder(SimpleNamespace(embeddings=SimpleNamespace(create=create)), WordEncoding(), sleep=lambda _: None, attempts=3)
    with pytest.raises(RuntimeError):
        embedder.embed(['a'])
    assert len(calls) == retries


def test_sql_contract_upsert_ann_and_mock_connection(monkeypatch):
    assert 'vector(1536)' in CREATE_TABLE and 'clause_id text PRIMARY KEY' in CREATE_TABLE
    assert 'ON CONFLICT (clause_id) DO UPDATE' in UPSERT_SQL
    assert 'WHERE clause_id = ANY(%(ids)s) ORDER BY embedding <=> %(q)s LIMIT %(k)s' in RANK_SQL
    assert_no_ann([('CREATE UNIQUE INDEX clause_embeddings_pkey USING btree (clause_id)',)])
    for method in ('hnsw', 'ivfflat'):
        with pytest.raises(RuntimeError, match='DESIGN CONFLICT'):
            assert_no_ann([{'indexdef': f'CREATE INDEX idx ON clause_embeddings USING {method} (embedding)'}])
    module = ModuleType('pgvector.psycopg')
    module.register_vector = lambda connection: None
    monkeypatch.setitem(sys.modules, 'pgvector.psycopg', module)
    class Connection:
        def __init__(self):
            self.written = []
        def execute(self, query, params=None):
            self.written.append(query)
            if query == INDEX_SQL:
                return SimpleNamespace(fetchall=lambda: [('btree',)])
            if 'pg_attribute a JOIN pg_class' in query:
                return SimpleNamespace(fetchall=lambda: [('clause_id', 'text'), ('embedding', 'vector(1536)')])
            if 'pg_index i' in query:
                return SimpleNamespace(fetchall=lambda: [('clause_id',)])
        def commit(self): pass
        def cursor(self): return self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def executemany(self, query, values):
            assert query == UPSERT_SQL
            self.values = values
    connection = Connection()
    store = PostgresStore(connection)
    store.upsert([('x:1', [0.0] * DIMS)])
    assert connection.values[0][0] == 'x:1'
    assert len(connection.values[0][1]) == DIMS
