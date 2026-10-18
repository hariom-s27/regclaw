# Entity annotation guideline v1.1 — S4b (2026-10-18)

v1.1 keeps all of v1 and adds the decisions agreed in round 1 calibration (D1–D13). If v1 and v1.1 conflict, v1.1 wins.

## Unchanged from v1 (summary)
- **Blind:** annotate before seeing model predictions. Holdout predictions stay sealed until the final evaluation.
- **Cell format:** `mention => canonical; …`. Notes say `complete`. Offsets are optional (`start:end`, zero-based, end exclusive).
- **Grounding:** the mention is copied character-for-character from the clause. Only what is written counts, including examples and exclusions.
- **Abbreviations:** expand only when the clause defines them, or for the explicit list:
  - EO/EtO = ethylene oxide
  - ECH = ethylene chlorohydrin
  - DEHP = bis(2-ethylhexyl) phthalate
- **Enum types:** biological_effects, regulatory_actors and lifecycle_phases use the closest enum, or `other`.
- **Deduplication:** one entity per (type, canonical) per clause.
- **Exclude:**
  - standard numbers (regex handles them);
  - the blocklist: device(s), material(s), test(s), requirement(s), product(s);
  - anything from outside knowledge.

## New rules (v1.1)

### device_types
- **D1:** include **contact categories** (e.g. surface device, external communicating device, direct/indirect/transient/non-contact, and named body-contact categories such as intact skin or circulating blood).
- **D2:** include **medical-device kinds only**. Exclude non-medical equipment: emission monitors, analysers, sterilizers, chambers, gas cylinders, meters.
- **D3:** the mention may be the whole identifying phrase, but the **canonical is the short generic head**, lowercase and singular. Example: "IVD medical devices for use by lay users" → `ivd medical device`. Purely descriptive phrases ("devices using X") are not entities.
  - The long form "in vitro diagnostic medical device" keeps its own canonical. Merging it with `ivd medical device` is the alias table's job, not the annotator's.
- **D4:** stand-alone software counts as a device kind (`stand-alone software`). A database does not.
- **D11:** **regulatory status, class or role is not a device kind.** Exclude investigational device, predicate device, licensed device, Class C/D device, and "devices labelled as sterile".

### biological_effects
- **D9:** a named generic effect with no specific enum (e.g. "Toxicity") → `other`.
- Umbrella properties ("biocompatibility") and non-biocompatibility effects ("infection") are not entities.

### regulatory_actors
- **D10:** officials acting for a regulator (e.g. a medical device officer, a controlling officer) → `competent_authority`.
- **D12:** users, lay users, patients and subjects are **not** regulatory actors.
- Other named regulatory roles without an enum → `other`: ethics committee, the Commission, economic operators, conformity assessment body, registration holders, national research councils.
- **D13:** "submitter" → `applicant`.

### lifecycle_phases
- **D5:** `other` only for a **named stage of the device's life** that has no enum: use / clinical use, installation, maintenance, servicing, labelling, clinical evaluation.
  - Not commercial acts: marketed, sell, making available on the market.
  - Not the lifecycle of non-medical equipment (monitor certification, entry into service).
- **D6:** an application to run a clinical investigation is **not** `premarket_submission`; it is covered by `clinical_investigation`. An application for a licence to manufacture or sell **is** `premarket_submission`.
- **D7:** "design" counts only when it is an activity or stage, not when it is listed as a device characteristic.
- **D8:** a material property such as "processing sensitivity" is **not** `manufacturing`.
- Sub-steps of a phase already labelled (e.g. aeration within sterilization) add nothing.
- A pre-licence site inspection, or demonstrating conformity with essential principles → `conformity_assessment`.
- An IVD clinical performance evaluation → `clinical_investigation`.

## Agreement protocol (v1 + round 1)
- **Inter-annotator** (Shyam vs Claude, blind, pre-calibration): reported in the round-1 agreement note.
- **Intra-annotator:** Shyam re-labels 10 clauses one week later (from 25 Oct) without seeing the first answers, as v1 requires.
- **Final gold:** both label sets adjudicated under v1.1. Every change is in the adjudication log.
