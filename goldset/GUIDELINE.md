# Obligation labelling guideline v1.0 (frozen)

- **A. Labels:**

  | Label | Meaning | Typical forms |
  |---|---|---|
  | SHALL | requirement or prohibition | shall, shall not, must, must not, is required to, has to, is to, may not (as prohibition), is responsible for |
  | SHOULD | recommendation | should, should not, it is recommended that, ought to, FDA recommends |
  | MAY | permission or exemption | may, is permitted, need not, is not required to |
  | NONE | no deontic force | can/cannot, will (prediction), descriptive text, NOTE, EXAMPLE, definitions, titles, scope |

- **B. Decision procedure** (first match wins):
  1. NOTE / EXAMPLE / definition ("X means…", "shall mean…") / heading / scope or purpose → **NONE**.
  2. Any duty or ban on anyone → **SHALL**.
  3. A recommendation → **SHOULD**.
  4. A permission or exemption → **MAY**.
  5. Otherwise → **NONE**.
- **C. Flags:**
  - `mixed=1` if another modal of a different strength also governs;
  - `restated_reg=1` if FDA guidance restates a statute or regulation (21 CFR 10.115).
- **D. Mixed clauses:** the strongest wins (SHALL > SHOULD > MAY > NONE).
- **E. Rules that never change the label:** conditions ("Where applicable…") don't weaken it, and the party (manufacturer, notified body, FDA) doesn't matter.
- **F. Basis for "must":** ISO forms for ISO/EU/India text; US plain-language guidance ("must" = obligation) for US text.
- **G. Uncertainty:** if still unsure after 30 s, give your best label, set `low_confidence=1`, and write one line in `note`.
- **H. Worked examples** (written in corpus style, not quotations):

| # | Clause | Label | Rule |
|---|---|---|---|
| 1 | The manufacturer shall not place the device on the market unless conformity has been assessed. | SHALL | Prohibition = SHALL |
| 2 | NOTE 2 Manufacturers should consider the intended user population. | NONE | NOTE beats modal |
| 3 | EXAMPLE A sponsor may submit the summary as an appendix. | NONE | EXAMPLE |
| 4 | FDA recommends that sponsors include a bench-testing summary. | SHOULD | Recommendation, no modal |
| 5 | Sponsors must comply with 21 CFR part 812 before beginning the study. | SHALL, restated_reg=1 | FDA restating a regulation |
| 6 | Owners or operators must not exceed the emission limit in Table 1. | SHALL | US "must not" = prohibition |
| 7 | The applicant may not use data from a different device version. | SHALL | "may not" denies permission |
| 8 | Residual solvents can be removed by vacuum drying. | NONE | can = capability |
| 9 | FDA will review the submission within 90 days of receipt. | NONE | will = prediction |
| 10 | The manufacturer shall document the rationale and may use a harmonised standard. | SHALL, mixed=1 | Strongest wins |
| 11 | Where applicable, the label should state the sterilisation method. | SHOULD | A condition doesn't weaken it |
| 12 | The importer is responsible for verifying that the device bears the CE marking. | SHALL | Non-modal obligation |
| 13 | Manufacturers need not repeat biocompatibility testing if materials are unchanged. | MAY | Exemption = permission form |
| 14 | "Clinical investigation" shall mean any systematic investigation involving subjects. | NONE | Definition |
| 15 | The notified body may request additional samples from the manufacturer. | MAY | Permission for an authority |
