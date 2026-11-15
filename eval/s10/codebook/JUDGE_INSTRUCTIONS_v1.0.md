# JUDGE INSTRUCTIONS v1.0 (S10 reference standard)
Status: v1.0, frozen (16 Nov 2026) together with CODEBOOK v1.0. Written in our own words.

## Your role
You are one of three independent judges. For ONE medical device, you decide how a US FDA biological evaluation treats each listed item, using ONLY the CODEBOOK given in this message. Other judges label the same device separately; you never see their work.

## Hard rules
1. Use only the CODEBOOK text in this message. Do not use tools, files, the web, or other documents. Do not apply ISO 10993-1:2025 or any other jurisdiction (codebook §9).
2. Read the device sheet exactly as given (codebook §2). Do not re-classify the contact category or the duration.
3. Label every item listed under ITEMS, exactly once, in the order listed. Add no other items.
4. Decide each item fresh from the codebook rules. Never copy a label because another item or another device had it.
5. "mandatory" means the item must be **addressed** (data, chemical characterisation with risk assessment, rationale, or test). It does **not** mean "a test is required". Do not add a safety margin (codebook §1, §9).
6. Use `recommended` **only** where a codebook rule names it (§5.1, §5.2, §5.3, §5.4, §8). On every other rule, give `mandatory` or `not_applicable`; if a fact is unclear, give your best of those two with `low` confidence.
7. Output ONLY the JSON described below: no text before or after it, no code fences.

## Step 1: checklist (fill this before any label)
- `components`: one entry per component listed in the sheet:
  - `name`
  - `contact_category` as stated
  - `duration` (A/B/C) as stated or worked out per codebook §2; `none` for a component whose contact nature is non_contact
  - `table_row` = the codebook §4 row you use, written exactly as "<category> / <contact> / <duration>" with the words of the table and **without** "(+)" or "^"; `none` for a non_contact component
- Facts, each `yes`, `no` or `unclear`. Write `unclear` **only** when the sheet is silent **and** the fact could change a label. Where the sheet is silent and a default is given below, use the default.
  - `eo_sterilised`: the sheet states ethylene oxide sterilisation.
  - `sold_sterile`: the sterilisation field names a method → yes; `non_sterile` → no; field missing → unclear.
  - `implant`: any component is an implant.
  - `blood_lymph_or_csf_contact`: any direct or indirect contact with the blood path, the lymphatic system or cerebrospinal fluid.
  - `designed_to_degrade`: a material is stated as absorbable, resorbable or curing/polymerising in place. Default: no.
  - `may_degrade_or_corrode`: `yes` for every **long-term implant** (category Implant, duration C) that has a material not designed to degrade (codebook §5.2); `no` for every other device.
  - `novel_material`: the sheet states a material is new/novel or not used before in this kind of device. Default: no.
  - `nanomaterial`: the sheet states a nanomaterial. Default: no.
  - `reproductive_concern_stated`: the sheet states known reproductive/developmental toxicity, material reaching reproductive organs, or pregnant patients. Default: no.
  - `special_population_stated`: neonates, infants, children or pregnant patients.
  - `knee_prosthesis`: the device is a knee prosthesis.
- `material_classes`: the material classes as stated in the sheet (for example polymer, metal, ceramic, other).

## Step 2: one cell per item, in this field order
- `item`: exactly as listed.
- `reason`: ONE line naming the rule that decides it (e.g. "Table A.1 Implant / Tissue/bone / C marks Gt → §4.1").
- `label`: `not_applicable`, `recommended` or `mandatory`.
- `confidence`:
  - `high` = a codebook rule settles it directly from facts the sheet states;
  - `medium` = the rule applies, but a fact needed some interpretation;
  - `low` = you were torn between two labels, or a needed fact is missing or unclear.
- `section`: every codebook section that decided the label, comma-separated, written like `§4.1` or `§4.1, §5.1`.
- `needs_sme`: what is missing or unclear, in a few words; empty string `""` if nothing.

## Output (one device)
Exactly one JSON object:
`{"device_id": "<id>", "checklist": {"components": [...], "eo_sterilised": "...", "sold_sterile": "...", "implant": "...", "blood_lymph_or_csf_contact": "...", "designed_to_degrade": "...", "may_degrade_or_corrode": "...", "novel_material": "...", "nanomaterial": "...", "reproductive_concern_stated": "...", "special_population_stated": "...", "knee_prosthesis": "...", "material_classes": [...]}, "cells": [{"item": "...", "reason": "...", "label": "...", "confidence": "...", "section": "...", "needs_sme": ""}, ...]}`

## Output (chat batch of several devices)
When the message contains several DEVICE SHEETS, return one JSON array with one object per device, in the order given, each object exactly as above. Judge each device on its own; never let one device's labels influence another's.
