---
status: proposed
date: 2026-11-03
conflict-id: C-GPT (extension, DC-1 option A)
evidence: offline contracts on fakes; no live call
---
# 0029: Opt-in Azure v1 route for Part II indexing and the Ragas judge (draft)

## Context and Problem Statement

[ADR 0025](0025-provisional-gpt-substitute.md) (C-GPT) made Azure OpenAI GPT an
explicit, provisional substitute for Claude on the S4, S10 and S8 roles only. Part II
was not covered: PageIndex indexing and summaries use Claude Sonnet through LiteLLM
(`anthropic/` models), the navigation agent uses Claude Sonnet and the citation
verifier uses Claude Haiku, both through the Anthropic SDK. The Ragas judge defaulted
to `gpt-4o-mini` on the OpenAI host, or Claude through `provider="anthropic"`.

Research R22 (PageIndex `main` @ 50a71b6, Ragas 0.4.3, LiteLLM 1.106.0) found that
running Part II on Azure needs specific wiring: PageIndex now defaults to flash mode
and to `gpt-6-*` models that 404 on Azure; PageIndex calls must not cap `max_tokens`
for reasoning models; LiteLLM and Ragas both detect reasoning models by deployment
name; Ragas must use `llm_factory` with an `AsyncOpenAI` client on the Azure v1 URL.

## Decision Drivers

- Keep Claude as the default and the report's headline configuration.
- Allow Part II indexing and evaluation to run while Claude access is blocked
  (ADR 0025 context), with explicit, recorded provenance and no silent fallback.
- Keep keys out of code, tests, logs and manifests; tests offline on fakes.
- Do not change the report's metric definitions or the agent/verifier roles.

## Considered Options

- **A:** Opt-in Azure v1 route for PageIndex indexing/summaries and for the Ragas
  judge + embeddings only. Agent and verifier stay on the Anthropic SDK.
- **B:** A, plus an Azure adapter for the agent and verifier (replaces the report's
  Sonnet/Haiku roles; needs its own decision).
- **C:** Claude only; skip the Azure-specific R22 items.

## Decision Outcome

Proposed: **A** (chosen by the user as DC-1 = A on 2026-11-03; this ADR records it as a
draft for review). B is explicitly deferred to a separate decision.

- **Indexing route.** `pi_settings.yaml` `index_model` / `summary_model` select the
  route by prefix: `anthropic/<model>` (default, unchanged) or `openai/<deployment>`
  (Azure v1). Bare names and other providers are refused, and both models must share
  a route because PageIndex's `_llm_backend` override applies to every call of the
  indexing lane. The Azure route passes `api_base=<AZURE_OPENAI_ENDPOINT>/openai/v1/`
  and `api_key=<AZURE_OPENAI_API_KEY>` explicitly (read from the environment at call
  time), not a process-wide `OPENAI_BASE_URL` (a warning is printed if it is set).
  No `max_tokens` is sent on the Azure route (`litellm_max_tokens: null` is required);
  the Anthropic route keeps its 16,000 cap.
- **Mode and models.** Standard mode always calls `page_index_main` directly; flash
  runs only when `index_mode: flash` is set (labelled comparison, ADR 0006). Index and
  summary models are always passed explicitly. The manifest records mode, both models,
  the route and the installed PageIndex commit (pinned at
  `50a71b686d0397df7cb193c33806eb035574b890`). An unpinned PageIndex commit is refused
  unless `--allow-unpinned-pageindex` (or `allow_unpinned_pageindex: true`) is given, and
  the manifest then records it as `unpinned: <commit>`.
- **Judge.** `AsyncOpenAI(base_url=<Azure v1>, api_key=env)` →
  `llm_factory(<judge deployment>, client=...)` with the default `openai` provider
  (which maps `max_tokens` to `max_completion_tokens` for reasoning models; not
  `provider="litellm"`), `max_tokens=8192`, and `reasoning_effort="low"` only for
  `gpt-5*` / `o<digit>*`. Embeddings: `embedding_factory("openai",
  model="text-embedding-3-small", client=...)`. The judge has no default. The Claude
  judge path is removed, because D2 admits only Azure deployment names.
- **Deployment names (D2).** Azure deployment names must be exactly one of
  `gpt-5-mini`, `gpt-4.1-mini`, `o4-mini`, `text-embedding-3-small`; anything else is
  refused at startup. This applies only to Azure-route names.
- **Agent (R7) on Anthropic.** OpenAI's `finish_reason == "length"` maps to Anthropic's
  `stop_reason == "max_tokens"`; it, or an empty final text, is logged and retried once,
  then the item fails. Reaching `agent_max_turns` is a failure, not a forced answer.

## Consequences

Azure-route trees and judge scores are provisional GPT evidence and cannot support
Claude performance claims (same rule as ADR 0025). Trees built on the two routes are
different artefacts; the manifest's `index_route` and models say which one a tree is.
Removing the Claude judge means Ragas scores in this rebuild come from an Azure GPT
judge; judge identity and kwargs are written to each run manifest.

`_llm_backend` is a private PageIndex API; re-check it at any PageIndex pin change.

## Confirmation

none yet for a live run. Offline: `tests/test_pi_settings.py` (routes, backend,
deployment names, no cap, no secret in output), `tests/test_pi_ingest.py` (standard
`page_index_main`, explicit models, manifest provenance, fake `pageindex` modules),
`tests/test_pi_agent.py` (R7 on a scripted fake Anthropic client),
`tests/test_eval_pageindex.py` (Ragas wiring on fake `ragas` and a fake `AsyncOpenAI`).
Pins: `pageindex/requirements_partII.txt`.

## Evidence and Status

Draft for review. Sources: `pageindex/pi_common.py`, `pageindex/pi_ingest.py`,
`pageindex/pi_agent.py`, `eval/eval_pageindex.py`. A test cited above confirms only
the behaviour it exercises. Pending live work is not replaced by synthetic fixtures.
