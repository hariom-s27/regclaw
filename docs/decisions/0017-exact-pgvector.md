---
status: accepted
date: 2026-10-15
conflict-id: C-17
evidence: reasoned
---
# 0017: Exact pgvector search without an ANN index

## Context and Problem Statement

The local corpus contains 1,143 clauses (`python tasks.py graph --dry-run --from-clauses`) and the vector contract has 1536 dimensions (embed/store.py). Stage ranking must remain independent of approximate-index recall.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Exact ranking over available candidate vectors.
- HNSW approximate index.
- IVFFlat approximate index.

## Decision Outcome

Use exact cosine ranking with no HNSW or IVFFlat index. Keep candidate filtering and LIMIT in the ranking query. Stage latency and its budget are TBD; no measured speed claim justifies this choice yet.

## Consequences

This removes approximation and index-tuning variables. Exact ranking cannot recover vectors that are missing or ids excluded by graph filtering. Equal-distance ordering still needs a tie policy before any strong determinism claim. Cost grows with available candidate vectors.

## Confirmation

`tests/test_embed.py::test_sql_contract_upsert_ann_and_mock_connection`; live EXPLAIN/latency benchmark: none yet.

## Evidence and Status

Sources: embed/store.py; retrieval/hybrid_retriever.py; python tasks.py graph --dry-run --from-clauses.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
