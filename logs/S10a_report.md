# S10a report — prep for the S10 evaluation (status, code on fakes, profiles, seal)

Resume file: each Part appends its result and SELF-AUDIT here. Parts done so far: **A**, **B**, **C1**, **C2**, **D**.

Branch note: PR #17 merged after Part B (CI #91-#93 green); C2 and D continue on s10/profiles.

## PLAIN SUMMARY
1. S10a got everything ready to test the "which ISO 10993 parts apply" tool fairly, without spending money on any model call.
2. New code (output budget, run details, exact intervals, frozen search results, cell tags, one-time seal) was built and tested on fake data only.
3. 30 test device profiles were written, checked twice by Hariom, checked for copying, split 10 dev / 20 holdout, and the holdout was sealed.
4. No paid or network call was made, and no locked file (engine, guard, rules, prompt, corpus) was changed.
5. Next (S10b): write the gold answers, seal them in a second manifest, then run the evaluation.

## Results A–C2 (sources in each Part below)
| Part | Verdict | Key numbers | Commit / PR |
|---|---|---|---|
| A — status + evidence | PASS (Hariom) | baseline 1027 passed / 1 skipped · mcp 20 · obs 1; 4 S10 record files | none (read-only) |
| B — code on fakes | merged | 1173 passed / 1 skipped · mcp 20 · obs 1; locked-file diff empty | 8111afd, PR #17 (merge 775df42) |
| C1 — draft profiles | profiles OK | 30 rows validated; 3 per stratum S1–S8 + 6 traps; 21/24 matrix rows; 2 review rounds | git-ignored files only |
| C2 — checks, split, seal | PASS (Hariom) | checks a/b/c 30/0; dev 10 / holdout 20; 1215 passed / 1 skipped | 511c683 |
| D — final report | this section + Part D below | full suite in Part D | (this commit) |

---

## PART A — status + evidence (read-only) · 2026-11-13

### A1 git
- branch `main`, HEAD `250ce15`, `git status --porcelain` empty before this file was written (source: `git branch --show-current`, `git rev-parse --short HEAD`, `git status --porcelain`).

### A2 baseline tests
Command: `.\.venv\Scripts\python.exe tasks.py test`

| suite | passed | failed | skipped |
|---|---|---|---|
| mcp | 20 | 0 | 0 |
| obs (run summary) | 1 | 0 | 0 |
| tests | 1027 | 0 | 1 |

Matches Shyam's BEFORE output (1027 passed / 1 skipped / 0 failed · mcp 20 · obs 1). Source: command output, last line `1027 passed, 1 skipped in 157.42s`, exit code 0.

### A3 locks
- RULESET lock: `tests/test_s10_ruleset.py::test_stored_shas_match_current_rule_yamls` passed; `standards.ruleset.check_ruleset()` returned `312738f9b085…`.
- ENGINE lock: `tests/test_s10_ruleset.py::test_engine_shas_match_current_code_and_version` passed; `standards.ruleset.check_engine()` returned `9d1a7a01cbb6…`.
- Output of the narrow run: `2 passed, 23 deselected`.

### A4 token evidence (S10 live records; `s8` never listed or opened)
Four S10 record files. Fields printed by key whitelist only; source = each file's `provenance` block.

| file (first 8 chars) | folder | device_name | date (UTC) | model / version | effort | input | output | reasoning | cached | fallback stop_reason | llm_status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `9358309e` | `data/runs/azure_openai/s10` | Skin electrode | 2026-10-18 01:43 | gpt-5-mini / gpt-5-mini-2025-08-07 | high | 24,143 | **8,000** | not recorded | not recorded | `max_tokens` | **fallback** |
| `81d83a29` | `data/runs/azure_openai/s10` | Skin electrode | 2026-10-18 01:46 | same | high | 24,143 | 11,725 | not recorded | not recorded | none | ok |
| `c4a337a7` | `data/runs/s9/live/s10` | Knee X | 2026-11-13 01:06 | same | high | 47,377 | 20,943 | 17,856 | 0 | none | ok |
| `1a37504f` | `data/runs/s9/live/s10` | Knee X | 2026-11-13 01:13 | same | high | 47,377 | 18,043 | 15,040 | 0 | none | ok |

- **Old 8,000 fallback:** record `9358309e`. Output tokens are exactly 8,000, the guard event is `fallback` with `stop_reason: max_tokens`, and `llm_status` is `fallback`. At that time the request's `max_tokens=8000` (`standards/classifier.py:56`) was sent directly as the cap; the comment at `llm/azure_openai.py:100-102` records this. The next record 3 minutes later (`81d83a29`) used 11,725 output tokens and is `ok`, i.e. after the ×3 scale.
- **Headroom today:** the two S9 records used 20,943 and 18,043 of the 24,000 cap (87 % and 75 %), with 85 % and 83 % of output spent on reasoning.
- **Not in any record:** `finish_reason`, `latency_s`, `max_completion_tokens`, attempts. `standards/tool.py:50-54` copies only `provenance` and `usage` from the response. The only timing is the whole MCP call in `data/runs/s9/calls_ledger.jsonl`: `B_standards_kneeX` 190,395 ms (exit 1, paid_delta 3), `B_standards_kneeX_r2` 169,000 ms, `C_standards_kneeX` 144,347 ms.
- Other S10-related files under `data/runs/s9/`: `B_standards_kneeX.json` (0 bytes), `B_standards_kneeX_r2.json`, `C_standards_kneeX.json` (MCP responses, not opened) and their `.stderr.log` files.

### A5 code facts
1. **S10 cap per effort today** (`llm/azure_openai.py:103-105`; request `max_tokens=8000` from `standards/classifier.py:56`): minimal 8,000 · low 8,000 · medium 16,000 · high 24,000. Default effort for S10 is `high` (`llm/azure_openai.py:40-41`).
2. **Client timeout today:** none is set (`OpenAI(base_url, api_key, max_retries=0)` at `llm/azure_openai.py:46-50`; no `timeout` anywhere in `llm/`, `standards/`, `ner/`). The installed SDK default applies: openai 2.54.0, `Timeout(connect=5.0, read=600, write=600, pool=600)` (source: `openai.DEFAULT_TIMEOUT`). S10 has its own retry loop of up to 3 sends (`llm/azure_openai.py:58`).
3. **Where `llm_status` is set:** `standards/guard.py:33-43` (`ok` only when the response is an `LLMResponse`, `stop_reason == 'end_turn'` and the payload passes the output schema; otherwise `fallback`), returned at `guard.py:95`, copied to provenance at `standards/assemble.py:23`, read by the runner at `eval/s10/runner.py:117`.
4. **Caches on a repeated device_name:** the tool's query is `device_name + ' biological evaluation ISO 10993'` with `top_k=30` (`standards/tool.py:28`), so the same device_name gives the same question.
   - Text2Cypher cache: **hit**. Key fields (`retrieval/cypher_cache.py:32-42`): `qid` (`adhoc:` + first 8 of the question sha, because the tool passes no qid), `variant` (a), `query_text`, `query_text_sha`, `gen_index` (0), schema/prompt hashes, `provider`, `role`, `requested_model`, `effort`, `attempt_no`, `feedback_sha`. A hit needs `fresh=False` and a stored row with `reusable: true` (`hybrid_retriever.py:370`, `cypher_cache.py:56`).
   - Query-embedding cache: **hit**. Key fields (`retrieval/query_embeddings.py:83-85`): `text_sha`, `model`, `provider`, plus `identity_sha` when an identity manifest exists.
   - Neo4j and pgvector reads still run on every call; only the two paid calls are cached.
   - The default cache path is `data/runs/<provider>/s8/<family>/cypher_cache.jsonl` (`cypher_cache.py:19-23`), i.e. inside an `s8` folder. S10c must pass its own `cache_file`.
5. **Re-running `seal_holdout` with the gold file added:** the function overwrites. It writes `files[0].parent/holdout_manifest.json` (or `manifest_path`) with only the files passed in that call (`eval/s10/split_guard.py:30-38`). `load_eval_files(..., final_holdout=True)` checks profiles and gold against one manifest (`split_guard.py:80-84`), so both must be listed in the same manifest. Adding gold later therefore means either re-sealing all files in one call (re-hashes the two sealed files; needs an explicit decision) or sealing gold to a second manifest and merging the entry by hand.

Extra finding: `SettingLLM` writes the setting's effort into `request['output_config']['effort']` (`eval/s10/runner.py:70-71`), but `AzureOpenAIAdapter.map_request` ignores that field and sends `self.effort`, read from the env when the client is built (`llm/azure_openai.py:40, 107`). With Azure, `effort_effective` in runs.jsonl can differ from the effort actually sent unless the live client factory builds one client per setting with the matching env.

### A6 engine: how each part's rule label is set, and the S10-17 tagger
Part label = highest label among the part's endpoints (`engine.py:198`), then the highest across components (`engine.py:259-270`).

| part | endpoints | rules that set the label |
|---|---|---|
| ISO 10993-5, -10, -23, -6 | Cyto / Sens / Irr / Impl | R03 matrix row only |
| ISO 10993-4 | Hemo | R03 matrix row only (R06 adds notes, no label change) |
| ISO 10993-11 | AST, SubA, Chr, MMP, Systemic | R03 matrix row; R07 can raise MMP to mandatory (implant, labeled non-pyrogenic, or sterile with blood/CSF contact); Systemic is never set |
| ISO 10993-3 | Geno, Carc, Repro | R03 matrix row; R04 overrides Geno for circulating blood, duration A (extracorporeal flag); R05 sets Repro `recommended` unless intact skin |
| ISO 10993-1, -17, -18, -12 | Framework, TRA, Chemical, Sample | R08, mandatory for every contacting component; no matrix column |
| ISO 10993-7 | EO | R09: mandatory if EO-sterilised, else not_applicable; no matrix column |
| ISO 10993-9, -13, -14, -15 | Degradation* | R05 (site and material triggers); no matrix column |
| ISO 10993-16, ISO/TS -19, -20, ISO 11737-3 | Toxicokinetics, Physicochemical, Immune, Endotoxin | never set → not_applicable |
| device-specific standards | none | R14: mandatory if any contacting component (`engine.py:272-281`) |

Other inputs: duration class from R02 (missing exposure → C assumed); a contact combination absent from the matrix → union of all rows at that duration, marked unverified (`engine.py:89-93`); a non-contact component → all not_applicable (R01). The matrix has 24 rows = 8 (nature, site) pairs × A/B/C, with 11 endpoint columns (AST, Carc, Chr, Cyto, Geno, Hemo, Impl, Irr, MMP, Sens, SubA).

**Option 1 — trace-based.** Call `evaluate()` and tag a cell settled when its `rule_trace` holds only matrix rule ids. Rejected: traces list every rule that touched an endpoint even when the label did not change (R05 always touches Repro in part 3; R08 touches every profile), so it over-tags judgement and depends on trace bookkeeping.

**Option 2 — counterfactual, in `eval/s10/tagger.py` (my pick).** For each profile compute a matrix-only label per part straight from the matrix rows and the part map (duration via `engine.rule_02`, as `formats.derived_duration` already does), and compare it with the label from `engine.evaluate()` (import only, no change to locked files). A cell is `matrix_settled` only if all of these hold:
- the part has at least one endpoint that is a matrix column;
- there is exactly one contacting component, and its (nature, site, duration) row exists in the matrix;
- the matrix-only label equals the engine rule label;
- the item is not a device-specific standard.

Everything else is `judgement`. Main risk: the tag follows the engine's rule encoding; if the engine is wrong about a trigger, the tag is wrong the same way.

### A7 corpus: device-specific documents (names/ids only; source `CORPUS.md` inventory, `corpus/` holds code only)
- `fda-knee` — FDA knee joint prostheses guidance (2003): device-specific.
- `fda-nitinol` — FDA nitinol guidance (July 2021): specific to a material/technology class of devices.
- All others are general: `fda-iso10993-1`, `fda-sterility-510k`, `fda-chem-analysis`, `eu-mdr`, `imdrf-n47`, `in-ep`, `in-mdr`, `epa-63o`, `who-decon` (optional, disabled).

### A8 schema: traps and validation failure
Source: `standards/profile.py`, `eval/s10/formats.py`.

| trap | expressible? | how |
|---|---|---|
| contradictory input (implant + limited) | yes | `contact_nature: implant` with `hours_per_use ≤ 24`; the matrix has implant rows for A |
| neonate/infant population | yes | `intended_population: [neonate_infant]` |
| labeled non-pyrogenic surface device | yes | `labeled_non_pyrogenic: true` |
| minimal profile | yes | `device_name` + one component with `name`, `contact_nature`, `contact_site`, `exposure.pattern`, one material `name`, `sterilization`; missing hours → duration C |
| intact-skin device, novel material | yes | `materials[].novel: true` |
| **non-contact-only device** | **DeviceProfile.v1 yes, eval format no** | `contact_nature: non_contact` validates, but the row's `contact_category` enum has only surface / externally_communicating / implant (`formats.py:25, 34`) and must match a component (`formats.py:102-104`), so the row fails `check_profile_entry` |

Not expressible: any field outside the schema (`extra='forbid'`), explicit nulls, a supplied `derived` block.

**Validation failure in the runner:** `main()` calls `load_eval_files` → `validate_profile_set`, which raises `FormatError` before any run (`runner.py:261`, `split_guard.py:97`). Nothing catches it, so the CLI ends with a traceback and writes no output. If `run()` is called directly with a bad profile, `DeviceProfile.model_validate` raises inside the tool (`standards/tool.py:23`), `run_one` does not catch it, the run aborts, and `runs.jsonl` keeps the lines written so far.

### A9 .gitignore
`git check-ignore -v data/gold/s10/x` → `.gitignore:12:data/gold/	data/gold/s10/x`. Ignored.

---

## SELF-AUDIT — Part A

**1. Asked vs done**
- A1 git: done (`main`, `250ce15`, clean).
- A2 baseline tests: done (table A2).
- A3 locks: done (2 tests + 2 check functions).
- A4 token evidence: done (4 records; latency/finish_reason not recorded anywhere per record).
- A5 code facts: done (5 answers with file:line, 1 extra finding).
- A6 engine + tag options: done (2 options, pick = option 2).
- A7 corpus: done (`fda-knee`, `fda-nitinol`).
- A8 schema traps + validation failure: done.
- A9 .gitignore: done.

**2. Decisions I made myself**
- Read `standards/guard.py`, `standards/ruleset.py`, `standards/profile.py`, `standards/demo.py` and two rule YAMLs in addition to the listed files, read-only, because items 3, 5, 6 and 8 cannot be answered without them. Rejected: answering from the listed files alone.
- Printed `device_name` of the 4 smoke records (Knee X, Skin electrode) because C2 needs the S9 smoke inputs. Rejected: leaving it for C2.
- Printed the rule matrix's row keys and column names through a short inline script (no file created) rather than reading the 2,000-line YAML.

**3. Deviations**
- The plan's `-k "sha or lock or frozen"` filter was replaced by the two exact lock test names, as announced in the plan.
- Also read `data/runs/s9/calls_ledger.jsonl` (key names and short scalar fields of the S10 rows) to find latency; it was not named in the plan. Effect: none beyond the three timings reported.

**4. How I implemented it**
- Created `logs/S10a_report.md` (this file). No other file created or changed.
- Commands run: `git branch --show-current; git rev-parse --short HEAD; git status --porcelain; git check-ignore -v data/gold/s10/x` · `.\.venv\Scripts\python.exe tasks.py test` · `.\.venv\Scripts\python.exe -m pytest -q tests/test_s10_ruleset.py -k "stored_shas_match_current_rule_yamls or engine_shas_match_current_code_and_version"` · a directory listing of `data\runs\s9` and `data\runs\azure_openai\s10` · four inline `python -` scripts (record provenance whitelist, SDK timeout + device names, matrix row keys, ledger rows).

**5. Checks with real output**
- Tests: see table A2. Lock tests: 2 passed, 23 deselected.
- Record files: 4; matrix: 24 rows, 11 endpoint columns, 1 footnote kind (`extracorporeal`).

**6. Frozen items**
- `standards/engine.py`, `guard.py`, `overclaim.py`, the 4 YAMLs, `classifier.py` PROMPT: read or grepped only, not changed. `check_ruleset()` and `check_engine()` pass. `git status` shows only this report as new.

**7. Holdouts and sealed files**
- Not opened, printed, copied or hashed: the four sealed S8 files, `D:\s8_*`, any holdout set. `data/runs/azure_openai/s8` was not listed.

**8. Safety**
- No commit, no push, `.env` not opened, no keys printed, nothing written outside the repo by me (pytest's own temp folders are allowed by the OK'd plan), no AI attribution, no guard or hook block met.

**9. Paid/external calls**
- 0. Tests ran with `REGCLAW_STUB=1`.

**10. Not checked / assumed**
- Why `B_standards_kneeX` exited 1 after 190 s with 3 paid calls: a grep of its stderr log for error words found nothing (the log may be UTF-16); not investigated.
- That record `81d83a29` ran under the ×3 scale is inferred from 11,725 > 8,000 and the code comment, not from a recorded cap.
- `fda-knee` / `fda-nitinol` are classed from their titles in `CORPUS.md`, not from clause text.
- The 600 s timeout is the SDK constant; Azure's own gateway limit is unknown.

**11. Errors and retries**
- None.

**12. Risks for later steps + what I need from Shyam**
- Risk: the S9 runs used 75–87 % of the 24,000 cap with 47k input tokens; a profile with more candidates can truncate. Check: set `AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS` and watch `llm_detail` in S10c.
- Risk: Azure ignores the per-request effort (extra finding in A5). Check in Part B: a test that the provenance effort equals the setting's effort for the live factory, or a decision.
- Risk: the default Text2Cypher cache path is inside an `s8` folder. Check: S10c passes `cache_file`.
- Need from Shyam: answers to Q1–Q5 below and "OK plan" for Part B.

### Open questions (Part A)
- **Q1 tagger option:** OK option 2 (counterfactual)?
- **Q2 parts with no matrix column** (ISO 10993-1, -12, -17, -18, -7, -9, -13 to -16, -19, -20, 11737-3): by the literal definition they are `judgement`, which puts 4 always-mandatory easy cells per profile into the judgement bucket. Recommended: keep the literal definition (`judgement`). Alternative: tag them `matrix_settled`.
- **Q3 profile-level features the engine cannot turn into labels** (neonate/pregnant population, novel or nano material, missing exposure assumed C): recommended = these make every cell of that profile `judgement`. Alternative: per-cell only (they would stay `matrix_settled`, since the engine label does not move).
- **Q4 non-contact-only trap:** the eval format cannot hold it. Options: (a) drop it and use another trap (recommended: a two-component device where one component is non-contact); (b) change `eval/s10/formats.py` to allow `non_contact/...` categories (not a locked file, but a format change).
- **Q5 S10-19 naming:** which string goes into `device_specific_standards` for the corpus guidances — the doc code (`fda-knee`, `fda-nitinol`) or a title? Recommended: the doc code.
- **Q6 Azure effort:** should Part B add anything for the effort mismatch, or is it handled in S10c by one client per setting?

Commit command (for reference; **do not run now**):
`git commit -m "S10a: Part A status and evidence report" -m "logs/S10a_report.md: git state, baseline tests, lock checks, S10 token evidence, engine rule map and tagger options, corpus device-specific docs, schema trap check."`

### Answers received after Part A (Shyam)
- Q1 option 2 (counterfactual) plus a self-check test. Q2 three tags: `matrix_settled`, `always_rule`, `judgement`. Q3 engine-inexpressible features make the profile's cells `judgement`. Q4 replace the non-contact-only trap with a two-component device, one component non_contact. Q5 `device_specific_standards: ["fda-knee"]` on 1–2 knee-implant profiles; nitinol only as a material in 1 profile; no device_name near "Knee X" or "Skin electrode". Q6 fix in Part B as S10-25.
- Added: S10-26 (explicit cache folder), S10-27 (write-once manifests, `--holdout-manifest`), pre-registered cap 64000.

---

## PART B — code on fakes (branch `s10/prep`) · 2026-11-13

### What was built
| Decision | Where | What |
|---|---|---|
| S10-09 v2 (a) cap | `llm/azure_openai.py` `s10_cap`, `map_request` | `AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS` (int 1..128000) is the cap for role S10 at every effort; unset = 8,000 × 1/1/2/3 as before |
| (b) timeout | `llm/azure_openai.py` `s10_timeout`, `__init__` | `AZURE_OPENAI_S10_TIMEOUT_S` (float > 0) passed as `timeout=` to the SDK client for role S10 only |
| (c) provenance | `llm/azure_openai.py` `_complete` | S10 provenance gains `max_completion_tokens`, `finish_reason`, `output_tokens`, `reasoning_tokens`, `cached_input_tokens`, `latency_s`, `attempts`, `http_attempts`, `http_log`; `reasoning_effort` comes from the mapped request |
| (d) re-run | `llm/azure_openai.py` `_complete` | on `finish_reason == length` with the env cap set: one re-run at `min(2 × cap, 128000)`; none when the cap is already 128000 |
| HTTP attempts | `llm/azure_openai.py` `_send` | one `http_log` row per HTTP request (status, finish_reason, cap), including transport retries and the re-run; `last_http_log` stays readable when the call raises |
| S10-25 effort | `llm/azure_openai.py` `__init__(effort=None)`; `eval/s10/runner.py` `azure_client_factory`, `run` | explicit effort argument; one client per setting; the runner refuses an Azure client whose effort differs from its setting |
| (e) `llm_detail` | `eval/s10/runner.py` `llm_detail`, `SettingLLM`, `run_one` | per-run value in {ok, truncated, refusal, parse_error, error, unavailable}; `guard.py` untouched |
| (f) primary / secondary | `eval/s10/runner.py` `cell_metrics`, `score_setting` | `llm_detail` counts per setting; primary = all runs (top level), secondary = `secondary_ok_only`; each labelled by `metrics_scope` |
| S10-18 | `eval/s10/metrics.py` `clopper_pearson`, `profiles_with_under_call`; `BOOTSTRAP_CI_LABEL` | exact interval, stdlib only; bootstrap labelled "approximate (few profiles)" |
| S10-17 tagger | `eval/s10/tagger.py` | three tags, approved order of checks, tags file `s10-cell-tags.v1`; runner `--tags` gives `by_tag` for every cell metric |
| S10-16 / S10-26 | `eval/s10/frozen_candidates.py` | `build_candidates`, `FrozenRetriever`, manifest; both caches must sit in an explicit cache folder; `data/runs/azure_openai/` and any path with "s8" refused; only `top_k=30` |
| S10-27 | `eval/s10/split_guard.py`, runner CLI | `seal_holdout(files, manifest_path)` explicit and write-once; `--holdout-manifest` (alias `--manifest`) repeatable; each holdout file in exactly one manifest |

**always_rule parts** (Q2): `ISO 10993-1` (Framework), `ISO 10993-17` (TRA), `ISO 10993-18` (Chemical), `ISO 10993-12` (Sample). Source: `standards/engine.py:166-167` sets Framework, TRA and Chemical to mandatory for every contacting component; `standards/engine.py:169-172` makes Sample mandatory because Chemical is always mandatory. Part mapping: `standards/data/endpoint_to_part.v1.yaml:3-7, 49-63`.

### Test results (source: `.\.venv\Scripts\python.exe tasks.py test`)
| suite | before | new | total | failed | skipped |
|---|---|---|---|---|---|
| tests | 1027 | 146 | 1173 | 0 | 1 |
| mcp | 20 | 0 | 20 | 0 | 0 |
| obs (run summary) | 1 | 0 | 1 | 0 | 0 |

Last line of the final run: `1173 passed, 1 skipped in 150.54s`, exit code 0. The suite was run twice: once after the code was complete, once more after the last edit (miss check moved into `run()`); both gave the same numbers.

New tests by file (source: narrow pytest runs):
- `tests/test_s10a_azure_budget.py`, `tests/test_s10a_runner_detail.py`, `tests/test_s10a_exact_interval.py`: 59 together.
- `tests/test_s10a_frozen_candidates.py`, `tests/test_s10a_tagger.py`: 75 together.
- `tests/test_s10_holdout_guard.py`: 12 added (6 tests, each run under native and simulated Windows text mode).

Required tests, where each lives:
- cap set/unset per effort, same cap at every effort when set: `test_cap_unset_keeps_the_scaled_budget`, `test_cap_set_is_the_same_cap_at_every_effort`.
- timeout only for S10 when set: `test_timeout_is_passed_only_for_s10_and_only_when_set`.
- truncation → exactly one re-run at 2x cap (≤ 128000), both attempts recorded: `test_truncation_gives_exactly_one_rerun_at_twice_the_cap`, `test_second_truncation_is_not_rerun_again`, `test_rerun_cap_never_exceeds_128000`, `test_no_rerun_when_the_cap_is_already_128000`.
- HTTP attempts with a 429-then-200 client: `test_http_attempts_count_a_429_then_200`, `test_http_attempts_add_retries_and_the_truncation_rerun`.
- mapped request carries each setting's effort: `test_runner_sends_each_settings_effort_in_the_mapped_request`.
- `llm_detail` for ok / length / refusal / empty data / exception: `test_llm_detail_mapping`, `test_each_bad_run_gets_its_llm_detail_and_guard_status_is_unchanged`.
- primary vs secondary: `test_primary_uses_all_runs_and_secondary_only_ok_runs`.
- Clopper-Pearson known values: `test_known_values_to_four_decimals` (0/16 → 0.2059; 1/16 → [0.0016, 0.3023]; 0/8 → 0.3694).
- FrozenRetriever replay + manifest sha stable under CRLF: `test_replay_through_the_tool_hits_every_query_and_feeds_the_candidates`, `test_list_sha_is_stable_under_crlf_and_sensitive_to_content`, `test_crlf_copy_of_the_candidates_file_still_matches_the_manifest`.
- both cache paths and the s8 / azure_openai refusal for each: `test_forbidden_cache_folder_is_refused`, `test_embedding_cache_in_a_forbidden_folder_is_refused`, `test_cypher_cache_in_a_forbidden_folder_is_refused`, `test_embedding_cache_outside_cache_dir_is_refused`, `test_retriever_without_readable_cache_paths_is_refused`.
- top_k refusal at build and replay, miss blocks the summary: `test_build_refuses_a_different_top_k`, `test_wrong_top_k_or_unknown_query_is_a_miss`, `test_a_miss_blocks_the_summary`.
- tagger on hand-made fakes, one settled and one judgement cell per rule type, plus the self-check: `tests/test_s10a_tagger.py` (`test_self_check_counterfactual_equals_engine_without_triggers`).
- write-once manifests and repeatable `--holdout-manifest`: `test_seal_refuses_to_overwrite_a_manifest`, `test_gold_sealed_later_in_a_second_manifest`, `test_file_listed_in_two_manifests_is_refused`, `test_cli_takes_repeatable_holdout_manifest`.
- no regression: all 1027 earlier tests still pass; the golden S10 request sha in `tests/test_s8_runs.py` is unchanged with the envs unset.

### Locked files
- `git diff --stat main -- standards/engine.py standards/guard.py standards/overclaim.py standards/data/ standards/classifier.py` → empty output.
- `git status --porcelain -- standards llm/provider.py eval/s10/formats.py` → empty output.
- Lock tests: `test_stored_shas_match_current_rule_yamls` and `test_engine_shas_match_current_code_and_version` → `2 passed, 23 deselected`.

### Diff stat (source: `git diff --stat main`, `git status --porcelain`)
```
 env.example                     |   6 ++
 eval/s10/README.md              |  85 ++++++++--
 eval/s10/metrics.py             |  39 ++++-
 eval/s10/runner.py              | 146 +++++++++++++---
 eval/s10/split_guard.py         |  60 ++++---
 llm/azure_openai.py             | 115 ++++++++++---
 tests/test_s10_holdout_guard.py |  61 ++++++-
 7 files changed, 446 insertions(+), 66 deletions(-)
```
New (untracked): `eval/s10/frozen_candidates.py`, `eval/s10/tagger.py`, `tests/fixtures/s10_eval/fake_tags.json`, `tests/test_s10a_azure_budget.py`, `tests/test_s10a_exact_interval.py`, `tests/test_s10a_frozen_candidates.py`, `tests/test_s10a_runner_detail.py`, `tests/test_s10a_tagger.py`, `logs/S10a_report.md`.

---

## SELF-AUDIT — Part B

**1. Asked vs done**
- B1 `llm/azure_openai.py` (a)–(d), defaults unchanged when unset: done (golden request sha test passes).
- B2 runner (e)–(f), S10-18, bootstrap label, tag split: done.
- B3 FrozenRetriever + build + manifest (S10-16), tagger (S10-17): done, on fakes only.
- B4 README + `env.example`: done; no links to `specs/` or `data/`. `.env.example` not opened.
- B5 tests on fakes: done (list above).
- B6 full suite + locks + byte-identical locked files: done.
- Additions 1–6 (both caches routed, exact query/top_k, HTTP attempts, tagger order, skip at 128000, report in the commit): done.
- S10-25, S10-26, S10-27, cap 64000 documented: done.

**2. Decisions I made myself**
- `usage` of the response is the final attempt's usage; the first attempt's usage is kept in `provenance.attempts`. Rejected: summing both into `usage`, which would misstate what the final answer cost on its own. S10c must add up `attempts` for cost.
- A response with no stop reason maps to `llm_detail = unavailable`; the plan table said "any other stop reason → error". Rejected `error` because nothing failed in the client. Unexpected stop reasons (e.g. `tool_calls`) stay `error`.
- The effort-mismatch refusal applies only to clients with `provider == 'azure_openai'`, because other clients take the effort from the request.
- The frozen-candidate miss check lives in `run()`, not only in the CLI, so a direct caller cannot skip it.
- The forbidden-folder check also applies to `out_path` and to both resolved cache paths, and each cache must sit directly in `cache_dir`.
- The candidates file and its manifest are write-once; the candidates file is keyed by the exact query string.
- A profile on another framework than FDA 2023 gets no `matrix_settled` cell.
- `by_tag` keeps the disagreement counts but not the per-cell rows (the rows stay in the un-split block).
- Run-level counts (`llm_status`, `llm_detail`, over-claim hits, wording events, citation validity) are not split by tag: they are counted per run, not per cell.
- No CLI was added for the tagger or the candidate build; S10b/S10c call the functions.

**3. Deviations**
- `llm_detail = unavailable` for a missing stop reason (see 2). Effect: none on `ok`; affects only how a rare failure is named.
- The trace (Langfuse) usage of a re-run call records the final attempt only. Effect: trace cost is low by the first attempt; provenance holds both.
- Otherwise no deviations from the OK'd plan.

**4. How I implemented it**
- `llm/azure_openai.py`: `s10_cap`, `s10_timeout`, `__init__(effort=)`, `_send(http_log)`, `map_request(cap=)`, re-run loop and S10 provenance in `_complete`.
- `eval/s10/metrics.py`: `clopper_pearson`, `profiles_with_under_call`, `BOOTSTRAP_CI_LABEL`.
- `eval/s10/runner.py`: `llm_detail`, `azure_client_factory`, `cell_metrics`, `score_setting`, `--tags`, `--holdout-manifest`, effort check and miss check in `run`.
- `eval/s10/split_guard.py`: `seal_holdout(files, manifest_path)` write-once; `verify_holdout` over several manifests.
- `eval/s10/tagger.py` (new): `tag_profile`, `counterfactual_labels`, `inexpressible_features`, `build_tags`, `write_tags`, `load_tags`.
- `eval/s10/frozen_candidates.py` (new): `build_candidates`, `FrozenRetriever`, `list_sha`, `check_folder`.
- `eval/s10/README.md`, `env.example`: docs.
- Tests: 5 new files, 1 fixture, `tests/test_s10_holdout_guard.py` extended.
- Commands: `git switch -c s10/prep` · narrow `python -m pytest -q` runs per file · `.\.venv\Scripts\python.exe tasks.py test` (twice) · `git diff --stat main -- <locked files>` · `git diff --stat main` · `git status --porcelain`. Edits to existing files were applied with inline `python -` scripts (exact-match replace, no scratch file).

**5. Checks with real output**
- tests 1027 → 1173 passed, 0 failed, 1 skipped; mcp 20; obs 1.
- Locked diff: empty. Lock tests: 2 passed.

**6. Frozen items**
- `standards/engine.py`, `guard.py`, `overclaim.py`, `standards/data/*.yaml`, `standards/classifier.py`: unchanged (empty diff vs main); `check_ruleset` / `check_engine` tests pass. Corpus and freeze manifests not touched.

**7. Holdouts and sealed files**
- None opened, printed, copied or hashed. No real profile exists yet. No tags or candidates were computed for real profiles.

**8. Safety**
- No commit, no push, `.env` and `.env.example` not opened, no keys printed, nothing written outside the repo by me (pytest's own temp folders only), no AI attribution, no guard or hook block met.

**9. Paid/external calls**
- 0. All new tests use fake SDK objects, fake retrievers and `tmp_path`.

**10. Not checked / assumed**
- The `timeout=` argument and `finish_reason` values were tested against fake SDK objects, not against Azure.
- The real `HybridRetriever` is assumed to expose `cypher_cache.path` and `query_embeddings.cache.path` (read in the code, `hybrid_retriever.py:344-348`); if that changes, the build refuses rather than writes elsewhere.
- The "s8" check is on the full resolved path; a repo moved under a folder with "s8" in its name would make every build refuse.
- Working-copy files I edited now have LF line endings; git reports it will convert on checkout. The lock shas are LF-normalised, and none of the locked files was edited.

**11. Errors and retries**
- `tests/test_s10_holdout_guard.py` failed once with `TypeError: seal_holdout() missing 1 required positional argument` — expected from S10-27; the test helper now passes the path.
- In my own new test, the tag keys were compared in tuple order although `summary.json` sorts keys, and the fake client did not fix the FAKE-3 under-call; both corrected before the first run of that file.

**12. Risks for later steps + what I need from Shyam**
- C2: the seal call is now `seal_holdout([holdout_profiles.yaml, profiles_draft.yaml], manifest_path)`; I will propose the manifest path in the C2 plan.
- S10c: sum `attempts[].usage` for cost; count paid calls from `http_attempts`; pass `cache_dir`; set the cap env to 64000.
- S10c: a truncated run after the re-run is scored at the rule floor in primary and dropped from secondary; check `llm_detail` counts before reading metrics.
- Need: review of this Part, the commit, and "OK plan" for Part C1 after I write that plan.

---

## PART C1 - draft profiles · 2026-11-13 (branch `s10/profiles`)

Naming note: from this Part on, new text names the reviewer and author of record as Hariom.

### Answers received before C1 (Hariom)
- Strata sheet stays unsealed, with only stratum/trap descriptions and the id -> stratum map.
- 2 profiles carry `fda-knee` (one S5, one S6 on the implant category). 1 paediatric profile inside a stratum plus the neonate trap; all other profiles have an empty or adult population.
- Stratum purity: ethylene oxide only in S6; absorbable / in-situ polymerising / nano only in S7; repeat use or multi-component only in S8 (the two-component trap excepted).
- Id shuffle seed `S10-ids-v1` approved.

### Files written (both git-ignored, `.gitignore:12`)
- `data/gold/s10/profiles_draft.yaml`: 30 rows, format `s10-eval-profiles.v1`, every row `split: dev` as a placeholder.
- `data/gold/s10/strata_sheet.md`: descriptions and the id -> stratum map only.

### Id assignment
Slots in the fixed order S1-1, S1-2, S1-3, ... S8-3, trap-1 ... trap-6; ids 1..30 shuffled with `random.Random('S10-ids-v1')`; slot i gets the i-th shuffled id. Rows are stored in id order.

### Validation output (source: inline script calling `eval.s10.formats.validate_profile_set`)
```
validation: OK, 30 rows validated
rows: 30
per stratum: {'S1': 3, 'S2': 3, 'S3': 3, 'S4': 3, 'S5': 3, 'S6': 3, 'S7': 3, 'S8': 3, 'trap': 6}
ids P-001..P-030 unique: True
rows with fda-knee: 2
banned-word hits in device_name/comment: 0
distinct (contact_category, duration) pairs: 21 of 24 matrix rows
```
The validation passed on the first run; no row needed a fix. The three matrix rows not covered are implant/tissue-bone at prolonged duration and implant/blood at limited and prolonged duration.

## SELF-AUDIT - Part C1

**1. Asked vs done**
- C1.1 strata sheet: done (`data/gold/s10/strata_sheet.md`).
- C1.2 draft with 30 shuffled ids, stratum, contact_category, duration, flags, comment, full DeviceProfile.v1: done.
- C1.3 validate every row, report counts, no content printed: done (output above).
- Additions (purity, coverage count, seed recorded, "Hariom"): done.
- Review loop ("profiles OK" or notes): waiting.

**2. Decisions I made myself**
- Which device type fills each slot, and its materials, exposure hours, sterilisation, markets and population. Hariom reviews and edits every row.
- The paediatric profile sits in S2. Rejected S5, to keep the long-term implant rows on adult populations.
- The nickel-titanium material sits in an S4 profile.
- The two-component trap gives its non-contact component a non-polymer material class, so the derived skin-material flag is not set by a component that has no contact.
- The in-situ polymerising profile carries the format's `absorbable` flag, because the format has one flag for both properties (`eval/s10/formats.py:67-68`).
- The banned-word list in my pre-check also matches the British spellings (sensitis-, haemocompat-).

**3. Deviations**
- No deviations from the OK'd plan.

**4. How I implemented it**
- `New-Item -ItemType Directory data\gold\s10`; one inline script for the seeded id shuffle (printed id -> slot only); both files written by hand; one inline validation script (output above); `git status --porcelain`.

**5. Checks with real output**
- 30 rows valid; 3 per stratum, 6 traps; 2 `fda-knee` rows; 0 banned-word hits; 21 of 24 matrix rows covered. `git status --porcelain` shows only `logs/S10a_report.md` modified.

**6. Frozen items**
- No code or rule file touched in this Part. `git status` shows no change under `standards/`.

**7. Holdouts and sealed files**
- No sealed file opened, printed, copied or hashed. The draft is not sealed yet; it is sealed in C2.

**8. Safety**
- No commit, no push, `.env` not opened, no keys, nothing written outside the repo, no profile content printed in chat or in this report, no AI attribution, no guard or hook block met.

**9. Paid/external calls**
- 0. The engine's `evaluate`, the tool, the classifier, the tagger and every LLM were not run on the profiles. Only the format validator ran (it uses the engine's duration rule, as allowed).

**10. Not checked / assumed**
- Clinical realism of each row (materials, sterilisation method, exposure hours) comes from general knowledge and is not checked against a source. Because ethylene oxide is reserved for S6, a few devices use another method than is most common for that device type.
- Near-match of device names against the demo names, the test fixtures and the S9 smoke inputs is checked by code only in C2; I avoided them by hand here.
- The exposure hours for long-term devices are nominal values chosen to land in the intended duration class.

**11. Errors and retries**
- None.

**12. Risks for later steps + what I need from Hariom**
- Edits to a row can change its derived duration or flags; I re-validate after every round of notes.
- The paediatric profile, the neonate trap, the nano profile, the novel-material trap and the minimal trap will have no `matrix_settled` cell under the approved tagger order.
- Need: "profiles OK", or notes to apply exactly.

### C1 review round 1 (Hariom's notes, applied exactly) · 2026-11-13
Hariom's own check of the draft found nothing mechanically wrong and gave three notes:
- P-014: comment reworded as given; duration stays A.
- P-015: comment reworded as given; no field change.
- P-029: `sterilization` changed from steam to gamma.

Re-validation output (source: the same inline script, plus one check per note):
```
validation: OK, 30 rows validated
rows: 30
per stratum: {'S1': 3, 'S2': 3, 'S3': 3, 'S4': 3, 'S5': 3, 'S6': 3, 'S7': 3, 'S8': 3, 'trap': 6}
rows with fda-knee: 2
banned-word hits in device_name/comment: 0
distinct (contact_category, duration) pairs: 21 of 24 matrix rows
note checks: P-014 comment applied: True | duration A
             P-015 comment applied: True
             P-029 sterilization: gamma
             ethylene_oxide outside S6: 0
             steam rows now: 3
```
SELF-AUDIT changes for this round: three edits to `data/gold/s10/profiles_draft.yaml`, nothing else touched; no deviation; no error; 0 paid calls; no sealed file touched; all other headings as in the C1 SELF-AUDIT above. Waiting for "profiles OK".

### C1 review round 2 (P-015 duration check) and "profiles OK"
Hariom asked for one check before the OK: P-015 declared duration and engine-derived duration must both be C.
Output of the inline check (`eval.s10.formats.derived_duration` on the draft row): `P-015 declared: C | derived: C`.
No edit was needed. Hariom's condition is met, so the draft has "profiles OK" (2026-11-13). Next: Part C2 plan, then STOP for "OK plan".

## Part C2 — contamination checks, split, seal, manifest (2026-11-13)
Plan OK'd by Hariom with answers Q1–Q4 and additions 1–5 (seal approved).

**Code:** `eval/s10/profile_checks.py` (checks a/b/c, seeded split, write-once CLI, seal call) and `tests/test_s10a_profile_checks.py` (42 tests on FAKE rows only; source: `pytest tests/test_s10a_profile_checks.py -q` → `42 passed`).

**Python version (the seeded split depends on it):** 3.11.17 (source: `.venv\Scripts\python.exe --version`; also stored in `profiles_v1.json`).

**Dry run** (`python -m eval.s10.profile_checks data\gold\s10\profiles_draft.yaml --dry-run`, exit 0, nothing written):
```
validation: OK, 30 rows
reference device names: 10
check (a): pass 30 / fail 0
check (b): pass 30 / fail 0
check (c): pass 30 / fail 0
info (not failures): component_names_in_prompt = 1, material_names_in_prompt = 0
split        S1   S2   S3   S4   S5   S6   S7   S8 trap  total
dev           1    1    1    1    1    1    1    1    2     10
holdout       2    2    2    2    2    2    2    2    4     20
```

**Write + seal** (`... --write --seal data\gold\s10\holdout_manifest.json --manifest-out eval\s10\manifests\profiles_v1.json`, exit 0). Same check and count lines as the dry run, then:
```
seal manifest: data\gold\s10\holdout_manifest.json
sha256 (LF) dev_profiles.yaml: e03a043f717b0a0d0458e3c77a30841df840fcc5230d523a1cc993a61979e24f
sha256 (LF) holdout_profiles.yaml: 18053020afcf33c5eea08e18852292d74c8c4f0263e5800d28e1d3b420063a36
sha256 (LF) profiles_draft.yaml: 791058c326f2ccc53020f4746df90bbce2e799b7ac23464c2ddc9ea35730f743
python: 3.11.17 | seed: S10-split-v1 | date: 2026-11-13
```
Sealed (never to be opened, printed or re-hashed again): `data\gold\s10\holdout_profiles.yaml`, `data\gold\s10\profiles_draft.yaml`. Both are listed in `data\gold\s10\holdout_manifest.json` (write-once). S10b seals the gold into a second manifest.
Committed manifest: `eval/s10/manifests/profiles_v1.json` (file names, LF sha256, rows, sealed yes/no, counts per split × stratum, seed, date, Python version; no profile content).

**Tests** (source: background run output, exit 0):
- `pytest tests -q -k "s10"` → `338 passed, 878 deselected`
- `tasks.py test` → tests `1215 passed, 1 skipped` (= 1173 Part B baseline + 42 new) · mcp `20 passed` · obs `1 passed`
- `git diff --stat main -- standards` → empty (no frozen file changed)
- `git status --porcelain` → ` M logs/S10a_report.md`, `?? eval/s10/manifests/`, `?? eval/s10/profile_checks.py`, `?? tests/test_s10a_profile_checks.py`

### SELF-AUDIT (Part C2)
1. **Asked vs done:** (1) checks + test → done, 30/0 on a, b, c; (2) split with seed `S10-split-v1`, dev 10 / holdout 20, both validated with `validate_profile_set` → done; (3) seal [holdout, draft] in an explicit write-once manifest → done; (4) `profiles_v1.json` → done; S10 tests + full suite → see Tests.
2. **Decisions I made myself:** near-match = equal, or difflib ratio ≥ 0.85, or whole-word containment only when the shorter name has two or more words (so the test literal "test" does not flag every name containing that word; a whole-name "Test" still fails); reference names = 4 demo + 2 smoke + test literals (`device_name=` in tests/*.py and fixture profile files) → 10 distinct; the CLI also refuses to write when the counts differ from the expected table; the dev sha is hashed once by the script, while the sealed shas are taken from the seal manifest it has just written (no second hash).
3. **Deviations:** no deviations from the OK'd plan and its additions.
4. **How I implemented it:** `normalise`, `reference_names`, `near_equal`, `check_rows`, `split_ids`, `split_docs`, `count_table`, `prepare`, `write_and_seal`, `main`; every output file is opened with mode `x` (never overwritten); `--write` refuses to start if any of the 4 outputs exists (exit 4); a failed check or wrong counts mean exit 2 with nothing written; an error after writing starts gives exit 3, no seal, no deletion, and only the error type is printed. Commands: as listed above.
5. **Checks with real output:** the blocks above; tests: see Tests.
6. **Frozen items:** none changed (`standards/` untouched; the classifier PROMPT is imported as a string and never called).
7. **Holdouts and sealed files:** both files were hashed once by `seal_holdout` at seal time (allowed). They were not opened, printed or re-hashed afterwards, and `verify_holdout` was not run on them. No other sealed file was touched.
8. **Safety:** no profile content printed (ids, counts and shas only); `.env` not opened; nothing written outside the repo; no deletion; no commit or push; no guard or hook block.
9. **Paid/external calls:** 0. The engine's evaluate, the tool, the classifier, the tagger and every LLM were not run on the profiles.
10. **Not checked / assumed:** the near-match threshold of 0.85 is a heuristic, so a paraphrase with different words would pass check (a). Check (c) is a substring test only. The 1 component-name hit in the PROMPT is info only by Hariom's Q2 rule and was not inspected. `random.Random(str).sample` is assumed stable for Python 3.11.x.
11. **Errors and retries:** none (the narrow test passed on the first run).
12. **Risks for later steps + what I need from Hariom:** `strata_sheet.md` is unsealed and maps holdout ids to strata (accepted in C1 Q1). Any change to a sealed file now breaks `verify_holdout` and needs a versioned amendment. I need Hariom's review and commit, then "OK" for the Part D plan.

## PART D — final report · 2026-11-13
Plan OK'd by Hariom (Q1 (a), Q2 yes, additions 1–2). Older lines that say "Shyam" are left unchanged on purpose; a separate hygiene prompt renames them.

### Where it fails (limits of the S10 evaluation set-up)
1. **Retrieval uses only `device_name`.** The tool's query is `device_name + ' biological evaluation ISO 10993'` with `top_k=30` (source: `standards/tool.py`; `eval/s10/frozen_candidates.py::tool_query`, `TOP_K`). Contact, materials, duration and population never reach retrieval, so two profiles with the same name get the same candidates.
2. **Tags depend on the engine's rule encoding.** `matrix_settled` and `always_rule` are computed from the locked engine and YAMLs (`eval/s10/tagger.py`). If a rule is encoded wrongly, its cells are mis-tagged and the per-tag metrics are biased.
3. **How the profiles were made.** Profiles drafted by an AI agent, then reviewed and edited by Hariom (2 review rounds); not written independently by a domain expert. Wording approved by Hariom, 2026-11-13. This is a method limitation, not an authorship credit.
4. **Shared assumptions with a judge.** The drafting agent's model family is the same as gold judge A's planned model family (S10b), so they may share assumptions. S10b will report a leave-one-judge-out sensitivity row.
5. **The ISO-2025 flag is off.** Eval profiles must use `framework: FDA_2023_ATTACHMENT_A` (`eval/s10/formats.py:99`), so the ISO 10993-1:2025 anchor rows (`standards/ruleset.py:28`) and the 2025 changes are not tested: genotoxicity for prolonged contact and the MMP rule.
6. **Matrix coverage 21 of 24 rows.** The 3 missing rows are all implant rows: implant/tissue_bone B, implant/blood A, implant/blood B (source: C1 validation output).
7. **One component name appears in the classifier PROMPT.** Counted as info only under Hariom's C2 Q2 rule and not inspected (source: C2 output `component_names_in_prompt = 1`).
8. **Small n.** 20 holdout profiles, so intervals are wide; the bootstrap is labelled "approximate (few profiles)" and `profiles_with_under_call` uses an exact Clopper-Pearson interval (`eval/s10/metrics.py`).
9. **Check (a) is a heuristic.** difflib ratio ≥ 0.85 plus whole-word containment (2+ words); a paraphrase with different words would pass. Check (c) is a plain substring test.
10. **`strata_sheet.md` is not sealed** and maps holdout ids to strata (accepted in C1 Q1).
11. **The seeded split assumes Python 3.11.x** behaviour of `random.Random(str).sample` (run on 3.11.17).

### Tests (Part D)
Source: `.\.venv\Scripts\python.exe tasks.py test` (exit 0), then git.
- tests `1215 passed, 1 skipped` · mcp `20 passed` · obs `1 passed`
- `git diff --stat main -- standards` → empty
- `git status --porcelain` → ` M logs/S10a_report.md` only

## SELF-AUDIT — Part D
1. **Asked vs done:** PLAIN SUMMARY → done; Results A–C2 table → done; "where it fails" with all 6 named items and addition 1 → done (11 items); full suite → see Tests; header line updated; "Shyam" lines left unchanged (addition 2).
2. **Decisions I made myself:** added items 8–11 (limits already recorded in C1/C2, as listed in the OK'd plan); the summary table cites numbers already in this file.
3. **Deviations:** none.
4. **How I implemented it:** one Edit (header line + PLAIN SUMMARY + results table) and one append of this section to `logs/S10a_report.md`; commands: `tasks.py test`, `git diff --stat main -- standards`, `git status --porcelain`.
5. **Checks with real output:** see Tests.
6. **Frozen items:** none changed (see the `standards` diff in Tests).
7. **Holdouts and sealed files:** none opened, printed, copied or hashed in Part D; no file under `data/gold/s10/` was read.
8. **Safety:** no `.env`; nothing written outside the repo; no deletion; no commit or push; no guard or hook block; no profile content in this report.
9. **Paid/external calls:** 0.
10. **Not checked / assumed:** the "where it fails" list is from the evidence in this file and the code named in it; clinical realism of the profiles was not checked by an independent expert (item 3).
11. **Errors and retries:** none.
12. **Risks for later steps + what I need from Hariom:** S10b needs the gold sealed in a second manifest (write-once) and the leave-one-judge-out row (item 4); a rename of old "Shyam" lines must not touch sealed files. I need Hariom's review and commit.
