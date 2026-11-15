# S10b2a: judge kit, runner, label-tool practice modes, freeze script

(PLAIN SUMMARY and the full SELF-AUDIT are added in Part G. Each Part's result is appended below at its STOP.)

## Part A: status, reading, tool facts (2026-11-15)

Result
- Start state (source: git): branch `main`, HEAD `c1f14bd86fbea64035f4b444bbfe9022989e2c4a`; `git status --porcelain` showed only `?? eval/s10/codebook/JUDGE_INSTRUCTIONS_v0.1.md` (as expected). Branch `s10b2/kit` created (`git switch -c s10b2/kit`).
- READ FIRST files read in full: CLAUDE.md; eval/s10/README.md, formats.py, judge_file.py, audit_list.py, label_tool.py, prereg_freeze.py, split_guard.py, profile_checks.py, s10b1_manifest.py; tests/test_s10b_{audit_list,build_manifest,grid_check,judge_file,label_tool,practice_format}.py; tests/conftest.py; CODEBOOK_v0.1.md; JUDGE_INSTRUCTIONS_v0.1.md; manifests/s10b1_build.json; logs/S10b1_report.md; standards/data/endpoint_to_part.v1.yaml (read-only). Also read (needed for the Part B plan): standards/profile.py lines 1-82 (DeviceProfile fields), standards/engine.py rule_02 (duration: A ≤ 24 h, B ≤ 720 h, C beyond; intermittent = hours x uses).
- Recorded shas (source: s10b1_build.json): codebook v0.1 `7992a374…80fe`, practice_profiles.yaml `cfd11a74…f9e9`.

Tool facts (read-only; source: command output)
- Claude Code: `C:\Users\hario\.vscode\extensions\anthropic.claude-code-2.1.325-win32-x64\resources\native-binary\claude.exe` (path from Hariom). `--version` → `2.1.325 (Claude Code)` (= expected_version A).
  - `-p, --print`: print response and exit (workspace trust dialog skipped in this mode).
  - `--output-format <format>`: text (default), json (single result), stream-json (only with --print).
  - `--json-schema <schema>`: JSON Schema for structured output validation (only with --print); takes an inline JSON string.
  - `--tools <tools...>`: list from the built-in set; `""` disables all tools, `default` = default set.
  - `--setting-sources <sources>`: comma-separated list of setting sources to load (user, project, local).
  - `--disable-slash-commands`: disable all skills and commands.
  - `--strict-mcp-config`: only use MCP servers from --mcp-config, ignore all other MCP configuration.
  - `--no-session-persistence`: sessions not saved to disk, cannot be resumed (only with --print).
  - `--effort <level>`: low, medium, high, xhigh, max.
  - `--model <model>`: alias (e.g. fable, opus, sonnet) or full name.
  - Also seen: `--restricted` (removes command/code tools and WebFetch unless --tools names them; ignores user, project and local settings files). Not in S10b2-03; reported only.
- Codex: `codex --version` → `codex-cli 0.157.1` (= expected_version B). `codex` on PATH resolves to `C:\nvm4w\nodejs\codex.ps1` / `codex.cmd` (npm shims).
  - `--json`: print events to stdout as JSONL.
  - `--output-schema <FILE>`: path to a JSON Schema file for the model's final response shape.
  - `-o, --output-last-message <FILE>`: file where the agent's last message is written.
  - `--ephemeral`: run without persisting session files to disk.
  - `-s, --sandbox <MODE>`: read-only, workspace-write, danger-full-access.
  - `-m, --model <MODEL>`; `-c, --config <key=value>` (TOML-parsed override of ~/.codex/config.toml).
  - `--skip-git-repo-check`: allow running outside a Git repository.
  - `[PROMPT]`: if absent or `-`, instructions are read from stdin.
  - Also seen: `--ignore-user-config` (do not load $CODEX_HOME/config.toml; auth still uses CODEX_HOME), `--ignore-rules`. Not in S10b2-03; reported only.
- Native Codex binary (S10b2a-04): JS entry `C:\nvm4w\nodejs\node_modules\@openai\codex\bin\codex.js` (package.json version 0.157.1). Native exe pattern: `<npm prefix>\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe` → `--version` `codex-cli 0.157.1`.
  - Warning: a second, stale copy exists in an npm staging leftover `<npm prefix>\node_modules\@openai\.codex-IpqLA8GV\...\bin\codex.exe` → `codex-cli 0.151.0`. The runner's per-call version check would refuse it; Hariom should pass the first path to `--codex-exe`.

SELF-AUDIT (Part A)
1. Asked vs done: git status + branch: done. READ FIRST: done (all 21 files). claude --version/--help: done (path from Hariom). codex --version / exec --help: done. Native codex location: done (2 copies; correct one is 0.157.1). Part B plan: done (in the STOP message).
2. Decisions I made myself: also read standards/profile.py (fields) and engine.rule_02 (read-only) to plan probe durations; filtered claude --help with Select-String instead of printing it in full.
3. Deviations: no deviations.
4. How I implemented it: no code. Commands: `git branch --show-current; git rev-parse HEAD; git status --porcelain`; `git switch -c s10b2/kit`; `codex --version`; `codex exec --help`; `Get-Command codex -All`; `Get-ChildItem -Recurse -Filter codex.exe` under `C:\nvm4w\nodejs\node_modules\@openai`; `<codex.exe> --version` (both copies); `<claude.exe> --version`; `<claude.exe> --help | Select-String …`; `git check-ignore -v` on planned paths.
5. Checks with real output: versions above; `git check-ignore`: data/gold/ (.gitignore:12) and data/.pytest_tmp/ (.gitignore:16) are ignored; `data/.s10b2_tmp/` is NOT ignored (so not used).
6. Frozen items: nothing edited (engine.py, guard.py, overclaim.py, standards/data YAMLs, classifier.py, retrieval_eval/*, corpus, manifests, practice_profiles.yaml, CODEBOOK_v0.1.md, JUDGE_INSTRUCTIONS_v0.1.md).
7. Holdouts and sealed files: none opened; judge_file_v1.yaml not opened in Part A.
8. Safety: no commit, no push; .env not opened; no keys; nothing written outside the repo; no model call (only --version/--help); no AI attribution; no blocks.
9. Paid/external calls: 0.
10. Not checked / assumed: claude --help was filtered; only matching lines were reviewed. Whether `--json-schema` accepts every keyword of the planned schema is checked only by the smoke run (Part D).
11. Errors and retries: none.
12. Risks for later steps + what I need from Hariom: answer the Part B questions (PR-9 needs a format decision); use the 0.157.1 codex.exe path; consider removing the stale npm staging folder (a human step, outside the repo).

## Part B: rule probes (2026-11-15), STOP 1 (awaiting "probes OK")

Plan approved with answers: Q1 = D (PR-9 = one contacting component + one non_contact component; formats.py unchanged); Q2 yes (device_name only); Q3 every string value in each probe entry; Q4 breached surface and drafted durations kept. Extra instructions noted for later Parts: practice sheets exclude flags and every derived field (Part C); no --restricted / --ignore-user-config; test whether --setting-sources "" is accepted (Part D).

Result (source: output of `.\.venv\Scripts\python.exe data\.pytest_tmp\s10b2_scratch\check_probes.py`)
- data/gold/s10/practice_probes.yaml (git-ignored, data/gold/): validate_practice_set 6 valid (PR-5..PR-10).
- Derived duration / flags: PR-5 B [attachment_g_material, multi_component]; PR-6 C [ethylene_oxide]; PR-7 B [nano]; PR-8 A []; PR-9 A [attachment_g_material, multi_component]; PR-10 B [].
- Name check (profile_checks.near_equal): probes 6 vs practice names 4 → 0 hits; vs judge_file_v1.yaml device_name fields 30 → 0 hits; within probes 0 hits.
- Banned-term check over every string value of each probe entry: 110 strings, 0 hits.
- practice_probes.yaml sha256_lf `d91a44d4d5911ff8ab3d94e022ca23b61ca0d0ea070011e904f8dc49f79b9597`.
- Limit (Q1 = D): a fully non-contacting device cannot be expressed in the s10 profile formats (contact_category must be surface / externally_communicating / implant and match a component), so codebook §6's whole-device "all not_applicable" rule is not tested in practice and cannot occur in eval either. PR-9 tests the per-component part only (non_contact component → row "none"). DeviceProfile requires a contact_site even on a non_contact component; PR-9's display unit carries `intact_skin` with material class `other` (so it adds no flag).

SELF-AUDIT (Part B, STOP 1)
1. Asked vs done: probes written: done. Validate: done (6). Name check: done (0 hits). Banned-term check: done (0). Hariom's review: pending.
2. Decisions I made myself: device types/materials within S2 and the approved table; PR-9 finger pad silicone (polymer, so attachment_g_material is derived from it); banned list = profile_checks.BANNED + toxic, haemo, endotox, degrad + regex `\biso\b` and 5-digit numbers starting with 1; within-probe name check added.
3. Deviations: no deviations.
4. How I implemented it: data/gold/s10/practice_probes.yaml (new, ignored); data/.pytest_tmp/s10b2_scratch/check_probes.py (temp, ignored; reads device_name only from practice_profiles.yaml and judge_file_v1.yaml, prints counts). No code or test change.
5. Checks with real output: see Result.
6. Frozen items: formats.py, practice_profiles.yaml, codebook, instructions, engine/guard/overclaim, standards/data YAMLs, manifests untouched.
7. Holdouts and sealed files: judge_file_v1.yaml read by the script for device_name only (count 30 printed, no names); no sealed file opened.
8. Safety: no commit/push; .env not opened; nothing outside the repo; no LLM call; no blocks.
9. Paid/external calls: 0. Engine use: rule_02 via validate_practice_set on the 6 probes (as S2 says).
10. Not checked / assumed: near_equal catches only near-identical names; paraphrases could pass.
11. Errors and retries: none.
12. Risks + needs: Hariom reviews practice_probes.yaml and replies "probes OK" or notes.

### Part B: Hariom's notes applied (2026-11-15), STOP 1 again (awaiting "probes OK")
Notes (rule D23: no probe may resemble an eval device by use): PR-6 → titanium middle-ear ossicular prosthesis (permanent, metal only, not designed to degrade, EO); PR-7 → compression sleeve, knit fabric on intact skin with a stated nano-silver coating, continuous 240 h (B); PR-8 → cardiopulmonary bypass circuit tubing pack, one surgery ~4 h, extracorporeal circuit; PR-9 → adhesive skin-surface temperature probe (8 h, intact skin) + separate bedside monitor (non_contact); PR-5, PR-10 unchanged.
Re-check (source: `.\.venv\Scripts\python.exe data\.pytest_tmp\s10b2_scratch\check_probes.py`)
- validate_practice_set 6 valid. Duration / flags: PR-5 B [attachment_g_material, multi_component]; PR-6 C [ethylene_oxide]; PR-7 B [attachment_g_material, nano]; PR-8 A []; PR-9 A [attachment_g_material, multi_component]; PR-10 B [].
- Name check: vs practice 4 names 0 hits; vs judge-file 30 device_name fields 0 hits; within probes 0 hits.
- Banned terms: 111 strings, 0 hits.
- New practice_probes.yaml sha256_lf `de70acab3a954b472217176a55abc1dcb4aae801c785c209ca059d2873f3490d`.
- Decisions I made myself: PR-7 fabric class polymer (so attachment_g_material is derived) and non_sterile (garment); PR-9 adhesive pad acrylic polymer; component names reworded to match the new devices.

### Part B: STOP 2 (2026-11-15)
- Hariom: "probes OK" for practice_probes.yaml sha256_lf `de70acab3a954b472217176a55abc1dcb4aae801c785c209ca059d2873f3490d` (file git-ignored).
- Code/test changes in Part B: none. Only this report changes in git.
