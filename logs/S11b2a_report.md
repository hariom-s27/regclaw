# S11b2a Golden Checker, Shared Answer Step, and Baselines Report

Date: 2026-11-27
Branch: `s11b2a/golden-baselines`
Commits: Part B `8baf1ae` · Part C `dd6723c` · Part D `b25fdd0` · Part E `e937033` · Part F `logs/S11b2a_report.md` (pending)
Frozen manifests:
- `eval/s11/manifests/prereg_v1.json` (`specs/S11_PREREGISTRATION.md`, `sha256_lf = 05adf4a5308c6feff15fc3a941815da3ca312f628b0ad91f06230e498d88a0b9`, `bytes_lf = 11678`)
- `eval/s11/manifests/prereg_addendum_a1.json` (`specs/S11_PREREG_ADDENDUM_A1.md`, `sha256_lf = 8ea7c71298610db09fae4565d9fa44ffc4fd488f67b4d0aabc65ea58b7279625`, `bytes_lf = 3466`)
- `eval/s11/manifests/golden_v1.json` (`eval/s11/golden_v1.json`, `sha256_lf = 30163287f0c2eadf48f6253f6eb4884e5693cfc313fee295dd44ae93994031a5`, `bytes_lf = 14865`)
- `eval/s11/manifests/answer_rules_v1.json` (`eval/s11/prompts/answer_rules_v1.txt`, `sha256_lf = 57681cc4ace83407a4dbc26c6cda08f3409bfebdf5cf449f093e8cb015ec3875`, `bytes_lf = 848`)

## PLAIN SUMMARY

- Part B (`8baf1ae`) added `eval/s11/golden.py` implementing v2 draft validation (`corpus`/`status` permitted and stripped on freeze, strict bool/int type checks, `10`-word PDF verbatim check, warning-only claim-term coverage check, write-once `golden_v1.json` freeze, and verified `load_frozen_golden`).
- Part C (`dd6723c`) froze `specs/S11_PREREG_ADDENDUM_A1.md` in `eval/s11/manifests/prereg_addendum_a1.json` and froze `eval/s11/golden_v1.json` (`test_answerable=12`, `test_unanswerable=4`, `dev_count=3`, `flags=0`, `verbatim=PASS`) with `prereg_v1.json` remaining `OK`.
- Part D (`b25fdd0`) updated `pageindex/pi_prices.yaml` and `eval/s11/ledger.py` (`0.10` Sonnet cache read, Haiku per-tier cache prices, `19`-field `UsageLogger`), `pi_common.py` and `pi_settings.yaml` (`answer_effort`/`verifier_effort`, `32000`/`600 s`, `16000`/`180 s`), `pi_client.py` (`count_tokens`, `model_refusal`, user-final guard), `pi_agent.py` (shared rules prompt, `.strip()` page blocks, `_VerifierStopError`, shared `verify_and_repair`), `eval/s11/answer.py` (`16`-key write-once records), and `eval/s11/precount.py`, and froze `eval/s11/prompts/answer_rules_v1.txt`.
- Part E (`e937033`) added `eval/s11/hybrid.py` (`retrieval_eval/bm25.py` import-only with numeric `int` page IDs and `score > 0` filter, guarded Azure dense embeddings in batches of `16` with write-once disk cache, `RRF k=60`, empty-page exclusion, `k_for`, `HY5`) and `eval/s11/whole_doc.py` (`116` physical page headers), plus `.gitattributes` LF rules and CRLF normalization in `load_answer_rules()`.
- Windows offline fake tests rose from `2777 passed, 4 skipped, 0 failed` (`S11b1`) to `2819 passed, 4 skipped, 0 failed` after Part E (`+42` new S11b2 test items across `4` test modules; all `4` frozen manifests verified `OK` and `git check-attr eol` confirmed `eol: lf`).

## SELF-AUDIT

1. **Asked vs done**
   - Part A (read-only survey + SDK inspection + `key-names` command): done (reported installed SDK `output_config`, non-streaming guard, `anthropic-version`, `count_tokens`, `OutputTokensDetails.thinking_tokens`, PI prompt lines to remove, PyMuPDF fetch format, and `retrieval_eval/bm25.py` lock/scorer status).
   - Part B (`S11b2-02`..`05`, `-23`, `-24`): done (`eval/s11/golden.py`, `tests/test_s11b2_golden.py`; committed as `8baf1ae`).
   - Part C (freeze A1 + golden): done (`eval/s11/manifests/prereg_addendum_a1.json`, `eval/s11/golden_v1.json`, `eval/s11/manifests/golden_v1.json`; committed as `dd6723c`).
   - Part D (`S11b2-06`..`10`, `-12`..`14`, `-17`..`21`, `-25`): done (`eval/s11/prompts/answer_rules_v1.txt`, `eval/s11/manifests/answer_rules_v1.json`, `eval/s11/answer.py`, `eval/s11/precount.py`, updates to `pageindex/pi_prices.yaml`, `eval/s11/ledger.py`, `pageindex/pi_common.py`, `pageindex/pi_settings.yaml`, `pageindex/pi_client.py`, `pageindex/pi_agent.py`, `tests/test_s11b2_answer.py`, `tests/test_s11b2_client_updates.py`, and updated S11b1 assertions; committed as `b25fdd0`).
   - Part E (`S11b2-11`, `-22`, `-26`): done (`eval/s11/hybrid.py`, `eval/s11/whole_doc.py`, `tests/test_s11b2_baselines.py`, `.gitattributes`, `pageindex/pi_agent.py`, `tests/test_s11b2_answer.py`; committed as `e937033`).
   - Part F (`logs/S11b2a_report.md`): written on `s11b2a/golden-baselines`; commit, push, PR, and CI steps prepared for Hariom.

2. **Decisions I made myself**
   - Placed shared `load_answer_rules`, `format_page_blocks`, `refusal_detected`, `_VerifierStopError`, `verify_and_repair`, and `sum_thinking_tokens` inside `pageindex/pi_agent.py` (re-exported by `eval/s11/answer.py`) to avoid circular imports while keeping standalone `pageindex/pi_agent.py` loading functional.
   - Used a lazy verifier factory in `verify_and_repair` so refusal items create `0` verifier clients as well as `0` verifier API calls.
   - Passed `int` physical page numbers (not `"p001"` strings) into `retrieval_eval.bm25.BM25` so score ties resolve strictly numerically (`2 < 10`), and filtered `score > 0.0` explicitly after ranking all non-empty pages.
   - Keyed write-once dense embedding cache files at `cache_dir / f"{sha256_hex}.json"` (`sha256` of `f"{text_sha256}\0{model}\0{deployment}"`), storing `text_sha256`, `model`, `deployment`, and `embedding` while separating `retrieval_out_dir` / `meta_out_dir` from the fixed `16`-key `RESULT_KEYS` answer file.
   - Preserved `eval/s11/golden_v1.json` top-level `schema="s11-golden-v2"` and added `load_frozen_golden(path, manifest)` verifying the SHA manifest before schema checks.

3. **Deviations**
   - No deviations. `S11b2-24`, `S11b2-25` and `S11b2-26` were approved table additions during the step.

4. **How I implemented it**
   - Created/modified across Parts B–F:
     - `eval/s11/golden.py` — v2 golden checker, `10`-word PDF verbatim check, warning-only claim-term coverage check (`NO_TERMS`), write-once freeze CLI, and `load_frozen_golden` (`8baf1ae`).
     - `tests/test_s11b2_golden.py` — `9` offline synthetic-PDF tests incl. guarded fresh-subprocess import (`8baf1ae`).
     - `eval/s11/manifests/prereg_addendum_a1.json`, `eval/s11/golden_v1.json`, `eval/s11/manifests/golden_v1.json` — frozen addendum A1 and golden v2 dataset + manifest (`dd6723c`).
     - `eval/s11/prompts/answer_rules_v1.txt`, `eval/s11/manifests/answer_rules_v1.json` — shared answer instructions (`848` bytes, LF, frozen SHA-256 `57681cc4...5ec3875`) (`b25fdd0`).
     - `pageindex/pi_prices.yaml`, `eval/s11/ledger.py` — `0.10` Sonnet cache read, Haiku per-tier `cache_read_per_1m` / `cache_write_5m_per_1m`, `19`-field `USAGE_FIELDS` (`effort`, `thinking_tokens`) (`b25fdd0`).
     - `pageindex/pi_common.py`, `pageindex/pi_settings.yaml` — `answer_effort`/`verifier_effort`, `32000`/`600.0` agent and `16000`/`180.0` verifier budgets (`b25fdd0`).
     - `pageindex/pi_client.py` — `_assert_ends_with_user`, `count_tokens` (`cost_usd=0.0`, no reserve), `effort`/`thinking_tokens` logging, and `model_refusal` handling (`b25fdd0`).
     - `pageindex/pi_agent.py` — shared rules prompt assembly (removing old lines `73`, `82`, `83`), shared `format_page_blocks`, `refusal_detected`, `_VerifierStopError`, shared `verify_and_repair`, `output_config`, and CRLF-safe `load_answer_rules()` (`b25fdd0`, `e937033`).
     - `eval/s11/answer.py`, `eval/s11/precount.py` — `HY`/`WD`/`PI` answer runners writing exact `16`-key `RESULT_KEYS` records and exact `WD`/`PI` token pre-counting helper (`b25fdd0`).
     - `.gitattributes`, `eval/s11/hybrid.py`, `eval/s11/whole_doc.py` — S11 `eol=lf` rules, `HY`/`HY5` (`BM25` + guarded `AsyncOpenAI` embeddings in batches of `16` + write-once cache + `RRF k=60` + `k_for`), and `WD` `116`-page builder (`e937033`).
     - `tests/test_s11b2_answer.py`, `tests/test_s11b2_client_updates.py`, `tests/test_s11b2_baselines.py`, plus updated S11b1 assertions in `tests/test_s11b1_fixes.py`, `tests/test_s11b1_ledger.py`, `tests/test_s11b1_client.py` (`b25fdd0`, `e937033`).
     - `logs/S11b2a_report.md` — this completion report (Part F).
   - Commands run by Hariom (Parts A–E): `git switch -c s11b2a/golden-baselines`; key-names probe on `data\s11\golden_draft.json`; `.venv\Scripts\python -m eval.s11.golden check --golden data\s11\golden_draft.json --pdf data\pdfs\guidelines_GTP.pdf`; `git check-ignore -v` on `A1`/`golden_v1.json`/`eval/s11/manifests`; `eval.s11.freeze freeze`/`check` for `prereg_addendum_a1.json` and `answer_rules_v1.json`; `eval.s11.golden freeze`; `git check-attr eol`; all `4` manifest `freeze check` commands; `pytest` runs for Parts B, D, E and `.venv\Scripts\python tasks.py test`; per-Part `git add` + `git commit` (`8baf1ae`, `dd6723c`, `b25fdd0`, `e937033`).

5. **Checks with real output**
   - Baseline before `S11b2a` (`logs/S11b1_report.md`): `2777 passed, 4 skipped, 0 failed`.
   - Part B (`8baf1ae`, Hariom, Windows): `tests/test_s11b2_golden.py` `9 passed`; `eval.s11.golden check` summary on real inputs: `test_answerable=12 test_unanswerable=4 dev_count=3 flags=0 verbatim=PASS`.
   - Part C (`dd6723c`, Hariom, Windows): `prereg_addendum_a1` `sha256_lf=8ea7c71298610db09fae4565d9fa44ffc4fd488f67b4d0aabc65ea58b7279625` (`3466` bytes, `check OK`); `golden_v1.json` `sha256_lf=30163287f0c2eadf48f6253f6eb4884e5693cfc313fee295dd44ae93994031a5` (`14865` bytes, `check OK`); `prereg_v1.json` `check OK` (`05adf4a5308c6feff15fc3a941815da3ca312f628b0ad91f06230e498d88a0b9`, `11678` bytes).
   - Part D (`b25fdd0`, Hariom, Windows): Part D new tests `23 passed`; Part II + S11b1 + S11b2 set `172 passed, 2 skipped`; full suite `2809 passed, 4 skipped, 0 failed`; `answer_rules_v1.txt` freeze `sha256_lf=57681cc4ace83407a4dbc26c6cda08f3409bfebdf5cf449f093e8cb015ec3875` (`848` bytes, `check OK`).
   - Part E (`e937033`, Hariom, Windows): Part E + answer tests `24 passed` (`9` baselines + `15` answer items); Part II + S11b1 + S11b2 set `182 passed, 2 skipped`; full suite `2819 passed, 4 skipped, 0 failed` (`+42` new S11b2 test items over `2777`); `git check-attr eol` returned `eol: lf` for `eval/s11/prompts/answer_rules_v1.txt`, `eval/s11/golden_v1.json`, and `eval/s11/manifests/golden_v1.json`; all `4` manifest checks returned `OK`.

6. **Frozen items**
   - Untouched and verified: everything under `eval/s10/`, all files listed in `eval/s10/manifests/s10c_code_lock.json`, `report.pdf`, and all files under `specs/`. All `4` frozen manifests (`prereg_v1.json`, `prereg_addendum_a1.json`, `golden_v1.json`, `answer_rules_v1.json`) verified `OK`.

7. **Holdouts and sealed files**
   - Before the Parts B–F work summarized here, in one turn on `2026-11-27` (after a message meant for Hariom), the coding agent opened `data\s11\golden_draft.json`, `golden_changes.md`, `golden_draft_backup1.json`, and `data\pdfs\guidelines_GTP.pdf` and ran `11` read-only commands; no file was modified (Hariom checked last-write times), and this event is recorded in pre-registration addendum `A1`. During Parts A–F of `S11b2a`, nothing under `data\` was opened, listed, searched, copied, printed, hashed, or scored by the agent, and no golden text is repeated anywhere in this report or commits.

8. **Safety**
   - Aside from the one turn on `2026-11-27` noted in item `7` (when the coding agent opened `data\s11\golden_draft.json`, `golden_changes.md`, `golden_draft_backup1.json`, and `data\pdfs\guidelines_GTP.pdf` and ran `11` read-only commands with zero file edits, verified by Hariom's last-write-time check and recorded in addendum `A1`), no `.env` was opened, no keys were printed, nothing under `data\` was opened or searched, no commits/pushes/tests/`pip`/`git` were executed by the agent, no files were written outside the repo, no scratch files remain, no AI assistant/tool attribution or names appear anywhere, and no guard/hook blocks were bypassed.

9. **Paid/external calls**
   - `0` calls, USD 0 cost.

10. **Not checked / assumed**
    - Live paid Anthropic (`messages.create` with `output_config`, `messages.count_tokens`) and live Azure (`text-embedding-3-small`) responses are untested until `S11b3`; all checks ran offline in fake mode (`REGCLAW_STUB=1`, `tests/_netguard.py`, `httpx`/`httpx2` `MockTransport`).
    - `.github/workflows/ci.yml` (`ubuntu-latest`) runs after push/PR and skips tests requiring packages omitted from CI (`litellm`, `ragas`, `PyPDF2`, `pageindex`); Windows `.venv` ran all `2819` passing tests.

11. **Errors and retries**
    - `2026-11-27` routing incident (prior to freeze): after a message meant for Hariom, opened `data\s11\golden_draft.json`, `golden_changes.md`, `golden_draft_backup1.json`, and `data\pdfs\guidelines_GTP.pdf` and ran `11` read-only commands; `0` files modified (Hariom verified last-write times); disclosed in addendum `A1`.
    - Part B import error: `tests/test_s11b2_golden.py` failed collection with `ModuleNotFoundError: No module named 'pageindex.pi_common'` because repo `pageindex/` has no `__init__.py` while installed `pageindex` does -> fixed `eval/s11/golden.py` to prepend `ROOT` and `ROOT / "pageindex"` to `sys.path` and added fresh-subprocess import tests across `golden`, `answer`, `precount`, `hybrid`, and `whole_doc`.
    - Part B test syntax error: initial one-line `-c` `guarded_code` string failed (`8 passed, 1 failed`, `SyntaxError` on `def` after `;`) -> replaced with `textwrap.dedent` multi-line string (`9 passed`).
    - Part D rules SHA stop: initial specification rules text summed to `834` LF bytes vs `848` bytes for target SHA-256 `57681cc4...5ec3875` (`14` bytes omitted from markdown `1.`–`5.` numbering and blank line when pasted) -> paused before creating files, obtained exact `848`-byte literal from Hariom, and proceeded cleanly.
    - Part D test run (`21 passed, 2 failed`): shallow `messages` reference in `_ScriptedAnthropic` was mutated after `run_agent` returned, and empty-messages error regex searched `"end with role='user'"` instead of `"role='user'"` -> updated fakes to store `copy.deepcopy(kwargs["messages"])` at call time and matched `"role='user'"` (`23 passed`).

12. **Risks for later steps + what I need from Hariom**
    - Known technical limits carried forward are listed below under *Known Limits and Risks for Later Steps*.
    - Need from Hariom: stage and commit `logs/S11b2a_report.md`, push `s11b2a/golden-baselines`, open the PR, confirm GitHub CI plus Windows full suite (`2819 passed, 4 skipped, 0 failed`), and merge only after the working chat's OK.

---

## Decisions (`S11b2-01`..`26`: `decision -> file -> test`)

| ID | Summary | Files | Verifying tests |
|---|---|---|---|
| `S11b2-01` | Split S11b2 into `b2a` / `b2b` / `b2c` *(process, no code)* | — | — |
| `S11b2-02` | Second check by AI instead of a human -> pre-registration addendum A1, frozen by sha | `eval/s11/manifests/prereg_addendum_a1.json` | none (freeze check output in Part C) |
| `S11b2-03` | No grader notes in the frozen golden file; unknown fields refused | `eval/s11/golden.py` | `tests/test_s11b2_golden.py::test_schema_rejects_unknown_top_fields_and_invalid_corpus_status` |
| `S11b2-04` | Warning-only claim-term text-layer share (`< 0.50` flag, `--show-terms`) | `eval/s11/golden.py` | `tests/test_s11b2_golden.py::test_text_layer_hand_example_flags_no_terms_and_show_terms` |
| `S11b2-05` | Write-once freeze mechanics incl. schema checks and `10`-word PDF verbatim check -> `eval/s11/golden_v1.json` + manifest + `load_frozen_golden` | `eval/s11/golden.py`, `eval/s11/golden_v1.json`, `eval/s11/manifests/golden_v1.json` | `tests/test_s11b2_golden.py::test_schema_rejects_item_rules_bools_counts_and_page_bounds`, `test_pdf_sha_mismatch_refused`, `test_verbatim_10_word_run_fails_and_9_word_passes_without_printing_text`, `test_freeze_write_once_format_sorted_ids_no_extra_and_load_frozen`, `test_freeze_refuses_on_verbatim_or_schema_failure_without_writing` |
| `S11b2-06` | `0.10` Sonnet cache read; Haiku per-tier cache read/write rates (warning removed) | `pageindex/pi_prices.yaml`, `eval/s11/ledger.py`, `tests/test_s11b1_ledger.py`, `tests/test_s11b1_client.py` | `tests/test_s11b2_client_updates.py::test_prices_sonnet_cache_read_and_haiku_cache_tiers` |
| `S11b2-07` | `answer_effort` (`medium`/`high`) & fixed `verifier_effort` (`medium`) via `output_config` | `pageindex/pi_common.py`, `pageindex/pi_settings.yaml`, `pageindex/pi_agent.py`, `eval/s11/answer.py` | `tests/test_s11b2_client_updates.py::test_effort_settings_validation_and_sent_in_request_bodies` |
| `S11b2-08` | No `thinking` field; verbatim ordered `resp.content` block pass-back | `pageindex/pi_agent.py`, `eval/s11/answer.py` | `tests/test_s11b2_client_updates.py::test_no_thinking_key_and_no_sonnet_tool_choice_any_or_tool`, `test_pi_agent_passes_content_blocks_verbatim_and_in_order` |
| `S11b2-09` | No Sonnet `tool_choice` `"any"`/`"tool"` or `thinking`; user-final `messages` guard | `pageindex/pi_client.py`, `pageindex/pi_agent.py`, `eval/s11/answer.py` | `tests/test_s11b2_client_updates.py::test_no_thinking_key_and_no_sonnet_tool_choice_any_or_tool`, `test_assistant_prefill_rejected_before_any_anthropic_call`, `tests/test_s11b2_answer.py::test_hy_and_wd_request_shapes_and_pi_prompt_order` |
| `S11b2-10` | Shared `answer_rules_v1.txt`, PI lines `73/82/83` removed, shared `format_page_blocks`, `HY`/`WD` shapes, `sys.path` subprocess imports | `eval/s11/prompts/answer_rules_v1.txt`, `eval/s11/manifests/answer_rules_v1.json`, `pageindex/pi_agent.py`, `eval/s11/answer.py`, `eval/s11/precount.py`, `eval/s11/golden.py` | `tests/test_s11b2_answer.py::test_answer_rules_file_sha_length_lf_and_no_trailing_spaces`, `test_hy_and_wd_request_shapes_and_pi_prompt_order`, `test_fresh_subprocess_import_answer_from_repo_root`, `tests/test_s11b2_client_updates.py::test_fresh_subprocess_import_precount_from_repo_root`, `tests/test_s11b2_golden.py::test_fresh_subprocess_import_golden_from_repo_root`, `tests/test_s11b2_baselines.py::test_fresh_subprocess_import_hybrid_and_whole_doc_from_repo_root` |
| `S11b2-11` | `HY`/`HY5`/`WD`: repo `BM25`, guarded embeddings (`batch=16`, write-once cache), `RRF k=60`, empty-page exclusion, `k_for`, `HY5`, `WD` `116` blocks | `eval/s11/hybrid.py`, `eval/s11/whole_doc.py` | `tests/test_s11b2_baselines.py::test_rrf_hand_computed_two_list_example_and_missing_bm25_page`, `test_empty_pages_excluded_from_retrieval_and_listed_in_meta`, `test_k_for_budget_rules`, `test_embedding_cache_write_once_reused_zero_calls_and_batches_of_16`, `test_guarded_async_openai_embeddings_logs_and_checks_model_version`, `test_hy5_is_first_5_of_same_ranking_and_retrieval_only`, `test_hy_end_to_end_matched_budget_and_wd_116_page_blocks_with_empty_headers` |
| `S11b2-12` | `pi_client` `count_tokens` (`cost=0.0`, no reserve) + `eval/s11/precount.py` | `pageindex/pi_client.py`, `eval/s11/precount.py` | `tests/test_s11b2_client_updates.py::test_count_tokens_logs_one_line_cost_zero_no_reserve_and_precount_requests` |
| `S11b2-13` | Cache write/read tokens logged per call (`0` miss does not fail) | `pageindex/pi_client.py`, `eval/s11/answer.py` | `tests/test_s11b2_client_updates.py::test_usage_logger_19_fields_effort_and_thinking_tokens` |
| `S11b2-14` | SDK default `anthropic-version` (`"2023-06-01"`) verified | `anthropic/_constants.py:7`, `anthropic/_client.py:407` | none (Part A read only) |
| `S11b2-15` | Branch `s11b2a/golden-baselines`, one commit per Part | Git history (`8baf1ae`, `dd6723c`, `b25fdd0`, `e937033`) | — |
| `S11b2-16` | Coding workflow choice *(process, no code)* | — | — |
| `S11b2-17` | `32000`/`600 s` agent, `16000`/`180 s` verifier, `32000` tree reserve / `180 s` judge unchanged, non-streaming SDK check | `pageindex/pi_common.py`, `pageindex/pi_settings.yaml`, `tests/test_s11b1_fixes.py` | `tests/test_s11b2_client_updates.py::test_nonstreaming_max_tokens_32000_with_explicit_timeout_600_allowed_by_real_sdk`, `tests/test_s11b1_fixes.py::test_timeout_and_max_token_settings_defaults_and_validation` |
| `S11b2-18` | `refusal_detected`, refusal skips check (`check_skipped_refusal`), shared `verify_and_repair` + pre/post-repair + `max_tokens` failures | `pageindex/pi_agent.py`, `eval/s11/answer.py` | `tests/test_s11b2_answer.py::test_refusal_detected_cases`, `test_refusal_skips_haiku_check_across_pi_hy_wd`, `test_max_tokens_and_model_refusal_fail_immediately_without_retry` |
| `S11b2-19` | Exact `16`-key write-once result record, `usage` (`answer`/`verifier`/`total`, summed/`null` `thinking_tokens`), `effort` | `eval/s11/answer.py` | `tests/test_s11b2_answer.py::test_shared_check_repair_drop_thinking_sum_and_exact_16_key_write_once_record` |
| `S11b2-20` | `UsageLogger` `19` fields (`effort`, `thinking_tokens` `None` when absent) | `eval/s11/ledger.py`, `pageindex/pi_client.py` | `tests/test_s11b2_client_updates.py::test_usage_logger_19_fields_effort_and_thinking_tokens` |
| `S11b2-21` | `stop_reason == "refusal"` fails item as `status="model_refusal"` (no retry, logged in result & `usage.jsonl`) | `pageindex/pi_client.py`, `pageindex/pi_agent.py`, `eval/s11/answer.py` | `tests/test_s11b2_answer.py::test_max_tokens_and_model_refusal_fail_immediately_without_retry` |
| `S11b2-22` | `BM25` list holds only `score > 0` pages; numeric `int` page IDs for tie breaking | `eval/s11/hybrid.py` | `tests/test_s11b2_baselines.py::test_bm25_uses_repo_scorer_numeric_ties_and_positive_only`, `test_rrf_hand_computed_two_list_example_and_missing_bm25_page` |
| `S11b2-23` | Exact `page_convention` string and `NO_TERMS FLAG` for claims with `0` checkable terms | `eval/s11/golden.py` | `tests/test_s11b2_golden.py::test_schema_rejects_unknown_top_fields_and_invalid_corpus_status`, `test_text_layer_hand_example_flags_no_terms_and_show_terms` |
| `S11b2-24` | Draft top-level `"corpus"` / `"status"` strings allowed/validated, never printed or frozen | `eval/s11/golden.py` | `tests/test_s11b2_golden.py::test_valid_draft_with_corpus_and_status_passes_without_printing_them`, `test_freeze_write_once_format_sorted_ids_no_extra_and_load_frozen` |
| `S11b2-25` | `_VerifierStopError` on verifier `stop_reason` `"max_tokens"` / `"refusal"` inside `verify_with_usage` -> failed item | `pageindex/pi_agent.py`, `eval/s11/answer.py` | `tests/test_s11b2_answer.py::test_max_tokens_and_model_refusal_fail_immediately_without_retry` |
| `S11b2-26` | `.gitattributes` `text eol=lf` for S11 prompts/golden/manifests and CRLF normalization in `load_answer_rules()` | `.gitattributes`, `pageindex/pi_agent.py` | `tests/test_s11b2_answer.py::test_load_answer_rules_normalizes_crlf_copy` |

---

## Part A SDK and Prompt Facts (`anthropic 1.14.0` + `pi_agent.py`)

- **`output_config` path:** accepted as a typed keyword argument in `Messages.create` (`.venv/Lib/site-packages/anthropic/resources/messages/messages.py:1056`), typed by `OutputConfigParam` (`.venv/Lib/site-packages/anthropic/types/output_config_param.py:11-18`) with `effort: Optional[Literal["low", "medium", "high", "xhigh", "max"]]`.
- **Non-streaming `max_tokens` guard:** `Messages.create` (`.venv/Lib/site-packages/anthropic/resources/messages/messages.py:1017-1020`) only invokes `_calculate_nonstreaming_timeout` (`.venv/Lib/site-packages/anthropic/_base_client.py:854-867`) when `not stream and not is_given(timeout) and self._client.timeout == DEFAULT_TIMEOUT`. Supplying explicit `timeout=600.0` bypasses the guard, so non-streaming `max_tokens=32000` is permitted (also verified via real `anthropic.Anthropic` + `httpx2.MockTransport` under `_netguard`).
- **`anthropic-version` header:** constant `ANTHROPIC_API_VERSION = "2023-06-01"` (`.venv/Lib/site-packages/anthropic/_constants.py:7`), attached via `default_headers` (`.venv/Lib/site-packages/anthropic/_client.py:407`).
- **`count_tokens` & `thinking_tokens`:** `Messages.count_tokens` (`.venv/Lib/site-packages/anthropic/resources/messages/messages.py:1319-1339`) accepts `messages`, `model`, `system`, `tools`, `tool_choice`, `output_config`, and `timeout`; `OutputTokensDetails.thinking_tokens: int` (`.venv/Lib/site-packages/anthropic/types/output_tokens_details.py:6-7`) carries reasoning token counts when reported.
- **PI prompt lines removed (`pageindex/pi_agent.py`):** old `SYSTEM_TMPL` line `73` (`"You answer questions about one regulatory document using ONLY its text."`), line `82` (`"4. Answer concisely. ... Cite only pages you actually fetched."`), and line `83` (`"5. If the fetched text does not answer the question, say so plainly. Never use outside knowledge to fill gaps."`), replaced in `build_pi_system_text` by `answer_rules_v1.txt` + blank line + navigation lines (`Document:`, `Description:`, `How to work: 1..3`) + blank line + `Outline:`.

---

## Golden Check Summary and Frozen Manifest SHAs

- **Golden check output (Hariom's run on the frozen dataset):** `test_answerable=12 test_unanswerable=4 dev_count=3 flags=0 verbatim=PASS`.
- The second check of gold pages and a later claim-wording review involved AI; recorded in addendum A1.

| Manifest | Target file | `sha256_lf` | `bytes_lf` | `freeze check` |
|---|---|---|---|---|
| `eval/s11/manifests/prereg_v1.json` | `specs/S11_PREREGISTRATION.md` | `05adf4a5308c6feff15fc3a941815da3ca312f628b0ad91f06230e498d88a0b9` | `11678` | `OK` |
| `eval/s11/manifests/prereg_addendum_a1.json` | `specs/S11_PREREG_ADDENDUM_A1.md` | `8ea7c71298610db09fae4565d9fa44ffc4fd488f67b4d0aabc65ea58b7279625` | `3466` | `OK` |
| `eval/s11/manifests/golden_v1.json` | `eval/s11/golden_v1.json` | `30163287f0c2eadf48f6253f6eb4884e5693cfc313fee295dd44ae93994031a5` | `14865` | `OK` |
| `eval/s11/manifests/answer_rules_v1.json` | `eval/s11/prompts/answer_rules_v1.txt` | `57681cc4ace83407a4dbc26c6cda08f3409bfebdf5cf449f093e8cb015ec3875` | `848` | `OK` |

---

## Known Limits and Risks for Later Steps

- **Line-break hyphenation in PyMuPDF extraction:** hard `10`-word verbatim checks can miss a split hyphenated run across line breaks (false negatives only; accepted limit).
- **Tree-build token cap (`litellm_max_tokens: 16000`):** Sonnet adaptive thinking counts inside `max_tokens`, leaving less room for large JSON tree responses; verify headroom in `S11b3` before building the production tree.
- **`asyncio.run` inside `eval/s11/hybrid.py`:** `load_or_compute_embeddings`, `rank_hybrid_pages`, `run_hy5_retrieval`, and `run_hy` invoke `asyncio.run(...)` internally, so the `S11b2c` driver must call them synchronously outside any active event loop.
- **Forced `tool_choice` on verifier:** the Haiku citation check retains `tool_choice={"type": "tool", "name": "report_verdicts"}`; `output_config={"effort": "medium"}` + forced `tool_choice` is verified only for Haiku (never used on Sonnet).

---

## Left for S11b2b / S11b2c / S11b3

- **`S11b2b` (offline evaluation code):** metrics on `R` and `C`, citation checks `C1–C5`, Ragas input assembly, and tree audit tool (code only).
- **`S11b2c` (run orchestration & analysis code):** end-to-end run driver, blind grading export/import, `40`-statement judge-validation sampler, cached re-judging, and statistical comparisons.
- **`S11b3` (live runs):** token pre-count via `precount.py`, tree build + structural audit (`max 2` builds), `dev` pilot (`3` questions to lock `answer_effort` `medium` vs `high`), `3` main runs, and judging.
