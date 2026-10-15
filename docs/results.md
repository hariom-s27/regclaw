# Rebuild results and evidence

> All numbers below are from **this rebuild**, on the public substitute corpus
> described in CORPUS.md, run on 2026-10-16 at commit a6bc968. They are **not** the
> numbers from the original internship system and should not be compared to them.

### Limitations — read before quoting any number
- **Small n.** With small query/label sets, intervals are wide; report Wilson 95% CIs.
- **Author-written eval set.** Queries and relevance judged by the author; obligation gold uses a blind first annotator plus an independent second annotator.
- **LLM-as-judge.** Ragas scores depend on judge model and prompt version.
- **Single run.** LLM outputs are non-deterministic.
- **Corpus differs from the original system's**, so absolute scores are not comparable; only trends between configurations of this rebuild are reported.

The limitations describe the evaluation protocol. Human gold, the independent
second annotation, retrieval judgments, and Ragas runs are not complete yet.
No model-quality or timing metric computed in stub mode is published here.
This page captures command output; links below point to tracked producers.

## Structure scoring

Command: `python tasks.py structure-score`.
Producer: [ingest/structure_gold.py](../ingest/structure_gold.py).
The command was rerun on the date and code commit recorded above.
Captured real-corpus structural results:

| Document/view | Expected | Actual | Matched | Precision | Recall | F1 | Parent accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| EPA raw | 134 | 136 | 134 | 0.985294118 | 1.000000000 | 0.992592593 | 0.955223881 |
| EPA policy | 136 | 136 | 136 | 1.000000000 | 1.000000000 | 1.000000000 | 1.000000000 |
| EU MDR raw | 364 | 370 | 363 | 0.981081081 | 0.997252747 | 0.989100817 | 0.889807163 |
| EU MDR policy | 371 | 370 | 370 | 1.000000000 | 0.997304582 | 0.998650472 | 1.000000000 |

Policy parent accuracy is exactly 100%: EPA 136/136, EU MDR 370/370.
Only independently parsed official headings retained by the parser qualify.
The raw official projection remains visible; arbitrary extras remain errors.
The missing EU unit is `Art 1(6)(a)`, so policy recall remains below complete.

Structural extras by design from the same command:

- EPA: `Appendix A.5.3`, `Appendix A.8.1`.
- EU MDR: `Annex I`, `Annex XIV.A`, `Annex XIV.B`, `Annex XV.I`,
  `Annex XV.II.3.8`, `Annex XV.II.3.19`, `Annex XV.III`.

The scorer also prints Wilson intervals, coverage findings, and previews.
These structural results do not establish complete body-text coverage or answer quality.

## Graph plan and live verification

Commands:

```powershell
python tasks.py graph --dry-run --from-clauses
python tasks.py graph --load --from-clauses --checks
```

Producer: [graph/build_graph.py](../graph/build_graph.py).
Captured plan output on the recorded date:

| Label or relationship | Planned count | Fresh live inventory |
|---|---:|---|
| Module | 1 | TBD |
| Standard | 10 | TBD |
| Clause | 1,143 | TBD |
| RegulatoryAuthority | 5 | TBD |
| LifecyclePhase, Chemical, BiologicalEffect, DeviceType, RegulatoryActor | 0 each | TBD |
| BELONGS_TO | 1,153 | TBD |
| REGULATED_BY | 10 | TBD |
| MENTIONS, REFERENCES | 0 each | TBD |
| Total nodes | 1,159 | TBD |
| Total relationships | 1,163 | TBD |

Docker reported the local containers up, but the live load failed with
`ServiceUnavailable` while waiting for the Bolt handshake on localhost:7687.
It failed before a transaction opened. The printed plan is not a live inventory.
Fresh graph check statuses and check #13 are **TBD**, because checks were not reached.
Re-run the load/check command once the local Bolt service responds.

Check #13 performs a second load and evaluates its actual creation counters
([graph/checks.py](../graph/checks.py)).
[The regression tests](../tests/test_graph.py) cover both zero and nonzero reloads.
Those offline tests do not establish a fresh live PASS.

## Matrix cross-check

Producer: [standards/crosscheck.py](../standards/crosscheck.py), function `crosscheck`;
rule data are loaded through [standards/data_loader.py](../standards/data_loader.py).
The real-corpus check was repeated on the recorded date. Captured output:

```text
rows: 24
cells: 264
mismatches: []
verified_by_human: false
physical_pdf_pages: 49, 50
```

Source clauses are `fda-iso10993-1:Attachment A.T1` and
`fda-iso10993-1:Attachment A.T2`. This compares extracted table cells with rule data.
Visual human verification is TBD; an automated agreement does not perform it.
The synthetic mismatch-detection test remains separate in
[tests/test_standards.py](../tests/test_standards.py).

## Embedding input size

Command: `python tasks.py embed --dry-run`.
Producer: [embed/embed.py](../embed/embed.py), using
[embed/token_stats.py](../embed/token_stats.py).
This read-only dry run was repeated on the recorded date. Captured values:

```text
inputs: 1143
total_tokens: 250829
max_tokens: 7250
over_8191: 0
```

Input is title plus newline plus text, counted with `cl100k_base`.
The hard ceiling is 8,191 tokens in the producer; the table dimension is 1536 in
[embed/store.py](../embed/store.py). No embeddings were requested or inserted.
These are input-size measurements, not throughput, cost, or model quality claims.

## Offline tests

Command: `python tasks.py test`.
Producer: [tasks.py](../tasks.py). Captured local test totals:

| Committed code / validation | MCP | Legacy summary | Root | Total |
|---|---:|---:|---:|---:|
| `2f50440`, fixes only, isolated checkout | 12 | 1 | 263 | 276 |
| `f9b9e69`, S14, isolated checkout | 12 | 1 | 272 | 285 |
| `a6bc968`, driver fix | 12 | 1 | 273 | 286 |
| Final S15b documentation validation | 12 | 1 | 273 | 286 |

The runner selects stub mode. Fakes test contracts and guard behavior;
the totals above are test outcomes, not retrieval or model-quality metrics.
Remote CI is TBD because these commits have not been pushed.
[CI configuration](../.github/workflows/ci.yml) selects Python 3.11.

## Tracing and driver confirmation

[requirements.txt](../requirements.txt) pins Langfuse 5.0.1 after local API inspection.
Manual observations, updates, and flush remain supported by the installed SDK.
The off path avoids importing it; the separate false switch avoids client creation.
[tests/test_tracing.py](../tests/test_tracing.py) checks stage identity, trace shape,
no import, no network, exception propagation, generations, PageIndex tools,
summary interval arithmetic, and cursor pagination.
The summary fixture is synthetic and is not a published latency result.

The installed Neo4j driver is 6.4.1 (`python -c "import neo4j; print(neo4j.__version__)"`).
Inspection printed these supported signatures:

```text
unit_of_work(metadata=None, timeout=None)
ManagedTransaction.run(self, query, parameters=None, **kwparameters)
Session.execute_read(self, transaction_function, *args, **kwargs)
```

The transaction callback carries `timeout`; its query is a string.
Read routing remains through `execute_read`. The installed transaction source
explicitly rejects `Query` objects, which explains the original bug.
[tests/test_retrieval_driver.py](../tests/test_retrieval_driver.py) uses a rejecting
fake ManagedTransaction and verifies timeout, parameters, ids, and eager consumption.

The fixed `_run_ids` helper was called with this query and `term='ethylene oxide'`:

```cypher
MATCH (c:Clause)
WHERE toLower(c.text) CONTAINS $term
RETURN DISTINCT c.id AS clause_id
```

Fresh smoke row count is **TBD**: the same localhost Bolt handshake failure
prevented the helper from opening its read transaction. No model was called.
The other retrieval stages were not run live; production vectors are still pending.

## Remaining measurements

| Work | Status | Reason |
|---|---|---|
| Manual parser gold for the remaining sources | TBD | Author annotation is not complete |
| Full entity extraction and grounded accuracy | TBD | Live extraction and human evaluation not run |
| Obligation agreement and LegalBERT quality | TBD | Gold annotation, independent second annotation, training and evaluation pending |
| Fresh graph inventory, checks and reload counters | TBD | Local Bolt handshake failed |
| Fixed-helper smoke row count | TBD | Local Bolt handshake failed |
| Visual matrix verification | TBD | Human visual comparison not completed |
| Production embedding reconciliation | TBD | Production vectors not loaded |
| Retrieval quality and pipeline latency | TBD | Judged queries and complete live run pending |
| Exact-vector latency and its target budget | TBD | Benchmark not run; budget not selected |
| Part II document, indexing and evaluation | TBD | Intended document and evaluation setup await confirmation |
| Remote CI | TBD | Commits remain local until the user pushes |

## Number provenance

Repeated numbers in README and ADRs refer to the matching captured output above.
Source dates and decision chronology are recorded in the journal without publishing private notes.

| Numbers and identifiers | Tracked source or reproducing command |
|---|---|
| Structural ids, P/R/F1, parent scores and counts | `python tasks.py structure-score`; captured above |
| Clause and graph-plan counts | `python tasks.py graph --dry-run --from-clauses`; captured above |
| Matrix rows, cells, mismatch count and source-page numbers | `standards.crosscheck.crosscheck`; captured above |
| Token totals, maximum and guard count | `python tasks.py embed --dry-run`; captured above |
| Test totals | `python tasks.py test`; captured above |
| Python version, dependency pins and model identifiers | CI workflow, requirements, `ner/client.py`, `standards/classifier.py` |
| Clause field count and physical-page origin | `ingest/validate.py`, `pageindex/pi_common.py` |
| Vector dimensions and token guard | `embed/store.py`, `embed/embed.py` |
| Framework, part identifiers and caveat locator | `standards/profile.py`, tracked standards YAML files |
| MCP limits | `mcp/server.py` |
| Stage and span names | `retrieval/hybrid_retriever.py`, `observability/tracing.py` |
| Driver version, timeout API and transaction behavior | Installed-package inspection; regression test linked above |
| Corpus dates, recorded digests and licences | `CORPUS.md`; acquisition code in `corpus/download.py`; edition metadata in `graph/standards.yaml` |
| Parser versions and dated fixes | Journal entries, named Git commits, and parser regression tests |

No result has been inferred from a roadmap target or a stub-mode quality metric.
