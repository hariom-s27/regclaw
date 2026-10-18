# Entity annotation guideline v1 — S4b (2026-10-18)

Annotate the original clause **blind**, before seeing model predictions. Keep
the five type columns separate. A cell contains `mention => canonical; …`;
leave it empty when no entity of that type occurs. Mark a finished row
`complete` in notes, including rows with no entities. Add uncertainty to notes;
do not infer missing facts. Keep repeated-mention offsets in notes (`start:end`,
zero-based, end exclusive) when the intended occurrence would be ambiguous.
The scorer consumes reviewed typed spans with explicit offsets, rather than
guessing offsets from repeated text. Empty templates are not completed gold.

| Type | Include only explicitly named terms |
|---|---|
| biological_effects | Biocompatibility endpoints: cytotoxicity, sensitization, irritation, intracutaneous reactivity, material mediated pyrogenicity, systemic toxicity (acute/subacute/subchronic/chronic), implantation effects, hemocompatibility, genotoxicity, carcinogenicity, reproductive/developmental toxicity, degradation; `other` when no enum fits. |
| chemicals | Named substances, sterilants, residues, extractables or leachables. Generic “substances”, “chemical”, or “material” alone names no chemical. |
| device_types | A named device or contact category, not the bare word “device”. Preserve the whole identifying phrase; use a lowercase singular generic canonical. |
| regulatory_actors | manufacturer, authorised_representative, importer, distributor, notified_body, competent_authority, sponsor, investigator, applicant, laboratory, other. |
| lifecycle_phases | design_development, material_selection, manufacturing, sterilization, packaging, storage_transport, clinical_investigation, premarket_submission, conformity_assessment, post_market_surveillance, change_control, reprocessing, other. |

**Grounding.** Copy each mention character-for-character, retaining case and
spelling. Record only what is written, including a term appearing solely as an
example or exclusion. Evidence, if retained in notes, must be the shortest exact
substring (at most 25 words) containing the mention. A grounded phrase can still
have the wrong type: grounding alone does not establish correctness.

**Canonical.** For the three enum types, choose the closest schema enum;
use `other` if none fits. For chemicals and devices use a lowercase generic
name. Expand abbreviations only when defined in the clause, or in the prompt's
explicit list: EO/EtO = ethylene oxide; ECH = ethylene chlorohydrin;
DEHP = bis(2-ethylhexyl) phthalate. Otherwise repeat the mention, lowercase.
The frozen S4 normaliser subsequently applies spelling, safe aliases and
conservative singularisation; do not invent fuzzy merges. Enum underscores
become spaces for graph display. Keep one entity per (type, canonical) within
the clause, matching S4's deduplication rule; note multiple occurrences when
useful. A mention can have two types when both are justified.

**Exclude.** Standard numbers (ISO/IEC/ASTM/USP) belong to the frozen regex
extractor. The complete checker blocklist is: device, devices, material,
materials, test, tests, requirement, requirements, product, products. Do not
add endpoints, chemicals or test names from outside knowledge. Keywords are
outside these five gold columns.

**Examples — only the prompt's synthetic few-shots.**

- “should address cytotoxicity, sensitization, and irritation”: three biological
  effects with the same lowercase canonicals; “sponsor” is a regulatory actor.
- “the notified body shall verify this during conformity assessment”:
  `notified body => notified_body` (actor) and
  `conformity assessment => conformity_assessment` (lifecycle).
- “residual ethylene oxide (EO) in sterilized catheters”:
  `ethylene oxide => ethylene oxide` (chemical), `catheters => catheter`
  (device), and `sterilized => sterilization` (lifecycle).

After annotation, re-annotate ten clauses one week later without the first
answers. Report **intra-annotator**, not inter-annotator, agreement. Keep the
twenty holdout predictions sealed until the final evaluation. No accuracy
claim until the hand gold exists.
