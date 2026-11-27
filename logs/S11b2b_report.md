# S11b2b Offline Metrics, Ragas Inputs, and Tree Audit Report

Date: 2026-11-27
Branch: `s11b2b/metrics-audit`
Commits: Part B `3eb2817` · Part C `aa2aa91` · Part D `2a93dbc` · Part E `logs/S11b2b_report.md` (pending)
Schemas / Rules: `s11-metrics-v1` · `s11-tree-audit-v1` · `s11-tree-instrument-v1`

---

## PLAIN SUMMARY

- Part B (`3eb2817`) added offline retrieval metrics (`R` and `C`), strict citation checks (`C1`–`C5`), status/`na_reason` rules, and `44`-key write-once metric records in `eval/s11/metrics.py` (`23` unit tests passed; full stub suite `2842` passed, `0` failed).
- Part C (`aa2aa91`) added Ragas `0.4.3` input assembly, citation stripping, empty-context handling, run validity tracking, `RecordingRagasLLM` JSONL recording, and judge flags in `eval/s11/ragas_inputs.py` (`9` unit tests passed; full stub suite `2851` passed, `0` failed).
- Part D (`2a93dbc`) added `eval/s11/tree_audit.py` (state-machine Contents parser, dual-extractor offset verification, 3-variant tree audit, no-LLM leaf forecast, anonymized `--shapes` / `--explain-fallbacks` diagnostics) and `eval/s11/tree_instrument.py` (`meta_processor` mode, `verify_toc` skip contextvar, pre-merge snapshot).
- Part D (`S11b2b-24`) also updated `pageindex/pi_common.py` and `pageindex/pi_agent.py`, which changes the `PI` system before any run: titles that PageIndex `merge_tree` collapses into `key_items` are now shown in the agent outline and `get_node` (they were invisible before).
- Real-PDF `contents` diagnostics parsed all `24` Contents entries and `7` annexures (`I`–`VII`), flagged `3` body-wording offset fallbacks (`83.333` / `87.582` / `85.714`, all peaking on `printed + 11`), forecasted `2` sections spanning `> 10` pages with `< 20000` approx tokens, and passed all `29` targeted Part D tests + `2` env-template tests (final full stub suite pending).

---

## SELF-AUDIT

### 1. Asked vs done

- **Part A (survey + branch setup):** **done** — verified `ragas 0.4.3` and `pageindex 0.2.10` (`50a71b6`) behaviors with `file:line`, identified citation/splitter/`key_items` gaps in `pageindex/pi_agent.py` and `pageindex/pi_common.py`, confirmed `rapidfuzz` absence in `.venv`, created branch `s11b2b/metrics-audit`, and verified `data/s11/tree/` is ignored by `.gitignore:35`.
- **Part B (`S11b2b-02..07`, `-21`):** **done** (`3eb2817`) — implemented `eval/s11/metrics.py` and `tests/test_s11b2b_metrics.py`; passed targeted (`23`) and full stub suite (`2842`).
- **Part C (`S11b2b-08..12`, `-21`, `F5`, `F6`):** **done** (`aa2aa91`) — implemented `eval/s11/ragas_inputs.py` and `tests/test_s11b2b_ragas_inputs.py`; passed targeted (`9`) and full stub suite (`2851`).
- **Part D (`S11b2b-13..20`, `-24..29`, `F1..F4`, `D1..D5`, `G1`, `T1`, `X1`):** **done** (`2a93dbc`) — implemented `eval/s11/tree_audit.py`, `eval/s11/tree_instrument.py`, `key_items` rendering in `pageindex/pi_common.py` and `pageindex/pi_agent.py`, forced `RAGAS_DO_NOT_TRACK="true"` in `eval/s11/ragas_inputs.py` with empty `RAGAS_DO_NOT_TRACK=` in `.env.example`, leak-check helper `tests/_s11_leakcheck.py`, and Part D tests; ran real-PDF `contents`, `--shapes`, and `--explain-fallbacks` diagnostics; passed `29` targeted Part D tests and `2` env-template tests.
- **Part E (report + PR):** **done (report written; commit/full suite/push/PR/CI pending Hariom's execution)** — wrote `logs/S11b2b_report.md` and issued the closing git/test/PR/CI commands.

### 2. Decisions I made myself

- Built strict standalone citation parsing (`parse_citations`) and sentence splitting (`split_sentences`) in `eval/s11/metrics.py` rather than modifying `pageindex/pi_agent.py`, preserving live check/repair behavior unchanged while catching malformed brackets and `S11b2b-05` sentence rules.
- Handled `[p. 0]` / `[p. 117]` as parsed out-of-range integers in `C` (`malformed_citations=0`, `c1=False`) per `Q1`, and kept malformed-only sentences as uncited for `C4` per `Q5`.
- Fixed MetricRecord key schema at all `44` keys (always emitting `<field>_na` siblings, `None` when populated) per `Q4`, using `bool` for `hit_*`/`c1`/`c2`/`c3` and `float` for `recall_*`/`precision_*`/`c4`/`c5` per `Q3`.
- Added `.bound(...)` and `contextvars`-scoped `.scope(...)` to `RecordingRagasLLM` over one thread-safe `_JsonlWriteOnceSink` so concurrent `asyncio.gather` metric calls record exact per-metric metadata without modifying scores.
- Normalized both Roman (`I`–`XXXIX`) and Arabic annexure numerals in `extract_annexure_numeral` to canonical integer keys (`"1"` vs `"2"`) and rejected unknown top-level tree keys outside `ALLOWED_TOP_LEVEL_KEYS` (`doc_name`, `structure`, `doc_description`, `open_reservations`, `wrapper_calls`, `custom_logger_calls`, with `IGNORED_TOP_LEVEL_KEYS` reported in `ignored_keys` per `Q-D3`).
- In `pageindex/pi_agent.py` (`_bounded_key_items`), truncated an overflowing `key_items` entry cleanly to remaining `160`-char budget + `"…"` per `Q-D1`, and placed `" · also: ..."` in `pageindex/pi_common.py:_line` right after the page span and before `[+N sub-sections]`, dropping `key_items` before `summary` during outline budget degradation.
- In `tests/_s11_leakcheck.py`, stripped fixed `_ALLOWED_SCHEMA_TOKENS` before scanning output for `>= 3`-letter source words and non-ASCII letters (`R2`), and cleared intentional `annexure_numeral` / `annexure_numerals` schema fields after asserting `"III"` in `test_explain_fallbacks_reports_scores_joins_and_leaks_zero_letters`.

### 3. Deviations

- **Approved design replacements / refinements during S11b2b (all pre-approved via `S11b2b-24..29`):**
  - Added `S11b2b-24` (`key_items` rendering in `pageindex/pi_common.py` and `pageindex/pi_agent.py`).
  - Added `S11b2b-25` (amended): forced `os.environ["RAGAS_DO_NOT_TRACK"] = "true"` at top of `eval/s11/ragas_inputs.py` and empty `RAGAS_DO_NOT_TRACK=` entry in `.env.example`.
  - Replaced `S11b2b-14` v1 regex Contents parser with `S11b2b-26` (`contents --shapes`), `S11b2b-27` + `G1` (state-machine Rules 1–6 across PyMuPDF and PyPDF2 plus 1/2/3-line window joins and label-only matching), `S11b2b-28` (`contents --explain-fallbacks`), and `S11b2b-29` (`R1`–`R3` test trace & `tests/_s11_leakcheck.py`).
- **Context recovery read outside repo:** two local session log files outside the repository were read (read-only) during Part E planning to recover earlier instructions after the session context was compacted (no files written outside the repo; nothing under `data/` and no `.env` opened).
- No other deviations.

### 4. How I implemented it

- `eval/s11/metrics.py` — strict `[p. N]`/`[pp. N-M]` citation parser (`parse_citations`), `S11b2b-05` sentence splitter (`split_sentences`), `R`/`C` set metrics (`compute_set_metrics`), exploratory numeric check (`compute_c5`), status/`na_reason` engine (`score_result_record`, `score_hy5_record`), and `44`-key write-once JSON records (`METRIC_RECORD_KEYS`).
- `eval/s11/ragas_inputs.py` — forced `RAGAS_DO_NOT_TRACK="true"` before importing `ragas`, line-preserving citation stripper (`strip_citations`), inclusion/exclusion filter (`build_ragas_input_row`, `build_ragas_inputs`), canonical `input_sha256` write-once JSONL writer (`write_ragas_inputs_jsonl`), `RecordingRagasLLM(InstructorBaseRagasLLM)` JSONL proxy, empty-context `faithfulness=0.0` + `NaN`/error validity runner (`evaluate_ragas_run`), and judge flag extractor (`derive_judge_flags`).
- `eval/s11/tree_audit.py` — NFKC/casefold normalizer & annexure numeral guard (`normalize_title`, `extract_annexure_numeral`, `fuzzy_title_score`), state-machine Contents parser (`parse_contents_pages`), dual-extractor 1/2/3-line window search (`verify_contents_offsets`), one-to-one tie-broken matcher (`_match_entries_to_nodes`), 3-variant 7-criterion audit (`audit_tree_variant`, `audit_tree`), no-LLM leaf forecast (`compute_leaf_forecast`), anonymized `--shapes` (`analyze_contents_shapes`) and `--explain-fallbacks` (`explain_contents_fallbacks`) diagnostics, and `contents`/`audit` CLI (`main`).
- `eval/s11/tree_instrument.py` — `instrument_tree_build(snapshot_path)` context manager wrapping `pageindex.page_index_classic.{meta_processor, verify_toc, check_title_appearance, merge_tree}` with `contextvars`-scoped `skipped_verify` counting (`F1`), `'pre-0000'..` pre-merge deep-copy snapshots (`F2`, `D5`), and exception-safe restore.
- `pageindex/pi_common.py` (`_format_key_items`, `_line`, `outline`) & `pageindex/pi_agent.py` (`_bounded_key_items`, `_get_node_child_payload`, `_run_tool`) — render `key_items` (160-char cap) in both tree outline and `get_node` while staying byte-identical when `key_items` is absent/empty (`S11b2b-24`).
- `.env.example` — added `# Disable ragas usage tracking; eval/s11/ragas_inputs.py forces it to true so ragas never posts usage events.` and empty `RAGAS_DO_NOT_TRACK=` (`S11b2b-25` amended).
- `tests/test_s11b2b_metrics.py`, `tests/test_s11b2b_ragas_inputs.py`, `tests/test_s11b2b_tree_audit.py`, `tests/test_s11b2b_tree_instrument.py`, `tests/_s11_leakcheck.py` — offline test suites + leak-check helper under `REGCLAW_STUB=1` and `tests/_netguard.py` (using `pytest.importorskip` for optional packages on Linux CI).
- `logs/S11b2b_report.md` — this report.
- **Commands given to Hariom across Parts A–E:**
  - Part A: `git switch -c s11b2b/metrics-audit`, `git check-ignore -v data/s11/tree/`.
  - Part B: `.venv\Scripts\python -m pytest -q tests/test_s11b2b_metrics.py`, `.venv\Scripts\python tasks.py test`, `git add eval/s11/metrics.py tests/test_s11b2b_metrics.py`, single-line `git commit`.
  - Part C: `.venv\Scripts\python -m pytest -q tests/test_s11b2b_ragas_inputs.py`, `.venv\Scripts\python tasks.py test`, `git add eval/s11/ragas_inputs.py tests/test_s11b2b_ragas_inputs.py`, single-line `git commit`.
  - Part D (incl. diagnostics & `.env.example` fix): `.venv\Scripts\python -m eval.s11.tree_audit contents --pdf data\pdfs\guidelines_GTP.pdf`, `.venv\Scripts\python -m eval.s11.tree_audit contents --pdf data\pdfs\guidelines_GTP.pdf --shapes`, `.venv\Scripts\python -m eval.s11.tree_audit contents --pdf data\pdfs\guidelines_GTP.pdf --explain-fallbacks`, `.venv\Scripts\python -m pytest -q tests/test_env_template.py tests/test_s11b2b_ragas_inputs.py tests/test_s11b2b_tree_audit.py tests/test_s11b2b_tree_instrument.py`, `.venv\Scripts\python tasks.py test`, 10-file `git add`, single-line `git commit`.
  - Part E (issued with this report): `git add logs/S11b2b_report.md`, single-line `git commit`, `.venv\Scripts\python tasks.py test`, `git push -u origin s11b2b/metrics-audit`, `gh pr create`, `gh pr checks`.

### 5. Checks with real output

- **Pre-S11b2b baseline (`main` `57ece47`, `logs/S11b2a_report.md`):** `2819 passed, 4 skipped, 0 failed`.
- **Part B (`3eb2817`, Hariom Windows run):**
  - `tests/test_s11b2b_metrics.py`: `23 passed in 26.02s` (`23` collected items across `9` test functions; previously reported `8` functions / `16` items in error).
  - Full stub suite (`.venv\Scripts\python tasks.py test`): `2842 passed, 4 skipped, 1 warning (pre-existing pydantic warning in tests/test_s11b1_litellm_hook.py), 0 failed`.
- **Part C (`aa2aa91`, Hariom Windows run):**
  - `tests/test_s11b2b_ragas_inputs.py`: `9 passed in 31.85s` (`9` test functions originally; extended to `10` in Part D for `S11b2b-25`).
  - Full stub suite (`.venv\Scripts\python tasks.py test`): `2851 passed, 4 skipped, 1 warning (pre-existing pydantic), 0 failed`.
- **Part D (`2a93dbc`, Hariom Windows runs):**
  - `tests/test_env_template.py`: `2 passed`.
  - Standalone re-runs of flaky S10 tests: `tests/test_s10b2g_friend_tool.py::test_permission_retries_third_attempt_or_all_five` `6 passed` alone; `tests/test_s10b2h_retest_a3.py::test_http_security_and_page` `1 passed` alone.
  - Targeted Part D (`tests/test_s11b2b_tree_audit.py`, `tests/test_s11b2b_tree_instrument.py`, `tests/test_s11b2b_ragas_inputs.py`): `29 passed` (`12 + 7 + 10` = `29`).
  - Real-PDF `eval.s11.tree_audit contents`: `entries 24, annexures 7 (I–VII), no_page 2, headings_skipped 1` on both extractors; `offset_fallbacks 3`, `extractor_disagreements 0`, `forecast spans_gt_10 2` (both `< 20000` approx tokens), `largest_span 20`.
- **Final full stub suite after `.env.example` fix & Part E report:** **pending** (command given below before push/merge).

### 6. Frozen items

- Untouched and unmodified across Parts A–E: `eval/s10/` (all code, manifests, `s10c_code_lock.json`, `.ots`), `report.pdf`, `specs/`, `eval/s11/golden_v1.json`, `eval/s11/prompts/answer_rules_v1.txt`, `eval/s11/manifests/*`, `eval/eval_pageindex.py`, `pageindex/pi_ingest.py`, and installed packages (`pageindex`, `ragas`).

### 7. Holdouts and sealed files

- Confirmed not opened, not listed, not printed, not hashed, and not scored: `.env`, `eval/s11/golden_v1.json`, and all files under `data/` were never accessed by the coding agent. All real-PDF commands were executed locally by Hariom with anonymized/count-only output pasted.

### 8. Safety

- No files written outside the repository; two local session log files outside the repository were read (read-only) to recover earlier instructions after the session context was compacted; nothing under `data/` and no `.env` was opened; zero commits, pushes, tests, `pip`, or `git` commands executed by the agent; zero secrets printed; zero scratch files left behind; zero AI/tool attribution anywhere in code, commits, PR text, or reports.

### 9. Paid/external calls

- `0` paid or external API calls (`USD 0`). All tests run offline under `REGCLAW_STUB=1` and `tests/_netguard.py` with fake LLM/embedding classes and synthetic Contents/trees.

### 10. Not checked / assumed

- Final full Windows stub suite (`tasks.py test`) after the `.env.example` fix (`2a93dbc`) and after adding `logs/S11b2b_report.md` is still pending Hariom's run before push/merge.
- Remote PR creation and Ubuntu CI (`.github/workflows/ci.yml`) checks are pending push; Ubuntu CI lacks `ragas`, `PyPDF2`, `litellm`, and `pageindex`, so those package tests skip via `pytest.importorskip`.
- Live end-to-end execution of `eval/s11/metrics.py`, `eval/s11/ragas_inputs.py`, `eval/s11/tree_audit.py audit`, `eval/s11/tree_instrument.py`, and updated `key_items` agent navigation against real Anthropic/Azure responses and a real built GTP tree has not been run yet (deferred to `S11b2c` / `S11b3`).

### 11. Errors and retries

- **Part B test-count reporting error:** reported `8` test functions / `16` parametrized items instead of the `23` tests pytest actually collected (caught and corrected from Hariom's `--collect-only` and pytest output).
- **Pre-`S11b2b-25` full-suite race (`2865 passed, 1 failed, 4 skipped`):** `tests/test_s10b2g_friend_tool.py::test_permission_retries_third_attempt_or_all_five[False-temp-write]` failed during global `time.sleep` monkeypatching because importing `ragas` starts `ragas._analytics._analytics_batcher` (`AnalyticsBatcher._flush_loop` calling `time.sleep(1)`). Re-running the S10 test standalone passed (`6 passed`), confirming a cross-test background-thread race. Added `S11b2b-25` forcing `RAGAS_DO_NOT_TRACK="true"` before importing `ragas`; note `S11b2b-25` only stops `ragas` usage HTTP posting (`requests.post`) and does **not** mitigate the `time.sleep` race because the daemon thread still starts unconditionally on `ragas` import.
- **First real-PDF `contents` stop (`S11b2b-14` v1 regex parser):** returned `entries 0, annexures 0, parse_fallbacks 0, skipped_roman 7, offset_fallbacks 0`, triggering the stop rule. Added `S11b2b-26` (`contents --shapes`), which showed PyMuPDF places printed page numbers and `Annexure <roman>` labels on standalone lines while PyPDF2 places `<title> [Annexure <roman>] <number>` on one line and splits numerals with spaces (`"V I"`, `"V II"`), and identified why v1 `_TOC_ROMAN_RE` discarded `7` lines (`skipped_roman`). Replaced the parser under `S11b2b-27`.
- **Repeated single-letter leak-check mistake (`T1` -> `S11b2b-28` -> `S11b2b-29` `R2`):** checking individual source letters against entire stdout failed because JSON/label schema words (`extractor`, `starts_with_annexure`, `wide`, `window`) contain those letters. First scoped `T1` to parsed `shape="..."` values, then standardized all leak tests on `tests/_s11_leakcheck.py::assert_no_source_words` (plus self-test and explicit redaction of intentional `annexure_numeral` `"III"` metadata after asserting it) under `S11b2b-29` `R2`.
- **`X1` Rule 4 synthetic-input mismatch (`25 passed, 1 failed` targeted):** `test_contents_parser_offset_window_disagreements_and_forecast_d2_d3` got `no_page == 1` instead of `2` because synthetic lines `"3. ..."` / `"4. ..."` carried a trailing dot not matched by Rule 4 (`^\d+(?:\.\d+)*\s+`), causing merges instead of flushing `no_page`. Resolved via `X1` Option A by updating synthetic lines to Rule 4 format (`"3 Section..."`, `"4 Earlier Jump"`) and adding `test_rule4_known_limit_trailing_dot_is_continuation`.
- **Second real-PDF `contents` stop (`S11b2b-27` parser):** matched pre-registered counts (`entries 24, annexures 7 (I–VII), no_page 2, headings_skipped 1` on both extractors) with `offset_fallbacks 3`, firing the stop rule. Added `S11b2b-28` (`contents --explain-fallbacks`), showing entries `1, 6, 13` scored `83.333`, `87.582` (`PyPDF2 87.013`), and `85.714` on `printed + 11` on both extractors due to body wording differences.
- **`S11b2b-29` forecast & known-limit arithmetic mistakes:** `test_contents_parser_offset_window_disagreements_and_forecast_d2_d3` initially expected `spans_gt_10 == 2` instead of `3` (missed the 11-page span `20..30`); `test_rule4_known_limit_trailing_dot_is_continuation` initially miscounted unclosed flushed `no_page` (`1`) and `entry_count` (`1`, `printed_page 20`); and `< 90.0` bounds in `test_explain_fallbacks_reports_scores_joins_and_leaks_zero_letters` were replaced with exact 3-decimal trace values (`84.615`, `63.636`, `60.87`). Verified every expectation in the `R1` trace table before re-running.
- **`.env.example` template mistake (`2869 passed, 2 failed` full suite):** `S11b2b-25` initially added `RAGAS_DO_NOT_TRACK=true`, failing `tests/test_env_template.py::test_template_covers_explicit_environment_reads_and_has_empty_values` (which requires empty `NAME=` lines); the same full-suite run also hit the known flaky `tests/test_s10b2h_retest_a3.py::test_http_security_and_page` (`WinError 10053`, passed `1` alone). Amended `.env.example` to `RAGAS_DO_NOT_TRACK=` empty with explanatory comment; `tests/test_env_template.py` passed (`2 passed`).

### 12. Risks for later steps + what I need from Hariom

- **Tree condition 7 forecast failure:** `2` forecasted sections span `> 10` pages (largest `20`) while having `< 20000` approx tokens, so `PageIndex` will not split them under registered `max_page_num_each_node 10` / `max_token_num_each_node 20000`; pre-build Variant `A`/`B`/`C` PASS selection must also be finalized before the first live build.
- **Residual thread race:** `ragas._analytics.AnalyticsBatcher` still starts its `time.sleep(1)` daemon thread on `ragas` import, so S10 tests patching `time.sleep` globally can still race intermittently during full-suite runs.
- **Needed from Hariom:** stage and commit `logs/S11b2b_report.md`, run the full Windows stub suite (`.venv\Scripts\python tasks.py test`), push `s11b2b/metrics-audit`, open the PR, inspect CI, and merge only after the working chat gives OK.

---

## Decision -> File -> Test Table (`S11b2b-02`..`29`)

| Decision | File(s) & Key Symbol(s) | Test(s) |
|---|---|---|
| `S11b2b-02` | `eval/s11/metrics.py` (`CITE_BRACKET_RE`, `parse_citations`, `score_result_record`) | `tests/test_s11b2b_metrics.py::test_citation_parser_single_range_endash_and_malformed_cases`, `::test_c1_and_c2_bounds_malformed_and_not_in_r` |
| `S11b2b-03` | `eval/s11/metrics.py` (`compute_set_metrics`, `score_result_record`, `score_hy5_record`) | `tests/test_s11b2b_metrics.py::test_retrieval_metrics_two_gold_sets_best_recall_and_empty_precision_na`, `::test_c3_equals_hit_c_parametrised` |
| `S11b2b-04` | `eval/s11/metrics.py` (`NA_REASONS`, `score_result_record`, `score_hy5_record`) | `tests/test_s11b2b_metrics.py::test_status_rules_all_rows_and_exact_na_reasons` |
| `S11b2b-05` | `eval/s11/metrics.py` (`ABBREVIATIONS`, `split_sentences`, `sentence_has_citation`) | `tests/test_s11b2b_metrics.py::test_split_sentences_all_required_cases` |
| `S11b2b-06` | `eval/s11/metrics.py` (`SENTENCE_NUMBER_RE`, `PAGE_NUMBER_RE`, `compute_c5`) | `tests/test_s11b2b_metrics.py::test_c5_found_not_found_thousands_decimal_uncited_and_cross_sentence_isolation` |
| `S11b2b-07` | `pageindex/pi_common.py` (`fetch_pages`), `eval/s11/metrics.py`, `eval/s11/ragas_inputs.py` | `tests/test_s11b2b_metrics.py::test_fresh_subprocess_import_metrics_from_repo_root`, `tests/test_s11b2b_ragas_inputs.py::test_contexts_first_seen_r_order_drops_empty_keeps_reference_and_write_once` |
| `S11b2b-08` | `eval/s11/ragas_inputs.py` (`strip_citations`, `build_ragas_input_row`, `build_ragas_inputs`) | `tests/test_s11b2b_ragas_inputs.py::test_included_and_excluded_sets_with_reasons`, `::test_strip_citations_valid_malformed_punctuation_and_bullets`, `::test_contexts_first_seen_r_order_drops_empty_keeps_reference_and_write_once` |
| `S11b2b-09` | `eval/s11/ragas_inputs.py` (`build_ragas_input_row`, `build_ragas_inputs`) | `tests/test_s11b2b_ragas_inputs.py::test_included_and_excluded_sets_with_reasons` |
| `S11b2b-10` | `eval/s11/ragas_inputs.py` (`evaluate_ragas_run`, `WD_FAITHFULNESS_LABEL`) | `tests/test_s11b2b_ragas_inputs.py::test_nan_from_empty_statements_and_exception_invalidate_run`, `::test_derived_flags_rel_and_corr_reference_empty_and_wd_label_and_judge_settings` |
| `S11b2b-10b` | `eval/s11/ragas_inputs.py` (`evaluate_ragas_run`) | `tests/test_s11b2b_ragas_inputs.py::test_empty_context_rule_gives_zero_no_judge_call_and_scores_others` |
| `S11b2b-11` | `eval/s11/ragas_inputs.py` (`RecordingRagasLLM`, `_JsonlWriteOnceSink`, `RECORDER_FIELDS`, `derive_judge_flags`, `_require_output_path`) | `tests/test_s11b2b_ragas_inputs.py::test_proxy_isinstance_jsonl_fields_write_once_and_identical_scores`, `::test_writer_requires_path_and_creates_no_file`, `::test_derived_flags_rel_and_corr_reference_empty_and_wd_label_and_judge_settings` |
| `S11b2b-12` | `eval/s11/ragas_inputs.py` (`build_s11_ragas_metrics`) | `tests/test_s11b2b_ragas_inputs.py::test_derived_flags_rel_and_corr_reference_empty_and_wd_label_and_judge_settings` |
| `S11b2b-13` | `eval/s11/tree_audit.py` (`_require_explicit_path`, CLI `--tree` / `--out` required) | `tests/test_s11b2b_tree_audit.py::test_schema_and_cli_rules_f3_f4_d4` |
| `S11b2b-14` | `eval/s11/tree_audit.py` (v1 regex Contents parser; superseded by `S11b2b-27`) | Replaced by `S11b2b-27` tests in `tests/test_s11b2b_tree_audit.py` |
| `S11b2b-15` | `eval/s11/tree_audit.py` (`verify_contents_offsets`, `_best_match_across_pages`) | `tests/test_s11b2b_tree_audit.py::test_contents_parser_offset_window_disagreements_and_forecast_d2_d3`, `::test_contents_real_synthetic_pdf_pymupdf_and_pypdf2_parity` |
| `S11b2b-16` | `eval/s11/tree_audit.py` (`normalize_title`, `extract_annexure_numeral`, `fuzzy_title_score`) | `tests/test_s11b2b_tree_audit.py::test_normalizer_annexure_guard_and_intro_exclusion` |
| `S11b2b-17` | `eval/s11/tree_audit.py` (`_match_entries_to_nodes`, `_assign_report_ids`) | `tests/test_s11b2b_tree_audit.py::test_one_to_one_matching_tie_breaks_and_variants_a_b_c` |
| `S11b2b-18` | `eval/s11/tree_audit.py` (`audit_tree_variant`, `audit_tree`) | `tests/test_s11b2b_tree_audit.py::test_audit_failure_modes_coverage_order_range_and_leaf_span` |
| `S11b2b-18b` | `eval/s11/tree_audit.py` (`audit_tree` Variants A/B/C) | `tests/test_s11b2b_tree_audit.py::test_one_to_one_matching_tie_breaks_and_variants_a_b_c`, `tests/test_s11b2b_tree_instrument.py::test_installed_pageindex_integration_under_netguard` |
| `S11b2b-19` | `eval/s11/tree_instrument.py` (`instrument_tree_build`, `assign_pre_merge_ids`, `TreeInstrumentState`) | `tests/test_s11b2b_tree_instrument.py::test_required_snapshot_path_and_write_once`, `::test_real_meta_processor_mode_vs_hardcoded_log_and_child_split`, `::test_verify_toc_contextvar_counts_only_inside_verify_toc_f1`, `::test_merge_tree_wraps_call_site_only_one_snapshot_per_call_f2_d5`, `::test_unpatches_on_exception_and_creates_no_snapshot_file`, `::test_installed_pageindex_integration_under_netguard` |
| `S11b2b-20` | `eval/s11/tree_audit.py` (`FORECAST_MAX_PAGES_PER_NODE`, `FORECAST_MAX_TOKENS_PER_NODE`, `compute_leaf_forecast`) | `tests/test_s11b2b_tree_audit.py::test_contents_parser_offset_window_disagreements_and_forecast_d2_d3` |
| `S11b2b-21` | `eval/s11/metrics.py` (`METRICS_RULES_VERSION`, `METRIC_RECORD_KEYS`), `eval/s11/ragas_inputs.py` (`RAGAS_INPUT_KEYS`, `write_ragas_inputs_jsonl`), `eval/s11/tree_audit.py`, `eval/s11/tree_instrument.py` | `tests/test_s11b2b_metrics.py::test_metric_record_exact_fields_and_write_once`, `tests/test_s11b2b_ragas_inputs.py::test_contexts_first_seen_r_order_drops_empty_keeps_reference_and_write_once`, `tests/test_s11b2b_tree_audit.py::test_schema_and_cli_rules_f3_f4_d4`, `tests/test_s11b2b_tree_instrument.py::test_required_snapshot_path_and_write_once` |
| `S11b2b-22` | Branch `s11b2b/metrics-audit`, commits `3eb2817`, `aa2aa91`, `2a93dbc`, Part E | Git history / `logs/S11b2b_report.md` |
| `S11b2b-23` | `specs/` untouched (zero addendum files written) | Frozen-item check in Self-Audit Heading 6 |
| `S11b2b-24` | `pageindex/pi_common.py` (`_format_key_items`, `_line`, `outline`), `pageindex/pi_agent.py` (`_bounded_key_items`, `_get_node_child_payload`, `_run_tool`) | `tests/test_s11b2b_tree_audit.py::test_key_items_outline_and_get_node_s11b2b_24` (plus existing `tests/test_pi_agent.py`, `tests/test_s11b2_answer.py` unchanged) |
| `S11b2b-25` | `eval/s11/ragas_inputs.py` (`RAGAS_DO_NOT_TRACK="true"` forced), `.env.example` (`RAGAS_DO_NOT_TRACK=`) | `tests/test_s11b2b_ragas_inputs.py::test_ragas_do_not_track_forced_before_import_in_fresh_subprocess`, `tests/test_env_template.py::test_template_covers_explicit_environment_reads_and_has_empty_values` |
| `S11b2b-26` | `eval/s11/tree_audit.py` (`line_shape`, `analyze_contents_shapes`, CLI `contents --shapes`) | `tests/test_s11b2b_tree_audit.py::test_contents_shapes_mode_anonymizes_letters_and_reports_decisions` |
| `S11b2b-27` | `eval/s11/tree_audit.py` (state-machine Rules 1–6 `parse_contents_pages`, 1/2/3-line joins, `G1` label-only handling) | `tests/test_s11b2b_tree_audit.py::test_contents_parser_offset_window_disagreements_and_forecast_d2_d3`, `::test_contents_real_synthetic_pdf_pymupdf_and_pypdf2_parity`, `::test_normalizer_annexure_guard_and_intro_exclusion`, `::test_rule4_known_limit_trailing_dot_is_continuation` |
| `S11b2b-28` | `eval/s11/tree_audit.py` (`explain_contents_fallbacks`, CLI `contents --explain-fallbacks`) | `tests/test_s11b2b_tree_audit.py::test_explain_fallbacks_reports_scores_joins_and_leaks_zero_letters` |
| `S11b2b-29` | `tests/_s11_leakcheck.py` (`assert_no_source_words`), `tests/test_s11b2b_tree_audit.py` | `tests/test_s11b2b_tree_audit.py::test_leakcheck_helper_catches_source_words_and_ignores_schema_words`, `::test_contents_shapes_mode_anonymizes_letters_and_reports_decisions`, `::test_explain_fallbacks_reports_scores_joins_and_leaks_zero_letters` |

---

## Part A Facts Confirmed & Repo Findings (`file:line`)

### 1. Installed `ragas` `0.4.3` (all confirmed)

- **`Faithfulness`** (`.venv/Lib/site-packages/ragas/metrics/collections/faithfulness/metric.py`): `ascore(self, user_input: str, response: str, retrieved_contexts: List[str])` at `L88-90` (`name="faithfulness"` at `L70`); raises `ValueError` if any of those inputs is empty (`L103-114`); returns `MetricResult(value=float("nan"))` when `statements` is empty (`L117-121`); joins `retrieved_contexts` with `"\n"` (`L124`); returns `float("nan")` if `not verdicts.statements` (`L148-151`).
- **`AnswerRelevancy`** (`.venv/Lib/site-packages/ragas/metrics/collections/answer_relevancy/metric.py`): default `strictness: int = 3` (`L71`), `ascore(self, user_input: str, response: str)` (`L93`); loops `strictness` times (`L116`), returns `0.0` if no questions are generated (`L126-127`), and multiplies `cosine_sim.mean() * int(not all_noncommittal)` (`L130`, `L154`).
- **`AnswerCorrectness`** (`.venv/Lib/site-packages/ragas/metrics/collections/answer_correctness/metric.py`): defaults `weights = [0.75, 0.25]` and `beta = 1.0` (`L83-84`), `ascore(self, user_input: str, response: str, reference: str)` (`L154-156`); sets `factuality_score = 1.0` whenever `response_statements` or `reference_statements` is empty (`L175-182`).
- **Base validation & ABCs:** `BaseMetric._validate_llm` checks `isinstance(llm, InstructorBaseRagasLLM)` and `_validate_embeddings` checks `isinstance(embeddings, BaseRagasEmbedding)` (`.venv/Lib/site-packages/ragas/metrics/collections/base.py:113-131`); `InstructorBaseRagasLLM` defines abstract `generate(prompt, response_model)` and `async agenerate(prompt, response_model)` (`.venv/Lib/site-packages/ragas/llms/base.py:730-748`); `BaseRagasEmbedding` defines `embed_text` and `async aembed_text` (`.venv/Lib/site-packages/ragas/embeddings/base.py:29-65`).
- **Telemetry behavior:** `ragas._analytics` (`.venv/Lib/site-packages/ragas/_analytics.py:41-49,58-77,132-164,222-234,287`) skips `requests.post` when `RAGAS_DO_NOT_TRACK` is `"true"`, but instantiates `_analytics_batcher = AnalyticsBatcher(...)` at module import, starting a daemon `_flush_loop` calling `time.sleep(1)` (`L158`).

### 2. Installed `pageindex` `0.2.10` (commit `50a71b686d0397df7cb193c33806eb035574b890`)

- Confirmed commit & version in `.venv/Lib/site-packages/pageindex-0.2.10.dist-info/METADATA:3` and `direct_url.json:1`.
- **`.venv/Lib/site-packages/pageindex/page_index_classic.py`:**
  - `meta_processor` (`L1127-1166`) logs hardcoded `'mode': 'process_toc_with_page_numbers'` (`L1150`) regardless of actual mode; falls back to `'process_toc_no_page_numbers'` then `'process_no_toc'` via module-global calls (`L1154-1165`); `process_large_node_recursively` calls `meta_processor(..., mode='process_no_toc', ...)` (`L1180`).
  - `verify_toc` (`L1066-1120`) calls `check_title_appearance` (`L80-113`) via `asyncio.gather(..., return_exceptions=True)` (`L1103`) and skips `BaseException` items (`L1109-1110`); `fix_incorrect_toc` also calls `check_title_appearance` (`L994`).
  - `check_title_appearance_in_start_concurrent` (`L143-166`) turns raised exceptions into `'no'`.
  - `merge_tree` is imported into `page_index_classic` namespace (`L9`) and called at `L1269` *before* `write_node_id(structure)` (`L1270-1271`), so pre-merge snapshot nodes have no `node_id` yet (`F2`).
  - When `if_add_doc_description == 'yes'` (`pageindex/pi_settings.yaml:20`), output carries top-level keys `{'doc_name', 'doc_description', 'structure'}` (`L1282-1286`), with node key order `['title', 'node_id', 'start_index', 'end_index', 'key_items', 'summary', 'text', 'nodes']` (`L1281, L1288`).
- **`.venv/Lib/site-packages/pageindex/tree_optimize.py`:** `ROUTING_COST = 1` (`L71`), `add_intro_nodes` (`L230`), `merge` (`L589-636`, collapsing subtrees into `node["key_items"]`), and `merge_tree` (`L639-646`).
- **`.venv/Lib/site-packages/pageindex/utils.py`:** `add_preface_if_needed` (`L534`), `get_page_tokens(..., pdf_parser='PyPDF2')` (`L549`), `post_processing` (`L600`), `INTRO_SUFFIX = " (intro)"` (`L770`), `cover_subtree_ranges` (`L859-867`), and short-leaf raw text summary threshold `(4/3) * 150 = 200` tokens (`L890-891, L1060-1061, L1128-1131`).

### 3. Repo Findings & Fixes Applied

- **Agent outline & `get_node` omitted `key_items` (fixed by `S11b2b-24`):** Before Part D, `pageindex/pi_common.py` (`_line` `L400-410`) and `pageindex/pi_agent.py` (`get_node` `L157-171`) rendered only `node_id`, `title`, page span, child count, and `summary`, omitting `key_items`. Under `S11b2b-24`, `pi_common.py:_line`/`outline` and `pi_agent.py:get_node` now render bounded `key_items` (`<= 160` chars), which changes the `PI` system before any live run so child titles collapsed into `key_items` by `merge_tree` are visible to the navigation agent during tree traversal.
- **Citation & splitter gaps left untouched in `pi_agent.py`:** `pageindex/pi_agent.py:42` (`CITE_RE`), `_cited_pages` (`L350-357`), and `split_claims` (`L360-369`) remain unchanged to preserve existing check/repair behavior; `eval/s11/metrics.py` implements strict `S11b2b-02`/`-05` rules separately.
- **PDF extractor, judge wiring, environment & git-ignore:** PyMuPDF extraction uses `pageindex/pi_common.py:fetch_pages` (`L354-362`); Ragas judge settings reuse `eval/eval_pageindex.py:84,125-169` (`JUDGE_MAX_TOKENS=8192`, `build_guarded_async_openai`, default `"openai"` provider); `rapidfuzz` is absent from `.venv`; and `git check-ignore -v data/s11/tree/` matched `.gitignore:35:data/s11/`.

---

## Real-PDF Results (`guidelines_GTP.pdf`, Counts & Numbers Only)

- **Contents parse (`contents` CLI across both PyMuPDF and PyPDF2 on pages `10–11`):**
  - `entries`: `24` (PyMuPDF) and `24` (PyPDF2)
  - `annexures`: `7` (`["I", "II", "III", "IV", "V", "VI", "VII"]`) on both extractors
  - `no_page`: `2` on both extractors
  - `headings_skipped`: `1` on both extractors
- **Dual-extractor offset verification & fallback explanation (`contents --explain-fallbacks`):**
  - `pymupdf` matches: `21`, `pypdf2` matches: `0`, `offset_fallbacks`: `3`
  - `extractor_disagreements`: `0`
  - Fallback breakdown (zero titles/text; both extractors peaked on exact `expected_start = printed + 11`):
    - `entry_index 1`: PyMuPDF best score `83.333`, PyPDF2 best score `83.333`, peaking on `printed + 11`
    - `entry_index 6`: PyMuPDF best score `87.582`, PyPDF2 best score `87.013`, peaking on `printed + 11`
    - `entry_index 13`: PyMuPDF best score `85.714`, PyPDF2 best score `85.714`, peaking on `printed + 11`
- **No-LLM leaf forecast (`max_page_num_each_node = 10`, `max_token_num_each_node = 20000`):**
  - `spans_gt_10`: `2` (both `< 20000` approx tokens on both PyMuPDF and PyPDF2 text)
  - `largest_span`: `20` pages
- **What replaced v1 `skipped_roman` matched (`S11b2b-26` rule in words, no PDF text):**
  - In the `S11b2b-14` v1 parser (before `S11b2b-27`), a non-empty, NFKC-normalized line that did not match a skipped header or an Arabic trailing page-number pattern was tested against a trailing Roman-numeral pattern expecting `<title><separator><token>` where `<separator>` was dot leaders, tabs, or `1+` spaces, `<token>` was a valid Roman numeral (`I`–`LXXXIX`), and `<title>` held at least two ASCII letters outside header keywords; matching lines incremented `skipped_roman` and cleared any buffered continuation text. Because PyMuPDF put real page numbers on standalone lines, lines ending with an annexure Roman numeral triggered that branch `7` times and produced `0` accepted entries until replaced by the `S11b2b-27` state machine.

---

## Known Limits

- **`C4` is sentence-level only:** `C4` measures whether each valid sentence carries at least one valid citation, not whether the cited page semantically entails the claim.
- **`C5` (exploratory) footer/word limits:** numeric token matching against cited page PyMuPDF text can falsely match header/footer numbers and ignores numbers spelled out as words.
- **Ragas `0.4.3` defaults that can mask failures (and flags/validity recorded):** `Faithfulness` returns `NaN` on empty extracted statements or verdicts (or raises `ValueError` on empty inputs/context); `AnswerCorrectness` assigns `factuality = 1.0` when either response or reference yields zero statements; and `AnswerRelevancy` returns `0.0` on zero generated questions or when all generated questions are noncommittal. `eval/s11/ragas_inputs.py` exposes these explicitly via `faith_zero_no_context`, `faith_no_statements`, `corr_no_statements`, `rel_no_questions`, `rel_all_noncommittal`, `is_nan`, `error_type`, and run-level `valid=False`.
- **PASS variant selector pending addendum:** `eval/s11/tree_audit.py` reports all three variants (`A` final node titles `|start - exp| <= 1`, `B` final node titles or `key_items` with `start - 1 <= exp <= end + 1`, `C` pre-merge snapshot titles) without choosing which variant decides PASS; a pre-build addendum will lock the choice.
- **Rule 4 trailing dot limitation:** `_TOC_SECTION_START_RE` (`^\d+(?:\.\d+)*\s+`) requires whitespace immediately after digits, so lines beginning `"4. Title"` (number + trailing dot + space) are treated as buffer continuations under Rule 5 rather than flushing an unclosed prior buffer as `no_page` under Rule 4.
- **3 Contents titles worded differently in body:** on the real GTP PDF, `entry_index` `1`, `6`, and `13` score `83.333`, `87.582` (`PyPDF2 87.013`), and `85.714` below the `90` fuzzy match threshold, though all three still align to exact `printed + 11` on both extractors.
- **`reasoning_effort="low"` judge gating:** `eval/eval_pageindex.py:judge_kwargs` sends `reasoning_effort="low"` only for reasoning-model judges (`gpt-5*`, `o<digit>*`), not for `gpt-4.1-mini` (existing `eval_pageindex.py` behavior reused by `eval/s11/ragas_inputs.py`).
- **Ragas import-time daemon thread race:** `S11b2b-25` forces `RAGAS_DO_NOT_TRACK="true"` so `ragas` never posts usage events, but `ragas._analytics.AnalyticsBatcher` still starts its daemon `_flush_loop` thread (`time.sleep(1)`) on import, so an S10 test that globally monkeypatches `time.sleep` can still race with it.
- **Offline/platform test boundary:** Ubuntu CI runs without `ragas`, `PyPDF2`, `litellm`, or `pageindex` (`pytest.importorskip` skips those tests cleanly), and all S11b2b tests run offline (`REGCLAW_STUB=1`, fake judges/embeddings, synthetic PDFs/trees).

---

## Left for `S11b2c` / `S11b3`

- **Forecasted tree split failure under registered config:** the leaf forecast predicts PASS condition `7` (`every leaf <= 10 pages`) failing at the registered PageIndex configuration (`max_page_num_each_node = 10`, `max_token_num_each_node = 20000`) because `2` sections exceed `10` pages (largest `20`) while containing `< 20000` approx tokens (not split by `process_large_node_recursively`).
- **the S11b2c driver & orchestration:** wrap `build_tree` inside `instrument_tree_build(snapshot_path)` to capture true `meta_processor` modes, `verify_toc` skips (`F1`), and the pre-merge snapshot (`F2`, `D5`); lock the Variant `A`/`B`/`C` PASS decider via pre-build addendum; wire metrics computation (`eval/s11/metrics.py`), Ragas execution and judge recording (`eval/s11/ragas_inputs.py`), blind human grading export/import, `40`-statement judge validation sampling, `input_sha256`-keyed cached sensitivity judging, and paired system comparisons.
- **Git-ignored storage for corpus-bearing files:** the built tree, the `contents --out` JSON, the Ragas inputs JSONL, and the judge recorder JSONL all hold verbatim guideline text and must be written only under the git-ignored `data\` folder (`data/s11/` / `data/runs/`), never committed.
- **`S11b3` live evaluation:** run live document token precounting (updating the `USD 60` Anthropic cap headroom estimate), perform max `2` tree builds with structural audits, run the `3`-question `dev` pilot with the pre-registered effort rule (medium; high only if a dev question fails at medium), execute main `PI`/`HY`/`WD` runs, and run Azure judging and human grading.
