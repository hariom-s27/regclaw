---
status: accepted
date: 2026-10-15
conflict-id: C-4
evidence: reasoned
---
# 0004: pgvector and the plain Neo4j driver

## Context and Problem Statement

The repository owns a fixed graph schema and a separate vector table. Retrieval must expose which candidate ids reach ranking.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Direct graph and vector clients.
- Adopt neo4j-graphrag orchestration.
- Consolidate storage behind a new abstraction.

## Decision Outcome

Use the plain Neo4j driver and pgvector/Postgres code. Keep SQL, Cypher, and candidate boundaries visible rather than adding a graph-RAG abstraction.

## Consequences

This limits indirection and preserves existing contracts. Connection handling, query validation, and driver compatibility remain repository responsibilities; the managed-transaction call is covered by `tests/test_retrieval_driver.py`.

## Confirmation

`tests/test_embed.py::test_sql_contract_upsert_ann_and_mock_connection`; `tests/test_tracing.py::test_on_off_identity_and_trace_shape`.

## Evidence and Status

Sources: retrieval/hybrid_retriever.py; embed/store.py; graph/build_graph.py.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
