import math
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from embed.embed import (AzureOpenAIEmbedder, EmbeddingConfig, FakeEmbedder, OpenAIEmbedder,
                         RollingTokenBudget, dry_run, make_embedder, pack, prepare,
                         reconcile, retry_after, run, text_hash)
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
    assert all(len(b) <= 2048 and sum(r['token_count'] for r in b) <= 60000 for b in batches)
    assert [len(b) for b in batches] == [7] * 14 + [2]


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


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_rolling_budget_partial_expiry_and_exact_boundary():
    clock = Clock()
    pacer = RollingTokenBudget(80000, clock=clock, sleep=clock.sleep)
    pacer.reserve(30000)
    clock.now = 10
    pacer.reserve(40000)
    clock.now = 20
    pacer.reserve(20000)
    assert clock.now == 60 and clock.sleeps == [40]
    pacer.reserve(20000)
    assert clock.now == 60  # 40K + 20K + 20K equals the budget.
    pacer.reserve(1)
    assert clock.now == 70 and clock.sleeps == [40, 10]
    assert sum(n for _, n in pacer.events) == 40001
    for tokens in (0, 80001):
        with pytest.raises(ValueError):
            pacer.reserve(tokens)


def test_custom_packing_and_planned_pacing():
    rows = [dict(token_count=5000) for _ in range(25)]
    batches = list(pack(rows, max_tokens=15000, tpm_budget=20000))
    assert [len(b) for b in batches] == [3] * 8 + [1]
    plan = dry_run(rows, EmbeddingConfig(max_tokens=15000, tpm_budget=20000))
    assert plan['planned_requests'] == 9 and plan['estimated_pacing_seconds'] == 420
    # A request cap above TPM must produce feasible batches too.
    assert all(sum(r['token_count'] for r in b) <= 10000
               for b in pack(rows, max_tokens=60000, tpm_budget=10000))
    with pytest.raises(ValueError, match='cannot fit'):
        list(pack(rows, max_tokens=4999))
    for limit in (0, -1):
        with pytest.raises(ValueError):
            list(pack(rows, max_tokens=limit))


def fake_sdk():
    calls = []
    vector = [1.0] + [0.0] * (DIMS-1)
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=vector)
                                      for i, _ in enumerate(kwargs['input'])],
                               usage=SimpleNamespace(prompt_tokens=len(kwargs['input'])))
    return SimpleNamespace(embeddings=SimpleNamespace(create=create)), calls


@pytest.mark.parametrize('provider', [None, 'openai'])
def test_default_openai_constructor_and_request_regression(monkeypatch, provider):
    import openai
    for name in ('EMBED_PROVIDER', 'EMBED_MAX_TOKENS_PER_REQUEST', 'EMBED_TPM_BUDGET'):
        monkeypatch.delenv(name, raising=False)
    if provider:
        monkeypatch.setenv('EMBED_PROVIDER', provider)
    # An Azure key/deployment must never implicitly switch the provider.
    monkeypatch.setenv('AZURE_OPENAI_EMBED_DEPLOYMENT', 'ignored-deployment')
    sdk, calls = fake_sdk()
    constructors = []
    def constructor(**kwargs):
        constructors.append(kwargs)
        return sdk
    monkeypatch.setattr(openai, 'OpenAI', constructor)
    embedder = make_embedder(encoding=WordEncoding())
    assert type(embedder) is OpenAIEmbedder
    embedder.embed(['Title\nBody'])
    assert constructors == [{'max_retries': 0}]
    assert calls == [{'model': 'text-embedding-3-small', 'input': ['Title\nBody']}]
    assert embedder.config.max_tokens == 60000 and embedder.config.tpm_budget == 80000


def test_azure_selection_url_config_and_provenance(monkeypatch):
    import openai
    monkeypatch.setenv('EMBED_PROVIDER', 'azure_openai')
    monkeypatch.setenv('AZURE_OPENAI_ENDPOINT', 'https://fixture.openai.azure.com///')
    monkeypatch.setenv('AZURE_OPENAI_API_KEY', 'offline-fixture')
    monkeypatch.setenv('AZURE_OPENAI_EMBED_DEPLOYMENT', 'fixture-deployment')
    monkeypatch.delenv('AZURE_OPENAI_EMBED_MODEL_VERSION', raising=False)
    monkeypatch.setenv('EMBED_MAX_TOKENS_PER_REQUEST', '100')
    monkeypatch.setenv('EMBED_TPM_BUDGET', '200')
    sdk, calls = fake_sdk()
    constructors = []
    def constructor(**kwargs):
        constructors.append(kwargs)
        return sdk
    monkeypatch.setattr(openai, 'OpenAI', constructor)
    embedder = make_embedder(encoding=WordEncoding())
    assert type(embedder) is AzureOpenAIEmbedder
    assert constructors == [dict(base_url='https://fixture.openai.azure.com/openai/v1/',
                                 api_key='offline-fixture', max_retries=0)]
    rows = prepare([clause()], WordEncoding())
    records, receipt = run(rows, embedder, MemoryStore())
    for metadata in (records[0], receipt):
        assert metadata['provider'] == 'azure_openai'
        assert metadata['deployment'] == 'fixture-deployment'
        assert metadata['model_version'] == '1' and metadata['model_version_source'] == 'configured'
        assert metadata['versionUpgradeOption'] == 'OnceNewDefaultVersionAvailable'
        assert metadata['versionUpgradeOption_source'] == 'configured'
        assert 'auto-upgrade' in metadata['version_note']
    assert calls[0] == dict(model='fixture-deployment', input=['Title\nBody'])
    monkeypatch.setenv('AZURE_OPENAI_EMBED_MODEL_VERSION', '2')
    assert EmbeddingConfig.from_env().model_version == '2'
    monkeypatch.setenv('EMBED_PROVIDER', 'invalid')
    with pytest.raises(ValueError, match='EMBED_PROVIDER'):
        make_embedder()


def test_provider_deployment_version_hash_and_reembedding():
    baseline = text_hash('same')
    assert baseline != text_hash('same', provider='azure_openai')
    assert baseline != text_hash('same', deployment='other')
    assert baseline != text_hash('same', model_version='1')
    rows = prepare([clause()], WordEncoding())
    store = MemoryStore()
    sdk, calls = fake_sdk()
    first = OpenAIEmbedder(sdk, WordEncoding())
    records, _ = run(rows, first, store)
    # Same model/deployment, different host: must re-embed.
    azure = AzureOpenAIEmbedder(sdk, EmbeddingConfig(provider='azure_openai'), encoding=WordEncoding())
    changed, result = run(rows, azure, store, records)
    assert result['embedded'] == 1 and len(calls) == 2
    _, result = run(rows, azure, store, changed)
    assert result['skipped_unchanged'] == 1 and len(calls) == 2
    assert result['api_requests'] == 0 and result['retries'] == 0
    # Old manifests lacking provider identity also safely re-embed.
    _, result = run(rows, first, store, [dict(clause_id='x:1', text_sha256='legacy-hash')])
    assert result['embedded'] == 1


@pytest.mark.parametrize('status', [429, 503])
def test_retries_honor_retry_after_and_count_attempts_in_budget(status):
    clock = Clock()
    sdk, calls = fake_sdk()
    original = sdk.embeddings.create
    attempts = []
    def create(**kwargs):
        attempts.append(clock.now)
        if len(attempts) == 1:
            error = RuntimeError('fixture')
            error.status_code = status
            error.response = SimpleNamespace(headers={'retry-after': '12'})
            raise error
        return original(**kwargs)
    sdk.embeddings.create = create
    embedder = OpenAIEmbedder(sdk, WordEncoding(), clock=clock, sleep=clock.sleep,
                             config=EmbeddingConfig(max_tokens=2, tpm_budget=2))
    embedder.embed(['two tokens'])
    assert attempts == [0, 60] and clock.sleeps == [12, 48]
    assert embedder.api_requests == 2 and embedder.retries == 1
    assert embedder.rate_limits == int(status == 429)


def test_retry_after_http_date_and_milliseconds(monkeypatch):
    monkeypatch.setattr('embed.embed.time.time', lambda: 0)
    for headers, expected in (({'retry-after': 'Thu, 01 Jan 1970 00:03:00 GMT'}, 180),
                              ({'retry-after-ms': '2500'}, 2.5),
                              ({'retry-after': 'bad'}, None)):
        assert retry_after(SimpleNamespace(response=SimpleNamespace(headers=headers))) == expected


def test_cli_persists_provider_manifest_and_zero_call_rerun(monkeypatch, tmp_path, capsys):
    from contextlib import nullcontext
    import embed.embed as module
    import embed.store as stores
    monkeypatch.setattr('dotenv.load_dotenv', lambda *_: None)
    monkeypatch.setenv('EMBED_PROVIDER', 'azure_openai')
    monkeypatch.setenv('AZURE_OPENAI_EMBED_DEPLOYMENT', 'text-embedding-3-small')
    monkeypatch.delenv('AZURE_OPENAI_EMBED_MODEL_VERSION', raising=False)
    monkeypatch.setattr(module, 'offline_encoding', WordEncoding)
    monkeypatch.setattr('psycopg.connect', lambda *_: nullcontext(None))
    store = MemoryStore()
    monkeypatch.setattr(stores, 'PostgresStore', lambda _: store)
    monkeypatch.setenv('PG_DSN', 'offline-fixture')
    sdk, calls = fake_sdk()
    monkeypatch.setattr(module, 'make_embedder', lambda config: AzureOpenAIEmbedder(
        sdk, config, encoding=WordEncoding()))
    source, manifest = tmp_path / 'clauses.json', tmp_path / 'manifest.json'
    source.write_text(json.dumps([clause()]), encoding='utf-8')
    args = ['--live', '--input', str(source), '--manifest', str(manifest)]
    assert module.main(args) == 0
    assert module.main(args) == 0
    saved = json.loads(manifest.read_text())
    assert saved['provider'] == 'azure_openai' and saved['model_version_source'] == 'configured'
    assert saved['records'][0]['action'] == 'skipped_unchanged'
    assert [r['api_requests'] for r in saved['runs']] == [1, 0]
    assert len(calls) == 1
    assert 'offline-fixture' not in capsys.readouterr().out


def test_postgres_stats_uses_vector_norm_for_vector_column():
    # pgvector exposes l2_norm for halfvec/sparsevec; vector_norm accepts vector.
    queries = []
    def execute(query):
        queries.append(query)
        return SimpleNamespace(fetchone=lambda: (2, 2, 0.9999, 1.0001))
    store = PostgresStore.__new__(PostgresStore)
    store.connection = SimpleNamespace(execute=execute)
    assert store.stats() == dict(distinct_ids=2, total_rows=2, norm_min=0.9999, norm_max=1.0001)
    assert 'min(vector_norm(embedding)), max(vector_norm(embedding))' in queries[0]
