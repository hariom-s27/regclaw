# Rebuild results and evidence

> All numbers below are from **this rebuild**, on the public substitute corpus
> described in CORPUS.md, with earlier sections run on 2026-10-16 at commits a6bc968 and f6a461a;
> the obligation and S7 sections record their own dates and working trees. They are **not** the
> numbers from the original internship system and should not be compared to them.

### Limitations — read before quoting any number
- **Small n.** With small query/label sets, intervals are wide; report Wilson 95% CIs.
- **Author-written eval set.** Queries and relevance judged by the author; obligation gold uses a blind first annotator plus an independent second annotator.
- **LLM-as-judge.** Ragas scores depend on judge model and prompt version.
- **Single run.** LLM outputs are non-deterministic.
- Results marked provisional (GPT) use Azure OpenAI GPT in place of Claude; they will be replaced by Claude runs.
- **Corpus differs from the original system's**, so absolute scores are not comparable; only trends between configurations of this rebuild are reported.

The limitations describe the evaluation protocol. Obligation gold and independent
second annotation are measured below; retrieval judgments and Ragas runs remain pending.
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
Live verification was repeated on 2026-10-16 at commit `f6a461a` with
`python tasks.py graph --load --from-clauses --checks`. Captured counts:

| Label or relationship | Planned count | Fresh live inventory |
|---|---:|---|
| Module | 1 | 1 |
| Standard | 10 | 10 |
| Clause | 1,143 | 1,143 |
| RegulatoryAuthority | 5 | 5 |
| LifecyclePhase, Chemical, BiologicalEffect, DeviceType, RegulatoryActor | 0 each | 0 each |
| BELONGS_TO | 1,153 | 1,153 |
| REGULATED_BY | 10 | 10 |
| MENTIONS, REFERENCES | 0 each | 0 each |
| Total nodes | 1,159 | 1,159 |
| Total relationships | 1,163 | 1,163 |

The live inventory matches the plan with no unexplained gaps. Both loader passes
reported `nodes_created=0, relationships_created=0` against the existing graph.
The same command produced these check results:

| Check | Measured value | Status |
|---|---|---|
| 1. Counts by label/type | Matches the counts above; no gaps | PASS |
| 2. Orphan clauses | 0 | PASS |
| 3. Standards without Module except stubs | 0 | PASS |
| 4. Clauses without MENTIONS | 1,143/1,143; share 1.0 | WARN, expected: no S4 entities yet |
| 5. Never-mentioned entities | 0 | PASS |
| 6. Entity degree distribution | Empty result; no entities | PASS, vacuous |
| 7. Name collisions and singleton share | Empty result; no entities | PASS, vacuous |
| 8. Empty text or null number | 0 | PASS |
| 9. Duplicate MENTIONS | 0 | PASS |
| 10. Self references | 0 | PASS |
| 11. Orphan LifecyclePhase | 0 | PASS |
| 12. Uniqueness constraints | All 9 present; none missing | PASS |
| 13. Idempotent reload | 0 nodes and 0 relationships created | PASS |

Check #13 performs a second load and evaluates its actual creation counters
([graph/checks.py](../graph/checks.py)).
[The regression tests](../tests/test_graph.py) separately cover both zero and nonzero
reloads. The live PASS above comes from the second loader invocation in this run.

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

## S7 Azure embedding load and reconciliation

Measured on **2026-10-18**, in the working tree based on `04074a3`, on the public
substitute corpus. Provider decision: [ADR 0026](decisions/0026-azure-hosted-embeddings.md),
approved conflict C-AZEMB. The model remains `text-embedding-3-small`, 1536 dims;
Azure is a different host. Version `1` and
`versionUpgradeOption=OnceNewDefaultVersionAvailable` are **configured** metadata
supplied from the deployment, not an observed runtime version. Azure may
auto-upgrade the version. No parser, clause, gold or retrieval code was changed.

Commands, with Docker running and project Python active; credentials are loaded
from the local, untracked `.env`:

```powershell
$env:EMBED_PROVIDER = 'azure_openai'
$env:REGCLAW_STUB = '0'
python tasks.py embed --dry-run
python tasks.py embed --live
python tasks.py embed --reconcile
```

Producers: [embed/embed.py](../embed/embed.py),
[embed/store.py](../embed/store.py). The successful live receipt and subsequent
idempotency receipt are in ignored `data/embed_manifest.json`.

| Dry-run item | Measured/planned value |
|---|---:|
| Inputs | 1,143 |
| `cl100k_base` tokens, title + newline + text | 250,829 |
| Maximum input / inputs over 8,191 | 7,250 / 0 |
| Planned requests | 5 |
| Request input counts | 338, 216, 365, 179, 45 |
| Request token counts | 59,999; 59,524; 59,758; 59,949; 11,599 |
| Request token cap / rolling 60-second budget | 60,000 / 80,000 |
| Minimum pacing schedule, excluding API/store latency and retries | 180 seconds |

The completed load's run id is `041b5897-b197-48f9-9a41-6b4e91af52cc`.
Captured receipt:

| Completed live load item | Measured value |
|---|---:|
| Embedded / skipped unchanged | 1,143 / 0 |
| Successful batch requests | 5 |
| API attempts, including retries | 7 |
| HTTP 429 responses / retries | 2 / 2 |
| Successful-response API usage tokens | 250,829 |
| tiktoken tokens in successful batches | 250,829 |
| API minus tiktoken | 0 |
| Pacing sleep requested | 276.125 seconds |
| Loader `run()` wall time | 308.969 seconds |

The wall measurement includes API calls, pacing, upserts and statistics; it
excludes CLI input preflight and client/database setup. The 429s exercised the
retry path. An earlier attempt completed all five batch upserts but failed in
the pre-existing statistics query: `l2_norm(vector)` was ambiguous because the
installed extension exposes `l2_norm(halfvec)` and `l2_norm(sparsevec)`, with
`vector_norm(vector)` for this column type. That one statistics expression was
corrected, regression-tested, and the full load repeated. The earlier attempt
did not write a manifest/receipt, so its API usage, retry count and wall time
are unavailable. The table above describes the completed repeat, not aggregate
provider billing across both attempts. It required five additional successful
batch requests. Token-price estimates remain unverified Azure billing.

`python tasks.py embed --reconcile` produced:

| Reconciliation item | Measured value |
|---|---:|
| Neo4j Clause ids | 1,143 |
| Postgres distinct clause_ids | 1,143 |
| Postgres total rows | 1,143 |
| Clauses without a vector | 0 |
| Orphan vectors | 0 |
| Minimum L2 norm | 0.9994254359825807 |
| Maximum L2 norm | 1.0006130569056302 |
| Norms within 0.001 of one | true |
| Recorded completed live runs / API-token delta | 1 / 0 |

The following separate spot-check command uses Azure query embeddings and calls
the existing `HybridRetriever.stage2_rank` method directly. Its candidate set is
all 1,143 Neo4j clause ids, without a stage-1 LLM filter. It exercises the exact
stage-2 cosine SQL and does not measure end-to-end retrieval quality or latency.
The constructor is bypassed only to supply the already registered database
connection; no retrieval source is changed.

```powershell
$env:EMBED_PROVIDER = 'azure_openai'
$env:LANGFUSE_ENABLED = 'false'
@'
import json, os
from pathlib import Path
from dotenv import load_dotenv
import psycopg
from neo4j import GraphDatabase
from pgvector import Vector
from embed.embed import make_embedder, provenance
from embed.store import PostgresStore, INDEX_SQL
from embed.token_stats import offline_encoding, token_count
from retrieval.hybrid_retriever import HybridRetriever, PG_TABLE
load_dotenv('.env')
assert PG_TABLE == 'clause_embeddings'
queries = ['ethylene oxide sterilization residuals',
           'biocompatibility cytotoxicity testing', 'labeling requirements']
embedder = make_embedder()
vectors = embedder.embed(queries)
with GraphDatabase.driver(os.environ['NEO4J_URI'], auth=(os.environ.get('NEO4J_USER', 'neo4j'), os.environ['NEO4J_PASSWORD'])) as driver:
    records, _, _ = driver.execute_query('MATCH (c:Clause) RETURN c.id AS id', database_='neo4j')
    ids = [r['id'] for r in records]
with psycopg.connect(os.environ['PG_DSN']) as connection:
    store = PostgresStore(connection)  # Includes the unchanged no-ANN assertion.
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever.pg = connection
    dimensions = connection.execute('SELECT vector_dims(embedding), count(*) FROM clause_embeddings GROUP BY 1').fetchall()
    indexes = connection.execute(INDEX_SQL).fetchall()
    checks = []
    for query, vector in zip(queries, vectors.vectors):
        hits = retriever.stage2_rank(Vector(vector), ids, 3)
        checks.append({'query': query, 'top3': [{'clause_id': h.clause_id, 'cosine_score': h.score} for h in hits]})
    result = {**provenance(embedder), 'candidate_ids': len(ids),
              'dimensions_and_rows': dimensions, 'indexes': indexes,
              'query_api_requests': embedder.api_requests,
              'query_429s': embedder.rate_limits,
              'query_api_tokens': vectors.prompt_tokens,
              'query_tiktoken_tokens': sum(token_count(offline_encoding(), q) for q in queries),
              'checks': checks}
    Path('data/embed_spot_checks.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
'@ | python -
```

Captured dimension inventory: `(1536, 1143)`. The only index returned was
`clause_embeddings_pkey`, `USING btree (clause_id)`; **no HNSW or IVFFlat index**.
The startup assertion remains in place. The three queries used one additional
Azure request, 15 API tokens = 15 tiktoken tokens, and zero 429s. Ignored output:
`data/embed_spot_checks.json`. Captured top three results, scores rounded to nine
decimal places:

| Query | Rank | Clause id | Cosine score |
|---|---:|---|---:|
| ethylene oxide sterilization residuals | 1 | `epa-63o:§63.361` | 0.517534486 |
| ethylene oxide sterilization residuals | 2 | `epa-63o:§63.364(i)` | 0.516995451 |
| ethylene oxide sterilization residuals | 3 | `epa-63o:§63.365(c)` | 0.507077580 |
| biocompatibility cytotoxicity testing | 1 | `fda-iso10993-1:Attachment C.T1` | 0.636776640 |
| biocompatibility cytotoxicity testing | 2 | `fda-iso10993-1:VI` | 0.604444824 |
| biocompatibility cytotoxicity testing | 3 | `fda-iso10993-1:VI.A` | 0.586226685 |
| labeling requirements | 1 | `in-mdr:Schedule 4.Part III.Appendix II.3` | 0.627550764 |
| labeling requirements | 2 | `imdrf-n47:3.22` | 0.621888650 |
| labeling requirements | 3 | `in-mdr:Schedule 4.Part III.Appendix III.20` | 0.594802063 |

**Idempotency:** rerunning `python tasks.py embed --live` produced
`embedded=0`, `skipped_unchanged=1143`, `requests=0`, `api_requests=0`, no retries,
and 0.063 seconds in `run()`. The manifest's 1,143 current records all have
`action=skipped_unchanged`; its live API-attempt receipts are `[7, 0]`. The CLI
still prints the full-corpus five-request planning preview before the zero-call
result. No API usage was reported for the rerun because no request was sent.

Frozen clause SHA-256 at start and end (`Get-FileHash data/clauses.json -Algorithm SHA256`):
`C517FCEE4F89967AD62D49A42A8EFE0D0FFD76DF91AE1E88A2516F2B8B3C585D`.
The parser, gold files, input v1, table contract, startup assertion, ranking SQL
and `retrieval/` are unchanged. These documents are uncommitted drafts.

## Offline tests

Command: `python tasks.py test`.
Producer: [tasks.py](../tasks.py). Captured local test totals:

| Committed code / validation | MCP | Legacy summary | Root | Total |
|---|---:|---:|---:|---:|
| `2f50440`, fixes only, isolated checkout | 12 | 1 | 263 | 276 |
| `f9b9e69`, S14, isolated checkout | 12 | 1 | 272 | 285 |
| `a6bc968`, driver fix | 12 | 1 | 273 | 286 |
| Final S15b documentation validation | 12 | 1 | 273 | 286 |
| Live-results documentation refresh | 12 | 1 | 273 | 286 |
| S7b provider implementation | 12 | 1 | 314 | 327 |
| S7b norm compatibility fix and run-counter validation | 12 | 1 | 315 | 328 |
| S7b after live load, reconciliation, spot checks and idempotency | 12 | 1 | 315 | 328 |

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

The real `_run_ids` helper was called on 2026-10-16 at commit `f6a461a` with
this query and `term='ethylene oxide'`:

```cypher
MATCH (c:Clause)
WHERE toLower(c.text) CONTAINS $term
RETURN DISTINCT c.id AS clause_id
```

Reproducing command, with credentials loaded locally and tracing disabled:

```powershell
$env:LANGFUSE_ENABLED = 'false'
@'
import os
from dotenv import load_dotenv
from neo4j import GraphDatabase
from retrieval.hybrid_retriever import HybridRetriever
load_dotenv('.env')
with GraphDatabase.driver('bolt://localhost:7687', auth=(os.environ.get('NEO4J_USER', 'neo4j'), os.environ['NEO4J_PASSWORD'])) as driver:
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever.driver, retriever.db, retriever.timeout = driver, 'neo4j', 5
    ids = retriever._run_ids('MATCH (c:Clause) WHERE toLower(c.text) CONTAINS $term RETURN DISTINCT c.id AS clause_id', {'term': 'ethylene oxide'})
    print('SMOKE _run_ids stage-1 rows:', len(ids))
'@ | python -
```

Captured output: `SMOKE _run_ids stage-1 rows: 6`.
This exercises the helper's managed read transaction and timeout. No model was called.
At that earlier verification, the other retrieval stages were not run live and
production vectors were pending. The S7 section above records the subsequent
vector load and stage-2 spot checks; complete retrieval evaluation remains pending.

## Obligation gold and classifier

Measured offline on 2026-10-18 on the public substitute corpus, in the working tree
based on `b58a2a8`. These are rebuild measurements. Local output files below are
gitignored; their results are captured here. Activate the repo `.venv` and set
`$env:HF_HUB_OFFLINE='1'` and `$env:TRANSFORMERS_OFFLINE='1'`. No model download
or provider call was used. Source/version/hash inspection: `git rev-parse HEAD`,
installed package metadata and local SHA-256 audit -> `data/gold/run_audit.json`.

**Gold and agreement.** `python tasks.py gold-agreement` -> `data/gold/agreement.json`.
Validation passed: 184 unique rows, exact required columns, valid labels and binary
flags, ids equal to `goldset/gold_ids.csv`, and tracked label/flag/source export
identical to `data/gold/gold_final.csv`.

| Gold label | Count |
|---|---:|
| SHALL | 50 |
| SHOULD | 56 |
| MAY | 13 |
| NONE | 65 |

Reported collection protocol: the domain expert labelled blind to regex/model
predictions; Claude supplied an independent second annotation. Labels were locked
by SHA-256 before comparison. Human adjudication resolved 12 disagreements:
5 finals follow A, 7 follow B, and 172 labels agreed. The agreement command records
the supplied file hashes and verifies those counts in `agreement.json`. No labels
were changed in this run. Guideline v1.0 stays frozen; v1.1 applies to future labelling.

**Headline: rows 21-184, excluding practice.** The same agreement command/output
provides paired-row percentile bootstrap intervals: 5,000 resamples, seed 1, using
the existing retest functions, NumPy generator and sorted clause ids within each
subset. Alpha is nominal Krippendorff alpha with finite-population expected disagreement.

| Rows | n | Raw agreement [95% CI] | Cohen kappa [95% CI] | Krippendorff alpha [95% CI] |
|---|---:|---|---|---|
| 21-184 (headline) | 164 | 0.926829 [0.884146, 0.963415] | 0.894990 [0.832293, 0.947318] | 0.895290 [0.832714, 0.947469] |
| 1-184 | 184 | 0.934783 [0.896739, 0.967391] | 0.907723 [0.852724, 0.953680] | 0.907960 [0.853002, 0.953801] |

Both alpha values meet the scorer's `rely` threshold. Point estimates match Claude's
expected kappa. Small CI differences come from bootstrap RNG and row order: the same
command/output includes a Python `random.Random(1).randrange` cross-check in original
CSV order, reproducing the expected intervals to three decimals. Retest formulas
and their default bootstrap implementation were retained.

| Rows | Expected kappa [CI] | Python RNG cross-check CI | Scorer NumPy CI |
|---|---|---|---|
| 1-184 | 0.908 [0.852, 0.954] | [0.852476, 0.953711] | [0.852724, 0.953680] |
| 21-184 | 0.895 [0.834, 0.948] | [0.834023, 0.947619] | [0.832293, 0.947318] |

**Training and exclusions.** Original R3 inspection:
`git show 208e8ea:obligation/train.py` -> CLI declarations captured in
`data/gold/inspection.json`. Current `python obligation/train.py --help` ->
`data/gold/train_cli.txt`. Original defaults: finetune, output `model_ft`, 3 epochs,
lr 2e-5, batch 8, max length 256, seed 13, threads from `os.cpu_count()`.
Its split was unstratified 10% with split seed 13. The run explicitly sets seed 42
and the requested stratification. Class weighting, optimizer, scheduler and the
best-validation-macro-F1 checkpoint rule remain as in R3. Validation uses weak
labels for checkpoint selection only; human gold is reserved for evaluation.

```powershell
python obligation/train.py data/clauses.json data/weak_labels.json --gold goldset/gold_ids.csv --train-ids goldset/train_ids.csv --retest goldset/retest_ids.csv --near-dup-flags goldset/near_dup_flags.csv --audit-output data/gold/training_data.json --mode finetune --out data/models/legalbert_obl_v1 --epochs 3 --lr 2e-5 --bs 8 --max_len 256 --seed 42 --threads 8
```

Outputs: `data/gold/training_data.json`, `data/gold/train_legalbert.txt`, and
`data/models/legalbert_obl_v1/meta.json`. Settings: CPU only, LegalBERT
`nlpaueb/legal-bert-base-uncased`, max length 256, batch 8, lr 2e-5, seed 42,
3 epochs, stratified 10% validation. Exclusions below come from this command/output;
original split/review overlap counts are captured by frozen-file inspection in
`inspection.json` alongside the R3 inspection.

| Item | Count |
|---|---:|
| Weak-labelled corpus | 1,143 |
| Requested train ids | 755 |
| Outside train ids, already excluded by frozen split | 388 |
| Additional gold / retest exclusions within train ids | 0 / 0 |
| Additional near-duplicate train ids excluded | 17 |
| Additional needs-review ids within train ids | 0 |
| Retained pool | 738 |
| Training / validation | 664 / 74 |
| Pool NONE / SHALL / SHOULD / MAY | 331 / 267 / 140 / 0 |

The outside-train group contains all 184 gold ids (including all 50 retest ids)
and 204 other needs-review ids. There are 240 needs-review ids overall, including
36 already in gold. All 34 weak-MAY corpus clauses are in gold, leaving no MAY
training examples (`inspection.json`: weak corpus/gold distributions). No frozen
labels or splits were altered.

Training command output (`train_legalbert.txt`, `meta.json`):

| Epoch | Wall seconds including validation/checkpoint | Validation loss | Macro-F1 vs weak validation labels |
|---|---:|---:|---:|
| 1 | 784.113 | 0.666797 | 0.564368 |
| 2 | 781.317 | 0.070862 | 0.750000 |
| 3 | 787.124 | 0.022145 | 0.750000 |

No epoch exceeded the 45-minute limit. Total loop time: 2352.564 seconds
(`meta.json`). Epoch 2 was selected: ties retain the earlier checkpoint even
when the later epoch has lower loss. This is weak-label validation, not human-gold quality.

Model: `data/models/legalbert_obl_v1/` (gitignored). Hash command:
`Get-FileHash data/models/legalbert_obl_v1/model.safetensors -Algorithm SHA256` ->
recorded in `data/models/legalbert_obl_v1/sha256_manifest.json`, verified in
`data/gold/run_audit.json`:
`8a7768c9952ca3cd4a54cc976be2e06d25dff7dfb1fcf923b03e392a3df94a46`.
The manifest also covers config, tokenizer and training metadata.

**Prediction and scoring.** The prediction command below calls the existing
`obligation/infer.py` function on gold-id-filtered clauses, on CPU, producing
`data/gold/pred_legalbert.json`. The measured run also captured its selection and
timing metadata in `data/gold/prediction_run.json`.

```powershell
@'
import csv, json, sys, torch
from pathlib import Path
sys.path.insert(0, str(Path("obligation").resolve()))
from infer import predict
torch.set_num_threads(8)
ids = {r["id"] for r in csv.DictReader(open("goldset/gold_ids.csv", encoding="utf-8-sig"))}
clauses = [c for c in json.load(open("data/clauses.json", encoding="utf-8")) if c["id"] in ids]
labels, probabilities = predict([c["text"] for c in clauses], "data/models/legalbert_obl_v1", bs=16)
rows = [{"id": c["id"], "label": labels[int(v.argmax())]} for c, v in zip(clauses, probabilities)]
Path("data/gold/pred_legalbert.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
'@ | python -
python tasks.py gold-score data/gold/gold_final.csv --output data/gold/scores_regex.json
python tasks.py gold-score data/gold/gold_final.csv --predictions data/gold/pred_legalbert.json --output data/gold/scores_legalbert.json
```

These scorer commands and JSON files supply every score table below. The second
view excludes 15 flagged gold items. Corpus weights use the existing weak-label x
document-type cell weights `N_cell / n_cell`, held fixed in the sensitivity view.

| Subset | n | System | Macro-F1 | Accuracy | Corpus-weighted macro-F1 | Corpus-weighted accuracy |
|---|---:|---|---:|---:|---:|---:|
| All | 184 | regex | 0.711525 | 0.750000 | 0.758836 | 0.869217 |
| All | 184 | legalbert | 0.639387 | 0.826087 | 0.685955 | 0.902408 |
| Without flagged gold | 169 | regex | 0.701657 | 0.733728 | 0.754914 | 0.866393 |
| Without flagged gold | 169 | legalbert | 0.635727 | 0.816568 | 0.686176 | 0.902790 |

Per-class results, from the same score commands and JSON outputs:

| Subset | Label | Gold support | Regex P | Regex R | Regex F1 | LegalBERT P | LegalBERT R | LegalBERT F1 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| All | SHALL | 50 | 0.800000 | 0.800000 | 0.800000 | 0.769231 | 0.800000 | 0.784314 |
| All | SHOULD | 56 | 0.880000 | 0.785714 | 0.830189 | 0.857143 | 0.964286 | 0.907563 |
| All | MAY | 13 | 0.323529 | 0.846154 | 0.468085 | 0.000000 | 0.000000 | 0.000000 |
| All | NONE | 65 | 0.860000 | 0.661538 | 0.747826 | 0.840580 | 0.892308 | 0.865672 |
| Without flagged gold | SHALL | 48 | 0.808511 | 0.791667 | 0.800000 | 0.764706 | 0.812500 | 0.787879 |
| Without flagged gold | SHOULD | 46 | 0.853659 | 0.760870 | 0.804598 | 0.830189 | 0.956522 | 0.888889 |
| Without flagged gold | MAY | 13 | 0.323529 | 0.846154 | 0.468085 | 0.000000 | 0.000000 | 0.000000 |
| Without flagged gold | NONE | 62 | 0.851064 | 0.645161 | 0.733945 | 0.846154 | 0.887097 | 0.866142 |

Confusion matrices from the same score JSON files. Rows = gold, columns = predicted;
order SHALL, SHOULD, MAY, NONE on both axes.

```text
All gold: regex
SHALL   40   1   5   4
SHOULD   8  44   2   2
MAY      0   1  11   1
NONE     2   4  16  43
All gold: legalbert
SHALL   40   3   0   7
SHOULD   0  54   0   2
MAY     10   1   0   2
NONE     2   5   0  58
Without flagged gold: regex
SHALL   38   1   5   4
SHOULD   7  35   2   2
MAY      0   1  11   1
NONE     2   4  16  40
Without flagged gold: legalbert
SHALL   39   3   0   6
SHOULD   0  44   0   2
MAY     10   1   0   2
NONE     2   5   0  55
```

Paired results: the second scoring command -> `data/gold/scores_legalbert.json`.
Bootstrap: 10,000 paired row resamples, seed 20261015, 95% percentile CI, keeping
all four labels in each resample. Decision: CI wholly above zero favours LegalBERT;
wholly below zero favours regex; straddling zero is inconclusive.

| Subset | Delta macro-F1 (LegalBERT - regex) | 95% CI | Decision | Exact McNemar p |
|---|---:|---|---|---:|
| All | -0.072138 | [-0.143915, 0.005000] | straddles 0 | 0.048766766 |
| Without flagged gold | -0.065930 | [-0.138694, 0.011284] | straddles 0 | 0.043558522 |

| Subset | Both correct | LegalBERT fixes | LegalBERT breaks | Both wrong |
|---|---:|---:|---:|---:|
| All | 123 | 29 | 15 | 17 |
| Without flagged gold | 110 | 28 | 14 | 17 |

LegalBERT has a lower macro-F1 point estimate, but the primary interval straddles
zero: this run does not establish a macro-F1 winner. McNemar tests correctness,
not macro-F1; more fixes than breaks explains the higher accuracy without
contradicting the primary result. Model choice and labels were not changed after scoring.

**Limitations.** One domain-expert annotator plus an AI second annotator does not
measure agreement between two human experts. MAY has only 13 gold items
(`gold-agreement` -> `agreement.json`). LegalBERT learns regex weak labels, not
human gold. The frozen sample exhausts weak MAY, leaving no MAY training examples,
and the checkpoint predicts no MAY (`training_data.json`, `inspection.json`, and
the prediction command -> `prediction_run.json`). These facts limit four-class
quality claims. Row bootstrap and the near-duplicate sensitivity view do not
establish transfer to other corpora.

**Verification.** `python tasks.py test` after scoring and after documentation ->
`data/gold/tests_phase_c.txt` and `data/gold/tests_phase_d.txt`: 292 passes each
(12 MCP, 1 summary, 279 root). The eight-clause dry-run test makes one NumPy
classification-head update with Torch/Transformers imports blocked; it checks
model-free data flow. The real CPU run above exercises Transformer training.
Source: `tests/test_obligation_train.py`. Frozen/model digest audit ->
`data/gold/run_audit.json`: all nine frozen inputs match the start manifest.
`Get-FileHash data/clauses.json -Algorithm SHA256`, recorded in that audit, gives
the same SHA-256 at start and end:
`C517FCEE4F89967AD62D49A42A8EFE0D0FFD76DF91AE1E88A2516F2B8B3C585D`.

## Remaining measurements

| Work | Status | Reason |
|---|---|---|
| Manual parser gold for the remaining sources | TBD | Author annotation is not complete |
| Full entity extraction and grounded accuracy | Live extraction provisional (GPT); accuracy pending | `python tasks.py ner` in the Azure/low environment below; no accuracy claim until the hand gold (S4b) exists |
| Obligation agreement and LegalBERT quality | Measured | See Obligation gold and classifier; macro-F1 comparison is inconclusive |
| Visual matrix verification | TBD | Human visual comparison not completed |
| Production embedding reconciliation | Measured | S7: 1,143 clauses and vectors, no missing/orphan ids; see commands and counts above |
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
| Live graph counts, check statuses and reload counters | `python tasks.py graph --load --from-clauses --checks`; captured above |
| Helper smoke row count | `_run_ids` reproducing command above |
| Matrix rows, cells, mismatch count and source-page numbers | `standards.crosscheck.crosscheck`; captured above |
| Token totals, maximum and guard count | `python tasks.py embed --dry-run`; captured above |
| S7 live load, usage, reconciliation and idempotency | `python tasks.py embed --live`, `--reconcile`, then `--live`; receipts and captured counts above |
| S7 vector dimensions, indexes and exact ranking | Stage-2 spot-check command above; `data/embed_spot_checks.json` (ignored) |
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

## S4 provisional (GPT): live entities and frozen evaluation

Claude remains the default provider and the final headline. This Azure run is
**provisional (GPT)** under ADR 0025 / C-GPT.

no accuracy claim until the hand gold (S4b) exists

Grounding verifies text support, not the correctness of a type
or canonical. The fixed prompt, schema, twelve checks, normaliser, aliases,
standard-reference rules, parser, existing gold and corpus are unchanged.

Every numerical paragraph and table in this S4 section is labelled
**provisional (GPT)** and inherits its phase's producing command below, including
metadata, checks and test counts. Counts in the split table describe the frozen
sampling design, not model performance. Commands are recorded as plain text;
local artifact paths are not publication links.

Sampling metadata — **provisional (GPT)** run. The eval freeze preceded every
new live S4 call. Producing command:
`python -m goldset.sample_entities`. Seed `20261018`; within each source family,
character length and clause id order define three rank terciles. Allocation
starts with two samples per stratum and fills proportional deficits; the split
starts with one dev item per stratum and fills proportional deficits. Both use
the same seeded RNG. Fixture ids and the earlier S4 smoke id are excluded.
Only ids and strata are tracked. Holdout model rows are never displayed or
reviewed individually; subsequent reporting uses aggregates.

Frozen-id digest — **provisional (GPT)** run; command:
`Get-FileHash goldset/entity_gold_ids.csv -Algorithm SHA256`:
`b0964e8b603b30a2bf39713e32ab3e06745e117cedac9d54608803eaa3fb6113`.

Sampling counts — **provisional (GPT)** run; command:
`python -m goldset.sample_entities`.

| Source family — provisional (GPT) | Dev | Holdout |
|---|---:|---:|
| FDA | 3 | 3 |
| EU MDR | 9 | 6 |
| IMDRF | 4 | 2 |
| India | 11 | 6 |
| EPA | 3 | 3 |
| Total | 30 | 20 |

Run environment and controls — **provisional (GPT)**; commands: the Phase 1,
Phase 2 and Phase 3 `python tasks.py ner` invocations below, with controls
implemented in `ner/live_controls.py`. `LLM_PROVIDER=azure_openai`,
`REGCLAW_STUB=0`, deployment
`AZURE_OPENAI_S4_DEPLOYMENT=gpt-5-mini`,
`AZURE_OPENAI_S4_REASONING_EFFORT=low`, `LANGFUSE_ENABLED=false`; `.env` is
loaded privately. Response version: `gpt-5-mini-2025-08-07`. Azure output lives
under `data/runs/azure_openai/s4/`; high effort uses its `high/` subdirectory.
The cache key includes provider, deployment and effort. Outputs and the call
ledger retain provenance, including the response's reported model version.
The Azure scheduler uses three workers and reserves at most 78 requests and
78,000 estimated prompt-plus-completion-cap tokens per rolling 61 seconds.
It honors Retry-After, checkpoints successful raw results, and guards the $8
cumulative and projected full-run cost and the full-corpus error threshold.

### Phase 1 — provisional (GPT) gate

Producing command for every Phase 1 number — **provisional (GPT)** (fixed ids
also saved in the local `phase1/ids.json`):

```powershell
python tasks.py ner --ids "fda-iso10993-1:Attachment G,fda-iso10993-1:IV.A,eu-mdr:Annex I.14.4,eu-mdr:Art 10(9)(b),imdrf-n47:7,imdrf-n47:Appendix B.5.12,in-ep:5.8.1,in-mdr:12,epa-63o:Appendix A.11.3.1,epa-63o:Table 5"
```
The fixed non-eval sample selects two clauses per source family using seed
`20261018`. Local receipts: `data/runs/azure_openai/s4/phase1/`.

| Type — all measurements provisional (GPT) | Mentions | Unique canonicals | Alias resolved | Sent to review | Grounding rejects / attempted |
|---|---:|---:|---:|---:|---:|
| biological_effects | 17 | 13 | 29.4% | 52.9% | 0 / 23 (0%) |
| chemicals | 3 | 2 | 66.7% | 33.3% | 0 / 6 (0%) |
| device_types | 6 | 6 | 0% | 83.3% | 0 / 9 (0%) |
| regulatory_actors | 8 | 5 | 12.5% | 62.5% | 0 / 12 (0%) |
| lifecycle_phases | 12 | 6 | 0% | 75.0% | 0 / 15 (0%) |

**provisional (GPT)**, from the fixed-id command above: ten calls, ten successful end-turn stops, zero error rows,
zero retries, nineteen non-grounding rejects, forty-six alias-review rows.
Input/output/reasoning tokens: **19,915 / 8,215 / 5,760**; reasoning is included
in output. Cached prompt tokens: **10,752**. Wall time **47.36 seconds**.
Estimated cost **$0.023550**; projected full-run cost **$2.6917**. The grounding
and cost gates pass. These are conservative estimates using published Azure
regional rates of $0.275 input / $2.20 output per million tokens, without any
cached-input discount; subscription billing is unverified. Source:
[Microsoft Foundry pricing announcement](https://devblogs.microsoft.com/foundry/whats-new-in-azure-ai-foundry-august-2025/).
The pre-existing R11 legacy-rate estimate is retained separately in receipts.

Corpus-quoting example rows are omitted. This report contains no verbatim
clause text; named canonical aggregates below exclude holdout clauses.

Review percentages count retained mentions flagged by legitimacy/collision or
fuzzy proposals, once per mention. Grounding denominators count all attempted
entities, including those later rejected for another reason or deduplicated.
Neither the alias-review candidates nor this report change `aliases.csv`.

### Blind S4b materials

Annotation/scorer metadata — **provisional (GPT)** run. Sampling command:
`python -m goldset.sample_entities`; scorer validation command:
`python tasks.py test`. Annotation-sheet construction copied only source
clause id/source/text into the blank sheet, without predictions.

**provisional (GPT)** run metadata, validated with `python tasks.py test`:
`data/gold/entity_annotation_sheet.csv` contains the fifty eval clauses and
empty type/notes cells, with no predictions. It is gitignored because it carries
corpus text. `goldset/ENTITY_GUIDELINE_v1.md` mirrors the frozen prompt and
uses only its synthetic few-shots. `goldset/score_entities.py` consumes reviewed
typed character spans and canonicals, calculates strict/exact/partial/type and
canonical-set precision/recall/F1, micro and five-type macro, and uses 1,000
clause bootstrap resamples with seed `20261018`. Empty denominators score zero.
One-to-one span matching maximizes total credit; per-type measures filter each
type, whereas exact/partial micro matching permits cross-type matches.
Known-answer synthetic tests cover wrong types, partial boundaries, duplicates,
non-greedy matching, canonical aliases, empty clauses and bootstrap repeatability.
No corpus scoring has been performed. Ambiguous repeated mentions require
explicit human-reviewed offsets before scoring. The guideline requests a blind
ten-clause repeat one week later for intra-annotator agreement.

### Phase 2 — provisional (GPT) full low-effort run

Producing command for every Phase 2 number — **provisional (GPT)**:
`python tasks.py ner` in the Azure/low environment above. Local frozen
receipts: `data/runs/azure_openai/s4/phase2/`. **provisional (GPT)**: **1,143**
clauses, **1,133** new requests and **10** reused Phase 1 cache entries; **zero**
error rows, HTTP 429s, schema retries, budget retries or other retries. All
successful calls stopped with `end_turn`. No error ids/reasons to report.

| Type — all measurements provisional (GPT) | Mentions | Unique canonicals | Alias resolved | Sent to review | Grounding rejects / attempted |
|---|---:|---:|---:|---:|---:|
| biological_effects | 259 | 18 | 17.4% | 71.8% | 5 / 455 (1.1%) |
| chemicals | 327 | 183 | 31.2% | 29.1% | 6 / 359 (1.7%) |
| device_types | 1,129 | 702 | 0% | 69.4% | 50 / 1,345 (3.7%) |
| regulatory_actors | 1,094 | 11 | 6.2% | 70.5% | 25 / 1,547 (1.6%) |
| lifecycle_phases | 1,334 | 13 | 0% | 91.0% | 54 / 1,839 (2.9%) |

**provisional (GPT)**, from `python tasks.py ner`: **4,143** retained mentions, **1,402** total rejects,
**2,833** deduplicated alias-review rows, **4,207** unsupported-expansion flags
and **2** cross-type collision flags. Candidates have not been accepted or
added to the frozen aliases. New Phase 2 calls used **2,090,837 / 852,989 /
606,976** input/output/reasoning tokens; output includes reasoning. Cached
prompt tokens: **1,715,712**. Phase 2 wall time: **6,116.20 seconds**; incremental
estimated cost **$2.451556**. Including the ten Phase 1 calls, the full low run
used **2,110,752 / 861,204 / 612,736** input/output/reasoning tokens, **1,726,464**
cached prompt tokens, **6,163.56 seconds** of pipeline time and **$2.475106**.
Tests and reporting time are excluded from pipeline time.

Check #12 comparison against Phase 1 — **provisional (GPT)**; producing
commands: the Phase 1 fixed-id `python tasks.py ner --ids ...` command and
Phase 2 `python tasks.py ner`. The following cross-sample arithmetic is derived
from their summary receipts:

| Type — provisional (GPT) | Phase 1 mean | Full mean | Phase 1 empty rate | Full empty rate | >25% relative change |
|---|---:|---:|---:|---:|---|
| biological_effects | 1.700 | 0.227 | 80.0% | 89.8% | mean |
| chemicals | 0.300 | 0.286 | 70.0% | 87.3% | none |
| device_types | 0.600 | 0.988 | 50.0% | 49.3% | mean |
| regulatory_actors | 0.800 | 0.957 | 50.0% | 45.0% | none |
| lifecycle_phases | 1.200 | 1.167 | 20.0% | 35.3% | empty rate |

**provisional (GPT)**: these three cross-sample warnings were inspected as aggregates. They compare a
small balanced-by-family sample with the full corpus, not repeated inference
on identical clauses. The unchanged automatic check requires matching ordered
id sets and therefore does not flag this different-size comparison. No prompt,
check, alias or output was adjusted in response. The explicit grounding,
full-run error and cost stop conditions all pass.

Named stop-entity review — **provisional (GPT)**; producing command:
`python tasks.py ner`, followed by non-holdout clause-frequency aggregation.
It uses **only the 1,123 non-holdout clauses**.
Automatic approval review rejected full-corpus named frequencies because they
would include holdout-derived model output. The safe alternative excludes
holdout rows before reading canonicals for this review; their names and
frequencies remain sealed. The full-run unnamed metrics above still include
all clauses. Each list is top fifteen or all canonicals if fewer exist;
all frequencies are provisional (GPT).

| Type — provisional (GPT) | Non-holdout canonical (clause frequency), provisional (GPT) |
|---|---|
| biological_effects | other (90); sensitization (21); carcinogenicity (16); genotoxicity (16); reproductive developmental toxicity (14); irritation (14); cytotoxicity (13); systemic toxicity (11); degradation (8); material mediated pyrogenicity (7); hemocompatibility (6); implantation effects (6); acute systemic toxicity (5); subchronic systemic toxicity (5); subacute systemic toxicity (4) |
| chemicals | ethylene oxide (74); nickel titanium alloy (13); nickel (12); titanium (4); ethane-1,2-diol (3); hexane (3); formaldehyde (3); plasticizers (3); stainless steel (3); eto cems (2); human blood (2); human plasma (2); endocrine-disrupting substances (2); sterilant residues (2); isopropyl alcohol (2) |
| device_types | ivd medical device (84); medical device (68); eto cems (26); in vitro diagnostic medical device (25); investigational medical device (25); medical device and ivd medical device (14); investigational device (12); new in vitro diagnostic medical device (10); implant (8); software (7); implantable device (7); nitinol device (7); active medical device (7); active implantable device (6); device for use by lay persons (6) |
| regulatory_actors | other (378); manufacturer (242); competent authority (228); sponsor (64); applicant (50); notified body (30); investigator (28); authorised representative (22); laboratory (18); importer (11); distributor (6) |
| lifecycle_phases | other (310); manufacturing (267); design development (151); clinical investigation (146); premarket submission (92); conformity assessment (82); sterilization (67); storage transport (65); post market surveillance (62); packaging (43); change control (12); material selection (8); reprocessing (6) |

Repeated command: `python tasks.py ner`. **provisional (GPT)** cache verification:
**zero API calls**, **1,143 cache hits**, **zero** new tokens/cost, unchanged call
ledger, **92.16 seconds**. The entity JSON is byte-identical to the frozen full
run. Existing review logic starts with the previous run's canonical bank, so
the rerun produces **2,919** review rows instead of **2,833**; chemical review
share becomes **30.3%**, device review share **80.6%**. Entity names, grounding,
aliases and the other mention counts are unchanged. The initial full-run table
above uses the preserved first-run receipts, not the overwritten rerun summary.
There are no identical-id drift alerts on the cache rerun.

### Phase 3 — provisional (GPT) effort comparison, no winner

Producing command for every high-effort Phase 3 number — **provisional (GPT)**.
Equivalent CLI to the executed Python launcher (same task and frozen ids):

```powershell
$env:AZURE_OPENAI_S4_REASONING_EFFORT = 'high'
$ids = (Import-Csv goldset/entity_gold_ids.csv).clause_id -join ','
python tasks.py ner --ids $ids
```

**provisional (GPT)**, from the high-effort command above: the existing process
completed all fifty ids before the continuation check;
the continuation made **zero** additional API calls. High-effort artifacts and
cache are under `data/runs/azure_openai/s4/high/`; `comparison.json` contains
only split/type aggregates. All measurements below are **provisional (GPT)**.
Response version remains `gpt-5-mini-2025-08-07`.

Every comparison below is **provisional (GPT)**. Low results come from
Phase 2 `python tasks.py ner`; high results come from the high-effort
`python tasks.py ner --ids $ids` command above. An offline `python -` aggregate
pass joined those receipts to the frozen split CSV, computed per-type counts,
grounding fractions, set Jaccards and operational means, and saved only
split/type aggregates in `comparison.json`. It made no provider calls and
exposed no individual holdout outputs.

DEV counts and grounding — **provisional (GPT)** (mentions / unique canonicals;
thirty clauses), from those low/high commands and aggregate pass:

| Type — provisional (GPT) | Low mentions / unique | High mentions / unique | Low grounding rejected / attempted | High grounding rejected / attempted |
|---|---:|---:|---:|---:|
| biological_effects | 6 / 2 | 7 / 2 | 0 / 13 (0%) | 0 / 12 (0%) |
| chemicals | 7 / 6 | 9 / 8 | 0 / 8 (0%) | 0 / 9 (0%) |
| device_types | 27 / 25 | 30 / 28 | 1 / 32 (3.13%) | 0 / 30 (0%) |
| regulatory_actors | 28 / 8 | 31 / 8 | 1 / 40 (2.50%) | 0 / 44 (0%) |
| lifecycle_phases | 39 / 10 | 51 / 10 | 3 / 66 (4.55%) | 0 / 76 (0%) |

Holdout **aggregates only**, **provisional (GPT)**, from the same low/high
commands and aggregate pass (twenty clauses; no individual ids, names, mentions,
text, evidence or per-clause scores were displayed or inspected):

| Type — provisional (GPT) | Low mentions / unique | High mentions / unique | Low grounding rejected / attempted | High grounding rejected / attempted |
|---|---:|---:|---:|---:|
| biological_effects | 14 / 14 | 15 / 14 | 0 / 17 (0%) | 0 / 18 (0%) |
| chemicals | 17 / 15 | 22 / 19 | 0 / 17 (0%) | 0 / 22 (0%) |
| device_types | 25 / 24 | 28 / 27 | 0 / 28 (0%) | 1 / 30 (3.33%) |
| regulatory_actors | 17 / 7 | 22 / 7 | 1 / 24 (4.17%) | 0 / 28 (0%) |
| lifecycle_phases | 23 / 12 | 33 / 13 | 2 / 36 (5.56%) | 0 / 47 (0%) |

Canonical-set agreement — **provisional (GPT)**, from the same low/high
commands and aggregate pass. Per-clause Jaccard uses sets of `(type, canonical)` after the unchanged
normaliser. Two empty sets score one. Only aggregate means/minima are exposed:

| Scope — provisional (GPT) | DEV mean | DEV minimum | Holdout mean | Holdout minimum |
|---|---:|---:|---:|---:|
| All five types together | 0.6498 | 0 | 0.7165 | 0.25 |
| biological_effects | 0.9667 | 0 | 0.9429 | 0 |
| chemicals | 0.9667 | 0 | 0.9433 | 0 |
| device_types | 0.7867 | 0 | 0.8054 | 0 |
| regulatory_actors | 0.8194 | 0 | 0.7375 | 0 |
| lifecycle_phases | 0.6683 | 0 | 0.5717 | 0 |

Operational means — **provisional (GPT)**, from the same low/high commands and
aggregate pass. Per-clause operational means include every attempt, including truncation
retries. Response latency sums all request latencies for a clause, excluding
limiter/queue time; it is different from amortized pipeline wall time.
Reasoning tokens are already included in output tokens.

| Split / effort — provisional (GPT) | Input / clause | Output / clause | Reasoning / clause | Response seconds / clause | USD / clause | Calls | Budget retries |
|---|---:|---:|---:|---:|---:|---:|---:|
| DEV low | 1,795.47 | 728.70 | 507.73 | 7.03 | 0.002097 | 30 | 0 |
| DEV high | 2,134.20 | 10,086.00 | 9,895.47 | 73.40 | 0.022776 | 35 | 5 |
| Holdout low, aggregate | 1,828.70 | 830.85 | 585.60 | 7.96 | 0.002331 | 20 | 0 |
| Holdout high, aggregate | 2,517.35 | 12,952.70 | 12,734.40 | 93.37 | 0.029188 | 27 | 7 |

**provisional (GPT)**, from those low/high commands: error rows and schema errors/retries are **zero** for both
efforts in both splits. High effort made **62** calls: **50** end-turn stops
and **12** max-token stops, all recovered by the existing doubled-budget retry.
The existing Azure mapping reserves **12,000** combined tokens initially and
**24,000** on that retry for high effort; these budgets were not retuned.
High totals: **114,373 / 561,634 / 551,552** input/output/reasoning tokens;
**86,272** cached prompt tokens; **2,431.87 seconds** pipeline wall time;
estimated **$1.267047**. High retained **248** mentions and produced **229**
alias-review rows. Its only grounding rejection was one device mention out of
the **60** attempted device entities. No high-effort entities were merged into
the graph.

**no accuracy claim until the hand gold (S4b) exists**. Differences in counts,
grounding and canonical-set agreement do not identify a winner. Holdout outputs
remain sealed; this report contains only their aggregate numbers.

### Phase 5 — provisional (GPT) merge and graph

Producing merge command — **provisional (GPT)**:
`python tasks.py enrich --ner data/runs/azure_openai/s4/phase2/ner.json --output data/runs/azure_openai/s4/phase2/enriched.json`.
The unchanged default source is **regex weak labels**, `data/weak_labels.json`;
it covers every corpus id without missing/unknown ids. **provisional (GPT)** merged
label counts: SHALL **481**, SHOULD **244**, MAY **34**, NONE **384**. LegalBERT
predictions were not selected; the S5b decision is unchanged. The enriched
contract is unchanged and provenance is a sibling JSON sidecar with source and
output hashes. The default `data/enriched.json` is untouched.

Producing graph command for every graph count and check — **provisional (GPT)**.
Graph entrypoint `graph.build_graph.main` ran with the following arguments,
equivalent to this CLI command (stdout and counters were captured locally):
`python tasks.py graph --load --input data/runs/azure_openai/s4/phase2/enriched.json --checks --verify-idempotent`.
`--from-clauses` is deliberately absent because that flag strips entities.
Graph work overlapped the independent high-effort comparison and uses only the
completed Phase 2 low-effort entities. Receipts are in the Phase 2 directory:
`graph_receipt.json`, `graph_checks.txt`, `enriched.provenance.json`.

| Nodes — provisional (GPT) | Count |
|---|---:|
| Module | 1 |
| Standard | 102 |
| Clause | 1,143 |
| LifecyclePhase | 13 |
| RegulatoryAuthority | 5 |
| Chemical | 183 |
| BiologicalEffect | 18 |
| DeviceType | 702 |
| RegulatoryActor | 11 |
| Total | 2,178 |

| Relationships — provisional (GPT) | Count |
|---|---:|
| BELONGS_TO | 1,153 |
| MENTIONS | 4,143 |
| REGULATED_BY | 10 |
| REFERENCES | 198 |
| Total | 5,504 |

**provisional (GPT)**, from the graph command above: **92** referenced-standard stubs; initial load created
**1,019** nodes and **4,341** relationships. Reload created **zero** nodes and
**zero** relationships. No graph check failed.

| Check — provisional (GPT) | provisional (GPT) result | Status |
|---|---|---|
| 1 Counts by label/type | Exact plan match; no unexplained gaps | PASS |
| 2 Orphan clauses | 0 | PASS |
| 3 Standards without Module, except stubs | 0 non-stubs | PASS |
| 4 Clauses without MENTIONS | 100 / 1,143 = 8.75%, below 10% threshold | PASS |
| 5 Never-mentioned entities | 0 | PASS |
| 6 Entity degree distribution | RegulatoryActor max 384; LifecyclePhase max 316; threshold 200 | WARN |
| 7 Name collisions and singleton share | 0 collisions; singleton shares below | PASS |
| 8 Empty text or null number | 0 | PASS |
| 9 Duplicate MENTIONS | 0 | PASS |
| 10 Self references | 0 | PASS |
| 11 Orphan LifecyclePhase | 0 | PASS |
| 12 Uniqueness constraints | All 9 present | PASS |
| 13 Idempotent reload | 0 nodes and 0 relationships created | PASS |

| Entity label — provisional (GPT) | Degree p50 | Degree p90 | Maximum | Singleton share |
|---|---:|---:|---:|---:|
| BiologicalEffect | 8 | 18.5 | 91 | 0% |
| Chemical | 1 | 2 | 77 | 78.69% |
| DeviceType | 1 | 2 | 86 | 85.61% |
| RegulatoryActor | 30 | 247 | 384 | 0% |
| LifecyclePhase | 68 | 246.6 | 316 | 0% |

Known limitations — **provisional (GPT)**; producing commands are the merge
and graph commands above:

- **provisional (GPT)**, graph command above: **DeviceType singleton share: 85.61%**, requiring hand review of specificity
  and aliases. Chemical singleton share is **78.69%**.
- **provisional (GPT)**, graph command above: **Degree WARN (check #6)**: RegulatoryActor maximum **384** and LifecyclePhase
  maximum **316**, against the **200** threshold. Stop-entity review is pending.
- **provisional (GPT)**, merge command above: **obligation labels = regex weak labels (LegalBERT pending S5b)**, loaded from
  `data/weak_labels.json`. This graph does not establish LegalBERT performance.

No entity, alias, graph rule or threshold was changed to remove a warning.
Graph audits expose aggregate values, never individual holdout entity rows or
holdout-derived named frequencies. Counts are structural checks, not accuracy.

### Final S4 audit

All S4 run measurements in this section remain **provisional (GPT)**. Producing
commands: the Phase 1 fixed-id `python tasks.py ner --ids ...`, Phase 2
`python tasks.py ner`, the repeated cache-only command, and Phase 3 high-effort
`python tasks.py ner --ids $ids`; totals sum their recorded usage and wall times.
Total
metered calls across both efforts: **1,205**; estimated cost **$3.742153**;
input/output/reasoning tokens **2,225,125 / 1,422,838 / 1,164,288**; cached prompt
tokens **1,812,736**. Reasoning is included in output, not billed twice.
Sum of extraction pipeline wall times: **8,595.44 seconds** (**143.26 minutes**),
plus **92.16 seconds** for the cache-only verification. Tests/reporting are
excluded; graph work overlapped the high-effort run. Azure subscription billing
is unverified; the conservative published-rate estimate omits cache discounts.
Total HTTP 429 count: **zero**.

| Stop condition — provisional (GPT) | Observed | Result |
|---|---|---|
| Phase 1 grounding rejection >15% for any type | 0% in every type | PASS |
| Full-run error rows >5% | 0 / 1,143 | PASS |
| Estimated cost >$8 | $3.742153 cumulative; admission and full-run projection guards active | PASS |

Test counts — **provisional (GPT)** run validation; producing command:
`python tasks.py test` after each completed phase. All passed. The
annotation materials and graph work were completed while independent live work
continued, so their validation preceded the final high-effort validation.

| Phase — provisional (GPT) | Status | Passing tests at phase validation |
|---|---|---:|
| 0 Freeze | Done | 328 |
| 1 Live check | Done | 332 |
| 2 Full low + cache verification | Done | 338 |
| 3 High comparison | Done | 339 |
| 4 Blind pack / scorer | Done; no corpus scoring | 338 |
| 5 Merge / graph | Done; degree warning retained | 338 |
| Final code and metadata guard | Done | 339 (12 MCP + 1 summary + 326 root) |

An initial sandbox test attempt could not access pytest temporary directories;
the suite passed with normal temporary-file access. `git diff --check` passes.
No prompt, schema, check, normaliser, alias-table content, standard-reference
rule, parser, clause or pre-existing gold file was modified. The annotation
sheet was reverified as fifty rows with all type/notes cells empty
(**provisional (GPT)** run metadata, offline CSV validation); digest command:
`Get-FileHash data/gold/entity_annotation_sheet.csv -Algorithm SHA256`. Its SHA-256
is `e1b272d0727aaade3bc3e4e5df16a95f80ee135b5c2779e0df0c630ff9526b7f`.

Corpus integrity — **provisional (GPT)** run; command:
`Get-FileHash data/clauses.json -Algorithm SHA256`.
`data/clauses.json` SHA-256 **at both start and end**:
`c517fcee4f89967ad62d49a42a8efe0d0ffd76df91ae1e88a2516f2b8b3c585d`.
The frozen eval-id SHA-256 is unchanged from Phase 0.

File-by-file changes:

| File — provisional (GPT) run | Change |
|---|---|
| `goldset/entity_gold_ids.csv` | Frozen ids/strata/splits only; no corpus text |
| `goldset/sample_entities.py` | Reproducible source/length sampling, exclusions, split, overwrite refusal |
| `goldset/ENTITY_GUIDELINE_v1.md` | One-page blind guideline; only synthetic prompt examples |
| `goldset/score_entities.py` | Typed-span/canonical scorer and seeded clause bootstrap; completed-gold guard |
| `tests/test_score_entities.py` | Known-answer synthetic metric and bootstrap tests |
| `ner/extract.py` | Provider cache identity, high-effort path, dotenv entrypoint, bounded batches/checkpoints, pacing hooks, request/retry/token/time reporting, retained-mention review percentages, Azure cost estimate, response provenance on error rows |
| `ner/live_controls.py` | Azure-only rolling token/request limits, Retry-After handling, persistent call/cost ledger, cumulative/projected cost and full-corpus error guards |
| `llm/azure_openai.py` | Preserve response reasoning-token and cached-input counters |
| `ner/merge.py` | Explicit NER/output paths, provider-directory guard, provenance/hash sidecar; unchanged weak-label default |
| `tests/test_entity_run.py` | Freeze, cache/provider/effort separation, pacing/cost/error/retry guards, merge paths/source and terminal-error response-version tests |
| `docs/results.md` | This provisional report, commands, metrics, warnings and audit |

DESIGN CONFLICTS: no new extraction, schema, graph or metric-contract change.
C-GPT remains provisional and Claude remains the default/final headline. The
reporting restriction is explicit: full-corpus named top-canonical frequencies
were rejected by automatic approval review to protect holdout outputs; the
safe named review uses non-holdout clauses only. No review/alias decision was
made from holdout output. The existing run-dependent proposal count on cache
rerun is reported above; entity JSON is byte-identical.

All run receipts/caches, the blind annotation sheet and enriched output remain
ignored under `data/`. The approved commits contain only the frozen-id and
annotation/scorer files, the listed run fixes/tests, and this report in separate
commits. No `data/`, `.env`, PDF or model artifact is included. No push.

## S4b/S4c provisional (GPT): dev scores and normaliser v1.1

Every number in this section is **provisional (GPT), dev split** unless it is
labelled as a corpus or graph count. Claude remains the default provider and the
final headline (ADR 0025). The holdout split is sealed: no holdout prediction or
score has been inspected, and the final holdout score is computed once, later,
after the freeze-manifest check (ADR 0027). This section contains no clause text.

### Gold

Gold metadata — **provisional (GPT), dev split** context; validation command:
`python -m goldset.score_entities s4b --completed-hand-gold` (gold loader checks).

- 50 eval clauses, frozen before any S4 live call: **30 dev / 20 holdout**.
- **185** gold entity entries over the 50 clauses, labelled by a human annotator
  and Claude, then adjudicated under `goldset/ENTITY_GUIDELINE_v1.1.md`. Every
  change is in the adjudication log.
- The gold has no character offsets. Only the per-clause canonical-set scheme is
  scored; strict/exact/partial/type schemes are not computed.
- Dev scoring counts unique (type, canonical) pairs per clause: **95** on dev
  (biological effects 2, chemicals 7, device types 18, regulatory actors 25,
  lifecycle phases 43).

### Dev scores, v1.0 vs v1.1

Producing commands — **provisional (GPT), dev split**: v1.0 is
`python -m goldset.score_entities s4b --completed-hand-gold --normaliser-version 1.0`
on the S4 Phase 2 (low) and Phase 3 (high) runs; v1.1 is the same scorer with
`--normaliser-version 1.1` on the cache-only v1.1 (rule rev3) rebuilds, with
`--baseline-low` set to the Phase 2 run (command in ADR 0027). Canonical-set F1
with clause-bootstrap CI95 (1,000 resamples, seed `20261018`):

| Run — provisional (GPT), dev split | Micro | Biological effects | Chemicals | Device types | Regulatory actors | Lifecycle phases |
|---|---|---|---|---|---|---|
| low, v1.0 | 0.693 [0.598, 0.772] | 0.500 [0.000, 0.889] | 1.000 [0.000, 1.000] | 0.400 [0.222, 0.578] | 0.792 [0.579, 0.914] | 0.756 [0.649, 0.842] |
| low, v1.1 | 0.723 [0.611, 0.806] | 0.500 [0.000, 0.889] | 1.000 [0.000, 1.000] | 0.533 [0.235, 0.755] | 0.792 [0.579, 0.914] | 0.756 [0.649, 0.842] |
| high, v1.0 | 0.717 [0.623, 0.787] | 0.444 [0.000, 0.800] | 0.875 [0.000, 1.000] | 0.458 [0.216, 0.667] | 0.786 [0.556, 0.909] | 0.809 [0.702, 0.889] |
| high, v1.1 | 0.744 [0.640, 0.814] | 0.444 [0.000, 0.800] | 0.875 [0.000, 1.000] | 0.583 [0.242, 0.767] | 0.786 [0.556, 0.909] | 0.809 [0.702, 0.889] |

**provisional (GPT), dev split**: raw model canonicals before any normalisation
(surface form only) score micro **0.638** (low) and **0.682** (high). The v1.0
re-score reproduces the original S4b scores exactly.

### Low over high

**provisional (GPT), dev split**, same commands: the paired high − low micro F1
difference is **+0.024, CI95 [−0.051, 0.094]** under v1.0 and **+0.022, CI95
[−0.053, 0.092]** under v1.1. Both intervals include zero. High effort costs about
**11×** more per dev clause (**$0.0228** vs **$0.0021**, published-rate estimate)
and is about 10× slower. Decision: **low effort** stays the S4 default.

### Normaliser gain

**provisional (GPT), dev split**: v1.1 − v1.0 for low effort is **+0.030, CI95
[0.000, 0.080]** micro F1. The gain is exactly the three dev plural pairs in
device types (correct pairs 70 → 73). Other types are unchanged.

Corpus counts — **provisional (GPT)**, all 1,143 clauses, cache-only rebuild
(`python -m ner.reclean`, **0** model calls): DeviceType unique canonicals
**702 → 689**, singleton share **85.61% → 84.76%**, **13** merges. Lifecycle
phases (13) and chemicals (183) are unchanged. The gold is unaffected: **0** gold
canonicals change under v1.1. The rule, its three revisions and the holdout
disclosures are in ADR 0027.

### Graph after the v1.1 reload

Producing command — **provisional (GPT)**: the old entity layer (927 nodes and
4,143 MENTIONS) was deleted in one checked transaction, then
`python tasks.py graph --load --input <v1.1 enriched.json> --checks --verify-idempotent`.
Weak obligation labels are unchanged.

| Count — provisional (GPT) | S4 (v1.0) | v1.1 |
|---|---:|---:|
| Clause / Standard / Module / RegulatoryAuthority | 1,143 / 102 / 1 / 5 | unchanged |
| LifecyclePhase / Chemical / BiologicalEffect / RegulatoryActor | 13 / 183 / 18 / 11 | unchanged |
| DeviceType | 702 | 689 |
| BELONGS_TO / REFERENCES / REGULATED_BY / MENTIONS | 1,153 / 198 / 10 / 4,143 | unchanged |
| Total nodes / relationships | 2,178 / 5,504 | 2,165 / 5,504 |

**provisional (GPT)**: 12 of 13 graph checks pass. Check 6 (degree) still WARNs:
RegulatoryActor maximum **384** and LifecyclePhase maximum **316**, against the
200 threshold; stop-entity review is pending. DeviceType degree p50 **1**, p90
**2**, maximum **86**. The idempotent reload created **0** nodes and **0** relationships.

### Known limitations

- **provisional (GPT), dev split**: DeviceType phrase noise. The frozen prompt
  yields long descriptive phrases as device types. Device-type F1 is the lowest
  of the five types, and the corpus DeviceType singleton share stays near 85%.
- **provisional (GPT), dev split**: `other` is over-used. On dev, low effort
  predicts `other` for 5 of 6 biological-effect, 10 of 28 regulatory-actor and
  12 of 39 lifecycle-phase entities. `other` is the most frequent false positive in
  each of those types.
- **provisional (GPT), dev split**: only **2** dev biological-effect gold pairs
  and **7** chemical pairs; those per-type scores are not informative.
- **provisional (GPT), dev split**: 30 clauses give wide CIs; one annotator plus
  Claude adjudication is not inter-annotator agreement.
