# S11a Part II Code Survey and GTP PDF Inspection Report

Date: 2026-11-26
Branch: `s11a/survey`

## PLAIN SUMMARY

- The Part II codebase already pins PageIndex at `50a71b686d0397df7cb193c33806eb035574b890` in `standard` mode, builds trees with `anthropic/claude-sonnet-5-5`, and wires Ragas `0.4.3` collections metrics to Azure `text-embedding-3-small` and an explicit judge with no default.
- Eleven S11b gaps were found: the verifier model is still `claude-haiku-4-5-20251001` instead of `claude-haiku-5-5`, no vector-RAG baseline or cost ledger exists, and `eval/eval_pageindex.py` lacks v2 golden schema (`gold_page_sets`, `answerable`, `pdf_sha256`), refusal scoring, judge model-version drift stops, and answer saving.
- Navigation in `pageindex/pi_agent.py` does not yet use `cache_control` prompt caching, omits cache and verifier tokens from returned usage, lacks `max_retries=0`/timeouts, and overwrites answers during repair without saving both the pre-repair and post-repair versions.
- Installing `pageindex/requirements_partII.txt` in `.venv` verified `pageindex 0.2.10` at the exact pinned commit and `litellm 1.106.0`, with the full offline fake test suite passing (`20 + 1 + 2725 passed, 2 skipped`).
- Inspection of the 116-page GTP PDF confirmed `104/116` text pages, high `PyPDF2`/`PyMuPDF` parity (`0.9839` mean similarity, ~`48-50k` approx tokens), Contents at physical pages `10-11`, a constant `+11` body offset (`physical = printed + 11`), and two `pageindex/inspect_pdf.py` bugs (reading header year `2019` and missing the Contents pages).

## SELF-AUDIT

1. **Asked vs done**
   - Part A read-only code survey (`A1-A15` + `Gaps for S11b`): done (`43` committed repository files read; `github/workflows/ci.yml` from the second file list did not exist at that path).
   - Part B read-only PDF and environment inspection (`B1`, `B1-commit`, `B2a`, `B2b`, `B3`, `B5a`, `B6`): done (`7` read-only commands run by Hariom on 26 Nov 2026; outputs summarised as numbers-only facts).
   - Part C report (`logs/S11a_report.md`) and commit command: done (`logs/S11a_report.md` created on branch `s11a/survey`; zero git commands executed).

2. **Decisions I made myself**
   - Used `FULL-CONTEXT` reading mode across `pageindex/`, `eval/`, `llm/`, `observability/`, `embed/`, `retrieval/`, `eval/s10/` (`.py` code only), `tests/`, and Part II/Azure ADRs (`0006`, `0007`, `0009`, `0010`, `0012`, `0025`, `0026`, `0029`) to audit against `S11-01..14`.
   - Omitted `--dump-toc`, `--json`, and `B5b` in Part B so inspection printed compact numbers only and wrote zero files.
   - Added `B1-commit` using `installed_pageindex_commit()` to verify the exact git commit SHA beyond `pip show` version `0.2.10`.
   - Read `logs/S10c3_report.md` as a style check before authoring `logs/S11a_report.md`.

3. **Deviations**
   - `CLAUDE.md` was read prior to the Part A plan gate to obey `AGENTS.md`; all Part II source files were read only after Part A `"OK plan"`.
   - `github/workflows/ci.yml` from the second file list returned file-not-found (`README.md:6,21` references `.github/workflows/ci.yml`, which was not read).
   - `B5b` (all-page parity dump) was dropped per Hariom's Part B plan modification.

4. **How I implemented it**
   - Files created/changed: `logs/S11a_report.md` (created only; zero code, config, test, or manifest edits).
   - Main sections authored: `PLAIN SUMMARY`, `SELF-AUDIT`, `Part A: Code Survey (A1-A15)`, `Part B: GTP PDF Inspection & Environment Facts (B1-B6)`, `Gaps for S11b`.
   - Exact commands given to Hariom (Part B, all run with `.venv\Scripts\python`):
     - `.venv\Scripts\python -m pip show pageindex ragas litellm anthropic openai PyPDF2 pymupdf`
     - `.venv\Scripts\python -c "import sys; sys.path.insert(0,'pageindex'); from pi_common import installed_pageindex_commit; print(installed_pageindex_commit())"`
     - `.venv\Scripts\python pageindex\inspect_pdf.py data\pdfs\guidelines_GTP.pdf`
     - `.venv\Scripts\python -c "import pymupdf; d=pymupdf.open('data/pdfs/guidelines_GTP.pdf'); bm=d.get_toc(); labs=[p.get_label() or '' for p in d]; print('bookmarks_count:', len(bm)); print('bookmark_pages:', [(lvl, pg) for lvl, _, pg in bm]); print('nonempty_labels:', [(i+1, l) for i, l in enumerate(labs) if l])"`
     - `.venv\Scripts\python -c "import sys; sys.path.insert(0,'pageindex'); import pymupdf, inspect_pdf as ip; d=pymupdf.open('data/pdfs/guidelines_GTP.pdf'); off, votes=ip.page_offset(d); print('mode_offset:', off, 'votes:', votes); runs=[(i+1, n, (i+1)-n) for i, p in enumerate(d) if (n:=ip.printed_number(p)) is not None]; print('detected_pages:', len(runs)); changes=[r for j, r in enumerate(runs) if j==0 or r[2]!=runs[j-1][2]]; print('offset_runs_start_phys_printed_offset:', changes)"`
     - `.venv\Scripts\python pageindex\parser_parity.py data\pdfs\guidelines_GTP.pdf --threshold 0.90`
     - `.venv\Scripts\python -c "import sys; sys.path.insert(0,'pageindex'); import parser_parity as pp; a=pp.pypdf2_pages('data/pdfs/guidelines_GTP.pdf'); b=pp.pymupdf_pages('data/pdfs/guidelines_GTP.pdf'); ca=sum(len(x) for x in a); cb=sum(len(x) for x in b); na=sum(len(pp.normalise(x)) for x in a); nb=sum(len(pp.normalise(x)) for x in b); print('pypdf2_raw_chars:', ca, 'approx_tokens_div4:', round(ca/4)); print('pypdf2_norm_chars:', na, 'approx_tokens_div4:', round(na/4)); print('pymupdf_raw_chars:', cb, 'approx_tokens_div4:', round(cb/4)); print('pymupdf_norm_chars:', nb, 'approx_tokens_div4:', round(nb/4))"`

5. **Checks with real output**
   - `B1` (pre-install): `ragas 0.4.3`, `openai 2.54.0`, `anthropic 1.14.0`, `PyPDF2 3.0.1`, `pymupdf 1.28.2`, `litellm 1.105.1`; `pageindex` not installed.
   - Install (`pip install -r pageindex\requirements_partII.txt`, after 198-line `pip freeze` backup): `litellm 1.106.0`, `openai-agents 0.20.0`, `pageindex 0.2.10`.
   - `B1-commit`: `50a71b686d0397df7cb193c33806eb035574b890`.
   - Post-install full fake test suite (Windows): `20 + 1 + 2725 passed, 2 skipped`.
   - `B2a` / `B2b` / `B3` / `B4`: `116` pages, `104/116` with text (median `2027` chars/page), `12` near-empty (`7, 9, 15, 83, 86, 87, 93, 103, 105, 111, 113, 115`), `4` bookmarks (level `1`, targets `-1, -1, 10, 12`), `0` page labels, automated offset `-63 (1 vote)` / `2019` on physical `12-114` (wrong), automated TOC not detected; Hariom manual PDF-viewer check: Contents physical `10-11`, printed footer = `physical - 11` on physical `31, 61, 85` (offset `+11`), physical `86` divider image (no content), physical `87` blank.
   - `B5a`: `116/116` pages, mean similarity `0.9839`, min `0.0`, `<0.90` on pages `1` (`0.856`), `2` (`0.875`), `86` (`0.000`).
   - `B6`: `PyPDF2` `198645` raw / `193919` normalised chars; `PyMuPDF` `197024` raw / `193371` normalised chars; approx `48-50k` tokens (`chars / 4`).

6. **Frozen items**
   - Untouched: `eval/s10/manifests/` (including `.ots` and `s10c_code_lock.json`), `specs/`, `report.pdf`. Zero code, config, test, or manifest files modified.

7. **Holdouts and sealed files**
   - Confirmed not opened, not printed, not hashed, not scored; nothing under `data/` opened, listed, or linked by the agent.

8. **Safety**
   - No commit, no push, no `git` run, no shell commands run by agent, no keys printed, `.env` not opened, nothing under `data/` opened by agent, no files created outside the repository, no scratch files created, no AI attribution or assistant names, no guard or hook blocks encountered.

9. **Paid/external calls**
   - `0` calls, \$0 cost.

10. **Not checked / assumed**
    - Installed source internals of `pageindex` (`0.2.10` at `50a71b686d0397df7cb193c33806eb035574b890`) and `ragas==0.4.3` were not read from `.venv`; PageIndex internal LiteLLM behavior is inferred from `pageindex/pi_common.py`, `pageindex/pi_ingest.py`, and `docs/decisions/0029-azure-route-part-ii-indexing-judge.md`.
    - `.github/workflows/ci.yml` was not read.

11. **Errors and retries**
    - Read attempt on `github/workflows/ci.yml` (exact path from List 2) returned `file does not exist`; not retried since CI config was non-essential for `A1-A15`.
    - `pageindex/inspect_pdf.py` `printed_number()` misread header year `2019` on physical pages `12-114` and missed Contents pages `10-11` (recorded in Gaps 9 and 10; Hariom checked offset `+11` and Contents `10-11` manually).

12. **Risks for later steps + what I need from Hariom**
    - Risks: `verifier_model` mismatch (`claude-haiku-4-5-20251001` vs S11-01 `claude-haiku-5-5`); lack of judge `response.model` drift guard (S11-02); missing baseline, cost ledger, v2 golden schema, refusal metric, and pre/post-repair answer persistence; CWD-relative `store_dir`; unpinned `anthropic`, `PyMuPDF`, `PyPDF2`, `openai-agents`; `inspect_pdf.py` header-year and TOC detection bugs.
    - Need from Hariom: stage and commit `logs/S11a_report.md` on `s11a/survey`, push, open PR, and verify CI passes before S11b.

---

## Part A: Code Survey (A1-A15)

### Files Read
- Root and pins: `CLAUDE.md`, `tasks.py`, `check_setup.py`, `requirements.txt`, `README.md`, `CORPUS.md`
- Part II (`pageindex/` and `eval/`): `pageindex/pi_common.py`, `pageindex/pi_ingest.py`, `pageindex/pi_agent.py`, `pageindex/inspect_pdf.py`, `pageindex/parser_parity.py`, `pageindex/pi_settings.yaml`, `pageindex/requirements_partII.txt`, `eval/eval_pageindex.py`, `eval/pageindex_adapter.py`
- LLM and observability: `llm/__init__.py`, `llm/provider.py`, `llm/azure_openai.py`, `llm/smoke.py`, `llm/cypher.py`, `ner/client.py`, `standards/classifier.py`, `observability/tracing.py`, `observability/lf_common.py`, `observability/run_summary.py`, `observability/trace_anthropic_loop.py`
- Part I embed and retrieval: `embed/embed.py`, `embed/store.py`, `embed/token_stats.py`, `retrieval/hybrid_retriever.py`, `retrieval/query_embeddings.py`, `retrieval/cypher_cache.py`, `retrieval_eval/bm25.py`, `retrieval_eval/metrics.py`
- S10 lock and ledger (`.py` code only): `eval/s10/paid_lock.py`, `eval/s10/frozen_candidates.py`, `eval/s10/live.py`, `eval/s10/runner.py`, `eval/s10/judge_runner.py`, `eval/s10/s10c_lock.py`, `eval/s10/code_lock.py`
- Tests: `tests/conftest.py`, `tests/_netguard.py`, `tests/test_netguard.py`, `tests/test_pi_settings.py`, `tests/test_pi_ingest.py`, `tests/test_pi_agent.py`, `tests/test_eval_pageindex.py`, `tests/test_parser_parity.py`, `tests/test_llm_provider.py`, `tests/test_tracing.py`, `tests/test_env_template.py`
- Docs and ADRs: `docs/architecture.md`, `docs/engineering_journal.md`, `docs/decisions/README.md`, `docs/decisions/0006-pageindex-mode.md`, `docs/decisions/0007-pdf-backends.md`, `docs/decisions/0009-ragas-factory.md`, `docs/decisions/0010-manual-tracing.md`, `docs/decisions/0012-gtp-identity.md`, `docs/decisions/0025-provisional-gpt-substitute.md`, `docs/decisions/0026-azure-hosted-embeddings.md`, `docs/decisions/0029-azure-route-part-ii-indexing-judge.md`, `logs/S10c3_report.md`
- Attempted but absent at path: `github/workflows/ci.yml` (`NOT FOUND`)

### A1. Pins
- **FACT:**
  - PageIndex commit pin: `50a71b686d0397df7cb193c33806eb035574b890` pinned via `pageindex @ git+https://github.com/VectifyAI/PageIndex@50a71b686d0397df7cb193c33806eb035574b890` (`pageindex/requirements_partII.txt:16`) and `PAGEINDEX_PIN_SHA = "50a71b686d0397df7cb193c33806eb035574b890"` (`pageindex/pi_common.py:87`).
  - Enforcement: `installed_pageindex_commit()` reads `direct_url.json` (`vcs_info.commit_id`) via `importlib.metadata` (`pageindex/pi_common.py:169-179`). `pageindex_commit_or_refuse()` raises `RuntimeError` if installed commit `!= PAGEINDEX_PIN_SHA`, unless `allow_unpinned_pageindex=True` (`pageindex/pi_common.py:57`, `pageindex/pi_settings.yaml:22`) or `--allow-unpinned-pageindex` (`pageindex/pi_ingest.py:78-90,115,173-174`), in which case the manifest records `"unpinned: <commit>"`. `build_tree()` (`pageindex/pi_ingest.py:29-76`) does not call `pageindex_commit_or_refuse()` directly.
  - Package pins in repository files:
    - `ragas`: `ragas==0.4.3` (`requirements.txt:36`, `pageindex/requirements_partII.txt:19`).
    - `litellm`: `litellm>=1.97.0` (`requirements.txt:24`) and `litellm==1.106.0` (`pageindex/requirements_partII.txt:18`).
    - `anthropic`: `anthropic>=1.0` (`requirements.txt:11`; comments note `1.11.0`/`1.14.0`; no exact pin).
    - `openai`: `openai>=1.70` (`requirements.txt:10`) and `openai==2.54.0` (`pageindex/requirements_partII.txt:21`).
    - `PyPDF2`: `PyPDF2>=3.0` (`requirements.txt:26`; `pageindex/requirements_partII.txt:13,24-25` notes `3.0.1` resolved transitively).
    - `PyMuPDF`: `PyMuPDF>=1.24` (`requirements.txt:28`; no exact pin).
- **Gap for S11b:** `anthropic`, `PyPDF2`, `PyMuPDF`, and transitive `openai-agents` lack exact pins, `requirements.txt` (`litellm>=1.97.0`) differs from `pageindex/requirements_partII.txt` (`litellm==1.106.0`), and `build_tree()` skips `pageindex_commit_or_refuse()` if called outside `ingest()`.

### A2. Tree build
- **FACT:**
  - `build_tree(pdf_path, s)` calls `backend = index_backend(s)` and sets ContextVar `pageindex.utils._llm_backend` around the call (`pageindex/pi_ingest.py:42-46,75`).
  - For default `index_mode: standard` (`pageindex/pi_common.py:32,64-70`, `pageindex/pi_settings.yaml:3`), it calls `pageindex.page_index_classic.page_index_main(pdf_path, opt)` with `ConfigLoader().load({...})` passing `index_model`, `summary_model`, `toc_check_page_num=20`, `max_page_num_each_node=10`, `max_token_num_each_node=20000`, `if_add_node_id="yes"`, `if_add_node_summary="yes"`, `if_add_doc_description="yes"`, and `if_add_node_text="no"` (`pageindex/pi_ingest.py:58-73`).
  - Model selection: `index_model` and `summary_model` come from `pi_settings.yaml` (both already `"anthropic/claude-sonnet-5-5"`, `pageindex/pi_settings.yaml:10-11`, matching `pageindex/pi_common.py:34-35`). `index_backend(s)` requires both non-empty and on the same prefix route (`"anthropic/"` or `"openai/"`); for `"anthropic/"`, it returns `{"max_tokens": s.litellm_max_tokens}` (`16000`) and requires no Azure credentials (`pageindex/pi_common.py:109-166`).
  - Tree and manifest storage: copied PDF goes to `<store_dir>/pdfs/<sha[:16]>.pdf`, tree to `<store_dir>/trees/<sha[:16]>.json`, and manifest atomically via `.tmp` + `os.replace` to `<store_dir>/manifest.json` (`pageindex/pi_ingest.py:128-153`, `pageindex/pi_common.py:212-228`).
  - `DocRecord` fields include `sha256`, `index_mode`, `index_model`, `summary_model`, `index_route`, `pageindex_version`, `pageindex_commit`, `page_count`, `doc_description`, `built_at`, `pdf_path`, and `tree_path` (`pageindex/pi_ingest.py:139-151`, `pageindex/pi_common.py:183-203`).
- **INFERENCE:** `pi_ingest` is not wired to Azure/OpenAI only; LiteLLM `"anthropic/claude-sonnet-5-5"` is already the default and switching routes requires only changing `index_model`, `summary_model`, and `litellm_max_tokens` in `pageindex/pi_settings.yaml`. (`pageindex` package internals are not in committed repository files.)
- **Gap for S11b:** `pageindex/pi_ingest.py:165-179` CLI lacks `--settings`, `store_dir: ./store` (`pageindex/pi_settings.yaml:2`) is CWD-relative, and `--force` overwrites `tree_path` instead of being write-once.

### A3. Navigation agent
- **FACT:**
  - SDK and model: Uses the Anthropic SDK directly (`anthropic.Anthropic().messages.create(...)`, `pageindex/pi_agent.py:20,162-164,306`) with `s.agent_model` = `"claude-sonnet-5-5"` (`pageindex/pi_settings.yaml:12`) and hardcoded `max_tokens=4096` (`pageindex/pi_agent.py:163`).
  - Caps and outline budget: `agent_max_turns=12`, `max_pages_per_fetch=12`, `max_pages_total=40`, `overview_char_budget=24000` (`pageindex/pi_common.py:59-62`, `pageindex/pi_settings.yaml:25-28`).
  - Tool names: `get_tree_overview`, `get_node`, `fetch_pages` (`pageindex/pi_agent.py:33-60`).
  - Citations: `SYSTEM_TMPL` instructs `[p. N]` or `[pp. N-M]` after every factual sentence using fetched PDF page headers; `CITE_RE` extracts page specs (`pageindex/pi_agent.py:30,71,235-242`).
  - Output fields: `question`, `doc_id`, `answer`, `status`, `cited_pages`, `cited_not_fetched`, `fetched_pages` (`sorted(sess.fetched)`), `verdicts`, `repaired`, `tool_calls`, `usage`, `retries`, `latency_s`, `contexts` (`[sess.fetched[p] for p in sorted(sess.fetched)]`) (`pageindex/pi_agent.py:339-347,353-360`).
  - All fetched pages recorded: `sess.fetched.update(fetch_pages(sess.rec.pdf_path, new))` accumulates every valid fetched page across all `fetch_pages` calls in both initial and repair `run_agent` calls (`pageindex/pi_agent.py:123,308,321`).
- **Gap for S11b:** `anthropic.Anthropic()` is constructed without `max_retries=0` or explicit `timeout`, `resp.model` is absent from the returned dict, and `max_tokens=4096` is hardcoded in `run_agent`.

### A4. Citation check
- **FACT:**
  - Model setting: `verifier_model: claude-haiku-4-5-20251001` (`pageindex/pi_common.py:38`, `pageindex/pi_settings.yaml:13`) — does not match S11-01 (`claude-haiku-5-5`).
  - Forced tool output and verdicts: `tools=[VERDICT_TOOL]`, `tool_choice={"type": "tool", "name": "report_verdicts"}`, `max_tokens=4096`; enum `["supported", "partially_supported", "unsupported", "needs_citation", "no_factual_content"]`; `FAIL = {"unsupported", "partially_supported", "needs_citation"}` (`pageindex/pi_agent.py:199-214,272-275,295`).
  - Deterministic guards: (1) any cited page `p not in fetched` forces `verdict = "unsupported"`; (2) if `verdict == "supported"` and `v.get("evidence")` is non-empty, `_norm(v["evidence"])[:80]` not in `_norm(" ".join(fetched[p] ...))` downgrades to `"partially_supported"`; (3) missing verdict for a claim defaults to `"unsupported"` (`pageindex/pi_agent.py:280-291`).
  - Effect on answer: Failed check modifies the answer — triggers one repair `run_agent` round, re-runs `verify()`, and if claims still fail, drops failing sentences and appends `[Note: N statement(s) removed because they could not be verified against the cited pages.]` (`pageindex/pi_agent.py:314-332`).
- **Gap for S11b:** `verifier_model` must be updated to `claude-haiku-5-5`, both the pre-repair and post-repair answers must be saved when repair/dropping runs, `verify()` token usage is missing from `usage`, `next(b for b in resp.content if b.type == "tool_use")` (`pageindex/pi_agent.py:277`) lacks a fallback guard, and `"supported"` with empty `evidence` (or empty `pages`) is unguarded.

### A5. Prompt caching (S11-14)
- **FACT:**
  - `cache_control` in Part II: `NOT FOUND` (`pageindex/pi_agent.py:163` passes `system=system` as plain `str`; compare `ner/client.py:38` which uses `'system': [{'type': 'text', 'text': SYSTEM_PROMPT, 'cache_control': {'type': 'ephemeral'}}]`).
  - Marking site and change size: `run_agent` formats `system` once (including `{outline}`) at `pageindex/pi_agent.py:149-152` and passes `system=system` at `pageindex/pi_agent.py:163`; changing `system` to a cache-controlled block list is a `1-2` line change.
  - Usage logging of cache tokens: `observability/tracing.py:44-49` captures `cache_read_input_tokens` and `cache_creation_input_tokens` when Langfuse is enabled, whereas `run_agent`'s `usage` dict tracks only `input_tokens`, `output_tokens`, `turns` (`pageindex/pi_agent.py:156,166-168`).
- **Gap for S11b:** Add `cache_control: {"type": "ephemeral"}` on `system` in `run_agent` (`1-2` lines) and include `cache_read_input_tokens`, `cache_creation_input_tokens`, and `verify()` tokens in `usage`.

### A6. Wrapper rule
- **FACT:**
  - Part II does not use a unified wrapper across its call lanes (`llm/provider.py:28-43` covers only Part I roles `S4`, `S10`, `s8_cypher`, `s8_hyde`, `s8_prelabel`):
    - Tree build (`pageindex/pi_ingest.py:42-75`): calls `page_index_main` with `_llm_backend`; no response cache (only whole-tree SHA cache), no project retries/timeout, no usage logging.
    - Navigation agent and citation check (`pageindex/pi_agent.py:161-168,271-276,306`): direct `anthropic.Anthropic().messages.create()` calls; optional Langfuse spans (`observability/tracing.py`), no response cache, no explicit timeout, SDK default retries, `verify()` usage dropped.
    - Judge and Ragas embeddings (`eval/eval_pageindex.py:127-150`): raw `AsyncOpenAI` passed to Ragas `llm_factory` / `embedding_factory`; no cache, project retries/timeout, usage logging, or tracing.
    - Baseline: `NOT FOUND`.
- **INFERENCE (options for counting PageIndex internal LiteLLM usage):**
  1. Register a `litellm.callbacks` / `CustomLogger` hook around `build_tree()` to capture `prompt_tokens`, `completion_tokens`, cache tokens, `response.model`, latency, and errors into a per-call JSONL log.
  2. Wrap `litellm.completion` / `litellm.acompletion` around `build_tree()` (and wrap `AsyncOpenAI` passed to Ragas) to centralize retries, timeout, ledger/cap, usage logging, and `response.model` checking.
  3. Declare any uncapturable PageIndex internal path as a pre-registered exception.
- **Gap for S11b:** Part II needs a unified wrapper across `pi_agent`, `eval_pageindex`, and the baseline (retries, timeout, cache, usage, model-version logging) plus a LiteLLM callback/hook (or declared exception) for `pi_ingest` internals.

### A7. Judge
- **FACT:**
  - Client and factory: `azure_async_client()` creates `AsyncOpenAI(base_url=azure_v1_base_url(endpoint), api_key=key)` from `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY`; `build_ragas()` enforces `check_deployment_names` and calls `llm_factory(judge, client=client, **judge_kwargs(judge))` with default `"openai"` provider (`eval/eval_pageindex.py:127-142`).
  - Temperature and `max_tokens`: `JUDGE_MAX_TOKENS = 8192`; `judge_kwargs(judge)` sets `max_tokens=8192` and adds `reasoning_effort="low"` only if `is_reasoning_model(judge)` (`gpt-5*` or `o\d*`, so `gpt-4.1-mini` sends `max_tokens=8192` only); `temperature` is not passed (`eval/eval_pageindex.py:78,119-124`, `pageindex/pi_common.py:104-106`).
  - `n/a` / `NaN` handling: `score_sample()` catches exceptions, `None`, non-numeric values, and `math.isnan(value)`; `counts()` sums `judge_errors_total` across answered items; any error sets `valid=False`, `exit_code=2`, writes raw rows + manifest, and prints `WITHHELD` / `RUN INVALID (R4)` (`eval/eval_pageindex.py:153-170,250,318-320,338-340,359-361`).
  - Required setting with no default: Confirmed (`Settings.judge_model = None`, `pageindex/pi_settings.yaml:32` `judge_model: null`; `eval/eval_pageindex.py:285-289` calls `ap.error(...)` if unset unless `--no-ragas`).
  - Read back model version from each response: `NOT FOUND` — `eval/eval_pageindex.py` reads only `(await metric.ascore(...)).value` and never inspects `response.model`.
- **Gap for S11b:** Wrap `AsyncOpenAI` in `eval/eval_pageindex.py` to log `response.model` per judge call and stop if it changes (S11-02), set `max_retries=0`/timeout, and add cached-answer re-judging (`gpt-5-mini` sensitivity check, S11-02).

### A8. Ragas embeddings
- **FACT:**
  - `eval/eval_pageindex.py:134-142` uses `s.embedding_model` (default `"text-embedding-3-small"`, `pageindex/pi_common.py:54`, `pageindex/pi_settings.yaml:34`), validated by `check_deployment_names`, via `embedding_factory("openai", model=embedding_model, client=client)` on `azure_async_client()`.
  - Part I uses the same model and Azure deployment: `text-embedding-3-small` (`1536` dims) on Azure OpenAI (`embed/embed.py:21,50`, `retrieval/query_embeddings.py:11`, `docs/decisions/0026-azure-hosted-embeddings.md:12-17`).
- **Gap for S11b:** None for embedding model identity (both use Azure `text-embedding-3-small`), though `eval/eval_pageindex.py` does not record/check embedding `response.model` or usage.

### A9. Baseline
- **FACT:**
  - Part II vector-RAG baseline: `NOT FOUND`.
  - Whole-page embedder: `NOT FOUND` (`embed/embed.py` and `embed/token_stats.py:29-30` embed Part I clauses via `title + "\n" + text`).
  - Reusable Part I components:
    - `embed/embed.py`: `EmbeddingConfig` (`from_env`), `AzureOpenAIEmbedder`, `OpenAIEmbedder`, `FakeEmbedder`, `RollingTokenBudget`, `retry_after`, `pack`, `text_hash`.
    - `retrieval/query_embeddings.py`: `QueryEmbeddings` (`JsonlCache` + `confirm_response`); `retrieval/cypher_cache.py`: `JsonlCache`, `sha`.
    - `embed/store.py`: `DIMS=1536`, `validate_vector`, `MemoryStore` (no `.rank()` method; `PostgresStore.rank()` is pgvector-only).
    - `retrieval_eval/bm25.py`: `BM25` (`tokenize`, `BM25.rank`, expects `id/title/text` dicts).
    - Not directly reusable / absent: `HybridRetriever` (`retrieval/hybrid_retriever.py`) requires Part I Neo4j `:Clause` and pgvector `clause_embeddings`; no reranker exists (`NOT FOUND`).
- **Gap for S11b:** Build a Part II 1-chunk-per-page vector-RAG baseline (`text-embedding-3-small` via `AzureOpenAIEmbedder`/`QueryEmbeddings` + exact cosine top-`k` + shared Sonnet generator/prompt/refusal/`[p. N]` format + Haiku citation check).

### A10. Golden set
- **FACT:**
  - No golden file is committed (`NOT FOUND`). `eval/eval_pageindex.py:295-297` loads a JSON list and `validate_golden()` enforces exact keys `{"id", "question", "reference", "gold_pages"}` (1-based `int >= 1`, `[]` allowed) and unique `id`s (`eval/eval_pageindex.py:38-40,194-204`).
  - v2 fields (`answerable`, `gold_page_sets`, `type`, `reference_claims`, `pdf_sha256`): `NOT FOUND`.
  - What breaks on v2 schema: (1) top-level object `{pdf_sha256, ...}` crashes `validate_golden()` iterating dict keys; (2) missing `gold_pages` raises `ValueError` (`eval/eval_pageindex.py:197-199`); (3) `page_metrics(retrieved, g["gold_pages"])` calls `set(gold)` (`eval/eval_pageindex.py:87`), raising `TypeError: unhashable type: 'list'` on `gold_page_sets`; (4) `unanswerable = not g["gold_pages"]` (`eval/eval_pageindex.py:212`) ignores `answerable`; (5) `pdf_sha256` is unchecked.
- **Gap for S11b:** Update loader and `validate_golden()` in `eval/eval_pageindex.py` for v2 golden schema (`pdf_sha256`, `answerable`, `gold_page_sets`, `type`, `reference_claims`) and check `pdf_sha256` against the indexed PDF manifest.

### A11. Metrics
- **FACT:**
  - Page hit/recall/precision: Set-based in `page_metrics(retrieved, gold, failed)` over `fetched_pages` or `cited_pages` (`--retrieved`); single `gold_pages` list only; `gold == []` returns `None`; agent failures score `hit=0.0, recall=0.0, precision=len(hits)/len(R) if R else None` (`eval/eval_pageindex.py:83-99,213-214`).
  - Refusal detection: `NOT FOUND` — no refusal detector or refusal accuracy metric exists.
  - Unanswerables in Ragas: Excluded (`scores = {m: None}`, `ragas_note = "unanswerable"`, `score_sample` skipped, excluded from summary means) (`eval/eval_pageindex.py:220-221,248,343-346`).
  - Outputs and write mode: `--out` (`eval_results.json` default) writes per `(run, judge_repeat, id)` metric rows and `<out_stem>.manifest.json`, omitting `answer`, `contexts`, `cited_pages`, `verdicts`, and `usage` (`eval/eval_pageindex.py:229-232,316-376`); both files use `write_text(...)` (overwriting, not write-once `'x'`, `eval/eval_pageindex.py:317,375`).
- **Gap for S11b:** Support `gold_page_sets`, add refusal detection/accuracy for unanswerables (S11-07), persist full outputs (`answer`, pre/post-repair, `contexts`, `cited_pages`, `verdicts`, `usage`), and enforce write-once (`'x'`) outputs.

### A12. Cost control
- **FACT:**
  - `eval/s10/paid_lock.py:10-41`: Nonblocking `paid_lock(root)` (`msvcrt.locking` on Windows / `fcntl.flock` on POSIX) locking `data/runs/s10c_paid.lock`.
  - `eval/s10/frozen_candidates.py:38-45,246-360`: `s10c-ledger.v2` append-only fsync'd reserve/settle JSONL ledger (`CALL_CAP = 150`, `RESERVES = {'candidates': 11, 'pilot': 6, 'main': 6}`).
  - `eval/s10/live.py:108-150,309-605`: Preflight/execute/code-lock driver requiring `LLM_PROVIDER == 'azure_openai'`, `LANGFUSE_ENABLED == 'false'`, and `eval/s10/manifests/s10c_code_lock.json`.
  - `eval/s10/s10c_lock.py:39-98` locks `paid_lock.py`, `frozen_candidates.py`, and `live.py`.
- **INFERENCE:** Locked S10 ledger/live modules reject Anthropic and hardcode S10c steps and integer request counts, so they cannot directly wrap Anthropic + Azure Part II calls; Part II should build a small dedicated lock and USD/call ledger modeled on `paid_lock.py` and `reserve_unit`/`settle_unit`.
- **Gap for S11b:** Build a Part II cost cap, reserve/settle ledger, and paid-process lock (`s11_paid.lock`) without modifying locked S10 files.

### A13. Page numbering
- **FACT:** Physical 1-based page indices are used across `pageindex/pi_common.py:3-5,300-308` (`doc[p - 1]`), `pageindex/inspect_pdf.py:4-5,44,60,128`, `pageindex/parser_parity.py:8,62`, `pageindex/pi_agent.py:53-54,71,124`, and `eval/eval_pageindex.py:40,200-201` (`p >= 1`).
- **INFERENCE:** Three places need care: (1) `parse_page_spec("0")` (`pageindex/pi_common.py:279-297`) accepts `0` which `fetch_pages()` silently skips; (2) fetched PDF page text still contains printed footer numbers, so the agent could cite a printed number if it happens to sit in `sess.fetched`; (3) `subtree_span()` (`pageindex/pi_common.py:333-343`) trusts PageIndex's `start_index`/`end_index` (verified by S11-13 tree check).
- **Gap for S11b:** Reject page `< 1` in `parse_page_spec()` and verify PageIndex `start_index`/`end_index` against physical Contents pages in S11-13.

### A14. Tests
- **FACT:** All existing Part II tests run offline in fake mode (`socket.socket.connect` denied, fake `pageindex`/`ragas`/Anthropic/`AsyncOpenAI`):
  - `tests/test_pi_settings.py` (159 lines): settings defaults, mode validation, Azure deployment allow-list, reasoning detection, Anthropic vs Azure `index_backend`, secret non-leakage, `installed_pageindex_commit`.
  - `tests/test_pi_ingest.py` (176 lines): `standard` vs `flash` calls, explicit models, Azure no-cap backend, manifest provenance, unpinned PageIndex refusal and `--allow-unpinned-pageindex` override.
  - `tests/test_pi_agent.py` (133 lines): `max_tokens`/`empty_answer` single retry and failure, `max_turns` failure, `answer_question` skipping `verify` on failure, repair round dropping unfetched citation, `verify` marking unfetched citation `unsupported`.
  - `tests/test_eval_pageindex.py` (246 lines): `build_ragas` collections wiring + `_netguard`, missing judge/env refusal, `--no-ragas`, 3 repeats `mean [min-max]`, unanswerable exclusion, agent failure as system outcome, judge error/NaN/None invalidation (exit 2, `WITHHELD`), `--retrieved cited`, `validate_golden`.
  - `tests/test_parser_parity.py` (74 lines): whitespace normalisation, threshold flagging, page count mismatch, CLI `--json`, tiny real PDF parity (`pytest.importorskip` if `PyPDF2`/`pymupdf` missing).
  - `tests/test_tracing.py:226-245`: `test_pageindex_generation_and_tools` span sequence without prompt leaks.
  - `tests/test_netguard.py` (130 lines): Windows `socketpair` loopback exception under `block_network`.
  - Missing tests (`NOT FOUND`): `pageindex/inspect_pdf.py` and `eval/pageindex_adapter.py`.
- **Gap for S11b:** Add offline tests for `inspect_pdf.py`, `pageindex_adapter.py`, `pi_agent.py` (`cache_control`, quote-not-found downgrade, pre/post-repair answer saving), and all S11b additions.

### A15. Live-run surprises
- **FACT:**
  1. `pageindex/pi_settings.yaml:13` sets `verifier_model: claude-haiku-4-5-20251001` instead of `claude-haiku-5-5`.
  2. `pageindex/pi_settings.yaml:2` sets `store_dir: ./store` (CWD-relative; `eval/pageindex_adapter.py:6-8` expects CWD = `pageindex/`, whereas running from repo root creates `store/` at repo root).
  3. `pageindex/pi_agent.py:25` uses `from pi_common import ...`, failing if imported as `pageindex.pi_agent` without `pageindex/` on `sys.path`.
  4. `pageindex/pi_agent.py:306` (`anthropic.Anthropic()`) and `eval/eval_pageindex.py:131` (`AsyncOpenAI(...)`) omit `max_retries=0` and explicit `timeout`.
  5. `eval/eval_pageindex.py` drops `answer`, `contexts`, `cited_pages`, `verdicts`, and `usage` from `--out` and overwrites existing files.
  6. Env vars read: `ANTHROPIC_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `OPENAI_BASE_URL` (warns if set, `pageindex/pi_common.py:127-134`), `PI_FAMILY`/`PI_VERSION` (`eval/pageindex_adapter.py:25-26`), `LANGFUSE_ENABLED`/`LANGFUSE_TRACING_ENABLED`; reusing `embed/embed.py` also requires `TIKTOKEN_CACHE_DIR` with `cl100k_base` (`9b5ad71b2ce5302211f9c61530b329a4922fc6a4`, `embed/token_stats.py:17-26`).
  7. `requirements.txt:46-47` notes a potential conflict between `ragas`/`langchain-community<0.4` and `langchain>=1.0`, though Part B confirmed `.venv` installed `pageindex/requirements_partII.txt` cleanly with the full offline test suite passing.
- **Gap for S11b:** Anchor `store_dir` cleanly to `pageindex/store`, fix `sys.path`-independent imports, set `max_retries=0` and explicit timeouts, and verify `TIKTOKEN_CACHE_DIR` if reusing `embed/embed.py`.

---

## Part B: GTP PDF Inspection & Environment Facts (B1-B6)

*(All outputs run and pasted by Hariom on 26 Nov 2026; numbers only, no corpus text or Contents titles.)*

- **B1 (Installed packages & PageIndex commit pin):**
  - **FACT (Hariom `B1` pre-install output):** `ragas 0.4.3`, `openai 2.54.0`, `anthropic 1.14.0`, `PyPDF2 3.0.1`, `pymupdf 1.28.2`, `litellm 1.105.1`; `pageindex` was not installed.
  - **FACT (Hariom install output, after 198-line `pip freeze` backup):** `pip install -r pageindex\requirements_partII.txt` installed `litellm 1.106.0`, `openai-agents 0.20.0` (transitive, unpinned), and `pageindex 0.2.10`.
  - **FACT (Hariom `B1-commit` output):** `installed_pageindex_commit()` returned `50a71b686d0397df7cb193c33806eb035574b890` (matches `PAGEINDEX_PIN_SHA` and `pageindex/requirements_partII.txt:16`).
  - **FACT (Hariom post-install test output, Windows):** Full fake-mode test suite passed: `20 + 1 + 2725 passed, 2 skipped`.
- **B2 (Text layer, bookmarks, page labels):**
  - **FACT (Hariom `B2a` output):** `116` physical pages; `104/116` pages with text (median `2027` chars/page); `12` near-empty pages: `7, 9, 15, 83, 86, 87, 93, 103, 105, 111, 113, 115`.
  - **FACT (Hariom manual PDF-viewer check):** Physical page `86` is an annexures divider image (no content); physical page `87` is blank.
  - **FACT (Hariom `B2a`, `B2b` output):** `4` embedded bookmarks, all level `1`, targets `-1, -1, 10, 12`; PDF page labels: `none`.
- **B3 (Printed-to-physical page offset):**
  - **FACT (Hariom `B2a`, `B3` output):** `pageindex/inspect_pdf.py` reported offset `"-63 (1 vote)"` and `printed_number()` returned `2019` (header year) on most pages from physical `12` to `114`, so the tool's automated offset is wrong.
  - **FACT (Hariom manual PDF-viewer check):** Contents pages (`10-11`) have no printed numbers; printed footer equals `physical - 11` on physical pages `31, 61, 85` (constant body offset `+11`).
- **B4 (Contents page physical numbers):**
  - **FACT (Hariom `B2a` output):** `pageindex/inspect_pdf.py` reported printed TOC `"not detected in first 20 pages"`.
  - **FACT (Hariom manual PDF-viewer check):** Contents pages are physical pages `10-11`.
- **B5 (Parser parity: `PyPDF2` vs `PyMuPDF`):**
  - **FACT (Hariom `B5a` output):** Page counts `116/116`; mean similarity `0.9839`, min `0.0`; `3` pages below `0.90`: page `1` (`0.856`), page `2` (`0.875`), page `86` (`0.000`, image only).
- **B6 (Whole-PDF character & approximate token counts):**
  - **FACT (Hariom `B6` output):** `PyPDF2` total chars `198645` raw / `193919` normalised; `PyMuPDF` total chars `197024` raw / `193371` normalised; approximate tokens (`chars / 4` method, labelled approximate) about `48-50k`.

---

## Gaps for S11b

1. **Verifier model mismatch (S11-01)**
   - **What:** `verifier_model` is `claude-haiku-4-5-20251001` instead of `claude-haiku-5-5`.
   - **Where:** `pageindex/pi_settings.yaml:13`, `pageindex/pi_common.py:38`.
   - **Smallest fix:** Update both to `claude-haiku-5-5`.
   - **Risk:** Low; verify `claude-haiku-5-5` acceptance in pilot before main run.

2. **Azure judge model-version logging, drift stop, and cached sensitivity mode (S11-02)**
   - **What:** `eval/eval_pageindex.py` cannot inspect `response.model` per Ragas call, cannot stop on Azure auto-upgrade, and cannot re-score cached answers with `gpt-5-mini`.
   - **Where:** `eval/eval_pageindex.py:127-170,267-377`.
   - **Smallest fix:** Wrap `AsyncOpenAI` (`chat.completions.create` and `embeddings.create`) to log `response.model` per call, halt on version drift, set `max_retries=0`/timeout, and add a `--from-cached` mode for sensitivity judging.
   - **Risk:** Medium; verify wrapper compatibility with `ragas==0.4.3` `llm_factory`.

3. **Prompt caching, full token logging, verifier guards, and pre/post-repair answer persistence (S11-14, S11-01)**
   - **What:** `pi_agent.py` lacks `cache_control` on `system`, drops cache and `verify()` tokens from `usage`, omits `max_retries=0`/timeout, crashes with `StopIteration` if `verify()` returns no `tool_use` block, leaves empty-`evidence` `"supported"` verdicts unguarded, and replaces answers during repair/removal without preserving both pre-repair and post-repair text.
   - **Where:** `pageindex/pi_agent.py:149-168,272-291,306-347`, `eval/pageindex_adapter.py:29-43`, `eval/eval_pageindex.py:208-233`.
   - **Smallest fix:** Add `cache_control: {"type": "ephemeral"}` on `system`, accumulate cache and `verify()` tokens into `usage`, set `max_retries=0`/timeout, guard missing `tool_use` and empty `evidence`, record `resp.model`, and persist both pre-repair and post-repair answers across `pi_agent`, `pageindex_adapter`, and `eval_pageindex`.
   - **Risk:** Low; verifiable with scripted fake Anthropic responses in `tests/test_pi_agent.py`.

4. **Unified Part II wrapper and PageIndex internal usage counting (Engineering rule)**
   - **What:** Part II calls (`pi_ingest` LiteLLM internals, `pi_agent`, `eval_pageindex`, baseline) do not share one wrapper for retries, timeout, cache, usage logging, and model-version tracking.
   - **Where:** `pageindex/pi_ingest.py:42-75`, `pageindex/pi_agent.py:162,272,306`, `eval/eval_pageindex.py:127-170`.
   - **Smallest fix:** Route `pi_agent`, `eval_pageindex`, and baseline calls through one Part II wrapper, and attach a `litellm` callback/hook around `build_tree()` to log PageIndex internal usage (or declare any uncaptured internals in pre-registration).
   - **Risk:** Medium; confirm LiteLLM callback payload shape against `pageindex 0.2.10` and `litellm 1.106.0`.

5. **Missing vector-RAG baseline (S11-06)**
   - **What:** No 1-chunk-per-page `text-embedding-3-small` cosine baseline exists for Part II.
   - **Where:** New module under `pageindex/` reusing `embed/embed.py` (`AzureOpenAIEmbedder`/`QueryEmbeddings`).
   - **Smallest fix:** Extract 1 page per chunk with `pymupdf`, embed via `text-embedding-3-small`, rank by exact cosine top-`k` (`k = round(mean pages PageIndex fetched)` primary and `k = 5` reference), and reuse the exact same Sonnet prompt, refusal rule, `[p. N]` format, and Haiku verifier.
   - **Risk:** Medium; requires local `TIKTOKEN_CACHE_DIR` if using `embed/embed.py` directly, and prompt/verifier parity must stay exact.

6. **Golden v2 schema, `gold_page_sets`, refusal scoring, and write-once full outputs (S11-05, S11-07)**
   - **What:** `eval/eval_pageindex.py` rejects v2 schema (`pdf_sha256`, `answerable`, `gold_page_sets`, `type`, `reference_claims`), lacks refusal detection/accuracy scoring, omits `answer`/pre-repair/`contexts`/`cited_pages`/`verdicts`/`usage` from `--out`, and overwrites existing files.
   - **Where:** `eval/eval_pageindex.py:83-99,194-251,316-376`.
   - **Smallest fix:** Support v2 schema + `pdf_sha256` verification, score `gold_page_sets` alternatives, compute unanswerable refusal accuracy, persist complete per-item outputs (for blind hand-grading and `gpt-5-mini` sensitivity judging), and switch writes to write-once `'x'`.
   - **Risk:** Medium; preserve compatibility with existing `tests/test_eval_pageindex.py` coverage.

7. **Part II cost cap, call/cost ledger, and paid-process lock (S11-09)**
   - **What:** No Part II cost cap, ledger, or paid lock exists; S10's `eval/s10/paid_lock.py` and `eval/s10/frozen_candidates.py` are locked by `s10c_code_lock.json` and S10c-specific.
   - **Where:** New Part II helper under `pageindex/` or `eval/` (leave `eval/s10/*` untouched).
   - **Smallest fix:** Add a Part II nonblocking process lock (`s11_paid.lock`) and fsync'd append-only reserve/settle ledger tracking call/USD caps across Anthropic and Azure calls.
   - **Risk:** Low-medium; keep all S10 locked files untouched.

8. **Path, pin, and page-spec hardening (S11-03, A1, A2, A13, A14, A15)**
   - **What:** `store_dir: ./store` (`pageindex/pi_settings.yaml:2`) is CWD-relative, `pi_ingest.py` CLI lacks `--settings`, `build_tree()` skips `pageindex_commit_or_refuse()`, `parse_page_spec("0")` passes validation and is silently skipped, and `eval/pageindex_adapter.py` has no dedicated test.
   - **Where:** `pageindex/pi_settings.yaml:2`, `pageindex/pi_ingest.py:29,165-179`, `pageindex/pi_common.py:282-297`, `tests/`.
   - **Smallest fix:** Resolve `store_dir` relative to `pageindex/`, add `--settings` + `build_tree()` pin guard, reject `p < 1` in `parse_page_spec()`, and add unit tests for `eval/pageindex_adapter.py`.
   - **Risk:** Low.

9. **`inspect_pdf.printed_number()` header-year bug (Part B finding)**
   - **What:** `inspect_pdf.printed_number()` matches header year `2019` on physical pages `12-114`, yielding wrong offset `"-63 (1 vote)"` instead of `+11`.
   - **Where:** `pageindex/inspect_pdf.py:25,33-39` and missing tests under `tests/`.
   - **Smallest fix:** Scan bottom footer lines before header lines (or footer-only) and ignore 4-digit years (`19xx`/`20xx`), plus add unit tests in `tests/`.
   - **Risk:** Low; verify against both GTP and synthetic header/footer PDFs.

10. **`inspect_pdf` missed Contents at physical `10-11` (S11-13 check)**
    - **What:** `inspect_pdf.toc_pages()` returned `"not detected in first 20 pages"` even though GTP Contents sits at physical pages `10-11`.
    - **Where:** `pageindex/inspect_pdf.py:24,51-61` and S11-13 tree-quality verification.
    - **Smallest fix:** Confirm during the S11-13 tree-quality check that PageIndex (`toc_check_page_num=20`) detected and mapped physical Contents pages `10-11` accurately (and optionally check/refine `TOC_LINE` regex in `inspect_pdf.py` with a test).
    - **Risk:** Low-medium; if PageIndex also struggled to parse the Contents layout on `10-11`, S11-13 catches it before any golden evaluation.

11. **Exact dependency pins and requirements alignment**
    - **What:** `anthropic`, `PyMuPDF`, `PyPDF2`, and transitive `openai-agents` are unpinned/lower-bounded, and `requirements.txt` (`litellm>=1.97.0`) is looser than `pageindex/requirements_partII.txt` (`litellm==1.106.0`).
    - **Where:** `requirements.txt:11,24,26,28` and `pageindex/requirements_partII.txt:18-25`.
    - **Smallest fix:** Pin exact verified versions `anthropic==1.14.0`, `PyMuPDF==1.28.2`, `PyPDF2==3.0.1`, `openai-agents==0.20.0`, and align `litellm==1.106.0` across `requirements.txt` and `pageindex/requirements_partII.txt`.
    - **Risk:** Low; verify `pip check` and CI compatibility after updating pins.
