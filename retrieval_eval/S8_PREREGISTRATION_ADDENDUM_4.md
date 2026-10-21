# S8 Pre-registration — Addendum 4: analysis details fixed before the qrels freeze

**Date:** 2026-10-21. **Decided by:** Shyam (on Claude chat's recommendations), during Part E0 planning.
**Committed:** before the merge and before any qrels or metric exists.
**Disclosure:** at this point only per-judge aggregate grade counts per query had been seen (from the validator); no merged label, qrels file, run list or metric had been seen.
**Builds on:** S8_PREREGISTRATION.md and Addenda 1–3 (and Addendum 2 notes 1–3). Where this addendum is more specific, it wins.

## 1. Swap rule at analysis time (DC1)
- The swap rule (queries.yaml `common_rules`) was meant to replace a query whose pool holds no grade-3 clause with an answerable one. After pooling and labelling, no replacement is possible: there are no reserve queries, and decision rule 3 forbids further prompt evaluation on these queries.
- **Rule:** such a query is **kept** in all analyses and flagged "swap-eligible (no replacement possible)". The pre-registered zero-relevant rule (no clause with grade ≥ 2) still removes unanswerable queries from relevance means.
- **Sensitivity:** every primary comparison is also reported with the swap-eligible queries removed (one extra row).
- **Why:** dropping them would shrink the already small query set and keep only the easier queries; the zero-relevant rule already handles truly unanswerable ones.

## 2. Which list each metric uses (DC2)
- R@5, R@10, MRR and nDCG@10 use the stage-2 ranked list of the k = 10 pipeline run (Addendum 1 e); R@5 = its first 5. Filter-off systems (vector_all, BM25, HyDE) are cut to 10.
- **MRR is MRR@10** for every system (same cutoff).
- R@final (ranked + neighbours) is reported for pipeline arms only; it is n/a for filter-off systems, which have no stage 3.

## 3. Terminal failures and undefined ratios (DC3)
- A system_failure scores 0 on candidate recall, R@k, MRR, nDCG and R@final (Addendum 3).
- Conditional R@10, expansion precision and reduction ratio are **n/a** for a failed run (left out of that metric's mean and counted), because 0/0 is undefined and a reduction ratio of 1.0 would reward failure.
- Failure counts are shown in every table.

## 4. Zero-relevant queries (DC4)
- They are excluded from the means of every relevance metric: candidate recall, R@k, conditional R@10, MRR, nDCG, R@final, expansion ΔR and expansion precision. Reduction ratio and judged@k cover all queries.
- Paired tests run on the analysed set only.
- "Reported separately" = per system: candidate count, number of top-10 items with grade 1 and grade 0, and the highest grade retrieved.

## 5. judged@k (DC5)
- judged@k = |top-k ∩ judged| / min(k, list length), for k = 5, 10 and the final list.
- It must be 1.0 for every scored run (all fed the pool at depth 20). A value below 1 stops the analysis as a join error.

## 6. n/a and replay_miss (DC6)
- A v1-noob "n/a" query is dropped from comparison 1 only, and listed.
- A replay_miss on a scored gen-0 non-LIMIT run stops the analysis.

## 7. LIMIT experiment (DC7)
- For LIMIT 5 and 10, the candidate set is read from the k = 20 ranked list (it holds every candidate when fewer than 20). For LIMIT 25, candidate recall is reported as lower/upper bounds.
- ΔR@10 = LIMIT run (k = 10) minus v1 variant-a gen-0 (k = 10), averaged over repeats per query. Repeat identity = number of distinct ranked and final lists per (query, LIMIT, k).
- Presented as a dose-response description, not a significance test.

## 8. Descriptive analyses not in the earlier documents (DC8)
These are added here and are **descriptive only**:
- the LIMIT dose-response metrics in §7;
- candidate-set Jaccard over gens 0–9;
- judged@k;
- the terminology gap for vector_all and BM25;
- expansion precision.

## 9. Stability over gens 0–9 (DC9)
Obligation-filter rate, candidate count min/max, the number of distinct candidate sets, and mean/min pairwise Jaccard (45 pairs). **No R@10 spread**: gens 1–9 have no ranked lists (Addendum 1 b).

## 10. Holm (DC10)
Holm adjusts the exact sign-flip permutation p-values and the paired-t p-values over the 3 primary comparisons. The paired-t CI is reported at 95% and at the Holm step level 1 − 0.05/(m − j + 1), with steps ordered by the t p-values.

## 11. Corpus size (DC11)
N for the reduction ratio = the number of clauses in data/clauses.json (sha recorded). A mismatch with an expected count stops the analysis.

## 12. qrels manifest (DC12)
retrieval_eval/s8_qrels_manifest.json (committed, write-once) records sha256 of:
- the audit freeze;
- the judge freezes of the judges read;
- the agreement file that matches the merge mode;
- audit_check.json, qrels_meta.json, qrels.tsv;
- the guideline in force and s8_partD_freeze.json;
- the merge mode.

runs.jsonl is not in the manifest (it opens only after the qrels freeze). The analysis records its sha.

## 13. Terminology gap (DC13)
- b−a and c−a for R@10 and nDCG@10: v1 is the main row; v1.1 and v1-noob are listed.
- Candidate recall b−a only, because variant c reuses variant a's candidates.
- vector_all: b−a, and c−a = HyDE(c) − vector_all(a). BM25: b−a only.

All outputs carry the label "provisional (GPT): stage 1 = Azure gpt-5-mini". No internship numbers.
