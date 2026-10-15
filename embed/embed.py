"""Embed input v1, with whole-corpus preflight before any service connection."""
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
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
MAX_TOKENS = 300000


@dataclass
class EmbeddingBatch:
    vectors: list
    prompt_tokens: int | None = None


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> EmbeddingBatch: ...


class FakeEmbedder:
    model = 'fake-sha256-1536'

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

    def __init__(self, client=None, encoding=None, sleep=time.sleep, attempts=5):
        if attempts < 1:
            raise ValueError('Retry attempts must be positive')
        if client is None:
            from openai import OpenAI
            client = OpenAI(max_retries=0)
        self.client = client
        self.encoding = encoding or offline_encoding()
        self.sleep = sleep
        self.attempts = attempts

    def embed(self, texts):
        lengths = [token_count(self.encoding, text) for text in texts]
        if not texts or any(not t.strip() for t in texts):
            raise ValueError('Never send empty embed inputs')
        if any(n > TOKEN_LIMIT for n in lengths):
            raise ValueError('STOP: input exceeds 8191 tokens; no truncation or splitting')
        if len(texts) > MAX_INPUTS or sum(lengths) > MAX_TOKENS:
            raise ValueError('Request exceeds packing limits')
        for attempt in range(self.attempts):
            try:
                response = self.client.embeddings.create(model=self.model, input=texts)
                break
            except Exception as exc:
                status = getattr(exc, 'status_code', None)
                retryable = status == 429 or isinstance(status, int) and 500 <= status < 600
                if not retryable or attempt == self.attempts - 1:
                    raise
                self.sleep(min(2**attempt, 30))
        mapped = {}
        for item in response.data:
            if item.index in mapped or not 0 <= item.index < len(texts):
                raise ValueError('Invalid/duplicate embedding response index')
            validate_vector(item.embedding)
            mapped[item.index] = item.embedding
        if len(mapped) != len(texts):
            raise ValueError('Missing embedding response indices')
        return EmbeddingBatch([mapped[i] for i in range(len(texts))], response.usage.prompt_tokens)


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


def pack(rows):
    batch, total = [], 0
    for row in rows:
        n = row['token_count']
        if n > TOKEN_LIMIT:
            raise ValueError('STOP: input over 8191 tokens')
        if n < 1:
            raise ValueError('Empty embed input')
        if batch and (len(batch) == MAX_INPUTS or total + n > MAX_TOKENS):
            yield batch
            batch, total = [], 0
        batch.append(row)
        total += n
    if batch:
        yield batch


def text_hash(text, model=MODEL):
    return hashlib.sha256(f'{model}|{DIMS}|{INPUT_VERSION}|{text}'.encode('utf-8')).hexdigest()


def dry_run(rows):
    total = sum(r['token_count'] for r in rows)
    return {'input_version': INPUT_VERSION, 'inputs': len(rows), 'total_tokens': total,
            'max_tokens': max((r['token_count'] for r in rows), default=0),
            'over_8191': sum(r['token_count'] > TOKEN_LIMIT for r in rows),
            'estimated_cost_usd': total * .02 / 1_000_000, 'cost_label': 'estimate at $0.02 / 1M tokens'}


def run(rows, embedder, store, previous=(), run_id=None, ts=None):
    # Validate all limits before the first call or upsert, even for library users.
    list(pack(rows))
    if len({r['clause_id'] for r in rows}) != len(rows) or any(not r['input'].strip() for r in rows):
        raise ValueError('Duplicate clause ids or empty embed inputs')
    old = {r['clause_id']: r for r in previous}
    existing = store.ids()
    run_id = run_id or str(uuid.uuid4())
    ts = ts or datetime.now(timezone.utc).isoformat()
    manifest, todo = [], []
    for row in rows:
        cid = row['clause_id']
        digest = text_hash(row['input'], embedder.model)
        unchanged = cid in existing and old.get(cid, {}).get('text_sha256') == digest
        manifest.append({'clause_id': cid, 'text_sha256': digest, 'token_count': row['token_count'],
                         'input_version': INPUT_VERSION, 'action': 'skipped_unchanged' if unchanged else 'embedded',
                         'run_id': run_id, 'ts': ts})
        if not unchanged:
            todo.append(row)
    usage, measured, calls = 0, 0, 0
    for batch in pack(todo):
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
    result = {'embedded': len(todo), 'skipped_unchanged': len(rows)-len(todo), 'requests': calls,
              'run_id': run_id, 'ts': ts, 'model': embedder.model,
              'tiktoken_tokens_sent': planned_tokens, 'api_prompt_tokens': usage if measured else None,
              'token_usage_delta': usage-planned_tokens if measured == calls and calls else None,
              'store': store.stats()}
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
    rows = prepare(json.loads(Path(args.input).read_text(encoding='utf-8')), offline_encoding())
    print(json.dumps(dry_run(rows), indent=2))
    if args.dry_run:
        return 0
    manifest_path = Path(args.manifest)
    saved = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    previous = saved if isinstance(saved, list) else saved.get('records', [])
    history = [] if isinstance(saved, list) else saved.get('runs', [])
    if args.stub:
        manifest, result = run(rows, FakeEmbedder(), MemoryStore(), previous)
    else:
        from contextlib import ExitStack
        from dotenv import load_dotenv
        import psycopg
        from embed.store import PostgresStore
        from neo4j import GraphDatabase
        load_dotenv(ROOT / '.env')
        with ExitStack() as stack:
            connection = stack.enter_context(psycopg.connect(os.environ['PG_DSN']))
            store = PostgresStore(connection)
            if args.live:
                manifest, result = run(rows, OpenAIEmbedder(), store, previous)
            else:
                driver = stack.enter_context(GraphDatabase.driver(os.environ['NEO4J_URI'],
                    auth=(os.environ.get('NEO4J_USER', 'neo4j'), os.environ['NEO4J_PASSWORD'])))
                records, _, _ = driver.execute_query('MATCH (c:Clause) RETURN c.id AS id', database_='neo4j')
                live_runs = [r for r in history if r['model'] == MODEL and r['api_prompt_tokens'] is not None]
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
    receipt = {k: result[k] for k in ('run_id', 'ts', 'model', 'tiktoken_tokens_sent', 'api_prompt_tokens', 'token_usage_delta')}
    temporary.write_text(json.dumps({'input_version': INPUT_VERSION, 'records': manifest,
                                     'runs': history + [receipt]}, indent=2) + '\n', encoding='utf-8')
    temporary.replace(manifest_path)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
