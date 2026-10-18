---
status: accepted (provisional)
date: 2026-10-18
conflict-id: none (versioned amendment to the S4 freeze)
evidence: dev split of the S4b gold only; see docs/results.md
---
# 0027: Normaliser v1.1 — a versioned amendment to the S4 freeze

## Context and Problem Statement

The S4 live run preregistered a freeze before its first call: the system prompt,
`ner/schema.json`, the twelve response checks, `ner/aliases.csv`, the regex
standard-reference rules, the parser, the normaliser and the eval ids. The S4b
adjudicated gold (50 clauses; 30 dev, 20 holdout) then scored the provisional
(GPT) runs on the dev split.

All dev evidence for a normaliser change is three device-type FP/FN pairs that
differ only in number, all in one clause: `guidewires`/`guidewire`,
`vena cava filters`/`vena cava filter` and `heart valves`/`heart valve`. The v1.0
normaliser singularises only six listed words (`SINGULARS`). No lifecycle-phase
pair occurred. Changing the normaliser after scoring dev risks tuning to the
evaluation unless the change is versioned, limited to dev evidence and
re-frozen before the holdout is scored.

## Decision Drivers

- Remove a mechanical error class (number variants) without touching the
  frozen prompt, schema, checks, aliases or model outputs.
- Keep v1.0 exactly reproducible for comparison.
- Use dev evidence only; keep holdout predictions and scores sealed.
- Re-freeze everything that the final holdout score depends on.

## Considered Options

- **A:** Keep v1.0 and report plural errors as a known limitation.
- **B:** Add the three dev words to `SINGULARS` (a list fitted to dev).
- **C:** A general, conservative last-word rule, versioned as v1.1, chosen
  without reference to which dev words it fixes, then re-frozen.

## Decision Outcome

Choose **C**. `NORMALISER_VERSION = "1.1"`; `Normaliser(version="1.0")` keeps
the S4 behaviour byte-for-byte. For DeviceType and LifecyclePhase only, after the
unchanged `SINGULARS` map, v1.1 singularises the last word of the canonical:

1. Exceptions `SINGULARS_V11`: prostheses→prosthesis, analyses→analysis,
   diagnoses→diagnosis, lenses→lens.
2. Protected, never changed: words ending -ss, -us, -is, -ics; series, species,
   apparatus, lens, mass, glass, gas, diagnosis, analysis, themselves.
3. Acronym guard: when the raw last word of the mention is upper case, with an
   optional lowercase plural `s` (`SCVs`), only that `s` is removed; an all-caps
   word never changes (`CEMS` stays `cems`).
4. -ies→-y, except for words of four letters or fewer (`dies`).
5. -sses, -xes, -ches, -shes drop `es`.
6. Any other -s (including -ses, -zes) drops `s` (`cases`→`case`, `sizes`→`size`).

Chemicals are never singularised. The version is recorded on every `ner.json`
row, in `run_summary.json` and in `enriched.provenance.json`. The scorer takes
`--normaliser-version` so a v1.0 run is never rescored with v1.1.

The rule was revised three times on dev and non-eval corpus evidence before the
re-freeze, never on holdout output:

- **rev1:** dropped `es` from every -ses/-zes word, giving `prostheses`→`prosthes`,
  `cems`→`cem` and `themselves`→`themselve`.
- **rev2:** -ses/-zes drop only `s`, plus the exceptions, `themselves` protected
  and the acronym guard; this gave `lenses`→`lense`.
- **rev3 (frozen):** adds `lenses`→`lens`. The all-caps heading guard is accepted
  as specified: one eu-mdr heading `VARIANTS AND ACCESSORIES` stays plural, and no
  separate rule for headings of five or more letters was added.

Rev1/rev2 outputs are kept as superseded drafts and not used. Rebuilding is
cache-only (`ner/reclean.py`, 0 model calls) into new run folders; the S4 run
folders are unchanged. `goldset/freeze_manifest.json` re-freezes the code, eval
inputs and model outputs before any holdout scoring. The final holdout uses v1.1
rev3, and `--final-holdout` verifies every manifest sha and the version first.

### Holdout exposure and sensitivity plan

Holdout model predictions and scores were not inspected. Holdout gold was authored
by the annotators (Shyam and a Claude chat adjudication). Disclosed agent exposures,
neither of which informed any rule (normaliser frozen at rev3 before both):

1. A 600-character print showing part of one holdout clause's text.
2. One adjudication-log row for one holdout clause (`eu-mdr:Annex XV.II.2.2`,
   a lifecycle_phases gold label).

The seal protects holdout predictions and scores against tuning; model outputs
are fixed in the cache and the rule was frozen first, so neither exposure can
be tuned on. The final report gives the primary 20-clause score plus a 19-clause
sensitivity score excluding that clause. The exclusion is listed in the manifest
(`sensitivity_exclusion`); `goldset/entity_gold_ids.csv` is unchanged. Later reads of gold and
model outputs go through a helper that drops holdout ids before printing.

## Consequences

All numbers are **provisional (GPT), dev split**.

- Low-effort dev micro canonical F1 rises **0.693 → 0.723**; the paired
  v1.1 − v1.0 difference is **+0.030, CI95 [0.000, 0.080]**. The gain is exactly
  the three dev plural pairs; rev1, rev2 and rev3 give identical dev scores.
- Corpus DeviceType unique canonicals **702 → 689**; singleton share
  **0.856 → 0.848**; 13 merges. Lifecycle phases (13) and chemicals (183) are unchanged.
- Keyword side effect: four non-holdout clauses regain a plural keyword that
  equalled a v1.0 canonical and so had been filtered out. Keywords are not in the graph.
- DeviceType noise remains: the frozen prompt still yields long descriptive
  phrases as device types. Singularisation does not address this; it is a known limitation.
- The graph entity layer was replaced (927 nodes and 4,143 MENTIONS deleted in
  one checked transaction, then reloaded from v1.1). The v1.0 layer is
  restorable from `norm_v1.1_rev3/graph_backup/` or from the unchanged S4 enriched file.
- The two holdout exposures above are disclosed; the final report carries the
  19-clause sensitivity score next to the primary score.

## Confirmation

```powershell
python tasks.py test
# Cache-only rebuild (aborts before any live call; refuses non-empty folders):
python -m ner.reclean --cache data/runs/azure_openai/s4/ner_cache.json --ids-from data/runs/azure_openai/s4/phase2/ner.json --output-dir data/runs/azure_openai/s4/norm_v1.1_rev3 --effort low
python -m ner.reclean --cache data/runs/azure_openai/s4/high/ner_cache.json --ids-from data/runs/azure_openai/s4/high/ner.json --output-dir data/runs/azure_openai/s4/norm_v1.1_rev3_high --effort high
# Dev scores, v1.1 with the paired diff against v1.0 low:
python -m goldset.score_entities s4b --completed-hand-gold --normaliser-version 1.1 --low data/runs/azure_openai/s4/norm_v1.1_rev3/ner.json data/runs/azure_openai/s4/ner_cache.json data/runs/azure_openai/s4/phase2/call_ledger.json --high data/runs/azure_openai/s4/norm_v1.1_rev3_high/ner.json data/runs/azure_openai/s4/high/ner_cache.json data/runs/azure_openai/s4/call_ledger.json --baseline-low data/runs/azure_openai/s4/phase2/ner.json --baseline-version 1.0 --output data/runs/azure_openai/s4/norm_v1.1_rev3/s4b_scores_dev_v1.1.json
# Merge and graph:
python tasks.py enrich --ner data/runs/azure_openai/s4/norm_v1.1_rev3/ner.json --output data/runs/azure_openai/s4/norm_v1.1_rev3/enriched.json
python tasks.py graph --load --input data/runs/azure_openai/s4/norm_v1.1_rev3/enriched.json --checks --verify-idempotent
# Final holdout, once only, later: the exact command is stored in goldset/freeze_manifest.json.
```
