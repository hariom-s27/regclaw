---
status: proposed
date: 2026-10-15
conflict-id: C-9
evidence: reasoned
---
# 0009: Use Ragas native llm_factory

## Context and Problem Statement

The evaluation adapter needs model wiring compatible with the repository's Ragas pin.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Native llm_factory.
- Legacy LangChain evaluator wiring.
- Implement a separate evaluator abstraction.

## Decision Outcome

Propose Ragas native llm_factory for evaluation. Treat evaluation configuration and actual dataset results as pending.

## Consequences

The intended integration is recorded without claiming a completed run. Dependency compatibility and evaluator behavior must be checked before publishing Part II scores.

## Confirmation

none yet for a live evaluator run.

## Evidence and Status

Sources: eval/eval_pageindex.py; requirements.txt.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
