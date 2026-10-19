---
status: accepted (provisional)
date: 2026-10-18
conflict-id: C-GPT
evidence: live smoke and offline contracts
---
# 0025: Provisional Azure OpenAI GPT substitute for Claude

## Context and Problem Statement

Claude remains the default provider and the final headline for the rebuild.
Access is currently blocked: the Bedrock agreement is `NOT_AVAILABLE`, Claude
Platform on AWS is unsubscribed, and OpenRouter has zero credit (access context
supplied with approval of design conflict C-GPT). Public substitute documents,
prompts, schemas, labels, retrieval, rule floors and metric definitions remain
unchanged. Stub results cannot establish live provider compatibility.

## Decision Drivers

- Exercise the real S4 extraction and S10 explanation paths while Claude access
  is unavailable, with explicit provisional provenance.
- Preserve default Anthropic requests, retry/stop guards and offline tests.
- Keep provider outputs separate so subsequent Claude runs preserve GPT evidence.
- Keep credentials private and limit initial smoke to three model calls.

## Considered Options

- **A:** Wait for Claude access and defer all live S4/S10 checks.
- **B:** Temporarily substitute Azure OpenAI GPT through one adapter for the
  existing S4/S10 interfaces, keeping Anthropic as the default.
- **C:** Continue with fixtures only, or adopt another provider with a separate
  compatibility and access review.

## Decision Outcome

Choose **B**, approved under C-GPT. `LLM_PROVIDER` defaults to `anthropic`;
`azure_openai` explicitly selects the provisional adapter. No fallback silently
switches providers. S4 maps the Haiku role to `AZURE_OPENAI_S4_DEPLOYMENT`; S10
maps the Sonnet role to `AZURE_OPENAI_S10_DEPLOYMENT`. The initial local deployment
for both roles is `gpt-5-mini`. S10 may point to a `gpt-5` deployment if quota is
granted; call sites do not embed either deployment name.

Azure-only effort variables default to `low` for S4 and `high` for S10:
`AZURE_OPENAI_S4_REASONING_EFFORT` and
`AZURE_OPENAI_S10_REASONING_EFFORT`. Existing Anthropic effort settings are kept.
Azure also requires `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` from the
environment. The smoke entry point loads the local `.env`; normal entry points
retain their existing environment-loading behavior. Never commit `.env`.

### Inspected API contract

| Role | REPORT model | Provisional substitute |
|---|---|---|
| Retrieval stage-1 Text2Cypher | Claude Haiku (REPORT) | gpt-5-mini, effort low (provisional), role s8_cypher |
| S8 HyDE passage (variant c) | not in the report — evaluation helper only | gpt-5-mini, effort low (provisional), role s8_hyde |
| S8 relevance pre-label | not in the report — evaluation helper only | gpt-5-mini, effort low (provisional), role s8_prelabel |

The project environment has `openai==2.54.0` and `anthropic==1.14.0`.
Azure v1 accepts `OpenAI(base_url=endpoint + '/openai/v1/', api_key=...)` with
`model=<deployment>` and needs no dated `api_version`. This is verified by a
fake HTTP transport and the live smoke, without adding a legacy Azure client.
See [Microsoft's v1 API documentation](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle).

GPT-5 mini rejects `temperature`, `top_p` and `logprobs`; the adapter omits them,
along with other sampling/stop fields that the existing requests do not use.
`max_tokens` maps to `max_completion_tokens`. System prompt text becomes a
system message; `output_config.format.schema` or the selected strict tool's
`input_schema` becomes strict `response_format.json_schema`. Prompt and schema
content are copied without edits. See [OpenAI's parameter compatibility guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.4)
and [Azure reasoning model usage](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/reasoning).

The GPT completion cap includes reasoning. Smoke call 2 exhausted a direct
8,000-token S10 mapping (`finish_reason=length`), so the adapter reserves reasoning
headroom: the existing budget times 1 for minimal/low, 2 for medium, or 3 for
high. S10 high therefore sends 24,000 combined tokens; S4 low sends 4,000.
This changes only the provisional Azure transport budget. It does not guarantee
that arbitrary future inputs will fit, nor constitute a new quality metric.
The S8 roles `s8_cypher` and `s8_hyde` instead use a fixed ×5 cap (800 → 4,000, retry
1,600 → 8,000), because family c truncated 49% of first attempts on hidden reasoning; see
[S8 Addendum 3](../../retrieval_eval/S8_PREREGISTRATION_ADDENDUM_3.md).

S4's Anthropic SDK has zero automatic retries; `ner.extract` retains four
attempts for 429/529 with 1/2/4-second backoff, one truncation-budget retry and
one schema retry. S10 retains the equivalent of Anthropic's two SDK retries
(408/409/429/5xx and connection errors, retry headers, exponential jitter).
OpenAI 2.54 rejects long `Retry-After` values above 120 seconds, whereas the
installed Anthropic SDK honors them. Azure SDK retries are disabled and the
adapter matches the inspected Anthropic S10 policy, including long headers.

Azure `stop` becomes `end_turn`, `length` becomes `max_tokens`, and content
filtering or a refusal becomes `refusal`. Unknown finish reasons remain
unsuccessful under the existing guards. Prompt/completion usage maps to
input/output tokens, including billed reasoning in output tokens.

## Consequences

Live records and summaries carry provider, deployment/model, reported model
version when available, reasoning effort and `provisional=true` for Azure.
S4 outputs/cache are under `data/runs/azure_openai/s4/`; S10 records are under
`data/runs/azure_openai/s10/`; smoke reports and the call ledger are under
`data/runs/azure_openai/smoke/`. Existing Anthropic S4 paths stay unchanged.
Run artifacts are local evidence, excluded from proposed commits.

The existing `ner.extract` and `standards.classify` generation spans retain their
names and gain provider/model metadata; Azure also records raw `finish_reason`.
Run summaries report those identities without changing metric definitions.
The smoke disables external telemetry and verifies tracing separately with fakes.

S10 option (c), `final = max(rule, LLM)`, remains in the unedited guard/engine.
No parser, clause data, gold, prompt, schema or retrieval edits are made.
GPT outputs cannot support Claude performance claims. Human grounded-accuracy
evaluation remains pending. S4's existing R11 cost metric keeps its definition
and is labeled as legacy rates, not Azure billing; separate smoke cost estimates
use illustrative **UNVERIFIED** prices of $0.25 input / $2 output per million.

## Confirmation

From the repository root in PowerShell, with the project Python active and the
Azure variables in the local `.env`:

```powershell
$env:PATH = (Join-Path $PWD '.venv/Scripts') + ';' + $env:PATH
$env:LLM_PROVIDER = 'azure_openai'
$env:REGCLAW_STUB = '0'
python -m llm.smoke S4
python -m llm.smoke S10
# Only after a mapping failure and its fix, one final invocation:
python -m llm.smoke S10
python tasks.py test
```

The persisted smoke ledger enforces the three-call total and each invocation
allows only one network request. It is not reset to make additional calls.
S4 uses one frozen clause through the real pipeline and original schema/checks.
S10 uses E2 through `get_applicable_standards`, the original schema and guard,
with the explicitly empty fixture retriever (no corpus citations).

Offline coverage: `tests/test_llm_provider.py` verifies both schema request
modes, response/usage/stop mapping, role effort, Anthropic default selection,
stub/key gating, Azure v1 routing, retry bounds, provenance, paths/cache isolation,
existing span names and run-summary provenance. Existing S4/S10 tests verify
the original request contracts and S10 rule-floor invariants.

Live smoke on 2026-10-18 used exactly three requests; both successful responses
reported `gpt-5-mini-2025-08-07`:

| Call | Role/effort | Status | Input/output tokens | Pipeline seconds | Estimated USD (UNVERIFIED) |
|---|---|---|---|---|---|
| 1 | S4 / low | Schema and grounding checks passed | 1,725 / 1,336 | 12.67 | 0.00310325 |
| 2 | S10 / high | Truncated at 8,000; guard fell back | 24,143 / 8,000 | 52.97 | 0.02203575 |
| 3 | S10 / high | Schema and guard passed at 24,000 cap | 24,143 / 11,725 | 79.65 | 0.02948575 |

These are compatibility checks, not a quality benchmark. S4 retained two grounded
device mentions and two lifecycle mentions; proposed expansions remained review
items. S10 explained the E2 skin-electrode rules with no corpus citations; every
rule floor still holds. Total illustrative cost, including the truncated request,
is $0.05462475; Azure billing and prices remain unverified.

`python tasks.py test` passed after the provider implementation and budget fix
(12 MCP + 1 summary + 301 root), and after smoke/final selection regressions
(12 MCP + 1 summary + 304 root = 317). Initial system-Python dependency and
sandbox temporary-file failures were resolved using the project Python and
normal temporary-file access. One stalled stdio attempt was stopped; subsequent
complete suite runs passed. `git diff --check` also passed.

Frozen-input checks report no changes. `data/clauses.json` SHA-256 at start and
end is `c517fcee4f89967ad62d49a42a8efe0d0ffd76df91ae1e88a2516f2b8b3c585d`.
The S10 prompt and output schema compare equal to the originals in `HEAD`.

Live call outcomes are recorded in the local smoke JSON reports. Documentation
is an uncommitted draft pending review; the decision status reflects the user's
provisional C-GPT approval.
