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
