---
status: accepted
date: 2026-10-15
conflict-id: S4-1
evidence: reasoned
---
# 0020: Structured outputs for the S4 and standards adapters

## Context and Problem Statement

Entity extraction and standards classification need constrained responses while preserving the existing local output contracts.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Structured outputs in the current adapters.
- Forced tool as the primary path.
- Unconstrained JSON prompting.

## Decision Outcome

Use output_config.format with JSON schema, effort low, no temperature, and no forced tool_choice in the structured-output path. Applies to claude-haiku-5-5 in ner/client.py and claude-sonnet-5-5 in standards/classifier.py.

## Consequences

The local adapters validate response schemas and stop reasons. The explicit strict_tool alternative remains in the S4 client. Legacy Cypher and PageIndex request settings are separate existing behavior and remain unchanged by tracing.

## Confirmation

`tests/test_standards.py::test_sdk_request_contract_with_real_types`; `tests/test_ner.py::test_exact_schema_prompt_and_regex_source`.

## Evidence and Status

Sources: ner/client.py; standards/classifier.py; tests/test_ner.py.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
