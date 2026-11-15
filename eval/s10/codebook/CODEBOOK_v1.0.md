# S10 LABELLING CODEBOOK v1.0
Status: v1.0, frozen (16 Nov 2026), calibrated on the practice round (S10b-2). Its LF sha256 is recorded in eval/s10/manifests/codebook_v1.json. Written in our own words; it quotes no standard or guidance text.
Readers: the three judges and Hariom (audit + referee). Everyone labels with this document only.

---

## 0. Basis (read first)
- **Framework:** US FDA guidance "Use of International Standard ISO 10993-1, Biological evaluation of medical devices – Part 1" (final, September 2023), and its **Attachment A, Table A.1**, which is built on ISO 10993-1:2018.
- **Do NOT use ISO 10993-1:2025**, even though it exists. See §9 (do-not-apply list).
- **Table check:** the Table A.1 grid in §4 was copied by hand from the FDA PDF (14 Nov 2026) and matched an independent electronic copy in all 264 cells.
- **Pinned editions** (for reference only; labels do not depend on editions): ISO 10993-1 (framework), -3:2026, -4:2017+A1:2025, -5:2009, -6:2026, -7:2026, -10:2021, -11:2017, -12:2021+A1:2025, -16:2017, -17:2023+A1:2025, -18:2020+A1:2022, -23:2021+A1:2025, ISO/TS 10993-19:2020, ISO/TS 10993-20:2006, ISO 11737-3:2023. Parts -9, -13, -14, -15: current editions (not needed for labelling).

## 1. What you label
- **One cell = one device profile × one item.** You give each cell one label.
- **The items (21):** ISO 10993-1, -3, -4, -5, -6, -7, -9, -10, -11, -12, -13, -14, -15, -16, -17, -18, -23, ISO/TS 10993-19, ISO/TS 10993-20, ISO 11737-3, and **fda-knee** (only on profiles that are knee prostheses).
- **Labels (from low to high):**
  - **not_applicable:** not indicated for this contact and duration, and no trigger in §5–§8 applies.
  - **recommended:** consider it. **Use it only where a rule names it: §5.1, §5.2, §5.3, §5.4 and §8.** Every other rule gives `mandatory` or `not_applicable`, never `recommended`.
  - **mandatory:** must be **addressed** in the biological evaluation. Addressed means by existing data, chemical characterisation with a toxicological risk assessment, a written rationale, **or** a test. **Mandatory does not mean "a test is required".**
- **Unclear facts on a two-way rule:** give your best `mandatory` / `not_applicable` label with **low** confidence. Never use `recommended` as a middle answer.

## 2. Read the profile as given
- Take the profile's **contact category**, **duration**, materials, sterilisation, population and components **exactly as stated**. Do not re-classify the device.
- **Duration codes:** A = limited (up to 24 h) · B = prolonged (more than 24 h, up to 30 days) · C = long-term (more than 30 days).
- **Repeated use:** the profile states the cumulative duration; use it.
- **Several components with different contact:** work out each component's row, then give each item the **highest** label any component gets.
- **Non-contact component:** a component whose contact nature is `non_contact` has no body contact, whatever contact site or exposure the sheet lists next to it. Ignore it for every item. In the checklist, write its duration `none` and its table row `none`.
- **Missing information:** do not guess silently. Give your best label, set confidence to low, and add a short note (`needs_sme: <what is missing>`).

## 3. Endpoint → item map (fixed)
A biological item takes the **highest** label among its endpoints.

| Endpoint (column in Table A.1) | Item |
|---|---|
| Cytotoxicity | ISO 10993-5 |
| Sensitisation | ISO 10993-10 |
| Irritation / intracutaneous reactivity | ISO 10993-23 |
| Acute systemic toxicity · Material-mediated pyrogenicity · Subacute/subchronic toxicity · Chronic toxicity | ISO 10993-11 |
| Genotoxicity · Carcinogenicity · Reproductive/developmental toxicity | ISO 10993-3 |
| Implantation | ISO 10993-6 |
| Haemocompatibility | ISO 10993-4 |

## 4. Table A.1 grid (X = ISO-recommended, O = FDA addition; both mean "address it")
Columns: Cy cytotoxicity · Se sensitisation · Ir irritation · AS acute systemic · MMP material-mediated pyrogenicity · SS subacute/subchronic · Gt genotoxicity · Im implantation · He haemocompatibility · CT chronic · Ca carcinogenicity.

| Category | Contact | Dur | Cy | Se | Ir | AS | MMP | SS | Gt | Im | He | CT | Ca |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Surface | Intact skin | A | X | X | X | | | | | | | | |
| Surface | Intact skin | B | X | X | X | | | | | | | | |
| Surface | Intact skin | C | X | X | X | | | | | | | | |
| Surface | Mucosal membrane | A | X | X | X | | | | | | | | |
| Surface | Mucosal membrane | B | X | X | X | X | O | X | | X | | | |
| Surface | Mucosal membrane | C | X | X | X | X | O | X | X | X | | X | |
| Surface | Breached/compromised | A | X | X | X | X | X | | | | | | |
| Surface | Breached/compromised | B | X | X | X | X | X | X | | X | | | |
| Surface | Breached/compromised | C | X | X | X | X | X | X | X | X | | X | X |
| Ext. communicating | Blood path, indirect | A | X | X | X | X | X | | | | X | | |
| Ext. communicating | Blood path, indirect | B | X | X | X | X | X | X | | | X | | |
| Ext. communicating | Blood path, indirect | C | X | X | X | X | X | X | X | X | X | X | X |
| Ext. communicating | Tissue/bone/dentin (+) | A | X | X | X | X | X | | | | | | |
| Ext. communicating | Tissue/bone/dentin (+) | B | X | X | X | X | X | X | X | X | | | |
| Ext. communicating | Tissue/bone/dentin (+) | C | X | X | X | X | X | X | X | X | | X | X |
| Ext. communicating | Circulating blood | A | X | X | X | X | X | | X^ | | X | | |
| Ext. communicating | Circulating blood | B | X | X | X | X | X | X | X | X | X | | |
| Ext. communicating | Circulating blood | C | X | X | X | X | X | X | X | X | X | X | X |
| Implant | Tissue/bone (+) | A | X | X | X | X | X | | | | | | |
| Implant | Tissue/bone (+) | B | X | X | X | X | X | X | X | X | | | |
| Implant | Tissue/bone (+) | C | X | X | X | X | X | X | X | X | | X | X |
| Implant | Blood | A | X | X | X | X | X | | X | X | X | | |
| Implant | Blood | B | X | X | X | X | X | X | X | X | X | | |
| Implant | Blood | C | X | X | X | X | X | X | X | X | X | X | X |

- **(+)** "Tissue" includes tissue fluids and spaces under the skin.
- **^** Genotoxicity in this row applies to devices used in extracorporeal blood circuits. Other limited-contact circulating-blood devices: genotoxicity is not marked.
- **Rule 4.1:** an X or O in the device's row → the mapped item is **mandatory**.
- **Rule 4.2:** an endpoint not marked and no trigger → that endpoint gives **not_applicable** (the item may still be raised by another endpoint or by §5–§7).
- **Writing the row:** "<category> / <contact> / <duration>", using the words in the first three columns, without the (+) or ^ marks. Example: "Implant / Tissue/bone / C".

## 5. Trigger rules (footnotes, not table cells)
These triggers apply to **every** contact category and duration, intact skin included (the FDA lists them outside the grid).
- **5.1 Reproductive/developmental toxicity → ISO 10993-3.**
  - **Mandatory** if the profile states any of: a novel material; a material with known reproductive or developmental toxicity; **pregnant patients** in the intended population; device material likely present in reproductive organs.
  - **Recommended** if, with none of the mandatory triggers stated: (a) the population includes **neonates, infants or children** (the endpoint covers early development after birth, but the FDA names only pregnant women as its population example); or (b) an **absorbable** material whose degradation products may spread through the body (the FDA says testing should be considered for such devices); or (c) a mandatory trigger is plausible from the profile but not stated.
  - Otherwise this endpoint gives not_applicable.
- **5.2 Degradation → ISO 10993-9.** Mandatory if the device or a material is **designed to degrade** while in contact with tissue (absorbable, resorbable, in-situ polymerising/curing). **Recommended** for every **long-term implant** (category Implant, duration C) that has a material not designed to degrade: any metal, polymer or ceramic in long-term implanted contact could degrade or corrode. Otherwise not_applicable.
- **5.3 Material-specific degradation parts.** ISO 10993-13 (polymers), -14 (ceramics), -15 (metals and alloys). Label each part by its own material class:
  - **mandatory** if a material of that class is designed to degrade;
  - else **recommended** if that class is present in a long-term implant;
  - else **not_applicable** (class not present, or not a long-term implant).
  - ISO 10993-9 takes the highest of these three labels. A class "other" has no part of its own.
- **5.4 Toxicokinetics → ISO 10993-16.** Recommended **only** when: 5.2 gives mandatory (a material is designed to degrade), or the sheet states a nanomaterial. Otherwise not_applicable.

## 6. Always-addressed items (any device with direct or indirect body contact)
- **ISO 10993-1** (framework), **ISO 10993-18** (chemical characterisation), **ISO 10993-17** (toxicological risk assessment), **ISO 10993-12** (sample preparation): **mandatory** for every device with direct or indirect body contact. They can be addressed with very little (a material list and a rationale count), which is why they are mandatory-to-address, not tests.
- **Non-contacting device** (no direct or indirect body contact by any component): **all 21 items are not_applicable.**

## 7. Special cases
- **7.1 Ethylene oxide → ISO 10993-7.** Mandatory if the device is EO-sterilised and has direct or indirect body contact. Not_applicable if not EO-sterilised, or if it is made only of metal alloys and/or glass. A neonatal or infant population does not change the label (it changes the limits).
- **7.2 Endotoxin → ISO 11737-3.** Mandatory if the device is **sold sterile** **and** any of: it is an implant; it contacts the blood path (directly or indirectly), the lymphatic system or cerebrospinal fluid; it is labelled non-pyrogenic. Otherwise not_applicable.
  - **Sold sterile** = the sterilisation field names a method (for example ethylene oxide, gamma, electron beam, steam). `non_sterile` = not sold sterile. Field missing = unclear → best label, low confidence.
- **7.3 Special populations** (neonates, infants, children, pregnant patients) never lower a label. Their only effect on labels is through §5.1 (pregnant patients → mandatory; neonates, infants or children → recommended).
- **7.4 Intact skin with a novel material:** the table labels stay (Cy/Se/Ir mandatory) and §5.1 applies as for any other contact (novel material → ISO 10993-3 mandatory).
- **7.5 Device-specific guidance (fda-knee):** on a knee-prosthesis profile, **fda-knee is mandatory**. It does not change any ISO item's label. On any other profile it is not present (don't label it).

## 8. Technical Specifications (never mandatory)
- **ISO/TS 10993-19** (physico-chemical, morphological and topographical characterisation): recommended for nanomaterials, novel materials, or devices whose **surface form matters**. Surface form matters only when the sheet **states** a surface feature: a porous or textured coating, a surface treatment, or a stated micro/nano surface structure. A fabric, mesh or textile construction by itself does not count. Otherwise not_applicable.
- **ISO/TS 10993-20** (immunotoxicology): recommended for long-term implants with a novel material, or when the profile states a known immune concern. Otherwise not_applicable.

## 9. Do-not-apply list (common mistakes)
- ISO 10993-1:2025's extra genotoxicity for prolonged mucosal, breached-surface or indirect-blood contact → **not** mandatory here (see the table).
- ISO 10993-1:2025's removal of material-mediated pyrogenicity → keep MMP as the table shows.
- ISO 10993-1:2025's "contact days" counting and its four contact groups → use the FDA categories and A/B/C.
- "Test everything to be safe" → label what the rules say; mandatory already means "address", not "test".
- `recommended` as a hedge on a two-way rule (§1).
- Labels from any other jurisdiction (EU MDR, Japan, China).

## 10. How to answer (per cell)
Write, in this order:
1. **reason:** one line, the rule that decides it (e.g. "Table A.1 Implant / Blood / B, Gt marked → §4.1").
2. **label:** not_applicable / recommended / mandatory.
3. **confidence:** low / medium / high.
4. **section:** every codebook section that decided the label, comma-separated (e.g. `§4.1` or `§4.1, §5.1`).
5. *(optional)* **needs_sme:** what is missing or unclear.
- If you are torn between two labels: give your best label and set confidence to low. Don't add an extra "safety" level.

## 11. Worked examples (invented devices; for the rules only)
**Example 1: EO-sterilised all-metal implant.** A titanium middle-ear implant, Implant / Tissue/bone / C, sterilisation: ethylene oxide.
- ISO 11737-3: sold sterile (a method is named) and an implant → **mandatory**, §7.2.
- ISO 10993-7: EO-sterilised, but made only of a metal alloy → **not_applicable**, §7.1.
- ISO 10993-15: long-term implant, metal may corrode, not designed to degrade → **recommended**, §5.2, §5.3.

**Example 2: neonatal skin device.** A soft polymer skin pad for newborns, Surface / Intact skin / B, non-sterile, no novel material.
- ISO 10993-3: the table marks no Gt or Ca; population neonates, no mandatory §5.1 trigger → **recommended**, §5.1.
- Same pad, but for pregnant patients → ISO 10993-3 **mandatory**, §5.1.
- ISO 11737-3: not sold sterile → **not_applicable**, §7.2.

**Example 3: sensor with a non-contact monitor.** A skin temperature probe (Surface / Intact skin / A) wired to a bedside monitor whose contact nature is non_contact.
- The monitor is ignored for every item (checklist: duration `none`, table row `none`), §2.
- ISO 10993-1, -12, -17, -18: the probe touches the body → **mandatory**, §6.
- ISO 10993-11: no AS/MMP/SS/CT mark in the probe's row → **not_applicable**, §4.2.

## 12. Change log
- v0.1 (14 Nov 2026): first draft for Hariom's review.
- v1.0 (16 Nov 2026): calibrated on the practice round (4 practice profiles + 6 rule probes; outputs never scored). Changes: `recommended` only where a rule names it (§1); sold sterile defined (§7.2); §5.1 split into mandatory / recommended triggers (pregnant patients mandatory; neonates, infants, children recommended; absorbable spread moved from mandatory to recommended, matching the FDA's "should be considered" wording); §5 triggers apply to every contact (§5, §7.4); §7.3 reworded; surface form for ISO/TS 10993-19 defined (§8); non-contact components (§2, §6); row-writing format (§4); several sections allowed (§10); worked examples added (§11); degradation parts labelled per material class, with every long-term implant giving recommended (§5.2, §5.3); toxicokinetics triggers made exhaustive (§5.4). Table A.1, the label meaning, the 21 items, the endpoint map and the ruleset are unchanged.
