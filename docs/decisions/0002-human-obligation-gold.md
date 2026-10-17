---
status: accepted
date: 2026-10-15
conflict-id: C-2
evidence: measured
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

Use a blind first annotation followed by independent second annotation and agreement reporting including kappa. Compare regex and LegalBERT on the same human gold. The completed round uses a domain-expert first annotator and Claude as the independent AI second annotator, followed by human adjudication.

## Consequences

The scorer supports paired comparisons, uncertainty, and agreement calculations. The existing retest measures intra-annotator consistency; an independent second annotator is a separate requirement. Agreement and classifier scores are measured; the macro-F1 comparison is inconclusive. The AI second annotator and depleted weak-MAY training pool limit the evidence.

## Confirmation

`python tasks.py gold-agreement` -> `data/gold/agreement.json`, using supplied
`goldset/annotator_a_round1.csv` and `goldset/annotator_b_round1.csv` before
adjudication. This validates tracked `goldset/gold_labels_round1.csv` against
local `data/gold/gold_final.csv` and records their hashes.

`python tasks.py gold-score data/gold/gold_final.csv --output data/gold/scores_regex.json`;
`python tasks.py gold-score data/gold/gold_final.csv --predictions data/gold/pred_legalbert.json --output data/gold/scores_legalbert.json`.
Captured tables, training command and limitations: [results](../results.md#obligation-gold-and-classifier).
`python tasks.py test` -> `data/gold/tests_phase_d.txt`; math and leak-exclusion
fixtures: `tests/test_goldset.py`, `tests/test_obligation_train.py`.

## Evidence and Status

Sources: goldset/GUIDELINE.md; goldset/score.py.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
