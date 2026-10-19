"""S8 Part C: eval generations, judging pool, blind labelling sheet and LLM pre-labels.

Commands (via tasks.py): s8-generate, s8-pool, s8-sheet, s8-prelabel.
Outputs live under data/runs/<provider>/s8/c/. Caches are append-only; result files are
write-once (a rerun must reproduce them byte for byte, otherwise it stops).
Reports print COUNTS ONLY (blindness): never which system retrieved which clause, ranks or scores.
"""
import argparse
from collections import Counter, defaultdict, deque
import csv
import hashlib
import inspect
import io
import json
import logging
import math
import os
import random
import re
import sys
import time
from pathlib import Path

from retrieval.cypher_cache import JsonlCache, canonical, sha
from retrieval.hybrid_retriever import (CypherGenerationError, HybridRetriever, RunLog,
                                        validate_cypher)

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger('s8_runs')

FAMILY = 'c2'  # Addendum 3: new completion budget; family c is kept read-only as evidence
ARMS = {'v1': '1.0', 'v1.1': '1.1'}
NOOB = 'v1-noob'
GEN_VARIANTS = ('a', 'b')
GENERATIONS = 10
TOP_KS = (10, 20)
FILTER_OFF_DEPTH = 20
LIMITS = (5, 10, 25)
LIMIT_REPEATS = 10
SHEET_SEED = 20261019
# Illustrative UNVERIFIED USD prices per 1M tokens (ADR 0025); not Azure billing.
PRICES = dict(input=0.25, output=2.0, embedding=0.02)
RPM, TPM = 100, 100_000
JUDGING_RULES = ('grades', 'judge_text_not_graph', 'jurisdiction', 'swap')
SHEET_COLUMNS = ['item_id', 'qid', 'query', 'jurisdiction', 'narrative_grade3', 'narrative_grade2',
                 'narrative_grade1_traps', 'common_rules', 'source_doc', 'clause_id', 'title', 'text',
                 'grade', 'note']

HYDE_PROMPT = ROOT / 'retrieval_eval/s8_hyde_prompt.md'
HYDE_FREEZE = ROOT / 'retrieval_eval/s8_hyde_freeze.json'
PRELABEL_PROMPT = ROOT / 'retrieval_eval/s8_prelabel_prompt.md'
PRELABEL_FREEZE = ROOT / 'retrieval_eval/s8_prelabel_freeze.json'
CYPHER_FREEZE = ROOT / 'retrieval_eval/s8_prompt_freeze.json'
QUERIES = ROOT / 'retrieval_eval/queries.yaml'
CLAUSES = ROOT / 'data/clauses.json'

HYDE_SCHEMA = {'type': 'object', 'additionalProperties': False,
               'properties': {'passage': {'type': 'string'}}, 'required': ['passage']}
# Field order is the generation order (analysis fields first, grade last).
PRELABEL_SCHEMA = {'type': 'object', 'additionalProperties': False,
                   'properties': {
                       'query_asks': {'type': 'string'},
                       'clause_states': {'type': 'string', 'enum': ['answers', 'partial', 'mentions_only', 'unrelated']},
                       'jurisdiction_match': {'type': 'string', 'enum': ['yes', 'no', 'not_applicable']},
                       'evidence_span': {'type': 'string'},
                       'grade': {'type': 'integer', 'enum': [0, 1, 2, 3]}},
                   'required': ['query_asks', 'clause_states', 'jurisdiction_match', 'evidence_span', 'grade']}
PRELABEL_FIELDS = tuple(PRELABEL_SCHEMA['properties'])


class BudgetExceeded(CypherGenerationError):
    """Hard call budget reached: stop cleanly; a rerun resumes."""


class ReplayMiss(CypherGenerationError):
    """A cache miss in replay-only mode. Counted and reported, never regenerated."""


class TransportError(CypherGenerationError):
    """The API call failed after bounded retries; the slot stays open for a resume."""


class HelperStop(RuntimeError):
    """A HyDE / pre-label response failed its stop guard or schema."""


# ---------------------------------------------------------------- frozen prompts
def lf_bytes(path):
    return Path(path).read_bytes().replace(b'\r\n', b'\n').replace(b'\r', b'\n')


def file_sha(path):
    """sha256 of the LF-normalised file, so CRLF checkouts hash the same."""
    return hashlib.sha256(lf_bytes(path)).hexdigest()


def load_frozen(prompt_path, freeze_path, schema):
    record = json.loads(Path(freeze_path).read_text(encoding='utf-8'))
    if (record.get('status') != 'FROZEN' or record.get('prompt_sha') != file_sha(prompt_path)
            or record.get('schema_sha') != sha(schema)):
        raise RuntimeError(f'{Path(prompt_path).name} does not match its freeze record; '
                           'a prompt change needs a versioned amendment')
    return lf_bytes(prompt_path).decode('utf-8'), record


# ---------------------------------------------------------------- plan
def variant_text(query, variant):
    return query['query'] if variant == 'a' else query['rephrase_b']


def generation_plan(queries, arms=ARMS, variants=GEN_VARIANTS, generations=GENERATIONS):
    """Every Text2Cypher slot. gen_index outermost, so the pooled gen 0 finishes first."""
    return [dict(arm=arm, prompt_version=version, qid=q['qid'], variant=variant, gen_index=g)
            for g in range(generations) for arm, version in arms.items()
            for q in queries for variant in variants]


def slot_key(row):
    return (row['arm'], row['qid'], row['variant'], row['gen_index'])


# ---------------------------------------------------------------- metering
class Ledger:
    """Append-only per-request ledger (one row per API request, including retries)."""

    def __init__(self, path):
        self.path = Path(path)
        self.rows = ([json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines()]
                     if self.path.exists() else [])

    def count(self, role):
        return sum(1 for row in self.rows if row['role'] == role)

    def add(self, **row):
        input_tokens, output_tokens = row.get('input_tokens') or 0, row.get('output_tokens') or 0
        rate = (input_tokens * PRICES['embedding'] if row['role'] == 'embedding'
                else input_tokens * PRICES['input'] + output_tokens * PRICES['output'])
        row = {**row, 'cost_usd_unverified': rate / 1e6}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(canonical(row) + '\n')
        self.rows.append(row)

    def summary(self, rows=None):
        out = {}
        for row in self.rows if rows is None else rows:
            s = out.setdefault(row['role'], Counter())
            s['requests'] += 1
            s[row['status']] += 1
            for k in ('input_tokens', 'output_tokens', 'reasoning_tokens'):
                s[k] += row.get(k) or 0
            s['cost_usd_unverified'] += row['cost_usd_unverified']
        return {role: dict(c) for role, c in out.items()}


class Pacer:
    """Sequential RPM/TPM pacing in a trailing 60 s window (90% headroom)."""

    def __init__(self, rpm=RPM, tpm=TPM, *, clock=time.monotonic, sleep=time.sleep, headroom=.9):
        self.rpm, self.tpm = rpm * headroom, tpm * headroom
        self.clock, self.sleep = clock, sleep
        self.events = deque()
        self.waited = 0.0

    def wait(self, estimate):
        while True:
            now = self.clock()
            while self.events and now - self.events[0][0] >= 60:
                self.events.popleft()
            if (len(self.events) < self.rpm and
                    (not self.events or sum(n for _, n in self.events) + estimate <= self.tpm)):
                return
            delay = max(.05, self.events[0][0] + 60 - now)
            self.sleep(delay)
            self.waited += delay

    def record(self, tokens):
        self.events.append((self.clock(), tokens))


def retry_delay(exc, attempt):
    from embed.embed import retry_after
    delay = retry_after(exc)
    return delay if delay is not None else min(2 ** attempt, 60)


def retryable(exc):
    status = getattr(exc, 'status_code', None)
    return (status in (408, 409, 429) or (isinstance(status, int) and status >= 500)
            or type(exc).__name__ in {'APIConnectionError', 'APITimeoutError'})


class MeteredClient:
    """Wraps an LLM client: hard budget, ledger, pacing, 429/Retry-After backoff, replay-only mode.

    Rate-limit retries happen here, so the retriever never sees a transport error (which it
    would otherwise turn into a feedback retry with a new cache key).
    """

    def __init__(self, inner, ledger, budget, *, pacer=None, replay_only=False, sleep=time.sleep,
                 max_attempts=6):
        self.inner, self.ledger, self.budget = inner, ledger, budget
        self.pacer = pacer or Pacer(sleep=sleep)
        self.replay_only, self.sleep, self.max_attempts = replay_only, sleep, max_attempts
        self.context = {}
        self.estimate = 4000
        self.replay_misses = 0

    provider = property(lambda self: self.inner.provider)
    model = property(lambda self: self.inner.model)
    effort = property(lambda self: self.inner.effort)
    role = property(lambda self: self.inner.role)

    def generate(self, request):
        if self.replay_only:
            self.replay_misses += 1
            raise ReplayMiss('cache miss in replay-only mode; never regenerated')
        for attempt in range(1, self.max_attempts + 1):
            if self.ledger.count(self.role) >= self.budget:
                raise BudgetExceeded(f'{self.role} call budget {self.budget} reached')
            self.pacer.wait(self.estimate)
            try:
                result = self.inner.generate(request)
            except Exception as exc:
                again = retryable(exc) and attempt < self.max_attempts
                status = getattr(exc, 'status_code', None)
                self.ledger.add(role=self.role, status='rate_limited' if status == 429 else
                                ('retryable_error' if again else 'error'),
                                error=type(exc).__name__, http_status=status, **self.context)
                self.pacer.record(0)
                if not again:
                    raise TransportError(f'{type(exc).__name__} after {attempt} attempt(s)') from exc
                self.sleep(retry_delay(exc, attempt))
                continue
            usage = result.get('usage') or {}
            tokens = (usage.get('input_tokens') or 0) + (usage.get('output_tokens') or 0)
            self.pacer.record(tokens)
            self.estimate = max(1000, tokens)
            self.ledger.add(role=self.role, status='ok', stop_reason=result.get('stop_reason'),
                            response_model=result.get('provenance', {}).get('response_model'),
                            input_tokens=usage.get('input_tokens'), output_tokens=usage.get('output_tokens'),
                            reasoning_tokens=usage.get('reasoning_tokens'),
                            max_completion_tokens=result.get('provenance', {}).get('max_completion_tokens'),
                            **self.context)
            return result
        raise TransportError('retries exhausted')  # pragma: no cover (loop always returns/raises)


class MeteredEmbedder:
    """Counts query-embedding requests against a hard budget; identity attributes pass through."""

    def __init__(self, inner, ledger, budget):
        self.__dict__.update(inner=inner, ledger=ledger, budget=budget)

    def __getattr__(self, name):
        return getattr(self.__dict__['inner'], name)

    def embed(self, texts):
        if self.ledger.count('embedding') >= self.budget:
            raise BudgetExceeded(f'embedding call budget {self.budget} reached')
        before = getattr(self.inner, 'api_requests', None)
        try:
            batch = self.inner.embed(texts)
        except Exception as exc:
            self.ledger.add(role='embedding', status='error', error=type(exc).__name__)
            raise
        sent = (self.inner.api_requests - before) if isinstance(before, int) else 1
        for _ in range(max(0, sent - 1)):
            self.ledger.add(role='embedding', status='retried')
        self.ledger.add(role='embedding', status='ok', input_tokens=batch.prompt_tokens,
                        response_model=batch.response_model)
        return batch


class ReplayOnlyCache(JsonlCache):
    """Replay-only Cypher cache: rows the retriever would append go to a separate miss log."""

    def __init__(self, path, divert):
        super().__init__(path)
        self.divert = JsonlCache(divert)

    def append(self, row):
        self.divert.append({k: row.get(k) for k in ('qid', 'variant', 'gen_index', 'attempt_no',
                                                     'prompt_version', 'validator_outcome')})


# ---------------------------------------------------------------- structured helper calls
def structured_request(system, content, schema, max_tokens):
    return dict(system=system, messages=[{'role': 'user', 'content': content}], max_tokens=max_tokens,
                output_config={'format': {'type': 'json_schema', 'schema': schema}})


def structured_call(client, system, content, schema, max_tokens):
    """One budget retry on truncation, like the Cypher path; any other stop is an error."""
    import jsonschema
    for budget in (max_tokens, 2 * max_tokens):
        result = client.generate(structured_request(system, content, schema, budget))
        stop = result['stop_reason']
        if stop == 'max_tokens' and budget == max_tokens:
            continue
        if stop != 'end_turn':
            raise HelperStop(f'{client.role} stop guard: {stop}')
        try:
            jsonschema.validate(result['data'], schema)
        except jsonschema.ValidationError as exc:
            raise HelperStop(f'{client.role} schema violation') from exc
        return result
    raise HelperStop(f'{client.role} truncated after one larger-budget retry')


def hyde_key(query, record, client):
    return dict(qid=query['qid'], variant='c', source_variant='a', query_text_sha=sha(query['query']),
                prompt_sha=record['prompt_sha'], schema_sha=record['schema_sha'], gen_index=0,
                provider=client.provider, role='s8_hyde', requested_model=client.model, effort=client.effort)


def hyde_content(query):
    """Variant-a text only: never the narrative, rephrase or any clause text."""
    return f"Question: {query['query']}"


def get_hyde(query, client, cache, system, record):
    key = hyde_key(query, record, client)
    row = cache.find(key)
    if row is not None:
        return row['passage']
    result = structured_call(client, system, hyde_content(query), HYDE_SCHEMA, record['max_tokens'])
    passage = result['data']['passage'].strip()
    if not passage:
        raise HelperStop('empty HyDE passage')
    cache.append(dict(key=key, passage=passage, reusable=True, provenance=result.get('provenance', {}),
                      usage=result.get('usage', {})))
    return passage


def cached_hyde(queries, cache, record, client):
    out = {}
    for q in queries:
        row = cache.find(hyde_key(q, record, client))
        if row is not None:
            out[q['qid']] = row['passage']
    return out


# ---------------------------------------------------------------- v1-noob
_MASK = re.compile(r"//[^\n]*|/\*.*?\*/|'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", re.S)
_OBLIGATION_REF = re.compile(r'\.\s*`?obligation`?\b', re.I)
_EXACT = re.compile(r'\bc\s*\.\s*obligation\s*=\s*\$[A-Za-z_]\w*')
_TOKEN = re.compile(r'\$?[A-Za-z_]\w*|\d+(?:\.\d+)?|<>|<=|>=|=~|\S')
_CLAUSE_END = {'RETURN', 'WITH', 'MATCH', 'OPTIONAL', 'UNWIND', 'CALL', 'ORDER', 'UNION', 'SKIP',
               'LIMIT', 'CREATE', 'MERGE', 'SET', 'DELETE', 'REMOVE', 'FOREACH'}


def _mask(query):
    """Blank comments and string literals, keeping every offset."""
    return _MASK.sub(lambda m: ' ' * len(m.group()), query)


def noob_rewrite(cypher):
    """Pre-registered v1-noob rule (ADR 0028 + C1 gate).

    Returns (status, rewritten, reason) with status 'rewritten' | 'equal_v1' | 'n/a'.
    Only `c.obligation = $param` that is a TOP-LEVEL AND conjunct of a WHERE is replaced
    with `true`; anything else that mentions obligation is ambiguous ('n/a').
    """
    masked = _mask(cypher or '')
    refs = list(_OBLIGATION_REF.finditer(masked))
    if not refs:
        return 'equal_v1', None, 'no obligation predicate'
    if len(refs) > 1:
        return 'n/a', None, 'more than one obligation reference'
    exact = list(_EXACT.finditer(masked))
    if len(exact) != 1 or not exact[0].start() <= refs[0].start() < exact[0].end():
        return 'n/a', None, 'obligation predicate not of the form c.obligation = $param'
    start, end = exact[0].span()
    tokens, depth = [], 0
    for match in _TOKEN.finditer(masked):
        text = match.group()
        if text in ')]}':
            depth -= 1
        tokens.append((match.start(), match.end(), text.upper(), depth))
        if text in '([{':
            depth += 1
    at = next(i for i, t in enumerate(tokens) if t[0] == start)
    if tokens[at][3] != 0:
        return 'n/a', None, 'predicate inside a nested or bracketed expression'
    if at == 0 or tokens[at - 1][2] not in {'WHERE', 'AND'}:
        return 'n/a', None, 'predicate not preceded by WHERE or AND'
    after = next((i for i, t in enumerate(tokens) if t[0] >= end), None)
    if after is not None and not (tokens[after][2] == 'AND' or
                                  (tokens[after][3] == 0 and tokens[after][2] in _CLAUSE_END)):
        return 'n/a', None, 'predicate not followed by AND or the end of the WHERE'
    where = None
    for i in range(at - 1, -1, -1):
        if tokens[i][3] != 0:
            continue
        if tokens[i][2] == 'WHERE':
            where = i
            break
        if tokens[i][2] in _CLAUSE_END:
            break
    if where is None:
        return 'n/a', None, 'predicate not inside a WHERE'
    stop = next((i for i in range(at + 1, len(tokens))
                 if tokens[i][3] == 0 and tokens[i][2] in _CLAUSE_END), len(tokens))
    if any(t[3] == 0 and t[2] in {'OR', 'XOR', 'NOT'} for t in tokens[where + 1:stop]):
        return 'n/a', None, 'WHERE has a top-level OR / XOR / NOT'
    return 'rewritten', cypher[:start] + 'true' + cypher[end:], 'top-level AND conjunct of WHERE'


def noob_candidates(r, cypher, params):
    """Unchanged validator + read-only EXPLAIN, then a read-routed run. No LLM."""
    query, _ = validate_cypher(cypher, r.labels, r.rels)
    r._explain_is_read_only(query, params)
    return list(dict.fromkeys(r._run_ids(query, params)))


# ---------------------------------------------------------------- generate
def read_jsonl(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def append_jsonl(path, row):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(canonical(row) + '\n')


def generate(r, queries, out, *, hyde_client, hyde_system, hyde_record, generations=GENERATIONS):
    """HyDE (1 per query) -> query embeddings (a, b, c) -> Text2Cypher slots + stage 1 -> v1-noob.

    Resumable: finished slots are skipped; a budget stop leaves the rest for the next run.
    Returns a status dict (counts only).
    """
    out = Path(out)
    status = Counter()
    stopped = None
    hyde_cache = JsonlCache(out / 'hyde_cache.jsonl')
    by_qid = {q['qid']: q for q in queries}
    try:
        for q in queries:
            try:
                get_hyde(q, hyde_client, hyde_cache, hyde_system, hyde_record)
            except (HelperStop, TransportError) as exc:
                status['hyde_error'] += 1
                log.warning('HyDE %s failed: %s', q['qid'], type(exc).__name__)
        passages = cached_hyde(queries, hyde_cache, hyde_record, hyde_client)
        for q in queries:
            for text in (q['query'], q['rephrase_b'], passages.get(q['qid'])):
                if text:
                    r.query_embeddings.get(text)
        done = {slot_key(row) for row in read_jsonl(out / 'generations.jsonl')}
        for slot in generation_plan(queries, generations=generations):
            if slot_key(slot) in done:
                continue
            q = by_qid[slot['qid']]
            text = variant_text(q, slot['variant'])
            run = RunLog(query=text, qid=slot['qid'], variant=slot['variant'], gen_index=slot['gen_index'],
                         prompt_version=slot['prompt_version'])
            r.llm.context = dict(slot)
            outcome = 'ok'
            try:
                ids = r.stage1_filter(text, run, None)
            except BudgetExceeded:
                raise
            except (TransportError, ReplayMiss) as exc:
                status['slot_transport_error'] += 1
                log.warning('slot %s left open: %s', slot_key(slot), type(exc).__name__)
                continue
            except CypherGenerationError as exc:
                outcome, ids = 'generation_error', []
                log.warning('slot %s generation error: %s', slot_key(slot), exc)
            append_jsonl(out / 'generations.jsonl', dict(
                **slot, status=outcome, filter_path=run.filter_path, attempts=run.attempts,
                validator_outcome=run.validator_outcome, cypher=run.cypher, params=run.params,
                cypher_sha=sha(run.cypher) if run.cypher else None, obligation_filter=run.obligation_filter,
                candidate_ids=sorted(ids), candidate_count=len(ids), candidate_set_sha=sha(sorted(ids)),
                new_calls=sum(1 for g in run.generations if not g.get('cache_hit')),
                cache_hits=sum(1 for g in run.generations if g.get('cache_hit')), warnings=len(run.warnings)))
            status['slots_written'] += 1
    except BudgetExceeded as exc:
        stopped = str(exc)
    derive_noob(r, out)
    return dict(status, stopped=stopped)


def derive_noob(r, out):
    out = Path(out)
    done = {(row['qid'], row['variant'], row['gen_index']) for row in read_jsonl(out / 'noob.jsonl')}
    for row in read_jsonl(out / 'generations.jsonl'):
        key = (row['qid'], row['variant'], row['gen_index'])
        if row['arm'] != 'v1' or key in done:
            continue
        if row['status'] != 'ok':
            result = dict(status='n/a', reason='v1 generation error', candidate_ids=None)
        else:
            outcome, rewritten, reason = noob_rewrite(row['cypher'])
            result = dict(status=outcome, reason=reason, candidate_ids=None)
            if outcome == 'equal_v1':
                result['candidate_ids'] = row['candidate_ids']
            elif outcome == 'rewritten':
                try:
                    ids = noob_candidates(r, rewritten, row['params'])
                    result.update(cypher=rewritten, cypher_sha=sha(rewritten), candidate_ids=sorted(ids))
                except Exception as exc:  # validator / EXPLAIN / run rejected the rewrite
                    result = dict(status='n/a', reason=f'rewrite rejected: {type(exc).__name__}',
                                  candidate_ids=None)
            if result['status'] == 'n/a':
                log.info('v1-noob n/a for %s: %s', key, result['reason'])
        if result['candidate_ids'] is not None:
            result['candidate_count'] = len(result['candidate_ids'])
        append_jsonl(out / 'noob.jsonl', dict(qid=row['qid'], variant=row['variant'],
                                              gen_index=row['gen_index'], **result))
        done.add(key)


def generation_report(gen_rows, noob_rows, queries):
    """Counts only (allowed under blindness)."""
    report = {}
    for arm in ARMS:
        rows = [row for row in gen_rows if row['arm'] == arm]
        gen0 = [row for row in rows if row['gen_index'] == 0]
        report[arm] = dict(
            slots=len(rows),
            status=dict(Counter(row['status'] for row in rows)),
            validator_outcomes=dict(Counter(row['validator_outcome'] or '(none)' for row in rows)),
            fallback_paths=dict(Counter(row['filter_path'] for row in rows)),
            attempts=dict(Counter(row['attempts'] for row in rows)),
            gen0_obligation_filter={v: sum(row['obligation_filter'] for row in gen0 if row['variant'] == v)
                                    for v in GEN_VARIANTS},
            gen0_candidates_a={row['qid']: row['candidate_count'] for row in gen0 if row['variant'] == 'a'},
            stability={})
        for q in queries:
            for v in GEN_VARIANTS:
                group = [row for row in rows if row['qid'] == q['qid'] and row['variant'] == v]
                if group:
                    report[arm]['stability'][f"{q['qid']}/{v}"] = dict(
                        gens=len(group), cypher=len({row['cypher_sha'] for row in group}),
                        candidate_sets=len({row['candidate_set_sha'] for row in group}),
                        obligation_gens=sum(row['obligation_filter'] for row in group))
    report[NOOB] = dict(all_gens=dict(Counter(row['status'] for row in noob_rows)),
                        gen0=dict(Counter(row['status'] for row in noob_rows if row['gen_index'] == 0)),
                        na_reasons=dict(Counter(row['reason'] for row in noob_rows if row['status'] == 'n/a')))
    return report


# ---------------------------------------------------------------- pool
def retrieve_defaults():
    params = inspect.signature(HybridRetriever.retrieve).parameters
    return {k: params[k].default for k in ('per_seed', 'neighbour_cap', 'rel_floor')}


def stages_2_4(r, qvec, ids, top_k):
    """Stages 2-4 exactly as retrieve() runs them, for a given candidate set and query vector.
    Returns (stage-2 ranked ids, stage-3 neighbour ids)."""
    d = retrieve_defaults()
    ranked = r.stage2_rank(qvec, list(ids), top_k)
    neighbours = r.stage3_expand(qvec, ranked, d['per_seed'], d['neighbour_cap'], d['rel_floor'])
    final = [h.clause_id for h in r.stage4_fetch(ranked + neighbours)]
    assert final == [h.clause_id for h in ranked + neighbours]
    return final[:len(ranked)], final[len(ranked):]


def split_run(run):
    """(stage-2 ranked ids, neighbour ids) of a retrieve() RunLog; ids only, no scores."""
    ranked = [cid for cid, _ in run.stage2_ranked]
    return ranked, [cid for cid, *_ in run.stage3_added]


def all_clause_ids(r):
    records, _, _ = r.driver.execute_query('MATCH (c:Clause) RETURN c.id AS clause_id',
                                           database_=r.db, routing_='r')
    return sorted(rec['clause_id'] for rec in records)


def build_runs(r, queries, clauses, out, *, hyde_record, hyde_client):
    """Every pooled (and scored) run, gen_index 0 only, all from cache. Returns run records."""
    out = Path(out)
    passages = cached_hyde(queries, JsonlCache(out / 'hyde_cache.jsonl'), hyde_record, hyde_client)
    noob = {(row['qid'], row['variant']): row for row in read_jsonl(out / 'noob.jsonl') if row['gen_index'] == 0}
    # Addendum 3: a gen-0 slot that ended in a terminal generation failure is a system failure:
    # an empty stage-1 result for that slot and every run derived from it (never regenerated).
    failed = {(row['arm'], row['qid'], row['variant']) for row in read_jsonl(out / 'generations.jsonl')
              if row['gen_index'] == 0 and row['status'] == 'generation_error'}
    EMPTY = ([], [])
    universe = all_clause_ids(r)
    bm25 = None
    if clauses is not None:
        from retrieval_eval.bm25 import BM25
        bm25 = BM25(clauses)
    runs = []

    def add(qid, system, arm, variant, top_k, lists=None, status='ok', **extra):
        """lists = (stage-2 ranked ids, neighbour ids); final_ids = ranked + neighbours."""
        ranked, neighbours = lists if lists is not None else (None, None)
        runs.append(dict(qid=qid, system=system, arm=arm, variant=variant, top_k=top_k, gen_index=0,
                         status=status, ranked_ids=ranked, neighbour_ids=neighbours,
                         final_ids=None if ranked is None else ranked + neighbours, **extra))

    for q in queries:
        qid = q['qid']
        texts = {'a': q['query'], 'b': q['rephrase_b'], 'c': passages.get(qid)}
        vectors = {v: r.embed(t) for v, t in texts.items() if t}
        v1_lists = {}
        for arm, version in ARMS.items():
            for variant in GEN_VARIANTS:
                for k in TOP_KS:
                    if (arm, qid, variant) in failed:
                        add(qid, 'pipeline', arm, variant, k, EMPTY, status='system_failure')
                        continue
                    try:
                        _, run = r.retrieve(texts[variant], k, qid=qid, variant=variant, gen_index=0,
                                            prompt_version=version)
                        add(qid, 'pipeline', arm, variant, k, split_run(run))
                        if arm == 'v1':
                            v1_lists[(variant, k)] = split_run(run)
                    except CypherGenerationError as exc:
                        add(qid, 'pipeline', arm, variant, k, status='replay_miss', reason=type(exc).__name__)
            if 'c' not in vectors:
                for k in TOP_KS:
                    add(qid, 'pipeline', arm, 'c', k, status='n/a', reason='no HyDE passage')
                continue
            if (arm, qid, 'a') in failed:
                for k in TOP_KS:
                    add(qid, 'pipeline', arm, 'c', k, EMPTY, status='system_failure')
                continue
            stage1 = RunLog(query=texts['a'], qid=qid, variant='a', gen_index=0, prompt_version=version)
            try:
                ids = r.stage1_filter(texts['a'], stage1, None)
            except CypherGenerationError as exc:
                for k in TOP_KS:
                    add(qid, 'pipeline', arm, 'c', k, status='replay_miss', reason=type(exc).__name__)
                continue
            for k in TOP_KS:
                final = stages_2_4(r, vectors['c'], ids, k)
                add(qid, 'pipeline', arm, 'c', k, final)
                if arm == 'v1':
                    v1_lists[('c', k)] = final
        for variant in ('a', 'b', 'c'):
            source = noob.get((qid, 'a' if variant == 'c' else variant))
            for k in TOP_KS:
                if ('v1', qid, 'a' if variant == 'c' else variant) in failed:
                    add(qid, 'pipeline', NOOB, variant, k, EMPTY, status='system_failure', derived_from='v1')
                elif source is None or variant not in vectors:
                    add(qid, 'pipeline', NOOB, variant, k, status='n/a', reason='no v1 gen-0 record')
                elif source['status'] == 'equal_v1':
                    ids = v1_lists.get((variant, k))
                    add(qid, 'pipeline', NOOB, variant, k, ids, status='ok' if ids is not None else 'n/a',
                        derived_from='v1')
                elif source['status'] == 'rewritten':
                    add(qid, 'pipeline', NOOB, variant, k, stages_2_4(r, vectors[variant], source['candidate_ids'], k))
                else:
                    add(qid, 'pipeline', NOOB, variant, k, status='n/a', reason=source['reason'])
        for variant in GEN_VARIANTS:
            ranked = r.stage2_rank(vectors[variant], universe, FILTER_OFF_DEPTH)
            add(qid, 'vector_all', None, variant, FILTER_OFF_DEPTH, ([h.clause_id for h in ranked], []))
            if bm25 is not None:
                add(qid, 'bm25', None, variant, FILTER_OFF_DEPTH,
                    ([cid for cid, _ in bm25.rank(texts[variant], FILTER_OFF_DEPTH)], []))
        if 'c' in vectors:
            ranked = r.stage2_rank(vectors['c'], universe, FILTER_OFF_DEPTH)
            add(qid, 'hyde', None, 'c', FILTER_OFF_DEPTH, ([h.clause_id for h in ranked], []))
        for limit in LIMITS:
            for repeat in range(LIMIT_REPEATS):
                for k in TOP_KS:
                    if ('v1', qid, 'a') in failed:
                        add(qid, 'limit', 'v1', 'a', k, EMPTY, status='system_failure', limit=limit, repeat=repeat)
                        continue
                    try:
                        _, run = r.retrieve(texts['a'], k, inject_limit=limit, qid=qid, variant='a',
                                            gen_index=0, prompt_version=ARMS['v1'])
                        add(qid, 'limit', 'v1', 'a', k, split_run(run), limit=limit, repeat=repeat)
                    except CypherGenerationError as exc:
                        add(qid, 'limit', 'v1', 'a', k, status='replay_miss', reason=type(exc).__name__,
                            limit=limit, repeat=repeat)
    return runs


def pool_from_runs(runs, queries):
    """Union of every pooled run's final ids, with the sealed provenance per (qid, clause_id)."""
    order = {q['qid']: i for i, q in enumerate(queries)}
    entries = defaultdict(list)
    for run in runs:
        if run['gen_index'] != 0:
            raise ValueError('Only gen_index 0 runs enter the pool')
        if run['status'] != 'ok':
            continue
        if len(set(run['final_ids'])) != len(run['final_ids']):
            raise ValueError('A run returned a duplicate clause id')
        n_ranked = len(run['ranked_ids'])
        for rank, cid in enumerate(run['final_ids'], 1):
            entries[(run['qid'], cid)].append({k: run[k] for k in ('system', 'arm', 'variant', 'top_k')}
                                              | {'rank': rank, 'stage': 'ranked' if rank <= n_ranked else 'neighbour',
                                                 'limit': run.get('limit'), 'repeat': run.get('repeat')})
    keys = sorted(entries, key=lambda k: (order[k[0]], k[1]))
    key_rows = [dict(qid=qid, clause_id=cid, provenance=sorted(entries[(qid, cid)], key=canonical))
                for qid, cid in keys]
    pool_rows = [dict(qid=qid, clause_id=cid) for qid, cid in keys]
    return pool_rows, key_rows


def jsonl_text(rows):
    return ''.join(canonical(row) + '\n' for row in rows)


def write_once(path, text):
    """Never overwrite: identical content is a no-op, different content is an error."""
    path = Path(path)
    data = text.encode('utf-8') if isinstance(text, str) else text
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f'{path.name} exists with different content; S8 outputs are never overwritten')
        return 'unchanged'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return 'written'


def bytes_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_pool(r, queries, clauses, out, *, hyde_record, hyde_client):
    runs = build_runs(r, queries, clauses, out, hyde_record=hyde_record, hyde_client=hyde_client)
    pool_rows, key_rows = pool_from_runs(runs, queries)
    out = Path(out)
    states = {name: write_once(out / name, jsonl_text(rows)) for name, rows in
              (('runs.jsonl', runs), ('pool_key.jsonl', key_rows), ('pool.jsonl', pool_rows))}
    return dict(states=states, runs=len(runs), run_status=dict(Counter(
        f"{run['system']}/{run['status']}" for run in runs)), pool=pool_counts(pool_rows, queries))


def pool_counts(pool_rows, queries):
    sizes = Counter(row['qid'] for row in pool_rows)
    return dict(per_query={q['qid']: sizes.get(q['qid'], 0) for q in queries}, total=len(pool_rows))


# ---------------------------------------------------------------- sheet
def included_pairs(key_rows, depth=None, depth_bc=None):
    """Depth rule (decided at the C2 gate from counts only); applied to every system alike."""
    keep = []
    for row in key_rows:
        for entry in row['provenance']:
            limit = depth_bc if entry['variant'] in ('b', 'c') else depth
            if limit is None or entry['rank'] <= limit:
                keep.append((row['qid'], row['clause_id']))
                break
    return keep


def common_rules_text(rules):
    return '\n'.join(f'{name}: {rules[name]}' for name in JUDGING_RULES)


def build_sheet(queries, rules, clauses, pairs, seed=SHEET_SEED):
    cmap = {c['id']: c for c in clauses}
    missing = sorted({cid for _, cid in pairs if cid not in cmap})
    if missing:
        raise ValueError(f'{len(missing)} pooled clause id(s) missing from clauses.json')
    rng = random.Random(seed)
    by_qid = defaultdict(list)
    for qid, cid in pairs:
        by_qid[qid].append(cid)
    rows, item_map, used = [], [], set()
    for q in queries:
        cids = sorted(set(by_qid.get(q['qid'], [])))
        rng.shuffle(cids)
        for i, cid in enumerate(cids):
            while (item_id := 'S8-' + format(rng.getrandbits(40), '010x')) in used:
                pass
            used.add(item_id)
            clause = cmap[cid]
            rows.append(dict(item_id=item_id, qid=q['qid'], query=q['query'], jurisdiction=q['jurisdiction'] or '',
                             narrative_grade3=q['narrative']['grade3'], narrative_grade2=q['narrative']['grade2'],
                             narrative_grade1_traps=q['narrative']['grade1_traps'],
                             common_rules=common_rules_text(rules) if i == 0 else '',
                             source_doc=clause['doc'], clause_id=cid, title=clause['title'], text=clause['text'],
                             grade='', note=''))
            item_map.append(dict(item_id=item_id, qid=q['qid'], clause_id=cid))
    return rows, item_map


def sheet_csv(rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=SHEET_COLUMNS, lineterminator='\r\n')
    writer.writeheader()
    writer.writerows(rows)
    return '﻿' + stream.getvalue()  # UTF-8 BOM so Excel opens it as UTF-8


def run_sheet(queries, rules, clauses, out, *, depth=None, depth_bc=None):
    out = Path(out)
    key_rows = read_jsonl(out / 'pool_key.jsonl')
    pairs = included_pairs(key_rows, depth, depth_bc)
    rows, item_map = build_sheet(queries, rules, clauses, pairs)
    folder = out / 'sheet'
    meta = dict(depth=depth, depth_bc=depth_bc, seed=SHEET_SEED, items=len(rows),
                pool_key_sha=bytes_sha(out / 'pool_key.jsonl'), columns=SHEET_COLUMNS)
    states = {'s8_sheet.csv': write_once(folder / 's8_sheet.csv', sheet_csv(rows)),
              'item_map.jsonl': write_once(folder / 'item_map.jsonl', jsonl_text(item_map)),
              'sheet_meta.json': write_once(folder / 'sheet_meta.json', json.dumps(meta, indent=2) + '\n')}
    try:
        import openpyxl  # noqa: F401  (optional; never installed by this module)
        states['xlsx'] = 'openpyxl present but xlsx writer not enabled'
    except ImportError:
        states['xlsx'] = 'skipped (openpyxl not installed)'
    return dict(states=states, items=len(rows), per_query=dict(Counter(r['qid'] for r in rows)))


# ---------------------------------------------------------------- pre-labels
def prelabel_content(query, rules, *, doc, title, text):
    """Only the query, its narrative, the judging rules and one clause's source/title/text.
    No system, arm, variant, rank, score or graph obligation label can reach this function."""
    n = query['narrative']
    return '\n'.join([
        f"QUERY: {query['query']}",
        f"JURISDICTION NAMED BY THE QUERY: {query['jurisdiction'] or 'none'}",
        'NARRATIVE:',
        f"- grade 3: {n['grade3']}",
        f"- grade 2: {n['grade2']}",
        f"- at most grade 1 (traps): {n['grade1_traps']}",
        'RULES:',
        *[f'- {name}: {rules[name]}' for name in JUDGING_RULES],
        'CLAUSE:',
        f'source document: {doc}',
        f'title: {title}',
        'text:',
        text,
    ])


def prelabel_key(item_id, content, record, client):
    return dict(item_id=item_id, input_sha=sha(content), prompt_sha=record['prompt_sha'],
                schema_sha=record['schema_sha'], provider=client.provider, role='s8_prelabel',
                requested_model=client.model, effort=client.effort)


def run_prelabels(queries, rules, clauses, out, client, system, record):
    out = Path(out)
    items = read_jsonl(out / 'sheet' / 'item_map.jsonl')
    cmap = {c['id']: c for c in clauses}
    by_qid = {q['qid']: q for q in queries}
    cache = JsonlCache(out / 'prelabels.jsonl')
    status, stopped = Counter(), None
    for item in items:
        clause = cmap[item['clause_id']]
        content = prelabel_content(by_qid[item['qid']], rules, doc=clause['doc'],
                                   title=clause['title'], text=clause['text'])
        key = prelabel_key(item['item_id'], content, record, client)
        if cache.find(key) is not None:
            status['replayed'] += 1
            continue
        client.context = {'item_id': item['item_id']}
        try:
            result = structured_call(client, system, content, PRELABEL_SCHEMA, record['max_tokens'])
        except BudgetExceeded as exc:
            stopped = str(exc)
            break
        except (HelperStop, TransportError) as exc:
            status['error'] += 1
            log.warning('pre-label %s failed: %s', item['item_id'], type(exc).__name__)
            continue
        cache.append(dict(key=key, item_id=item['item_id'], **{f: result['data'][f] for f in PRELABEL_FIELDS},
                          reusable=True,
                          provenance=result.get('provenance', {}), usage=result.get('usage', {})))
        status['labelled'] += 1
    labels = {row['item_id']: row['grade'] for row in read_jsonl(out / 'prelabels.jsonl') if row.get('reusable')}
    return dict(status, stopped=stopped, items=len(items), labelled_total=len(labels),
                grade_distribution=dict(sorted(Counter(labels.values()).items())))


# ---------------------------------------------------------------- live wiring
def output_dir(provider='azure_openai'):
    return ROOT / 'data' / 'runs' / provider / 's8' / FAMILY


def require_live():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    if os.getenv('REGCLAW_STUB') == '1':
        raise SystemExit('S8 live commands are disabled in stub mode (REGCLAW_STUB=1)')
    if os.getenv('LLM_PROVIDER') != 'azure_openai':
        raise SystemExit('S8 Part C runs on the provisional Azure provider: set LLM_PROVIDER=azure_openai')


def live_retriever(ledger, *, budget, embed_budget, replay_only):
    from embed.embed import make_embedder
    from llm.provider import make_client
    out = output_dir()
    llm = MeteredClient(make_client('s8_cypher'), ledger, budget, replay_only=replay_only)
    embedder = MeteredEmbedder(make_embedder(), ledger, embed_budget)
    r = HybridRetriever(run_family=FAMILY, llm=llm, embedder=embedder)
    if replay_only:
        r.cypher_cache = ReplayOnlyCache(r.cypher_cache.path, out / 'replay_misses.jsonl')
    frozen = json.loads(CYPHER_FREEZE.read_text(encoding='utf-8'))
    from retrieval.hybrid_retriever import canonical_schema
    if sha(canonical_schema(r.schema)) != frozen['live_schema_sha_at_freeze']:
        r.close()
        raise SystemExit('Live graph schema differs from the schema at prompt freeze; stopping')
    return r


def load_inputs(need_clauses=True):
    import yaml
    from retrieval_eval.pool import read_queries
    queries = read_queries(QUERIES)
    if file_sha(QUERIES) != '83d7813873d6d9e47a732db8778f68c18cfb4654834acfa16ca6847457affc7c':
        raise SystemExit('queries.yaml differs from the frozen version')
    rules = yaml.safe_load(QUERIES.read_text(encoding='utf-8'))['common_rules']
    clauses = json.loads(CLAUSES.read_text(encoding='utf-8')) if need_clauses else None
    return queries, rules, clauses


def print_json(label, value):
    print(f'{label}: ' + json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog='tasks.py', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    gen = sub.add_parser('s8-generate', help='HyDE + query embeddings + Text2Cypher gens + stage 1 + v1-noob')
    gen.add_argument('--budget', type=int, default=900, help='s8_cypher API requests incl. retries (cumulative)')
    gen.add_argument('--hyde-budget', type=int, default=16)
    gen.add_argument('--embed-budget', type=int, default=60)
    sub.add_parser('s8-pool', help='All pooled runs from cache (0 LLM / 0 embedding calls)')
    sheet = sub.add_parser('s8-sheet', help='Blind labelling sheet + sealed item map (offline)')
    sheet.add_argument('--depth', type=int)
    sheet.add_argument('--depth-bc', type=int)
    pre = sub.add_parser('s8-prelabel', help='LLM pre-labels for every sheet item (separate file)')
    pre.add_argument('--budget', type=int, help='default: items + 5%%')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(levelname)s %(message)s')
    out = output_dir()

    if args.command == 's8-sheet':
        queries, rules, clauses = load_inputs()
        result = run_sheet(queries, rules, clauses, out, depth=args.depth, depth_bc=args.depth_bc)
        print_json('sheet', result)
        for name in ('s8_sheet.csv', 'item_map.jsonl'):
            print(f'{out / "sheet" / name}  sha256={bytes_sha(out / "sheet" / name)}')
        return 0

    require_live()
    from llm.provider import make_client
    ledger = Ledger(out / 'cost_ledger.jsonl')
    start = len(ledger.rows)
    if args.command == 's8-prelabel':
        queries, rules, clauses = load_inputs()
        system, record = load_frozen(PRELABEL_PROMPT, PRELABEL_FREEZE, PRELABEL_SCHEMA)
        items = len(read_jsonl(out / 'sheet' / 'item_map.jsonl'))
        budget = args.budget if args.budget is not None else math.ceil(items * 1.05)
        client = MeteredClient(make_client('s8_prelabel'), ledger, budget)
        result = run_prelabels(queries, rules, clauses, out, client, system, record)
        print_json('prelabel', result)
        print_json('ledger (this run)', ledger.summary(ledger.rows[start:]))
        print_json('ledger (cumulative)', ledger.summary())
        print(f'{out / "prelabels.jsonl"}  sha256={bytes_sha(out / "prelabels.jsonl")}')
        return 0 if result['stopped'] is None else 2

    hyde_system, hyde_record = load_frozen(HYDE_PROMPT, HYDE_FREEZE, HYDE_SCHEMA)
    if args.command == 's8-generate':
        queries, _, _ = load_inputs(need_clauses=False)
        r = live_retriever(ledger, budget=args.budget, embed_budget=args.embed_budget, replay_only=False)
        hyde = MeteredClient(make_client('s8_hyde'), ledger, args.hyde_budget)
        try:
            result = generate(r, queries, out, hyde_client=hyde, hyde_system=hyde_system, hyde_record=hyde_record)
        finally:
            r.close()
        print_json('generate', result)
        print_json('ledger (this run)', ledger.summary(ledger.rows[start:]))
        print_json('ledger (cumulative)', ledger.summary())
        print_json('report', generation_report(read_jsonl(out / 'generations.jsonl'),
                                               read_jsonl(out / 'noob.jsonl'), queries))
        return 0 if result['stopped'] is None else 2

    # s8-pool: replay only, zero budgets.
    queries, _, clauses = load_inputs()
    r = live_retriever(ledger, budget=0, embed_budget=0, replay_only=True)
    hyde = MeteredClient(make_client('s8_hyde'), ledger, 0, replay_only=True)
    try:
        result = run_pool(r, queries, clauses, out, hyde_record=hyde_record, hyde_client=hyde)
    finally:
        r.close()
    print_json('pool', result)
    print_json('ledger (this run)', ledger.summary(ledger.rows[start:]))
    for name in ('pool.jsonl', 'pool_key.jsonl', 'runs.jsonl'):
        print(f'{out / name}  sha256={bytes_sha(out / name)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
