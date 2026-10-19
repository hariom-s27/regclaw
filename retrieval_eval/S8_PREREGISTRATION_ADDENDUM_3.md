# S8 pre-registration — Addendum 3: completion-token budget for the provisional GPT roles

Dated 2026-10-20. Written before any relevance label, pool, sheet or metric exists.
Addendum 2 is reserved for the Part D labelling protocol (Addendum 1, item d).

## Change

For the provisional Azure gpt-5-mini roles `s8_cypher` and `s8_hyde` only, the completion cap
is 4,000 tokens on the first attempt, with one retry at 8,000 (previously 800 and 1,600). The
cap includes hidden reasoning. It is applied in the Azure transport (`ROLE_BUDGET_SCALE` in
`llm/azure_openai.py`).

Nothing else changes:
- the prompts and their freeze shas, the schemas and the effort (low);
- the retry and stop guards, the validator and the 4-stage design;
- `s8_prelabel`, S4 and S10, whose requests are byte-identical (pinned by tests).

The change applies equally to all arms (v1, v1.1, and v1-noob, which is derived from v1) and to
all variants (a, b, c).

## Reason

Family c covered the first 39 of 640 slots and was stopped on purpose.
- 49% of first attempts (19 of 39) were truncated: the model spent the whole 800-token cap on
  hidden reasoning and returned no visible output.
- Two gen-0 slots failed terminally after the 1,600 retry: v1 Q4/a and v1 Q12/b.
- Under the stop guard, these failures would leave scored queries without a stage-1 result,
  for reasons unrelated to retrieval.
- Successful requests used at most 704 reasoning and 1,132 output tokens, so a 4,000 / 8,000
  cap leaves several-fold headroom.

## Terminal failures under the new budget

Any generation that still ends in a terminal failure under the new budget counts as a system
failure: an empty stage-1 result (recall 0, no candidates) for that slot and its derived runs.
Such slots are reported with their count, and are never excluded, regenerated or given a
fallback. The stop guard is unchanged.

For a gen-0 slot, the derived runs are:
- the same arm's variant-c run (stage 1 = the variant-a Cypher);
- v1-noob for that query and variant;
- for v1 variant a, the LIMIT-experiment runs.

## Runs

- A new run family `c2`. The Cypher and HyDE cache keys do not contain the budget, so no
  family-c generation is reused.
- Budgets: ≤ 900 Cypher requests including retries, ≤ 16 HyDE, ≤ 60 embeddings.
- Family c (39 slots; 69 Cypher, 16 HyDE and 48 embedding requests) is kept unchanged as
  evidence and reported as a finding about the provisional substitute. It never enters the
  pool or a metric.
- From family c, only counts were inspected: no candidate content, ranking, pool or relevance
  information.
