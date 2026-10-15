---
status: accepted
date: 2026-10-15
conflict-id: C-13
evidence: reasoned
---
# 0013: Mandatory means an endpoint must be addressed

## Context and Problem Statement

An endpoint classification can be mistaken for a demand to perform a specific test or a statement of compliance.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Endpoint-addressing labels with a caveat.
- Interpret every mandatory endpoint as a required test.
- Omit the regulatory caveat.

## Decision Outcome

Use mandatory to mean must be addressed in the biological evaluation. Existing data, chemical characterisation with toxicological risk assessment, rationale, or a test may satisfy it. Preserve the caveat from labels.v1.yaml verbatim in responses.

## Required response caveat

FDA guidance is nonbinding and "should" means recommended. Conformance to recognised standards is voluntary unless incorporated by reference. In the EU, MDR Annex I §10.1 makes material toxicity a general safety and performance requirement.

Source: `standards/data/labels.v1.yaml`, copied verbatim.

## Consequences

The guard rejects banned wording and response assembly includes the caveat. These safeguards support interpretation; they are not legal validation.

## Confirmation

`tests/test_standards.py::test_guard_banned_phrase_replaced`; `tests/test_standards.py::test_fake_request_contract_and_provenance`.

## Evidence and Status

Sources: standards/data/labels.v1.yaml; standards/guard.py; standards/assemble.py.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
