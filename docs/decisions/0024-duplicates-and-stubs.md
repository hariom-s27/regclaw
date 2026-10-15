---
status: accepted
date: 2026-10-15
conflict-id: S6-prep
evidence: reasoned
---
# 0024: Report near-duplicates and keep reference stubs

## Context and Problem Statement

Different issuers may publish similar regulatory text. References can also point to standards outside the substitute corpus.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Keep issuer-specific clauses and report candidates.
- Merge near-duplicate clauses automatically.
- Invent clause content for external referenced standards.

## Decision Outcome

Report duplicate candidates without merging documents or adding graph properties. Keep out-of-corpus referenced standards as stub Standard nodes keyed by their canonical identifiers.

## Consequences

Issuer provenance and clause ids remain intact. Similarity is a review signal; stub nodes establish a reference target only, not evidence that the standard text was ingested.

## Confirmation

`tests/test_graph.py::test_near_dups_exact_edited_and_short_text`; `tests/test_graph.py::test_plan_counts_and_stub_standard`.

## Evidence and Status

Sources: graph/near_dups.py; graph/build_graph.py
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
