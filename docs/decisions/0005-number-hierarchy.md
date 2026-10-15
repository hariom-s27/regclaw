---
status: accepted
date: 2026-10-15
conflict-id: C-5
evidence: reasoned
---
# 0005: Derive clause hierarchy from clause numbers

## Context and Problem Statement

Clause ids already preserve source numbering. The graph contract contains no parent-child relationship type between clauses.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Derive neighbours from clause numbers.
- Persist new hierarchy edges.
- Ignore structural neighbours.

## Decision Outcome

Derive parent, child, and sibling candidates from clause numbers within the same Standard. Preserve grouping prefixes and add no clause-to-clause edges.

## Consequences

The graph schema stays stable. Grouping and non-decimal numbering require careful handling; current expansion is a string-based heuristic and its recall remains unmeasured.

## Confirmation

`tests/test_ingest.py::test_s3_9_document_group_prefixes`; `tests/test_tracing.py::test_on_off_identity_and_trace_shape`.

## Evidence and Status

Sources: retrieval/hybrid_retriever.py::stage3_expand; graph/build_graph.py; tests/test_ingest.py.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
