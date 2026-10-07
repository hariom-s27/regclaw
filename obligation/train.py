"""
Train a LegalBERT obligation-strength classifier (SHALL / SHOULD / MAY / NONE) on regex weak labels.

Two modes (same data, same split, so results are comparable):
  --mode finetune : nlpaueb/legal-bert-base-uncased + linear classification head, full fine-tune (recommended)
  --mode frozen   : frozen LegalBERT mean-pooled embeddings + scikit-learn LogisticRegression (fast baseline)

Gold (hand-labelled) clause ids are EXCLUDED from training so evaluation is honest.
Clauses flagged needs_review by the weak labeller are excluded from training (noisy labels).

Usage:
  python train.py clauses.json weak_labels.json --gold gold.csv --mode finetune --out model_ft
  python train.py clauses.json weak_labels.json --gold gold.csv --mode frozen   --out model_frozen
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import pickle
import random
import time

import numpy as np
import torch
from torch.utils.data import DataLoader
from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

LABELS = ["NONE", "MAY", "SHOULD", "SHALL"]
L2I = {l: i for i, l in enumerate(LABELS)}
MODEL = "nlpaueb/legal-bert-base-uncased"


def set_seed(s: int):
    random.seed(s); np.random.seed(s); torch.manual_seed(s)


def load_data(clauses_path, weak_path, gold_path):
    clauses = {c["id"]: c for c in json.load(open(clauses_path, encoding="utf-8"))}
    weak = json.load(open(weak_path, encoding="utf-8"))
    gold_ids = set()
    if gold_path:
        with open(gold_path, encoding="utf-8") as f:
            gold_ids = {r["id"] for r in csv.DictReader(f)}
    rows = [(clauses[w["id"]]["text"], L2I[w["weak_label"]]) for w in weak
            if w["id"] not in gold_ids and not w["needs_review"]]
    print(f"train pool: {len(rows)} clauses (excluded {len(gold_ids)} gold, "
          f"{sum(w['needs_review'] for w in weak)} needs_review)")
    return rows


def split(rows, dev_frac=0.1, seed=13):
    rng = random.Random(seed)
    rows = rows[:]; rng.shuffle(rows)
    k = max(1, int(len(rows) * dev_frac))
    return rows[k:], rows[:k]


def class_weights(y):
    counts = np.bincount(y, minlength=len(LABELS)).astype(float)
    counts[counts == 0] = 1.0
    w = len(y) / (len(LABELS) * counts)          # inverse frequency ("balanced")
    return torch.tensor(w, dtype=torch.float)


def collate(tok, max_len):
    def f(batch):
        texts, labels = zip(*batch)
        enc = tok(list(texts), truncation=True, max_length=max_len, padding=True, return_tensors="pt")
        enc["labels"] = torch.tensor(labels)
        return enc
    return f


@torch.no_grad()
def dev_macro_f1(model, loader):
    from sklearn.metrics import f1_score
    model.eval(); ys, ps = [], []
    for b in loader:
        y = b.pop("labels")
        ps += model(**b).logits.argmax(-1).tolist(); ys += y.tolist()
    model.train()
    return f1_score(ys, ps, average="macro", labels=list(range(len(LABELS))), zero_division=0)


def finetune(rows, out, epochs, lr, bs, max_len):
    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL, num_labels=len(LABELS), id2label=dict(enumerate(LABELS)), label2id=L2I)
    train, dev = split(rows)
    loss_fn = torch.nn.CrossEntropyLoss(weight=class_weights([y for _, y in train]))
    tl = DataLoader(train, batch_size=bs, shuffle=True, collate_fn=collate(tok, max_len))
    dl = DataLoader(dev, batch_size=32, collate_fn=collate(tok, max_len))
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    total = epochs * len(tl)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min((s + 1) / max(1, int(0.1 * total)), max(0.0, (total - s) / (0.9 * total))))

    best, t0 = -1.0, time.time()
    model.train()
    for ep in range(epochs):
        for step, b in enumerate(tl):
            y = b.pop("labels")
            loss = loss_fn(model(**b).logits, y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(); sched.step(); opt.zero_grad()
            if step % 20 == 0:
                print(f"epoch {ep+1} step {step}/{len(tl)} loss {loss.item():.4f} [{time.time()-t0:.0f}s]")
        f1 = dev_macro_f1(model, dl)
        print(f"epoch {ep+1} dev macro-F1 (vs weak labels) = {f1:.3f}")
        if f1 > best:                              # keep best epoch = simple early stopping
            best = f1
            model.save_pretrained(out); tok.save_pretrained(out)
    json.dump({"mode": "finetune", "labels": LABELS, "max_len": max_len, "dev_macro_f1_weak": best,
               "train_seconds": round(time.time() - t0)}, open(os.path.join(out, "meta.json"), "w"), indent=2)
    print(f"saved best model to {out} (dev macro-F1 {best:.3f}, {time.time()-t0:.0f}s)")


@torch.no_grad()
def embed(texts, tok, enc, max_len, bs=16):
    """Mean pooling over non-padding tokens (usually better than raw [CLS] for a frozen BERT)."""
    enc.eval(); out = []
    for i in range(0, len(texts), bs):
        b = tok(texts[i:i + bs], truncation=True, max_length=max_len, padding=True, return_tensors="pt")
        h = enc(**b).last_hidden_state
        m = b["attention_mask"].unsqueeze(-1).float()
        out.append(((h * m).sum(1) / m.sum(1)).numpy())
    return np.vstack(out)


def frozen(rows, out, max_len):
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import f1_score
    tok = AutoTokenizer.from_pretrained(MODEL); enc = AutoModel.from_pretrained(MODEL)
    train, dev = split(rows)
    t0 = time.time()
    Xtr = embed([t for t, _ in train], tok, enc, max_len); ytr = [y for _, y in train]
    Xdv = embed([t for t, _ in dev], tok, enc, max_len); ydv = [y for _, y in dev]
    clf = LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0).fit(Xtr, ytr)
    f1 = f1_score(ydv, clf.predict(Xdv), average="macro", labels=list(range(len(LABELS))), zero_division=0)
    os.makedirs(out, exist_ok=True)
    pickle.dump(clf, open(os.path.join(out, "logreg.pkl"), "wb"))
    json.dump({"mode": "frozen", "base": MODEL, "labels": LABELS, "max_len": max_len,
               "dev_macro_f1_weak": f1, "train_seconds": round(time.time() - t0)},
              open(os.path.join(out, "meta.json"), "w"), indent=2)
    print(f"saved frozen-embedding classifier to {out} (dev macro-F1 {f1:.3f}, {time.time()-t0:.0f}s)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clauses_json"); ap.add_argument("weak_json")
    ap.add_argument("--gold", help="CSV of hand-labelled clauses (excluded from training)")
    ap.add_argument("--mode", choices=["finetune", "frozen"], default="finetune")
    ap.add_argument("--out", default="model_ft")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--bs", type=int, default=8)
    ap.add_argument("--max_len", type=int, default=256)
    ap.add_argument("--threads", type=int, default=os.cpu_count())
    ap.add_argument("--seed", type=int, default=13)
    a = ap.parse_args()
    torch.set_num_threads(a.threads); set_seed(a.seed)
    rows = load_data(a.clauses_json, a.weak_json, a.gold)
    if a.mode == "finetune":
        finetune(rows, a.out, a.epochs, a.lr, a.bs, a.max_len)
    else:
        frozen(rows, a.out, a.max_len)


if __name__ == "__main__":
    main()
