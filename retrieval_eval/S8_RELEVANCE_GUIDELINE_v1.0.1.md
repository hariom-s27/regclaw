# S8 relevance-judging guideline v1.0.1

**Who reads this:** the two LLM judges (Claude Opus 5.5 and GPT-6 Astra) and Shyam, as audit labeller and referee. Everyone uses the same text.

**Status:** v1.0.1, frozen (sha recorded in `s8_partD_freeze.json`; see Addendum 2 and its note on v1.0.1) before any real item is judged. Do not change it after real judging starts.

**Changes from v0.9 (calibration on 15 non-eval practice items, 2026-10-20):** rules 13–16 added; illustrations for them added in section 8. Nothing else changed.

**Changes from v1.0 (judge rehearsal on the same practice items, 2026-10-20; the one wording fix Addendum 2 §4 allows):** rule 16 no longer lists "another document for a document-content query" as a 0 example, and it now says that the narrative's grade-1 line always wins. Nothing else changed.

---

## 1. Your task
You are an experienced **medical-device regulatory affairs analyst**. For each item you get:
- a **query**: a question a regulatory professional might ask;
- the query's **narrative**: what counts as grade 3, 2 and 1, and the traps;
- the **jurisdiction** the query names, if any;
- **one clause** from a public regulatory corpus: its source document, clause id, title and full text.

Decide **how well this clause answers this query**, on a 0–3 scale. Judge **only the text in front of you**. You don't know, and must not guess, which search system found the clause or how it was ranked.

## 2. The grades
| Grade | Meaning | Simple test |
|---|---|---|
| **3** | **Directly answers.** The clause itself states the requirement, recommendation, definition, limit, actor or procedure the query asks for. | "If I could read only this clause, would the question be answered?" → yes |
| **2** | **Relevant, but partial.** It answers part of the question, or states a necessary condition, or gives the answer for a closely related case the narrative accepts. | "It gives a real piece of the answer, not just the topic." |
| **1** | **Same topic, doesn't answer.** It's about the subject but doesn't give what was asked: background, scope, a heading, a cross-reference, a different aspect, or the right answer for the wrong jurisdiction. | "It's on the topic, but I still don't have the answer." |
| **0** | **Unrelated.** | "Off topic." |

**Relevant = grade 2 or 3.** The line between **1 and 2** matters most, so take the most care there.

## 3. Decide in these steps (write a short note for each)
1. **What exactly does the query ask?** Mandatory duties only? A definition? A number or time period? Who does something? A procedure? For which devices or jurisdiction?
2. **What does the clause actually state?** Choose one:
   - `answers`: the clause states it;
   - `partial`: it states part of it, or a necessary condition;
   - `mentions_only`: the topic or words appear but the asked-for content isn't there;
   - `unrelated`.
3. **Jurisdiction / scope match?** `yes` / `no` / `not_applicable` (the query names no jurisdiction).
4. **Evidence span:** copy up to 30 words from the clause that support your grade, or write `none`.
5. **Grade 0–3**, using the narrative first and section 4 second.

## 4. Rules (these override your general sense of "relevance")
1. **The narrative wins.** If the query's narrative says what earns 3/2/1, follow it, even if you'd have graded differently.
2. **Mentioning is not answering.** A clause that only uses the query's words, or only names the topic, gets **at most 1**.
3. **When unsure between 1 and 2, choose 1** and set `unsure = yes`.
4. **Judge the clause text, not labels.** Ignore any outside idea of whether the clause is "mandatory". Read its own wording.
5. **Mandatory vs recommended.** When a query asks for **mandatory** requirements:
   - wording that imposes a duty ("shall", "must", "is required to", or a binding article/rule) can answer;
   - wording that only recommends ("should", "we recommend", "FDA recommends", "it is advisable") is **at most 1**, unless the narrative says otherwise.

   When the query doesn't ask about strength, a "should" clause can be a full answer.
6. **Jurisdiction.** If the query names a jurisdiction (US, EU/Europe, India), a clause from **another** jurisdiction on the same topic gets **at most 1**. **IMDRF counts as another jurisdiction** for US/EU/India queries. If the query names none, all sources are eligible.
7. **Headings, stubs, cross-references.** A clause that's only a heading, a title, a table of contents line, or "see section X" with no substance is **at most 1**.
8. **Definitions.** If the query asks for a definition, the defining clause is 3. A clause that uses the term without defining it is at most 1.
9. **Numbers and time limits.** If the query asks for a number, period or deadline, the clause must state it for a 3. A duty without the number is at most 2, and only if the narrative allows.
10. **Near-duplicates.** Some documents contain nearly identical text, for example IMDRF essential principles and India's Essential Principles. Judge each clause on its own, with the jurisdiction rule. Never copy a grade because "it looks the same".
11. **Some queries may have no grade-3 clause at all.** That's expected for some, so don't force a 3.
12. **No outside lookup.** Use your general regulatory knowledge to *understand* the text, but the grade must rest on what **this clause says**, not on what you know the law says elsewhere.
13. **Device scope.** If the query names a device type (for example wound dressings or infusion sets), a clause that gives the requirement **only for a different, specific device category** (for example implants only, or one table section that lists other device categories) is **at most 1**. A clause that gives the requirement **for devices in general** is graded by the narrative (often 2).
14. **A passing reference is not content.** A clause about something else that only refers to a document or topic in passing (for example a signed commitment that says "I have read document X") does **not** state what that document must contain or what the requirement is. It is **at most 1**, even if the passing reference names a detail. The narrative's "names part of its content" means the clause itself is about what must be contained or done.
15. **Lists: part vs whole.** When the query asks for a list (contents, elements, requirements), a clause that gives **only one part** of that list (one section of a longer table, a few items with the rest elsewhere) is **2**. A **3** needs the clause to give the list itself, or the core of it. An introductory sentence ("shall contain the following:") with the list cut off is at most 2.
16. **0 vs 1.** Give **1** when the clause is on the **same specific subject** as the query (the same test, endpoint, document or duty), or on anything the narrative's grade-1 line names. Give **0** when the clause is on a **different subject** that the grade-1 line does not name, even within the same broad field: for example another biological endpoint, or a general safety duty that does not address the asked test. **The narrative's grade-1 line always wins: anything it names is 1, never 0.**

## 5. Domain primer (background only, never a reason by itself)
**The corpus has 5 source families:**

| Code | Source | Nature | Typical wording |
|---|---|---|---|
| `fda-*` | US FDA guidance documents (e.g. biocompatibility / ISO 10993-1 use, nitinol, labeling, sterility) | **Guidance**: non-binding recommendations ("Contains Nonbinding Recommendations"); explains FDA's current thinking and what to put in submissions such as 510(k)/PMA | mostly "should", "we recommend" |
| `eu-mdr` | EU Medical Device Regulation 2017/745 (a slice: Articles 1–10, Annexes I–III, XIV–XV) | **Binding law**: definitions (Art 2), manufacturer obligations (Art 10), General Safety and Performance Requirements (Annex I), technical documentation (Annex II–III), clinical evaluation and PMCF (Annex XIV), clinical investigations (Annex XV) | "shall" |
| `in-mdr` / `in-ep` | India Medical Devices Rules 2017 and India Essential Principles | **Binding rules**: licences (import/manufacture), Central/State Licensing Authority, forms, labelling (rules/schedules); the EPs are safety/performance principles | "shall" |
| `imdrf-n47` | IMDRF Essential Principles of Safety and Performance (N47) | **International harmonisation guidance**, not law in itself | "should" / "shall" mixed |
| `epa-63o` | US EPA 40 CFR Part 63 Subpart O: ethylene oxide **emission standards for sterilisation facilities** | **Binding environmental law** for facilities (air emissions, controls, monitoring, compliance tests). Not about device biocompatibility or residues in devices | "shall" |

**Key concepts you may meet:**
- **Biological evaluation / biocompatibility:** assessing a device's biological safety within risk management. ISO 10993 parts: -1 framework, -5 cytotoxicity, -7 ethylene oxide sterilisation residuals (EO/ECH limits), -10 sensitisation, -11 systemic toxicity, -18 chemical characterisation, and others. Endpoints depend on **body contact** (surface, externally communicating, implant) and **duration** (limited ≤24 h, prolonged >24 h–30 d, long-term >30 d).
- **Ethylene oxide (EO/EtO):** a sterilant. **Residues in the device** (EO, ECH) are a device-safety topic. **Emissions from facilities** are an environmental topic (EPA). Different questions.
- **Sterilisation:** validation, sterility assurance, labelling of sterile devices, changes to the sterilisation method (and their submission consequences in the US).
- **EU concepts:** manufacturer, authorised representative, importer, **notified body** (assesses conformity for higher-risk classes), technical documentation, classes I/IIa/IIb/III, **clinical evaluation** (may use literature and an **equivalent device** under conditions) vs **clinical investigation** (a study on humans), PMCF, UDI, vigilance and **serious incident**.
- **India concepts:** Central Licensing Authority (import licences, certain classes), State Licensing Authority, forms and applications, labelling particulars.
- **Labelling:** the label vs instructions for use (IFU); sterile state, sterilisation method, single-use indication, symbols.

## 6. Output (one row per item, exactly these fields)
`item_id, query_asks, clause_states, jurisdiction_match, evidence_span, grade, unsure, note`
- `query_asks`: ≤ 20 words.
- `clause_states`: one of `answers | partial | mentions_only | unrelated`.
- `jurisdiction_match`: `yes | no | not_applicable`.
- `evidence_span`: ≤ 30 words copied from the clause, or `none`.
- `grade`: 0, 1, 2 or 3.
- `unsure`: `yes | no`.
- `note`: ≤ 25 words, optional (e.g. "should-only; query asks mandatory").

## 7. Process rules for the judges
- **Read every clause yourself and judge it.** **Do not write code, scripts or keyword rules to label.** Automated keyword matching is exactly the error this study guards against.
- Judge **one item at a time**, in the given order. Don't compare items or rank them against each other.
- Don't look at, ask for, or infer any system, rank or score information. Don't open any file except the sheet and this guideline.
- Work query by query. Save after each query, so the work can resume.
- Keep the same standard from the first item to the last. If you notice you've drifted, say so in `note`. Don't silently change earlier grades.
- **For the human auditor (Shyam):** the audit is your own reading. Do not use any AI tool, chatbot, search engine summary or either judge's files while labelling audit items. You may look up a word or abbreviation in a dictionary. If you cannot decide, choose the lower grade and set `unsure = yes`.

## 8. Illustrations (invented clauses, NOT from the corpus)
- *Query:* "What must a manufacturer keep on file about device testing?"
  *Clause (invented):* "The manufacturer shall keep test reports for each batch for five years."
  → `answers` → **3**.
- Same query.
  *Clause (invented):* "Testing is an important part of quality management."
  → `mentions_only` → **1**.
- *Query:* "Mandatory labelling requirements in the EU for sterile devices."
  *Clause (invented, US guidance):* "The label should indicate the sterilisation method."
  → recommendation + other jurisdiction → **1**.
- *Query:* "What is the definition of X?"
  *Clause (invented):* "'X' means a device intended for …"
  → **3**.
  *Clause (invented):* "Requirements for X are set out in Annex II."
  → **1**.
- *Query:* "Leak-testing requirements for catheters."
  *Clause (invented):* "For implants, leak testing should be performed on the final sterilised device."
  → requirement only for another specific device category → **1** (rule 13).
- *Query:* "What must a test plan contain?"
  *Clause (invented, an operator's sign-off form):* "I have read the test plan, including its acceptance criteria."
  → passing reference inside a different document → **1** (rule 14).
- Same query.
  *Clause (invented):* "Section 1 of the test plan shall give the plan's title, version and author." (sections 2–9 are in other clauses)
  → one part of the list → **2** (rule 15).
- *Query:* "Mandatory noise-limit testing for ventilators."
  *Clause (invented):* "Devices shall be designed to reduce the risk of electric shock."
  → different subject in the same broad field → **0** (rule 16).
