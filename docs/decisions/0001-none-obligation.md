---
status: accepted
date: 2026-10-15
conflict-id: C-1
evidence: reasoned
---
# 0001: NONE as the fourth obligation label

## Context and Problem Statement

Descriptive, informative, and non-deontic clauses must survive enrichment without becoming requirements.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Keep only deontic labels and drop descriptive clauses.
- Keep the explicit NONE label.
- Store a nullable obligation.

## Decision Outcome

Keep SHALL, SHOULD, MAY, and NONE as the complete stored label set. NONE is a string, never null.

## Consequences

Every clause retains a valid value and can remain in the graph. The additional class needs its own gold support and evaluation; corpus frequency is not classifier quality.

## Confirmation

`tests/test_merge.py::test_merge_contract_and_canonical_strings`; `tests/test_merge.py::test_missing_duplicate_unknown_and_default`.

## Evidence and Status

Sources: CLAUDE.md; ner/merge.py; goldset/GUIDELINE.md.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
