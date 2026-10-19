---
status: accepted
date: 2026-10-19
conflict-id: none (pre-registered secondary arm; v1 headline unchanged)
evidence: B2 smoke on a non-eval query; B3 sanity check on three non-eval queries
---
# 0028: S8 pre-registered Text2Cypher prompt v1.1 (obligation-filter arm)

## Context and Problem Statement

The S8 Text2Cypher prompt v1.0 was frozen on 2026-10-18. In the B2 smoke on a non-eval
query ("cytotoxicity test requirements for wound dressings"), v1 added
`c.obligation = $ob` with SHALL. The user had not asked for mandatory-only content;
"requirements" is an everyday word. The corpus mixes binding regulations (mostly "shall")
and guidance (mostly "should"), so such a filter can drop much of the relevant guidance.
In that smoke a stage-3 neighbour outscored every stage-1 candidate.

Changing the frozen prompt after seeing this would make the headline differ from the
frozen design. Ignoring it would leave a known failure mode unmeasured. No S8 eval query
had been run through any arm at the time of this decision.

## Decision Drivers

- Keep v1 byte-identical and faithful to the frozen design.
- Measure the obligation filter fairly. Any arm's results must be in the judging pool
  before labelling, or its unique retrievals go unjudged.
- Avoid tuning to the 16 eval queries: state the change as a general principle and fix
  it before any eval result exists.
- Separate the filter's own effect from other prompt effects.

## Considered Options

- **A:** Keep v1 and only measure the obligation flag in Part E.
- **B:** Replace v1 with v1.1 as the headline.
- **C:** Drop obligation filtering from stage 1 entirely.
- **D:** Keep v1 as the headline and add pre-registered ablation arms to the pool.

## Decision Outcome

Choose **D**:

1. **v1** (prompt 1.0) stays the headline and stays frozen (`retrieval_eval/s8_prompt_freeze.json`).
2. **v1.1** (prompt 1.1) is the pre-registered secondary arm, frozen in
   `retrieval_eval/s8_prompt_freeze_v1_1.json`. It is v1.0 plus exactly two additions:
   - HARD RULE 8: filter on obligation only when the question explicitly restricts the
     strength of the provision.
   - Few-shot 6: "irritation testing requirements for surgical gloves", with no
     obligation filter.
3. **v1-noob** is a pre-registered, LLM-free ablation built in S8 Part C. In v1's cached
   Cypher, a single `c.obligation = $param` predicate is replaced with `true`, then the
   unchanged validator and EXPLAIN run. With no predicate it equals v1. If the rewrite is
   ambiguous, the query is "n/a" and logged.
4. The pool is the union of the top-20 from six systems: pipeline v1, pipeline v1.1,
   pipeline v1-noob, vector_all, BM25 and HyDE. The labelling sheet stays blind.
5. v1 vs v1-noob measures the filter alone. v1 vs v1.1 measures the whole prompt change.
   The B3 sanity check showed that v1.1 also changes the model's search terms, so
   v1-vs-v1.1 differences are not attributed to the filter alone.
6. No further prompt version is evaluated on these 16 queries.

Why not the others: **A** cannot judge the clauses an unfiltered arm would retrieve.
**B** and **C** change the frozen headline design after a result was seen.

Implementation: `PROMPT_VERSIONS` in `retrieval/hybrid_retriever.py`. The default
everywhere is "1.0". v1.1 is derived from the v1.0 objects in code. `prompt_version` is
recorded on cache rows, in the RunLog and on the `cypher.generate` span. It enters the
cache key only for versions other than 1.0, so v1.0 keys stay byte-identical and B2
caches still replay; the versions also differ by template, few-shot and rendered-prompt
shas. The pre-registration is in `retrieval_eval/S8_PREREGISTRATION.md`.

## Consequences

- One more Text2Cypher arm (v1.1) and one derived arm (v1-noob) in Part C. The pool grows
  from 4 to 6 systems, so there are more clauses to label.
- Part C costs ≈ $0.7 estimated (480 v1.1 generations × ≈ $0.0014, illustrative rates).
  v1-noob makes no LLM calls.
- With 16 queries, the arm comparisons have wide intervals and are reported as ablations,
  not as replacements for the headline.
- The B3 sanity check (non-eval queries only) confirmed the rule as written: no filter for
  "requirements" or "must" questions, and SHALL for a "mandatory" question. It is not
  evidence that v1.1 retrieves better.

## Confirmation

```powershell
.venv\Scripts\python tasks.py test   # v1 and v1.1 freeze tests, version and cache-key tests
.venv\Scripts\python retrieval\hybrid_retriever.py "<non-eval question>" --prompt-version 1.1 --run-family <family>
```
