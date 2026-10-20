# S8 Pre-registration — Addendum 2, note 1: guideline v1.0 → v1.0.1 (rehearsal)

**Date:** 2026-10-20. **Authorised by:** Addendum 2 §4 step 3 (at most one wording fix after the judge rehearsal, before the audit). **Status:** frozen with the v1.0.1 sha in `s8_partD_freeze.json`. Addendum 2 itself is unchanged; wherever it says "guideline v1.0", the guideline in force is **v1.0.1**.

## Rehearsal
- Fresh judge sessions, guideline v1.0, the same 15 non-eval practice items, old practice outputs moved out of the kits first.
- Judge A = Claude Opus 5.5, High (Claude Code). Judge B = GPT-6 Astra, High (Codex). A first B attempt was started on another model by mistake, stopped and produced no file (logged).
- Result: A and B gave the same grade, clause state and unsure flag on 15/15 items. Rules 13, 14 and 15 were applied as intended.
- **Caveat:** rules 13–16 were written from these same 15 items, so this agreement shows the rules are understood. It is **not** evidence of agreement on eval items. The audit measures that.

## Problem found
- Both judges gave 0 to two items that the query's own narrative places in grade 1 ("clinical-investigation documents in general").
- Cause: v1.0 rule 16 listed "another document for a document-content query" as an example of 0, which contradicts rule 1 ("the narrative wins") when the narrative names such documents as grade 1. The cause is the guideline wording, not the judges.
- Impact: none on binary relevance (0 and 1 are both not relevant). Small on nDCG.

## Fix (the only change)
- Rule 16: the example "another document for a document-content query" is removed, and one sentence is added: "The narrative's grade-1 line always wins: anything it names is 1, never 0."
- No other text changes. No further rehearsal: Addendum 2 allows one fix, and re-running would invite more tuning on the practice items.
