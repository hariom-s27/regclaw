# S8 pre-registration — Addendum 1: variant c, pool scope, metrics and statistics

Dated 2026-10-20. Written before any S8 eval query was run through any arm, the HyDE prompt
or the pre-labeller. It clarifies [S8_PREREGISTRATION.md](S8_PREREGISTRATION.md) (which is
not edited) and ADR [0028](../docs/decisions/0028-s8-prompt-v1-1-obligation-arm.md).
It adds no arm. Items a–d change no metric. Items e–g fix how the pre-registered metrics are
cut off and which secondary statistics are reported, before any result exists.

## a) Variant c (HyDE)

- An LLM writes ONE hypothetical clause per query from the variant-a text, with the frozen
  HyDE prompt (`s8_hyde_prompt.md`, freeze record `s8_hyde_freeze.json`), `gen_index` 0 only.
  The prompt is generic. It sees only the variant-a question: no narrative, no rephrase and
  no clause text.
- In the pipeline, stage 1 uses the variant-a Cypher of the same arm and generation, so
  variant c makes no new Cypher calls. Stage 2 ranks with the HyDE embedding instead of the
  query embedding.
- The filter-off HyDE system is an exact vector search over all clauses with the HyDE
  embedding.
- So the Text2Cypher generations per arm are 16 queries × variants a, b × 10 = 320, and
  640 over the two LLM arms (v1, v1.1). The earlier "a/b/c × 10" over-counted; this is
  corrected here, before any run.

## b) Pool scope

The pool contains every run Part E will score, at depth 20 where a ranked list exists:

- pipeline v1 / v1.1 / v1-noob × variants a, b, c: the final ids of a `top_k`=10 run (the
  scored run) AND of a `top_k`=20 run. Stage-3 caps mean a k=10 list is not always inside a
  k=20 list. "Final ids" means the stage-2 ranked list plus the stage-3 neighbours;
- vector_all (filter off; variants a, b), top 20;
- BM25 (variants a, b), top 20;
- HyDE filter-off (variant c), top 20;
- the LIMIT-experiment runs: v1, variant a, gen 0, injected LIMIT 5 / 10 / 25, every repeat
  (10 each), at `top_k` 10 and 20. A LIMIT run whose injected LIMIT makes the cached Cypher
  need a new generation is a "replay miss". It is counted and reported, never regenerated.

Only `gen_index` 0 runs enter the pool. Generations 1–9 are used for stability counts only.
A v1-noob query with no obligation predicate equals v1 (no extra run, no new pool clauses).

**v1-noob rewrite (precision of the ADR 0028 rule).** `c.obligation = $param` (variable `c`,
any parameter name, whitespace-insensitive) is replaced with `true` only when it is a
top-level AND conjunct of a WHERE. That means paren depth 0 relative to the WHERE, preceded
by WHERE or AND, followed by AND or the end of the WHERE, and no top-level OR / XOR / NOT in
that WHERE. Inside an OR, a NOT or any nested or bracketed expression, `true` would widen
the filter to everything. Those cases, and two or more obligation references or any other
obligation form, are "n/a" and logged.

## c) Labelling depth

If the pool is too large to label, the depth rule is decided at the C2 gate from pool
COUNTS only (never from content). It is logged and applied to all systems alike.

## d) Relevance labels

Relevance labels are produced by the protocol in Addendum 2 (Part D), written and committed
before any labelling. The gpt-5-mini pre-labeller is optional; if run, its output is never
used as a label.

## e) Metric cutoff

R@5, R@10, MRR and nDCG@10 are computed on the stage-2 RANKED list, before the stage-3
neighbours, so that all systems are compared at the same cutoff. The full returned set
(ranked + neighbours) is reported as a secondary "R@final". The expansion ΔRecall stays a
separate metric.

## f) Secondary statistics

The 3 primary comparisons are:

1. candidate recall: v1 vs v1-noob;
2. R@10: v1 vs v1.1;
3. R@10: v1 vs vector_all (filter off, variant a, `gen_index` 0). This is the "headline"
   comparison: what stage 1 adds over ranking all clauses.

The paired bootstrap of the pre-registration stays primary. For each of the 3 comparisons,
the report also gives:

- an exact paired sign-flip permutation p-value (full enumeration over the queries); and
- a paired t 95% CI.

Both are Holm-adjusted across these 3 comparisons. Evidence: Urbano, Lima & Hanjalic,
"Statistical Significance Testing in Information Retrieval: An Empirical Analysis of Type I,
Type II and Type III Errors", SIGIR 2019.

## g) Effective n for v1 vs v1-noob

The two arms differ only on queries whose v1 Cypher filters on obligation. For v1 vs v1-noob
the report gives k = the number of queries whose difference is non-zero, plus the per-query
values. The mean over those k queries is descriptive only.
