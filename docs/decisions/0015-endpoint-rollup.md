---
status: accepted
date: 2026-10-15
conflict-id: C-15
evidence: reasoned
---
# 0015: Classify endpoints and roll up to parts

## Context and Problem Statement

The source matrix lists biological endpoints, while tool users ask which standards parts apply.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Endpoint classification followed by explicit part mapping.
- Treat matrix cells as direct standards labels.
- Classify the whole device in one free-form call.

## Decision Outcome

Classify endpoints per component using the matrix and rules. Roll up to parts through the explicit endpoint-to-part mapping, then merge components using the strongest label.

## Consequences

The mapping is auditable and reusable. Editions and unsupported framework rows retain verification flags instead of being implied verified.

## Confirmation

`tests/test_standards.py::test_exact_r9_golden`; `tests/test_standards.py::test_property_device_union`.

## Evidence and Status

Sources: standards/engine.py; standards/data/endpoint_to_part.v1.yaml; standards/data_loader.py.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
