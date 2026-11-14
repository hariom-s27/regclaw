# S10b1 — build: grid check, practice profiles, judge file, audit list, label tool

## PLAIN SUMMARY
1. Hariom's hand-copied FDA 2023 Attachment A grid matches the frozen ruleset YAML on all 264 cells (24 rows x 11 endpoints); manifest eval/s10/manifests/e1_grid_check.json.
2. Codebook v0.1 recorded by LF sha; 4 practice decoy profiles written in a new additive practice format, reviewed and approved by Hariom ("practice OK").
3. Judge file built for the 30 eval profiles (602 items, blinded): holdout read once, sha match vs profiles_v1.json, duplicate check 0 hits, J-id map sealed at once, access ledger written.
4. Audit-A list: 350 cells, 11-12 per J-id, family totals 122/70/122/35/1 (seed S10b-v1); build manifest eval/s10/manifests/s10b1_build.json records all shas.
5. Local label tool built and tested on fakes only; it was not started on real files on purpose: it is first opened in S10b-2, after the codebook is calibrated and frozen as v1.0 (pre-registration §4).

## SELF-AUDIT (whole step S10b-1)
1. Asked vs done
   - Part A status + branch `s10b/build`: done (start HEAD faccc98, clean apart from the codebook folder).
   - S10b-02 grid check: done — 24 rows, 264 cells, 0 mismatches, verdict match (commit af30e6a).
   - S10b-03 codebook: done — LF sha256 7992a374…80fe recorded, not edited (commit 9833a11).
   - S10b-04 practice profiles: done — PR-1..PR-4 valid, 0 banned terms, Hariom's notes applied, "practice OK" (final sha cfd11a74…f9e9; file git-ignored).
   - S10b-05/06 judge file + seal + ledger: done — 30 entries, 602 items, holdout sha match, 0 duplicate hits, map sealed, ledger outcome written (commit 8e599ed; outputs git-ignored).
   - S10b-07/08 audit list: done — 350 rows, per J-id 11/12, family totals as planned for k=2.
   - S10b-09 build manifest: done — 6 files with LF sha + counts (commit 8e599ed).
   - S10b-10 label tool: done — tests on fakes; not started on real files (commit d6d1571).
   - Part E report: done (this file).
2. Decisions I made myself
   - Grid check: join on (nature, site, duration); row-set/mark errors are input errors (exit 2), not mismatches.
   - Practice: decoy details within S10b-04; PR-2 material class `other` so the derived attachment_g_material flag is false; PR-4 site tissue_bone (only FDA implant tissue site).
   - Judge file: non-own error messages print the type only; dev sha gate before the holdout read; ledger records dev/practice/holdout reads.
   - Audit list: derived seed strings S10b-v1|rows / |cells|{a} / |order; greedy attempt loop (attempt 4 used).
   - Build manifest refuses unless ledger outcome is written.
   - Label tool: port 8766, key bindings, `#`/`##` headings, defensive scrub of hidden keys, smaller --upto labels open under a larger --upto.
3. Deviations: no deviations from the approved plans and answers.
4. How I implemented it
   - eval/s10/e1_grid_check.py — grid vs YAML compare, write-once manifest.
   - eval/s10/formats.py — additive s10-practice-profiles.v1 + validate_practice_set.
   - eval/s10/judge_file.py — one-read holdout, duplicate check, seeded J-ids, seal, ledger.
   - eval/s10/audit_list.py — family LR quotas, row quotas, seeded greedy selection, seeded order.
   - eval/s10/s10b1_manifest.py — build manifest (LF shas + counts).
   - eval/s10/label_tool.py — local audit labelling page.
   - Tests: tests/test_s10b_grid_check.py, test_s10b_practice_format.py, test_s10b_judge_file.py, test_s10b_audit_list.py, test_s10b_build_manifest.py, test_s10b_label_tool.py.
   - Real runs: `python -m eval.s10.e1_grid_check`, `-m eval.s10.judge_file` (once), `-m eval.s10.audit_list`, `-m eval.s10.s10b1_manifest`.
5. Checks with real output
   - Targeted tests (my terminal, `--basetemp data\.pytest_tmp\s10b`): Part A 6 passed; Part B 7 passed; Part C 23 passed; Part D 23 passed.
   - Full suite (source: Hariom's terminal): 1223 → 1230 → 1253 → 1276 passed / 1 skipped / 0 failed; mcp 20, obs 1 each time.
   - Script outputs and shas: see Parts A–D below and eval/s10/manifests/s10b1_build.json.
6. Frozen items: engine.py, guard.py, overclaim.py, the 4 standards/data YAMLs (two read-only), classifier.py PROMPT, retrieval_eval/* (s8_label_tool.py read only), corpus, freeze manifests — untouched; existing formats.py schemas byte-identical (additive diff, 0 deleted lines).
7. Holdouts and sealed files: holdout_profiles.yaml read once (one read_bytes) by judge_file.py in the run that wrote holdout_access_ledger.json; sha match; nothing printed. jid_map.json sealed immediately (jid_map_manifest.json) and not opened since. holdout_manifest.json not read; verify_holdout not called. No other sealed file opened.
8. Safety: no commit or push by me; .env not opened; no keys; nothing written outside the repo (pytest temp in data/.pytest_tmp/s10b); data/ outputs git-ignored; no AI attribution in commits/code; no guard or permission block.
9. Paid/external calls: 0. Engine: only rule_02 via the validator on the 4 practice profiles (approved Part B Q2); no tool, classifier or LLM call on any profile.
10. Not checked / assumed: the label page was not opened in a real browser; judge-file content not reviewed by a human; X vs O grid marks not distinguished (per S10b-02).
11. Errors and retries: Part C first targeted run 1 failed (fake names contained P-ids; test data fixed); Part D cosmetic test tidy; every real run ran once, exit 0.
12. Risks for later steps + what I need from Hariom: calibrate and freeze the codebook as v1.0 before opening the label tool on real files (S10b-2); F5 has only 1 audit cell (2 knee J-ids); merge the PR.

## Commits (source: git log)
- af30e6a S10b Part A: FDA Attachment A grid check (264 cells, match)
- 9833a11 S10b Part B: codebook v0.1 and practice-profile format
- 8e599ed S10b Part C: judge file, sealed J-id map, access ledger, Audit-A list
- d6d1571 S10b Part D: Audit-A label tool

# Parts A–D results (as written at each STOP)

## Part A — status + grid check (2026-11-14)

Result
- Start state: branch `main`, HEAD `faccc98`; `git status --porcelain` showed only `?? eval/s10/codebook/` (Hariom's `CODEBOOK_v0.1.md`). Branch `s10b/build` created.
- `data/gold/s10/e1_fda_grid_hariom.csv` exists, git-ignored (`git check-ignore -v` → `.gitignore:12:data/gold/`).
- `python -m eval.s10.e1_grid_check` (exit 0): rows=24, cells_compared=264, mismatches=0, verdict=match.
- grid_sha256_lf `c42e67d551d3fc62b77f9f32d0d6a7421e29747d8e76d081fdad80e6b775fdf4`; yaml_sha256_lf `5989b50fd98a89c6d4dad246bf88268b2d009eb98cdb00ec4d750a8825b44804` (source: script output / manifest).
- Information only: Repro marked 0, Degr marked 0; footnote-note rows 7 (7 rows carry grid footnote symbols, 1 row carries a YAML cell footnote; source: `eval/s10/manifests/e1_grid_check.json`).

SELF-AUDIT (Part A)
1. Asked vs done: git status + branch: done. Grid exists + ignored: done. Script + targeted test: done (6 passed). Run + manifest: done (match).
2. Decisions I made myself: rows join on (nature, site, duration); row-set differences, duplicate rows and marks other than X/O/empty are input errors (exit 2, nothing written) rather than "mismatches"; mismatching cells are printed by row+column (no grid content otherwise); manifest also lists the 11 endpoint column names.
3. Deviations: none against the approved plan (pytest run with `--basetemp data\.pytest_tmp\s10b` as Hariom asked; yaml_file + yaml_sha256_lf added per Hariom's Q2).
4. How I implemented it: `eval/s10/e1_grid_check.py` (load_grid, load_ruleset, compare, build_manifest, main; LF sha via `standards.ruleset.normalised_sha`; write-once via `open('x')`); `tests/test_s10b_grid_check.py` (fake 24-row grid + fake YAML: match, CRLF/LF sha equality, one flipped cell, write-once, bad mark, missing row). Commands: `.\.venv\Scripts\python.exe -m pytest tests/test_s10b_grid_check.py -q --basetemp data\.pytest_tmp\s10b`; `.\.venv\Scripts\python.exe -m eval.s10.e1_grid_check`.
5. Checks with real output: pytest `6 passed in 1.58s`; script output above; exit 0.
6. Frozen items: `standards/data/fda2023_attachment_a.v1.yaml` read-only (not modified); engine.py, guard.py, overclaim.py, classifier.py, retrieval_eval/*, corpus, freeze manifests not touched.
7. Holdouts and sealed files: none opened in Part A.
8. Safety: no commit, no push; .env not opened; no keys; nothing written outside the repo (pytest temp in `data/.pytest_tmp/s10b`); no AI attribution; no blocks hit (Hariom moved the pytest run to the background; it completed, exit 0).
9. Paid/external calls: 0.
10. Not checked / assumed: the full suite was not run (Hariom runs it); I assume `consider: true` ⇔ X or O in the grid (X vs O distinction not compared, per S10b-02).
11. Errors and retries: none.
12. Risks for later steps + what I need from Hariom: run the full suite; OK the Part B plan.

## Part B — codebook record + practice profiles (2026-11-14) — STOP 1 (awaiting "practice OK")

Result
- CODEBOOK_v0.1.md LF sha256 `7992a3743acfeade7b4d94c9c610cd3620e1fc35d895ea70dc49091655bd80fe` (source: `split_guard.file_sha`); file not edited.
- `eval/s10/formats.py`: additive only (`git diff --stat` 30 insertions, 0 deletions): PRACTICE_FORMAT `s10-practice-profiles.v1`, split `practice`, no stratum, no comment; `validate_practice_set` reuses `check_profile_entry`.
- `data/gold/s10/practice_profiles.yaml` (git-ignored): 4 profiles PR-1..PR-4, `validate_practice_set` → 4 valid; banned-term hits 0; LF sha256 `fe150368de2681def66d7854694c71e30a8bc33e568daea8e489356856c54d2a`.
- Targeted test `tests/test_s10b_practice_format.py`: 7 passed in 6.52s.

SELF-AUDIT (Part B, before review)
1. Asked vs done: codebook sha: done. Format change (Q1 A): done. 4 profiles: done, validated. Review by Hariom: pending.
2. Decisions I made myself: decoy design within S10b-04 (durations from exposure hours: PR-1 B, PR-2 A, PR-3 B, PR-4 C; PR-4 sterilised by gamma so EO stays a PR-3-only decoy; target market US; device_specific_standards empty).
3. Deviations: none.
4. How I implemented it: formats.py (+PRACTICE_* schema, validate_practice_set); tests/test_s10b_practice_format.py (valid set; stratum/comment rejected; wrong split; duplicate id; wrong duration/flags; eval validator still rejects split practice); practice_profiles.yaml.
5. Checks with real output: validate → 4; banned hits 0; pytest 7 passed.
6. Frozen items: engine.py, guard.py, overclaim.py, standards/data YAMLs, classifier.py, retrieval_eval/*, corpus, freeze manifests untouched; existing formats.py schemas/validators unchanged (0 deleted lines).
7. Holdouts and sealed files: none opened.
8. Safety: no commit/push; .env not opened; nothing outside repo; no AI attribution; no blocks.
9. Paid/external calls: 0. Engine use: `rule_02` via the validator on the 4 practice profiles only (approved Q2).
10. Not checked / assumed: full suite not run (Hariom).
11. Errors and retries: none.
12. Risks + needs: Hariom reviews practice_profiles.yaml ("practice OK" or notes).

### Part B — Hariom's review notes applied (2026-11-14) — STOP 2 (awaiting "practice OK")
- PR-1 replaced by a nasal septal splint (continuous ~7 days, surface/mucosal_membrane); no "removable", not oral/dental.
- PR-2 exposure changed to intermittent_repeat (~12 h per use, 180 uses); attachment_g_material removed by setting the novel material's class to `other` (the flag is derived, not declared: intact skin + polymer/hydrogel_adhesive triggers it, so a non-listed novel material cannot be `polymer`); flags now [repeat_use].
- PR-3 unchanged. PR-4 replaced by a fully absorbable soft-tissue reinforcement mesh; gamma kept; contact site stays `tissue_bone` (the only FDA implant tissue site; it covers soft tissue).
- Re-validation: 4 valid; durations PR-1 B, PR-2 C, PR-3 B, PR-4 C; banned-term hits 0; new LF sha256 `d9617c43e2746ce65d5c6b1d4ed5495c6fbf643ef5832b9cb94d2387a65e9c46`.
- Decisions I made myself: material_class `other` for PR-2 (needed for the flag to be false); 180 uses for "months"; site tissue_bone for PR-4.

### Part B — final note applied; "practice OK" (2026-11-14)
- PR-1 sterilization set to gamma (no EO anywhere except PR-3: `sterilization: ethylene_oxide` count 1). No other change.
- Re-validation: 4 valid; durations PR-1 B, PR-2 C, PR-3 B, PR-4 C; flags as derived (PR-1 none, PR-2 repeat_use, PR-3 ethylene_oxide, PR-4 absorbable); banned-term hits 0.
- Final practice_profiles.yaml LF sha256 `cfd11a7466f99f8d875fd852dbd223383b52821735fc341edbd35c21f430f9e9`.
- Approval: Hariom's instruction "validation passes with 0 banned-term hits → treat as practice OK" — condition met.

## Part C — STOP 1: scripts + targeted tests (2026-11-14; real run waits for "run judge file")
- Plan approved with change: `existing_evidence` excluded from the judge file (test asserts it appears nowhere). Q1 yes (s10b1_manifest.py), Q2 yes (profiles_v1.json only; no verify_holdout, no holdout_manifest.json read), Q3 yes (practice sha must equal cfd11a74...).
- New: eval/s10/judge_file.py, eval/s10/audit_list.py, eval/s10/s10b1_manifest.py; tests/test_s10b_judge_file.py (7), tests/test_s10b_audit_list.py (12), tests/test_s10b_build_manifest.py (4).
- Targeted tests (`--basetemp data\.pytest_tmp\s10b`): 23 passed in 3.08s. First run: 1 failed (test fake names contained the P-id; fake data fixed, script unchanged).
- Real outputs absent (judge file, map, map manifest, ledger, audit list, build manifest): nothing run on real files.
- Decisions I made myself: error messages from non-own exceptions print the exception type only (YAML errors can quote file lines); judge_file refuses before the holdout read if the dev sha mismatches; ledger records practice/dev/holdout reads with shas; s10b1_manifest refuses unless the ledger outcome is "written".

## Part C — STOP 2: real run (2026-11-14, after Hariom's "run judge file")
Result (source: script outputs; eval/s10/manifests/s10b1_build.json)
- `python -m eval.s10.judge_file` (once, exit 0): dev sha match; holdout sha match (vs profiles_v1.json); duplicate check practice names=4, eval names=30, hits=0; entries=30, items_total=602, knee_entries=2, other_device_specific_standards=0.
- judge_file_v1.yaml `83b4519c0c057175510d51620a64f6e17ef540eb19f990851b413f49c87bdba6`; jid_map.json written and sealed → jid_map_manifest.json `7d28d5a12c3f5b4ed2b513918f3b4540974d00566629373f8240a5a6b4040a24`; holdout_access_ledger.json `8dd792f8add2d8ff9e230f42a5795a34924df71c21121798ed6fb1619a872a58` (outcome written; files_read 3, outputs_written 3).
- `python -m eval.s10.audit_list` (exit 0): cells 602, rows 350, J-ids 30, per J-id min 11 / max 12, attempt 4. Family cells/total/selected: F1 210/122/122, F2 120/70/70, F3 210/122/122, F4 60/35/35, F5 2/1/1 (matches the plan table for k=2). audit_a_list.csv `d8c30a5723c00050fe026157223393c64901a4fed9c461c6d630be42922336a0`.
- `python -m eval.s10.s10b1_manifest` (exit 0): eval/s10/manifests/s10b1_build.json written (6 files; codebook 114 lines; practice 4 profiles).

SELF-AUDIT (Part C)
1. Asked vs done: scripts + tests: done (23 passed). judge_file once: done. Seal: done. Ledger: done. audit_list: done. Build manifest: done.
2. Decisions I made myself: see Part C STOP 1 (type-only error messages; dev sha gate; ledger records all 3 reads; manifest requires ledger outcome written); greedy selection succeeded at attempt 4 (deterministic, recorded).
3. Deviations: none.
4. How I implemented it: eval/s10/judge_file.py, eval/s10/audit_list.py, eval/s10/s10b1_manifest.py + 3 test files; commands as above.
5. Checks with real output: shas, counts and family table above; targeted tests 23 passed.
6. Frozen items: engine.py, guard.py, overclaim.py, standards/data YAMLs (endpoint_to_part read-only), classifier.py, retrieval_eval/*, corpus, freeze manifests untouched.
7. Holdouts and sealed files: holdout_profiles.yaml read once (one read_bytes) in the run that wrote the ledger; sha match; content not printed. jid_map.json sealed at once; not opened or printed afterwards. holdout_manifest.json not read; verify_holdout not called. No other sealed file opened.
8. Safety: no commit/push; .env not opened; nothing outside the repo; data/ outputs git-ignored (checked for judge_file_v1.yaml); no AI attribution; no blocks.
9. Paid/external calls: 0. No engine/rule/classifier/LLM call on any profile in Part C (pydantic shape check only).
10. Not checked / assumed: full suite not run (Hariom); judge-file content not reviewed by a human yet.
11. Errors and retries: one test-data bug in the first targeted run (fixed); real runs each ran once, exit 0.
12. Risks + needs: the audit list depends on the judge-file item lists (only 2 knee entries → F5 has 1 cell); Hariom runs the full suite and OKs the Part D plan.

## Part D — label tool (2026-11-14)
Result
- New eval/s10/label_tool.py (stdlib + yaml; 127.0.0.1; modelled on retrieval_eval/s8_label_tool.py, which is unchanged) and tests/test_s10b_label_tool.py.
- Targeted tests (`--basetemp data\.pytest_tmp\s10b`): 23 passed in 11.74s.
- Netguard: tests/_netguard.py `block_network` is a helper, not autouse (conftest sets only REGCLAW_STUB); loopback test server not blocked; netguard untouched.
- Start command for Hariom (not run on real files): `.\.venv\Scripts\python.exe -m eval.s10.label_tool --mode audit --upto 150`
- Labels file data/gold/s10/labels_hariom_auditA.csv: git-ignored (.gitignore:12), does not exist yet.

SELF-AUDIT (Part D)
1. Asked vs done: tool: done; tests on fakes: done (23 passed); start command: given; tool not started on real files.
2. Decisions I made myself: default port 8766 (S8 uses 8765); keys 1/2/3 for label and l/m/h for confidence; codebook headings = lines starting "# " or "## "; defensive scrub of hidden keys (id, split, stratum, comment, flags, derived, existing_evidence, family, p_id); a labels file made under a smaller --upto opens under a larger one, cells beyond --upto are refused.
3. Deviations: none (Q1–Q5 applied: reason required ≤300 one line; family hidden; sha guard vs s10b1_build.json; gap-free; BOM + CRLF).
4. How I implemented it: LabelApp (check_shas, item, save, csv_text, atomic _write via .tmp + os.replace), make_handler (/, /api/state, /api/item, POST /api/save), serve, main.
5. Checks with real output: pytest 23 passed; git check-ignore for the labels file.
6. Frozen items: retrieval_eval/* (s8_label_tool.py read only), engine/guard/overclaim, standards/data YAMLs, classifier, corpus, freeze manifests untouched.
7. Holdouts and sealed files: none opened; the tool never opens jid_map.json or holdout files.
8. Safety: no commit/push; .env not opened; nothing outside the repo; no AI attribution; no blocks.
9. Paid/external calls: 0 (tests use a 127.0.0.1 server only).
10. Not checked / assumed: the page was not opened in a real browser (HTTP round-trip tested only); full suite not run.
11. Errors and retries: none (one cosmetic test tidy, re-run 23 passed).
12. Risks + needs: Hariom starts the tool himself and checks the page renders on his browser; full suite.

## Where it fails
- The codebook is a draft (v0.1) until calibration; labels made before the v1.0 freeze are not evidence.
- Codebook v0.1 and the 4 practice profiles were drafted with an AI model from primary sources, then reviewed and approved by Hariom; the FDA table was verified by Hariom's independent hand copy.
- The audit list depends on the judge-file item lists: only 2 J-ids list fda-knee, so family F5 has 1 audit cell.
- One human labeller (Hariom); no second rater in S10b-1.
- The label tool has been tested on fakes and over local HTTP only; it is first used on real files in S10b-2.
- The duplicate-name check is the near-equal rule of profile_checks check (a) (difflib ratio 0.85 or whole-word containment); paraphrased names could pass.
- The profile validator derives attachment_g_material for every intact-skin polymer, even when novel (engine simplification; does not change S10 labels; found while writing PR-2).

## PR description
```
S10b-1: grid check, practice profiles, judge file, Audit-A list, label tool

Summary
- FDA 2023 Attachment A: Hariom's hand-copied grid matches the ruleset YAML on all 264 cells (eval/s10/manifests/e1_grid_check.json).
- Codebook v0.1 added (eval/s10/codebook/CODEBOOK_v0.1.md); additive practice-profile format s10-practice-profiles.v1 with validate_practice_set.
- Judge file builder: one holdout read with sha check, duplicate-name check, seeded J-ids, J-id map sealed at once, access ledger.
- Audit-A list: 350 cells, 11-12 per J-id, family largest-remainder quotas, seed S10b-v1.
- Build manifest eval/s10/manifests/s10b1_build.json (LF shas + counts, no content).
- Local Audit-A label tool (127.0.0.1), tested on fakes; first used on real files in S10b-2 after the codebook v1.0 freeze.

Checks
- Targeted tests: 6 + 7 + 23 + 23 passed.
- Full suite (Hariom): 1276 passed / 1 skipped / 0 failed.
- 0 paid or network calls; no engine, classifier or LLM run on eval profiles.

Not in this PR
- data/ outputs (judge file, map, ledger, audit list, practice profiles) stay local and git-ignored.

Next
- S10b-2: calibrate and freeze the codebook as v1.0, then label Audit-A with the tool.
```
