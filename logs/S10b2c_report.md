# S10b2c report

## PLAIN SUMMARY

Audit A labels are frozen; this report uses the saved manifest.
Manifest: eval/s10/manifests/auditA_labels_hariom_150.json.
sha256_lf: f5ba78e5c925d81bcc9de5e32e5bc8ffa58b3a5a44ae18400bab8d99ad5f0ff2.
Last label UTC: 2026-11-16T00:47:55+00:00; earliest test-retest UTC: 2026-11-23T00:47:55+00:00.
Frozen files match; Hariom reports that the fresh final tests passed.

## SELF-AUDIT

### 1. Asked vs done

- Part A: done. Script and fake tests were committed by Hariom as b2ce15e; source: git --no-pager log --oneline -3.
- Part B: done by Hariom. The saved manifest confirms the supplied SHA, data_rows 150, and last-label timestamp. Hariom reports that the labels-file SHA was unchanged after freezing and status showed only the new manifest.
- Part C step 1, report: written here, with the manifest identity, calculated retest date, postmark, limitations, and this self-audit.
- Part C step 2, gate: locked/frozen checks passed locally. Hariom reports that the fresh final full suite passed; the earlier and fresh results are attributed below. Labels-file SHA was not rechecked because opening the labels file is prohibited for this Part.

Postmark:

- Part A commit: b2ce15e, S10b2c Part A: label freeze script + tests.
- Part B commit: d702057, S10b2c Part B: Audit A labels frozen (sha manifest).
- Source for both commit identities: local git --no-pager log --oneline -3.
- Hariom reports that d702057 was pushed to origin/s10b2c/label-freeze and GitHub Actions run "Tests #117" on that commit passed, supported by Hariom's screenshot. The remote branch and Actions run were not independently inspected here.

### 2. Decisions I made myself

- Recorded the CI postmark and earlier tests as Hariom-reported evidence; rejected presenting them as independently checked online results.
- Recorded the fresh final run as Hariom-reported evidence; rejected treating the earlier test run as the fresh Part C run or claiming a local rerun.
- Used the manifest's offset-aware timestamp plus seven days in Python for PR-05; rejected manual date arithmetic.
- Put the requested "What broke" section under errors and retries to keep the plain summary followed by the complete self-audit.

### 3. Deviations

No deviations from the approved Part C plan and additions. Hariom supplied the fresh final full-suite result, now recorded below. Unchanged labels-file SHA is attributed to Hariom's Part B terminal result, without reopening or hashing the labels file.

### 4. How I implemented it

- Created logs/S10b2c_report.md only for Part C.
- Updated this report with Hariom's fresh final test result at his request; no tests were rerun here.
- No code or existing manifest changed.
- Earlier committed implementation: eval/s10/label_freeze.py provides parse_sha, checked_audit, checked_rows, codebook_sha, and main; tests/test_s10b2c_label_freeze.py uses synthetic files in tmp_path.

Exact Part C shell commands, including the final review commands:

```powershell
git branch --show-current
git status --short
Test-Path -LiteralPath logs/S10b2c_report.md
Get-Content -Raw eval/s10/manifests/auditA_labels_hariom_150.json
git --no-pager log --oneline -3
```

Manifest confirmation and retest calculation:

```powershell
@'
import json
from datetime import datetime, timedelta
from pathlib import Path

p = Path("eval/s10/manifests/auditA_labels_hariom_150.json")
m = json.loads(p.read_text(encoding="utf-8"))
assert m["sha256_lf"] == "f5ba78e5c925d81bcc9de5e32e5bc8ffa58b3a5a44ae18400bab8d99ad5f0ff2"
assert m["data_rows"] == 150
assert m["last_label_utc"] == "2026-11-16T00:47:55+00:00"
last = datetime.fromisoformat(m["last_label_utc"])
print("earliest_test_retest_utc=" + (last + timedelta(days=7)).isoformat())
'@ | .\.venv\Scripts\python.exe -
```

Locked/frozen verification:

```powershell
@'
import hashlib
import json
from pathlib import Path

root = Path.cwd()
for name, filename, expected in (
    ("locked", "s10b2b_code_lock.json", 9),
    ("frozen", "codebook_v1.json", 6),
):
    files = json.loads(
        (root / "eval/s10/manifests" / filename).read_text(encoding="utf-8")
    )["files"]
    matches = sum(
        hashlib.sha256(
            (root / rel).read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest() == info["sha256_lf"]
        for rel, info in files.items()
    )
    print(f"{name} {matches}/{len(files)}")
    if len(files) != expected or matches != expected:
        raise SystemExit(1)
'@ | .\.venv\Scripts\python.exe -
```

Final report review:

```powershell
git diff --check
git status --short
Get-Content -Raw logs/S10b2c_report.md
```

### 5. Checks with real output

Earlier full suite, source: Hariom's laptop pytest result reported in chat:

| Before passed | New passed | Total passed | Failed | Skipped |
| --- | --- | --- | --- | --- |
| 1579 | 31 | 1610 | 0 | 1 |

- A separate successful focused-run output was not supplied.
- Fresh final full suite: 1610 passed, 1 skipped in 207.61s (0:03:27); 0 failed. Source: Hariom's laptop terminal result reported in chat, on branch s10b2c/label-freeze. This run was performed by Hariom and was not independently repeated here.
- Local plain-Python integrity output: locked 9/9; frozen 6/6.
- Local manifest assertions: SHA matches f5ba78e5c925d81bcc9de5e32e5bc8ffa58b3a5a44ae18400bab8d99ad5f0ff2; data_rows equals 150; last_label_utc equals 2026-11-16T00:47:55+00:00.
- Source for those values: eval/s10/manifests/auditA_labels_hariom_150.json, read without opening its referenced labels file.
- Python calculation output: earliest_test_retest_utc=2026-11-23T00:47:55+00:00.
- Labels-file SHA unchanged after the freeze: reported by Hariom; not independently rechecked in Part C.

Command for the fresh final run performed by Hariom; not executed here:

```powershell
.\.venv\Scripts\python.exe -m pytest tests mcp observability -q --basetemp data/.pytest_tmp/s10b2c-full -p no:cacheprovider
```

### 6. Frozen items

No locked file, frozen file, or existing manifest was edited. Every entry in the following manifests was rehashed with stdlib Python using CRLF-to-LF normalization:

- eval/s10/manifests/s10b2b_code_lock.json: locked 9/9.
- eval/s10/manifests/codebook_v1.json: frozen 6/6.

Checked locked files: eval/s10/judge_output.py, eval/s10/judge_runner.py, eval/s10/metrics.py, eval/s10/agreement.py, eval/s10/merge.py, eval/s10/gate.py, eval/s10/draws.py, eval/s10/sheets.py, eval/s10/diagnostics.py.

Checked frozen files: eval/s10/codebook/CODEBOOK_v1.0.md, eval/s10/codebook/JUDGE_INSTRUCTIONS_v1.0.md, eval/s10/codebook/judge_output.schema.json, eval/s10/judge_kit.py, eval/s10/judge_config.yaml, specs/S10_PREREG_ADDENDUM_A1.md.

The step gate passes using the local locked/frozen integrity checks and Hariom's reported fresh final full-suite result. Unchanged labels-file SHA remains attributed to Hariom's Part B terminal result.

### 7. Holdouts and sealed files

Protected files were not opened, printed, copied, hashed, or scored. Real labels were not opened during Part C. No label values, protected identifiers, free-text label content, or per-label counts are included in this report.

### 8. Safety

No commit or push was executed here. No keys were printed; .env was not opened. No file outside the repo was written. No attribution was added. No Git config or global ignore file was changed. Git commands in Part C were read-only. Permission blocks were reported without workarounds; Hariom performed the required Git writes, pytest runs, and real freeze.

### 9. Paid/external calls

Count: 0. Cost: 0. No external timestamping, network request, or paid call was made. CI information is attributed to Hariom.

### 10. Not checked / assumed

- The push and "Tests #117" pass were reported by Hariom; the screenshot and remote Actions state were not independently inspected here.
- The fresh final pytest result was supplied by Hariom and was not independently repeated here.
- The labels-file SHA, byte count, and line count were not independently checked in Part C.
- The supplied earlier test result is attributed evidence, not a locally repeated test.
- No additional labeller freeze is claimed.

### 11. Errors and retries

#### What broke

- The prompt requested a 14-key manifest test while listing 13 keys. Hariom corrected the requirement to the exact listed 13-key set.
- The sandbox blocked access to the global Git ignore file outside the repo. The block was reported; that file and Git config were never read or changed. Hariom explicitly allowed continuing past that specific warning after the repo ignore rule was confirmed.
- The sandbox blocked writing .git/index.lock during branch creation. No retry or workaround was attempted; Hariom performed the successful Git writes.
- The sandbox blocked pytest from creating its approved temporary directory under data/. The initial focused attempt reported 31 fixture-setup errors. No alternate path or retry was used; Hariom ran pytest and the real freeze.

Other corrections:

- Combined source-read output was truncated; the incomplete source was reread.
- The initial offset-free timestamp assumption was removed at Hariom's direction. Missing offsets now fail the fixed timestamp check, with a synthetic rejection test.
- Part C manifest assertions, date calculation, and integrity checks returned exit code 0. No Part C error or retry occurred.

### 12. Risks for later steps + what I need from Hariom

- Hariom's fresh final suite passed and its result is recorded above; no further test run is requested here.
- Review and commit only logs/S10b2c_report.md.
- PR-05 earliest test-retest time is 2026-11-23T00:47:55+00:00, derived from the manifest's last-label timestamp.
- The CI postmark is reported as existing and passed; no judge call was made here.

