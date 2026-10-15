---
status: accepted
date: 2026-10-15
conflict-id: C-17-token-limit
evidence: reasoned
---
# 0018: Stop oversized embedding input instead of splitting

## Context and Problem Statement

The clause-to-vector contract uses one row per clause id. Splitting would need a deliberate representation decision.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Hard guard with the existing row contract.
- Token-accurate splitting with a new representation.
- Silent truncation.

## Decision Outcome

Stop before API or storage work if title-plus-text exceeds 8,191 tokens (embed/embed.py). Do not truncate or split silently. The local histogram records maximum 7,250 tokens and zero over the ceiling (`python tasks.py embed --dry-run`).

## Consequences

All current inputs fit according to the recorded histogram. Revisit splitting when an actual input exceeds the guard, and settle chunk ids and retrieval aggregation before changing the contract.

## Confirmation

`tests/test_embed.py::test_whole_corpus_guard_before_embed_or_store`.

## Evidence and Status

Sources: embed/embed.py; embed/token_stats.py
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
