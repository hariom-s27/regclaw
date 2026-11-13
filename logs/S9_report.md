# S9 — MCP server live over stdio + one Langfuse trace (provisional (GPT))

**Date:** 2026-11-13 · **Branches:** `s9/mcp-live` (code fix, PR #15, merge `72b656c`), `s9/report` (this report) · **Status:** done

## PLAIN SUMMARY
1. The regclaw MCP server ran in real mode (`REGCLAW_STUB=0`) over stdio against the live Neo4j graph and pgvector store; standard MCP clients called both tools, all three resources and the resource template.
2. Every S9-17 shape check passed for `query_regulatory_knowledge` (Q1) and `get_applicable_standards` (Knee X); the bad-input call (`top_k=500`) was rejected before any model call.
3. With tracing on, Langfuse Cloud received the expected 3 traces (Q2 retriever, Knee X retriever, `standards.classify`); all 33 trace-shape checks passed and a run summary was produced (n is tiny: P95 = max).
4. Paid calls: 9 counted, 10 conservative (the cap), about $0.108 counted and at most $0.162 conservative — all provisional (GPT).
5. Four environment/tool blockers and one sync-tool timeout gap were found and are documented below; one code fix (optional `REGCLAW_RUN_DIR` + tracing flush per tool call) was merged before any live call.

## What ran
| Item | Value | Source |
|---|---|---|
| Server | `mcp/server.py`, FastMCP 4.0.11, stdio, real mode; `.env` loaded by absolute path; logs to stderr | code, server stderr `backend ready (stub=False)` |
| Clients | MCP Inspector CLI 2.13.0 (pinned; newest release at least 2 weeks old) for list/read calls, the bad-input call, P1 and C1; a small fastmcp 4.0.11 Python stdio client (`Client` + `StdioTransport`, `call_tool(timeout=…)`) for P2, P2 r2 and C2 | `calls_ledger.jsonl` |
| Why two clients | Inspector 2.13.0 does not pass the per-server `requestTimeout` to `tools/call`, so tool calls stop at the SDK default of 60 s; evidence below | Inspector `clients/cli/build/index.js` |
| Versions | fastmcp 4.0.11 · mcp 2.3.0 · langfuse 5.0.1 · openai 2.54.0 (all match the pins) · Node 24.18.0 | `pip show`, `node --version` |
| Services | `regclaw-neo4j` (neo4j 5.26.32), `regclaw-pgvector` (pgvector 0.8.7, pg17) | `docker ps` |
| Read-only counts | Neo4j nodes 2,165 · relationships 5,504 · Clause 1,143 · Standard 102 · Module 1; pg `clause_embeddings` 1,143 rows (1,143 non-null) | read-only queries |
| Models | Text2Cypher: gpt-5-mini, effort low (code default; `AZURE_OPENAI_S8_*` unset, same as the S8 runs). S10 classifier: gpt-5-mini, effort high. Embeddings: text-embedding-3-small. All provisional (GPT), not the REPORT's Haiku/Sonnet. | provenance in the run caches |
| Code fix before live calls | `REGCLAW_RUN_DIR=data/runs/s9/live` routes the Cypher cache, the query-embedding cache and S10 records outside `data/runs/azure_openai/` (guarded); tools and lifespan flush tracing in `finally` | PR #15 |
| Smoke queries | Q1 "How is skin sensitization assessed for a contact lens?" (Part B) · Q2 "What genotoxicity tests should a bone screw undergo?" (Part C) | prompt |
| Device profile | the "Knee X" fixture from `mcp/test_server.py` (implant, tissue/bone, 8,760 h single exposure, Ti, ethylene oxide) | test file |

## Part A status (read-only; 0 paid calls)
| Item | Result | Evidence |
|---|---|---|
| git | `main` @ `01f9492`, clean | `git status` |
| Real-mode switch | `REGCLAW_STUB=="1"` read at import | `mcp/server.py:54` (pre-fix numbering) |
| Tools / timeouts | `query_regulatory_knowledge` (60 s), `get_applicable_standards` (120 s) | `mcp/server.py` tool decorators |
| top_k bound | `Field(ge=1, le=50)`, validated before the tool body | `mcp/server.py` |
| Tracing switch | `LANGFUSE_ENABLED` | `observability/tracing.py:11-13` |
| Spans | root `retriever`; children `neo4j.cypher`, `embed.query`, `pgvector.rank`, `neighbour.expand`, `text.fetch`; generations `cypher.generate` (cache miss only) and `standards.classify` | `retrieval/hybrid_retriever.py:572-617`, `llm/azure_openai.py:117` |
| Flush at exit | SDK registers `atexit` shutdown only; the server teardown had no flush, so the fix added one | langfuse `_client/resource_manager.py:278` |
| run_summary API | Observations API v2 (`get_many`, supports `trace_id`) | `observability/run_summary.py:43` |
| Usage log | none exists; paid calls counted from new run-cache rows and S10 records | grep |
| Keys | all names the code needs are set; names only were checked | key-name script |

## Tool and resource calls
| Call | Client | Exit | ms (client) | Paid Δ | Cumulative | Result |
|---|---|---|---|---|---|---|
| tools/list r1 | Inspector | 1 | 11,243 | 0 | 0 | blocker 1 (catalog vs config) |
| tools/list r2 | Inspector | 1 | 444 | 0 | 0 | config JSON escaping slip |
| tools/list r3 | Inspector | 4 | 32,798 | 0 | 0 | blocker 2 (pg via localhost) |
| tools/list r4 | Inspector | 1 | 6,322 | 0 | 0 | blocker 3 (cl100k vocabulary) |
| tools/list r5 | Inspector | 0 | 8,434 | 0 | 0 | 2 tools |
| resources/list | Inspector | 0 | 8,582 | 0 | 0 | 3 resources |
| resources/templates/list | Inspector | 0 | 9,044 | 0 | 0 | `regclaw://modules/{module_id}` |
| read `regclaw://modules` | Inspector | 0 | 8,837 | 0 | 0 | 1 module (`biocompatibility`, 10 standards, 1,143 clauses) |
| read `regclaw://actors` | Inspector | 0 | 7,751 | 0 | 0 | non-empty |
| read `regclaw://lifecycle-phases` | Inspector | 0 | 7,626 | 0 | 0 | non-empty |
| read `regclaw://modules/biocompatibility` | Inspector | 0 | 7,846 | 0 | 0 | same module object |
| S9-18 bad input (`top_k=500`) | Inspector | 5 | 8,264 | 0 | 0 | `isError`, pydantic `less_than_equal` on `top_k`; no model call, no span, no run files created |
| P1 query Q1 (top_k 5) | Inspector | 0 | 20,310 | 2 | 2 | stage ms 10,423 / 1,028 / 509 / 114; candidates 25 |
| P2 standards Knee X | Python client (180 s) | 1 | 190,395 | 3 | 5 | client timeout; retrieve done in about 15 s, the S10 request was still in flight |
| (conservative line) | — | — | — | 1 | 6 | the in-flight S10 request from P2, counted as paid |
| P2 r2 standards Knee X | Python client (ran with 180 s, see errors) | 0 | 169,000 | 1 | 7 | retrieve replayed from cache; classify about 157.6 s |
| C1 query Q2 (top_k 5), tracing on | Inspector | 0 | 18,982 | 2 | 9 | stage ms 7,587 / 1,072 / 18 / 12; candidates 62 |
| C2 standards Knee X, tracing on | Python client (600 s) | 0 | 144,347 | 1 | **10** | 20 standards, `llm_status ok`; cap reached |

Cumulative values include the conservative line; the ledger is `data/runs/s9/calls_ledger.jsonl` (git-ignored). The gate checked the cap between calls only.

## S9-17 shape checks (all pass)
| Field | Expected | Got | Pass |
|---|---|---|---|
| query `isError` | false | false | PASS |
| `candidate_count` | int | int (25) | PASS |
| `max_score` | float | float (0.5506) | PASS |
| ranked clauses | 1..5 | 5 | PASS |
| neighbour clauses | any | 5 | PASS |
| `source` values | subset of {ranked, neighbour} | both | PASS |
| `clause_id`, `standard`, `number`, `title` | present, non-empty | 10/10 | PASS |
| `text` | non-empty (lengths only) | 10 clauses, 82–5,582 chars | PASS |
| `obligation` | in {SHALL, SHOULD, MAY, NONE} | all four present | PASS |
| standards `isError` | false | false | PASS |
| standards list | non-empty | 20 | PASS |
| labels | in {mandatory, recommended, not applicable} | mandatory 11 · recommended 3 · not_applicable 6 | PASS |
| provenance | azure_openai, provisional | azure_openai, provisional true, gpt-5-mini | PASS |

Label mapping: the code emits `not_applicable` on the wire; it is read as "not applicable" (approved).

## Trace shape (Part C; 33 checks, all pass)
| Trace | Expected | Got | Pass |
|---|---|---|---|
| all | 3 traces | 3 | PASS |
| Q2 retriever (`b675007d…`) | 1 root `retriever`/RETRIEVER; one child each of `neo4j.cypher`, `embed.query`, `pgvector.rank`, `neighbour.expand`, `text.fetch`; 1 `cypher.generate` GENERATION under `neo4j.cypher` (gpt-5-mini, usage > 0); no orphans; no ERROR | 7 observations, as expected | PASS |
| Knee X retriever (`a8e21b08…`) | same root and 5 children; 0 `cypher.generate` (cache replay) | 6 observations, as expected | PASS |
| Knee X classify (`c9180d69…`) | 1 root `standards.classify`/GENERATION (gpt-5-mini, usage > 0) | 1 observation | PASS |
| all | no input/output fields in the export | 0 | PASS |

Source: Observations API v2 export by trace id, whitelisted fields only, `data/runs/s9/C_observations_r2.json` (git-ignored).

## Run summary (`observability/run_summary.py` over the export; n is tiny: P95 = max)
| Metric | Value |
|---|---|
| traces | 3 |
| model calls (GENERATION) | 2 |
| tool calls: from observations / from client outputs | 0 (no TOOL span exists) / 2 |
| total tokens (Langfuse) | 68,280 |
| Langfuse total cost | $0.049251 |
| failed executions / error observations | 0 / 0 |
| trace latency, n=3 | P50 8,699 ms · P95 133,584 ms |
| model-call latency, n=2 | P50 7,101 ms · P95 133,584 ms |

| Observation | n | P50 ms | P95 ms (= max) |
|---|---|---|---|
| cypher.generate | 1 | 7,101 | 7,101 |
| embed.query | 2 | 2 | 1,050 |
| neighbour.expand | 2 | 17 | 28 |
| neo4j.cypher | 2 | 83 | 7,586 |
| pgvector.rank | 2 | 5 | 22 |
| retriever | 2 | 142 | 8,699 |
| retriever.self | 2 | 7 | 12 |
| standards.classify | 1 | 133,584 | 133,584 |
| text.fetch | 2 | 12 | 17 |

The Langfuse token total is 1,152 above the run-cache sum (67,128); likely cached-input tokens stored as a separate usage key (not verified).

## Paid calls and cost (provisional (GPT); $0.25/1M input, $2.00/1M output)
| Call | Kind | Input tok | Output tok | Cost |
|---|---|---|---|---|
| P1 | Text2Cypher | 1,214 | 649 | $0.0016 |
| P1 | embedding | 11 | — | ≈ $0 |
| P2 | Text2Cypher attempt 1 (Cypher syntax error, retried) | 1,211 | 726 | $0.0018 |
| P2 | Text2Cypher attempt 2 | 1,685 | 812 | $0.0021 |
| P2 | embedding | 9 | — | ≈ $0 |
| P2 r2 | S10 classify | 47,377 | 20,943 | $0.0537 |
| C1 | Text2Cypher | 1,214 | 494 | $0.0013 |
| C1 | embedding | 11 | — | ≈ $0 |
| C2 | S10 classify | 47,377 | 18,043 | $0.0479 |
| **Counted** | **9 calls** | | | **$0.1084** |
| P2 in-flight S10 (conservative, sized like r2) | 1 call | — | — | ≤ $0.0537 |
| **Conservative total** | **10 calls (cap)** | | | **≤ $0.1621** |

Counting method: new rows in the run Cypher cache that carry usage, new query-embedding cache rows, and new S10 records with usage, before vs after each call. Limit: a call that fails before its row is written is not counted (hence the conservative line for P2).

## S9-05 overlap check
| Smoke query | Exact match | Max token Jaccard |
|---|---|---|
| Q1 | no | 0.200 |
| Q2 | no | 0.105 |

Compared against the 32 frozen query and rephrase texts (none printed).

## Flush latency (server stderr timestamps; client receive time for Knee X)
| Call | Interval | Tracing on (Part C) | Tracing off (Part B) | Approx. flush cost |
|---|---|---|---|---|
| Q2 / Q1 | end of retrieval log → `backend closed` | 1,107 ms | 37 ms | +1,070 ms |
| Knee X | `standards.guard` log → `backend closed` | 789 ms | 261 ms | +528 ms |
| Knee X, client side | `standards.guard` log → result received | 787 ms | — | per-call flush + response |

`backend closed` came 2 ms after the client received the result, so the teardown flush costs about 0 ms once the per-call flush has run.

## Limits / what broke
- **Not rebuilt:** the Literature Agent / Orchestrator; a standard MCP client stands in.
- **Models:** provisional (GPT) instead of the REPORT's Haiku/Sonnet.
- **Tiny n:** 1–3 samples per observation, so P95 = max.
- **Validation errors leave no trace:** the `top_k=500` call fails in argument validation, before any span.
- **Deferred:** the Claude Desktop demo (S9-02).
- **Blocker 1 — Inspector config:** `MCP_CATALOG_PATH` counts as `--catalog`, which cannot be combined with `--config`; dropped.
- **Blocker 2 — Postgres via `localhost`:** `localhost` tried IPv6 `::1` first while the container listens on `127.0.0.1` only; connect took about 130 s. Fixed by setting the `PG_DSN` host to `127.0.0.1` (0.2 s).
- **Blocker 3 — tokenizer vocabulary:** a fresh machine needs the verified cl100k vocabulary file plus `TIKTOKEN_CACHE_DIR` before real mode can start (`embed/token_stats.py:17-25`; downloads are disabled by design).
- **Blocker 4 — Inspector request timeout:** Inspector 2.13.0 builds its client without a `timeout` (`clients/cli/build/index.js:17492-17507`), so `requestTimeout` from the config never reaches `tools/call` (`:13106`, `:13342-13344`, `:14337`) and the SDK default of 60 s applies (`:13013`). The Python client was used for the long tool calls.
- **Sync-tool timeout is a no-op:** FastMCP wraps the tool in `anyio.fail_after(120)` (`fastmcp/tools/function_tool.py:415-430`) but runs sync tools with `anyio.to_thread.run_sync` and the default `abandon_on_cancel=False` (`:479`; `fastmcp/utilities/async_utils.py:9, 34`; `anyio/to_thread.py:27-30`), so the 120 s timeout on `get_applicable_standards` cannot stop a running call.
- **Slow classifier:** the Knee X classify request is about 47k input tokens (35 candidate clauses, about 103k characters) at effort high with a 24,000-token completion cap; it took about 158 s (r2) and 134 s (C2). The first P2 attempt hit the 180 s client timeout while the request was in flight.
- **Truncated earlier S10 record:** one of the two earlier S10 records shows `llm_status: fallback` with exactly 8,000 output tokens (metadata only).
- **Flush cost:** the per-call flush adds roughly 0.5–1.1 s to each tool response when tracing is on.
- **Log change:** with tracing on, the `httpx` INFO request lines disappear from the server log (cause not verified); the Knee X flush marker therefore uses the `standards.guard` line.
- **Trace gaps (accepted):** embeddings have no generation span, there is no TOOL span (tool calls are counted from client outputs), and `standards.classify` is its own trace.
- **Trace retention:** Langfuse Hobby keeps traces for 30 days, so the cloud trace expires; the local export `C_observations_r2.json` (git-ignored) and the screenshot are the lasting evidence.
- **Public key in span scope:** the Langfuse SDK attaches the project public key as a span scope attribute; the export whitelist drops it, and the screenshot was taken without the metadata panel.
- **Faulty first export kept:** `C_observations.json` and `C_trace_ids.json` (git-ignored) have all aliased fields empty because of an export-script bug; they are kept under the write-once rule, and the `_r2` files are the valid export.

## SELF-AUDIT
1. **Asked vs done**
   - Part A status: done (status table above).
   - Part B: free calls, bad-input call, P1, P2 (via r2) and shape checks: done.
   - Part C: Q2 and Knee X with tracing, export, trace shape, run summary, flush latency: done.
   - Part D: README line, this report, tests, image check: done.
2. **Decisions I made myself**
   - Inspector 2.13.0 instead of the 1-day-old 2.15.0 (supply-chain caution).
   - Helper scripts and npm cache inside the repo under `data/scratch/` (git-ignored), not in a system temp folder.
   - Launching the pinned Inspector bin through `node` instead of `npx.cmd`, to avoid cmd.exe quoting of JSON.
   - Measuring flush cost from server log intervals against the Part B baselines.
   - Write-once retries under new names (`_r2`…`_r5`).
3. **Deviations**
   - P2 r2 ran with a 180 s client timeout instead of the approved 600 s; it still succeeded (169 s).
   - The export files are `C_observations_r2.json` / `C_trace_ids_r2.json` instead of the planned names; the first export was faulty and kept.
4. **How I implemented it**
   - Repo files: `mcp/server.py`, `llm/provider.py`, `mcp/test_server.py`, `tests/test_llm_provider.py`, `.env.example` (PR #15); `README.md` (one line), `logs/S9_report.md`; `docs/img/S9_langfuse_trace.png` was added by Shyam.
   - Git-ignored helpers in `data/scratch/s9/`: key-name, count and overlap scripts; Inspector configs B and C; the call runner with ledger and cap gate; the Python client; shape, prompt-size, export, trace-shape and summary scripts.
5. **Checks with real output**
   - Tests: see the step log (mcp 20, obs 1, tests 1027 passed / 1 skipped / 0 failed).
   - Shape checks: 13/13 pass. Trace shape: 33/33 pass.
   - Counts: Clause 1,143 in Neo4j and in pgvector.
6. **Frozen items:** untouched — corpus, `retrieval_eval/queries.yaml` (read in memory for the overlap check only), freeze manifests, pre-registration, qrels manifest, the S10 code locks (`standards/engine.py`, `guard.py`, `overclaim.py`), and the S8 Cypher caches (nothing was written to `data/runs/azure_openai/`).
7. **Holdouts and sealed files:** not opened, printed, hashed or scored. The two earlier S10 records were read for provenance metadata only, as asked.
8. **Safety:** no commit or push by me; no keys printed; `.env` read only by code; nothing written outside the repo; no endpoint host in this report; no AI attribution.
9. **Paid/external calls:** 9 counted, 10 conservative (cap); about $0.108 counted, at most $0.162 conservative (provisional (GPT)). Free: 12 Inspector launches without model calls (tools/list r1–r5, 6 resource calls, the bad-input call), 10 Langfuse v2 reads (2 export runs × 5), 3 npm metadata requests and 1 package download.
10. **Not checked / assumed**
    - Whether Azure billed the in-flight P2 request.
    - Why the `httpx` log lines disappear with tracing on.
    - The 1,152-token difference in the Langfuse total.
11. **Errors and retries**
    - Four `tools/list` failures (blockers 1–3 and the config escaping slip).
    - P2 client timeout (blocker 4 plus the slow classifier).
    - A failed `b_client.py` patch launched in the same batch as r2, which caused the 180 s r2.
    - The `b_shape.py` patch failure (rewritten).
    - The first export's empty aliased fields (re-exported as `_r2`).
12. **Risks and what Shyam needs to do**
    - The cloud trace expires after 30 days.
    - Any S9 rerun needs a new cap decision, because the cap is used up.
    - A fresh machine needs the pg `127.0.0.1` host and the cl100k vocabulary + `TIKTOKEN_CACHE_DIR`.
    - Next: commit, push, PR, CI, Windows laptop test, merge.
