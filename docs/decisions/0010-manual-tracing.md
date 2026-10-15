---
status: accepted
date: 2026-10-15
conflict-id: C-10
evidence: measured
---
# 0010: Manual Langfuse spans behind an environment flag

## Context and Problem Statement

Tracing must preserve retrieval outputs and avoid importing or contacting a tracing service when disabled.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Explicit manual observations.
- Automatic instrumentation of all calls.
- Tracing enabled unconditionally.

## Decision Outcome

Use LANGFUSE_ENABLED, default off, and short-circuit LANGFUSE_TRACING_ENABLED=false. Pin Langfuse 5.0.1: local signature inspection confirmed get_client, start_as_current_observation, update, and flush support the manual pattern.

## Consequences

The retriever root has named sibling stages. Model generations record model and available usage without prompts or clause bodies. SDK minor details are pinned; enabled exporter failures are not covered by a live tracing-service run.

## Confirmation

`tests/test_tracing.py` on/off identity, no import, kill switch, exception, shape, generation, and summary tests.

## Evidence and Status

Sources: observability/tracing.py; observability/run_summary.py; requirements.txt; tests/test_tracing.py; local SDK inspect.signature command.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
