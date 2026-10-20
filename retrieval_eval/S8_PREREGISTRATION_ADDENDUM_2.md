# S8 Pre-registration — Addendum 2: relevance labelling protocol (Part D)

**Date:** 2026-10-20. **Decided by:** Shyam (option D on 20 Oct; flexible audit size, option B, on 20 Oct).
**Frozen before:** any eval item is judged by anyone. The guideline sha below is recorded at commit time.
**Applies to:** the S8 blind sheet (1,482 items, 16 queries). It does not change Addendum 1 or 3.

## 1. Why
The 15 Oct rule was "never AI-only gold". This is replaced for S8 relevance, for a reason: the human annotator is not a regulatory specialist, and frontier models carry domain knowledge. The human stays in control as a **blind auditor** and **referee**, and a fixed decision rule can stop the plan.

## 2. Roles
| Role | Who | Sees |
|---|---|---|
| Judge A | Claude Opus 5.5, high effort, in Claude Code opened in an isolated folder (`D:\s8_judgeA`) | guideline v1.0, JUDGE_INSTRUCTIONS, its own sheets only |
| Judge B | GPT-6 Astra, high effort, in Codex opened in a separate isolated folder (`D:\s8_judgeB`) | the same |
| Auditor + referee | Shyam | the audit items first, blind and without AI help; later only the disputed items, with both judges' fields |

No system, arm, variant, rank or score is shown to anyone before the qrels freeze. The gpt-5-mini pre-labels are not used (this model family runs the system's stage 1).

## 3. Guideline
- `retrieval_eval/S8_RELEVANCE_GUIDELINE_v1.0.md`, sha recorded in `retrieval_eval/s8_partD_freeze.json` at commit.
- v1.0 = v0.9 + rules 13–16 (device scope; a passing reference is not content; list part vs whole; 0 vs 1) + matching invented illustrations + the auditor's no-AI rule.
- These fixes came from calibration on 15 **non-eval** practice items (3 non-eval questions × top-5 vector clauses).
- **Calibration result:** judge A vs judge B gave exact agreement on 11/15 items and binary (≥2) agreement on 13/15. Two threshold splits and two same-side splits were all traced to wording that rules 13–16 now settle.
- **Disclosure:** all four fixes take the more conservative reading, and on these four items that matches judge A (Claude). The chat that wrote the fixes is also a Claude model. The fixes follow the existing "unsure 1 vs 2 → 1" rule and are reviewed by Shyam. Conservative grading lowers every system's recall equally, so the comparisons are unaffected (INFERENCE).
- Shyam's own practice labels were made with GPT help under time pressure. They are excluded from every number and recorded only as a process lesson.
- No guideline change after this commit. A judge that breaks a rule is re-run on the affected file; the rule is not changed.

## 4. Order of work
1. Commit this addendum + guideline v1.0 + the audit-order change (§5). Push, then CI green.
2. Copy guideline v1.0 into both kits, then run `s8-check-kits`, which must verify GUIDELINE.md bytes = committed v1.0.
3. **Optional rehearsal:** judges A and B re-judge the practice sheet in **fresh sessions** with v1.0, writing to a new file name; old practice outputs are moved out of the kits first.
   - At most one wording fix is allowed, and only if a judge clearly misreads a new rule (logged).
   - Then v1.0 stays. If wording changes, it becomes v1.0.1 with a new sha before step 4.
4. **Shyam's audit (§5)**, then `s8-freeze-audit`.
5. Only after the freeze: copy the 16 per-query CSVs into both kits' `eval/`; judges A and B label all 1,482 items.
6. `s8-merge-labels` stage 1 (A vs B agreement), then stage 2 (audit check + disputes), then the referee, then stage 3 (qrels).
7. Freeze the qrels (sha). Only then are pool_key, runs and metrics opened (Part E).

## 5. Audit (fixed now)
- **Sample:** the existing stratified sample of 200 items. Seed 20261020; `audit_sheet.csv` sha `606d4bdb…142f`; `audit_ids.txt` sha `97069918…1a60`.
- **Order:** a fixed interleaved order, `audit_order.txt` (write-once, sha logged).
  - Within each query, the audit items are shuffled with seed 20261021.
  - Queries are then visited round-robin in the order of queries.yaml (R1, R2, R3, Q1 … Q13) (one item from each query per round; a query that runs out is skipped).
  - Any prefix therefore spreads roughly evenly across the 16 queries.
  - The order uses item_id + qid only.
- **Size:** Shyam labels in that order, without skipping, and stops when time runs out. **Minimum N = 100.** `s8-freeze-audit` accepts only a gap-free prefix of the order with N ≥ 100, and records N.
- **The stop is fair** because N is fixed by the freeze, and no LLM label or κ is visible before it. The merge refuses to run stage 2 without the freeze.
- **No AI.** The audit is Shyam's own reading: no AI tool, chatbot or judge file. The tool records a timestamp per item, and the write-up reports the median gap between consecutive saves (all save timestamps sorted by time; breaks between sittings are outliers the median absorbs).
- **Decision rule (unchanged):**
  - Binary (≥2) Cohen κ between Shyam's audit and the A/B consensus, on audit items where A and B are on the same side.
  - **κ ≥ 0.60 → PASS. κ < 0.60 → STOP:** no metric is computed, and Shyam labels more items blind (the scope is decided then, from counts only).
  - The bootstrap CI over queries (10,000, seed 20261020) is reported. The decision uses the point estimate.
- **Where they disagree:** audit items where Shyam and the consensus differ go to the referee step.
- **Intra-annotator recheck (optional, if time allows):** the first 30 items of the frozen prefix are re-labelled blind ≥ 7 days later. Reported only if done.

## 6. Merge and referee
- **A and B on the same side of the threshold → accepted.** The final grade is the lower of the two.
- **A and B on opposite sides → dispute.**
- **Shyam referees** the disputes plus the audit disagreements.
  - He sees the query, narrative, clause, and both judges' steps, evidence and notes, with no system information.
  - He may read the source document.
  - He records a grade + a reason code: `A_right | B_right | both_wrong | guideline_gap`.
- **qrels** = the accepted items + the referee decisions. Write-once, sha.

## 7. What gets reported
- Judge A vs judge B: binary κ, quadratic-weighted κ, raw agreement, 4×4 confusion matrix, CIs.
- The audit: N, κ vs the A/B consensus with CI, PASS/STOP, median seconds per item.
- Disputes: their count, reason codes, and how many flipped relevant ↔ not.
- The calibration and the practice-label lesson (§3).
- The sentence: "Relevance labels were produced by two LLM judges from different model families (Claude Opus 5.5, GPT-6 Astra) using a frozen guideline; threshold disagreements were adjudicated by the author, and a blind audit of N items by the author, made without AI assistance, gave κ = … against the LLM consensus. The author is not a regulatory specialist."
- All S8 system numbers are labelled provisional (GPT), because stage 1 used gpt-5-mini. No internship numbers.

## 8. Research gate
- **Guideline wording:** no research needed. The fixes come from calibration evidence in this project.
- **Stopping at N ≥ 100 with a fixed order:** no research needed. This is a standard pre-specified stopping rule with no peeking, and the κ margin is about ±0.15 at N = 100 (INFERENCE).
- **R21** (labelling evidence) already covers judge choice, anchoring, κ and effective n.
