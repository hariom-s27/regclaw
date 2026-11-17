# S10b2h report

## PLAIN SUMMARY

Result: short-interval consistency (S10P-23, deviation A3); a gross-error check.
Agreement was 30/30; kappa was 1.0000 with registered bootstrap CI [1.0000, 1.0000] (intra_rater.json; retest_a3_companions.json).
There were 0 changes; the exact one-sided 95% upper bound on the change rate was 0.0950 (retest_a3_companions.json).
Recognition gave no signal: all 60 answers were 'unsure'; memory was not measured, so kappa remains an upper bound (retest_a3_companions.json).
The 7-day filler follow-up, from about 24 Nov, is pending.

## SELF-AUDIT

### 1. Asked vs done

- Part C report: done. This file contains the five-line summary, Willroth deviation row, supplied results with source identifiers, exact Q6 wording, limitations and provenance.
- Requested revisions: done. Full supplied SHAs are recorded; file names are plain text; PR-37 diagnostics are separated from the retest results.
- Read-back and final Git status: validation commands are listed below; their observed results accompany the delivery STOP.

### 2. Decisions I made myself

- Used tables for parallel aggregates and provenance; rejected continuous prose because the tables make transcription easier to check.
- Kept all supplied precision in the results tables and preserved the separate Q6 paragraph exactly, including its median of 38.4 hours and percentage of 9.5%.
- Used only Hariom's supplied aggregates and provenance. Rejected opening real output files because that is prohibited for this task.

### 3. Deviations

No deviations from the revised Part C instructions. The study-design deviation A3 is disclosed separately below.

### 4. How I implemented it

- Created logs/S10b2h_report.md only. No functions, code, tests, manifests or existing reports were changed.
- Read-only validation commands:

```powershell
Test-Path -LiteralPath logs/S10b2h_report.md
git status --short
Get-Content -Encoding UTF8 -LiteralPath logs/S10b2h_report.md
git status --short
```

### 5. Checks with real output

- Before writing: Test-Path returned False and git status --short returned no entries; both commands exited 0.
- Tests executed in this documentation part: 0. Before/new/total/failed/skipped suite counts were not remeasured; no pass or skip counts are inferred.
- Part A's gate pass and commit e59b6d8 were reported by Hariom earlier; test totals were not supplied here.
- Result counts, intervals and SHAs below are transcribed from Hariom's supplied aggregates. The real output files were not opened or independently hashed for this report.

### 6. Frozen items

- No existing files were edited. The code locks, preregistration/build/codebook freezes, A2/A3 manifests, label manifests, judge_kit.py, judge_config.yaml, label_tool.py and friend_tool.py remain untouched by this part.
- The revised Part C instructions explicitly authorized report creation without another plan STOP. No draw, session, freeze or analysis command was run.

### 7. Holdouts and sealed files

None opened, printed, copied, hashed or scored. No real file under data/gold/s10 was read. No access to the protected map folder, S8 sealed material, external S8 directories or backup directory occurred.

### 8. Safety

No commit, push, deletion, key printing or .env access. No file outside the repository was written. No authorship attribution was added. No guard, hook or permission block occurred in this part.

### 9. Paid/external calls

0 paid/external calls; cost 0. Local report editing and read-only validation only.

### 10. Not checked / assumed

- Full source hashes, output contents, freeze chronology, pushed commits and CI outcomes are Hariom-provided provenance; they were not independently verified here.
- No temporal-stability, memory or correctness conclusion is inferred from perfect short-interval agreement.
- The filler follow-up is pending; there are no follow-up results in this report.

### 11. Errors and retries

None in this report-writing part. Earlier B2 file-not-found attempts were reported at their respective STOPs; they did not supply evidence for the results here.

### 12. Risks for later steps + what I need from Hariom

- Preserve the distinction between short-interval consistency and stability over time or correctness.
- Hariom reviews and commits this report. The 7-day filler follow-up, from about 24 Nov, remains pending.
- A later second-seal step still needs its separate split_guard/file-list review. S10c still needs its own research gate before any paid call; this report does not authorize either step.

## Deviation-table row (S10b2h-23; Willroth format)

| No. | Type | Reason | Timing | Original wording | Change | Reader impact |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Study design (timing of a repeated measurement) | Schedule (researcher's choice, decision P-27 b) | Before any retest label existed (A3 frozen 2026-11-17T14:39:31Z, pushed in c8926e7) | "re-labelled blind >= 7 days after the first pass" (S10P-23, A1-3) | minimum gap 1 day; observed 38.19-38.63 h; same 30 cells (locked draw); 30 fillers + recognition question added | Recall can only raise agreement; result is a gross-error check, kappa an upper bound. |

Timing and commit provenance in this row are supplied by Hariom. Observed gaps are from retest_a3_companions.json; full source identifiers appear below.

## Results

### Retest: short-interval consistency (S10P-23, deviation A3)

This is a gross-error check. Source: intra_rater.json, as supplied by Hariom.

| Metric | Result |
| --- | --- |
| Binary kappa | 1.0000 |
| Registered bootstrap CI | [1.0000, 1.0000] |
| Cells | 30 |
| Undefined bootstrap replicates | 0 |

Source: retest_a3_companions.json, as supplied by Hariom.

| Metric | Result |
| --- | --- |
| Raw binary agreement | 30/30 |
| Changes | 0 |
| Exact one-sided 95% upper bound on the change rate | 0.0950 |
| Gap hours: n | 30 |
| Gap hours: minimum | 38.19 |
| Gap hours: median | 38.44 |
| Gap hours: maximum | 38.63 |
| One-sided Fisher exact p for recognition 'yes', repeats > fillers | 1.0 |
| Undefined-kappa note | none |

Recognition counts, from retest_a3_companions.json:

| Group | yes | no | unsure |
| --- | --- | --- | --- |
| Repeats | 0 | 0 | 30 |
| Fillers | 0 | 0 | 30 |

Recognition gave no signal: all 60 answers were 'unsure'. Memory was not measured; these answers do not establish that recall was absent. Kappa therefore remains an upper bound on independent retest reliability.

### Pre-registered S10b diagnostics (PR-37), separate from the retest

Source: c_position.json, as supplied by Hariom. These counts concern C matching A/B at each batch position.

| Batch position | C equal to A/B: cells | Share |
| --- | --- | --- |
| 1 | 121 | 1.000 |
| 2 | 119 | 1.000 |
| 3 | 119 | 1.000 |
| 4 | 121 | 1.000 |
| 5 | 119 | 1.000 |

Source: stability.json, as supplied by Hariom. The registered metric is self-agreement under reversed item order.

| Judge | Identical | Share |
| --- | --- | --- |
| A | 60/60 | 1.000 |
| B | 60/60 | 1.000 |
| C | 60/60 | 1.000 |

These are the pre-registered S10b diagnostics (PR-37), not additional human retest observations. They do not establish temporal stability or correctness of the human annotations.

## R30 Q6 wording

Intra-rater consistency was checked on the 30 pre-registered seeded cells (S10P-23). The retest ran a median 38.4 hours after the first pass instead of the registered minimum of 7 days, for scheduling reasons, recorded in addendum A3 and frozen on 17 Nov 2026 before any retest label existed. The rater was blind to the first-pass labels, and the 30 cells were mixed with 30 never-labelled filler cells. Agreement was 30 of 30 (binary kappa 1.0, registered bootstrap interval [1.0, 1.0]; exact one-sided 95% upper bound on the change rate 9.5%). A recognition question gave no signal (all 60 answers 'unsure'), so memory was not measured. This shows no gross inconsistency; it is not evidence of stability over time or of correctness.

## Where it fails

- The observed interval was 38.19-38.63 hours, not the registered minimum of 7 days. Recall can raise agreement; the result is a gross-error check and kappa remains an upper bound.
- All 60 recognition answers were 'unsure'. Recognition supplied no signal and memory was not measured; blindness to the first-pass display does not establish absence of recall.
- No changes among 30 cells does not establish a zero change rate. The exact one-sided 95% upper bound remains 0.0950 (9.5%).
- The registered bootstrap CI [1.0000, 1.0000] describes the observed agreement under that bootstrap. It does not resolve recall, extend the time interval or establish correctness.
- The PR-37 position and reversed-order diagnostics address their registered checks. They do not substitute for an independent human retest over the registered interval.
- The 7-day filler follow-up, from about 24 Nov, is pending. It must be reported separately as exploratory 7-day retest on fillers (A3).

Numerical sources for these limitations are intra_rater.json and retest_a3_companions.json, plus the supplied PR-37 aggregates. The follow-up status is supplied by Hariom.

## Sources and provenance

All results and provenance below are supplied by Hariom as aggregates and identifiers. The real data files were not opened or hashed for this report. File names are plain text; the SHA column distinguishes file hashes from the module hash.

| Source | Supplied SHA256 LF |
| --- | --- |
| data/gold/s10/diagnostics/intra_rater.json | 8c7a552d511eab070393382d0a704486acae19d662dd0a16eaed9d0c91c37c6c |
| data/gold/s10/diagnostics/retest_a3_companions.json | 705fcab0f0f77dca5928f9311c03e9213a4460c3e28f7999c6ffc6c73df4ab67 |
| data/gold/s10/diagnostics/c_position.json | 7507080fae7723807a25f2d704e74f82e59927c9d35e5ee92f859f13a723bc49 |
| data/gold/s10/diagnostics/stability.json | 85f3f639cf7a0c82c804700a538f64baa82a1137a7caa8f18c8d744f73460c5f |
| eval/s10/manifests/retest_session_v1.json | caa8d7a1b0f970e143f46e0102a08406dfaff364eb4dc9ed459a1a928a3a7b2b |
| data/gold/s10/draws/retest.json | 4b855c198bac3d82b731e3907e15b87564b589a4caffb1dcf4e43bcd2e16ece4 |
| data/gold/s10/draws/retest_fillers.json | 9ef0be5dc570fcd54081ae1268be5ef9686a72104a46db5873d8b03d84c8dfa8 |
| specs/S10_PREREG_ADDENDUM_A3.md (A3 text) | 33bd04d17d87e5d53943d6c42d471fda3d572805c01316bdbc8dabdae979bbff |
| eval/s10/retest_a3.py (lock v3 module, not lock-manifest hash) | 5f4c2285e6199d854f2d79ae57f807d2efc6b9776464d2704737394a1b8ba07d |

Provenance line supplied by Hariom: lock 14:38:40Z, A3 14:39:31Z (c8926e7, CI #134 green); session freeze 7385eb1 (CI #135 green) before analysis.

The A3 freeze timestamp is 2026-11-17T14:39:31Z. Hariom reports the session manifest was committed in 7385eb1 and pushed before analysis.
