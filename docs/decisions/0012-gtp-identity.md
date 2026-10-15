---
status: proposed
date: 2026-10-15
conflict-id: C-12
evidence: reasoned
---
# 0012: Confirm GTP as the ICMR gene therapy guideline

## Context and Problem Statement

The intended Part II document is described as GTP, but the batch explicitly leaves its identity to confirmation.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Confirm the intended guideline before indexing.
- Infer identity from an acronym.
- Substitute another guideline silently.

## Decision Outcome

Record the intended interpretation as the ICMR gene therapy guideline and keep confirmation pending. Do not infer a completed corpus from the acronym.

## Consequences

The planned scope stays visible. A confirmed edition, source, and local manifest must precede an evaluation claim.

## Confirmation

none yet; document confirmation is TBD.

## Evidence and Status

Sources: CLAUDE.md Part II summary; pageindex/pi_settings.yaml.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
