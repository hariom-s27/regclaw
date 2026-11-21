# S10c-1 report: S10c code on fakes, ready for code lock v4

Branch `s10c1/code` · base `main` = 39b1156 (PR #29) · HEAD 70aa094 · not pushed yet · 21 Nov 2026

## §0 PLAIN SUMMARY
1. S10c-1 wrote and tested all the code that S10c needs: the dev export, the candidate build, the pilot and main driver, the analysis, the code lock and two helpers.
2. Everything ran on fake data only. No paid call was made, and no real call ledger exists yet.
3. The work is 10 commits: 19 files changed, 8152 lines added and 13 removed.
4. Three separate read-only reviews found gaps. The biggest was call counting under crashes and parallel runs, and it is now fixed in code.
5. Both fresh test runs at 70aa094 passed (exit 0). Next: push, PR, CI, then lock v4 and the addendum A4 freeze.

## §1 What S10c-1 built

**dev_export** (`eval/s10/dev_export.py`). Builds the write-once dev reference export. It finds the 10 dev profiles by matching their content against the judge file, so the sealed id map is never opened. It writes the dev gold file (format v2), the per-cell dev table, an export summary and a committed manifest. It also computes the audit-A numbers before the second seal: per-judge binary under-call and over-call gaps against Hariom's labels, plus the E1 and E2 inputs, checked against the locked label-quality functions. Command: `python -m eval.s10.dev_export run`. It refuses with exit 3 if any output already exists. It refuses with exit 1 on a wrong input sha, a merge sha that differs from the A2 value, a ruleset change, or a content match that is not exactly 10 unique matches and 20 non-matches. It refuses with exit 2 on an invalid audit judge label, a sealed or out-of-repo path, or any other validation error. It prints only counts, shas and check names.

**frozen_candidates** (`eval/s10/frozen_candidates.py`). Calls the real hybrid retriever once per distinct query and saves the candidates. Replaying them later means every setting and repeat sees the same candidates. Command: `python -m eval.s10.frozen_candidates build --profiles P --cache-dir DIR --out FILE --manifest M`. Before any read it checks the paths (no sealed names, inside the repo, no "s8"). It then checks the environment, the tracing switch, code lock v4, the ruleset and the dev profiles. During the build it checks again before each query. Each query first reserves 11 requests in the global call ledger, then settles at the real count. It refuses with exit 3 if an output or completion record already exists. It refuses with exit 2 if another paid process holds the lock, if `REGCLAW_RUN_DIR` is missing, if the global ledger is shorter than the folder ledger, if the folder is stopped, if the profiles are not the dev split, or if an input is unreadable or invalid (`input_unreadable` / `input_invalid`).

**live** (`eval/s10/live.py`). The guarded pilot and main driver. It runs the interleaved loop itself and reserves each run in the ledger before calling it (6 requests per classify run). Commands: `python -m eval.s10.live pilot --profiles --candidates --candidates-manifest --candidates-run-manifest --out-dir`; `main` takes the same options plus `--gold --tags --pilot-settings`. Preflight refuses with exit 2 when: stub mode is on, the provider is wrong, the run folder is invalid, tracing is not explicitly off, the lock does not match, the candidates or profiles do not check out, main's cap or timeout differs from the pilot values, the cap headroom is too small, or another paid process is running. The pilot exits 1 with "pilot gate failed" when a required field is missing. A stop during the run (cap reached, 3 error runs in a row, interrupt, lock or guard failure) writes `STOPPED.json` and exits 4. A stopped folder is never scored.

**analysis** (`eval/s10/analysis.py`). Computes only the registered S10c dev outputs from a completed main run: primary and secondary metrics on the 8 strata profiles, traps in their own table, the effort rule with exact fractions, the sign-flip test, the six-version robustness band, split-cell two-way scoring, tipping ranges, and an exploratory classic PPI ratio. It makes no model calls. Command: `python -m eval.s10.analysis --run-dir D --gold G --dev-cells C --tags T --export-summary S --out A.json`. It refuses with exit 2 if the run was stopped, the lock sha differs, an input changed during the run, or the export-manifest sha or the global ledger fields do not tie back to main. It refuses with exit 3 if the output already exists.

**s10c_lock** (`eval/s10/s10c_lock.py`). Writes code lock v4 once. Options: `--show` prints one line per source file plus a listing sha and writes nothing. `--save-listing` saves that listing once. Creating the lock needs `--reviewed-listing` and `--expected-listing-sha`. Exit codes: 0 shown or written, 1 digest mismatch, 2 input or output validation, 3 destination already exists. Test and environment details are stored as provenance only and are not enforced. LOCKED has 58 files. It grew from 56 at the first lock design, to 57 when `addendum_freeze_n.py` was added (F-01), to 58 when `paid_lock.py` was added (FX-01d).

**addendum_freeze_n** (`eval/s10/addendum_freeze_n.py`). Freezes one approved numbered addendum (A4 onward) into a write-once manifest. Command: `python -m eval.s10.addendum_freeze_n --number N --file F --expected-sha SHA`. It refuses with exit 2 if the number is below 4, the file name does not fit the number, the sha is malformed, or the file's sha differs. It refuses with exit 3 if the manifest already exists.

**paid_lock** (`eval/s10/paid_lock.py`). A small non-blocking process lock built only on the standard library, with no command of its own. The candidate build and the live driver hold it on the sidecar file `data/runs/s10c_paid.lock` for the whole paid phase. A second paid process is refused at once ("another paid S10c process is running"; the caller exits 2). The sidecar file is kept, never deleted.

## §2 Commits (oldest first)

| # | hash | subject | files changed |
|---|---|---|---|
| Part A | 2070ff6 | Implement S10c Part A | 5 (+751 / −10) |
| Part B | cdb2542 | Add S10c dev reference export | 2 (+878) |
| Part C | 540acf8 | Add guarded S10c pilot and main driver | 4 (+1409 / −15) |
| Part D | d25197f | Add preregistered S10c dev analysis | 2 (+1253) |
| Part E | dd303cf | Add S10c v4 code-lock tooling | 2 (+811) |
| F-fixes | 5e846da | Close S10c validation gaps before lock v4 | 11 (+638 / −16) |
| G-fixes | 9488761 | Apply S10c audit fixes G-01 to G-12 before lock v4 | 8 (+712 / −111) |
| FX-01 | 5c92022 | Make S10c paid accounting durable and process locked | 9 (+1045 / −128) |
| FX-02/03 | 85627cc | Enforce S10c environment and analysis checks | 6 (+606 / −26) |
| G3 | 70aa094 | Apply S10c G3 fixes before lock v4 | 9 (+354 / −12) |

Whole range 39b1156..70aa094: 19 files changed, 8152 insertions, 13 deletions.

## §3 Decisions

The file named is where the decision mainly lives. "Run sheet" means the later S10c-2 run sheet.

**S10c table (approved 19 Nov; later changes marked)**
- S10c-01: the classifier is the configured Azure gpt-5-mini; its results are labelled provisional. → `eval/s10/live.py`
- S10c-02: dev profiles are found by content match against the judge file; refuse unless 10 unique matches and 20 non-matches. → `eval/s10/dev_export.py`
- S10c-03: the dev export runs before the second seal. → `eval/s10/dev_export.py` (order in run sheet)
- S10c-04: a new gold format v2 with a reference block and no source field; v1 stays as it was. → `eval/s10/formats.py`
- S10c-05: the export holds final label, routing, split flag, the three judge labels and the human labels per dev cell; write-once with a manifest. → `eval/s10/dev_export.py`
- S10c-06: a timeout is never retried; other errors keep up to 3 tries; per-request timeout T, and 2 × T on the truncation re-run. → `llm/azure_openai.py`
- S10c-07: a new live driver using the runner's client factory, frozen candidates and a 150-call cap. → `eval/s10/live.py`
- S10c-08: a write-once tags command. → `eval/s10/tagger.py`
- S10c-09: a frozen-candidates command with the cache guard and counted calls. → `eval/s10/frozen_candidates.py`
- S10c-10: the pilot follows the frozen plan; code prints the cap and T rules. → `eval/s10/live.py`
- S10c-11: the main run follows the frozen plan: 100 interleaved runs in one write-once folder. → `eval/s10/live.py`
- S10c-12: analysis computes registered outputs only. Changed: the LOJO rule now follows the frozen text (the higher label; Hariom's audit label is never used, D-02). → `eval/s10/analysis.py`
- S10c-13: an exploratory PPI row. Changed: classic PPI with lambda 1 as a ratio, in a profile bootstrap, never ppboot (D-06/D-08). → `eval/s10/analysis.py`
- S10c-14: code lock v4 before any export or paid call, after "yes lock". Changed: the lock now comes after push and CI (A-01). → `eval/s10/s10c_lock.py`
- S10c-15: "no addendum A4". Overturned: A4 is written before the dev export (A-04). → addendum A4
- S10c-16: the spend cap stays at 150 calls. Changed: it now applies to the global total, with headroom checks (G-07). → `eval/s10/live.py`
- S10c-17: local prerequisites are checked in the later run sheet. → run sheet
- S10c-18: the fixed order of steps from S10c-1 to the analysis. → run sheet
- S10c-19: this step writes code and fake tests only; Hariom runs git, pytest and anything on real data. → process (no file)
- S10c-20: the working setup for the step; the report goes to `logs/S10c1_report.md`. → `logs/S10c1_report.md`
- S10c-21: the driver refuses an output folder that exists and is not empty; the runs file gets a sha manifest. → `eval/s10/live.py`
- S10c-22: primary and secondary numbers on the 8 strata profiles; traps apart; the runner summary is marked diagnostic. Changed: LOJO as in S10c-12 (D-02). → `eval/s10/analysis.py`
- S10c-23: the driver owns the loop, checks the cap before each run, stops after 3 error runs in a row, and never scores a partial folder. → `eval/s10/live.py`
- S10c-24: ppi-python is an optional local extra, imported lazily, "not run" if missing. Changed: PPI form as in S10c-13 (D-06/D-08). → `eval/s10/analysis.py`
- S10c-25: retrieval calls counted as new cache rows. Replaced: real requests are counted (C-01), in one global ledger (G-01). → `eval/s10/frozen_candidates.py`
- S10c-26: audit-A aggregates are computed in the export step, before the seal. → `eval/s10/dev_export.py`
- S10c-27: live safety checks run in code (stub, provider, run folder, pilot cap and T, dev split, lock match). → `eval/s10/live.py`

**Part B**
- Part B: per-judge audit gaps are binary, in both directions (under and over); an invalid audit judge label stops the export with exit 2. → `eval/s10/dev_export.py`

**C rows (live driver)**
- C-01: count real retrieval requests, not cache rows. → `eval/s10/frozen_candidates.py`
- C-02: the provider module stays unchanged; each phase gets an empty sidecar folder; the record count must equal the runs, each one unique. → `eval/s10/live.py`
- C-03: the pilot is never scored; a summary is written only for a complete main run. → `eval/s10/live.py`
- C-04: a bound on the crash under-count was documented. Superseded by FX-01a, so the count can no longer be too low. → addendum A4

**D rows (analysis)**
- D-01: lift signs: under-call reduction = rules minus hybrid; over-call increase = hybrid minus rules; raw rates printed too. → `eval/s10/analysis.py`
- D-02: LOJO uses the frozen rule (the higher label); Hariom's audit label is never used there. → `eval/s10/analysis.py`
- D-03: the conservative-max version uses the judges plus Hariom, never the second rater. → `eval/s10/analysis.py`
- D-04: tipping E counts use the 8 strata profiles, with the E1 rate at full precision. → `eval/s10/analysis.py`
- D-05: print how many strata split cells are gold-mandatory; if above 0, report the primary both ways and flag it. → `eval/s10/analysis.py`
- D-06: PPI is classic PPI with lambda 1. → `eval/s10/analysis.py`
- D-07: the effort rule and sign-flip test use exact fractions; ties count as high ≤ medium; if all differences are zero, the p-value is 1. → `eval/s10/analysis.py`
- D-08: PPI is a ratio inside a profile bootstrap, never ppboot. → `eval/s10/analysis.py`

**E rows (lock design)**
- E-01: the lock covers a fixed source list (56 files at design; 58 now, see §1). → `eval/s10/s10c_lock.py`
- E-02: conditional imports stay inside the import closure. → `eval/s10/s10c_lock.py`
- E-03: the lock list is checked for overlap against every earlier code manifest, the engine hashes and the prompt sha. → `eval/s10/s10c_lock.py`
- E-04: the environment (Python version, 5 package versions, an environment sha) is recorded as provenance, not enforced. → `eval/s10/s10c_lock.py`
- E-05: a listing sha plus a reviewed listing file, both write-once; a mismatch prints the changed file names. → `eval/s10/s10c_lock.py`

**A rows (process)**
- A-01: lock v4 is taken after push and CI. → run order (§9)
- A-02: a review of the lock module (done; it led to 2 diagnostic fixes). → `eval/s10/s10c_lock.py`
- A-03: an independent audit before the lock (done; see §4a). → this report
- A-04: addendum A4 is written before the dev export. → addendum A4
- A-05: at Final, compare the environment sha; if it differs, report it and decide then. → Final run sheet

**F rows (commit 5e846da)**
- F-01: a tool to freeze numbered addenda from A4 on. → `eval/s10/addendum_freeze_n.py`
- F-02: the ruleset is checked in the export and in the candidate build. → `eval/s10/dev_export.py`
- F-03: the candidate build and live check the profiles against the profiles manifest; the candidate manifest records the profiles sha. → `eval/s10/frozen_candidates.py`
- F-04: main rebuilds the tags and compares them. → `eval/s10/live.py`
- F-05: the candidate build records provenance shas. → `eval/s10/frozen_candidates.py`
- F-06: a merge sha check (now enforced by G-09). → `eval/s10/dev_export.py`
- F-07: the first candidate build uses a new empty cache folder; a rebuild reuses it. → run sheet
- F-08: main's gold sha must equal the one in the export manifest. → `eval/s10/live.py`

**G rows (first audit fixes, commit 9488761; outcomes in §4a)**
- G-01: one global append-only call ledger; the cap applies to the global total. → `eval/s10/frozen_candidates.py`
- G-02: the candidate build checks the environment and the lock first, and records the lock sha; live insists on the same sha. → `eval/s10/frozen_candidates.py`
- G-03: the pilot refuses unless the cap is 64000 tokens and the timeout 1200 s. → `eval/s10/live.py`
- G-04: T comes from the per-attempt latency at the pilot cap. → `eval/s10/live.py`
- G-05: a strata profile undefined on both arms counts as a tie with difference 0. → `eval/s10/analysis.py`
- G-06: all candidate command paths are guarded before any read. → `eval/s10/frozen_candidates.py`
- G-07: no candidate sub-cap; headroom checks before the pilot and main instead. → `eval/s10/live.py`
- G-08: if counting fails, charge the reserved maximum, then refuse. → `eval/s10/frozen_candidates.py`
- G-09: the export checks the merge sha against the A2 value. → `eval/s10/dev_export.py`
- G-10: live and the candidate build refuse unless tracing is off. → `eval/s10/live.py`
- G-11: the stop marker goes in both the output and the run folder; preflight refuses a stopped run folder. → `eval/s10/live.py`
- G-12: the tipping block reports U and P per profile. → `eval/s10/analysis.py`

**FX rows (second-review fixes, commits 5c92022 and 85627cc)**
- FX-01a: reserve calls before each paid unit and settle them after (write-ahead). → `eval/s10/live.py`
- FX-01b: flush and fsync the ledger before each paid unit. → `eval/s10/live.py`
- FX-01c: the first Ctrl+C stops gracefully; a second one stops at once. → `eval/s10/live.py`
- FX-01d: only one paid process at a time, through a sidecar lock. → `eval/s10/paid_lock.py`
- FX-01e: reserve sizes (6 per classify run, 11 per candidate query) are proven from the code, with worst-case tests. → `tests/test_s10c1_live.py`
- FX-01f: reconcile against Azure's own request count after each phase, by hand. → run sheet
- FX-02a: the candidate build re-checks the lock and inputs before each query and before publishing. → `eval/s10/frozen_candidates.py`
- FX-02b: `LANGFUSE_ENABLED` must be set to false explicitly; safety variables are re-checked after imports and before each unit. → `eval/s10/live.py`
- FX-02c: no stop marker for candidate builds; addendum A4 explains this instead. → addendum A4
- FX-03: analysis acceptance checks (export-manifest sha, global ledger fields, cap, printed range). → `eval/s10/analysis.py`
- FX-04: addendum A4 wording, version 1.2 (no code). → addendum A4
- FX-05: roles and order: fixes behind a plan gate, tests on Linux and Windows, then a separate read-only re-check. → process (no file)
- FX-06: the minimum list of tests for these fixes. → `tests/test_s10c1_part_a.py`, `tests/test_s10c1_live.py`
- CT-01: a test that LOCKED equals the static import closure of the run path (58 files). → `tests/test_s10c1_lock.py`

**G3 rows (third-review fixes, commit 70aa094)**
- G3-01: an error run that still returns a record settles at its logged count (code kept, A4 reworded). → `eval/s10/live.py`
- G3-02: the adapter logs a failed request before it reads the headers. → `llm/azure_openai.py`
- G3-03: a test with the real retriever proves at most 11 requests per candidate query. → `tests/test_s10c1_g3.py`
- G3-04: small fixes, one test each: one reserve constant; fixed refusal reasons `input_unreadable` / `input_invalid`; the stop notice written with `os.write`; analysis checks main's ledger delta. → `eval/s10/live.py`
- G3-05: finding F7 becomes a run-sheet rule (no analysis during a paid phase); F9 needs no change (directory fsync matters on POSIX only). → run sheet
- G3-06: dev_export needs no new review. → `eval/s10/dev_export.py`
- G3-07: roles: fixes behind a plan gate, tests run by Hariom, and the final diff read line by line by a second reader. → process (no file)

## §4 Independent reviews

### §4a First audit (read-only, at 5e846da, 20 Nov)
Severity counts: BLOCKER 2 · MAJOR 1 · MINOR 10 · NOTE 6 = 19. All fixes are in 9488761.

| # | severity | finding (one line) | outcome |
|---|---|---|---|
| 1 | BLOCKER | The cap total missed calls in stopped or failed attempts in other folders, so real spend could pass 150 | Fixed (G-01): one global append-only ledger `data/runs/s10c_global_call_ledger.jsonl`; cap on the global total |
| 2 | BLOCKER | The candidate build made paid calls without checking the lock, stub or provider, and kept no lock sha | Fixed (G-02): lock and environment checks first; lock sha recorded; live checks it |
| 3 | MAJOR | The pilot ran at whatever cap and timeout the environment gave | Fixed (G-03): pilot needs cap 64000 and timeout 1200 s; A4 row 4 |
| 4 | MINOR | T used the whole call's latency, not the latency at the 64k cap | Fixed (G-04): T from per-attempt latency at the pilot cap; A4 row 4 |
| 5 | MINOR | Retries also cover 408/409 and a retry header, wider than the registered wording | Not fixed: existing adapter policy; at most 3 tries; timeouts never retried; A4 row 7 |
| 6 | MINOR | Two-way split scoring not applied to the robustness band or the paired test | Not fixed: same result by construction (no split cell is gold-mandatory); A4 row 6 |
| 7 | MINOR | No per-profile tipping table | Fixed (G-12) |
| 8 | MINOR | A strata profile with no gold-mandatory cell counted toward neither side | Fixed (G-05): counts as a tie; A4 row 5 |
| 9 | MINOR | Candidate command paths were not guarded; profiles read before refusal | Fixed (G-06) |
| 10 | MINOR | When classify raises, the first attempt's tokens are lost | Not fixed: the runner is protected; requests still counted; A4 row 8 |
| 11 | MINOR | The "retrieval ≤ 20" budget was not enforced | Fixed differently (G-07): headroom checks instead of a sub-cap |
| 12 | MINOR | If counting raised, sent requests went uncharged | Fixed (G-08): charge the reserved maximum, then refuse |
| 13 | MINOR | The merge file was not checked against a frozen sha | Fixed (G-09): checked against the A2 value; A4 row 11 |
| 14 | NOTE | LOCKED had 57 files where the design said 56 | Noted: 56 + `addendum_freeze_n.py`; closure equals LOCKED |
| 15 | NOTE | Live did not turn off tracing | Fixed (G-10) |
| 16 | NOTE | The stop marker was written only to the output folder | Fixed (G-11) |
| 17 | NOTE | At cap 128,000 no re-run happens | Not fixed: consistent with "≤ 128,000"; A4 row 9 (wording) |
| 18 | NOTE | The PPI library call shape in version 0.2.3 is not verified | Not fixed: optional value test if the library is installed (run-sheet decision) |
| 19 | NOTE | `ner/schema.json` is read on import and not frozen | Not fixed: not on the S10 classify path; noted only |

Note: the fixes for this audit were made in the same session that did the audit. That is why a second, separate review followed.

### §4b Second review (PF-13; 21 Nov; independent, read-only, of 9488761)
- 10 findings. The reviewer rated them 5 BLOCKER, 4 MAJOR, 1 MINOR. The maintainer re-rated them 3 MAJOR, 5 MINOR, and 2 where the code was correct and only the A4 wording needed work.
- Main issues:
  - counting after the call could miss requests on Ctrl+C or a crash;
  - a candidate build charged its calls only at the end;
  - two processes could pass the cap together;
  - a counting error could be lost;
  - analysis did not tie the export manifest or the global count to main;
  - the candidate build did not re-check the lock and environment during the build.
- Outcome: fixes FX-01..FX-06, in commits 5c92022 and 85627cc.
- Two suggestions were rejected with reasons: a zero-filled bootstrap, and stop markers for candidate builds.

### §4c Third review (PF-13; 21 Nov; independent, read-only, of 9488761..85627cc)
- 0 BLOCKER, 0 MAJOR, 3 MINOR, 7 NOTE.
- Design checks: 12 of 12 correct. A4 claims: 19 match, 5 partly match, 0 false. All 90 diff hunks matched the files.
- Outcome: G3 fixes in 70aa094. F7 became a run-sheet rule. F9 needed no change (POSIX only).
- This review and the small fixes after it were not fully independent of each other. So the final diff was also read line by line by a second reader.

## §5 Tests (fresh runs at 70aa094, on fakes)

| run | command | pytest summary (copied) | exit |
|---|---|---|---|
| scoped | `.venv\Scripts\python -B -m pytest -q tests/test_s10c1_live.py tests/test_s10c1_part_a.py tests/test_s10c1_analysis.py tests/test_s10c1_dev_export.py tests/test_s10c1_lock.py tests/test_s10c1_addendum_freeze_n.py tests/test_s10c1_paid_lock.py tests/test_s10c1_g3.py -p no:cacheprovider --basetemp=data/.pytest_tmp/pf` | `747 passed, 2 skipped in 231.36s (0:03:51)` | 0 |
| full | `.venv\Scripts\python tasks.py test` | two small sub-runs: line 2 `20 passed in 5.54s`; line 4 `1 passed in 0.23s`; the main suite: line 46 `2703 passed, 3 skipped in 575.25s (0:09:35)` | 0 (one code for the whole command) |

- No failure appears in either run. The known socket flake (in `test_s10b2h_retest_a3`) did not occur in these runs. It stays open (§8 item 1).
- Per-commit test counts from earlier runs are not reported, because no files of those runs were saved.

## §6 Deviations
All differences from the pre-registration are declared in addendum A4. A4 is written but not yet frozen; it is frozen after lock v4. It lists 4 deviations, 11 clarifications or gap-fills, and 4 unregistered steps. It also states that 4 robustness outputs are fixed by construction, because the three judges agreed on every cell. This report adds no deviation of its own.

## §7 Paid calls
0. All S10c-1 code ran on fakes only. No real call ledger exists yet.

## §8 Where it fails / what broke (PF-14)

| # | item | status |
|---|---|---|
| 1 | A socket test in a locked v3 module fails now and then on Windows: the server answers 403 before reading the body, and the connection resets. This blocks a Windows CI job. It did not occur in the §5 runs. | open: Windows CI |
| 2 | A tiktoken test downloads a vocabulary at run time, so it depends on the network. | open: S10c-2 run sheet (`TIKTOKEN_CACHE_DIR`) |
| 3 | The first per-folder call ledger would have split the cap across folders. | fixed: one global ledger (G-01) |
| 4 | Counting after the call could miss requests on Ctrl+C or a crash. | fixed: write-ahead reserve and settle (FX-01a) |
| 4b | Side effect of item 4: an interrupted unit now over-counts by up to its reserve, which can stop main early. The run that triggers a stop keeps its full reserve. | accepted by design: errs on the safe side |
| 5 | An approved prompt row (S10c-12/22) contradicted the frozen LOJO rule. | fixed: corrected to the frozen text (D-02) |
| 6 | Temp folder names in tests that formed "s8", and a test helper that used the drive root. | fixed in tests |
| 7 | A new data folder was not git-ignored at first. | fixed (5e846da) |
| 8 | Prompts were pasted into the wrong terminal window 3 times. | mitigated: process check; no effect on the repo |
| 9 | The real-retriever test's library guards depend on import order; the network block is the real backstop. | accepted |
| 10 | No directory fsync after a ledger is created. This matters on POSIX only, and the run is on Windows. | accepted |
| 11 | Running analysis during a paid phase can see a half-written ledger line and refuse. It fails safe. | mitigated: run-sheet rule (G3-05) |
| 12 | A torn ledger line stops all paid steps until someone decides what to do. There is no automatic repair. | accepted by design |
| 13 | Most robustness outputs are fixed by the judges' agreement, and there are only 8 profiles (clusters), so the exact tests carry the inference (A4 §6, §7). | accepted: reported as limits |

**Where we got lucky**
- The ledger split (item 3) and the gap from counting after the call (item 4) were both found before any paid call.
- The LOJO contradiction (item 5) was caught by reading the frozen text, not by a test.

## §9 Open items before S10c-2
1. Push → PR → CI → listing → "yes lock" → lock v4 → addendum A4 freeze → merge.
2. The A3 filler follow-up (about 24 Nov).
3. The second seal.
4. The run sheet:
   - reconcile with Azure Monitor after each phase;
   - set `LANGFUSE_ENABLED=false` explicitly;
   - set the Azure settings in the shell;
   - decide whether to install ppi-python 0.2.3;
   - set `TIKTOKEN_CACHE_DIR` (item 2);
   - no analysis during a paid phase (item 11).
5. A Windows CI job (blocked by §8 item 1).

## §10 How to verify (read-only)
- `git --no-pager log --stat 39b1156..70aa094`
- `git --no-pager diff --stat 39b1156 70aa094`
- Scoped tests: `.venv\Scripts\python -B -m pytest -q tests/test_s10c1_live.py tests/test_s10c1_part_a.py tests/test_s10c1_analysis.py tests/test_s10c1_dev_export.py tests/test_s10c1_lock.py tests/test_s10c1_addendum_freeze_n.py tests/test_s10c1_paid_lock.py tests/test_s10c1_g3.py -p no:cacheprovider --basetemp=data/.pytest_tmp/pf`
- Full tests: `.venv\Scripts\python tasks.py test`
- Lock listing: `.venv\Scripts\python -m eval.s10.s10c_lock --show` (writes nothing)

## §11 SELF-AUDIT (this report task)
1. **Asked vs done:** §0–§11 all done. Sources: §1 module docstrings and CLI parsers; §2 the git log and diff-stat inputs; §3 the original prompt rows plus the maintainer's answers; §4a the audit findings input; §4b, §4c, §6–§9 the maintainer's facts; §5 the two test-output inputs; §10 the commands given by the maintainer.
2. **Decisions I made myself:**
   - The "main file" for decisions that span several files (for example G-01 → `frozen_candidates.py`, not `live.py`; G3-04 → `live.py`). I picked it by where the code tags or the main logic sit. Rejected: listing every file, because lines would get too long.
   - Added a row "4b" in §8 for the side effect of item 4, so each row has one status.
   - Added `TIKTOKEN_CACHE_DIR` and the no-analysis rule to the §9 run-sheet list, because §8 assigns them to the run sheet.
3. **Deviations:** no deviations.
4. **How I did it:** read the six report inputs, the first 30 lines of the 7 modules, their `main()` and refusal sections, and greps for decision tags and the closure and 11-request tests. Commands: `Get-Item`/`Get-Content` for sizes and first lines; a `Select-String` loop over `eval\s10\*.py` for decision ids; `Grep` for `add_parser|add_argument|return [1-9]` and `_require(` in the modules.
5. **Checks with real output:** 12 sections (§0–§11). §3 lists 27 S10c rows, 1 Part B row, 4 C, 8 D, 5 E, 5 A, 8 F, 12 G, 14 FX/CT and 7 G3 rows. The §4a table has 19 rows (2/1/10/6). LOCKED = 58 (`s10c_lock.py:39` list; `tests/test_s10c1_lock.py:133`). Gate results: not run by me; Hariom runs them.
6. **Frozen items:** only `logs/S10c1_report.md` was created; no other file changed.
7. **Holdouts and sealed files:** none opened, printed or hashed. The diagnostic lines in the full test output were not copied.
8. **Safety:** no git, no tests, no network, no `.env`, no keys, nothing written outside the repo, no tool attribution. No block happened.
9. **Paid/external calls:** 0.
10. **Not checked / assumed:**
    - The FACTS for §4b, §4c, §6 and §8 cannot be checked against files.
    - The exit-code details in §1 come from reading the code, not from running it.
    - The A4 counts depend on an addendum that is not in the repo yet.
11. **Errors and retries:** at the first plan gate, the input folder did not exist, and one `Get-Content` call failed with "path not found". I stopped and asked; Hariom added the files. No other errors.
12. **Risks for later steps + what I need from Hariom:**
    - Run gates 1–4 and compare §5 with the test files.
    - If A4's final counts change at the freeze, §6 needs a versioned update.
