---
status: proposed
date: 2026-10-15
conflict-id: C-6
evidence: reasoned
---
# 0006: PageIndex standard mode; Flash as a comparison

## Context and Problem Statement

The document-tree workflow needs a declared primary indexing mode before quality results can be interpreted.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Standard as primary.
- Flash as primary.
- Run both without identifying a primary configuration.

## Decision Outcome

Propose standard mode as the primary run and reserve Flash for a separately labeled comparison. No live result validates this choice yet.

## Consequences

The planned experiment has an explicit baseline. Configuration support alone does not establish successful indexing or relative quality.

## Confirmation

none yet; a complete Part II indexing and comparison run is pending.

## Evidence and Status

Sources: pageindex/pi_common.py::Settings; pageindex/pi_ingest.py.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
