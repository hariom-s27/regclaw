# S10b2g report

## PLAIN SUMMARY

Added the offline friend browser tool with anonymous sheet content, resumable answers and atomic CSV saves.
Reused existing rendering and validation helpers; added request security, Windows save retries, keyboard controls and validated codebook access.
Hariom reviewed and committed Part A as `f7a7985`; Hariom reports locked files unchanged.
Hariom reports friend-tool tests: 165 passed; tasks.py main suite: 1904 passed, 1 skipped, 0 failed (source: owner's completion message in this session).
Part B adds this report and launch instructions; no tests, git commands or real S10 data access were performed for Part B.

## SELF-AUDIT

### 1. Asked vs done

- Part A: built `eval/s10/friend_tool.py` and `tests/test_s10b2g_friend_tool.py`, including the approved security, retry, keyboard, empty-selection and codebook additions. Hariom reviewed and committed the code.
- Part B: created only `logs/S10b2g_report.md`, recording the owner-supplied validation results and exact operating commands below.
- No work or file changes were made for the withdrawn, unrelated project instruction.

### 2. Decisions I made myself

- Used a serial HTTP server to process navigation and saves without concurrent in-process writes.
- Tested the actual HTTP handler through in-memory HTTP requests; server binding and browser launch use test doubles.
- Interpreted the approved retry budget as five total attempts, with the listed backoff after permission failures (source: `_atomic_write` in `eval/s10/friend_tool.py`).

### 3. Deviations

- None. Part B does not edit source, tests, frozen files, manifests or locks.

### 4. How I implemented it (files, functions, exact commands)

- `eval/s10/friend_tool.py`: `FriendApp` validates startup inputs and resumes answers; `_sheet_sections` extracts anonymous rendered content; `_load_answers` reuses `checked_friend_rows` for partial responses; `_clean_note` sanitises notes; `_csv_bytes` and `_atomic_write` persist ordered CSV responses.
- `make_handler` enforces Host, session-token and Origin checks, serves the inline page and validated plain-text codebook, and exposes only permitted cell state. `serve` binds to loopback; `main` implements the approved CLI.
- `tests/test_s10b2g_friend_tool.py`: synthetic roots under `tmp_path`, forbidden-data guards, in-memory handler requests, independent expected CSV bytes, and simulated permission failures.
- `logs/S10b2g_report.md`: the only file created during Part B. Source inspection was read-only.
- Exact approved validation commands, provided for Hariom and not run by this agent:

```powershell
.venv\Scripts\python -m pytest tests/test_s10b2g_friend_tool.py -q --basetemp data/.pytest_tmp/s10b2g_a
.venv\Scripts\python tasks.py test
```

Run from `D:\sem_iitk\sem9\meril`. Start the cold-read sheet:

```powershell
.venv\Scripts\python -m eval.s10.friend_tool serve --sheet coldread --port 8767
```

Start the main sheet:

```powershell
.venv\Scripts\python -m eval.s10.friend_tool serve --sheet main --port 8767
```

The default browser opens automatically. Stop the server with **Ctrl+C in its PowerShell terminal** before starting the other sheet on the same port. The port and CLI options above come from `serve` and `main` in `eval/s10/friend_tool.py`.

### 5. Checks with real output

- Owner-supplied result: friend-tool tests **165 passed**.
- Owner-supplied result: tasks.py main suite **1904 passed, 1 skipped, 0 failed**.
- Source for these results: Hariom's Part A completion message, which also records review approval and commit `f7a7985`. These are owner-reported results, not independently rerun results or locally inspected test logs.
- Part B pre-write `Test-Path logs/S10b2g_report.md` returned `False`; the report did not overwrite an existing file.
- A read-only standard-library Python structure/encoding check returned:

```text
PLAIN SUMMARY: 5 lines
SELF-AUDIT: 12 ordered headings
Launch commands and Ctrl+C: OK
UTF-8/LF/no-BOM: OK
```

- No git or pytest commands were executed by this agent.

### 6. Frozen items untouched

- No locked source, manifest, manifest-listed existing file or lock was edited by this agent.
- Hariom explicitly reports locked files unchanged for the reviewed Part A commit. No independent git or hash verification was performed.
- No lock membership was added and the tool writes no manifest.

### 7. Holdouts and sealed files not opened

- No real file under `data/gold/s10` was opened, read, printed or hashed by this agent.
- No holdout profile, draft profile, J-id map or its folder, or S8 sealed file was opened, copied, printed or hashed.
- Tests use synthetic inputs; imports of existing helpers were permitted.

### 8. Safety (no commit/push, no keys, .env not opened, nothing outside the repo written, no AI attribution, blocks reported)

- No commit, push or other git command was executed by this agent. Part A was committed by Hariom.
- No keys were printed and `.env` was not opened.
- Agent-created files were confined to the repository. No tests were run by this agent; fake test roots use `tmp_path` without assuming that it lies inside the repository.
- No attribution was added to source, comments, reports or the proposed commit message.
- Permission blocks: none.

### 9. Paid/external calls

- None. No web research, external service or paid API call was made for this work.

### 10. Not checked / assumed

- Owner-reported test results, commit identity and locked-file status were accepted without running git or pytest.
- Actual browser interaction, responsive layout and operation against real S10 inputs were not exercised by this agent.
- Existing target CSV templates, matching rendered sheets and the frozen codebook are runtime prerequisites; the tool refuses inconsistent startup inputs.

### 11. Errors and retries

- No Part B command or write errors occurred.
- During planning, combined source output was truncated; missing portions were retrieved. No protected data was accessed.
- Permission-error retry behavior is covered by the synthetic test suite; no real CSV lock was exercised by this agent.

### 12. Risks + what I need from Hariom

- Run one server instance at a time. Concurrent instances or external CSV edits during a session are unsupported.
- If a save exhausts permission retries, the page reports `not saved`; the current cell, old CSV and in-memory answers remain unchanged. Resolve the file lock before retrying.
- Main-sheet startup refuses when the friend-label freeze manifest exists; this report does not authorise changing a frozen artifact.
- Hariom can launch either sheet with the commands above and stop it with Ctrl+C. No further implementation decision is pending; the report is ready for review and commit.
