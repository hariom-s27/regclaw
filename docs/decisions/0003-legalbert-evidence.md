---
status: accepted
date: 2026-10-15
conflict-id: C-3
evidence: reasoned
---
# 0003: LegalBERT without an unsupported biomedical comparison

## Context and Problem Statement

The rebuild needs an obligation baseline, but the local record does not establish a controlled comparison of biomedical and legal BERT models for this task.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Use LegalBERT and measure locally.
- Choose biomedical BERT on domain association alone.
- Claim superiority from unrelated tasks.

## Decision Outcome

Use the existing LegalBERT pipeline. Make no claim that a paper proves it outperforms biomedical BERT on this corpus.

## Consequences

Training and held-out evaluation remain pending. The architecture can be explained without turning a model-selection rationale into an empirical result.

## Confirmation

none yet for the model comparison or trained-model quality.

## Evidence and Status

Sources: obligation/train.py; obligation/infer.py.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
