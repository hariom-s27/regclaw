# S10 offline evaluation harness (R23)

Evaluates `get_applicable_standards` (rule floor + LLM + guard, ADR 0019) against
human gold labels. Everything here runs offline on fakes; the live run later only
injects a real client factory (and retriever) into `eval.s10.runner.run()`.

```
python -m eval.s10.runner --profiles dev_profiles.yaml --gold dev_gold.yaml --out results/s10_dev \
    --repeats 5 --setting medium:effort=medium --setting high:effort=high --seed 0 --n-boot 2000
```

Outputs in `--out`:
- `runs.jsonl`: one line per tool call. Fields:
  - setting, `effort_effective`, repeat, profile, `llm_status`
  - `llm_detail`, `llm_error` (exception type only), `http_attempts`, `llm_provenance` (see below)
  - final / rule / raw-LLM label per ISO part
  - `rule_endpoints` (diagnostic only)
  - citations, guard events, over-claim hits
  - ruleset id + sha
- `summary.json`: metrics per setting with 95% CIs, plus provenance:
  - git sha + dirty flag
  - ruleset version + combined sha
  - engine version + engine code sha (`engine.py`, `guard.py`, `overclaim.py`; per-file shas too)
  - matrix sha (actual and stored)
  - engine version, prompt sha
  - settings, effective effort, seed, n_boot, repeats
  - split, file shas, timestamp
  - `tag_split` (whether a tags file was given)
- `citation_review.csv`: a blank sheet for HUMAN citation precision/recall (repeat 1 only).

## Unit of evaluation: ISO part, not endpoint (granularity limit)
The tool, the LLM and the guard all label **ISO parts** (`ISO 10993-5`, `ISO 10993-11` …).
Gold and every metric use the same unit.

One part can cover several endpoints. For example, `ISO 10993-11` covers acute, subacute, subchronic and chronic systemic toxicity plus material-mediated pyrogenicity, and `ISO 10993-3` covers genotoxicity, carcinogenicity and reproductive toxicity. So a part-level "mandatory" can hide an endpoint inside that part that the rules or the LLM never flagged.

The engine's per-endpoint labels are written to `runs.jsonl` (`rule_endpoints`) for later inspection. They are **not scored**, and no LLM endpoint labels exist.

## Framework: FDA 2023 Attachment A only
Eval profiles must use `framework: FDA_2023_ATTACHMENT_A`.

The other framework values take different code paths:
- `ISO_10993_1_2025` uses the PMDA anchor rows, which are unverified, and falls back to FDA rows.
- `ISO_10993_1_2018` reuses the FDA matrix.

Mixing frameworks would score several rulesets as one. Every output states its own `ruleset` and `disclaimer` (`standards/ruleset.py`).

## Profile-set format (`s10-eval-profiles.v1`)
```yaml
format: s10-eval-profiles.v1
split: dev                      # dev | holdout; every row must match
profiles:
- id: P-001                     # unique
  stratum: S3                   # S1..S8 or trap
  split: dev
  contact_category: implant/tissue_bone   # <contact_nature>/<contact_site> of one component
  duration: C                   # A|B|C, must equal the engine-derived class (longest component)
  flags: [ethylene_oxide]       # must equal the flags derived from the profile:
                                # ethylene_oxide, absorbable, nano, repeat_use,
                                # multi_component, attachment_g_material (R11 trigger)
  comment: optional, <= 500 chars
  profile: {DeviceProfile.v1}   # strict, validated by standards/profile.py
```

## Gold format (`s10-eval-gold.v1`)
```yaml
format: s10-eval-gold.v1
split: dev
labels:
- profile_id: P-001
  item: ISO 10993-6             # a part of endpoint_to_part.v1.yaml or a declared device-specific standard
  label: mandatory              # mandatory | recommended | not_applicable
  source: {doc: fda-iso10993-1, clause_id: "fda-iso10993-1:<number>"}   # pointer only
```
The schema forbids extra fields, so no clause text can be stored.

`mandatory` means "must be addressed (test, existing data or documented rationale)".

## Holdout seal
Holdout files are sealed with:
```python
eval.s10.split_guard.seal_holdout([profiles], manifest_path)
```
This writes the manifest: the LF-normalised sha256 of each file.

- `manifest_path` is required and **write-once**: an existing manifest is never overwritten.
- Files sealed later (for example the gold) go into a second manifest, so no sealed file is hashed again.
- The runner takes `--holdout-manifest PATH` (alias `--manifest`), which can be repeated. Every holdout file must be listed in exactly one of the given manifests. With no flag, the runner looks for `holdout_manifest.json` next to the profiles file.

The runner refuses `split: holdout` unless `--final-holdout` is given **and** the manifests exist and match. It also refuses:
- a dev file that contains a holdout row
- `--final-holdout` on dev files

Dev needs no flag.

## Run status: `llm_detail`
The guard's `llm_status` stays `ok` or `fallback`. The runner adds one more field per run, read from the wrapped client's last response:

| `llm_detail` | when |
|---|---|
| `ok` | the model stopped normally and the guard accepted the output |
| `truncated` | the output hit the token cap (`finish_reason: length`) |
| `refusal` | the model refused or the content filter stopped it |
| `parse_error` | the model stopped normally but the data was empty or broke the output schema |
| `error` | the client raised an exception, or returned an unexpected stop reason |
| `unavailable` | there was no response or no stop reason |

`summary.json` counts `llm_detail` per setting and reports every cell metric twice:
- **primary** (top level of each setting): all runs as the tool returned them. A failed LLM call falls back to the rule floor and is scored as such.
- **secondary** (`secondary_ok_only`): only runs with `llm_detail == ok`.

Each block carries a `metrics_scope` label.

## Azure output budget, timeout and effort (role S10)
Two optional environment variables. With both unset, requests are byte-identical to before.

| variable | meaning |
|---|---|
| `AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS` | integer 1..128000. When set, it is the cap for role S10 at every effort, so all settings share one cap. Unset: 8,000 x 1 / 1 / 2 / 3 for minimal / low / medium / high. |
| `AZURE_OPENAI_S10_TIMEOUT_S` | number > 0. When set, it is the client timeout in seconds for role S10. Unset: the SDK default. |

**Pre-registered cap for the S10 evaluation: 64000.** The largest output seen so far is 20,943 tokens; twice that is 41,886, which is below 64000. The value is set in the environment for the run; the code default stays unset.

Truncation re-run: if the cap variable is set and the output is truncated, the adapter sends the same request once more with twice the cap (at most 128000). There is no second re-run, and no re-run when the cap is already 128000.

Effort: `AzureOpenAIAdapter('S10', effort=...)` takes an explicit effort (default: the environment, as before). Azure ignores the effort written in the request, so the live factory (`eval.s10.runner.azure_client_factory`) builds one client per setting with that setting's effort. The runner refuses to start if an Azure client's effort differs from its setting.

Provenance recorded for role S10 (also copied to `llm_provenance` in `runs.jsonl`):
- `reasoning_effort` and `max_completion_tokens` actually sent
- `finish_reason`, `output_tokens`, `reasoning_tokens`, `cached_input_tokens`, `latency_s`
- `attempts`: one entry per generation (the first run and the re-run, if any) with cap, finish reason and usage
- `http_attempts` and `http_log`: every HTTP request sent, including transport retries and the re-run, with status and finish reason. Paid calls are counted from this.

## Frozen retrieval candidates (`frozen_candidates.py`)
The tool's retrieval query depends only on the device name (`<device_name> biological evaluation ISO 10993`, `top_k=30`). To give every setting and repeat the same candidates:

1. `build_candidates(profiles, cache_dir=..., out_path=..., manifest_path=..., retriever_factory=...)` calls the real retriever once per distinct query and writes two write-once files:
   - the candidates file (git-ignored; it holds clause text)
   - a manifest with, per profile id, the number of candidates and one sha256 over clause id, doc, edition and the sha of the text (CRLF converted to LF). It holds no clause text and no device names.
2. `FrozenRetriever(candidates, manifest_path=..., profiles=...)` replays them and checks the manifest.

Rules:
- `cache_dir` is required. The Text2Cypher cache and the query-embedding cache must both sit directly in it. A folder under `data/runs/azure_openai/`, or with `s8` anywhere in its path, is refused. If the cache paths cannot be read from the retriever, the build is refused.
- Build and replay accept only `top_k=30`.
- A replay miss (unknown query or another `top_k`) raises an error. The tool swallows retriever errors, so the runner checks the retriever's `misses` and refuses to write `summary.json` if there is any.

## Cell tags (`tagger.py`)
Each (profile, item) cell gets one tag. The first matching check wins:

1. the item is a device-specific standard of the profile → `judgement`
2. the item is an always-rule part and a component has contact → `always_rule`
3. the profile has a feature the engine cannot express → `judgement`
4. the part has a matrix column, exactly one component has contact, its Attachment A row exists, and the label from that row alone equals the engine label → `matrix_settled`
5. anything else → `judgement`

Definitions:
- **always-rule parts**: `ISO 10993-1`, `-12`, `-17`, `-18`. Engine rule R08 makes them mandatory for every contacting component (`standards/engine.py`, `rule_08`).
- **features the engine cannot express**: any `intended_population` value other than `adult` (an empty list is not special), a novel or nano material, a missing exposure (assumed long-term).
- Parts with no matrix column that depend on flags (EO residuals, degradation, toxicokinetics and so on) are `judgement`.

Use: `build_tags(profiles, data)` and `write_tags(path, doc)` produce a `s10-cell-tags.v1` file; pass it to the runner with `--tags`. Every cell metric is then also reported per tag (`by_tag`), for primary and secondary. Without `--tags` there is no split. A scored cell with no tag is refused.

Limit: tags follow the engine's rule encoding. If the engine encodes a trigger wrongly, the tag is wrong in the same way.

## Metrics (`metrics.py`, pure functions)
A cell is one (profile, ISO part) decision of one run. Repeats are pooled. Undefined values (0/0) are `NaN`, written as `null` in JSON.

| metric | definition |
|---|---|
| under-call rate (**primary safety**) | gold = mandatory and final < mandatory, ÷ gold-mandatory cells |
| over-call rate | final > gold, ÷ gold non-mandatory cells |
| exact accuracy | final == gold, ÷ cells |
| macro-F1 | mean F1 over the labels present in gold or prediction |
| QW kappa | quadratic-weighted Cohen's kappa, N/A < recommended < mandatory |
| binary kappa | Cohen's kappa, mandatory vs not |
| LLM lift | metric(final) − metric(rules-only engine labels), paired by profile |
| upgrade precision / recall | upgrade = final > rule; warranted = gold > rule |
| blocked downgrades | LLM label < rule label (guard kept the rule); `omitted` = LLM skipped a rule part. Counted over all output parts; no gold needed |
| disagreement table | cells with LLM ≠ rule, split into rules right / LLM right / both wrong |
| stability (k repeats) | share of profiles with an identical label vector in every repeat; per-cell flip rate |
| 95% CI | percentile bootstrap, **resampling profiles** (all cells and repeats of a profile move together), seeded, `--n-boot`. Labelled `approximate (few profiles)` in `summary.json` |
| profiles with ≥ 1 under-call | count, n, rate: profiles with at least one under-called gold-mandatory cell in any repeat, over all scored profiles, with an **exact Clopper-Pearson 95% interval** (standard library only) |

Coverage handling:
- A gold item missing from the output is scored as `not_applicable` and counted.
- Output parts without gold are ignored and counted.

Citations:
- **Validity** reuses the guard: a cited id that was not retrieved is dropped and logged as `unretrieved_clause`.
- **Precision and recall** are judged by a human in `citation_review.csv`.

## Version locks
The runner refuses to start if either lock fails:
- **Rule data:** the 4 rule YAMLs must match `RULESET_FILES_SHA256` for `RULESET_VERSION`.
- **Decision/wording code:** `engine.py`, `guard.py` and `overclaim.py` must match `ENGINE_FILES_SHA256` for `standards.ENGINE_VERSION`.

Both live in `standards/ruleset.py` and are hashed after converting CRLF to LF. A change needs a version bump plus new shas.

## Known limits
- `--llm fake` only. The live client and retriever are injected by code.
- Retrieval uses only the device name, so two profiles with the same name get the same candidates.
- `AnthropicLLM` provenance reports `effort: low` whatever the request says. Use `effort_effective` from this harness.
- Output wording is linted by `standards/overclaim.py`. The guard rewrites over-claiming reasons and drops over-claiming LLM notes; labels never change.
