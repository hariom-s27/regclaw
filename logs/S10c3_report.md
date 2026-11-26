# S10c3 Dev Results Reporting Report

Date: 2026-11-26  
Branch: `s10c3/results`

## Plain Summary

- What we tested: whether the LLM layer of `get_applicable_standards` (final = max(rule, LLM)) catches mandatory ISO 10993 parts that the rules miss. Dev set: 8 strata profiles (2 trap profiles reported separately), 2 efforts × 5 repeats; classifier Azure gpt-5-mini; provisional (GPT), dev only.
- The tool still misses mandatory parts: under-call U/P = 0.060 (25/420) at medium (tipping range at E1: [0.037, 0.081]; at E2: [0.013, 0.102]) and 0.057 (24/420) at high (tipping range at E1: [0.034, 0.079]; at E2: [0.010, 0.100]), against 0.060 (25/420) for the rules alone.
- The misses sit in judgement items: rules-only under-call is 0.625 on judgement items and 0.000 on matrix_settled and 0.000 on always_rule items.
- The LLM barely helps and adds over-calls: it removed 0/420 misses at medium and 1/420 at high (a reduction of 0 or more is guaranteed by design). Paired difference high − medium = -0.002 [-0.007, 0.000] (approximate (few profiles)), consistent with no difference / a difference of up to 0.007; exact sign-flip p = 1. Over-call rises by 0.011 [0.002, 0.023] at medium and 0.095 [0.040, 0.170] at high (approximate (few profiles) intervals).
- Effort rule (S10P-14): tier 3 → MEDIUM, the same under all 6 reference versions. The holdout has not been run; it stays sealed until Final, after a Claude rerun.

## Verification Outputs (Run by Hariom)

- Stray-number check (`scripts/s10c3_render_results.py check`): `check passed: 0 stray numbers in rendered outputs` (0 strays).
- S10c3 render test suite (`tests/test_s10c3_render.py`): `12 passed`.
- Full repository test suite (`tasks.py test`): `2725 passed / 2 skipped`.

## Deliverables

- `scripts/s10c3_render_results.py`: Deterministic `extract`, `render`, and `check` subcommands enforcing strict required-key validation (`req(...)`), explicit `OPTIONAL_FIELDS`, normalized `-0.0 -> 0.000`, 3-decimal formatting, integer denominator-1 p-values, scope checks across all 100 runs (strata + traps), exact robustness-band ordering, and stray-number checking.
- `scripts/s10c3_results_template.md`: Full standalone report template rendered to `docs/results_s10c_dev.md`.
- `scripts/s10c3_results_block_template.md`: Summary block template rendered between `<!-- S10c:start -->` and `<!-- S10c:end -->` in `docs/results.md`.
- `scripts/s10c3_number_allowlist.json`: Context-anchored non-analysis constants allowlist with line-start regex for numbered lists and documented sources.
- `tests/test_s10c3_render.py`: Offline test suite covering SHA verification, write-once refusal, missing-key refusal on extract and render, non-ok scope refusal, difference direction labels, determinism, marker preservation, line-start list vs stray decimal checks, template resolution, and LF-normalized committed re-render parity.
- `docs/results_s10c_dev.numbers.json`: Write-once extracted metrics and SHA receipts from committed manifests and analysis output.
- `docs/results_s10c_dev.md` and `docs/results.md`: Verified rendered dev results outputs.

## Self-Audit

1. **Workspace boundary check**: All repository edits (`scripts/s10c3_render_results.py`, `scripts/s10c3_results_template.md`, `scripts/s10c3_results_block_template.md`, `scripts/s10c3_number_allowlist.json`, `tests/test_s10c3_render.py`, `docs/engineering_journal.md`, `docs/results.md`, `docs/results_s10c_dev.md`, `docs/results_s10c_dev.numbers.json`, and `logs/S10c3_report.md`) remain strictly inside `d:\sem_iitk\sem9\meril`. Earlier read-only scratch scripts created outside the repo during inspection (`inspect_numbers.py`, `inspect_structure.py`, `search_fractions.py`) were flagged in the audit correction and deleted by Hariom after explicit approval.
2. **Secrets and credential safety**: `.env` was never opened, searched, or printed; zero API keys or credentials appear in code, docs, logs, or commits.
3. **Data directory guard**: Nothing under `data/` was opened, listed, searched, printed, copied, or hashed by the coding workflow; all real-data `extract` commands were executed directly by Hariom. Final S10c3 tests (`12 passed`), full suite (`2725 passed / 2 skipped`), and `check` (`0 strays`) were executed by Hariom. (An earlier sandboxed fake-test run and background full-suite invocation during Part B were corrected and halted after review.)
4. **Code lock & manifest protection**: Zero edits to `eval/s10/manifests/`, `specs/`, or any of the 58 files listed in `eval/s10/manifests/s10c_code_lock.json` (lock v4).
5. **Untracked .ots protection**: `eval/s10/manifests/seal2/backup_s10_friend_sha256.txt.ots` was never read, modified, or included in staging commands.
6. **Git write command restrictions**: Zero git write commands (`git add`, `git commit`, `git push`, `git checkout -b`) were executed automatically; exact single-line stage and commit commands are provided for Hariom.
7. **PowerShell safety & placeholders**: Every command issued to Hariom uses single-line PowerShell-safe syntax without nested double quotes, one command per code block, with exact file paths and exact hashes.
8. **AI provenance trailer exclusion**: Zero provenance trailers, attribution lines, or non-human authorship references appear in code, comments, templates, docs, reports, or commit messages.
9. **Source citation of every reported number**: Every metric, fraction, CI, count, and SHA reported in `docs/results_s10c_dev.md`, `docs/results.md`, and this report traces directly to `docs/results_s10c_dev.numbers.json`, committed receipts under `eval/s10/manifests/`, or Hariom's verified terminal outputs.
10. **Approved scope adherence**: Implemented only pre-registered/approved Part A–D specifications and explicit word-for-word revisions (Q1 disagreement/upgrade wording, Q2 tag-count handling, bullet formatting, Family overlap note, and PPI fixed-line phrasing).
11. **Background task & daemon audit**: No background tasks or daemons remain running.
12. **Stop rule adherence**: Stopped at every checkpoint for review/approval and prepared final report and commit commands for Hariom.
