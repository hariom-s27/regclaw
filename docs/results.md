# Rebuild results and evidence

> All numbers below are from **this rebuild**, on the public substitute corpus
> described in CORPUS.md, with earlier sections run on 2026-10-16 at commits a6bc968 and f6a461a;
> the obligation section records its own date and working tree. They are **not** the
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
The other retrieval stages were not run live; production vectors are still pending.

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
| Full entity extraction and grounded accuracy | TBD | Live extraction and human evaluation not run |
| Obligation agreement and LegalBERT quality | Measured | See Obligation gold and classifier; macro-F1 comparison is inconclusive |
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
| Live graph counts, check statuses and reload counters | `python tasks.py graph --load --from-clauses --checks`; captured above |
| Helper smoke row count | `_run_ids` reproducing command above |
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
