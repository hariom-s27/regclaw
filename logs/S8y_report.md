# S8y — Commit-history check (history kept unchanged)

**Date:** 2026-11-13 · **Status:** done (closed by decision PR-09 = A)

## PLAIN SUMMARY
1. Checked all 113 commits on `main`: commits with attribution lines by category: T1 48, T2 0, T3a 22, T3b 0 (48 commits with any category); 22 commits carry an assistant-vendor author/committer email (the same 22 commits as T3a).
2. A full message + identity rewrite was built and proven locally (10/10 proofs + a negative test), but **not published**.
3. Decision PR-09 = A: keep the history as is. **No public change:** no GitHub setting, repo, branch or history was changed; nothing was pushed.
4. Backup of the whole GitHub repo: bundle sha256 `9EC76F6A58C67A8385106D71403491575805E61E2FD5BF84790A3E954876BBD7` (797,056 bytes), `git bundle verify` okay, restore test passed.
5. A local `commit-msg` hook (not versioned) now rejects new T1/T2/T3 lines; tested with 7 message files, all as expected.

## Categories (S8y-02; per line, case-insensitive, trailing CR stripped)
- **T1:** co-author trailer line with the assistant vendor's no-reply address.
- **T2:** "generated with <assistant tool>" footer line (optional leading symbol).
- **T3a:** session-id trailer line. **T3b:** session URL line.
- **Identity:** author or committer email at the assistant vendor's domain.
- **Residual (information only):** a line naming the assistant or vendor that matches no category.

## Counts
| Item | main (113 commits) | all refs (113 commits) | Source command |
|---|---|---|---|
| T1 | 48 | 48 | `py -3 -I D:\regclaw_s8y\scripts\count_hits.py --repo . --rev main` / `--rev=--all` |
| T2 | 0 | 0 | same |
| T3a | 22 | 22 | same |
| T3b | 0 | 0 | same |
| any T1/T2/T3 | 48 | 48 | same |
| identity commits | 22 | 22 | same, `--identities` |
| T3a ∩ identity | 22 (0 only-T3a, 0 only-identity) | – | `py -3 -I D:\regclaw_s8y\scripts\s8y_info.py .` |
| distinct vendor addresses | 1 (44 author/committer fields) | – | same |
| residual (total / excluding the instructions filename) | 13 / 9 | 13 / 9 | `count_hits.py` |

Residual classification (S8y-15, `s8y_info.py` + manual review, no text quoted): 7 product/design mentions, 1 filename, 1 standard merge line naming a branch (`af78077`); 0 authorship statements; all stay.
Tags: 0 (`git tag`, `git tag --merged main`). Workflow secret references: 0 (`git grep -noE "secrets\.[A-Za-z_]+" -- .github/workflows`).

## What was built but not used (information)
Work folder `D:\regclaw_s8y\` (kept as is): `backup\` (mirror + bundle), `rewrite\` (unpublished rewritten history, `main` = `2dbd27bf9293fe698e239db0b122b841ac82485f`, **no remote**), `toolvenv\` (git-filter-repo 2.47.0, Python 3.14.3), `scripts\`, `proof\`.

Proof table of the unpublished rewrite (`py -3 -I D:\regclaw_s8y\scripts\proofs.py after`):

| Check | Expected | Got | Result |
|---|---|---|---|
| (a) commits on main | 113 | 113 | PASS |
| (b1) sha256 of ordered (tree, author date, committer date) list | equal | `af105215cee1…` both | PASS |
| (b1′) per-pair tree/date diffs | 0 | 0 | PASS |
| (b2) non-identity commits, identity unchanged | 91 | 91 | PASS |
| (b3) identity commits mapped to the owner identity | 22 | 22 | PASS |
| (b) unexpected identity changes | 0 | 0 | PASS |
| (b4) vendor identities after | 0 | 0 | PASS |
| (c) T1/T2/T3a/T3b after | 0/0/0/0 | 0/0/0/0 | PASS |
| commit-map pairs | 113 | 113 | PASS |
| (d) unexpected messages (unchanged 65, clean 48, id-only 0) | 0 | 0 | PASS |
| (e) hits before == changed by clean | 48 | 48 | PASS |

Negative test (no-op clean): (d) 48 unexpected, (e) 0 → FAIL as expected, so the check is not vacuous. Restore test: `git clone <bundle>` → `main` = `c15d3ea2d93615ba3221dbf81d7110ab4bd33f48`, 113 commits, clean tree.
`logs/S8y_commit_map.csv` was **not** written (no rewrite was published; approved).

## Local commit-msg hook (S8y-10)
`.git/hooks/commit-msg` (POSIX sh, Git for Windows; not versioned). Reads the message file, strips CR, runs `LC_ALL=C grep -i -E` with POSIX-ERE versions of T1/T2/T3a/T3b; on a hit prints the category name to stderr and exits 1.
Tests (`git hook run commit-msg -- <file>`, files in `D:\regclaw_s8y\proof\hooktest\`, no commit created):

| # | Case | Expected | Exit |
|---|---|---|---|
| 1 | clean message | 0 | 0 |
| 2 | T1 line | 1 | 1 (T1) |
| 3 | T1 line, CRLF | 1 | 1 (T1) |
| 4 | T2 line with emoji prefix | 1 | 1 (T2) |
| 5 | T3a line | 1 | 1 (T3a) |
| 6 | T3b line | 1 | 1 (T3b) |
| 7 | near-miss (filename, vendor SDK, phrase mid-sentence) | 0 | 0 |

## Limits
- The 48 T-commits and 22 identity commits stay visible on GitHub (commit pages, PR pages) and in any forks (fork count not checked).
- The hook is local to this clone only: it is not versioned, so it does not act in other clones, in cloud-session commits, or on GitHub web merges. The 22 identity commits came from such routes.
- The hook does not change existing history, and `git commit --no-verify` skips it.
- `LC_ALL=C` makes matching byte-wise: any non-ASCII prefix counts as non-word for T2 (slightly broader than the Unicode rule used in the counts); a T2-style line inside a `#` comment of the editor template would also be rejected.
- Old commit ids quoted in docs and tests (40 mentions, 23 commits) stay valid because history is unchanged; the blob-id test change (PR-14) is not needed now.

## SELF-AUDIT
1. **Asked vs done:** Part A done (counts, tags 0, identity 22 → STOP per S8y-08). S8y-13/14/15/16 done. Part B done (backup, venv, rewrite, 10/10 proofs + negative test). Restore test done. Part C/D not done (stopped by PR-09 = A). Final: hook installed + 7/7 tests; this report; test suite (see 5).
2. **Decisions I made myself:** `--date=raw` for date comparison; normalise a message only when a line was dropped; one shared regex module; filter-repo unpinned (2.47.0 recorded); residual split by the instructions filename; an extra category "branch name" for `af78077`; negative test; restore-test extras (branch, status); `LC_ALL=C` in the hook (see 3).
3. **Deviations:** (a) hook: `LC_ALL=C` added after test 4 failed (GNU grep 3.0 on Windows does not match a 4-byte UTF-8 character with `[^[:alnum:]_]` in UTF-8 locales); patterns unchanged; effect: byte-wise matching (see Limits). (b) One stderr redirect went to Git Bash `/tmp` (see 11). (c) Part D fetch/reset/id-mapping and the commit-map CSV dropped by decision.
4. **How I implemented it:** created `D:\regclaw_s8y\scripts\{count_hits.py, s8y_info.py, make_mailmap.py, clean_messages.py, proofs.py, s8y_mailmap.txt}`; `D:\regclaw_s8y\proof\{before_list.txt, after_list.txt, commit-map, message_check.csv, ident_check.csv, residual_lines.txt, negtest\, restore_test\, hooktest\}`; `D:\regclaw_s8y\backup\{regclaw.git, regclaw_all.bundle}`; `D:\regclaw_s8y\{toolvenv, rewrite}`; `.git/hooks/commit-msg`; `logs/S8y_report.md`. Main commands: `git clone --mirror`, `git bundle create/verify`, `git clone --no-local --single-branch --branch main`, `clean_messages.py` (one filter-repo pass with `--mailmap` + message callback), `proofs.py before/after`, `git clone <bundle>`, `git hook run commit-msg -- <file>`, `.venv\Scripts\python tasks.py test`.
5. **Checks with real output:** counts and proof table above; self-tests regex 12/12, clean 7/7; hook 7/7; bundle sha256 above; test suite (`.venv\Scripts\python tasks.py test`, exit 0): main 1022 passed / 1 skipped / 0 failed, mcp 12 passed, obs 1 passed (before = after: no code changed in this step).
6. **Frozen items:** untouched — no tracked file changed except the new `logs/S8y_report.md`; `queries.yaml`, freeze manifests, qrels, prompt freeze not touched; history unchanged.
7. **Holdouts and sealed files:** not opened, printed, hashed or scored; only git objects and tracked tests/workflow read.
8. **Safety:** no commit, no push, no GitHub change, no key printed, `.env` not opened; writes only in the repo and `D:\regclaw_s8y`, except one empty stray file (see 11); no AI attribution in any text; no guard/hook block happened.
9. **Paid/external calls:** 0 paid; external: 1 `git ls-remote`, 1 mirror clone (GitHub), 1 PyPI install; cost 0.
10. **Not checked / assumed:** fork count of the GitHub repo; that the hook runs the same in GUI clients (they call the same hook); non-ASCII T2 prefixes other than emoji tested only via the C-locale rule.
11. **Errors and retries:** (a) PowerShell 5.1 NativeCommandError on git progress output with `2>&1` (display only). (b) hook test 4 failed (exit 0) → locale cause found, `LC_ALL=C` added, all 7 rerun → pass. (c) rule slip: the first hook test run redirected stderr to Git Bash `/tmp` = `C:\Users\hario\AppData\Local\Temp\null_err_s8y` (0 bytes) — outside the approved folders; not deleted (deletion is a human step).
12. **Risks + what I need from Shyam:** `D:\regclaw_s8y\rewrite` holds rewritten history with no remote — never push from it; the hook must be re-installed in any new clone; delete the stray temp file above if wanted; OK for the commit of this report.
