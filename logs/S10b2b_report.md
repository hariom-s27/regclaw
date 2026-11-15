# S10b2b: agreement stats, merge, gate, draws, sheets, diagnostics, code lock, codebook v1.0 freeze

## PLAIN SUMMARY
1. Built the S10b analysis code on fakes only: own-code agreement statistics, the jury merge with boundary routing and property checks, and the audit-A gate (looks 150/250/350, profile-cluster lower bound at 0.0083).
2. Built the seeded draws (audit B, retest, stability set, judge C batches), blind audit-B/retest sheets, referee pass 1/2 sheets with blind-label reuse and a pass-1 lock, and the descriptive diagnostics (C position, stability self-agreement, intra-rater kappa).
3. Extended judge_kit with the eval round (C batches from the draw) and the stability round (reversed main-pass order, C 3 + 3), and let judge_runner run both; practice/smoke kits are byte-identical.
4. Locked the 9 analysis modules in eval/s10/manifests/s10b2b_code_lock.json and froze codebook v1.0 + judge setup in eval/s10/manifests/codebook_v1.json (both write-once; second runs refused).
5. No real draw, judge run, merge or gate was run and no file under data/gold/s10/ was opened; full suite 1579 passed, 1 skipped (before this step: 1433 passed, 1 skipped).

## SELF-AUDIT (whole S10b2b; details per Part below)
1. Asked vs done:
   - Part A (stats, merge, property checks, gate + CLIs): done; commit 49f4112.
   - Part B (draws, sheets, diagnostics, judge_kit eval/stability rounds, judge_runner rounds): done; commit c34402d.
   - Pre-lock fixes asked by Hariom (judge/model/vendor name masking with versions): done; commit 216f3aa.
   - Part C (code lock script, then lock on "yes lock"): done; commits 2c96391 (script) and 16eae54 (manifest).
   - Part D (texts placed by Hariom, shas printed, freeze on "yes freeze"): done; commit a8f4944.
   - Part E (this report + final full suite): done; 1579 passed, 1 skipped, 0 failed (Tests table).
2. Decisions I made myself (Hariom accepted the Part A and B lists at each STOP):
   - merge CLI writes nothing on STOP (> 200 routed), exit 4;
   - every CLI sha-checks its inputs against s10b1_build.json (judge file, audit list) and the merge file's judge-file sha;
   - the gate stores the sha of the human-label prefix per look and refuses to reuse a look file if those labels changed;
   - exit codes: merge 0/2/3/4, gate 0/2/4/5, draws/sheets/diagnostics 0/1/2/3;
   - the gate lower bound reuses metrics.bootstrap_ci unchanged, with alpha = 2 x 0.0083;
   - the gate CLI prints aggregate gate numbers; secondary rows go to the file only;
   - clean_reason: NA masked upper-case only; arrows written " → ";
   - referee label still required when unsure_guideline_gap = yes;
   - referee pass 1 waits for all audit-B labels;
   - pass 2 does not show the pass-1 label;
   - pass 2 computes all reasons before writing the lock;
   - intra-rater refuses re-labels time-stamped before the earliest date;
   - a missing C label counts in the C-position denominator (c_missing reported);
   - code_lock: --show and --expected-sha are mutually exclusive.
3. Deviations:
   - Part D and Part E had no separate PLAN / "OK plan". Their steps were fixed in the prompt; Part D's printing step was read-only and both write-once steps waited for "yes freeze" / your instruction; Part E started on "continue".
   - In test_s10b2b_sheets.py::test_name_masking_cases, the case 'gpt4 stays' (expected unchanged) was replaced by 'astral; gpts stay', because Hariom's extended rule makes gpt4 -> [judge].
   - Otherwise no deviations.
4. How I implemented it (files; exact commands are in the Parts):
   - New modules:
     - eval/s10/agreement.py
     - eval/s10/merge.py
     - eval/s10/gate.py
     - eval/s10/draws.py
     - eval/s10/sheets.py
     - eval/s10/diagnostics.py
     - eval/s10/code_lock.py
   - Changed modules:
     - eval/s10/judge_kit.py (eval and stability rounds)
     - eval/s10/judge_runner.py (RUN_ROUNDS, argparse choices, refusal messages)
   - New tests:
     - tests/test_s10b2b_agreement.py
     - tests/test_s10b2b_merge.py
     - tests/test_s10b2b_gate.py
     - tests/test_s10b2b_draws.py
     - tests/test_s10b2b_sheets.py
     - tests/test_s10b2b_diagnostics.py
     - tests/test_s10b2b_kit_eval.py
     - tests/test_s10b2b_code_lock.py
     - helper tests/_s10b2b_fake.py
   - Existing tests edited (Part B, Q2):

     | File | Test | Old | New |
     |---|---|---|---|
     | tests/test_s10b2_judge_kit.py | test_eval_round_refused | `'no eval kit is built' in l` | `'CODEBOOK_v1.0.md is not recorded' in l` |
     | tests/test_s10b2_judge_runner.py | test_eval_round_refused, renamed test_eval_round_without_kit_is_input_error | eval `run` / `status` / `import-chat` `== 1` | `== 2` (kit missing); added `run --round stability == 2` |

   - Manifests:
     - eval/s10/manifests/s10b2b_code_lock.json (9 files, python 3.11.17)
     - eval/s10/manifests/codebook_v1.json (6 files, template s10-judge-prompt.v1)
   - Texts placed by Hariom:
     - eval/s10/codebook/CODEBOOK_v1.0.md
     - eval/s10/codebook/JUDGE_INSTRUCTIONS_v1.0.md
     - specs/S10_PREREG_ADDENDUM_A1.md (local, git-ignored)
5. Checks with real output:
   - Test counts: see Tests. Every number is from the pytest output of that run.
   - Practice/smoke byte-identity: data/.pytest_tmp/s10b2b_kitcheck/snap.py, before vs after the judge_kit change; 27 file shas IDENTICAL (cmp).
   - Code lock shas:
     - --show output equals the committed-blob shas at 216f3aa (`git show HEAD:<f> | tr -d '\r' | sha256sum`, 9 of 9);
     - the manifest equals the confirmed list.
   - Codebook freeze shas:
     - printed read-only twice (old drafts, then the replacements);
     - the manifest equals the confirmed second list;
     - the 3 committed files equal their git blobs;
     - judge_kit.check_eval_manifest(ROOT) printed "check_eval_manifest OK 6 files s10-judge-prompt.v1".
   - Write-once: second lock run and second freeze run both exit 3 (REFUSED).
6. Frozen items:
   - specs/S10_PREREGISTRATION.md, prereg_v1.json, s10b1_build.json and every file it lists, e1_grid_check.json, and the engine/ruleset files: not modified (git diff over specs/, eval/s10/manifests/ and standards/ was empty before each new manifest).
   - New frozen items this step: s10b2b_code_lock.json, codebook_v1.json.
7. Holdouts and sealed files: holdout_profiles.yaml, profiles_draft.yaml, jid_map.json, S8 sealed files and D:\s8_* were never opened, printed, copied or hashed. No real file under data/gold/s10/ was opened; all data was fake, under data/.pytest_tmp/.
8. Safety:
   - no commit and no push by me (Hariom committed each Part);
   - .env not opened; no keys printed; no AI attribution in code, docs or this report;
   - no guard or hook blocked anything;
   - one scratch file was written outside the repo in Part A (session scratchpad, gate_proto.py, 821 bytes; path printed for Hariom to delete); all later temp files went under data/.pytest_tmp/.
9. Paid/external calls: 0.
10. Not checked / assumed:
    - Real judge-result, label-CSV and judge-file shapes are taken from the code (schema, judge_runner, label_tool FIELDS), not from real files.
    - Two property checks are guards that may never fire: non_contact_above_na (A1-6: a fully non-contacting device cannot be expressed in the profile format) and knee_on_non_knee (the judge file adds fda-knee only to knee profiles; the validator rejects extra items).
    - Name masking covers only the agreed list (plus attached versions and astra / flash / pro / mini after a name + version); other product names pass through.
    - specs/S10_PREREG_ADDENDUM_A1.md exists only on the laptop (sha recorded in codebook_v1.json).
11. Errors and retries:
    - Part A: one wrong test assertion ('reason' matched the key route_reasons), fixed; one Write retried after a classifier no-verdict.
    - Part B: a `(?!/)` lookahead and a lock-before-reasons ordering, fixed before the first test run; one fake reason held judge names, changed before the run.
    - Part B report: heading 2 wrongly called the retest earliest date "draw time + 7 days"; the code always used the approved rule (latest audit-A 1-150 label + 7 days). Corrected at the pre-lock STOP.
    - Part D: the first placed texts were old drafts; Hariom said do not freeze, replaced them, and the shas were re-printed; one check script hit a Python 3.11 f-string SyntaxError and was rerun (no file touched).
12. Risks for later steps + what I need from Hariom:
    - Any edit to the 9 locked files fails tests/test_s10b2b_code_lock.py; a deliberate change needs a code lock v2 (amendment + log entry).
    - Any edit to the 6 codebook files makes `judge_kit --round eval` refuse; a deliberate change needs codebook v1.1.
    - Keep a backup of specs/S10_PREREG_ADDENDUM_A1.md.
    - Next (before Audit A 1-150): refresh S10b2_LABELLING_GUIDE.md for v1.0.
    - At the judge runs: record exact tool/model versions at start (prereg §15).

## Real-data order for Hariom (placeholders only; nothing here was run)
1. `python -m eval.s10.label_tool --mode audit --upto 150` (blind audit A 1-150; codebook_v1.json now exists).
2. `python -m eval.s10.draws c-batches`, then `python -m eval.s10.judge_kit --round eval`.
3. Judge runs: `python -m eval.s10.judge_runner run --round eval --judge A|B` (with the usual --claude-exe / --codex-exe / --run-dir), and `import-chat --round eval --batch NN ...` for C.
4. `python -m eval.s10.merge` (STOP if routed > 200), then `python -m eval.s10.gate` (exit 5: label the next look; exit 4: STOP).
5. `python -m eval.s10.draws retest` (prints the earliest re-label date) and `python -m eval.s10.draws audit-b`; then `python -m eval.s10.sheets retest-sheet` / `audit-b-sheet`.
6. `python -m eval.s10.draws stability`, then `python -m eval.s10.judge_kit --round stability` and the stability judge runs.
7. `python -m eval.s10.sheets referee-pass1` (needs all audit-B labels), fill pass 1, then `python -m eval.s10.sheets referee-pass2` (writes the pass-1 lock).
8. `python -m eval.s10.diagnostics c-position | stability | intra-rater`.

## Tests

| Point | Passed | Skipped | Failed | Source |
|---|---|---|---|---|
| Baseline (6b486b2) | 1433 | 1 | 0 | full suite, Part A start |
| Part A (49f4112) | 1491 | 1 | 0 | +58: agreement 11, merge 32, gate 15 |
| Part B (c34402d) | 1531 | 1 | 0 | +40: draws 12, kit 6, sheets 17, diagnostics 5 |
| Pre-lock masking (216f3aa) | 1567 | 1 | 0 | +36 sheet tests |
| Part C script (2c96391) | 1578 | 2 | 0 | +11 lock tests; real-manifest test skipped until lock |
| After lock (16eae54) | 1579 | 1 | 0 | real-manifest test now enforced |
| After freeze (a8f4944) | 1579 | 1 | 0 | |
| Part E final (a8f4944 + this report) | 1579 | 1 | 0 | full suite, 212 s |

Full-suite command: `.venv\Scripts\python -m pytest tests mcp observability -q --basetemp data/.pytest_tmp/s10b2b_full`.

## Part A: agreement, merge, property checks, gate (2026-11-16)
- agreement.py: Cohen kappa (k x k), Po, PABAK, Gwet AC1, prevalence/bias index (absolute; signed kept), FCE variance/interval labelled "assumes independent cells (too narrow here)". All computed with Fraction; undefined -> NaN.
- merge.py:
  - routes on label_missing_or_invalid, straddle, always_rule_part_na, non_contact_above_na, knee_on_non_knee, empty_reason;
  - otherwise final = middle label after sorting NA < rec < mand;
  - cell_id "J-001|ISO 10993-1";
  - STOP when routed > 200.
- gate.py:
  - gate cells = non-routed prefix cells, with profile_id = J-id;
  - binary kappa, human vs final;
  - pass when the lower bound >= 0.60; NaN = not passed;
  - fail at 350 = STOP;
  - secondary rows exactly as pre-registered (S10P-07).
- Tests use hand tables with exact fractions, each checked by a second, independent computation.

## Part B: draws, sheets, diagnostics, kit rounds (2026-11-16)
- draws.py: Random("S10b-v1|<purpose>") on sorted ids for audit-b, retest, stab-devices, stab-items|<J-id> and c-batches; write-once files under data/gold/s10/draws/ with the LF sha printed.
- Audit B: unanimous, not routed (prereg §4 step 7), any "low", not in audit A; sample 50 if there are more; the routed-excluded count is reported.
- sheets.py:
  - blind audit-B and retest sheets (profile + item only);
  - referee set = routed cells + audit-B disagreements;
  - blind audit-A / audit-B labels reused as pass-1 labels (source recorded);
  - pass-1 lock;
  - pass 2 shows reasons cleaned (label words -> [label], names -> [judge]) and shuffled per cell.
- diagnostics.py: C position (descriptive), stability "self-agreement under reversed item order" (3-class share with counts), intra-rater binary kappa with a profile-cluster CI.
- judge_kit.py:
  - eval round: judge file, v1.0 texts, C batches from c_batches.json;
  - stability round: 6 drawn devices, each judge's main-pass order reversed, C 3 + 3.
- judge_runner.py: eval and stability rounds enabled (a kit manifest is still required).

## Pre-lock: name masking (2026-11-16)
- clean_reason masks claude, opus, sonnet, haiku, anthropic, gpt, openai, codex, gemini, google, antigravity, chatgpt, astra and "judge a/b/c" (whole word, any case).
- A name plus an attached version (space, -, . or none; starting with a digit) plus an optional astra / flash / pro / mini becomes one [judge].
- flash / pro / mini alone stay.

## Part C: code lock (2026-11-16)
- code_lock.py: --show is read-only; all 9 --expected-sha values are required; a mismatch or missing file writes nothing; refuses to overwrite.
- Locked at 216f3aa:
  - judge_output, judge_runner, metrics;
  - agreement, merge, gate;
  - draws, sheets, diagnostics.
- Command run once after "yes lock": `.venv\Scripts\python -m eval.s10.code_lock --expected-sha <REL>=<SHA>` x 9. A second run gave exit 3.

## Part D: codebook v1.0 freeze (2026-11-16)
- Hariom placed the three texts; the first versions were old drafts and were not frozen. After "replaced", the 6-file table was re-printed read-only (UTF-8, no BOM).
- Command run once after "yes freeze": `.venv\Scripts\python -m eval.s10.codebook_freeze --expected-sha <REL>=<SHA>` x 6. A second run gave exit 3.
- Committed: the manifest and the two eval/s10/codebook texts. specs/ is git-ignored and was not force-added.
