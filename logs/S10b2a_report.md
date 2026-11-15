# S10b2a: judge kit, runner, label-tool practice modes, freeze script

## PLAIN SUMMARY
1. Built the S10 judge setup for practice: 6 rule probes (PR-5..PR-10), a strict judge output schema with a validator, and a seeded judge kit builder (practice and smoke rounds; eval refused).
2. Built a runner for judge A (Claude Code) and judge B (Codex CLI) with version checks, a clean environment, an empty run folder, output guards, one retry and a run log, plus a chat import for judge C (Antigravity).
3. Smoke runs by Hariom forced three fixes: no "$schema" key, stream-json with the pinned StructuredOutput tool for A, and multi-section citations; A and B smoke results are now valid.
4. Extended the label tool (practice, read-only review, audit gated on the frozen codebook and grouped by J-id) and added practice_compare, which writes numbered practice tables once the practice lock opens.
5. Wrote the codebook v1 freeze script (not run); codebook v1.0, judge instructions v1.0 and addendum A1 are still to be written by Hariom.

## SELF-AUDIT (whole S10b2a; details in each Part below)
1. Asked vs done:
   - Part A (tool facts): done.
   - Part B (probes, "probes OK"): done.
   - Part C (schema, validator, kit builder, practice kit): done.
   - Part D (runner, config; fixes 1-3 after smoke): done.
   - Part E (S7 label tool, S8 practice_compare): done.
   - Part F (S9 freeze script; code and tests only): done, not run.
   - Part G (this report): done.
2. Decisions I made myself: listed per Part. The main ones:
   - runner exit codes (0/1/2/4);
   - run-dir rules;
   - fence stripping for C;
   - B model logged as "pinned by -m" when not echoed;
   - unpinned tool means no retry;
   - numbered compare tables;
   - checklist difference rules;
   - freeze format name and check order.
   Hariom decided:
   - PR-9 option D;
   - probe notes (D23);
   - B arguments;
   - --verbose;
   - StructuredOutput pin;
   - num_turns strict;
   - the S10b2a-07 lock definition;
   - Q1-Q5 in Parts E and F.
3. Deviations: none. The three smoke fixes were planned and approved before each was made.
4. How I implemented it:
   - New code: eval/s10/judge_output.py, judge_kit.py, judge_config.yaml, judge_runner.py, practice_compare.py, codebook_freeze.py.
   - Changed code: eval/s10/label_tool.py.
   - Schema: eval/s10/codebook/judge_output.schema.json.
   - Tests: tests/test_s10b2_* (6 files) and tests/test_s10b_label_tool.py.
   - Data (git-ignored): data/gold/s10/practice_probes.yaml, and kits for practice and smoke.
5. Checks with real output:
   - Targeted run after Part F: 160 passed in 62.36s. Per file:
     - codebook_freeze 17
     - judge_output 43
     - judge_kit 13
     - judge_runner 43
     - label_tool_practice 10
     - practice_compare 6
     - s10b_label_tool 28
   - Full suite: see "Tests" below.
   - Kit shas:
     - practice kit_manifest 33581eb08fbe4344a368143e92ed4d84e796691e8cc41d8df5569fd64f1c6830
     - smoke kit_manifest 944e330f818ca8ace0dbcfcd6631e1ba017fc8c0fd83464f9173f80baa0f6976
   - Input shas:
     - schema ccef52c093a071a13cdfd4864d457347d10f4b25c1b8584e95e27c3a7a1d816d
     - probes de70acab3a954b472217176a55abc1dcb4aae801c785c209ca059d2873f3490d
6. Frozen items: untouched:
   - standards/engine.py, guard.py, overclaim.py;
   - the 4 standards YAMLs;
   - the classifier PROMPT;
   - retrieval_eval/*;
   - the corpus;
   - existing eval/s10/manifests files;
   - practice_profiles.yaml;
   - CODEBOOK_v0.1.md, JUDGE_INSTRUCTIONS_v0.1.md.
7. Holdouts and sealed files:
   - None opened, printed, copied, hashed or scored.
   - judge_file_v1.yaml was read by code for device_name only (Parts B and C name checks; counts only).
   - Files under judges/ were opened only once, by a structure-only script on Hariom's request: event types, block types, tool names and num_turns (Part D note).
8. Safety:
   - No commit or push by me.
   - .env not opened; no keys printed.
   - Nothing written outside the repo.
   - No judge or LLM call made by me (only --version and --help).
   - No AI attribution.
   - No blocks.
9. Paid/external calls: 0 by me. All judge calls were made by Hariom.
10. Not checked / assumed:
    - Real practice outputs and Hariom's practice labels.
    - The browser pages, which were tested over HTTP only.
    - The freeze on real files.
    - The C smoke import.
11. Errors and retries:
    - Smoke-driven fixes 1-3 (Part D).
    - Test-only mistakes in Parts D and E, fixed in the tests.
    - One stale argument assertion.
12. Risks for later steps + what I need from Hariom: see "Where it fails / open" below.

## Where it fails / open
- Judge B loads Hariom's Codex skills list (no off switch). The listed skills have generic web-tool names and cannot be used under read-only, no web and the guard.
- Judge C runs in chat with a manual paste; there is no version or model check beyond the label Hariom types.
- The probes (PR-5..PR-10) and practice profiles were drafted by an AI model and reviewed by Hariom.
- The Claude Code extension can auto-update; the runner checks the version before every call and stops on a change.
- The canary covers instruction files, not every possible context source.
- A fully non-contacting device cannot be expressed in the s10 profile formats, so codebook §6's whole-device rule is not tested in practice and cannot occur in eval.
- Judge A sometimes answers in text before calling StructuredOutput (num_turns 3). Hariom kept num_turns strict (1-2), so such an attempt is rejected and the one retry handles it.
- Codex --json events do not echo the model; -m pins it and the run log records "pinned by -m (not echoed in --json events)".
- Codebook v1.0, judge instructions v1.0 and addendum A1 are not written yet, and the freeze (codebook_freeze.py) has not been run. Audit labelling and the eval kit stay blocked until then.
- judge_output.py (the validator) is not in the frozen list; it is to be covered by the code lock in S10b2b.

## Practice-round commands (Hariom; placeholders only)
Hariom labels first, before opening any judge output:
```
.\.venv\Scripts\python.exe -m eval.s10.label_tool --mode practice
```
Judges A and B (run folders: new, empty, outside the repo):
```
.\.venv\Scripts\python.exe -m eval.s10.judge_runner --claude-exe "<CLAUDE_EXE>" --run-dir "<EMPTY_DIR_A>" run --round practice --judge A
.\.venv\Scripts\python.exe -m eval.s10.judge_runner --codex-exe "<CODEX_EXE>" --run-dir "<EMPTY_DIR_B>" run --round practice --judge B
```
Judge C: paste data\gold\s10\kits\practice\C\batch_01.prompt.md into the Antigravity chat as one message, then save the reply as UTF-8 to D:\s10_chat\practice_batch_01.txt. Do the same for batch_02.
```
.\.venv\Scripts\python.exe -m eval.s10.judge_runner import-chat --round practice --batch 1 --file D:\s10_chat\practice_batch_01.txt --antigravity-version "<AG_VERSION>" --model-label "Gemini 3.8 Flash (High)"
.\.venv\Scripts\python.exe -m eval.s10.judge_runner import-chat --round practice --batch 2 --file D:\s10_chat\practice_batch_02.txt --antigravity-version "<AG_VERSION>" --model-label "Gemini 3.8 Flash (High)"
```
Status, review, comparison:
```
.\.venv\Scripts\python.exe -m eval.s10.judge_runner status --round practice
.\.venv\Scripts\python.exe -m eval.s10.label_tool --mode review-practice
.\.venv\Scripts\python.exe -m eval.s10.practice_compare
```
The C smoke import (optional, one device) is the same as above with --round smoke --batch 1 after pasting data\gold\s10\kits\smoke\C\batch_01.prompt.md.

## Tests
- Full-suite command: `.venv\Scripts\python -m pytest tests mcp observability -q`
- 1397 was an expectation, not a measured run.
- 1416 passed / 1 skipped was measured on the Part E working tree, before Part F existed.
- Part E added 23 tests by file count; Part F added 17.
- Full suite at dad6de0 (E+F), run by Hariom on Windows: 1433 passed, 1 skipped (270.69 s).
- Targeted files: see SELF-AUDIT item 5.

## PR description
Title: S10b2a: judge kit, runner, label-tool practice modes, codebook freeze (base: main)

Adds the S10 judge setup for the practice round:
- Six rule probes (PR-5..PR-10; data git-ignored).
- A strict judge output schema and validator.
- A seeded judge kit builder (practice and smoke rounds; the eval round is refused until the codebook is frozen).
- A runner for judges A (Claude Code) and B (Codex CLI): per-call version check, clean environment, empty run folder, output guards, validator, one retry, resume, run log.
- A chat import for judge C, and a status command.

Fixes found by the smoke runs:
- The schema no longer carries "$schema".
- Judge A uses stream-json with the pinned StructuredOutput tool.
- Sections may cite several parts.
- The B model is logged as pinned.

The label tool gains a practice mode (80 seeded cells) and a read-only review mode. Audit mode now requires the frozen codebook and groups cells by J-id inside each look. practice_compare writes numbered practice tables once the practice lock opens. codebook_freeze writes the write-once codebook_v1.json manifest; it has not been run.

Open: codebook v1.0, judge instructions v1.0 and addendum A1 are still to be written and frozen; the validator is to be covered by the S10b2b code lock.

Tests: targeted files 160 passed; full suite 1433 passed, 1 skipped (Windows, at dad6de0).

---
Each Part's result follows below, appended at its STOP.

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

## Part C: schema, validator, kit builder (2026-11-15)
Plan "OK plan" (no answers given → my recommendations applied: Q1 SMOKE-1 = fixed generic profile in judge_kit.py, name-checked; Q2 natural id order; Q3 eval refused twice (manifest check, then unconditionally); Q4 JUDGE_INSTRUCTIONS_v0.1.md in the Part C commit; Q5 task line items joined by "; ").

Result
- eval/s10/codebook/judge_output.schema.json: draft 2020-12, S3 shape and order; 4 objects, each additionalProperties false + all properties required; only type/properties/required/items/enum (+ $schema). LF sha `240eb068…39f8`.
- eval/s10/judge_output.py: validate(result, sheet) → (ok, rule names) with rules schema, device_id, items_missing, items_extra, items_duplicate, section, reason, needs_sme, components_empty.
- eval/s10/judge_kit.py: TEMPLATE_VERSION s10-judge-prompt.v1; rounds practice / smoke / eval (eval refused); write-once per round folder.
- Targeted tests (`--basetemp data\.pytest_tmp\s10b2`): tests/test_s10b2_judge_output.py + tests/test_s10b2_judge_kit.py → 38 passed in 7.00s.
- SMOKE-1 check (source: data/.pytest_tmp/s10b2_scratch/check_smoke.py): validate_practice_set 1; name hits 0 vs 10 practice+probe names, 0 vs 30 judge-file device_name fields; banned hits 0.
- Real build, once (`.\.venv\Scripts\python.exe -m eval.s10.judge_kit --round practice`, exit 0): devices 10; A 10, B 10, C 2 batches. Input shas: codebook `7992a374…80fe`, instructions `a9a66d83d2bb648dca567a34a1c322cc8aa5ec05edbabcdd5d6f3db1f7865ac4`, schema `240eb0682bc8bfa3c96e300632f8a56325100c9c008e01ce9cf4d55fbc2e39f8`, practice_profiles `cfd11a74…f9e9`, practice_probes `de70acab…490d`. kit_manifest.json sha256_lf `89e20d61f957c0c048c33ef48305fff2bf7440b7ed30f74090746971338a57e0`.
- Post-build counts: 22 prompt files; 0 contain flags/derived/existing_evidence/attachment_g_material; 0 contain CR; kits folder git-ignored.

SELF-AUDIT (Part C)
1. Asked vs done: schema: done. Validator: done. Kit builder: done. Tests on fakes: done (38 passed). Practice kit built once: done (A 10, B 10, C 2).
2. Decisions I made myself: rule names as listed; reason must be non-blank after strip; validator checks item set, not order (S4); sheet drops existing_evidence and derived and never carries flags/split; smoke round also builds a C batch (uniform layout); round folder must be empty (write-once at folder level, so a half-built kit can't be topped up); manifest format name s10-judge-kit-manifest.v1; codebook_v1.json read as {template_version, files: {rel: {sha256_lf}}} (Part F must write this shape); expected codebook/practice shas read from s10b1_build.json, probes sha hard-coded (approved).
3. Deviations: no deviations.
4. How I implemented it: files above + tests; commands: pytest (above), check_smoke.py, judge_kit --round practice, git check-ignore, Select-String counts.
5. Checks with real output: see Result.
6. Frozen items: CODEBOOK_v0.1.md, JUDGE_INSTRUCTIONS_v0.1.md, practice_profiles.yaml, engine/guard/overclaim, standards/data YAMLs, manifests untouched (only read/hashed).
7. Holdouts and sealed files: judge_file_v1.yaml read for device_name only by check_smoke.py (count 30). No sealed file opened.
8. Safety: no commit/push; .env not opened; nothing outside the repo; no LLM call; no blocks.
9. Paid/external calls: 0. SMOKE-1 validated with validate_practice_set (rule_02), as planned.
10. Not checked / assumed: whether Claude --json-schema and Codex --output-schema accept the schema ($schema keyword included) is shown only by the Part D smoke.
11. Errors and retries: none.
12. Risks + needs: commit; OK the Part D plan.

## Part D: runner (2026-11-15)
Plan "OK plan" with: exact A/B argument lists, A env_set (CLAUDE_CODE_DISABLE_CLAUDE_MDS=1, CLAUDE_CODE_DISABLE_AUTO_MEMORY=1), canary text; Q2 attempt files `<id>.attemptN.*` + `<id>.result.json`; Q3 count devices still invalid after their one retry; Q4 add run_dir_written; Q5 strip one outer fence and log fence_stripped. Part C answers matched my choices; "$schema" kept until smoke.

Result
- eval/s10/judge_config.yaml: per-judge tool, expected_version, model, effort, args (exact lists), env_set; global env_remove, timeout_s 1800, max_consecutive_failures 2, canary_question; C antigravity-chat / "Gemini 3.8 Flash (High)". No --restricted, no --ignore-user-config.
- eval/s10/judge_runner.py: subcommands canary, smoke, run, import-chat, status; eval round refused; exit codes 0 / 1 refused / 2 input error / 4 STOP.
- Targeted tests (`--basetemp data\.pytest_tmp\s10b2`): tests/test_s10b2_judge_runner.py 29 passed in 37.52s; all three S10b2 files together 67 passed in 63.65s (output + kit + runner).
- Fake-executable cases covered: valid A and B (argv exactly config list + appended flags; "" arguments preserved; env keys removed, env_set present; cwd = run-dir); is_error; wrong model; extra Haiku key (valid); result text without structured_output (valid); non-zero exit; missing / extra item; tool-use event; no turn.completed; last message not JSON; wrong model on stderr; wrong version (STOP before any call); timeout; run_dir_written (STOP); run-dir inside repo / missing / not empty; 2 devices in a row invalid (STOP after 4 calls); retry then valid + resume (0 new calls); eval refused; import-chat (fence stripped, 4 valid / 1 invalid, re-import keeps results, paste copies kept); bad JSON, wrong length, wrong label, unknown batch; status; canary A/B (two lines, no schema, B ends with "-", folder untouched); canary non-zero exit prints the first stderr line.
- --setting-sources "": cannot be tested without a model call (works only with -p); it is in canary A. If Claude rejects it, canary A exits non-zero and prints the first stderr line → report, new plan to drop it.
- Not run by me: canary, smoke, status (Hariom runs them).

SELF-AUDIT (Part D)
1. Asked vs done: config: done. Runner: done. Tests with fake executables (all listed cases): done (29 passed). Commands for Hariom: printed, not run.
2. Decisions I made myself: STOP exit code 4; per-attempt stderr.txt also kept for A; version check runs with the child env and cwd; run-dir must be empty before each call and still empty after (else run_dir_written + STOP); canary folder may be non-empty (it holds the codeword files) but must exist and lie outside the repo, and nothing is written for a canary; canary prints up to 2 non-empty lines (300 chars each) plus exit code, guard verdict and observed models; B "model observed" read from stderr only; non-JSON lines on Codex stdout trip event_not_json; import-chat refuses a --model-label that differs from the config and copies the paste to `C/batch_NN.attemptN.paste.txt`; the smoke kit must be built first (`judge_kit --round smoke`, offline, Hariom runs it); kit prompt files are sha-checked against kit_manifest.json before use.
3. Deviations: no deviations.
4. How I implemented it: judge_config.yaml; judge_runner.py (load_config, check_run_dir, child_env, tool_version, run_child, guard_a, guard_b, Runner.call / run_round, canary, import_chat, status, main); tests/test_s10b2_judge_runner.py (fake script written to the pytest temp folder, launched with prefix [sys.executable]).
5. Checks with real output: pytest 29 passed; 67 passed for all S10b2 files.
6. Frozen items: untouched (engine/guard/overclaim, standards/data YAMLs, classifier, retrieval_eval, codebook/instructions, manifests, practice files).
7. Holdouts and sealed files: none opened in Part D.
8. Safety: no commit/push; .env not opened; tests never launch claude/codex (fakes only; ANTHROPIC_API_KEY in tests is the fake string "fake-not-a-key"); nothing written outside the repo; no model call; no blocks.
9. Paid/external calls: 0.
10. Not checked / assumed: real envelope / event shapes (fields assumed: is_error, num_turns, permission_denials, modelUsage, structured_output, result, usage; Codex item.type / turn.completed); whether Claude needs one turn with --json-schema (num_turns == 1 is enforced as specified); whether Codex writes -o into the repo folder under -s read-only. The smoke run shows all of these.
11. Errors and retries: none (tests passed on the first run).
12. Risks + needs: Hariom builds the smoke kit, runs canary A/B, smoke A/B and status, and pastes the output to Claude chat.


## Part D fix: schema "$schema" key, superseded kits, B model log (15 Nov 2026)
Smoke result (Hariom): A failed ("--json-schema is not a valid JSON Schema: no schema with key or ref https://json-schema.org/draft/2020-12/schema"); B passed (guard ok, validator valid), with no model name in its --json events.
- Schema: removed the "$schema" line only. New LF sha ccef52c093a071a13cdfd4864d457347d10f4b25c1b8584e95e27c3a7a1d816d (was 240eb068...). The validator still pins Draft 2020-12 in code.
- Moved (renamed; nothing deleted; git-ignored): kits/practice -> kits/_superseded/practice_schema0 (23 files, 391040 bytes); kits/smoke -> kits/_superseded/smoke_schema0 (4 files, 51711 bytes); judges/smoke -> judges/_superseded/smoke_schema0 (9 files, 19620 bytes). Byte totals match the pre-move listing.
- Code paths are always <KITS_REL|JUDGES_REL>/<round>, with round from fixed lists, so _superseded is never read. A new test covers this.
- Rebuilt kits (judge_kit output): smoke kit_manifest sha 944e330f818ca8ace0dbcfcd6631e1ba017fc8c0fd83464f9173f80baa0f6976; practice kit_manifest sha 33581eb08fbe4344a368143e92ed4d84e796691e8cc41d8df5569fd64f1c6830 (replaces 89e20d61... from Part C).
- compare_kits.py: smoke 3/3 and practice 22/22 prompt files byte-identical with identical manifest entries; only date and inputs.schema.sha256_lf differ (verdict True for both rounds).
- guard_b: when stderr has no "model:" line, model_observed = "pinned by -m (not echoed in --json events)", which is not a guard reason. A real model line is still recorded and checked (Hariom, Q1).

### SELF-AUDIT (Part D fix)
1. Asked vs done: all 5 plan steps done; Hariom runs the smoke re-run.
2. Decisions I made myself: constant name B_MODEL_PINNED; fake mode no_model_line; comparison checked prompt bytes as well as manifest entries.
3. Deviations: none.
4. How I implemented it: edited the schema, judge_runner.py (constant + 2-line fallback) and two test files; Move-Item; judge_kit --round smoke/practice; scratch data/.pytest_tmp/s10b2_scratch/compare_kits.py.
5. Checks with real output: 3 S10b2 test files: 69 passed in 69.70s; kit build exit 0 twice; compare verdict True twice.
6. Frozen items: untouched (the schema is not on the frozen list; its new sha goes into codebook_v1.json in Part F).
7. Holdouts and sealed files: none opened; files under judges/ were listed by name and size only, never opened.
8. Safety: no deletion, commit or push; nothing written outside the repo.
9. Paid/external calls: 0.
10. Not checked / assumed: that Claude accepts the schema without "$schema" (Hariom's smoke A shows it); B must be re-run because its old result is superseded.
11. Errors and retries: the first compare run was backgrounded and gave empty output; re-ran it in the foreground with the same script.
12. Risks + needs: Hariom re-runs smoke A, smoke B and status smoke, checks that the run folders are empty, and pastes the output.


## Part D fix 2: judge A stream-json + pinned structured-output tool; section lists (15 Nov 2026)
Smoke result (Hariom): A schema accepted but guard=num_turns twice (num_turns=2, no denials, success, structured_output present, only claude-opus-5-5); B attempt 1 validator=section ("§4.2, §5.1"), attempt 2 valid; run folders empty.
- judge_config A: --output-format stream-json + --verbose (--help 2.1.325 does not say whether it is required; added as Hariom approved); new key structured_output_tool: null.
- guard_a parses the stream; the envelope is the final event (type result). Checks: is_error false, subtype success, num_turns in {1,2}, permission_denials empty, model_missing/model_other, subagent_stats.spawned 0 if present, structured_output present. tool_use count must equal num_turns - 1, all with the pinned name (else tool_use). Null name gives structured_tool_unpinned, with no retry; the names are in the run log (tool_use_names) and on the console. New reasons: event_not_json, no_result_event, subtype, subagents, tool_use, structured_tool_unpinned, structured_output_missing (replaces result_not_json). Raw stream saved as <id>.attemptN.stream.jsonl. Canary A parses the same stream (any tool_use = tool_use).
- Validator: section = one or more parts joined by ", ", each ^§\d+(\.\d+)*$.
- Hariom's old A smoke attempts 1-2 (envelope.json) stay in judges/smoke/A; new attempts are numbered from 3.

### SELF-AUDIT (Part D fix 2)
1. Asked vs done: all plan items done, including Hariom's addition (tool_use count = num_turns - 1; test for num_turns 2 with zero tool_use).
2. Decisions I made myself: console line printing the observed tool_use names when unpinned; test-only tool name FakeStructTool; an invalid num_turns still triggers tool_use if there is more than one tool_use or a wrong name.
3. Deviations: none.
4. How I implemented it: judge_config.yaml, judge_runner.py (guard_a, call, run_round, canary), judge_output.py (section_ok), two test files.
5. Checks with real output: first run 1 failed / 97 passed (test_config_matches_approved_lists still pinned the old A args; updated to the approved list); re-run 98 passed in 33.52s.
6. Frozen items: untouched (the config sha changes; it is recorded in codebook_v1.json in Part F).
7. Holdouts and sealed files: none opened.
8. Safety: no commit, push or deletion; nothing written outside the repo.
9. Paid/external calls: 0 (claude --help only).
10. Not checked / assumed: the real stream event shapes (assistant.message.content[].type tool_use / name; final result event); whether --verbose is required; the real structured-output tool name (the next smoke A shows it).
11. Errors and retries: one stale assertion, fixed as above.
12. Risks + needs: Hariom runs smoke A (expected: invalid, structured_tool_unpinned, one call), reports the observed name; the pre-approved follow-up then pins it.


## Part D fix 3: pin StructuredOutput (15 Nov 2026)
Smoke A (Hariom): exactly one tool_use name, StructuredOutput; guard=structured_tool_unpinned only; run folder empty.
- judge_config A: structured_output_tool: StructuredOutput (the only config change; pre-approved follow-up).
- Console: a device that is not retried now prints "not retried (marked)" instead of "still invalid after retry (marked)".

### SELF-AUDIT (Part D fix 3)
1. Asked vs done: both items done.
2. Decisions I made myself: renamed test_real_config_has_unpinned_key to test_real_config_pins_structured_tool (it now asserts the pinned name).
3. Deviations: none.
4. How I implemented it: 1 config line; a retried flag in run_round; 2 test assertions.
5. Checks with real output: 3 targeted test files: 98 passed in 46.52s; git diff --stat: config 1 line, runner +4/-1, tests +3/-2.
6. Frozen items: untouched (the config sha changes again; it is recorded in Part F).
7. Holdouts and sealed files: none opened.
8. Safety: no commit, push or deletion.
9. Paid/external calls: 0.
10. Not checked / assumed: that the pinned run passes the guard (Hariom's next smoke A).
11. Errors and retries: none.
12. Risks + needs: Hariom re-runs smoke A, status smoke and the run-folder count.

### Part D note: judge A extra-turn finding and decision (16 Nov 2026)
- Finding (data/.pytest_tmp/s10b2_scratch/stream_shape.py on judges/practice/A/PR-4.attempt1.stream.jsonl; event types, subtypes, block types, tool names and num_turns only; no text printed):
  - system/init, then 5 x system/thinking_tokens;
  - assistant [thinking], then assistant [text] with no tool call;
  - rate_limit_event, then user [text];
  - assistant [tool_use: StructuredOutput], then user [tool_result];
  - rate_limit_event, then result/success with num_turns=3.
- Reading: the model first answered in plain text; after a user-side text message (its text not read; most likely a CLI prompt to use the structured-output tool) it called StructuredOutput once. Guard reason: num_turns only (one tool_use, pinned name).
- Decision (Hariom, 16 Nov 2026): keep num_turns strict (allowed 1-2). This pattern stays rejected; the runner's one retry handles it. No code change.


## Part E: label tool practice / review-practice / audit changes (S7), practice_compare (S8) (15 Nov 2026)
- judge_runner import-chat now accepts --round smoke (Hariom Q1), so judge C can be smoke-tested on SMOKE-1.
- label_tool.py:
  - Shared labelling core (_Labeller).
  - Audit: needs eval/s10/manifests/codebook_v1.json with the LF sha of the codebook in use (default CODEBOOK_v1.0.md); display order is grouped by J-id inside each look (1-150, 151-250, 251-350); the gap-free rule follows display order; CSV rows stay in list order.
  - Practice: PR-1..PR-4 x 20 parts = 80 cells, seed "S10b-v1|hariom-practice|<id>"; practice and codebook v0.1 shas checked against s10b1_build.json; judge view of the profile; labels_hariom_practice.csv with a device_id column.
  - Review-practice: read-only; needs 80 labels + valid A/B/C results for PR-1..PR-4, else it refuses with counts.
- practice_compare.py:
  - Lock S10b2a-07 (Hariom Q2): 80 labels complete AND A, B, C valid for all 10 practice devices.
  - Writes data/gold/s10/practice/practice_table_NN.md (next free number, never overwrites).
  - Table: per item A/B/C label (section), Hariom, STRADDLE; reasons for non-unanimous or STRADDLE cells; checklist fields where A/B/C differ.
  - Console prints counts only.

### SELF-AUDIT (Part E)
1. Asked vs done:
   - S7 practice / review-practice / audit: done.
   - S8: done.
   - Q1 (smoke import-chat): done.
   - Q3 (A=B=C over 200 + all-four over 80): done.
   - Q4 (numbered tables): done.
   - Q5 (device_id): done.
   - Hariom's addition (reasons + checklist differences): done.
2. Decisions I made myself:
   - In audit mode, --upto is required; outside audit it is refused.
   - Review also requires each result to pass judge_output.validate again.
   - material_classes is compared as a sorted list; components as an ordered list.
   - Checklist "different" means the A/B/C values are not all equal.
   - The table header records the date and the practice kit_manifest sha.
   - Exit codes: 0 written, 1 refused, 2 input error.
   - The progress bar shows done/total.
3. Deviations: none.
4. How I implemented it:
   - label_tool.py rebuilt from a new head plus the existing PAGE (6 small JS edits, checked to match once each) and a new REVIEW_PAGE; scratch scripts in data/.pytest_tmp/s10b2_scratch/.
   - New practice_compare.py and two new test files.
   - Existing label-tool fixture extended with a fake codebook_v1.json; existing assertions unchanged.
5. Checks with real output:
   - Six targeted files: 143 passed in 54.21s.
   - Earlier runs: 2 test bugs in new audit tests (fake list size; label count needed to cross a J-id) and 2 in compare tests (heading count; split boundaries); all in the tests, fixed there.
6. Frozen items: untouched (s10b1_build.json, practice_profiles.yaml and codebook v0.1 read only in code; none opened by me).
7. Holdouts and sealed files: none opened. No labels_*.csv or judges/ file opened (existence checks only).
8. Safety: no commit, push or deletion; tool not started on real files.
9. Paid/external calls: 0.
10. Not checked / assumed:
    - The real practice run: Hariom's labelling, the judges' practice outputs and the table on real data.
    - The browser pages were checked only through tests (HTTP), not visually.
11. Errors and retries: the 4 test bugs above.
12. Risks + needs:
    - Audit mode cannot start until Part F's codebook_v1.json exists.
    - Hariom labels the practice set before looking at any judge output.


## Part F: codebook freeze script (S9), code + tests only (16 Nov 2026)
- eval/s10/codebook_freeze.py writes eval/s10/manifests/codebook_v1.json once: {format, version v1.0, template_version, files: {rel: {sha256_lf, bytes_lf, lines}}, created_at}.
- It freezes exactly these 6 files: CODEBOOK_v1.0.md, JUDGE_INSTRUCTIONS_v1.0.md, judge_output.schema.json, judge_kit.py, judge_config.yaml, specs/S10_PREREG_ADDENDUM_A1.md.
- --expected-sha REL=SHA can be repeated and works for any of the 6 files (Hariom Q1); per-file bytes_lf and lines are recorded (Q2).
- Exit codes: 0 written, 1 sha mismatch, 2 file missing or bad argument, 3 manifest already exists.
- Not run on real files.

### SELF-AUDIT (Part F)
1. Asked vs done: code and tests done; the freeze was not run (as instructed).
2. Decisions I made myself:
   - format name s10-codebook-manifest.v1;
   - --expected-sha accepts upper-case shas and refuses the same file given twice;
   - checks run in this order: manifest exists, then arguments, then missing files, then sha check.
3. Deviations: none.
4. How I implemented it: new eval/s10/codebook_freeze.py (reuses compute() from prereg_freeze.py and TEMPLATE_VERSION from judge_kit); new tests/test_s10b2_codebook_freeze.py (temp root under data/.pytest_tmp/s10b2).
5. Checks with real output: per-file counts from one run of 7 files, 160 passed in 62.36s:
   - codebook_freeze 17
   - judge_output 43
   - judge_kit 13
   - judge_runner 43
   - label_tool_practice 10
   - practice_compare 6
   - s10b_label_tool 28
6. Frozen items: untouched. Neither judge_output.py nor judge_runner.py is in the frozen list (Hariom note 2).
7. Holdouts and sealed files: none opened.
8. Safety: no commit, push or deletion; no freeze run.
9. Paid/external calls: 0.
10. Not checked / assumed: the real v1.0 files and the addendum do not exist yet.
11. Errors and retries: none.
12. Risks + needs: after Hariom's freeze, any edit to the 6 files blocks the eval kit until a new versioned manifest exists, so every fix must land before the freeze.
