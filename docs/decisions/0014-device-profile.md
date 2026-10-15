---
status: accepted
date: 2026-10-15
conflict-id: C-14
evidence: reasoned
---
# 0014: DeviceProfile.v1 per component at the MCP boundary

## Context and Problem Statement

Different materials or components can have different contact categories and exposure. A single legacy device-level profile cannot express that reliably.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Strict per-component model.
- Legacy device-level profile.
- Unvalidated free-form profile.

## Decision Outcome

Accept the implemented DeviceProfile.v1 model at the MCP boundary. Keep per-component exposure, materials, and contact information, with engine-owned derived fields.

## Consequences

Invalid and additional fields are rejected before rule evaluation. Existing callers must use the implemented model; documentation must not silently add fields from older research examples.

## Confirmation

`tests/test_standards.py::test_profile_nested_and_readonly`; `tests/test_standards.py::test_mcp_v1_engine_with_injected_retriever`.

## Evidence and Status

Sources: standards/profile.py; mcp/server.py
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
