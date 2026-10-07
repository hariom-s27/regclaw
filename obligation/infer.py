"""
Run the trained classifier over a JSON list of clauses and write obligation labels.

Final label policy (keeps LegalBERT as the classifier, regex as a cross-check):
  obligation  = LegalBERT prediction
  needs_review = True when LegalBERT and the regex disagree, or LegalBERT confidence < --min_conf
  The `obligation` field is what gets written onto (:Clause {obligation}) in Neo4j.

Usage:
  python infer.py clauses.json model_ft predictions.json
  python infer.py clauses.json model_frozen predictions.json
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import time

import numpy as np
import torch
from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

from weak_labeler import label_clause


def softmax(x):
    e = np.exp(x - x.max(-1, keepdims=True))
    return e / e.sum(-1, keepdims=True)


@torch.no_grad()
def predict(texts, model_dir, bs=16):
    meta = json.load(open(os.path.join(model_dir, "meta.json")))
    labels, max_len = meta["labels"], meta["max_len"]
    if meta["mode"] == "finetune":
        tok = AutoTokenizer.from_pretrained(model_dir)
        model = AutoModelForSequenceClassification.from_pretrained(model_dir).eval()
        probs = []
        for i in range(0, len(texts), bs):
            b = tok(texts[i:i + bs], truncation=True, max_length=max_len, padding=True, return_tensors="pt")
            probs.append(softmax(model(**b).logits.numpy()))
        P = np.vstack(probs)
    else:
        from train import embed
        tok = AutoTokenizer.from_pretrained(meta["base"]); enc = AutoModel.from_pretrained(meta["base"])
        clf = pickle.load(open(os.path.join(model_dir, "logreg.pkl"), "rb"))
        X = embed(texts, tok, enc, max_len)
        P = np.zeros((len(texts), len(labels)))
        P[:, clf.classes_] = clf.predict_proba(X)
    return labels, P


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clauses_json"); ap.add_argument("model_dir"); ap.add_argument("out_json")
    ap.add_argument("--min_conf", type=float, default=0.7)
    ap.add_argument("--threads", type=int, default=os.cpu_count())
    a = ap.parse_args()
    torch.set_num_threads(a.threads)

    clauses = json.load(open(a.clauses_json, encoding="utf-8"))
    t0 = time.time()
    labels, P = predict([c["text"] for c in clauses], a.model_dir)
    secs = time.time() - t0

    out = []
    for c, p in zip(clauses, P):
        k = int(p.argmax()); pred = labels[k]
        rx = label_clause(c)
        out.append({
            "id": c["id"],
            "obligation": pred,
            "confidence": round(float(p[k]), 4),
            "probs": {l: round(float(v), 4) for l, v in zip(labels, p)},
            "regex_label": rx["weak_label"],
            "regex_triggers": rx["triggers"],
            "agrees_with_regex": pred == rx["weak_label"],
            "needs_review": pred != rx["weak_label"] or p[k] < a.min_conf or rx["needs_review"],
        })
    json.dump(out, open(a.out_json, "w", encoding="utf-8"), indent=2)
    agree = np.mean([o["agrees_with_regex"] for o in out])
    print(f"{len(out)} clauses in {secs:.1f}s ({1000*secs/len(out):.0f} ms/clause) | "
          f"agreement with regex {agree:.1%} | needs_review {sum(o['needs_review'] for o in out)}")


if __name__ == "__main__":
    main()
