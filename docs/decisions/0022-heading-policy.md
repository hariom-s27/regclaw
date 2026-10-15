---
status: accepted
date: 2026-10-15
conflict-id: S3-9-S3f-1
evidence: measured
---
# 0022: Retain grouping labels and heading parents

## Context and Problem Statement

The parser deliberately retains structural heading units. The raw official projection removes empty/title-only units and therefore disagrees with some valid parser parents.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Publish raw plus explicit policy projection.
- Delete retained parser headings.
- Treat arbitrary parser extras as correct.

## Decision Outcome

Keep grouping labels in clause numbers and preserve retained heading parents. Publish raw and policy scores together. Policy retains only independently parsed official heading ids present in parser output.

## Consequences

Policy parent accuracy is 100% for EPA and EU MDR (Phase A scorer command, captured in docs/results.md). EU policy recall remains below complete because an official unit is missing. No score authorizes modifying frozen clauses.

## Confirmation

`tests/test_structure_gold.py::test_policy_accepts_official_headings_only_and_repairs_parents`; `python tasks.py structure-score`.

## Evidence and Status

Sources: ingest/structure_gold.py; tests/test_structure_gold.py; tests/test_ingest.py; docs/results.md.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
