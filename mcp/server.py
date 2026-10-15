"""RegClaw regulatory-knowledge MCP server (FastMCP 4.x, stdio transport).

Tools
  - query_regulatory_knowledge(query, top_k)   -> 4-stage hybrid retrieval
  - get_applicable_standards(device_profile)    -> mandatory / recommended / not applicable
Resources (read-only)
  - regclaw://modules, regclaw://modules/{module_id}
  - regclaw://actors
  - regclaw://lifecycle-phases

Run:  python server.py            (stdio; this is what Claude Desktop launches)
      fastmcp dev inspector server.py   (opens MCP Inspector)

RULE: stdout carries JSON-RPC frames. Never print(); log to stderr only.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

from dotenv import load_dotenv
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.server.lifespan import lifespan
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

# ---------------------------------------------------------------- setup
# Load .env by absolute path, NOT from cwd: Claude Desktop launches the
# process with an unpredictable working directory. mcp/.env wins, then rebuild/.env.
HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))
from standards.profile import DeviceProfile
from standards.tool import get_applicable_standards as classify_profile

load_dotenv(HERE / ".env")
load_dotenv(HERE.parent / ".env")

logging.basicConfig(
    stream=sys.stderr,  # stdout is reserved for the protocol
    level=os.getenv("REGCLAW_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("regclaw.mcp")

# REGCLAW_STUB=1 (default) -> canned data, no Neo4j / Postgres / API keys needed.
STUB = os.getenv("REGCLAW_STUB", "1") == "1"


# ---------------------------------------------------------------- schemas
# NONE = no modal / "can" / informative text (R3 design decision; stored as obligation="NONE").
Obligation = Literal["SHALL", "SHOULD", "MAY", "NONE"]


class Clause(BaseModel):
    clause_id: str
    standard: str = Field(description="e.g. 'ISO 10993-7'")
    number: str = Field(description="Clause number, e.g. '4.3.2' or 'Annex A.3'")
    title: str
    text: str
    obligation: Obligation | None = None
    score: float | None = Field(None, description="Cosine similarity to the query (neighbours are re-scored too)")
    source: Literal["ranked", "neighbour"] = "ranked"


class RetrievalResult(BaseModel):
    query: str
    candidate_count: int = Field(description="Clauses surviving stage-1 Cypher filter")
    max_score: float | None
    clauses: list[Clause]


# ---------------------------------------------------------------- backend
@dataclass
class Backend:
    """Holds long-lived connections.
    Stub mode: canned data. Real mode: one HybridRetriever (retrieval/hybrid_retriever.py, R4),
    which owns the Neo4j driver, the psycopg connection and the Anthropic/OpenAI clients."""

    neo4j: Any = None
    pg: Any = None
    retriever: Any = None
    standards_retriever: Any = None
    standards_llm: Any = None
    # HybridRetriever shares ONE psycopg connection; FastMCP runs sync tools in worker
    # threads, so serialise access. Fine for stdio (one client); use a pool if you go HTTP.
    lock: threading.Lock = field(default_factory=threading.Lock)

    # ---- real mode: full 4-stage pipeline in one call ----
    def retrieve(self, query: str, top_k: int) -> RetrievalResult:
        with self.lock:
            hits, run = self.retriever.retrieve(query, top_k=top_k)
        if not hits:
            raise ToolError("No clauses matched. Try broader wording or specific entities.")
        clauses = [
            Clause(clause_id=h.clause_id, standard=h.standard, number=h.number, title=h.title,
                   text=h.text, obligation=h.obligation or None, score=round(h.score, 4),
                   source="ranked" if h.via == "ranked" else "neighbour")
            for h in hits
        ]
        log.info("query=%r path=%s candidates=%d max_score=%s warnings=%s",
                 query, run.filter_path, run.candidates, run.max_score, run.warnings)
        return RetrievalResult(query=query, candidate_count=run.candidates,
                               max_score=run.max_score, clauses=clauses)

    def _read(self, cypher: str) -> list[dict]:
        recs, _, _ = self.retriever.driver.execute_query(cypher, routing_="r")
        return [dict(r) for r in recs]

    # ---- stage functions: replace stubs with real implementations ----
    def stage1_cypher_filter(self, query: str) -> list[str]:
        if STUB:
            return [f"iso10993-1:{n}" for n in ("4.1", "4.2", "5.1")] + ["iso10993-7:4.3"]
        raise NotImplementedError("Claude Haiku -> Cypher -> clause ids (strip LIMIT!)")

    def stage2_rank(self, query: str, candidate_ids: list[str], top_k: int) -> list[tuple[str, float]]:
        if STUB:
            return [(cid, round(0.65 - i * 0.04, 4)) for i, cid in enumerate(candidate_ids)][:top_k]
        raise NotImplementedError("pgvector: WHERE clause_id = ANY(%s) ORDER BY embedding <=> %s")

    def stage3_neighbours(self, clause_ids: list[str]) -> list[str]:
        if STUB:
            return ["iso10993-1:4.1.1"] if clause_ids else []
        raise NotImplementedError("Neo4j: parent/sibling via BELONGS_TO, cross-refs via REFERENCES")

    def stage4_fetch(self, clause_ids: list[str]) -> dict[str, dict]:
        if STUB:
            return {
                cid: {
                    "standard": "ISO 10993-" + cid.split(":")[0].removeprefix("iso10993-"),
                    "number": cid.split(":")[1],
                    "title": f"Stub clause {cid}",
                    "text": "The biological evaluation shall be planned within a risk management process.",
                    "obligation": "SHALL",
                }
                for cid in clause_ids
            }
        raise NotImplementedError("Neo4j: MATCH (c:Clause) WHERE c.id IN $ids RETURN c")

    def classify_standards(self, profile: DeviceProfile) -> dict:
        """DeviceProfile.v1 -> rules/classifier/guard -> assembled response.

        Retrieval and classifier can be injected independently of DB startup.
        An unavailable retriever is reported by the tool as unsupported citations.
        """
        with self.lock:
            return classify_profile(profile, self.standards_retriever or self.retriever,
                                    llm=self.standards_llm)

    # ---- resource data ----
    def modules(self) -> list[dict]:
        if STUB:
            return [{"module_id": "biocompatibility", "name": "Biocompatibility (ISO 10993)",
                     "standards": 16, "clauses": 1149}]
        return self._read(
            "MATCH (m:Module) OPTIONAL MATCH (m)<-[:BELONGS_TO]-(s:Standard) "
            "OPTIONAL MATCH (s)<-[:BELONGS_TO]-(c:Clause) "
            "RETURN toLower(replace(m.name, ' ', '-')) AS module_id, m.name AS name, "
            "count(DISTINCT s) AS standards, count(DISTINCT c) AS clauses ORDER BY name")

    def actors(self) -> list[dict]:
        if STUB:
            return [{"name": "Manufacturer"}, {"name": "Test laboratory"}, {"name": "Notified body"}]
        return self._read("MATCH (a:RegulatoryActor) RETURN a.name AS name ORDER BY name")

    def lifecycle_phases(self) -> list[dict]:
        if STUB:
            return [{"name": p} for p in ("Design", "Material selection", "Sterilization",
                                          "Biological evaluation", "Post-market")]
        return self._read("MATCH (l:LifecyclePhase) RETURN l.name AS name ORDER BY name")

    def close(self) -> None:
        if self.retriever is not None:
            self.retriever.close()
        for conn in (self.neo4j, self.pg):
            if conn is not None:
                conn.close()


@lifespan
async def backend_lifespan(server: FastMCP):
    """Open DB connections once per server process, not once per tool call."""
    if STUB:
        backend = Backend()
    else:  # real connections; imported lazily so stub mode needs no drivers
        sys.path.insert(0, str(HERE.parent / "retrieval"))
        from hybrid_retriever import HybridRetriever  # R4 code: Neo4j + pgvector + Haiku + OpenAI

        backend = Backend(retriever=HybridRetriever())
        backend.retriever.driver.verify_connectivity()
    log.info("backend ready (stub=%s)", STUB)
    try:
        yield {"backend": backend}
    finally:
        backend.close()
        log.info("backend closed")


mcp = FastMCP(
    name="regclaw",
    instructions=(
        "Regulatory knowledge graph over ISO 10993 biocompatibility standards. "
        "Use query_regulatory_knowledge for clause lookup and get_applicable_standards "
        "for a device profile. Read regclaw://modules first to see what is covered."
    ),
    lifespan=backend_lifespan,
    mask_error_details=True,  # unexpected exceptions -> generic message; ToolError text still passes through
)


def _backend(ctx: Context) -> Backend:
    return ctx.lifespan_context["backend"]


# MCP SDK v2 uses snake_case fields (serialised as camelCase on the wire).
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


# ---------------------------------------------------------------- tools
# Tools are plain `def`: the backend calls (neo4j driver, psycopg, Anthropic SDK) are
# blocking, and FastMCP runs sync tools in a worker thread so they never stall the
# event loop. An `async def` tool calling blocking code would freeze the whole server.
@mcp.tool(annotations=READ_ONLY, timeout=60)
def query_regulatory_knowledge(
    query: Annotated[str, Field(min_length=3, description="Natural-language regulatory question")],
    ctx: Context,
    top_k: Annotated[int, Field(ge=1, le=50, description="Clauses to keep after semantic ranking")] = 10,
) -> RetrievalResult:
    """Retrieve ISO 10993 clauses via 4-stage hybrid retrieval: Cypher filter -> pgvector ranking -> neighbour expansion -> full-text fetch."""
    b = _backend(ctx)
    if not STUB:
        return b.retrieve(query, top_k)
    candidates = b.stage1_cypher_filter(query)
    log.info("stage1: %d candidates", len(candidates))  # ctx.info() = MCP logging, deprecated in protocol 2026-07-28
    if not candidates:
        raise ToolError("No clauses matched the graph filter. Try broader wording or specific entities.")

    ranked = b.stage2_rank(query, candidates, top_k)
    ranked_ids = [cid for cid, _ in ranked]
    neighbours = [n for n in b.stage3_neighbours(ranked_ids) if n not in ranked_ids]
    texts = b.stage4_fetch(ranked_ids + neighbours)

    clauses = [Clause(clause_id=cid, score=s, source="ranked", **texts[cid]) for cid, s in ranked]
    clauses += [Clause(clause_id=cid, source="neighbour", **texts[cid]) for cid in neighbours]
    max_score = max((s for _, s in ranked), default=None)
    log.info("query=%r candidates=%d max_score=%s", query, len(candidates), max_score)
    return RetrievalResult(query=query, candidate_count=len(candidates), max_score=max_score, clauses=clauses)


@mcp.tool(annotations=READ_ONLY, timeout=120)
def get_applicable_standards(device_profile: DeviceProfile, ctx: Context) -> dict:
    """Classify DeviceProfile.v1 using mandatory / recommended / not_applicable.

    mandatory = must be addressed in the biological evaluation, not necessarily tested.
    recommended = consider; address if the stated trigger applies.
    not_applicable = not indicated for this contact/duration and current triggers.
    """
    return _backend(ctx).classify_standards(device_profile)


# ---------------------------------------------------------------- resources
# Resources are read-only by definition in MCP (clients can only read them).
# Static resources: fixed URI. Templated: {param} in URI becomes a function arg.
@mcp.resource("regclaw://modules", mime_type="application/json")
def list_modules(ctx: Context) -> str:
    """Knowledge modules in the graph (standards and clause counts)."""
    return json.dumps(_backend(ctx).modules())


@mcp.resource("regclaw://modules/{module_id}", mime_type="application/json")
def get_module(module_id: str, ctx: Context) -> str:
    """One module by id."""
    for m in _backend(ctx).modules():
        if m["module_id"] == module_id:
            return json.dumps(m)
    raise ResourceError(f"Unknown module '{module_id}'")


@mcp.resource("regclaw://actors", mime_type="application/json")
def list_actors(ctx: Context) -> str:
    """Regulatory actors referenced by clauses (RegulatoryActor nodes)."""
    return json.dumps(_backend(ctx).actors())


@mcp.resource("regclaw://lifecycle-phases", mime_type="application/json")
def list_lifecycle_phases(ctx: Context) -> str:
    """Device lifecycle phases used to tag clauses (LifecyclePhase nodes)."""
    return json.dumps(_backend(ctx).lifecycle_phases())


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
