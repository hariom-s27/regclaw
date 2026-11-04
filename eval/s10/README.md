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
eval.s10.split_guard.seal_holdout([profiles, gold])
```
This writes `holdout_manifest.json`, the LF-normalised sha256 of each file.

The runner refuses `split: holdout` unless `--final-holdout` is given **and** the manifest exists and matches. It also refuses:
- a dev file that contains a holdout row
- `--final-holdout` on dev files

Dev needs no flag.

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
| 95% CI | percentile bootstrap, **resampling profiles** (all cells and repeats of a profile move together), seeded, `--n-boot` |

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
- `AnthropicLLM` provenance reports `effort: low` whatever the request says. Use `effort_effective` from this harness.
- Output wording is linted by `standards/overclaim.py`. The guard rewrites over-claiming reasons and drops over-claiming LLM notes; labels never change.
