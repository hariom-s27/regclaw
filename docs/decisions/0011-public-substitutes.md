---
status: accepted
date: 2026-10-15
conflict-id: C-11
evidence: reasoned
---
# 0011: Public substitute corpus and trend comparisons

## Context and Problem Statement

The rebuild must be reviewable without exposing private documents or proprietary implementation material.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Public substitutes with explicit scope.
- Redistribute private source material.
- Treat absolute measurements from different corpora as interchangeable.

## Decision Outcome

Use public substitute sources, publish only rebuild measurements, and compare trends only where methodology is comparable. Keep private comparisons outside Git.

## Consequences

Results remain interpretable within their own corpus and protocol. Differences in sources and judgments limit cross-system conclusions.

## Confirmation

none yet for a cross-system trend study; `git ls-files -- "*.pdf"` checks tracked PDF absence.

## Evidence and Status

Sources: SCOPE.md; CORPUS.md.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
