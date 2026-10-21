# S8 Addendum 4 — Note 1: undefined ratios in runs that did not fail

**Date:** 2026-10-21. **Decided by:** Shyam (on Claude chat's recommendation), during E0 Part B.
**Committed:** with the E0 analysis code, before the merge and before any qrels or metric exists. Nothing new has been seen since Addendum 4.

## 1. 0/0 in a run that did not fail
- **Conditional R@10** is n/a for a query/run where no relevant clause is among the candidates (it is defined only "given at least one relevant candidate").
- **Expansion precision** is n/a for a query/run where stage 3 added no clause.
- n/a = left out of that metric's mean and counted in an `na` column.
- **Why:** a 0 would say "every added clause was irrelevant" when nothing was added. This extends the reason in Addendum 4 §3 (0/0 is undefined) from failed runs to every run. The older `metrics.py` convention (0 for undefined ratios) is not used for S8.

## 2. Expansion ΔR for a failed run
ΔR = R@final − R@10 = 0 − 0 = 0 for a system_failure (both terms are 0 under Addendum 3). It stays in the mean.

## 3. Conventions recorded for completeness (no change to any decision)
- **Filter-off systems** (vector_all, BM25, HyDE): candidate recall = 1 and reduction ratio = 0 by construction (no candidate filter); conditional R@10 = R@10. Tables mark these as "by definition", and they are not part of any comparison.
- **LIMIT repeats:** a failed repeat scores R@10 = 0 (Addendum 3); a replay_miss repeat is left out and counted.
- **LIMIT 25 upper bound:** relevant hits in the 20 ranked items + at most 5 unseen candidates (no assumption about how Neo4j orders LIMIT rows).
- **Paired t with zero spread:** p = 1 if the mean difference is 0, otherwise 0; CI = [mean, mean].
- **Obligation-filter rate:** over generations that succeeded; generation errors are counted separately.
