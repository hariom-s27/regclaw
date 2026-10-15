---
status: accepted
date: 2026-10-15
conflict-id: S10-option-c
evidence: reasoned
---
# 0019: Rule floor plus Sonnet plus guard

## Context and Problem Statement

The classifier can supply explanations and upgrades, but it must not erase deterministic endpoint obligations.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Rules alone.
- Classifier alone.
- Rule floor with classifier and guard.

## Decision Outcome

Compute rule labels first, provide them to Sonnet, and guard the result so final label is the stronger of rule and classifier. Log disagreement, validate citations, and fall back to rules when the classifier is unavailable.

## Consequences

The response preserves a deterministic floor and visible model provenance. Classifier quality remains unmeasured; fallback output is not evidence of successful model reasoning.

## Confirmation

`tests/test_standards.py::test_guard_downgrade_blocked_and_logged`; `tests/test_standards.py::test_guard_upgrade_allowed_and_logged`.

## Evidence and Status

Sources: standards/engine.py; standards/classifier.py; standards/guard.py
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
