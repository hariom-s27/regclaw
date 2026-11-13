# S10h — freeze hygiene

## PLAIN SUMMARY
1. The S10 pre-registration is now frozen: a write-once manifest stores its sha, and the sha matches the approved value.
2. Running the freeze script a second time is refused, and the manifest stays byte-identical.
3. CLAUDE.md now points rules 7 and 9 to the Standing rules, lists the 12 self-audit headings, and uses Hariom's name.
4. The env templates and README now carry Windows setup notes (Postgres host 127.0.0.1, offline cl100k vocabulary).
5. All tests pass (tests 1217 passed / 1 skipped / 0 failed, mcp 20, obs 1); locked and frozen files are untouched.

Branch `tidy/s10h-freeze-hygiene` (from `main` @ `8d95a46`). Commits: `06ea75b` (A), `9e0a85c` (B1), `8d48024` (B2). This report is committed in Part C.

## Part A — status + pre-registration freeze (2026-11-14)

### Result
- Status before: branch `main`, HEAD `8d95a46`, `git status --porcelain` empty. Then `git switch -c tidy/s10h-freeze-hygiene`.
- Baseline (`tasks.py test`, 8d95a46): mcp 20 passed · obs 1 passed · tests 1215 passed / 1 skipped / 0 failed.
- `git check-ignore -v specs/S10_PREREGISTRATION.md` → `.gitignore:26:specs/`. `Test-Path` → True. Content not printed.
- Run 1 of `python -m eval.s10.prereg_freeze`: exit 0, `sha256_lf=84934b357da1fceff619a40d0a8595ad32d271d22ae734ed11fb0a8d8988eefe bytes_lf=27970 lines=174`. This equals the expected sha.
- Manifest `eval/s10/manifests/prereg_v1.json`: format `s10-prereg-manifest.v1`, version `v1.0`, approved `2026-11-14`, decisions `S10P-01..S10P-29 (D20)`, created_at `2026-11-13T21:31:46.925467+00:00` (UTC; local date 2026-11-14 IST).
- Manifest file sha256 before run 2: `59A5BF6FB0A7058005953DF17131E3B00FA2AEDBED43A98793A6B76CF113BDF7`.
- Run 2: `REFUSED: manifest already exists (write-once): eval/s10/manifests/prereg_v1.json`, exit 3.
- Manifest file sha256 after run 2: `59A5BF6FB0A7058005953DF17131E3B00FA2AEDBED43A98793A6B76CF113BDF7` (identical).
- New test alone: `tests/test_s10_prereg_manifest.py` 2 passed. The recompute check ran because the spec is present.
- Full suite after: mcp 20 passed · obs 1 passed · tests 1217 passed / 1 skipped / 0 failed (+2 = the new tests).

### SELF-AUDIT (Part A)
1. **Asked vs done**
   - git status/branch/HEAD + create branch: done (output above).
   - Baseline tests: done (20 / 1 / 1215 passed, 1 skipped).
   - check-ignore + Test-Path: done.
   - Script + run 1 + show manifest: done.
   - Run 2 refused, sha unchanged: done (exit 3, same sha).
   - Test alone, then full suite: done (2 passed; 1217 passed / 1 skipped).
2. **Decisions I made myself**
   - Reused `standards.ruleset.normalised_sha` (sha256 of bytes with `\r\n`→`\n`). Rejected: a copy in the script, and importing from split_guard, which imports yaml and is not stdlib-only.
   - Exit codes 1 (mismatch), 2 (spec missing), 3 (exists). The manifest-exists check runs before the spec is read.
   - `lines` = count of `\n` after LF normalisation, +1 if the last line has no newline.
   - JSON keeps the field order of S10h-02 (no sort), indent=2, LF, trailing newline.
3. **Deviations**
   - **What:** three output files were written outside the repo, in the tool's session folder `%TEMP%\<tool session folder>\tasks\`. This breaks the standing rule "write nothing outside the repo" and the prompt rule "never the system temp folder".
     - `b0ymksu05.output` (106 bytes): the baseline `tasks.py test` run, captured when that run was moved to the background. It holds the 3 suite summary lines (`20 passed`, `1 passed`, `1215 passed, 1 skipped`) and the exit-code line.
     - `bc81i1gbl.output` (209 bytes): the after-run, which I started with `run_in_background`. It holds the 3 summary lines (`20 passed`, `1 passed`, `1217 passed, 1 skipped`), 3 `git status --porcelain` lines (the 3 new file names) and the exit-code line.
     - `b985u61bb.output` (0 bytes): a transient capture for the read-only listing of that folder. It was gone at the next listing.
   - **Why:** the command tool saves the output of a background command to that folder. I did not name it in the plan.
   - **Effect:** counts, file names and exit codes only. No spec content, keys or sealed data are in them, and no repo file changed. Hariom will delete them.
4. **How I implemented it**
   - Files:
     - `eval/s10/prereg_freeze.py` (new): `compute`, `build_manifest`, `main`.
     - `eval/s10/manifests/prereg_v1.json` (new, written by the script).
     - `tests/test_s10_prereg_manifest.py` (new, read-only test).
     - `logs/S10h_report.md` (new, this file).
   - Commands: `git branch --show-current; git rev-parse --short HEAD; git status --porcelain; git switch -c tidy/s10h-freeze-hygiene` · `.\.venv\Scripts\python.exe tasks.py test` (x2) · `git check-ignore -v specs/S10_PREREGISTRATION.md; Test-Path specs/S10_PREREGISTRATION.md` · `.\.venv\Scripts\python.exe -m eval.s10.prereg_freeze` (x2) · `Get-Content eval/s10/manifests/prereg_v1.json` · `Get-FileHash ... -Algorithm SHA256` (x2) · `.\.venv\Scripts\python.exe -m pytest -q -rs tests/test_s10_prereg_manifest.py` · `git diff --stat main -- standards/ retrieval_eval/` · `git status --porcelain`.
5. **Checks with real output** (source: command output above)
   - Tests before: 20 / 1 / 1215 passed, 1 skipped.
   - New tests: 2 passed.
   - Total after: 20 / 1 / 1217 passed, 1 skipped, 0 failed.
   - `sha256_lf` matches expected.
   - Manifest file sha is the same before and after run 2.
6. **Frozen items:** `git diff --stat main -- standards/ retrieval_eval/` is empty (0 lines). Engine, ruleset YAMLs, classifier PROMPT, corpus and freeze manifests were not touched. The lock tests are part of the 1217 passed.
7. **Holdouts and sealed files:** not opened, not printed, not hashed, not scored. Not opened either: `profiles_v1.json`, holdout YAMLs/manifests.
8. **Safety**
   - No commit, no push.
   - `.env` not opened; no keys printed.
   - Files written outside the repo: 3 background-output files in the tool's session folder (see heading 3). This is a rule breach, reported there.
   - The spec content was never printed.
   - No AI attribution; no guard/hook blocks.
9. **Paid/external calls:** 0.
10. **Not checked / assumed:** that the local spec is the approved v1.0 file. This is only shown by the sha match against the hash given in the prompt. `created_at` is in UTC, so it reads 2026-11-13 even though local time was 2026-11-14.
11. **Errors and retries:** none. Run 2's exit 3 is the intended refusal.
12. **Risks + what I need from Hariom**
    - Risk: the manifest is write-once. Any correction needs Hariom to delete it by hand, or a v2 manifest.
    - Need: OK to commit (script + manifest + test), then "OK plan" for the Part B plan.

Part A committed by Hariom as `06ea75b`.

## Part B — doc hygiene (2026-11-14)

### Result
- **CLAUDE.md** (+16 / −24, source `git show --numstat 9e0a85c`) (corrected in Part C; see Errors):
  - Rule 7 → one line.
  - Rule 9 → one line. The template block and the "Also list every file…" line were removed (old lines 20–30).
  - The SELF-AUDIT items 1–10 were replaced by the 12 titles. The intro line was kept.
  - The closing line → "At the end of the whole task, finish logs/S##_report.md (see Standing rules)." (S10h-04b)
  - The old-handle note was added under the Standing rules: "- Older records say 'Shyam' (Hariom's earlier handle); leave them as written."
- **eval/s10/split_guard.py** (+1 / −1): docstring "when Shyam seals the set" → "when Hariom seals the set" (no code change).
- **.env.example** (+7 / −0): comment lines only (Windows PG_DSN, TIKTOKEN_CACHE_DIR, the 5-line S10 block). No line removed. 0 non-ASCII bytes.
- **env.example** (+2 / −1): Windows comment added. PG_DSN host `localhost` → `127.0.0.1`. NEO4J_URI unchanged.
- **README.md** (+5 / −0): "Windows setup notes" block (heading + 3 bullets) under the settings table.
- **Old-handle count** (`git grep -n -i shyam`): 66 → 65 lines in 24 files (table in Part C).
- **Narrow tests:** `tests/test_env_template.py tests/test_s10_holdout_guard.py` → 40 passed.
- **Full suite:** mcp 20 passed · obs 1 passed · tests 1217 passed / 1 skipped / 0 failed.
- **Frozen paths:** `git diff --stat main -- standards/ retrieval_eval/` → 0 lines.
- **PR-25 (new):** two env templates (.env.example, env.example) drift; merge later after an exact list + Hariom's yes.

### SELF-AUDIT (Part B)
1. **Asked vs done**
   - CLAUDE.md per S10h-04, S10h-04b and S10h-07: done (diff above).
   - split_guard docstring: done.
   - .env.example, env.example, README: done.
   - Old-handle count: done (65).
   - Full suite + locks + frozen diff: done.
2. **Decisions I made myself**
   - README bullet text. I described the vocabulary from `embed/token_stats.py:17-25`: cache file name, sha check, downloads disabled. No path under data/ and no link. Rejected: naming `data/cache/tiktoken` in the README, because the plan said no data/ links.
   - The S10 block is appended at the end of .env.example. Rejected: next to the Azure keys, which would mean moving existing lines.
   - Decision added by Hariom during Part B, **S10h-04b:** CLAUDE.md line 111 → "At the end of the whole task, finish logs/S##_report.md (see Standing rules)."
   - Hariom's answers: Q1 keep the intro line (line 100). Q2 remove line 30.
3. **Deviations:** no deviations.
4. **How I implemented it**
   - Files: CLAUDE.md, eval/s10/split_guard.py, .env.example, env.example, README.md, and this report. All edits were made with the Edit tool.
   - Commands: `git diff --stat` · `git diff -- CLAUDE.md eval/s10/split_guard.py .env.example env.example README.md` · `git grep -n -i shyam` (grouped per file) · `.\.venv\Scripts\python.exe -m pytest -q tests/test_env_template.py tests/test_s10_holdout_guard.py` · `.\.venv\Scripts\python.exe tasks.py test` (foreground) · `git diff --stat main -- standards/ retrieval_eval/` · `git status --porcelain`.
5. **Checks with real output**
   - Tests before: 20 / 1 / 1217 passed, 1 skipped (Part A end).
   - New tests: 0.
   - Total after: 20 / 1 / 1217 passed, 1 skipped, 0 failed.
   - Narrow: 40 passed.
   - Old-handle count: 65 lines in 24 files.
   - .env.example: 0 non-ASCII bytes.
6. **Frozen items:** `standards/` and `retrieval_eval/` diff vs main is empty. The lock tests passed in the full suite: `tests/test_s10_ruleset.py` (engine + ruleset sha locks) and `tests/test_s10_eval_runner.py` (PROMPT_SHA256).
7. **Holdouts and sealed files:** not opened, not printed, not hashed, not scored.
8. **Safety**
   - No commit, no push.
   - `.env` not opened (only `.env.example` and `env.example`); no keys printed.
   - No AI attribution; no guard/hook blocks.
   - Outside the repo: no new persistent file. A read-only listing of the tool's output folder showed only the 2 Part A files and one 0-byte transient capture for the listing itself.
9. **Paid/external calls:** 0.
10. **Not checked / assumed**
    - The "~130 s" figure was measured on this laptop in S9: IPv6 about 130 s → 0.2 s with 127.0.0.1 (source: logs/S9_report.md:162 and Hariom's note) (corrected in Part C; see Errors).
    - The rendering of the README block on GitHub was not viewed.
11. **Errors and retries:** none found during Part B. Two errors in this section were found later and are listed in Part C, heading 11.
12. **Risks + what I need from Hariom**
    - Risk: PR-25 (template drift) stays open.
    - Need: OK for the B1 and B2 commits, then "OK plan" for Part C.

B1 committed by Hariom as `9e0a85c`, B2 as `8d48024`.

## Part C — final (2026-11-14)

### Commits (source: `git show --numstat 06ea75b 9e0a85c 8d48024`)
| Commit | File | Added | Deleted |
|---|---|---|---|
| 06ea75b (A) | eval/s10/manifests/prereg_v1.json | 11 | 0 |
| 06ea75b (A) | eval/s10/prereg_freeze.py | 66 | 0 |
| 06ea75b (A) | tests/test_s10_prereg_manifest.py | 41 | 0 |
| 9e0a85c (B1) | CLAUDE.md | 16 | 24 |
| 9e0a85c (B1) | eval/s10/split_guard.py | 1 | 1 |
| 8d48024 (B2) | .env.example | 7 | 0 |
| 8d48024 (B2) | README.md | 5 | 0 |
| 8d48024 (B2) | env.example | 2 | 1 |

### Pre-registration manifest (source: run 1 output, `Get-FileHash`)
| Field | Value |
|---|---|
| file | specs/S10_PREREGISTRATION.md (local, git-ignored) |
| sha256_lf | 84934b357da1fceff619a40d0a8595ad32d271d22ae734ed11fb0a8d8988eefe |
| bytes_lf / lines | 27970 / 174 |
| manifest file sha256 | 59A5BF6FB0A7058005953DF17131E3B00FA2AEDBED43A98793A6B76CF113BDF7 (same after the refused run 2) |

### Old-handle count per file (source: `git grep -n -i shyam` at HEAD 8d48024, tracked files only; before this report is committed)
Total: 65 lines in 24 files (was 66 at 8d95a46).

| File | Lines |
|---|---|
| CLAUDE.md | 1 (the new note line) |
| eval/s10/split_guard.py | 0 (was 1) |
| docs/decisions/0027-normaliser-v1-1-freeze-amendment.md | 1 |
| goldset/ENTITY_GUIDELINE_v1.1.md | 2 |
| logs/S10a_report.md | 8 |
| logs/S8y_report.md | 1 |
| logs/S8z_report.md | 6 |
| logs/S9_report.md | 2 |
| retrieval_eval/S8_PREREGISTRATION_ADDENDUM_2.md | 11 |
| retrieval_eval/S8_PREREGISTRATION_ADDENDUM_2_NOTE_2.md | 1 |
| retrieval_eval/S8_PREREGISTRATION_ADDENDUM_2_NOTE_3.md | 1 |
| retrieval_eval/S8_PREREGISTRATION_ADDENDUM_2_NOTE_4.md | 1 |
| retrieval_eval/S8_PREREGISTRATION_ADDENDUM_4.md | 1 |
| retrieval_eval/S8_PREREGISTRATION_ADDENDUM_4_NOTE_1.md | 1 |
| retrieval_eval/S8_RELEVANCE_GUIDELINE_v1.0.1.md | 2 |
| retrieval_eval/S8_RELEVANCE_GUIDELINE_v1.0.md | 2 |
| retrieval_eval/queries.yaml | 1 |
| retrieval_eval/s8_judging.py | 1 |
| retrieval_eval/s8_label_tool.py | 1 |
| retrieval_eval/s8_labels.py | 3 |
| tests/test_s8_analysis.py | 1 |
| tests/test_s8_e2e_chain.py | 7 |
| tests/test_s8_label_tool.py | 2 |
| tests/test_s8_labels.py | 5 |
| tests/test_s8_three_judges.py | 3 |

Once this report is committed, its own lines that hold the literal (the `git grep` command and the exact S10h-07 quotes) also count.

### Open item
- **PR-25 (new):** two env templates (.env.example, env.example) drift; merge later after an exact list + Hariom's yes.

### Where it fails
- The sha check relies on Hariom downloading the exact approved file. A different download or encoding gives a different sha, and the freeze refuses.
- `specs/` is local only (git-ignored). The committed manifest is the public proof of which version was frozen; outsiders can check the sha only if they have the file.
- The manifest is write-once. A wrong field can only be fixed by Hariom deleting it by hand, or by a v2 manifest.
- Older records (logs, retrieval_eval/, goldset/, docs/decisions/, test names, the audit file name) still use the old handle, by design (S10h-07).

### SELF-AUDIT (whole of S10h)
1. **Asked vs done**
   - Part A: branch + status, baseline, ignore check, script, 2 runs, test, full suite: done (Part A section).
   - Part B: CLAUDE.md, split_guard docstring, 2 templates + README, old-handle count, full suite + locks + frozen diff: done (Part B section).
   - Part C: PLAIN SUMMARY, Parts A–B results, manifest sha, count table, PR-25, this SELF-AUDIT, "where it fails", final full suite: done (this file; suite output below).
2. **Decisions I made myself**
   - Part A: reuse `normalised_sha`; exit codes 1/2/3; the line-count rule; field order kept.
   - Part B: README bullet text from `embed/token_stats.py`; S10 block appended at the end of `.env.example`.
   - Decisions added by Hariom:
     - **S10h-04b** (during Part B): CLAUDE.md closing line → "At the end of the whole task, finish logs/S##_report.md (see Standing rules)."
     - Part B: Q1 keep the SELF-AUDIT intro line; Q2 remove rule 9's "Also list every file…" line.
     - Part C: Q1 fix errors in place with a marker; Q2 write the tool folder as `%TEMP%\<tool session folder>\tasks\`; Q3 write "the old handle" except in the `git grep` command and the exact S10h-07 quotes.
3. **Deviations**
   - Files written outside the repo, in `%TEMP%\<tool session folder>\tasks\`. Listing at Part C: `b0ymksu05.output` 106 B, `bc81i1gbl.output` 209 B, `blzvju1be.output` 191 B, `b9rdfnn2j.output` 0 B.
     - The first two are from Part A (details in Part A, heading 3).
     - `blzvju1be.output` is the final Part C suite run. It was moved to the background by Hariom during the run, so the tool saved its output there. It holds 3 suite summary lines, `exit=0`, `frozen-diff-lines=0`, the old-handle count (65 lines, 24 files), the HEAD `8d48024` and 1 status line.
     - `b9rdfnn2j.output` (0 B) is a transient capture for the listing command.
     - Effect: counts and names only, no secrets. Hariom will delete them; I deleted nothing.
   - No other deviations.
4. **How I implemented it**
   - Files (commit + one line each):
     - `eval/s10/prereg_freeze.py`: freeze script (A).
     - `eval/s10/manifests/prereg_v1.json`: write-once manifest (A).
     - `tests/test_s10_prereg_manifest.py`: read-only manifest test (A).
     - `CLAUDE.md`: rules 7/9, 12 headings, closing line, old-handle note (B1).
     - `eval/s10/split_guard.py`: docstring name (B1).
     - `.env.example`, `env.example`, `README.md`: Windows notes (B2).
     - `logs/S10h_report.md`: this report (C).
   - Part C commands: `.\.venv\Scripts\python.exe tasks.py test` (with `git diff --stat main -- standards/ retrieval_eval/`, `git grep -n -i shyam`, `git rev-parse --short HEAD`, `git status --porcelain` in the same call) · `git log --oneline -4` · `git show --stat` / `git show --numstat 06ea75b 9e0a85c 8d48024` · `Get-ChildItem` of the tool output folder · attribution/key grep on this file.
5. **Checks with real output**

   | Suite | Before (8d95a46) | After A | After B | Final (C, at 8d48024) |
   |---|---|---|---|---|
   | mcp | 20 passed | 20 | 20 | 20 passed |
   | obs | 1 passed | 1 | 1 | 1 passed |
   | tests | 1215 passed / 1 skipped | 1217 / 1 | 1217 / 1 | 1217 passed / 1 skipped / 0 failed |

   - New tests: 2.
   - Manifest sha matches. Old-handle count 65. Frozen diff 0 lines.
6. **Frozen items:** `git diff --stat main -- standards/ retrieval_eval/` → 0 lines at A, B and C. The lock tests passed: `tests/test_s10_ruleset.py` (ENGINE_FILES_SHA256, RULESET_FILES_SHA256) and `tests/test_s10_eval_runner.py` (PROMPT_SHA256). Corpus and freeze manifests were not touched.
7. **Holdouts and sealed files:** none opened, printed, hashed or scored. That covers the S8 c2 files, the judge folders, the S10 holdout/draft/manifest files, and every other holdout.
8. **Safety**
   - No commit or push by me; Hariom made the 3 commits.
   - `.env` never opened; no keys printed.
   - Files outside the repo: see heading 3.
   - No AI attribution in any text. The only hits of the attribution grep on this file are the repo file name `CLAUDE.md`.
   - No guard/hook blocks.
9. **Paid/external calls:** 0.
10. **Not checked / assumed**
    - That the local spec is the approved v1.0 file. This rests only on the sha match.
    - The README rendering on GitHub was not viewed.
    - `test_env_template` reads `.env.example` with the default encoding. The added text is ASCII (0 non-ASCII bytes), so it is safe here, but a later non-ASCII comment could fail on Windows.
11. **Errors and retries**
    - Part B result: I gave the CLAUDE.md change as +14 / −26, counted by hand. The correct figure is +16 / −24 (`git show --numstat 9e0a85c`). Hariom found it, and it is fixed in place.
    - Part B heading 10: I wrote that logs/S9_report.md held only the host fix and that the 130 s figure came from the prompt. In fact logs/S9_report.md:162 records it ("connect took about 130 s … 127.0.0.1 (0.2 s)"); I had not checked. Hariom found it, and it is fixed in place.
    - Part A heading 3 first said "no deviations". It was corrected after Hariom asked about the outside-repo write.
12. **Risks for later steps + what I need from Hariom**
    - PR-25: merge the two env templates later, after an exact list and Hariom's yes.
    - Committing this report adds the lines that quote the old handle to the `git grep` count.
    - Need: delete the tool output files listed in heading 3 (human step), commit this report, then open the PR.
