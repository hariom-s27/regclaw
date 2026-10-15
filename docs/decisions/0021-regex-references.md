---
status: accepted
date: 2026-10-15
conflict-id: S3-10-S4-3
evidence: reasoned
---
# 0021: Regex-only standard references and separate footnotes

## Context and Problem Statement

Standard identifiers and footnotes need auditable extraction without expanding the clause contract or inviting invented references.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Regex references and footnote side file.
- Ask the entity model to invent reference records.
- Attach new footnote fields to clauses.

## Decision Outcome

Extract iso_refs through the repository regex pipeline only. Expand supported shorthand and preserve canonical ids. Keep footnotes in a separate file and include their references in downstream processing without new clause fields.

## Consequences

The identifier source stays inspectable. Regex coverage remains bounded by its patterns and tested shorthand; the model cannot fill unsupported reference gaps.

## Confirmation

`tests/test_ner.py::test_canonical_ids_and_shorthand`; `tests/test_ner.py::test_footnote_references_without_extra_model_calls`.

## Evidence and Status

Sources: ner/stdref.py; ner/extract.py; ingest/ingest.py
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
