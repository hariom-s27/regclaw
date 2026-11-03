"""
eval_pageindex.py — page-level retrieval metrics + Ragas (0.4.x collections API) for the PageIndex agent.

Metric set (report): judge-free page metrics hit / recall / precision (over FETCHED or CITED pages,
--retrieved) and Ragas Faithfulness, AnswerRelevancy, AnswerCorrectness.

Ragas wiring (R22, ragas==0.4.3; ADR 0029 draft — Azure v1 is the judge route):
  - collections API only (ragas.metrics.collections, .ascore(...).value); never evaluate() or the
    legacy metrics (legacy answer relevancy sends n=3 + temperature 0.3)               [R1]
  - judge: AsyncOpenAI(base_url=<AZURE_OPENAI_ENDPOINT>/openai/v1/, api_key=env)
           -> llm_factory(<judge deployment>, client=that)  (default provider "openai", which
           maps max_tokens -> max_completion_tokens for reasoning models; NOT provider="litellm")
    embeddings: embedding_factory("openai", model="text-embedding-3-small", client=that) [R2]
  - judge max_tokens 8192; reasoning_effort="low" only for gpt-5* / o<digit>* judges;
    the judge is REQUIRED (--judge or judge_model in pi_settings.yaml), no default     [R3]
  - deployment names must be exactly one of pi_common.KNOWN_AZURE_DEPLOYMENTS           [D2]
Signatures read from the ragas 0.4.3 wheel:
  Faithfulness(llm).ascore(user_input, response, retrieved_contexts)
  AnswerRelevancy(llm, embeddings, strictness=3).ascore(user_input, response)
  AnswerCorrectness(llm, embeddings, weights=[0.75, 0.25], beta=1.0).ascore(user_input, response, reference)

Outcome rules:
  - R4  a judge/metric error (exception, NaN, None) on an item WITH an answer invalidates the run:
        raw rows are still written, n/a counts per metric are printed, Ragas means are withheld
        and the exit code is 2.
  - R5  each system output is judged --judge-repeats times (default 3 from settings);
        tables show mean [min-max] per item and metric.
  - R6  unanswerable items (gold_pages == []) are excluded from Ragas means and from the page
        metrics (no gold pages to hit); they are counted and listed.
  - R7  an agent failure (status "failed:<reason>": max_tokens/empty after one retry, or max
        turns) is a SYSTEM OUTCOME: the item stays in the set; hit = recall = 0, precision over
        the fetched pages (n/a if none); AnswerCorrectness = 0 by definition; Faithfulness and
        AnswerRelevancy are not computed (no answer) and counted. Agent failures are listed and
        do NOT change the exit code.

Install (pins, R22 D1): pip install -r pageindex/requirements_partII.txt

golden.json format (one object per query):
  [{"id": "gtp-001", "question": "...", "reference": "one-sentence ground-truth answer",
    "gold_pages": [12, 13]}]          # 1-based PDF page indices; [] = unanswerable

Your system must expose a function  run(question: str) -> dict  returning:
  {"answer": str,
   "fetched_pages": [int, ...],       # every page the agent pulled from the PDF
   "page_text": {page: str, ...},     # text of each fetched page (what the answer was grounded on)
   "cited_pages": [int, ...],         # optional: pages cited in the final answer
   "status": "ok" | "failed:<why>",   # optional, default "ok"
   "timings": {"navigate": s, ...}}   # optional: per-stage seconds

Usage:
  python eval_pageindex.py --golden golden.json --system my_agent:run --runs 3 --judge gpt-5-mini
  python eval_pageindex.py --golden golden.json --dry-run --no-ragas      # offline smoke test
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pageindex"))
from pi_common import (azure_credentials, azure_v1_base_url, check_deployment_names,  # noqa: E402
                       is_reasoning_model, load_settings)

RAGAS_METRICS = ["faithfulness", "answer_relevancy", "answer_correctness"]
PAGE_METRICS = ["hit", "recall", "precision"]
JUDGE_MAX_TOKENS = 8192
EXIT_JUDGE_ERRORS = 2


# ---------------------------------------------------------------- page-level metrics (by hand)
def page_metrics(retrieved: list[int], gold: list[int], failed: bool = False) -> dict:
    """Set-based retrieval metrics over pages. k = number of pages the agent retrieved.
    gold == []  -> unanswerable: hit/recall/precision are n/a (None).
    failed      -> agent failure: hit = recall = 0; precision over retrieved pages, n/a if none."""
    R, G = set(retrieved), set(gold)
    if not G:
        return {"k": len(R), "hit": None, "recall": None, "precision": None}
    hits = R & G
    if failed:
        return {"k": len(R), "hit": 0.0, "recall": 0.0,
                "precision": len(hits) / len(R) if R else None}
    return {
        "k": len(R),
        "hit": 1.0 if hits else 0.0,                       # at least one gold page retrieved
        "recall": len(hits) / len(G),                      # share of gold pages found
        "precision": len(hits) / len(R) if R else 0.0,     # share of retrieved pages that are gold
    }


# ---------------------------------------------------------------- system under test
def load_system(spec: str):
    module, fn = spec.split(":")
    return getattr(importlib.import_module(module), fn)


def fake_system(question: str) -> dict:
    """Deterministic stand-in so the harness can be tested without the agent or API keys."""
    time.sleep(0.05)
    return {"answer": f"Stub answer to: {question}",
            "fetched_pages": [12, 13, 14],
            "page_text": {12: "stub page 12", 13: "stub page 13", 14: "stub page 14"},
            "cited_pages": [12], "status": "ok",
            "timings": {"navigate": 0.03, "fetch": 0.02}}


# ---------------------------------------------------------------- Ragas setup (collections API)
def judge_kwargs(judge: str) -> dict:
    """R3: 8192 max tokens; reasoning_effort only for reasoning models (by name)."""
    kw = {"max_tokens": JUDGE_MAX_TOKENS}
    if is_reasoning_model(judge):
        kw["reasoning_effort"] = "low"
    return kw


def azure_async_client():
    """AsyncOpenAI on the Azure v1 surface; endpoint and key from env at call time only."""
    from openai import AsyncOpenAI
    endpoint, key = azure_credentials()
    return AsyncOpenAI(base_url=azure_v1_base_url(endpoint), api_key=key)


def build_ragas(judge: str, embedding_model: str = "text-embedding-3-small", client=None) -> dict:
    check_deployment_names({"judge": judge, "embedding_model": embedding_model})   # D2, before any client
    from ragas.embeddings.base import embedding_factory
    from ragas.llms import llm_factory
    from ragas.metrics.collections import AnswerCorrectness, AnswerRelevancy, Faithfulness

    client = client or azure_async_client()
    llm = llm_factory(judge, client=client, **judge_kwargs(judge))   # provider stays "openai" (R2)
    emb = embedding_factory("openai", model=embedding_model, client=client)
    return {
        "faithfulness": (Faithfulness(llm=llm),
                         lambda s: dict(user_input=s["q"], response=s["a"], retrieved_contexts=s["ctx"])),
        "answer_relevancy": (AnswerRelevancy(llm=llm, embeddings=emb),
                             lambda s: dict(user_input=s["q"], response=s["a"])),
        "answer_correctness": (AnswerCorrectness(llm=llm, embeddings=emb),
                               lambda s: dict(user_input=s["q"], response=s["a"], reference=s["ref"])),
    }


async def score_sample(metrics: dict, sample: dict) -> tuple[dict, dict]:
    """-> (scores {metric: float|None}, errors {metric: reason}). Never turns an error into 0."""
    async def one(name, metric, kw):
        try:
            value = (await metric.ascore(**kw(sample))).value
        except Exception as e:  # reported, never silently counted
            return name, None, f"{type(e).__name__}: {e}"
        if value is None:
            return name, None, "returned None"
        try:
            value = float(value)
        except (TypeError, ValueError):
            return name, None, f"non-numeric value {value!r}"
        if math.isnan(value):
            return name, None, "returned NaN"
        return name, value, None
    results = await asyncio.gather(*(one(n, m, kw) for n, (m, kw) in metrics.items()))
    return {n: v for n, v, _ in results}, {n: e for n, _, e in results if e}


# ---------------------------------------------------------------- helpers
def contexts_from(out: dict) -> list[str]:
    """One context string per fetched page, in the order the system reports them."""
    return [f"[page {p}]\n{out['page_text'][p]}" for p in out.get("fetched_pages", [])
            if p in out.get("page_text", {})]


def fmt(vals: list) -> str:
    vals = [v for v in vals if v is not None]
    if not vals:
        return "n/a"
    lo, hi, mu = min(vals), max(vals), statistics.mean(vals)
    return f"{mu:.2f}" if hi - lo < 0.005 else f"{mu:.2f} [{lo:.2f}-{hi:.2f}]"


def pct(vals: list[float], q: float) -> float:
    s = sorted(vals)
    i = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return s[i]


def validate_golden(golden: list) -> None:
    ids = set()
    for g in golden:
        missing = {"id", "question", "reference", "gold_pages"} - set(g)
        if missing:
            raise ValueError(f"golden item {g.get('id')!r} lacks {sorted(missing)}")
        if not all(isinstance(p, int) and p >= 1 for p in g["gold_pages"]):
            raise ValueError(f"golden item {g['id']!r}: gold_pages must be 1-based ints")
        if g["id"] in ids:
            raise ValueError(f"duplicate golden id {g['id']!r}")
        ids.add(g["id"])


# ---------------------------------------------------------------- one item
def evaluate_item(g: dict, out: dict, metrics: dict | None, judge_repeats: int,
                  retrieved_mode: str, run: int, latency: float) -> list[dict]:
    status = out.get("status", "ok")
    failed = status != "ok"
    unanswerable = not g["gold_pages"]
    retrieved = out.get("cited_pages", []) if retrieved_mode == "cited" else out.get("fetched_pages", [])
    pm = page_metrics(retrieved, g["gold_pages"], failed=failed)
    sample = {"q": g["question"], "a": out.get("answer", ""), "ref": g["reference"], "ctx": contexts_from(out)}
    rows = []
    for jr in range(judge_repeats if metrics else 1):
        scores, errors, note = {}, {}, None
        if metrics is not None:
            if unanswerable:
                scores, note = {m: None for m in RAGAS_METRICS}, "unanswerable"
            elif failed:   # system outcome: no answer to judge
                scores = {"faithfulness": None, "answer_relevancy": None, "answer_correctness": 0.0}
                note = "agent_failure"
            else:
                scores, errors = asyncio.run(score_sample(metrics, sample))
                for name, why in errors.items():
                    print(f"  ! {g['id']} run {run} repeat {jr + 1}: {name} failed: {why}", file=sys.stderr)
        rows.append({"run": run, "judge_repeat": jr + 1, "id": g["id"], "status": status,
                     "unanswerable": unanswerable, "ragas_note": note, "judge_errors": errors,
                     "retrieved": sorted(set(retrieved)), "gold": g["gold_pages"],
                     **pm, **scores, "latency_s": latency, "timings": out.get("timings", {})})
    return rows


def counts(rows: list[dict], with_ragas: bool) -> dict:
    first = [r for r in rows if r["judge_repeat"] == 1]
    out = {
        "unanswerable_ids": sorted({r["id"] for r in first if r["unanswerable"]}),
        "agent_failures": [{"run": r["run"], "id": r["id"], "status": r["status"]}
                           for r in first if r["status"] != "ok"],
    }
    if with_ragas:
        out["ragas_na"] = {m: {
            "judge_error": sum(1 for r in rows if m in r["judge_errors"]),
            "not_computed_agent_failure": sum(1 for r in rows if r["ragas_note"] == "agent_failure"
                                              and r.get(m) is None),
            "excluded_unanswerable": sum(1 for r in rows if r["ragas_note"] == "unanswerable"),
        } for m in RAGAS_METRICS}
        out["judge_errors_total"] = sum(v["judge_error"] for v in out["ragas_na"].values())
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ragas_version() -> str:
    from importlib import metadata
    try:
        return metadata.version("ragas")
    except metadata.PackageNotFoundError:
        return "not installed"


# ---------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", required=True)
    ap.add_argument("--system", help="module:function returning the dict described in the docstring")
    ap.add_argument("--dry-run", action="store_true", help="use the stub system")
    ap.add_argument("--runs", type=int, default=3, help="full end-to-end repeats (system + judge)")
    ap.add_argument("--judge-repeats", type=int, default=None,
                    help="re-score each system output this many times (default: judge_repeats in settings, 3)")
    ap.add_argument("--retrieved", choices=["fetched", "cited"], default="fetched",
                    help="which page set counts as 'retrieved' for page metrics")
    ap.add_argument("--judge", help="Azure judge deployment (REQUIRED for Ragas unless judge_model is set): "
                                    "gpt-5-mini, gpt-4.1-mini or o4-mini")
    ap.add_argument("--settings", default=str(ROOT / "pageindex" / "pi_settings.yaml"))
    ap.add_argument("--no-ragas", action="store_true")
    ap.add_argument("--out", default="eval_results.json")
    args = ap.parse_args(argv)

    s = load_settings(Path(args.settings))
    judge = args.judge or s.judge_model
    repeats = args.judge_repeats if args.judge_repeats is not None else s.judge_repeats
    if not args.no_ragas and not judge:
        ap.error("no judge: pass --judge <deployment> or set judge_model in pi_settings.yaml "
                 "(there is deliberately no default), or use --no-ragas")
    if repeats < 1 or args.runs < 1:
        ap.error("--runs and --judge-repeats must be >= 1")
    if not args.dry_run and not args.system:
        ap.error("--system module:function is required unless --dry-run")

    golden_path = Path(args.golden)
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    validate_golden(golden)
    system = fake_system if args.dry_run else load_system(args.system)
    metrics = None if args.no_ragas else build_ragas(judge, s.embedding_model)

    rows = []  # one row per (run, query, judge_repeat)
    for run in range(1, args.runs + 1):
        print(f"\n=== run {run}/{args.runs} ===")
        for g in golden:
            t0 = time.perf_counter()
            out = system(g["question"])
            latency = time.perf_counter() - t0          # end-to-end, wall clock, excludes judging
            item_rows = evaluate_item(g, out, metrics, repeats, args.retrieved, run, latency)
            rows.extend(item_rows)
            r0 = item_rows[0]
            tag = "" if r0["status"] == "ok" else f" [{r0['status']}]"
            tag += " [unanswerable]" if r0["unanswerable"] else ""
            print(f"  {g['id']}: pages={r0['retrieved']} gold={g['gold_pages']} "
                  f"P={fmt([r0['precision']])} R={fmt([r0['recall']])} latency={latency:.2f}s{tag}")

    out_path = Path(args.out)
    out_path.write_text(json.dumps(rows, indent=2))
    c = counts(rows, metrics is not None)
    invalid = metrics is not None and c["judge_errors_total"] > 0
    exit_code = EXIT_JUDGE_ERRORS if invalid else 0

    # ---- per-query table: mean [min-max] across all runs/judge repeats
    ragas_cols = [] if metrics is None else RAGAS_METRICS
    cols = PAGE_METRICS + ragas_cols + ["latency_s"]
    short = {"answer_relevancy": "ans_rel", "answer_correctness": "ans_corr", "faithfulness": "faith",
             "latency_s": "latency"}
    print(f"\nPage metrics over {args.retrieved.upper()} pages")
    head = ["id"] + [short.get(col, col) for col in cols]
    print(" | ".join(head))
    print(" | ".join("---" for _ in head))
    for g in golden:
        r = [x for x in rows if x["id"] == g["id"]]
        print(" | ".join([g["id"]] + [fmt([x.get(col) for x in r]) for col in cols]))

    # ---- summary: macro-average per run, then mean and range across runs
    print("\nSUMMARY (macro-average over queries; range across runs)")
    for col in cols:
        if invalid and col in RAGAS_METRICS:
            print(f"  {col:20s} WITHHELD (judge errors; run invalid)")
            continue
        per_run = []
        for run in range(1, args.runs + 1):
            v = [x.get(col) for x in rows if x["run"] == run and x.get(col) is not None]
            if v:
                per_run.append(statistics.mean(v))
        print(f"  {col:20s} {fmt(per_run)}")
    lat = [x["latency_s"] for x in rows if x["judge_repeat"] == 1]
    print(f"  latency P50/P95 (s)  {pct(lat, .5):.2f} / {pct(lat, .95):.2f}   (n={len(lat)} calls)")

    print(f"\nUnanswerable items (gold_pages == [], excluded from means): "
          f"{len(c['unanswerable_ids'])} {c['unanswerable_ids']}")
    print(f"Agent failures (system outcome; hit=recall=0, ans_corr=0): {len(c['agent_failures'])}")
    for f in c["agent_failures"]:
        print(f"  run {f['run']} {f['id']}: {f['status']}")
    if metrics is not None:
        print("\nRagas n/a per metric (rows): judge_error | not_computed_agent_failure | excluded_unanswerable")
        for m, v in c["ragas_na"].items():
            print(f"  {m:20s} {v['judge_error']} | {v['not_computed_agent_failure']} | {v['excluded_unanswerable']}")
    if invalid:
        print(f"\nRUN INVALID (R4): {c['judge_errors_total']} judge/metric error(s) on answered items. "
              "Fix and re-run; no Ragas means are reported.", file=sys.stderr)

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "golden": str(golden_path), "golden_sha256": _sha256(golden_path), "n_items": len(golden),
        "system": "dry-run stub" if args.dry_run else args.system,
        "runs": args.runs, "judge_repeats": repeats if metrics else None, "retrieved": args.retrieved,
        "ragas": None if metrics is None else {
            "version": _ragas_version(), "api": "ragas.metrics.collections", "metrics": RAGAS_METRICS,
            "judge": judge, "judge_kwargs": judge_kwargs(judge), "embedding_model": s.embedding_model,
            "route": "azure_openai_v1"},
        "counts": c, "valid": not invalid, "exit_code": exit_code,
    }
    manifest_path = out_path.with_name(out_path.stem + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nRaw rows written to {out_path}; run manifest to {manifest_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
