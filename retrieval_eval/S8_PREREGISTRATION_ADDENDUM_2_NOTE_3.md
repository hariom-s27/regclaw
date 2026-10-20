# S8 Pre-registration — Addendum 2, note 3: three LLM judges + a smaller blind audit

**Date:** 2026-10-20. **Decided by:** Shyam (option C).

**Committed:** before the audit freeze. No judge has labelled any eval item, and no audit κ exists. The audit has started, with a few items labelled; their content has not been seen by anyone but the author.

**Builds on:** Addendum 2, note 1 (guideline v1.0.1) and note 2 (check-only audit). Where this note differs, this note wins.

## 1. Judges
| Judge | Model | Tool | Kit |
|---|---|---|---|
| A | Claude Opus 5.5, High | Claude Code | `D:\s8_judgeA` |
| B | GPT-6 Astra, High | Codex | `D:\s8_judgeB` |
| C | **Gemini 3.8 Flash, High** | Antigravity | `D:\s8_judgeC` |

- **Why three:** three model families reduce single-family bias, and ensembles of LLM relevance judges outperform single judges (JudgeBlender, Rahmani et al. 2024; BACKGROUND).
- **Why Flash:** it is the only Gemini model available in this account. Gemini 4 Argon is unavailable, and the other Antigravity entries are Claude or OpenAI family, too close to A or B.
- **Kit C** holds exactly the same files as A and B: GUIDELINE.md (v1.0.1), JUDGE_INSTRUCTIONS.md, practice/, eval/ and output/.
- **The exact model is fixed.** A model change during the run (an update or a fallback) is logged, and that judge's affected files are re-run.

## 2. Smoke test of judge C (decided before it ran)
- **Rule, set beforehand:** ≥ 13/15 same side (relevant vs not) as the A = B practice answers → the full three-judge protocol. Fewer → a lighter mode (C shown only as a third opinion).
- **Result:**
  - **15/15 same side, 13/15 exact.** The 2 exact differences are the two items that v1.0.1 rule 16 now places at grade 1. C applied v1.0.1 there, while A and B had applied v1.0.
  - One "invalid output" error occurred during the run and recovered.
  - **→ Full three-judge protocol.**
- **Caveat:** the practice items shaped rules 13–16, so this shows the rules are understood, not eval agreement.

## 3. Merge (replaces the two-judge merge)
- **All three on the same side of the threshold** (all ≥ 2, or all ≤ 1) → **accepted.** Final grade = the **median** of the three grades.
- **Any relevant-vs-not split** → **dispute.** The author decides, seeing the query, narrative, clause and all three judges' fields. No system, rank or score is shown. If the author marks `unsure = yes`, the final grade = the median of the three, logged as `dispute_unsure_median`.
- **Missing or invalid judge rows:** the affected file is re-run once for that judge. If rows are still missing, that item is decided by the other two judges under the two-judge rule (same side → the lower grade; split → dispute), and the count is reported.

## 4. Audit (replaces the minimum in Addendum 2 §5; everything else in §5 stays)
- The same 200-item sample and fixed interleaved order. **Minimum N = 50** (was 100).
- The κ margin at N = 50 is about ±0.2 (INFERENCE); it is reported with its bootstrap CI.
- **Primary κ:** binary, the author vs the three-judge consensus, on audit items where all three are on the same side.
- **Secondary κ:** the same, excluding items where the author marked `unsure`.
- **Gate:** primary κ ≥ 0.60 → PASS; otherwise STOP. Note 2 §5 applies.
- **Audit disagreements:** the consensus (median) stands, unless the referee records a quote found in the clause text plus a guideline rule number (note 2 §2). Audit items that are three-judge disputes go to the dispute step anyway.

## 5. Agreement reported
- Pairwise Cohen's κ for A–B, A–C and B–C, binary and quadratic-weighted.
- Fleiss' κ across the three judges, binary.
- Raw agreement, the confusion matrices, and bootstrap CIs over queries (10,000 draws, seed 20261020).
- Counts for each merge and referee category.

## 6. Fallback (fixed now)
- **Trigger:** judge C cannot finish all 16 sheets within **3 calendar days** of starting (quota or errors), or more than **2%** of C's rows are still missing or invalid after one re-run.
- **Then:** the two-judge protocol of Addendum 2 + notes 1–2 is used for all items. C's labels are reported only as a secondary comparison.
- The fallback is logged with its reason.

## 7. Role of the Claude chat on disputes
- The Claude chat runs the same model as judge A. On dispute items it may explain terms and guideline rules, and it **never proposes a grade**.
- On audit items it gives no help at all.

## 8. Order (unchanged except kit C)
1. Commit this note and the code.
2. Kit C passes the kit check.
3. The author finishes the audit (N ≥ 50), then `s8-freeze-audit`.
4. Then judges A, B and C label all 1,482 items in parallel.
5. Merge, disputes and referee.
6. qrels freeze.
