# S8z — repo hygiene (line endings, standing rules, attribution scan)

**Date:** 2026-11-13 · **Branch:** `tidy/s8z-hygiene` (from `main` @ `f71db3d`) · **Status:** done

## PLAIN SUMMARY
1. `.gitattributes` now forces LF for `retrieval_eval/*.json`, `*.md`, `*.yaml`; committed as `977dc99`; all 46 files in `retrieval_eval/` hash-identical before and after.
2. CLAUDE.md gained a "Standing rules (from 13 Nov 2026)" section: 12 lines added, 0 deleted; committed as `fea472e`.
3. Tracked-file scan: 6 hits, 0 authorship/attribution text; 3 are agent-instruction lines (CLAUDE.md), 3 are judge-model configuration in frozen pre-registration files.
4. History: 57 commits carry trailer term T1 and 4 carry trailer term T2; all are on `main`; nothing was rewritten.
5. Tests: 1022 passed / 1 skipped / 0 failed in `tests/`, plus 12 (`mcp/`) and 1 (observability) passed, at every step.

## Part A — line endings (S8z-01)
- Start state: `git status --porcelain` empty; `git rev-list --left-right --count main...origin/main` → `0 0`; HEAD `f71db3d67580f42ca75a4219e76d00eabcd424d0`.
- Before: `.gitattributes` none; `git config core.autocrlf` → `true`.
- `git ls-files --eol retrieval_eval/`: 35/35 index entries `i/lf`.
- Repo-wide `git ls-files --eol`: 253 `i/lf`, 1 `i/none`, 0 `i/crlf` / `i/mixed`.
- Added lines (LF, 0 CR bytes):
  ```
  retrieval_eval/*.json text eol=lf
  retrieval_eval/*.md text eol=lf
  retrieval_eval/*.yaml text eol=lf
  ```
- `git add --renormalize --dry-run .` listed all 254 tracked paths (`git ls-files` = 254). Ruled by Shyam: with `--dry-run` git lists every path; the accepted evidence is 0 `i/crlf` index entries. No real renormalize was run.
- SHA256 (`Get-FileHash`) of every file in `retrieval_eval/` (35 tracked + 11 untracked `__pycache__/*.pyc`): **46/46 identical** before vs after the change; 35/35 tracked identical after the test run.
- Working-copy CRLF files in `retrieval_eval/` (from `git ls-files --eol retrieval_eval/`, `w/crlf`). Their on-disk bytes are CRLF while the index is LF:
  - `retrieval_eval/S8_PREREGISTRATION_ADDENDUM_2_NOTE_4.md`
  - `retrieval_eval/s8_analysis.py`
  - `retrieval_eval/s8_label_tool.py`
  - `retrieval_eval/s8_labels.py`
  - `retrieval_eval/s8_notes_freeze.json`
  (An earlier STOP message said 6; the command output shows 5.)

## Part B — CLAUDE.md standing rules (S8z-02)
- Appended at the end: one blank line, a `##` heading, 10 bullet rules (wording as approved; markdown heading/bullets added because the pasted text had lost its formatting).
- `git diff --numstat` → `12 0 CLAUDE.md`; `git status --porcelain` → only CLAUDE.md modified.
- Appended with CRLF to match the working copy (index stays LF via `core.autocrlf=true`).

## Part C — read-only attribution scan (S8z-03 / S8z-04)

### C1. Tracked files
Command: `git grep -n -i -E "<attribution search pattern>"` → 6 hits.

Categories: (a) authorship/attribution text · (b) product/eval LLM configuration · (c) agent-instruction file.

| File | Line | Cat. |
|---|---|---|
| CLAUDE.md | 1 | c |
| CLAUDE.md | 19 | c |
| CLAUDE.md | 117 | c |
| retrieval_eval/S8_PREREGISTRATION_ADDENDUM_2.md | 13 | b |
| retrieval_eval/S8_PREREGISTRATION_ADDENDUM_2_NOTE_3.md | 12 | b |
| retrieval_eval/S8_PREREGISTRATION_ADDENDUM_2_NOTE_v1.0.1.md | 7 | b |

- (a) authorship/attribution: **0**.
- (b) 3 hits, all in frozen `retrieval_eval/` pre-registration files naming the tool used to run relevance Judge A. These are part of the evaluation method; not changed.
- (c) 3 hits in CLAUDE.md, including the new standing rule that forbids such trailers; not changed.

### C2. Commit history
Commands: `git log --all --format="%h %s" --grep="<term>" -i` and `git log main --grep="<term>" -i --oneline` (count only).

| Term | All refs | Newest | Oldest | On main |
|---|---|---|---|---|
| T1 | 57 | f71db3d | 0195347 | 57 |
| T2 | 4 | 08abcbe | 0195347 | 4 |

T1/T2 = the two trailer search terms from prompt step C2.

- Nothing was rewritten. Note: `f71db3d` (S8 Part D qrels freeze) is among the trailer commits; any history rewrite would change hashes that freeze records or PRs may cite.

### C3. Ignore check
`git check-ignore -v logs/S8z_report.md` → no output, exit 1: `logs/` is not ignored.

## SELF-AUDIT

### 1. Asked vs done
- A1–A6 done (outputs above). A7 passed under Shyam's ruling (0 `i/crlf`; dry-run gate waived). A8 done, 46/46 identical. A9 done, tests green. A10 STOP given; committed by Shyam as `977dc99`.
- B1 done (`977dc99` shown). B2 done. B3 done (`12 0 CLAUDE.md`). B4 done, tests green. B5 STOP given; committed by Shyam as `fea472e`.
- C1 done (table C1). C2 done (table C2). C3 done (not ignored). C4 done (this file). C5 done (git status + tests, see section 5). C6 STOP.

### 2. Decisions I made myself
- `.gitattributes` patterns: read the pasted `retrieval_eval/.json` / `.md` as `*.json` / `*.md` (markdown had eaten the `*`); rejected the literal text (matches nothing). Confirmed by Shyam.
- Hashed all 46 files in `retrieval_eval/` incl. untracked `.pyc` ("every file"); tracked-only would also have been valid.
- CLAUDE.md section formatted as `##` heading + `-` bullets; rejected plain lines (would render as one paragraph). Appended with CRLF to leave existing bytes untouched.
- Category for the 3 pre-registration hits: (b), because they record which tool ran Judge A (method/configuration), not who wrote the file.

### 3. Deviations
- Ran extra read-only checks: repo-wide eol summary, dry-run line count, post-test re-hash. No effect on files.
- Corrected my own count of `w/crlf` files from 6 to 5.
- Otherwise no deviations.

### 4. How I implemented it
- `.gitattributes` — created, 3 lines (committed `977dc99`).
- `CLAUDE.md` — 12 lines appended (committed `fea472e`).
- `logs/S8z_report.md` — this report (new).
- Helper outside the repo (session scratchpad): `append_rules.py` (append-only), plus hash list and test logs.
- Commands: `git status --porcelain`; `git fetch`; `git rev-list --left-right --count main...origin/main`; `git rev-parse HEAD`; `git checkout -b tidy/s8z-hygiene`; `git config core.autocrlf`; `git ls-files --eol retrieval_eval/`; `git ls-files --eol`; `Get-FileHash -Algorithm SHA256` loop over `retrieval_eval`; `git add --renormalize --dry-run .`; `git diff --cached --name-only`; `.venv\Scripts\python tasks.py test` (×3); `git log --oneline -1`; `git ls-files --eol CLAUDE.md`; `.venv\Scripts\python -I append_rules.py CLAUDE.md`; `git diff --stat`; `git diff --numstat`; `git grep -n -i -E "<pattern>"`; `git log --all --format="%h %s" --grep=<term> -i`; `git log main --grep=<term> -i --oneline`; `git check-ignore -v logs/S8z_report.md`.

### 5. Checks with real output
Tests (`.venv\Scripts\python tasks.py test`, exit 0 each run):

| Run | mcp/ | observability | tests/ passed | skipped | failed |
|---|---|---|---|---|---|
| Part A (A9) | 12 | 1 | 1022 | 1 | 0 |
| Part B (B4) | 12 | 1 | 1022 | 1 | 0 |
| Part C (C5) | 12 | 1 | 1022 | 1 | 0 |

- Tests before / new / total: 1022+1 skipped / 0 new / 1022+1 skipped.
- `retrieval_eval` sha: 46/46 identical.
- Renormalize dry-run: 254 paths listed (= all tracked; waived per ruling).
- Diff stats: Part A `.gitattributes` new file (3 lines); Part B `12 0 CLAUDE.md`.

### 6. Frozen items
- `retrieval_eval/` contents untouched: 46/46 identical after the change, 35/35 tracked identical after tests. No content edits in any step.

### 7. Holdouts and sealed files
- None of the sealed files opened, printed, copied, hashed or scored. Nothing under `data/` read (only the path `data/README.md` appeared in the dry-run list).

### 8. Safety
- No commit or push by me; no keys printed; `.env` not opened; nothing written outside the repo except the session scratchpad; no AI attribution in any text I wrote; no guard/hook blocks occurred.

### 9. Paid/external calls
- 0 API calls. One `git fetch`.

### 10. Not checked / assumed
- The new attributes rewrite the 5 CRLF working files to LF only on re-checkout or fresh clone; not re-checked-out here. Any hash check over working-copy bytes of those 5 files currently sees CRLF.
- History counts use `--grep`, which matches anywhere in the message, not only trailer lines; a message that merely mentions the term would be counted.

### 11. Errors and retries
- Dry-run gate failed (listed all paths) → stopped, reported; Shyam ruled it a prompt error. No retries of anything.

### 12. Risks for later steps + what I need from Shyam
- Decision on the 57 + 4 history trailers: leave them, or rewrite history. A rewrite changes every hash from `0195347` onward, including `f71db3d` (qrels freeze) and any hashes cited in freeze manifests/PRs; recommendation: leave history as is and rely on the new standing rule going forward.
- CLAUDE.md now names a 12-heading SELF-AUDIT, while the older section describes 10 headings; consider reconciling.
- After merge, a fresh clone should be checked with `git ls-files --eol retrieval_eval/` (expect all `w/lf`) and the freeze tests.
