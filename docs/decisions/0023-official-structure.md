---
status: accepted
date: 2026-10-15
conflict-id: S3f
evidence: measured
---
# 0023: Official source structure as automatic parser gold

## Context and Problem Statement

Official eCFR XML and EUR-Lex HTML supply independent numbering and hierarchy for the corresponding local documents.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Official structural gold plus explicit projection.
- Only a clause-count sanity band.
- Manual labels for every unit before any automatic score.

## Decision Outcome

Project those official units to the configured parser depth and compare ids, parents, and normalised text. Limit current automatic structure results to EPA and EU MDR, with raw and policy views.

## Consequences

This gives repeatable full-document structural checks on the supported sources. Projection decisions, source versions, body coverage, and remaining manual corpus gold still need explicit reporting.

## Confirmation

`tests/test_structure_gold.py::test_ecfr_projection`; `tests/test_structure_gold.py::test_eurlex_projection`; `python tasks.py structure-score`.

## Evidence and Status

Sources: ingest/structure_gold.py; docs/results.md.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
