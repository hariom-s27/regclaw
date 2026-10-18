---
status: accepted (documentation draft)
date: 2026-10-18
conflict-id: C-AZEMB
evidence: offline contracts and live load; see docs/results.md
---
# 0026: Azure-hosted text-embedding-3-small embeddings

## Context and Problem Statement

S7 needs a complete vector load for the public substitute corpus. The user
approved C-AZEMB: keep `text-embedding-3-small` with 1536 dimensions and use Azure
OpenAI as an explicitly selected host. This changes the host of the same model.
It does not establish identical outputs across hosts or future model versions.

The deployment is `text-embedding-3-small`, SKU `GlobalStandard`, in resource
`regg222` located in `koreacentral`. The user supplied these deployment-create
results, including `model.version="1"` and
`versionUpgradeOption="OnceNewDefaultVersionAvailable"`. These are configured
metadata, not a runtime model-version attestation.

## Decision Drivers

- Complete the real embedding load with traceable host and deployment identity.
- Preserve the default OpenAI client, model and request payload.
- Stay within per-request limits and the configured rolling token budget.
- Retain input v1, the two-column table, and exact cosine ranking.
- Keep tests offline and credentials private.

## Considered Options

- Continue using the OpenAI host exclusively.
- Add explicit Azure OpenAI selection for the same embedding model.
- Change model, dimensions, clause input or approximate-search policy.

## Decision Outcome

Choose explicit Azure selection under C-AZEMB. `EMBED_PROVIDER` accepts
`openai` and `azure_openai`; unset selects `openai`. The default still constructs
`OpenAI(max_retries=0)` and sends `model="text-embedding-3-small", input=texts`.
There is no automatic provider fallback.

Azure follows the v1 client setup already used by `llm/azure_openai.py`, without
coupling embedding configuration to chat roles:

```python
OpenAI(
    base_url=f"{AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/v1/",
    api_key=AZURE_OPENAI_API_KEY,
    max_retries=0,
)
```

The request's `model` is `AZURE_OPENAI_EMBED_DEPLOYMENT`. Credentials and endpoint
come from the local environment or untracked `.env`. Azure model version comes
from `AZURE_OPENAI_EMBED_MODEL_VERSION`, default `"1"` when unset. The manifest
labels version and upgrade option as `configured`. Azure may auto-upgrade the
version under `OnceNewDefaultVersionAvailable`; reuse does not detect an upgrade
if configured metadata is left unchanged. Update the configured version and
reload when that happens.

Each request contains at most 2,048 inputs and at most
`EMBED_MAX_TOKENS_PER_REQUEST` summed `cl100k_base` tokens (default 60,000).
Packing also respects `EMBED_TPM_BUDGET` when it is smaller than the request cap.
The pacer reserves tokens immediately before every attempt, including failed
attempts, and caps the trailing 60-second window at `EMBED_TPM_BUDGET` (default
80,000, headroom under the supplied 100K quota). This pacing is local to one
embedder process; concurrent clients need their own shared quota coordination.

The existing five-attempt 429/5xx policy retains exponential backoff and now
honors `Retry-After` (seconds or HTTP date) and `retry-after-ms`. Retries also
consume local token reservations. Actual Azure quota enforcement can still
return 429s; the configured budget is not a throughput guarantee.

`data/embed_manifest.json` records provider, deployment, configured version,
upgrade policy, run receipts and per-clause actions. The reuse digest includes
provider, deployment, configured model version, model, dimensions, input version
and complete input text. Changing host or deployment forces re-embedding. Old
digests safely miss once; missing stored vectors are repaired. The manifest is
written after successful vector persistence and remains a local ignored cache.

## Consequences

Input v1 remains `title + "\n" + text`, with the 8,191-token guard and no
truncation or splitting. The table remains
`clause_embeddings(clause_id text primary key, embedding vector(1536))`.
Ranking SQL and `retrieval/` remain unchanged. Startup still rejects HNSW/IVFFlat
indexes. The statistics query uses `vector_norm(vector)`; the installed pgvector
exposes `l2_norm` only for halfvec/sparsevec, making the old implicit vector
conversion ambiguous. Parser, clause data and gold remain frozen.

GlobalStandard can route inference to Azure regions outside `koreacentral`.
Storage at rest stays in the designated Azure geography; the resource location
does not restrict global inference processing to that region. This distinction
is documented in [Microsoft's deployment-type guidance](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/deployment-types?view=foundry-classic).
The run uses the public substitute corpus.

Dry-run time is a minimum pacing schedule excluding API latency and retry
delays. The inherited illustrative token-price estimate is not verified Azure
billing. Load timing and spot checks do not establish retrieval quality or
end-to-end pipeline latency. The retrieval module still has its original
OpenAI query-embedding path; these spot checks explicitly supply Azure query
vectors to the existing stage-2 method.

## Confirmation

With Docker running, project Python active, and Azure/database settings in the
local `.env`, run from the repository root:

```powershell
$env:PATH = (Join-Path $PWD '.venv/Scripts') + ';' + $env:PATH
$env:EMBED_PROVIDER = 'azure_openai'
$env:REGCLAW_STUB = '0'
python tasks.py test
python tasks.py embed --dry-run
python tasks.py embed --live
python tasks.py embed --reconcile
# Run the three-query stage-2 command recorded in docs/results.md.
python tasks.py embed --live  # Idempotency: zero API calls.
python tasks.py test
# After finalizing these documentation drafts:
python tasks.py test
Get-FileHash data/clauses.json -Algorithm SHA256
git diff --check
git status --short
```

[S7 results](../results.md#s7-azure-embedding-load-and-reconciliation) records
the dry run, completed live receipt, reconciliation, dimensions/index inventory,
exact-ranking spot checks, idempotency and frozen-data digest. All measurements
are rebuild results. `tests/test_embed.py` covers provider selection, the default
OpenAI constructor and payload, Azure URL/deployment, both packing limits,
rolling-window arithmetic, retry reservations and headers, configured provenance,
provider/deployment/version digest changes, and persisted-manifest reuse without
network access. No commits or pushes are part of this confirmation.
