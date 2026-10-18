# Architecture decision records

This index records the fixed set of design choices and their accepted or proposed status.
Identifiers label decisions, not measurement counts. Each date records a decision;
confirmation cites tracked implementation, tests, or a reproducing command.
Measured evidence confirms the named behavior; an accepted design can still have
unmeasured performance consequences. Current measurements are in [results](../results.md).

| Number | Title | Status | Outcome |
|---|---|---|---|
| [0001](0001-none-obligation.md) | NONE as the fourth obligation label | accepted | Keep NONE, never null. |
| [0002](0002-human-obligation-gold.md) | Score regex and LegalBERT against human gold | accepted | Human gold; independent agreement pending. |
| [0003](0003-legalbert-evidence.md) | LegalBERT without an unsupported biomedical comparison | accepted | Use LegalBERT; no superiority claim. |
| [0004](0004-direct-drivers.md) | pgvector and the plain Neo4j driver | accepted | Expose SQL and Cypher directly. |
| [0005](0005-number-hierarchy.md) | Derive clause hierarchy from clause numbers | accepted | No new hierarchy edges. |
| [0006](0006-pageindex-mode.md) | PageIndex standard mode; Flash as a comparison | proposed | Primary standard mode; compare Flash later. |
| [0007](0007-pdf-backends.md) | pdfplumber scanned check and PyMuPDF page fetch | proposed | Separate inspection and fetch backends. |
| [0008](0008-fastmcp-pin.md) | Pin FastMCP at the verified version | accepted | Pin fastmcp==4.0.11. |
| [0009](0009-ragas-factory.md) | Use Ragas native llm_factory | proposed | Use the native evaluator factory. |
| [0010](0010-manual-tracing.md) | Manual Langfuse spans behind an environment flag | accepted | Opt-in spans; pin compatible SDK 5.0.1. |
| [0011](0011-public-substitutes.md) | Public substitute corpus and trend comparisons | accepted | Publish rebuild results only. |
| [0012](0012-gtp-identity.md) | Confirm GTP as the ICMR gene therapy guideline | proposed | Confirm intended document. |
| [0013](0013-mandatory-meaning.md) | Mandatory means an endpoint must be addressed | accepted | Address endpoints; preserve caveat. |
| [0014](0014-device-profile.md) | DeviceProfile.v1 per component at the MCP boundary | accepted | Strict per-component MCP input. |
| [0015](0015-endpoint-rollup.md) | Classify endpoints and roll up to parts | accepted | Matrix endpoints map to parts. |
| [0016](0016-framework-input.md) | Explicit framework with FDA default | accepted | Explicit framework; FDA default. |
| [0017](0017-exact-pgvector.md) | Exact pgvector search without an ANN index | accepted | No ANN index; latency TBD. |
| [0018](0018-long-clause-guard.md) | Stop oversized embedding input instead of splitting | accepted | Guard oversized input; no silent split. |
| [0019](0019-hybrid-applicability.md) | Rule floor plus Sonnet plus guard | accepted | Final label takes the stronger floor. |
| [0020](0020-structured-claude.md) | Structured outputs for the S4 and standards adapters | accepted | Schema outputs and low effort. |
| [0021](0021-regex-references.md) | Regex-only standard references and separate footnotes | accepted | Regex references; separate footnotes. |
| [0022](0022-heading-policy.md) | Retain grouping labels and heading parents | accepted | Raw plus policy scores. |
| [0023](0023-official-structure.md) | Official source structure as automatic parser gold | accepted | Use local official structural gold. |
| [0024](0024-duplicates-and-stubs.md) | Report near-duplicates and keep reference stubs | accepted | Keep issuer records and reference stubs. |
| [0025](0025-provisional-gpt-substitute.md) | Provisional Azure OpenAI GPT substitute for Claude | accepted (provisional) | Explicit GPT substitute; Claude stays default. |
| [0026](0026-azure-hosted-embeddings.md) | Azure-hosted text-embedding-3-small embeddings | accepted (documentation draft) | Explicit Azure selection; no fallback. |
| [0027](0027-normaliser-v1-1-freeze-amendment.md) | Normaliser v1.1 — a versioned amendment to the S4 freeze | accepted (provisional) | Versioned v1.1 rule; re-frozen before holdout. |
