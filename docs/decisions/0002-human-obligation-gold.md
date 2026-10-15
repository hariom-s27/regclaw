---
status: accepted
date: 2026-10-15
conflict-id: C-2
evidence: reasoned
---
# 0002: Score regex and LegalBERT against human gold

## Context and Problem Statement

Weak labels can reproduce their own assumptions. A classifier comparison needs labels independent of either prediction method.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Treat weak labels as evaluation truth.
- Blind human gold with an independent second annotator.
- Compare only overall prediction distributions.

## Decision Outcome

Use a blind first annotation followed by independent second annotation and agreement reporting including kappa. Compare regex and LegalBERT on the same human gold. Gold collection and independent agreement remain pending.

## Consequences

The scorer supports paired comparisons, uncertainty, and agreement calculations. The existing retest measures intra-annotator consistency; an independent second annotator is a separate requirement. No agreement or model score is available yet.

## Confirmation

`tests/test_goldset.py::test_scoring_pairing_and_nominal_alpha`; independent human labeling: none yet.

## Evidence and Status

Sources: goldset/GUIDELINE.md; goldset/score.py.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
