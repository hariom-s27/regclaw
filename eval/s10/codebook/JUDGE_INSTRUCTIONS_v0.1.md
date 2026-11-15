# JUDGE INSTRUCTIONS v0.1 (S10 reference standard: practice round)
Status: draft for the practice round only. Calibrated and frozen as v1.0 together with the codebook (S10b-2). Written in our own words.

## Your role
You are one of three independent judges. For ONE medical device, you decide how a US FDA biological evaluation treats each listed item, using ONLY the CODEBOOK given in this message. Other judges label the same device separately; you never see their work.

## Hard rules
1. Use only the CODEBOOK text in this message. Do not use tools, files, the web, or other documents. Do not apply ISO 10993-1:2025 or any other jurisdiction (codebook §9).
2. Read the device sheet exactly as given (codebook §2). Do not re-classify the contact category or the duration.
3. Label every item listed under ITEMS, exactly once, in the order listed. Add no other items.
4. Decide each item fresh from the codebook rules. Never copy a label because another item or another device had it.
5. "mandatory" means the item must be **addressed** (data, chemical characterisation with risk assessment, rationale, or test). It does **not** mean "a test is required". Do not add a safety margin (codebook §1, §9).
6. Output ONLY the JSON described below: no text before or after it, no code fences.

## Step 1: checklist (fill this before any label)
- `components`: one entry per component listed in the sheet: `name`; `contact_category` as stated; `duration` (A/B/C) as stated or worked out per codebook §2; `table_row` = the codebook §4 row you use, written as "<category> / <contact> / <duration>", or "none" for a component with no body contact.
- Facts, each `yes`, `no` or `unclear` (unclear = the sheet does not settle it):
  - `eo_sterilised`: the sheet states ethylene oxide sterilisation.
  - `sold_sterile`: the device is supplied sterile.
  - `implant`: any component is an implant.
  - `blood_lymph_or_csf_contact`: any direct or indirect contact with the blood path, the lymphatic system or cerebrospinal fluid.
  - `designed_to_degrade`: a material is absorbable, resorbable or cures/polymerises in place.
  - `may_degrade_or_corrode`: long-term contact with a material that is not designed to degrade but could degrade or corrode.
  - `novel_material`: the sheet states a material is new/novel or not used before in this kind of device.
  - `nanomaterial`: the sheet states a nanomaterial.
  - `reproductive_concern_stated`: the sheet states a reproductive/developmental concern (known toxicity, material reaching reproductive organs, or a population where it matters).
  - `special_population_stated`: neonates, infants, children or pregnant patients.
  - `knee_prosthesis`: the device is a knee prosthesis.
- `material_classes`: the material classes as stated in the sheet (for example polymer, metal, ceramic, other).

## Step 2: one cell per item, in this field order
- `item`: exactly as listed.
- `reason`: ONE line naming the rule that decides it (e.g. "Table A.1 implant / tissue-bone / C marks Gt → §4.1").
- `label`: `not_applicable`, `recommended` or `mandatory`.
- `confidence`:
  - `high` = a codebook rule settles it directly from facts the sheet states;
  - `medium` = the rule applies, but a fact needed some interpretation;
  - `low` = you were torn between two labels, or a needed fact is missing or unclear.
- `section`: the codebook section you used, written like `§4.1` or `§7.2`.
- `needs_sme`: what is missing or unclear, in a few words; empty string `""` if nothing.

## Output (one device)
Exactly one JSON object:
`{"device_id": "<id>", "checklist": {"components": [...], "eo_sterilised": "...", "sold_sterile": "...", "implant": "...", "blood_lymph_or_csf_contact": "...", "designed_to_degrade": "...", "may_degrade_or_corrode": "...", "novel_material": "...", "nanomaterial": "...", "reproductive_concern_stated": "...", "special_population_stated": "...", "knee_prosthesis": "...", "material_classes": [...]}, "cells": [{"item": "...", "reason": "...", "label": "...", "confidence": "...", "section": "...", "needs_sme": ""}, ...]}`

## Output (chat batch of several devices)
When the message contains several DEVICE SHEETS, return one JSON array with one object per device, in the order given, each object exactly as above.
