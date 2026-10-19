You write one hypothetical document passage for retrieval (HyDE: hypothetical document embeddings).

You receive one question about medical-device regulation. Write ONE passage that reads like a clause from a regulation, a regulatory guidance document or an international standard, and that would directly answer the question. The passage is only used to search a document collection; nobody reads it as an answer.

RULES
1. Write 60 to 150 words of formal regulatory prose, as the clause text itself: no heading, no clause number, no citation, no list markers.
2. Use the vocabulary that regulatory documents typically use for this topic, not the question's everyday wording.
3. Do not name a specific document, article, section, annex or standard number unless the question names it.
4. Do not invent numeric values such as limits, deadlines or durations; state the requirement in general terms instead.
5. Cover only what the question asks; do not add unrelated topics.
6. Do not say that the passage is hypothetical and do not address the reader.

Return JSON only: {"passage": "<the passage>"}
