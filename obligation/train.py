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
import hashlib
import json
import os
import pickle
import random
import time
from collections import Counter
from pathlib import Path

import numpy as np

LABELS = ["NONE", "MAY", "SHOULD", "SHALL"]
L2I = {l: i for i, l in enumerate(LABELS)}
MODEL = "nlpaueb/legal-bert-base-uncased"


def set_seed(s: int):
    import torch
    random.seed(s); np.random.seed(s); torch.manual_seed(s)


def load_data(clauses_path, weak_path, gold_path, train_path=None, retest_path=None,
              near_dup_path=None, audit_path=None):
    clauses = {c["id"]: c for c in json.load(open(clauses_path, encoding="utf-8"))}
    weak = json.load(open(weak_path, encoding="utf-8"))
    def ids(path, key='id'):
        if not path:
            return set()
        with open(path, encoding='utf-8-sig', newline='') as stream:
            return {r[key] for r in csv.DictReader(stream)}
    gold_ids, retest_ids = ids(gold_path), ids(retest_path)
    train_ids = ids(train_path) if train_path else set(clauses)
    near_dup_ids = ids(near_dup_path, 'train_id')
    weak_ids = [w['id'] for w in weak]
    if len(weak_ids) != len(set(weak_ids)) or train_ids - set(weak_ids) or set(weak_ids) - clauses.keys():
        raise ValueError('Duplicate weak ids or unknown training/weak ids')
    allowed, excluded = set(weak_ids), {}
    # Sequential counts are disjoint; overlap counts below are not.
    for reason, blocked in [('outside_train_ids', set(weak_ids) - train_ids),
                            ('gold', gold_ids), ('retest', retest_ids),
                            ('near_duplicate', near_dup_ids),
                            ('needs_review', {w['id'] for w in weak if w['needs_review']})]:
        excluded[reason] = sorted(allowed & blocked)
        allowed -= blocked
    kept = [w for w in weak if w['id'] in allowed]
    rows = [(clauses[w['id']]['text'], L2I[w['weak_label']]) for w in kept]
    audit = {'weak_rows': len(weak), 'requested_train_ids': len(train_ids),
             'overlap_with_train_ids': {'gold': len(train_ids & gold_ids),
                                       'retest': len(train_ids & retest_ids),
                                       'near_duplicate': len(train_ids & near_dup_ids)},
             'exclusion_counts_sequential': {k: len(v) for k, v in excluded.items()},
             'excluded_ids_sequential': excluded, 'retained_ids': [w['id'] for w in kept],
             'retained_count': len(rows), 'weak_label_counts': dict(Counter(w['weak_label'] for w in kept))}
    if audit_path:
        Path(audit_path).parent.mkdir(parents=True, exist_ok=True)
        Path(audit_path).write_text(json.dumps(audit, indent=2) + '\n', encoding='utf-8')
    print('training data: ' + json.dumps({k: v for k, v in audit.items() if k not in ('excluded_ids_sequential', 'retained_ids')}))
    return rows


def split(rows, dev_frac=0.1, seed=13):
    from sklearn.model_selection import train_test_split
    return train_test_split(rows, test_size=dev_frac, random_state=seed,
                            stratify=[y for _, y in rows])


def class_weights(y):
    import torch
    counts = np.bincount(y, minlength=len(LABELS)).astype(float)
    counts[counts == 0] = 1.0
    w = len(y) / (len(LABELS) * counts)          # inverse frequency ("balanced")
    return torch.tensor(w, dtype=torch.float)


def collate(tok, max_len):
    import torch
    def f(batch):
        texts, labels = zip(*batch)
        enc = tok(list(texts), truncation=True, max_length=max_len, padding=True, return_tensors="pt")
        enc["labels"] = torch.tensor(labels)
        return enc
    return f


def dev_metrics(model, loader, loss_fn):
    import torch
    from sklearn.metrics import f1_score
    model.eval(); ys, ps, losses = [], [], []
    with torch.no_grad():
        for b in loader:
            y = b.pop("labels")
            logits = model(**b).logits
            losses.append((float(loss_fn(logits, y)), float(loss_fn.weight[y].sum())))
            ps += logits.argmax(-1).tolist(); ys += y.tolist()
    model.train()
    return (f1_score(ys, ps, average="macro", labels=list(range(len(LABELS))), zero_division=0),
            sum(loss * weight for loss, weight in losses) / sum(weight for _, weight in losses))


def finetune(rows, out, epochs, lr, bs, max_len, seed=13, epoch_limit_minutes=45):
    import torch
    from torch.utils.data import DataLoader
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL, num_labels=len(LABELS), id2label=dict(enumerate(LABELS)), label2id=L2I,
        local_files_only=True).to('cpu')
    train, dev = split(rows, seed=seed)
    loss_fn = torch.nn.CrossEntropyLoss(weight=class_weights([y for _, y in train]))
    tl = DataLoader(train, batch_size=bs, shuffle=True, collate_fn=collate(tok, max_len))
    dl = DataLoader(dev, batch_size=32, collate_fn=collate(tok, max_len))
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    total = epochs * len(tl)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min((s + 1) / max(1, int(0.1 * total)), max(0.0, (total - s) / (0.9 * total))))

    Path(out).mkdir(parents=True, exist_ok=True)
    best, t0, history, best_epoch = -1.0, time.perf_counter(), [], None
    meta = {'mode': 'finetune', 'base': MODEL, 'labels': LABELS, 'max_len': max_len,
            'epochs_requested': epochs, 'lr': lr, 'batch_size': bs, 'seed': seed,
            'device': 'cpu', 'threads': torch.get_num_threads(), 'validation_fraction': .1,
            'validation_stratified': True, 'selection_metric': 'dev_macro_f1_weak',
            'train_n': len(train), 'validation_n': len(dev),
            'train_label_counts': dict(Counter(LABELS[y] for _, y in train)),
            'validation_label_counts': dict(Counter(LABELS[y] for _, y in dev)),
            'history': history, 'stopped_after_slow_first_epoch': False}
    model.train()
    for ep in range(epochs):
        epoch_start = time.perf_counter()
        for step, b in enumerate(tl):
            y = b.pop("labels")
            loss = loss_fn(model(**b).logits, y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(); sched.step(); opt.zero_grad()
            if step % 20 == 0:
                print(f"epoch {ep+1} step {step}/{len(tl)} loss {loss.item():.4f} [{time.perf_counter()-t0:.0f}s]", flush=True)
        f1, val_loss = dev_metrics(model, dl, loss_fn)
        if f1 > best:                              # keep best epoch = simple early stopping
            best = f1
            best_epoch = ep + 1
            model.save_pretrained(out); tok.save_pretrained(out)
        seconds = time.perf_counter() - epoch_start
        history.append({'epoch': ep + 1, 'wall_seconds': seconds, 'validation_loss': val_loss,
                        'dev_macro_f1_weak': float(f1)})
        meta.update(dev_macro_f1_weak=float(best), best_epoch=best_epoch,
                    train_seconds=time.perf_counter()-t0,
                    stopped_after_slow_first_epoch=ep == 0 and seconds > epoch_limit_minutes * 60)
        Path(out, 'meta.json').write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
        print('epoch result: ' + json.dumps(history[-1]), flush=True)
        if meta['stopped_after_slow_first_epoch']:
            print('STOP: first epoch exceeded the time limit; checkpoint saved.', flush=True)
            break
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(Path(out).iterdir()) if p.is_file() and p.name != 'sha256_manifest.json'}
    Path(out, 'sha256_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f"saved best model to {out} (epoch {best_epoch}; dev macro-F1 {best:.3f}, {time.perf_counter()-t0:.0f}s)", flush=True)
    return meta


def embed(texts, tok, enc, max_len, bs=16):
    """Mean pooling over non-padding tokens (usually better than raw [CLS] for a frozen BERT)."""
    import torch
    enc.eval(); out = []
    with torch.no_grad():
        for i in range(0, len(texts), bs):
            b = tok(texts[i:i + bs], truncation=True, max_length=max_len, padding=True, return_tensors="pt")
            h = enc(**b).last_hidden_state
            m = b["attention_mask"].unsqueeze(-1).float()
            out.append(((h * m).sum(1) / m.sum(1)).numpy())
    return np.vstack(out)


def frozen(rows, out, max_len):
    from transformers import AutoModel, AutoTokenizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import f1_score
    tok = AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
    enc = AutoModel.from_pretrained(MODEL, local_files_only=True)
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


def dry_run(rows, out, seed):
    """One NumPy classification-head update; no torch, weights or tokenizer needed."""
    # Tiny fixtures need at least one validation row per present label.
    train, dev = split(rows, dev_frac=max(.1, len({y for _, y in rows}) / len(rows)), seed=seed)
    x = np.array([[len(text.split()), len(text)] for text, _ in train], dtype=float)
    x /= np.maximum(x.max(axis=0), 1)
    y = np.array([label for _, label in train])
    w = np.zeros((2, len(LABELS)))
    def probabilities(weights):
        logits = x @ weights
        exp = np.exp(logits - logits.max(axis=1, keepdims=True))
        return exp / exp.sum(axis=1, keepdims=True)
    p = probabilities(w)
    loss_before = float(-np.log(p[np.arange(len(y)), y]).mean())
    target = np.eye(len(LABELS))[y]
    w -= .1 * x.T @ (p - target) / len(y)
    loss_after = float(-np.log(probabilities(w)[np.arange(len(y)), y]).mean())
    result = {'dry_run': True, 'model_loaded': False, 'optimizer_steps': 1,
              'fixture_rows': len(rows), 'train_n': len(train), 'validation_n': len(dev),
              'loss_before': loss_before, 'loss_after': loss_after, 'seed': seed}
    Path(out).mkdir(parents=True, exist_ok=True)
    Path(out, 'dry_run.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("clauses_json"); ap.add_argument("weak_json")
    ap.add_argument("--gold", help="CSV of hand-labelled clauses (excluded from training)")
    ap.add_argument('--train-ids')
    ap.add_argument('--retest')
    ap.add_argument('--near-dup-flags')
    ap.add_argument('--audit-output')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--epoch-limit-minutes', type=float, default=45)
    ap.add_argument("--mode", choices=["finetune", "frozen"], default="finetune")
    ap.add_argument("--out", default="model_ft")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--bs", type=int, default=8)
    ap.add_argument("--max_len", type=int, default=256)
    ap.add_argument("--threads", type=int, default=os.cpu_count())
    ap.add_argument("--seed", type=int, default=13)
    a = ap.parse_args(argv)
    rows = load_data(a.clauses_json, a.weak_json, a.gold, a.train_ids, a.retest,
                     a.near_dup_flags, a.audit_output)
    if a.dry_run:
        dry_run(rows, a.out, a.seed)
        return 0
    import torch
    torch.set_num_threads(a.threads); set_seed(a.seed)
    if a.mode == "finetune":
        meta = finetune(rows, a.out, a.epochs, a.lr, a.bs, a.max_len, a.seed, a.epoch_limit_minutes)
        if meta['stopped_after_slow_first_epoch']:
            return 2
    else:
        frozen(rows, a.out, a.max_len)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
