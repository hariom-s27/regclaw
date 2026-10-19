"""Embed input v1, with whole-corpus preflight before any service connection."""
import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import time
from typing import Protocol
import uuid

from embed.store import DIMS, MemoryStore, validate_vector
from embed.token_stats import TOKEN_LIMIT, embed_input, offline_encoding, token_count

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'text-embedding-3-small'
INPUT_VERSION = 'v1'
MAX_INPUTS = 2048
MAX_TOKENS = 60000
TPM_BUDGET = 80000


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str = 'openai'
    deployment: str = MODEL
    model_version: str | None = None
    version_upgrade_option: str | None = None
    max_tokens: int = MAX_TOKENS
    tpm_budget: int = TPM_BUDGET

    def __post_init__(self):
        if self.provider not in ('openai', 'azure_openai'):
            raise ValueError('EMBED_PROVIDER must be openai or azure_openai')
        if not self.deployment or self.max_tokens < 1 or self.tpm_budget < 1:
            raise ValueError('Deployment and positive embedding token limits are required')

    @classmethod
    def from_env(cls):
        provider = os.getenv('EMBED_PROVIDER', 'openai')
        if provider not in ('openai', 'azure_openai'):
            raise ValueError('EMBED_PROVIDER must be openai or azure_openai')
        azure = provider == 'azure_openai'
        return cls(provider=provider,
                   deployment=os.environ['AZURE_OPENAI_EMBED_DEPLOYMENT'] if azure else MODEL,
                   model_version=os.getenv('AZURE_OPENAI_EMBED_MODEL_VERSION', '1') if azure else os.getenv('OPENAI_EMBED_MODEL_VERSION'),
                   version_upgrade_option='OnceNewDefaultVersionAvailable' if azure else None,
                   max_tokens=int(os.getenv('EMBED_MAX_TOKENS_PER_REQUEST', str(MAX_TOKENS))),
                   tpm_budget=int(os.getenv('EMBED_TPM_BUDGET', str(TPM_BUDGET))))


class RollingTokenBudget:
    """Reserve every attempt, including failures, in a trailing 60-second window."""

    def __init__(self, budget=TPM_BUDGET, *, clock=time.monotonic, sleep=time.sleep):
        if budget < 1:
            raise ValueError('TPM budget must be positive')
        self.budget, self.clock, self.sleep = budget, clock, sleep
        self.events = deque()
        self.wait_seconds = 0.0

    def reserve(self, tokens):
        if not 0 < tokens <= self.budget:
            raise ValueError('Request cannot fit the rolling token budget')
        while True:
            now = self.clock()
            while self.events and now - self.events[0][0] >= 60:
                self.events.popleft()
            if sum(n for _, n in self.events) + tokens <= self.budget:
                self.events.append((now, tokens))
                return
            delay = max(0.0, self.events[0][0] + 60 - now)
            self.sleep(delay)
            self.wait_seconds += delay


def retry_after(exc):
    response = getattr(exc, 'response', None)
    headers = response.headers if response is not None else {}
    try:
        delay = float(headers.get('retry-after-ms')) / 1000
        if delay >= 0 and math.isfinite(delay):
            return delay
    except (ValueError, TypeError):
        pass
    value = headers.get('retry-after')
    try:
        delay = float(value)
    except (ValueError, TypeError):
        try:
            delay = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, delay) if math.isfinite(delay) else None


@dataclass
class EmbeddingBatch:
    vectors: list
    prompt_tokens: int | None = None
    response_model: str | None = None


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> EmbeddingBatch: ...


class FakeEmbedder:
    model = 'fake-sha256-1536'
    provider = 'stub'

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        vectors = []
        for text in texts:
            if not text.strip():
                raise ValueError('Empty embed input')
            rng = random.Random(int.from_bytes(hashlib.sha256(text.encode('utf-8')).digest(), 'big'))
            vector = [rng.gauss(0, 1) for _ in range(DIMS)]
            norm = math.sqrt(sum(x*x for x in vector))
            vectors.append([x / norm for x in vector])
        return EmbeddingBatch(vectors)


class OpenAIEmbedder:
    model = MODEL
    provider = 'openai'
    embedding_model = MODEL
    dimensions = DIMS

    def __init__(self, client=None, encoding=None, sleep=time.sleep, attempts=5,
                 config=None, clock=time.monotonic):
        if attempts < 1:
            raise ValueError('Retry attempts must be positive')
        self.config = config or EmbeddingConfig()
        self.model = self.config.deployment
        self.provider = self.config.provider
        if client is None:
            from openai import OpenAI
            client = OpenAI(max_retries=0)
        self.client = client
        self.encoding = encoding or offline_encoding()
        self.sleep = sleep
        self.attempts = attempts
        self.pacer = RollingTokenBudget(self.config.tpm_budget, clock=clock, sleep=sleep)
        self.api_requests = self.retries = self.rate_limits = 0

    def embed(self, texts):
        lengths = [token_count(self.encoding, text) for text in texts]
        if not texts or any(not t.strip() for t in texts):
            raise ValueError('Never send empty embed inputs')
        if any(n > TOKEN_LIMIT for n in lengths):
            raise ValueError('STOP: input exceeds 8191 tokens; no truncation or splitting')
        if len(texts) > MAX_INPUTS or sum(lengths) > min(self.config.max_tokens, self.config.tpm_budget):
            raise ValueError('Request exceeds packing limits')
        for attempt in range(self.attempts):
            self.pacer.reserve(sum(lengths))
            self.api_requests += 1
            try:
                response = self.client.embeddings.create(model=self.model, input=texts)
                break
            except Exception as exc:
                status = getattr(exc, 'status_code', None)
                if status == 429:
                    self.rate_limits += 1
                retryable = status == 429 or isinstance(status, int) and 500 <= status < 600
                if not retryable or attempt == self.attempts - 1:
                    raise
                self.retries += 1
                delay = retry_after(exc)
                self.sleep(min(2**attempt, 30) if delay is None else delay)
        mapped = {}
        for item in response.data:
            if item.index in mapped or not 0 <= item.index < len(texts):
                raise ValueError('Invalid/duplicate embedding response index')
            validate_vector(item.embedding)
            mapped[item.index] = item.embedding
        if len(mapped) != len(texts):
            raise ValueError('Missing embedding response indices')
        return EmbeddingBatch([mapped[i] for i in range(len(texts))], response.usage.prompt_tokens,
                              getattr(response, 'model', None))


class AzureOpenAIEmbedder(OpenAIEmbedder):
    def __init__(self, client=None, config=None, **kwargs):
        config = config or EmbeddingConfig.from_env()
        if config.provider != 'azure_openai':
            raise ValueError('Azure embedder requires azure_openai configuration')
        if client is None:
            # Same v1 setup as llm.azure_openai, without chat-role dependencies.
            from openai import OpenAI
            client = OpenAI(
                base_url=f"{os.environ['AZURE_OPENAI_ENDPOINT'].rstrip('/')}/openai/v1/",
                api_key=os.environ['AZURE_OPENAI_API_KEY'], max_retries=0)
        super().__init__(client=client, config=config, **kwargs)


def make_embedder(config=None, **kwargs):
    config = config or EmbeddingConfig.from_env()
    cls = AzureOpenAIEmbedder if config.provider == 'azure_openai' else OpenAIEmbedder
    return cls(config=config, **kwargs)


def provenance(embedder):
    config = getattr(embedder, 'config', None)
    return {'provider': getattr(embedder, 'provider', 'openai'),
            'deployment': embedder.model,
            'model_version': config.model_version if config else None,
            'model_version_source': 'configured',
            'versionUpgradeOption': config.version_upgrade_option if config else None,
            'versionUpgradeOption_source': 'configured',
            'version_note': 'Azure may auto-upgrade the version; configured metadata is not a runtime attestation.'
                            if config and config.provider == 'azure_openai' else None}


def prepare(clauses, encoding):
    rows, seen, over = [], set(), []
    for clause in clauses:
        cid = clause['id']
        if cid in seen:
            raise ValueError(f'Duplicate clause id: {cid}')
        seen.add(cid)
        text = embed_input(clause)
        if not text.strip():
            raise ValueError(f'{cid}: empty embed input')
        tokens = token_count(encoding, text)
        if tokens > TOKEN_LIMIT:
            over.append((cid, tokens))
        rows.append({'clause_id': cid, 'input': text, 'token_count': tokens})
    if over:
        raise ValueError(f'STOP: {len(over)} inputs exceed 8,191 tokens: {over}; no splitting or truncation')
    return rows


def pack(rows, max_tokens=MAX_TOKENS, tpm_budget=TPM_BUDGET):
    if max_tokens < 1 or tpm_budget < 1:
        raise ValueError('Token limits must be positive')
    ceiling = min(max_tokens, tpm_budget)
    batch, total = [], 0
    for row in rows:
        n = row['token_count']
        if n > TOKEN_LIMIT:
            raise ValueError('STOP: input over 8191 tokens')
        if n < 1:
            raise ValueError('Empty embed input')
        if n > ceiling:
            raise ValueError('Input cannot fit request/TPM token limits; no splitting or truncation')
        if batch and (len(batch) == MAX_INPUTS or total + n > ceiling):
            yield batch
            batch, total = [], 0
        batch.append(row)
        total += n
    if batch:
        yield batch


def text_hash(text, model=MODEL, provider='openai', deployment=None, model_version=None):
    identity = [provider, deployment or model, model, model_version, DIMS, INPUT_VERSION, text]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode('utf-8')).hexdigest()


def dry_run(rows, config=None):
    config = config or EmbeddingConfig()
    total = sum(r['token_count'] for r in rows)
    batches = list(pack(rows, config.max_tokens, config.tpm_budget))
    now = [0.0]
    def advance(delay):
        now[0] += delay
    pacer = RollingTokenBudget(config.tpm_budget, clock=lambda: now[0], sleep=advance)
    for batch in batches:
        pacer.reserve(sum(r['token_count'] for r in batch))
    return {'input_version': INPUT_VERSION, 'inputs': len(rows), 'total_tokens': total,
            'max_tokens': max((r['token_count'] for r in rows), default=0),
            'over_8191': sum(r['token_count'] > TOKEN_LIMIT for r in rows),
            'provider': config.provider, 'deployment': config.deployment,
            'max_tokens_per_request': config.max_tokens, 'tpm_budget': config.tpm_budget,
            'planned_requests': len(batches),
            'request_inputs': [len(b) for b in batches],
            'request_tokens': [sum(r['token_count'] for r in b) for b in batches],
            'estimated_pacing_seconds': now[0],
            'time_label': 'minimum schedule without API latency or retries; rolling 60-second budget',
            'estimated_cost_usd': total * .02 / 1_000_000,
            'cost_label': 'illustrative estimate at $0.02 / 1M tokens; Azure billing unverified'}


def run(rows, embedder, store, previous=(), run_id=None, ts=None):
    started = time.monotonic()
    initial_requests = getattr(embedder, 'api_requests', 0)
    initial_retries = getattr(embedder, 'retries', 0)
    initial_rate_limits = getattr(embedder, 'rate_limits', 0)
    initial_wait = getattr(getattr(embedder, 'pacer', None), 'wait_seconds', 0)
    config = getattr(embedder, 'config', None) or EmbeddingConfig()
    metadata = provenance(embedder)
    # Validate all limits before the first call or upsert, even for library users.
    list(pack(rows, config.max_tokens, config.tpm_budget))
    if len({r['clause_id'] for r in rows}) != len(rows) or any(not r['input'].strip() for r in rows):
        raise ValueError('Duplicate clause ids or empty embed inputs')
    old = {r['clause_id']: r for r in previous}
    existing = store.ids()
    run_id = run_id or str(uuid.uuid4())
    ts = ts or datetime.now(timezone.utc).isoformat()
    manifest, todo = [], []
    for row in rows:
        cid = row['clause_id']
        digest = text_hash(row['input'], embedder.model, metadata['provider'],
                           metadata['deployment'], metadata['model_version'])
        unchanged = cid in existing and old.get(cid, {}).get('text_sha256') == digest
        manifest.append({**metadata, 'clause_id': cid, 'text_sha256': digest, 'token_count': row['token_count'],
                         'input_version': INPUT_VERSION, 'action': 'skipped_unchanged' if unchanged else 'embedded',
                         'run_id': run_id, 'ts': ts})
        if not unchanged:
            todo.append(row)
    usage, measured, calls = 0, 0, 0
    for batch in pack(todo, config.max_tokens, config.tpm_budget):
        result = embedder.embed([r['input'] for r in batch])
        if len(result.vectors) != len(batch):
            raise ValueError('Embedding result length mismatch')
        for vector in result.vectors:
            validate_vector(vector)
        store.upsert([(r['clause_id'], v) for r, v in zip(batch, result.vectors)])
        calls += 1
        if result.prompt_tokens is not None:
            usage += result.prompt_tokens
            measured += 1
    planned_tokens = sum(r['token_count'] for r in todo)
    result = {**metadata, 'embedded': len(todo), 'skipped_unchanged': len(rows)-len(todo), 'requests': calls,
              'run_id': run_id, 'ts': ts, 'model': embedder.model,
              'tiktoken_tokens_sent': planned_tokens, 'api_prompt_tokens': usage if measured else None,
              'token_usage_delta': usage-planned_tokens if measured == calls and calls else None,
              'api_requests': getattr(embedder, 'api_requests', calls) - initial_requests,
              'retries': getattr(embedder, 'retries', 0) - initial_retries,
              'rate_limits_429': getattr(embedder, 'rate_limits', 0) - initial_rate_limits,
              'pacing_seconds': getattr(getattr(embedder, 'pacer', None), 'wait_seconds', 0) - initial_wait,
              'store': store.stats()}
    result['wall_seconds'] = time.monotonic() - started
    return manifest, result


def reconcile(graph_ids, store, usage=None):
    graph_ids = set(graph_ids)
    vector_ids = store.ids()
    stats = store.stats()
    return {'neo4j_clauses': len(graph_ids), **stats, 'clauses_without_vector': sorted(graph_ids-vector_ids),
            'orphan_vectors': sorted(vector_ids-graph_ids),
            'norms_approximately_one': all(abs(stats[k]-1) <= .001 for k in ('norm_min', 'norm_max') if stats[k] is not None),
            'token_usage': usage}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for name in ('dry-run', 'stub', 'live', 'reconcile'):
        modes.add_argument('--' + name, action='store_true')
    parser.add_argument('--input', default=ROOT / 'data/clauses.json')
    parser.add_argument('--manifest', default=ROOT / 'data/embed_manifest.json')
    args = parser.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    config = EmbeddingConfig() if args.stub else EmbeddingConfig.from_env()
    rows = prepare(json.loads(Path(args.input).read_text(encoding='utf-8')), offline_encoding())
    print(json.dumps(dry_run(rows, config), indent=2))
    if args.dry_run:
        return 0
    manifest_path = Path(args.manifest)
    saved = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    previous = saved if isinstance(saved, list) else saved.get('records', [])
    history = [] if isinstance(saved, list) else saved.get('runs', [])
    if args.stub:
        embedder = FakeEmbedder()
        manifest, result = run(rows, embedder, MemoryStore(), previous)
    else:
        from contextlib import ExitStack
        import psycopg
        from embed.store import PostgresStore
        from neo4j import GraphDatabase
        with ExitStack() as stack:
            connection = stack.enter_context(psycopg.connect(os.environ['PG_DSN']))
            store = PostgresStore(connection)
            if args.live:
                embedder = make_embedder(config)
                manifest, result = run(rows, embedder, store, previous)
            else:
                driver = stack.enter_context(GraphDatabase.driver(os.environ['NEO4J_URI'],
                    auth=(os.environ.get('NEO4J_USER', 'neo4j'), os.environ['NEO4J_PASSWORD'])))
                records, _, _ = driver.execute_query('MATCH (c:Clause) RETURN c.id AS id', database_='neo4j')
                live_runs = [r for r in history if r.get('provider', 'openai') == config.provider
                             and r.get('deployment', r['model']) == config.deployment
                             and r['api_prompt_tokens'] is not None]
                usage = {'current_corpus_tokens': sum(r['token_count'] for r in rows),
                         'recorded_live_runs': len(live_runs),
                         'tiktoken_tokens_sent': sum(r['tiktoken_tokens_sent'] for r in live_runs),
                         'api_prompt_tokens': sum(r['api_prompt_tokens'] for r in live_runs)}
                usage['delta'] = usage['api_prompt_tokens'] - usage['tiktoken_tokens_sent']
                print(json.dumps(reconcile([r['id'] for r in records], store, usage), indent=2))
                return 0
    # Write only after all validated vectors have been persisted successfully.
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix('.tmp')
    receipt = {k: v for k, v in result.items() if k != 'store'}
    temporary.write_text(json.dumps({**provenance(embedder),
                                     'input_version': INPUT_VERSION, 'records': manifest,
                                     'runs': history + [receipt]}, indent=2) + '\n', encoding='utf-8')
    temporary.replace(manifest_path)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
