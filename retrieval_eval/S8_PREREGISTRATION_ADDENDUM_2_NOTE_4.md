# S8 Addendum 2 — Note 4: three-judge referee and analysis details, fixed before refereeing

**Date:** 2026-11-04. **Decided by:** Shyam, on Claude chat's recommendations after an independent code review (cloud session, 3 Nov).
**Committed:** before any referee decision exists (referee progress 0 / 104), before stage 3, before any qrels or metric.
**Disclosure:** seen so far: merge stage 1–2 aggregate outputs only (judge agreement κ, number of splits, audit κ, number of audit disagreements). No referee decision, no qrels, no run list, no metric.
**Builds on:** Addendum 2, Notes 1–3, Addenda 1, 3, 4 and Addendum 4 Note 1. Where this note is more specific, it wins.

## 1. Reason codes in three-judge mode (replaces the Addendum 2 §6 list)
- The referee records `reason_code` ∈ {`clear_reading`, `guideline_gap`, `unclear_text`}.
- Per-judge correctness is derived automatically: a judge is "right" on an item when its side (relevant = grade ≥ 2) equals the side of the final grade.
- Why: the §6 codes (`A_right | B_right | both_wrong | guideline_gap`) were written for two judges and do not extend to three. This rule has been in the code since 20 Oct (before any label); this note records it in the rules.

## 2. "Unsure" on referee items
- Threshold split, referee unsure = yes → final grade = median of the three valid judge grades; on an item decided by the two-judge rule (only two valid rows) → the lower of the two (as Note 2 §3).
- Audit disagreement, referee unsure = yes → the judges' consensus stands, even if an override quote and rule are filled in. An override needs all three: a quote found in the clause, a rule number 1–16, and unsure = no.

## 3. What is counted and reported
- Per-judge correctness counts only items where the referee's decision sets the grade: `dispute_referee` and `auditor_override_evidence`. Kept consensus and unsure-median items are excluded from it and reported per category.
- "Flipped relevant ↔ not" for a split is measured against the **majority side of the three valid judges** (for a two-judge-rule item: the lower of the two). Report: referee sided with the majority (n) vs against it (n). Audit overrides keep their own flip count.
- Audit disagreements on two-judge-rule items go to the referee (as coded) and are counted separately.
- `date` in qrels.tsv = the audit-freeze date (the protocol date), the same for every row.

## 4. Analysis details not fixed before
- nDCG gain = 2^g − 1; ideal DCG over all judged clauses of the query (not only retrieved ones).
- Every paired difference = first-named arm − second-named arm: v1 − v1-noob; v1 − v1.1; v1 − vector_all.
- Bootstrap percentiles use numpy's default (linear) interpolation.

## 5. Guards (no rule change; they make a wrong input stop the run)
- The analysis refuses status `n/a` for any system except v1-noob. (C2 had 16/16 HyDE passages and 0 terminal failures, so this case should not occur; if it does, the run stops and a dated note decides it.)
- Stage 1 refuses a sheet with a duplicate (qid, clause_id) and checks the sheet's sha and item count (1,482) against the s8-sheet record, before writing anything.
- The qrels-manifest gate requires the full Addendum 4 §12 key set plus one judge-freeze entry per judge read.
- `--corpus-size` and `--expect-runs-sha` are required for s8-analyze. The runs sha may be given in full or as the logged short form `<first 8>…<last 4>`, which must match both ends; the full sha is recorded in the outputs.

## 6. Freeze record
- A new file `retrieval_eval/s8_notes_freeze.json` records the LF-normalised sha256 of this note and of Addendum 4 Note 1. The qrels manifest also records the sha of `s8_notes_freeze.json` (one entry added to the Addendum 4 §12 list). Earlier freeze files are not edited.

All S8 numbers stay labelled "provisional (GPT): stage 1 = Azure gpt-5-mini". No internship numbers.
