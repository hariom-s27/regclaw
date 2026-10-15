---
status: accepted
date: 2026-10-15
conflict-id: C-8
evidence: reasoned
---
# 0008: Pin FastMCP at the verified version

## Context and Problem Statement

MCP server behavior depends on SDK lifecycle, tool schema, and transport APIs. An unconstrained install can change those interfaces.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Pin the inspected version.
- Use an unconstrained lower bound.
- Rewrite against tutorial-era interfaces.

## Decision Outcome

Pin fastmcp==4.0.11 as recorded in requirements.txt and mcp/requirements_mcp.txt. Validate stdio tools and resources through the in-memory client.

## Consequences

Installs are more reproducible. Future upgrades need explicit contract checks; a pin is not a substitute for testing.

## Confirmation

`python tasks.py test` MCP suite; `tests/test_standards.py::test_mcp_v1_engine_with_injected_retriever`.

## Evidence and Status

Sources: requirements.txt; mcp/requirements_mcp.txt; mcp/test_server.py.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
