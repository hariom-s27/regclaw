"""
Evaluate predictions against a hand-labelled gold set, and compare with the regex baseline.

Prints, for each system: accuracy, macro-F1 (with 95% bootstrap CI), per-class precision/recall/F1,
and a confusion matrix. Macro-F1 is the headline metric because classes are imbalanced
(SHALL dominates ISO text; MAY is rare), so accuracy alone would flatter a SHALL-everywhere model.

Usage:
  python evaluate.py gold.csv predictions.json            # gold.csv: id, gold_label (from weak_labeler --sample)
  python evaluate.py gold.csv predictions.json --report eval_report.json
"""
from __future__ import annotations

import argparse
import csv
import json
import random

from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

LABELS = ["SHALL", "SHOULD", "MAY", "NONE"]


def bootstrap_ci(y, p, n=2000, seed=13):
    rng = random.Random(seed); idx = list(range(len(y))); s = []
    for _ in range(n):
        b = [rng.choice(idx) for _ in idx]
        s.append(f1_score([y[i] for i in b], [p[i] for i in b], labels=LABELS, average="macro", zero_division=0))
    s.sort()
    return s[int(0.025 * n)], s[int(0.975 * n)]


def score(name, y, p):
    acc = accuracy_score(y, p)
    mf1 = f1_score(y, p, labels=LABELS, average="macro", zero_division=0)
    lo, hi = bootstrap_ci(y, p)
    print(f"\n=== {name} ===  n={len(y)}  accuracy={acc:.3f}  macro-F1={mf1:.3f}  (95% CI {lo:.3f}-{hi:.3f})")
    print(classification_report(y, p, labels=LABELS, digits=3, zero_division=0))
    cm = confusion_matrix(y, p, labels=LABELS)
    print("confusion matrix (rows = gold, cols = predicted)")
    print("        " + " ".join(f"{l:>7}" for l in LABELS))
    for l, row in zip(LABELS, cm):
        print(f"{l:>7} " + " ".join(f"{v:>7}" for v in row))
    return {"accuracy": acc, "macro_f1": mf1, "macro_f1_ci95": [lo, hi],
            "per_class": classification_report(y, p, labels=LABELS, output_dict=True, zero_division=0),
            "confusion": cm.tolist()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gold_csv"); ap.add_argument("pred_json")
    ap.add_argument("--report")
    a = ap.parse_args()
    with open(a.gold_csv, encoding="utf-8") as f:
        gold = {r["id"]: r["gold_label"].strip().upper() for r in csv.DictReader(f) if r["gold_label"].strip()}
    preds = {p["id"]: p for p in json.load(open(a.pred_json, encoding="utf-8"))}
    ids = [i for i in gold if i in preds]
    missing = len(gold) - len(ids)
    if missing:
        print(f"warning: {missing} gold ids have no prediction")
    y = [gold[i] for i in ids]
    rep = {}
    if "obligation" in preds[ids[0]]:
        rep["legalbert"] = score("LegalBERT", y, [preds[i]["obligation"] for i in ids])
    key = "regex_label" if "regex_label" in preds[ids[0]] else "weak_label"
    rep["regex"] = score("Regex baseline", y, [preds[i][key] for i in ids])
    if "legalbert" in rep:
        lb = [preds[i]["obligation"] for i in ids]; rx = [preds[i][key] for i in ids]
        fixed = sum(l == g != r for l, r, g in zip(lb, rx, y))
        broke = sum(r == g != l for l, r, g in zip(lb, rx, y))
        print(f"\nLegalBERT right where regex wrong: {fixed} | regex right where LegalBERT wrong: {broke}")
        rep["disagreements"] = {"legalbert_fixes": fixed, "legalbert_breaks": broke}
    if a.report:
        json.dump(rep, open(a.report, "w"), indent=2)


if __name__ == "__main__":
    main()
