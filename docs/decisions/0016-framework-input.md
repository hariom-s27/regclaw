---
status: accepted
date: 2026-10-15
conflict-id: C-16
evidence: reasoned
---
# 0016: Explicit framework with FDA default

## Context and Problem Statement

The same profile can be evaluated under different source frameworks; silently mixing rows hides provenance.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Explicit framework with a stable default.
- Infer a framework from device text.
- Mix matrix editions without a recorded choice.

## Decision Outcome

Expose framework in DeviceProfile.v1 and default to FDA_2023_ATTACHMENT_A. Preserve unverified-row flags and the implemented fallback for incomplete alternative frameworks.

## Consequences

Callers can identify the interpretation used in a response. A selectable alternative does not establish complete data coverage.

## Confirmation

`tests/test_standards.py::test_2025_anchors_and_unverified_fallback`; `tests/test_standards.py::test_fake_request_contract_and_provenance`.

## Evidence and Status

Sources: standards/profile.py; standards/engine.py; standards/data/iso2025_anchor_rows.v1.yaml.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
