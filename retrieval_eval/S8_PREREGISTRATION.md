# S8 pre-registration: Text2Cypher prompt arms

Fixed on 2026-10-19, before any S8 evaluation query was run through any arm.
See ADR [0028](../docs/decisions/0028-s8-prompt-v1-1-obligation-arm.md).

## Arms

| Arm | Definition | Frozen | Status |
|---|---|---|---|
| **v1** (headline) | Text2Cypher prompt v1.0 | 2026-10-18 (`s8_prompt_freeze.json`) | headline system |
| **v1.1** | v1.0 + one rule line + one few-shot | 2026-10-19 (`s8_prompt_freeze_v1_1.json`) | pre-registered secondary arm |
| **v1-noob** | v1's cached Cypher with the obligation predicate replaced by `true` (no LLM) | defined 2026-10-19; built in S8 Part C | pre-registered ablation |

**Why v1.1 exists.** In a smoke test on a non-eval query, v1 added an obligation = SHALL
filter for a question that did not ask for mandatory-only content ("requirements" is an
everyday word). The corpus mixes binding regulations (mostly "shall") and guidance
(mostly "should"), so such a filter can drop relevant guidance clauses. v1.1 states a
general principle, not one tuned to the eval queries: filter on obligation only when the
user explicitly restricts the strength of the provision ("mandatory", "binding",
"legally required" → SHALL; "recommended", "non-binding" → SHOULD; "optional" → MAY).
Question words ("must", "should", "need to") and nouns ("requirements", "tests", "rules",
"guidance") alone are not a restriction, because a user's "must" does not map to the
document's modal verb. The new few-shot is a non-eval question with no obligation filter.

**v1-noob rule.** For each query / variant / generation, take the cached v1 Cypher.
- If it contains an obligation predicate of the form `c.obligation = $param`, replace
  exactly that predicate with `true`, then run the unchanged validator and the read-only
  EXPLAIN.
- If there is no obligation predicate, the arm equals v1 (no extra run, no new pool
  clauses).
- If the rewrite is ambiguous (any other obligation form, or more than one predicate),
  that query is "n/a" for this arm and is logged.

No LLM call is made for this arm.

**Disclosure.** The eval query texts were written by the same people who wrote the v1.1
rule. For that reason the rule is stated as a general principle, and all three arms are
fixed before any S8 eval result exists. A B3 sanity check on three non-eval questions only
confirmed that the rule behaves as written. It also showed that v1.1 changes the model's
choice of search terms, not only the obligation filter.

While writing the rule, the authors knew that some eval queries contain 'must'. The 'must'
clause rests on the general principle above; its effect is measured, not assumed.

BM25 pools for an earlier draft of the queries were built during prep (15 Oct) and have
never been opened.

## Part C run plan

- v1 and v1.1 run the same generations: 16 queries × variants a/b/c, 10 generations each.
  `gen_index` 0 is the scored one. v1-noob is derived from the v1 generations.
- The judging pool is the union of the top-20 from 6 systems: pipeline v1, pipeline v1.1,
  pipeline v1-noob, vector_all (filter off), BM25 and HyDE.
- The labelling sheet stays blind: no system or version columns.

## Analysis plan (Part E)

**Primary analysis.** Variant a, `gen_index` 0. Primary metrics:
- candidate recall for v1 vs v1-noob, because the filter acts at stage 1;
- R@10 for v1 vs v1.1, and for the headline (v1).

All other metrics and comparisons are secondary.

**Statistics.** Paired bootstrap over queries: 10,000 resamples, seed 20261019, 95%
percentile intervals. Report each difference with its interval.

**Query handling.**
- The queries.yaml swap rule applies first.
- Queries with zero relevant (grade ≥ 2) clauses after labelling, including Q7 and Q12 if
  that happens, are excluded from the means of all relevance metrics (recall, R@k, MRR,
  nDCG) and reported separately.
- R1–R3 are included in the main means and also shown separately.

**Variants b and c** are used only for the terminology-gap analysis.

**What each comparison means.**
- **v1 vs v1-noob** = the effect of the obligation filter alone.
- **v1 vs v1.1** = the effect of the whole prompt change (rule + few-shot + term choice).
  Differences are not attributed to the filter alone, because v1.1 also changes search
  terms.

**Secondary reporting.**
- Per query and arm, report the obligation_filter flag and its stability over the 10
  generations.
- For each arm, report candidate recall, R@5, R@10, conditional R@10, MRR and nDCG@10.
- Results are split by "v1 filtered on obligation: yes / no".
- With 16 queries the intervals will be wide; the report says so.

## Decision rules (fixed now)

1. v1 stays the headline whatever the result.
2. v1.1 and v1-noob are reported as pre-registered ablations.
3. No further prompt version is evaluated on these 16 queries. A v1.2 needs new queries.
