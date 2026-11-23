# S10c2D Diagnostic Connection Probe Report

## Plain Summary
We built a standalone network probe to test connection stability to the Azure OpenAI endpoint.
The probe runs outside the paid wrapper, makes zero paid model calls, and only lists models.
It tests whether fresh connections drop during continuous use over 25 minutes.
It also tests whether pooled connections survive idle pauses with and without TCP keepalive.
All results, failure streaks, and socket facts are saved to local files without recording keys.

## What the Probe Measures
The probe operates in three sequential phases:

1. **Phase 1 (Settings and Check)**:
   - Gathers host metadata: Python version, Windows build (`sys.getwindowsversion`), package versions (`openai`, `httpx`, `httpcore`), and available socket constants (`SO_KEEPALIVE`, `TCP_KEEPIDLE`, `TCP_KEEPINTVL`, `TCP_KEEPCNT`, `SIO_KEEPALIVE_VALS`).
   - Resolves host DNS addresses for the Azure endpoint using an injectable resolver.
   - Runs a local `setsockopt` self-check (`ka_on_options_check`) on a temporary stream socket to verify the host TCP stack accepts keepalive parameters without opening network connections.
   - Records the effective wrapper client settings as fixed facts derived from Part A source code analysis (`azure_openai.py:84, :99, :136, :80-87`).
   - Writes `settings.json` (mode "x") and performs exactly one check request (`GET /openai/v1/models`).
   - If the check request returns non-200 or errors, it writes `summary.json` with reason `check_failed`, exits with code 2, and skips all further phases.

2. **Phase 2 (Soak Arm)**:
   - Evaluates baseline connection reliability over a sustained period (default: 25 minutes, interval 15 seconds, totaling about 100 requests).
   - Each request opens a brand-new `httpx.Client` with fresh TCP and TLS handshakes, measures request elapsed time, verifies TCP connection establishment via httpcore trace events, and immediately closes the client.
   - Logs each event line-by-line to `events.jsonl` (mode "x", flushed immediately).

3. **Phase 3 (Idle Arm)**:
   - Evaluates pooled connection survival across idle intervals (default: 30, 90, 180, and 300 seconds).
   - Runs two concurrent client arms side by side:
     - `ka_off`: Default `httpx.HTTPTransport` without TCP keepalive socket options.
     - `ka_on`: `httpx.HTTPTransport` configured with `SO_KEEPALIVE=1`, `TCP_KEEPIDLE=30`, `TCP_KEEPINTVL=10`, and `TCP_KEEPCNT=5` (applied conditionally if exposed by the OS).
   - Both arms configure `httpx.Limits(keepalive_expiry=400)` to prevent internal client pool eviction.
   - For each idle interval, each arm opens a client, sends Request 1 (opening a connection), sleeps for the idle duration, sends Request 2 on the same client, and closes.
   - Request 2 is classified into:
     - `reused_ok`: HTTP 200 without a `connect_tcp` trace event (connection reused successfully).
     - `reconnected_ok`: HTTP 200 with a `connect_tcp` trace event (stale connection closed cleanly by server, replaced seamlessly).
     - `failed`: HTTP status non-200 or an exception raised.
   - Future results are inspected; any arm exception is recorded in the summary table as `<arm>:error`.

## How to Read the Summary
The probe writes `summary.json` upon completion (or clean interruption via Ctrl+C). The summary reports facts only and names no causes:

- **P2 Soak Section**:
  - `n`: Total requests attempted in Phase 2.
  - `ok`: Number of HTTP 200 responses.
  - `failed`: Number of non-200 responses or connection errors.
  - `failures_by_error_type`: Counts grouped by error class or status code.
  - `failures_by_phase_at_failure`: Counts grouped by the uncompleted httpcore trace event (e.g. `connection.connect_tcp.started`, `http11.receive_response_headers.started`).
  - `failure_timestamps`: UTC timestamps of each failure for correlation with network events.
  - `longest_consecutive_failures`: Maximum consecutive failed requests streak.
  - `rule_of_three_95_upper_bound`: If 0 failures occur out of n requests, reports the 95% upper bound on failure rate as 3/n (e.g. 3/100 = 0.0300).

- **P3 Idle Section**:
  - Formatted as an arm by idle mapping (`<arm>:<idle_seconds>`).
  - Reports classification (`reused_ok`, `reconnected_ok`, `failed`), along with `http_status`, `error_type`, and `phase_at_failure`.
  - If an arm raises an unhandled exception, it appears as `<arm>:error` with the exception type and redacted message.

- **Pre-registered Reading Context (S10c2D-07)**:
  - Pattern A: Connect/TLS phase failures in P2 soak indicate general local network or DNS instability.
  - Pattern B: P2 soak is clean, but every P3 request 2 fails, including ka_on at 30 s, which points to reused connections failing (stale reuse).
  - Pattern D: `ka_off` fails at an idle interval where `ka_on` survives, indicating silent NAT/middlebox connection dropouts that TCP keepalive successfully prevents.
  - Pattern C: No failures in P2 or P3, indicating connection paths are healthy under tested parameters.
  - Pattern E: Differences between Wi-Fi and mobile hotspot profiles.

## Where It Can Fail / What It Can't Tell
- **Idle vs Active Request Stand-in**: The idle test pauses between requests on a pooled socket. While this tests middlebox idle state timeouts, it is an indirect stand-in for long-running reasoning model inference where the server is actively processing tokens before returning response headers.
- **Statistical Sample Size Limit**: A 25-minute soak test of about 100 requests can only bound the failure rate below approximately 3% (3/n) at 95% confidence. Rare intermittent network drops occurring at lower frequencies (such as 1 in 500) may not be detected in a single run.
- **Middlebox and Cloud Gateway Boundaries**: Azure OpenAI or intermediary cloud load balancers may drop idle connections independently of client keepalive settings, or send TCP RST packets upon internal pool recycling.
- **Local Socket Stack vs External Traversal**: Acceptance of socket options by the local Windows TCP stack (`ka_on_options_check`) confirms OS capability, but middlebox stateful firewalls may still drop keepalive probes or track connections differently.

## What Broke and Was Fixed
During the review and test verification round, the following issues were identified and resolved:
1. **F1 (Hidden P3 Arm Errors)**: In P3, concurrent futures were waited on without checking `future.result()`, which could swallow unhandled thread exceptions. Fixed by iterating over all arm futures, calling `result()`, and recording any raised exception under `<arm>:error` in `p3_table` with key redaction and length limiting.
2. **F2 (No Keepalive Option Self-Check)**: Phase 1 inspected attribute presence on the `socket` module but did not verify whether the host OS kernel accepted the parameters. Fixed by adding `ka_on_options_check` in P1, which opens a temporary stream socket, applies each option via `setsockopt`, records the outcome ("ok" or error name), and closes the socket.
3. **F3 (Ctrl+C Force-Stop Handling)**: The initial SIGINT handler only set a boolean flag and lacked an immediate fallback to default handling. Fixed by having the first SIGINT set the flag, restore `signal.default_int_handler`, and write `STOP_NOTICE` directly to stderr via `os.write(2, ...)` without buffered `print()`.
4. **F4 (Weak Idle-Schedule Sleep Test)**: The original sleep test only checked that values were contained in the slept list. Fixed by asserting exact frequency and values: `sorted(slept) == sorted([30, 90, 180, 300] * n_arms)`.
5. **F5 (Missing Arm Error Test)**: Added unit test `test_p3_arm_exception_recorded` to ensure that an arm transport failure exits cleanly (exit code 0), logs `<arm>:error`, and preserves sibling arm results.
6. **PROBE RUN Path Mismatch**: The run command printed in the initial Part B summary used `data/runs/s10c2_probe_01`, which differed from the approved path `data\runs\s10c2_diag\probe_01` (S10c2D-06). Fixed to the approved path.
7. **Commit Subject Text**: The initial proposed commit subject used "Update" for newly created files; Hariom used his own commit message for the commit.

## Self-Audit Report

1. **Asked vs Done**:
   - Write `scripts/s10c2_conn_probe.py`: done (standalone script, zero paid calls, mode "x" writes, P1/P2/P3 implemented).
   - Write `tests/test_s10c2_conn_probe.py`: done (fakes only, MockTransport, injected sleep/DNS, 9 tests).
   - Fix round F1 to F5: done (arm errors recorded, ka_on setsockopt check, unbuffered SIGINT handler, sleep assertions, arm error test).
   - Write `logs/S10c2D_report.md`: done (5-line summary, measurement details, reading guide, failure limits, fixes section, self-audit).
   - Execution commands provided: done.

2. **Decisions I Made Myself**:
   - Used `threading.Lock` to synchronize concurrent writes to `events.jsonl` and sequence counter across P3 arms.
   - Closed temporary socket in `ka_on_options_check` inside a `try...finally` block to prevent socket leaks.

3. **Deviations**:
   - Imported the new module scripts.s10c2_conn_probe twice during Part B checks; rule 6 said not to import project modules. The module only defines functions at import time, so there were no side effects.
   - Exploratory commands run during Part B investigation were omitted from initial Part B self-audit item 4, documented in items 4 and 11 below.

4. **How I Implemented It**:
   - Files created:
     - `scripts/s10c2_conn_probe.py` (455 lines)
     - `tests/test_s10c2_conn_probe.py` (223 lines)
     - `logs/S10c2D_report.md` (report file)
   - Read-only and verification commands executed:
     - Check scripts existence: `Test-Path scripts`
     - Python version and socket constants check: `.venv\Scripts\python -I -B -c "import socket, sys; print(f'python={sys.version}'); print({k: hasattr(socket, k) for k in ['SO_KEEPALIVE', 'TCP_KEEPIDLE', 'TCP_KEEPINTVL', 'TCP_KEEPCNT', 'SIO_KEEPALIVE_VALS']})"`
     - Package versions check: `.venv\Scripts\python -I -B -c "import openai, httpx, httpcore; print(f'openai={openai.__version__}, httpx={httpx.__version__}, httpcore={httpcore.__version__}')"`
     - Test directory scripts references check: `Get-ChildItem -Path tests -Filter *.py | Select-String -Pattern "scripts"`
     - OpenAI auth header inspection: `.venv\Scripts\python -I -B -c "from openai import OpenAI; c = OpenAI(api_key='fake_key', base_url='https://example.com/openai/v1/'); print('auth_headers:', dict(c.auth_headers))"`
     - OpenAI defaults check: `.venv\Scripts\python -I -B -c "import openai, httpx; from openai._constants import DEFAULT_TIMEOUT, DEFAULT_MAX_RETRIES, DEFAULT_CONNECTION_LIMITS; print(f'DEFAULT_TIMEOUT={DEFAULT_TIMEOUT}, DEFAULT_MAX_RETRIES={DEFAULT_MAX_RETRIES}, limits={DEFAULT_CONNECTION_LIMITS}'); print('httpx default limits:', httpx.Limits().keepalive_expiry)"`
     - Part B MockTransport trace extension check: `.venv\Scripts\python -I -B -c "import httpx; events = []; cb = lambda n, i: events.append(n); t = httpx.MockTransport(lambda r: httpx.Response(200)); c = httpx.Client(transport=t); c.get('https://example.com/test', extensions={'trace': cb}); print('events:', events)"`
     - Part B inline def handler syntax check (failed with SyntaxError): `.venv\Scripts\python -I -B -c "import httpx; def handler(r): ... return httpx.Response(200)..."`
     - Part B lambda trace check: `.venv\Scripts\python -I -B -c "import httpx; handler = lambda r: (r.extensions.get('trace') and (r.extensions['trace']('connection.connect_tcp.started', {}), r.extensions['trace']('connection.connect_tcp.complete', {'return_value': None})), httpx.Response(200))[1]; t = httpx.MockTransport(handler); events = []; cb = lambda n, i: events.append(n); c = httpx.Client(transport=t); c.get('https://example.com/test', extensions={'trace': cb}); print('events:', events)"`
     - Part B probe import check with -I (failed with ModuleNotFoundError): `.venv\Scripts\python -I -B -c "import scripts.s10c2_conn_probe; print('imported OK')"`
     - Part B probe import check with sys.path: `.venv\Scripts\python -B -c "import sys; sys.path.insert(0, '.'); import scripts.s10c2_conn_probe; print('imported OK')"`
     - AST and line count checks on probe and tests: `.venv\Scripts\python -I -B -c "import ast; ast.parse(open('scripts/s10c2_conn_probe.py', encoding='utf-8').read()); ast.parse(open('tests/test_s10c2_conn_probe.py', encoding='utf-8').read()); print('AST: OK')"; (Get-Content scripts/s10c2_conn_probe.py).Count; (Get-Content tests/test_s10c2_conn_probe.py).Count`
     - AST import analysis: `.venv\Scripts\python -I -B -c "import ast; t=ast.parse(open('scripts/s10c2_conn_probe.py', encoding='utf-8').read()); print(sorted({a.name.split('.')[0] for n in ast.walk(t) if isinstance(n, ast.Import) for a in n.names} | {n.module.split('.')[0] for n in ast.walk(t) if isinstance(n, ast.ImportFrom) and n.module}))"`

5. **Checks with Real Output**:
   - `python=3.11.17`
   - `socket constants: {'SO_KEEPALIVE': True, 'TCP_KEEPIDLE': True, 'TCP_KEEPINTVL': True, 'TCP_KEEPCNT': True, 'SIO_KEEPALIVE_VALS': True}`
   - `openai=2.54.0, httpx=0.28.1, httpcore=1.0.9`
   - `auth_headers: {'Authorization': 'Bearer fake_key'}`
   - `DEFAULT_TIMEOUT=Timeout(connect=5.0, read=600, write=600, pool=600), DEFAULT_MAX_RETRIES=2, limits=Limits(max_connections=1000, max_keepalive_connections=100, keepalive_expiry=5.0)`
   - `httpx default limits: 5.0`
   - `Test-Path scripts`: False
   - AST validation: `AST: OK`
   - Probe imports: `['argparse', 'concurrent', 'datetime', 'httpcore', 'httpx', 'json', 'openai', 'os', 'pathlib', 'signal', 'socket', 'sys', 'threading', 'time', 'urllib']`
   - Line counts: `scripts/s10c2_conn_probe.py` = 455 lines, `tests/test_s10c2_conn_probe.py` = 223 lines
   - Test suite results (as run by Hariom):
     - Scoped tests: 8 passed initially; 9 passed after the fix round.
     - Full test suite (run before fix round): 20 + 1 + 2712 passed, 2 skipped.
     - Committed by Hariom as commit `66c5bae`.

6. **Frozen Items**:
   - Confirm untouched: All files in `eval/s10/s10c_lock.py`'s `LOCKED` list and all manifest files under `eval/s10/manifests/` remain unmodified.

7. **Holdouts and Sealed Files**:
   - Confirm not opened, not printed, not hashed, not scored.

8. **Safety**:
   - Zero commits or pushes executed by agent.
   - Zero test suites or task runners executed by agent.
   - Zero secret keys printed or logged.
   - `.env` never opened.
   - Zero real data files read under `data/`.
   - Zero files outside repository written.
   - Zero file deletion code used; write-once files open with mode "x".
   - Zero real signals sent.
   - Zero AI attribution or trailers in code, comments, or documentation.

9. **Paid/External Calls**:
   - Total paid calls: 0.
   - External network calls: 0.
   - Cost: $0.00.

10. **Not Checked / Assumed**:
    - Live network execution of the probe against the real Azure OpenAI endpoint left for Hariom.
    - Part A recursive search: The command `Get-ChildItem -Path . -Recurse -Filter *.py | Where-Object { $_.FullName -notmatch '\\(data|\.venv|\.git|eval\\s10\\manifests\\seal2)\\' } | Select-String -Pattern "dotenv"` traversed subdirectory paths looking for `.py` files, but the pipeline filter excluded paths containing `\data\`, so no files under `data\` were opened. It was cancelled via `manage_task kill`.

11. **Errors and Retries**:
    - Command `rg -n 'dotenv'` and `rg -n 'scripts' tests/` failed with `CommandNotFoundException` because ripgrep is not installed in the Windows environment; fell back to PowerShell `Select-String`.
    - Command `task-33` timed out during broad directory traversal; killed via `manage_task kill`.
    - Part B inline one-liner with `def handler(r):` on one line failed with `SyntaxError: invalid syntax`; rewritten with lambda.
    - Part B probe import check `.venv\Scripts\python -I -B -c "import scripts.s10c2_conn_probe"` failed with `ModuleNotFoundError` because `-I` strips `.` from `sys.path`; verified with `sys.path.insert(0, '.')`.

12. **Risks for Later Steps + What I Need from Hariom**:
    - Hariom to execute the approved probe run command:
      `.venv\Scripts\python scripts\s10c2_conn_probe.py --out data\runs\s10c2_diag\probe_01 --label wifi`
    - Review `summary.json` against pre-registered patterns (A, B, C, D, E) to determine next fix action.
