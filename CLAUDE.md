# CLAUDE.md — instructions for coding agents (Claude Code, Codex, Copilot)

**Project:** `regclaw-rebuild` — an independent rebuild, on public substitute documents, of two regulatory-retrieval systems I worked on during my internship:
- **Part I (RegClaw Literature Agent):** PDF ingestion → entities + obligation (LegalBERT) → Neo4j knowledge graph + pgvector → 4-stage hybrid retrieval → FastMCP stdio server.
- **Part II (PageIndex):** vectorless tree retrieval over the ICMR GTP 2019 guideline → Sonnet navigation agent + Haiku citation check → Ragas evaluation.
- **Observability:** Langfuse v4 tracing + run summaries.

Python 3.11 · Windows (PowerShell) laptop · repo root = `D:\sem_iitk\sem9\meril` · private GitHub repo `regclaw-rebuild`.

## Rules for every task
1. **Follow the data contracts below exactly.** Never rename labels, properties, relationship types, tables, columns, JSON fields or doc codes.
2. **Edit only the files the task lists.** Never delete files. If something outside scope looks wrong, report it; don't fix it.
3. **Never change the design silently.** If a library, API or doc forces a change, stop and report it as `DESIGN CONFLICT: <what> — options — recommendation`.
4. **Tests:** add/extend pytest tests for new code; run `python tasks.py test` before finishing. Tests must pass in stub mode with no API keys or databases.
5. **MCP server:** never `print()` in `mcp/`; log to stderr only (stdout carries JSON-RPC).
6. **Secrets:** read keys from `.env` (template `env.example`); never hard-code or print them.
7. **Git:** before any commit, show `git status` and the full list of files to be committed, then STOP and wait for the user's OK. Never add a remote and never push. Commit message: `<step>: <summary>`.
8. **Never commit:** `.env`, any `*.pdf` (incl. `report.pdf`, `data/pdfs/`), model weights (`*.safetensors`, `*.bin`, `obligation/models/`), `pageindex/store/`, caches in `data/*.json`, research notes.
9. **Finish every task with a step log in your final message** (do NOT write log files; the user saves it in the claude.ai Project):

```markdown
# <step-id> — <title>
**Date:** YYYY-MM-DD · **Status:** done | blocked
## Did
## Found
## Decided (and why)
## Remaining
## Next
```
Also list every file created or changed, and the test results.

## Commands
- `python tasks.py up` — start Neo4j + Postgres (Docker)
- `python tasks.py check` — check services, keys, libraries
- `python tasks.py test` — all tests in stub mode (`REGCLAW_STUB=1`)

## Data contracts (follow exactly)

**Document codes:** `fda-iso10993-1`, `fda-sterility-510k`, `fda-chem-analysis`, `fda-nitinol`, `fda-knee`, `eu-mdr` (Annex I + Arts 1–10 + Annexes II, III, XIV, XV sliced from one OJ PDF), `imdrf-n47`, `in-ep`, `in-mdr`, `epa-63o`, optional `who-decon`.

**`data/clauses.json`** (Phase 1 output):
```json
[{"id": "fda-iso10993-1:III.A", "doc": "fda-iso10993-1", "number": "III.A", "title": "Risk Assessment of the Medical Device",
  "text": "...", "parent": "fda-iso10993-1:III", "source_pdf": "fda_iso10993-1_2023.pdf", "page": 12, "informative": false}]
```
- `id` = `<doc code>:<number>`; unique, stable across re-runs, the MERGE key.
- `number` keeps the document's own style (`III.A.1.a`, `10.4.1`, `22(1)(a)`, `Annex I.10.4`, `§63.362(a)`). R4 neighbour expansion splits on `.`.
- `informative: true` for informative annexes and NOTE/EXAMPLE-only blocks.

**`data/enriched.json`** (Phase 2 output):
```json
[{"...all clause fields...": "...",
  "obligation": "SHALL|SHOULD|MAY|NONE", "obligation_flags": ["prohibition"],
  "entities": {"biological_effects": [], "chemicals": [], "device_types": [],
               "regulatory_actors": [], "lifecycle_phases": [], "keywords": []},
  "iso_refs": ["ISO 10993-18"]}]
```

**Neo4j** (read by `hybrid_retriever.py` and `server.py`; do not rename):
- `(:Standard {id, number, title, edition?})`: for ingested docs, `id` = doc code; for referenced ISO standards, `id` = `number` = "ISO 10993-18" (no clauses).
- `(:Clause {id, number, title, text, obligation})`, `(:Module {name})` ("Biocompatibility"), `(:LifecyclePhase {name})`, `(:RegulatoryAuthority {name})`, `(:Chemical|BiologicalEffect|DeviceType|RegulatoryActor {name})`.
- `(Clause)-[:BELONGS_TO]->(Standard)`, `(Standard)-[:BELONGS_TO]->(Module)`, `(Clause)-[:MENTIONS]->(entity)`, `(Standard)-[:REGULATED_BY]->(RegulatoryAuthority)`, `(Clause)-[:REFERENCES]->(Standard)`.
- Uniqueness: `Clause.id`, `Standard.id`, `name` per entity label. `obligation` is one of the 4 strings, never null. Full-text index `clause_text_ft` (created by the retriever).

**Postgres:** `clause_embeddings(clause_id text primary key, embedding vector(1536))`. **No ANN index.**

**PageIndex:** 1-based physical PDF pages everywhere. `golden.json` = `[{"id","question","reference","gold_pages"}]`.

**DeviceProfile:** `DeviceProfile.v1` from `mcp/R9_iso10993_applicability_rules.md` §A (per component; `framework` defaults to `FDA_2023_ATTACHMENT_A`).

## Folder map
`corpus/` download + counts · `ingest/` Phase 1 parser · `ner/` entities + merge · `obligation/` LegalBERT (weak labels, train, infer, evaluate) · `graph/` Neo4j build · `embed/` pgvector · `retrieval/` 4-stage retriever · `mcp/` FastMCP server · `pageindex/` Part II ingest + agent · `eval/` page metrics + Ragas · `observability/` Langfuse · `data/` local data (gitignored) · `results/` tables for the README.
