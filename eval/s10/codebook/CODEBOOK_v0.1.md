# S10 LABELLING CODEBOOK v0.1 (DRAFT, for Hariom's review)
Status: draft 14 Nov 2026. Becomes v1.0 only after calibration on the practice profiles (S10b-2), then frozen with a sha256. Written in our own words; it quotes no standard or guidance text.
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
  - **not_applicable:** not indicated for this contact and duration, and no trigger in §5–§7 applies.
  - **recommended:** consider it; it should be addressed if its trigger applies. Use it when a trigger is plausible from the profile but not established, or for a Technical Specification whose subject is present (§8).
  - **mandatory:** must be **addressed** in the biological evaluation. Addressed means by existing data, chemical characterisation with a toxicological risk assessment, a written rationale, **or** a test. **Mandatory does not mean "a test is required".**

## 2. Read the profile as given
- Take the profile's **contact category**, **duration**, materials, sterilisation, population and components **exactly as stated**. Do not re-classify the device.
- **Duration codes:** A = limited (up to 24 h) · B = prolonged (more than 24 h, up to 30 days) · C = long-term (more than 30 days).
- **Repeated use:** the profile states the cumulative duration; use it.
- **Several components with different contact:** work out each component's row, then give each item the **highest** label any component gets.
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

## 5. Trigger rules (footnotes, not table cells)
- **5.1 Reproductive/developmental toxicity → ISO 10993-3.** Mandatory if the profile states any of: a novel material; a material with known reproductive or developmental toxicity; a population where this matters (e.g. pregnant patients); device material likely present in reproductive organs; an absorbable material whose products spread through the body. **Recommended** if one of these is plausible but not stated. Otherwise this endpoint gives not_applicable.
- **5.2 Degradation → ISO 10993-9.** Mandatory if the device or a material is **designed to degrade** while in contact with tissue (absorbable, resorbable, in-situ polymerising/curing). **Recommended** for a long-term implant whose material could degrade or corrode but is not designed to. Otherwise not_applicable.
- **5.3 Material-specific degradation parts.** ISO 10993-13 (polymers), -14 (ceramics), -15 (metals and alloys): **same label as ISO 10993-9**, but only for the material class that degrades or may degrade; not_applicable for classes not present.
- **5.4 Toxicokinetics → ISO 10993-16.** Recommended when degradation products or leachables may spread through the body (5.2 mandatory, or nanomaterials). Otherwise not_applicable.

## 6. Always-addressed items (any device with direct or indirect body contact)
- **ISO 10993-1** (framework), **ISO 10993-18** (chemical characterisation), **ISO 10993-17** (toxicological risk assessment), **ISO 10993-12** (sample preparation): **mandatory** for every device with direct or indirect body contact. They can be addressed with very little (a material list and a rationale count), which is why they are mandatory-to-address, not tests.
- **Non-contacting device** (no direct or indirect body contact): **all 21 items are not_applicable.**

## 7. Special cases
- **7.1 Ethylene oxide → ISO 10993-7.** Mandatory if the device is EO-sterilised and has direct or indirect body contact. Not_applicable if not EO-sterilised, or if it is made only of metal alloys and/or glass. A neonatal or infant population does not change the label (it changes the limits).
- **7.2 Endotoxin → ISO 11737-3.** Mandatory if the device is sold sterile **and** any of: it is an implant; it contacts the blood path (directly or indirectly), the lymphatic system or cerebrospinal fluid; it is labelled non-pyrogenic. Otherwise not_applicable.
- **7.3 Special populations** (neonates, children, pregnant patients) never lower a label; they can raise 5.1.
- **7.4 Intact skin with a novel material:** the table labels stay (Cy/Se/Ir mandatory); add 5.1 if it applies.
- **7.5 Device-specific guidance (fda-knee):** on a knee-prosthesis profile, **fda-knee is mandatory**. It does not change any ISO item's label. On any other profile it is not present (don't label it).

## 8. Technical Specifications (never mandatory)
- **ISO/TS 10993-19** (physico-chemical, morphological and topographical characterisation): recommended for nanomaterials, novel materials, or devices whose surface form matters (e.g. porous coatings). Otherwise not_applicable.
- **ISO/TS 10993-20** (immunotoxicology): recommended for long-term implants with a novel material, or when the profile states a known immune concern. Otherwise not_applicable.

## 9. Do-not-apply list (common mistakes)
- ISO 10993-1:2025's extra genotoxicity for prolonged mucosal, breached-surface or indirect-blood contact → **not** mandatory here (see the table).
- ISO 10993-1:2025's removal of material-mediated pyrogenicity → keep MMP as the table shows.
- ISO 10993-1:2025's "contact days" counting and its four contact groups → use the FDA categories and A/B/C.
- "Test everything to be safe" → label what the rules say; mandatory already means "address", not "test".
- Labels from any other jurisdiction (EU MDR, Japan, China).

## 10. How to answer (per cell)
Write, in this order:
1. **reason:** one line, the rule that decides it (e.g. "Table A.1 implant blood B, Gt marked → §4.1").
2. **label:** not_applicable / recommended / mandatory.
3. **confidence:** low / medium / high.
4. **section:** the codebook section you used (e.g. §4.1, §5.2, §7.2).
5. *(optional)* **needs_sme:** what is missing or unclear.
- If you are torn between two labels: give your best label and set confidence to low. Don't add an extra "safety" level.

## 11. Change log
- v0.1 (14 Nov 2026): first draft for Hariom's review. Practice-profile calibration and freezing happen in S10b-2.
