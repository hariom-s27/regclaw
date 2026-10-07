"""
Regex weak labeller for clause obligation strength: SHALL / SHOULD / MAY / NONE.

Rules follow ISO/IEC Directives Part 2 (2021), Clause 7 verbal forms:
  shall  = requirement      (equivalents: is to, is required to, it is required that, has to)
  should = recommendation   (equivalent: it is recommended that)
  may    = permission       (equivalent: is permitted, is allowed)
  can    = possibility / capability  -> NOT an obligation -> NONE
  must   = external constraint (Directives Table 7) -> mapped to SHALL, flagged

Clause-level rule: strongest normative modal wins (SHALL > SHOULD > MAY > NONE),
counting only normative text (NOTE / EXAMPLE paragraphs and informative annexes are excluded).

Usage:
  python weak_labeler.py clauses.json weak_labels.json
  python weak_labeler.py clauses.json weak_labels.json --sample gold_to_label.csv --n 200
Input: JSON list of {"id": str, "text": str, optional "number": str, optional "informative": bool}
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter

RANK = {"NONE": 0, "MAY": 1, "SHOULD": 2, "SHALL": 3}

# Order matters: longer / negated / special phrases first; matched spans are masked
# so later, shorter patterns cannot re-match the same words.
PATTERNS: list[tuple[str, str, str]] = [
    # (regex, label, flag)
    (r"\bshall\s+not\b", "SHALL", "prohibition"),
    (r"\b(?:is|are)\s+not\s+(?:allowed|permitted|acceptable|permissible)\b", "SHALL", "prohibition"),
    (r"\bmust\s+not\b", "SHALL", "prohibition;must_external"),
    (r"\bmay\s+not\b", "SHALL", "may_not_ambiguous"),          # Directives: do not use for prohibition
    (r"\bmay\s+(?:also\s+)?be\s+(?:required|necessary|needed)\b", "MAY", "conditional_requirement"),
    (r"\bshould\s+not\b", "SHOULD", "negative_recommendation"),
    (r"\bit\s+is\s+not\s+recommended\b", "SHOULD", "negative_recommendation"),
    (r"\bshall\b", "SHALL", ""),
    (r"\bmust\b", "SHALL", "must_external"),
    (r"\b(?:is|are)\s+required\s+to\b", "SHALL", "equivalent_form"),
    (r"\bit\s+is\s+required\s+that\b", "SHALL", "equivalent_form"),
    (r"\b(?:has|have)\s+to\b", "SHALL", "equivalent_form"),
    (r"\bshould\b", "SHOULD", ""),
    (r"\b(?:it\s+is|is|are)\s+recommended\b", "SHOULD", "equivalent_form"),
    (r"\b(?:is|are)\s+(?:permitted|allowed)\b", "MAY", "equivalent_form"),
    (r"\bmay\b", "MAY", ""),
    (r"\bcan(?:not)?\b|\bbe\s+able\s+to\b", "NONE", "possibility"),
]
COMPILED = [(re.compile(p, re.IGNORECASE), lab, flag) for p, lab, flag in PATTERNS]

# NOTE / EXAMPLE paragraphs are informative in ISO documents: drop them before matching.
INFORMATIVE_PARA = re.compile(r"^\s*(?:NOTE|EXAMPLE)(?:\s+\d+)?\b.*$", re.MULTILINE)
# Quoted modal words, e.g. the ISO foreword: '"shall" indicates a requirement'.
QUOTED_MODAL = re.compile(r"[\"'‘’“”](?:shall|should|may|can|must)[\"'‘’“”]",
                          re.IGNORECASE)
# The month "May", e.g. "May 2017", "15 May 2020".
MONTH_MAY = re.compile(r"\b(?:\d{1,2}\s+)?May\s+\d{4}\b|\bMay\s+\d{1,2},?\s+\d{4}\b")


def normative_text(text: str) -> str:
    t = INFORMATIVE_PARA.sub(" ", text)
    t = QUOTED_MODAL.sub(" ", t)
    t = MONTH_MAY.sub(" ", t)
    return t


def label_clause(clause: dict) -> dict:
    text = clause.get("text", "")
    flags: set[str] = set()
    if clause.get("informative"):
        # Informative annex: whatever the verbs say, nothing here is binding.
        return {"id": clause["id"], "weak_label": "NONE", "triggers": [],
                "flags": ["informative_annex"], "needs_review": False}

    t = normative_text(text)
    if t.strip() != text.strip() and INFORMATIVE_PARA.search(text):
        flags.add("note_or_example_removed")

    masked = list(t)
    hits = []
    for rx, lab, flag in COMPILED:
        for m in rx.finditer("".join(masked)):
            hits.append((m.start(), m.group(0), lab))
            if flag:
                flags.update(flag.split(";"))
            for i in range(m.start(), m.end()):
                masked[i] = "#"          # same length, so later offsets stay valid

    labels = [h[2] for h in hits]
    label = max(labels, key=RANK.get) if labels else "NONE"
    if not labels:
        flags.add("no_modal")
    if len({l for l in labels if l != "NONE"}) > 1:
        flags.add("mixed_modals")

    needs_review = bool(flags & {"may_not_ambiguous", "conditional_requirement", "mixed_modals"})
    return {
        "id": clause["id"],
        "weak_label": label,
        "triggers": [h[1].lower() for h in sorted(hits)],
        "flags": sorted(flags),
        "needs_review": needs_review,
    }


def stratified_sample(rows: list[dict], clauses: dict, n: int, seed: int = 13) -> list[dict]:
    """Equal share per weak label (capped by availability), rest filled at random.
    Over-samples rare classes (MAY, SHOULD) so per-class F1 is measurable."""
    rng = random.Random(seed)
    by = {}
    for r in rows:
        by.setdefault(r["weak_label"], []).append(r)
    per = n // max(len(by), 1)
    picked = []
    for lab, items in by.items():
        rng.shuffle(items)
        picked += items[:per]
    rest = [r for r in rows if r not in picked]
    rng.shuffle(rest)
    picked += rest[: max(0, n - len(picked))]
    rng.shuffle(picked)  # annotator must not see clauses grouped by weak label
    return picked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clauses_json")
    ap.add_argument("out_json")
    ap.add_argument("--sample", help="write a CSV of clauses to hand-label")
    ap.add_argument("--n", type=int, default=200)
    a = ap.parse_args()

    clauses = json.load(open(a.clauses_json, encoding="utf-8"))
    rows = [label_clause(c) for c in clauses]
    json.dump(rows, open(a.out_json, "w", encoding="utf-8"), indent=2)

    dist = Counter(r["weak_label"] for r in rows)
    rev = sum(r["needs_review"] for r in rows)
    print(f"labelled {len(rows)} clauses: {dict(dist)} | needs_review={rev}")

    if a.sample:
        cmap = {c["id"]: c for c in clauses}
        sample = stratified_sample(rows, cmap, a.n)
        with open(a.sample, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            # weak label deliberately NOT written: annotate blind to avoid anchoring
            w.writerow(["id", "number", "text", "gold_label", "annotator_note"])
            for r in sample:
                c = cmap[r["id"]]
                w.writerow([r["id"], c.get("number", ""), c["text"], "", ""])
        print(f"wrote {len(sample)} clauses to {a.sample} (fill gold_label with SHALL/SHOULD/MAY/NONE)")


if __name__ == "__main__":
    main()
