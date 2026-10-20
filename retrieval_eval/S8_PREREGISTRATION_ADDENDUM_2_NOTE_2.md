# S8 Pre-registration — Addendum 2, note 2: check-only audit (non-expert auditor)

**Date:** 2026-10-20. **Decided by:** Shyam (option B). **Committed:** before the audit freeze. No audit κ, judge label or merge output exists at this point (the judges have not started on eval items). Addendum 2 and note 1 are unchanged except where this note says so.

## Why
- The auditor and referee (the author) is not a regulatory specialist.
- Evidence: non-expert relevance judges agree with expert judges only to a low degree, and system rankings shift slightly when judges change (Bailey et al., SIGIR 2008).
- In Addendum 2 as frozen, the author's grade was final on audit disagreements. A non-expert could therefore overturn two strong LLM judges, and noisy audit labels could trigger a false STOP.
- The audit's job is to catch errors the two judges **share** (for example over-grading clauses that only repeat the query's words). That needs careful reading, not expert knowledge.

## Changes (all fixed now, before the audit freeze)
1. **The audit is a measurement only.** The author's audit labels never enter the qrels directly.
2. **Audit disagreements** (the author vs a same-side A/B consensus):
   - The consensus grade (the lower of A and B) **stands**, unless the referee records **both**:
     - (a) a quote copied from the clause text, which must appear in the clause;
     - (b) the guideline rule number it shows was broken.
   - Without both, the consensus stays, and the item is logged as `auditor_disagrees_kept`.
   - With both, the referee's grade is final, logged as `auditor_override_evidence`.
3. **A/B threshold disputes:**
   - The referee chooses the grade, using both judges' fields.
   - If the referee marks `unsure = yes`, the final grade is the **lower** of A and B, logged as `dispute_unsure_lower`.
4. **Agreement:**
   - The primary audit κ is unchanged: binary, all frozen audit items with an A/B same-side consensus.
   - **Secondary:** the same κ, excluding items where the author marked `unsure = yes`. Both are reported.
5. **The decision rule is unchanged:** primary κ ≥ 0.60 → PASS, otherwise STOP. On STOP, the disagreement reason codes are reviewed before the remedy is chosen. The remedy is decided from counts only, as in Addendum 2 §5.
6. **Reporting:**
   - the counts for `auditor_disagrees_kept`, `auditor_override_evidence` and `dispute_unsure_lower`;
   - the secondary κ;
   - and the sentence: "The author, a non-specialist, audited N items blind; the LLM consensus was not overridden without quoted clause text and a named guideline rule."

## Not changed
How the audit is labelled (the order, N ≥ 100, no AI), guideline v1.0.1, the judges, the merge rule (same side → lower grade), and the metrics.
