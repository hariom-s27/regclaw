"""
RegClaw rebuild — Phase 5: 4-stage hybrid retriever.

  Stage 1  Graph filter   : Claude Haiku -> read-only Cypher -> candidate clause ids (Neo4j)
  Stage 2  Semantic rank  : exact cosine ranking of ONLY those ids (Postgres + pgvector)
  Stage 3  Neighbours     : structural parent/child/sibling clauses (same standard), capped + re-scored
  Stage 4  Fetch text     : full clause text from Neo4j (the source of truth)

Usage
  python hybrid_retriever.py "ethylene oxide sterilization residuals"
  python hybrid_retriever.py "knee implant biocompatibility tests" --inject-limit 10   # reproduce the LIMIT bug
  python hybrid_retriever.py --compare "mandatory requirements for biological evaluation"  # bug vs fixed side by side

Versions this was written against (PyPI, checked 2026-10-07):
  neo4j 6.3.1 · anthropic 1.11.0 · openai (any 1.x with embeddings.create) · psycopg 3 · pgvector 0.5.0
  Neo4j server 5.x (docker image neo4j:5) · pgvector extension 0.8.x (image pgvector/pgvector:pg16)

Assumed graph shape (edit SCHEMA_FALLBACK / the expansion query if yours differs):
  (:Clause {id, number, title, text, obligation})-[:BELONGS_TO]->(:Standard {id, number, title})
  (:Clause)-[:MENTIONS]->(:Chemical|BiologicalEffect|DeviceType|RegulatoryActor {name})
  (:Clause)-[:REFERENCES]->(:Standard)
Postgres table: clause_embeddings(clause_id text primary key, embedding vector(1536))
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dotenv import load_dotenv
# Preserve the documented direct-script entry point for the shared tracer.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from observability.tracing import stage, traced
from llm.provider import make_client, provenance
from llm.cypher import request as cypher_request, parse_output
from retrieval.cypher_cache import JsonlCache, cache_path, generation_key, sha
from retrieval.query_embeddings import DEFAULT_IDENTITY, QueryEmbeddings, check_identity
from retrieval.setup import FULLTEXT_INDEX

load_dotenv()
log = logging.getLogger("hybrid")
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")

EMBED_MODEL = "text-embedding-3-small"
PG_TABLE = os.getenv("PG_TABLE", "clause_embeddings")

# --------------------------------------------------------------------------------------
# Stage 1 helpers: schema, prompt, validation
# --------------------------------------------------------------------------------------
SCHEMA_FALLBACK = """Node labels and properties:
  Clause {id: STRING, number: STRING, title: STRING, text: STRING, obligation: STRING}
  Standard {id: STRING, number: STRING, title: STRING}
  Module {name: STRING}
  LifecyclePhase {name: STRING}
  RegulatoryAuthority {name: STRING}
  Chemical {name: STRING}
  BiologicalEffect {name: STRING}
  DeviceType {name: STRING}
  RegulatoryActor {name: STRING}
Relationship patterns:
  (:Clause)-[:BELONGS_TO]->(:Standard)
  (:Standard)-[:BELONGS_TO]->(:Module)
  (:Clause)-[:MENTIONS]->(:Chemical)
  (:Clause)-[:MENTIONS]->(:BiologicalEffect)
  (:Clause)-[:MENTIONS]->(:DeviceType)
  (:Clause)-[:MENTIONS]->(:RegulatoryActor)
  (:Clause)-[:MENTIONS]->(:LifecyclePhase)
  (:Clause)-[:REFERENCES]->(:Standard)
  (:Standard)-[:REGULATED_BY]->(:RegulatoryAuthority)"""


def canonical_schema(schema):
    """Canonicalise the documented nodes, properties, obligations and patterns."""
    nodes = []
    for label, properties in re.findall(r'^\s{2}(\w+) \{([^}]+)\}', schema, re.M):
        nodes.append((label, ', '.join(sorted(p.strip() for p in properties.split(',')))))
    patterns = sorted(set(re.findall(r'\(:\w+\)-\[:\w+\]->\(:\w+\)', schema)))
    values = re.search(r'Clause.obligation values: (\[[^\n]+\])', schema)
    obligations = sorted(ast.literal_eval(values[1])) if values else sorted(['SHALL', 'SHOULD', 'MAY', 'NONE'])
    lines = ['Node labels and properties:']
    lines += [f'  {label} {{{props}}}' for label, props in sorted(nodes)]
    lines += [f'  // Clause.obligation values: {obligations}', 'Relationship patterns (direction matters):']
    lines += ['  ' + pattern for pattern in patterns]
    return '\n'.join(lines)


SCHEMA_FALLBACK = canonical_schema(SCHEMA_FALLBACK)


def introspect_schema(driver, database: str = "neo4j") -> str:
    """Read the LIVE schema so the prompt never drifts from the graph. No APOC needed.
    At ~4.4k nodes / 12.4k rels a full pattern scan is cheap."""
    try:
        props, _, _ = driver.execute_query(
            "MATCH (n) UNWIND labels(n) AS l UNWIND keys(n) AS k "
            "RETURN l AS label, collect(DISTINCT k) AS keys ORDER BY label",
            database_=database, routing_="r")
        pats, _, _ = driver.execute_query(
            "MATCH (a)-[r]->(b) RETURN DISTINCT labels(a)[0] AS s, type(r) AS t, labels(b)[0] AS e "
            "ORDER BY s, t, e", database_=database, routing_="r")
        obl, _, _ = driver.execute_query(
            "MATCH (c:Clause) RETURN collect(DISTINCT c.obligation) AS v", database_=database, routing_="r")
        lines = ["Node labels and properties:"]
        lines += [f"  {r['label']} {{{', '.join(sorted(r['keys']))}}}" for r in props]
        lines.append(f"  // Clause.obligation values: {sorted(obl[0]['v'])}")
        lines.append("Relationship patterns (direction matters):")
        lines += [f"  (:{r['s']})-[:{r['t']}]->(:{r['e']})" for r in pats]
        return canonical_schema("\n".join(lines))
    except Exception as e:  # graph not reachable yet -> use the documented schema
        log.warning("schema introspection failed (%s); using fallback", e)
        return SCHEMA_FALLBACK


SYSTEM_PROMPT = """You translate questions about ISO 10993-style regulatory clauses into ONE read-only Cypher query
for Neo4j 5. Your query is a RECALL FILTER: a later stage ranks results semantically, so returning extra clauses
is cheap and missing a relevant clause is expensive.

SCHEMA (use only these labels, relationship types, properties and directions):
{schema}
The value 'other' on RegulatoryActor / LifecyclePhase / BiologicalEffect is a catch-all; filtering on it is not selective.

HARD RULES
1. Return exactly one column: `RETURN DISTINCT c.id AS clause_id` where c is a :Clause.
2. NEVER use LIMIT, SKIP, ORDER BY or TOP-N logic. Every matching clause must be returned.
3. Read-only: never use CREATE, MERGE, SET, DELETE, REMOVE, DROP, LOAD CSV, FOREACH or CALL of procedures,
   except CALL db.index.fulltext.queryNodes('{ft}', $ft_query).
4. Put every user-derived literal in params and reference it as $name. No string literals from the question
   inside the query text.
5. String matching on names/text must be case-insensitive: toLower(x) CONTAINS toLower($p).
6. Prefer OR across synonyms and across several entity labels over a single narrow condition.
   ISO wording differs from user wording (e.g. "implant" ~ "implantation", "EO" ~ "ethylene oxide",
   "mandatory" ~ obligation 'SHALL').
7. If the question names no entity or obligation, fall back to the full-text index on clause text.

EXAMPLES
{examples}"""

FEW_SHOTS = [
    {"q": "mandatory requirements for genotoxicity assessment",
     "cypher": "MATCH (c:Clause) WHERE c.obligation = $ob AND (toLower(c.text) CONTAINS $t1 OR toLower(c.title) CONTAINS $t1) RETURN DISTINCT c.id AS clause_id",
     "params": [{"name": "ob", "value": "SHALL"}, {"name": "t1", "value": "genotoxicity"}]},
    {"q": "nickel release from nitinol devices",
     "cypher": "MATCH (c:Clause) WHERE EXISTS { (c)-[:MENTIONS]->(ch:Chemical) WHERE toLower(ch.name) CONTAINS $a OR toLower(ch.name) CONTAINS $b OR toLower(ch.name) CONTAINS $c2 } OR toLower(c.text) CONTAINS $d RETURN DISTINCT c.id AS clause_id",
     "params": [{"name": "a", "value": "nickel"}, {"name": "b", "value": "nickel titanium alloy"}, {"name": "c2", "value": "nitinol"}, {"name": "d", "value": "release"}]},
    {"q": "hemocompatibility testing for blood-contacting catheters",
     "cypher": "MATCH (c:Clause) WHERE EXISTS { (c)-[:MENTIONS]->(d:DeviceType) WHERE toLower(d.name) CONTAINS $a OR toLower(d.name) CONTAINS $b } OR EXISTS { (c)-[:MENTIONS]->(e:BiologicalEffect) WHERE toLower(e.name) CONTAINS $c2 } RETURN DISTINCT c.id AS clause_id",
     "params": [{"name": "a", "value": "catheter"}, {"name": "b", "value": "blood"}, {"name": "c2", "value": "hemocompatibility"}]},
    {"q": "which clauses of the cytotoxicity standard are recommendations only",
     "cypher": "MATCH (c:Clause)-[:BELONGS_TO]->(s:Standard) WHERE c.obligation = $ob AND (toLower(s.title) CONTAINS $t OR s.number CONTAINS $n) RETURN DISTINCT c.id AS clause_id",
     "params": [{"name": "ob", "value": "SHOULD"}, {"name": "t", "value": "cytotoxicity"}, {"name": "n", "value": "10993-5"}]},
    {"q": "clauses that point to the chemical characterization standard",
     "cypher": "MATCH (c:Clause)-[:REFERENCES]->(s:Standard) WHERE s.number CONTAINS $n OR toLower(s.title) CONTAINS $t RETURN DISTINCT c.id AS clause_id",
     "params": [{"name": "n", "value": "10993-18"}, {"name": "t", "value": "chemical characterization"}]},
]

def render_fewshots():
    return '\n'.join(
        f"Q: {e['q']}\nCYPHER: {e['cypher']}\nPARAMS: {json.dumps(e['params'], ensure_ascii=False, sort_keys=True)}\n"
        for e in FEW_SHOTS)


def render_prompt(schema):
    return SYSTEM_PROMPT.format(schema=canonical_schema(schema), examples=render_fewshots(), ft=FULLTEXT_INDEX)


def prompt_hashes(schema):
    return dict(prompt_template_sha=sha(SYSTEM_PROMPT), fewshot_sha=sha(render_fewshots()),
                rendered_prompt_sha=sha(render_prompt(schema)), schema_sha=sha(canonical_schema(schema)))

WRITE_PATTERN = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|FOREACH|LOAD\s+CSV|IN\s+TRANSACTIONS|"
    r"ALTER|GRANT|REVOKE|DENY|START\s+DATABASE|STOP\s+DATABASE)\b", re.I)
CALL_PATTERN = re.compile(r"\bCALL\s+([A-Za-z_][\w.]*)", re.I)
ALLOWED_PROCS = {"db.index.fulltext.querynodes"}
LIMIT_PATTERN = re.compile(r"\s+(SKIP|LIMIT)\s+(\d+|\$\w+)", re.I)
LABEL_PATTERN = re.compile(r"\(\s*\w*\s*:\s*`?([A-Za-z_]\w*)")
RELTYPE_PATTERN = re.compile(r"\[\s*\w*\s*:\s*`?([A-Za-z_]\w*)")


class CypherRejected(ValueError):
    pass


class CypherGenerationError(RuntimeError):
    """Terminal stop guard: a refusal or exhausted truncation is an error."""


def has_obligation_filter(query):
    return bool(re.search(r'\.\s*`?obligation`?\b', _strip_comments_and_strings(query), re.I))


def _strip_comments_and_strings(q: str) -> str:
    q = re.sub(r"//[^\n]*", " ", q)
    q = re.sub(r"/\*.*?\*/", " ", q, flags=re.S)
    return re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", "''", q)  # keywords inside literals don't count


def validate_cypher(q: str, allowed_labels: set[str], allowed_rels: set[str]) -> tuple[str, list[str]]:
    """Static checks. Returns (possibly rewritten query, warnings). Raises CypherRejected."""
    warnings: list[str] = []
    q = q.strip().rstrip(";").strip()
    if q.startswith("```"):
        q = re.sub(r"^```\w*|```$", "", q).strip()
    bare = _strip_comments_and_strings(q)
    if ";" in bare:
        raise CypherRejected("multiple statements")
    if m := WRITE_PATTERN.search(bare):
        raise CypherRejected(f"write/admin keyword: {m.group(1)}")
    for proc in CALL_PATTERN.findall(bare):
        if proc.lower() not in ALLOWED_PROCS:
            raise CypherRejected(f"procedure not allowed: {proc}")
    if not re.search(r"\bRETURN\b[^\n]*\bclause_id\b", bare, re.I):
        raise CypherRejected("must RETURN ... AS clause_id")
    if bad := {l for l in LABEL_PATTERN.findall(bare)} - allowed_labels:
        raise CypherRejected(f"unknown label(s): {sorted(bad)}")
    if bad := {r for r in RELTYPE_PATTERN.findall(bare)} - allowed_rels:
        raise CypherRejected(f"unknown relationship type(s): {sorted(bad)}")
    # THE bug: strip LIMIT/SKIP that follow the final RETURN; reject ones buried earlier (rewrite is unsafe there).
    last_ret = bare.upper().rfind("RETURN")
    tail = bare[last_ret:]
    if LIMIT_PATTERN.search(bare[:last_ret]):
        raise CypherRejected("LIMIT/SKIP inside the query body")
    if LIMIT_PATTERN.search(tail):
        warnings.append("stripped LIMIT/SKIP added by the model")
        q = q[: q.upper().rfind("RETURN")] + LIMIT_PATTERN.sub("", q[q.upper().rfind("RETURN"):])
        q = re.sub(r"\s+ORDER\s+BY\s+[^\n]*$", "", q, flags=re.I)
    return q, warnings


# --------------------------------------------------------------------------------------
# Result container
# --------------------------------------------------------------------------------------
@dataclass
class Hit:
    clause_id: str
    score: float
    via: str = "ranked"          # ranked | neighbour
    number: str = ""
    title: str = ""
    standard: str = ""
    obligation: str = ""
    text: str = ""


@dataclass
class RunLog:
    query: str
    cypher: str = ""
    params: dict = field(default_factory=dict)
    attempts: int = 0
    warnings: list = field(default_factory=list)
    filter_path: str = "text2cypher"   # text2cypher | fulltext_fallback | all_clauses
    candidates: int = 0
    max_score: float | None = None
    neighbours_added: int = 0
    ms: dict = field(default_factory=dict)
    stage1_candidate_ids: list = field(default_factory=list)
    candidate_count: int = 0
    fallback_path: str = 'text2cypher'
    cache_key: dict = field(default_factory=dict)
    obligation_filter: bool = False
    stage2_ranked: list = field(default_factory=list)
    stage3_added: list = field(default_factory=list)
    stage3_skipped_no_vector: int = 0
    final_ids: list = field(default_factory=list)
    query_embedding_key: str = ''
    embedding_provider: str = ''
    embedding_model: str = ''
    generations: list = field(default_factory=list)
    qid: str | None = None
    variant: str = 'a'
    gen_index: int = 0
    validator_outcome: str = ''
    embedding_identity: dict = field(default_factory=dict)
    query_embedding: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


# --------------------------------------------------------------------------------------
# Retriever
# --------------------------------------------------------------------------------------
class HybridRetriever:
    def __init__(self, database: str = "neo4j", cypher_timeout_s: float = 5.0, max_retries: int = 2,
                 *, cache_file=None, run_family=None, fresh=False,
                 manifest_path=None, confirmation_path=None, driver=None, pg=None, llm=None, embedder=None):
        self.db = database
        self.timeout = cypher_timeout_s
        self.max_retries = max_retries
        if driver is None:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(os.environ['NEO4J_URI'],
                         auth=(os.environ['NEO4J_USER'], os.environ['NEO4J_PASSWORD']))
        self.driver = driver
        if pg is None:
            import psycopg
            from pgvector.psycopg import register_vector
            pg = psycopg.connect(os.environ['PG_DSN'], autocommit=True)
            self.pg = pg
            register_vector(self.pg)
        self.pg = pg
        self.llm = llm if llm is not None else make_client('s8_cypher')
        self.fresh = fresh
        self.cypher_cache = JsonlCache(cache_file or cache_path(self.llm.provider, run_family))
        self.manifest_path = Path(manifest_path) if manifest_path else DEFAULT_IDENTITY
        self.query_embeddings = QueryEmbeddings(
            self.cypher_cache.path.with_name('query_embedding_cache.jsonl'), embedder,
            identity_path=self.manifest_path, confirmation_path=confirmation_path)
        self.embedding_identity = check_identity(self.query_embeddings.embedder, self.manifest_path, self.pg, PG_TABLE)
        self.schema = introspect_schema(self.driver, database)
        self.labels = set(re.findall(r"^\s{2}(\w+) \{", self.schema, re.M)) or {
            "Clause", "Standard", "Module", "LifecyclePhase", "RegulatoryAuthority",
            "Chemical", "BiologicalEffect", "DeviceType", "RegulatoryActor"}
        self.rels = set(re.findall(r"\[:(\w+)\]", self.schema)) or {"BELONGS_TO", "MENTIONS", "REGULATED_BY", "REFERENCES"}

    def close(self):
        self.driver.close()
        self.pg.close()

    # ---------- Stage 1 ----------
    def _generate_cypher(self, question: str, feedback: str | None, prev: str | None,
                         *, run=None, records=None) -> tuple[str, dict]:
        run = run if run is not None else RunLog(query=question, attempts=1)
        key = generation_key(question, qid=run.qid, variant=run.variant, gen_index=run.gen_index,
                             hashes=prompt_hashes(self.schema), provider=self.llm.provider,
                             requested_model=self.llm.model, effort=self.llm.effort,
                             attempt_no=run.attempts, feedback=feedback, prev=prev)
        run.cache_key = key
        cached = None if self.fresh else self.cypher_cache.find(key)
        if cached is not None:
            run.generations.append({**cached['provenance'], 'cache_hit': True, 'usage': {},
                                    'validator_outcome': cached['validator_outcome']})
            return cached['cypher'], dict(cached['params'])
        msgs = [{"role": "user", "content": f"Question: {question}"}]
        if feedback:
            msgs.append({"role": "assistant", "content": f"Previous attempt:\n{prev}"})
            msgs.append({"role": "user", "content":
                         f"That query failed: {feedback}\nFix it. Follow every HARD RULE. Question: {question}"})
        for budget in (800, 1600):
            row = dict(**key, key=key, cypher=None, params={}, reusable=False,
                       response_model=None, provenance=provenance(self.llm),
                       validator_outcome='generation_error', fallback_path='text2cypher', max_tokens=budget)
            try:
                result = self.llm.generate(cypher_request(render_prompt(self.schema), msgs, max_tokens=budget))
                row['provenance'] = result['provenance']
                row['response_model'] = result['provenance'].get('response_model')
                row['usage'] = result.get('usage', {})
                run.generations.append({**result['provenance'], 'cache_hit': False,
                                        'usage': row['usage']})
                stop = result['stop_reason']
                row['validator_outcome'] = stop
                if stop == 'max_tokens':
                    if budget == 800:
                        continue
                    raise CypherGenerationError('Text2Cypher truncated after one larger-budget retry')
                if stop != 'end_turn':
                    raise CypherGenerationError(f'Text2Cypher stop guard: {stop}')
                query, params = parse_output(result['data'])
                row.update(cypher=query, params=params, reusable=True, validator_outcome='structured_valid')
                return query, params
            finally:
                if records is None:
                    self.cypher_cache.append(row)
                else:
                    records.append(row)

    def _explain_is_read_only(self, q: str, params: dict):
        # EXPLAIN plans without executing; summary.query_type: 'r' | 'rw' | 'w' | 's'
        _, summary, _ = self.driver.execute_query("EXPLAIN " + q, params, database_=self.db, routing_="r")
        if summary.query_type != "r":
            raise CypherRejected(f"EXPLAIN says query_type={summary.query_type!r}, not read-only")

    def _run_ids(self, q: str, params: dict) -> list[str]:
        from neo4j import unit_of_work
        @unit_of_work(timeout=self.timeout)
        def work(tx):
            return [r["clause_id"] for r in tx.run(q, params) if r["clause_id"]]
        with self.driver.session(database=self.db) as s:
            return s.execute_read(work)

    def stage1_filter(self, question: str, run: RunLog, inject_limit: int | None) -> list[str]:
        records = []
        try:
            ids = self._stage1_filter(question, run, inject_limit, records)
            run.stage1_candidate_ids = list(ids)
            run.candidates = run.candidate_count = len(ids)
            return ids
        finally:
            run.fallback_path = run.filter_path
            run.obligation_filter = has_obligation_filter(run.cypher)
            for row in records:
                row['fallback_path'] = run.fallback_path
                self.cypher_cache.append(row)

    def _stage1_filter(self, question, run, inject_limit, records):
        feedback = prev = None
        for attempt in range(1, self.max_retries + 2):
            run.attempts = attempt
            try:
                start = len(records)
                q, params = self._generate_cypher(question, feedback, prev, run=run, records=records)
                prev = q
                q, warns = validate_cypher(q, self.labels, self.rels)
                if len(records) > start:
                    records[-1]['validator_outcome'] = 'accepted'
                run.warnings += warns
                if inject_limit:  # reproduce the original internship bug on purpose
                    q = f"{q} LIMIT {int(inject_limit)}"
                    run.warnings.append(f"BUG MODE: injected LIMIT {inject_limit}")
                self._explain_is_read_only(q, params)
                run.validator_outcome = 'accepted (static validation + read-only EXPLAIN)'
                ids = self._run_ids(q, params)
                run.cypher, run.params = q, params
                if ids:
                    return list(dict.fromkeys(ids))
                feedback = "it ran but returned 0 rows; broaden it (synonyms, OR across labels, full-text fallback)"
            except CypherGenerationError:
                raise
            except Exception as e:  # syntax error, rejected, timeout -> feed error back
                if len(records) > start:
                    records[-1]['validator_outcome'] = type(e).__name__
                feedback = f"{type(e).__name__}: {e}"
                run.warnings.append(f"attempt {attempt}: {feedback[:200]}")
        # Fallback 1: Lucene full-text over clause title+text (case-insensitive by default analyzer)
        run.filter_path = "fulltext_fallback"
        lucene = " OR ".join(re.findall(r"[A-Za-z0-9-]{3,}", question)) or question
        run.cypher = (f"CALL db.index.fulltext.queryNodes('{FULLTEXT_INDEX}', $q) YIELD node, score "
                      "RETURN node.id AS clause_id")
        run.params = {'q': lucene}
        try:
            recs, _, _ = self.driver.execute_query(run.cypher, run.params, database_=self.db, routing_='r')
            ids = [r['clause_id'] for r in recs]
        except Exception as exc:
            message = f'full-text fallback failed ({type(exc).__name__}); continuing to all clauses'
            log.warning(message)
            run.warnings.append(message)
            ids = []
        if ids:
            return ids
        # Fallback 2: no filter at all — stage 2 ranks all ~1.15k vectors exactly (still cheap)
        run.filter_path = "all_clauses"
        run.cypher, run.params = 'MATCH (c:Clause) RETURN c.id AS clause_id', {}
        recs, _, _ = self.driver.execute_query(run.cypher,
                                               database_=self.db, routing_="r")
        return [r["clause_id"] for r in recs]

    # ---------- Stage 2 ----------
    def embed(self, text: str):
        from pgvector import Vector
        vector, self.query_embedding_key = self.query_embeddings.get(text)
        return Vector(vector)

    def stage2_rank(self, qvec, ids: list[str], top_k: int) -> list[Hit]:
        # Exact scan over the candidate set: no ANN index, so the ANY() filter can never silently drop rows.
        rows = self.pg.execute(
            f"SELECT clause_id, 1 - (embedding <=> %s) AS score FROM {PG_TABLE} "
            f"WHERE clause_id = ANY(%s) AND embedding IS NOT NULL ORDER BY embedding <=> %s, clause_id LIMIT %s",
            (qvec, ids, qvec, top_k)).fetchall()
        return [Hit(clause_id=r[0], score=float(r[1])) for r in rows]

    def score_ids(self, qvec, ids: list[str]) -> dict[str, float]:
        if not ids:
            return {}
        rows = self.pg.execute(
            f"SELECT clause_id, 1 - (embedding <=> %s) FROM {PG_TABLE} WHERE clause_id = ANY(%s) AND embedding IS NOT NULL",
            (qvec, ids)).fetchall()
        return {r[0]: float(r[1]) for r in rows}

    # ---------- Stage 3 ----------
    def stage3_expand(self, qvec, hits: list[Hit], per_seed: int, total_cap: int, rel_floor: float) -> list[Hit]:
        """Structural neighbours inside the SAME standard: parent (4.1 for 4.1.2), children (4.1.2.x),
        and siblings (4.1.x). Clause hierarchy is derived from the clause number, so no extra relationship
        type is needed. Neighbours are re-scored with real cosine and kept only if
        score >= rel_floor * seed_score — this is what stops flooding."""
        if not hits:
            self.stage3_skipped_no_vector = 0
            return []
        seeds = [h.clause_id for h in hits]
        recs, _, _ = self.driver.execute_query(
            """
            UNWIND $seeds AS sid
            MATCH (s:Clause {id: sid})-[:BELONGS_TO]->(std:Standard)<-[:BELONGS_TO]-(n:Clause)
            WHERE n.id <> s.id AND n.number IS NOT NULL AND s.number IS NOT NULL
            WITH sid, n, s,
                 CASE WHEN s.number CONTAINS '.' THEN left(s.number, size(s.number) - size(last(split(s.number, '.'))) - 1)
                      ELSE null END AS parent_no
            WITH sid, n,
                 CASE
                   WHEN n.number = parent_no                                        THEN 'parent'
                   WHEN n.number STARTS WITH s.number + '.'
                        AND size(split(n.number, '.')) = size(split(s.number, '.')) + 1 THEN 'child'
                   WHEN parent_no IS NOT NULL AND n.number STARTS WITH parent_no + '.'
                        AND size(split(n.number, '.')) = size(split(s.number, '.')) THEN 'sibling'
                 END AS rel
            WHERE rel IS NOT NULL
            RETURN sid, n.id AS nid, rel
            """, {"seeds": seeds}, database_=self.db, routing_="r")
        by_seed: dict[str, list[tuple[str, str]]] = {}
        for r in recs:
            by_seed.setdefault(r["sid"], []).append((r["nid"], r["rel"]))
        scores = self.score_ids(qvec, list({nid for v in by_seed.values() for nid, _ in v}))
        self.stage3_skipped_no_vector = len({nid for v in by_seed.values() for nid, _ in v
                                            if nid not in scores and nid not in seeds})
        have = set(seeds)
        added: list[Hit] = []
        for h in hits:  # best seeds first
            cands = sorted(((scores[nid], nid, rel) for nid, rel in by_seed.get(h.clause_id, [])
                            if nid not in have and nid in scores), reverse=True)
            for sc, nid, rel in cands[:per_seed]:
                if sc >= rel_floor * h.score and len(added) < total_cap:
                    added.append(Hit(clause_id=nid, score=sc, via=f"neighbour:{rel}:{h.clause_id}"))
                    have.add(nid)
        return added

    # ---------- Stage 4 ----------
    def stage4_fetch(self, hits: list[Hit]) -> list[Hit]:
        recs, _, _ = self.driver.execute_query(
            "UNWIND $ids AS cid MATCH (c:Clause {id: cid}) OPTIONAL MATCH (c)-[:BELONGS_TO]->(s:Standard) "
            "RETURN c.id AS id, c.number AS number, c.title AS title, c.text AS text, "
            "c.obligation AS obligation, s.number AS standard",
            {"ids": [h.clause_id for h in hits]}, database_=self.db, routing_="r")
        by_id = {r["id"]: r for r in recs}
        for h in hits:
            if r := by_id.get(h.clause_id):
                h.number, h.title, h.text = r["number"] or "", r["title"] or "", r["text"] or ""
                h.obligation, h.standard = r["obligation"] or "", r["standard"] or ""
        return hits

    # ---------- orchestration ----------
    @traced('retriever', as_type='retriever')
    def retrieve(self, question: str, top_k: int = 10, inject_limit: int | None = None,
                 per_seed: int = 2, neighbour_cap: int = 5, rel_floor: float = 0.85,
                 *, qid=None, variant='a', gen_index=0) -> tuple[list[Hit], RunLog]:
        # Fail closed before any generation or ranking, even for library callers.
        self.embedding_identity = check_identity(self.query_embeddings.embedder, self.manifest_path, self.pg, PG_TABLE)
        run = RunLog(query=question, qid=qid, variant=variant, gen_index=gen_index,
                     embedding_provider=self.embedding_identity['provider'],
                     embedding_model=self.embedding_identity['model'],
                     embedding_identity=dict(self.embedding_identity))
        t = time.perf_counter()
        with stage('neo4j.cypher') as observation:
            ids = self.stage1_filter(question, run, inject_limit)
            if observation is not None:
                observation.update(metadata={'rows': len(ids), 'candidate_count': len(ids)})
        run.candidates = len(ids)
        run.ms["stage1"] = round((time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        with stage('embed.query', metadata={'provider': run.embedding_provider, 'model': run.embedding_model}):
            qvec = self.embed(question)
        run.query_embedding_key = self.query_embedding_key
        run.query_embedding = dict(self.query_embeddings.last)
        with stage('pgvector.rank') as observation:
            ranked = self.stage2_rank(qvec, ids, top_k)
            if observation is not None:
                observation.update(metadata={'rows': len(ranked), 'candidate_count': len(ids),
                                             'max_score': ranked[0].score if ranked else None})
        run.max_score = round(ranked[0].score, 4) if ranked else None
        run.stage2_ranked = [(h.clause_id, h.score) for h in ranked]
        run.ms["stage2"] = round((time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        with stage('neighbour.expand') as observation:
            neigh = self.stage3_expand(qvec, ranked, per_seed, neighbour_cap, rel_floor)
            if observation is not None:
                observation.update(metadata={'expanded': len(neigh)})
        run.neighbours_added = len(neigh)
        run.stage3_added = [(h.clause_id, h.score, h.via) for h in neigh]
        run.stage3_skipped_no_vector = self.stage3_skipped_no_vector
        run.ms["stage3"] = round((time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        with stage('text.fetch') as observation:
            hits = self.stage4_fetch(ranked + neigh)
            if observation is not None:
                observation.update(metadata={'rows': len(hits), 'docs': sorted({h.standard for h in hits})})
        run.ms["stage4"] = round((time.perf_counter() - t) * 1000)
        run.final_ids = [h.clause_id for h in hits]

        log.info("query=%r path=%s candidates=%d max_score=%s neighbours=%d attempts=%d ms=%s warnings=%s",
                 question, run.filter_path, run.candidates, run.max_score, run.neighbours_added,
                 run.attempts, run.ms, run.warnings)
        return hits, run


# --------------------------------------------------------------------------------------
# Evaluation helper for the filter stage
# --------------------------------------------------------------------------------------
def candidate_recall(gold_ids: set[str], candidate_ids: list[str]) -> dict:
    """Filter-stage metric: did Stage 1 keep the clauses a human marked relevant?
    Also report the reduction ratio so recall can't be 'won' by returning everything."""
    cand = set(candidate_ids)
    hit = gold_ids & cand
    return {"gold": len(gold_ids), "kept": len(hit),
            "candidate_recall": round(len(hit) / len(gold_ids), 3) if gold_ids else None,
            "candidates": len(cand), "missed": sorted(gold_ids - cand)}


def _print(hits: list[Hit], run: RunLog):
    print(f"\nQUERY: {run.query}")
    print(f"filter={run.filter_path} attempts={run.attempts} candidates={run.candidates} "
          f"max_score={run.max_score} neighbours={run.neighbours_added} ms={run.ms}")
    if run.warnings:
        print("warnings:", *run.warnings, sep="\n  - ")
    print(f"cypher: {run.cypher}\nparams: {run.params}")
    for h in hits:
        print(f"  {h.score:.4f}  {h.standard} §{h.number:<10} [{h.obligation or '-':6}] {h.via:<28} {h.title[:60]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--inject-limit", type=int, default=None, help="append LIMIT N to reproduce the bug")
    ap.add_argument("--compare", action="store_true", help="run with LIMIT 10 injected, then fixed")
    ap.add_argument('--fresh', action='store_true', help='append new Cypher generations instead of replaying')
    ap.add_argument('--cache-path', type=Path, help='path to append-only cypher_cache.jsonl')
    ap.add_argument('--run-family', help='cache family under data/runs/<provider>/s8/')
    ap.add_argument('--identity', '--manifest', dest='manifest', type=Path,
                    help='S7 identity addendum (default: data/embed_identity.json)')
    ap.add_argument('--qid')
    ap.add_argument('--variant', choices=['a', 'b', 'c'], default='a')
    ap.add_argument('--generations', type=int, default=1, help='generate indices 0..N-1; metrics always use index 0')
    a = ap.parse_args()
    if a.generations < 1:
        ap.error('--generations must be positive')
    r = HybridRetriever(cache_file=a.cache_path, run_family=a.run_family, fresh=a.fresh, manifest_path=a.manifest)
    try:
        if a.compare:
            for lim in (10, None):
                _print(*r.retrieve(a.query, a.top_k, inject_limit=lim))
        else:
            for gen_index in range(a.generations):
                _print(*r.retrieve(a.query, a.top_k, inject_limit=a.inject_limit,
                                  qid=a.qid, variant=a.variant, gen_index=gen_index))
    finally:
        r.close()
