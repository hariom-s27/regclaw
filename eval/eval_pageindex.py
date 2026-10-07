"""
eval_pageindex.py — page-level retrieval metrics + Ragas (0.4.x collections API) for the PageIndex agent.

Verified against ragas==0.4.3 (PyPI, 13 Jan 2026). Signatures used below were read from the installed
package with inspect.signature on 2026-10-07:
  Faithfulness(llm).ascore(user_input, response, retrieved_contexts)
  AnswerRelevancy(llm, embeddings, strictness=3).ascore(user_input, response)
  AnswerCorrectness(llm, embeddings, weights=[0.75, 0.25], beta=1.0).ascore(user_input, response, reference)
  ContextPrecision(llm).ascore(user_input, reference, retrieved_contexts)
  ContextRecall(llm).ascore(user_input, retrieved_contexts, reference)
  -> each returns MetricResult; the score is .value

Install (pin langchain-community: ragas 0.4.3 imports langchain_community.chat_models.vertexai,
which is missing in langchain-community 0.4.x -> ModuleNotFoundError):
  pip install "ragas==0.4.3" "langchain-community<0.4" openai python-dotenv

golden.json format (one object per query):
  [{"id": "gtp-001",
    "question": "...",
    "reference": "one-sentence ground-truth answer",
    "gold_pages": [12, 13]}]          # 1-based PDF page indices, same convention as your agent

Your system must expose a function  run(question: str) -> dict  returning:
  {"answer": str,
   "fetched_pages": [int, ...],       # every page the agent pulled from the PDF, in fetch order
   "page_text": {page: str, ...},     # text of each fetched page (what the answer was grounded on)
   "cited_pages": [int, ...],         # optional: pages cited in the final answer
   "timings": {"navigate": s, ...}}   # optional: per-stage seconds

Usage:
  python eval_pageindex.py --golden golden.json --system my_agent:run --runs 3
  python eval_pageindex.py --golden golden.json --dry-run --no-ragas      # offline smoke test
  python eval_pageindex.py --golden golden.json --system my_agent:run --runs 3 --judge-repeats 3
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import statistics
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

RAGAS_METRICS = ["faithfulness", "answer_relevancy", "answer_correctness",
                 "context_precision", "context_recall"]


# ---------------------------------------------------------------- page-level metrics (by hand)
def page_metrics(retrieved: list[int], gold: list[int]) -> dict:
    """Set-based retrieval metrics over pages. k = number of pages the agent retrieved."""
    R, G = set(retrieved), set(gold)
    hits = R & G
    return {
        "k": len(R),
        "hit": 1.0 if hits else 0.0,                       # at least one gold page retrieved
        "recall": len(hits) / len(G) if G else 0.0,        # share of gold pages found
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
            "cited_pages": [12],
            "timings": {"navigate": 0.03, "fetch": 0.02}}


# ---------------------------------------------------------------- Ragas setup (collections API)
def build_ragas(judge: str):
    from openai import AsyncOpenAI
    from ragas.embeddings.base import embedding_factory
    from ragas.llms import llm_factory
    from ragas.metrics.collections import (AnswerCorrectness, AnswerRelevancy,
                                           ContextPrecision, ContextRecall, Faithfulness)

    oai = AsyncOpenAI()
    # Embeddings stay on OpenAI regardless of judge (used by answer_relevancy + answer_correctness).
    emb = embedding_factory("openai", model="text-embedding-3-small", client=oai)

    if judge.startswith("claude"):
        from anthropic import AsyncAnthropic
        llm = llm_factory(judge, provider="anthropic", client=AsyncAnthropic())
        # ragas passes temperature=0.01 AND top_p=0.1 unchanged to Anthropic; recent Claude models
        # reject both together, so drop top_p.
        llm.model_args.pop("top_p", None)
    else:
        llm = llm_factory(judge, client=oai)

    return {
        "faithfulness": (Faithfulness(llm=llm),
                         lambda s: dict(user_input=s["q"], response=s["a"], retrieved_contexts=s["ctx"])),
        "answer_relevancy": (AnswerRelevancy(llm=llm, embeddings=emb),
                             lambda s: dict(user_input=s["q"], response=s["a"])),
        "answer_correctness": (AnswerCorrectness(llm=llm, embeddings=emb),
                               lambda s: dict(user_input=s["q"], response=s["a"], reference=s["ref"])),
        "context_precision": (ContextPrecision(llm=llm),
                              lambda s: dict(user_input=s["q"], reference=s["ref"], retrieved_contexts=s["ctx"])),
        "context_recall": (ContextRecall(llm=llm),
                           lambda s: dict(user_input=s["q"], retrieved_contexts=s["ctx"], reference=s["ref"])),
    }


async def score_sample(metrics: dict, sample: dict) -> dict:
    async def one(name, metric, kw):
        try:
            return name, float((await metric.ascore(**kw(sample))).value)
        except Exception as e:  # a failed judge call is reported, never silently counted as 0
            print(f"  ! {name} failed: {type(e).__name__}: {e}")
            return name, None
    pairs = await asyncio.gather(*(one(n, m, kw) for n, (m, kw) in metrics.items()))
    return dict(pairs)


# ---------------------------------------------------------------- helpers
def contexts_from(out: dict) -> list[str]:
    """One context string per fetched page, in fetch order (order matters for context_precision)."""
    return [f"[page {p}]\n{out['page_text'][p]}" for p in out["fetched_pages"] if p in out["page_text"]]


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


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", required=True)
    ap.add_argument("--system", help="module:function returning the dict described in the docstring")
    ap.add_argument("--dry-run", action="store_true", help="use the stub system")
    ap.add_argument("--runs", type=int, default=3, help="full end-to-end repeats (system + judge)")
    ap.add_argument("--judge-repeats", type=int, default=1,
                    help="re-score each system output this many times to isolate judge variance")
    ap.add_argument("--retrieved", choices=["fetched", "cited"], default="fetched",
                    help="which page set counts as 'retrieved' for page metrics")
    ap.add_argument("--judge", default="gpt-4o-mini", help="evaluator model, e.g. gpt-4o-mini or claude-haiku-4-5")
    ap.add_argument("--no-ragas", action="store_true")
    ap.add_argument("--out", default="eval_results.json")
    args = ap.parse_args()

    golden = json.loads(Path(args.golden).read_text())
    system = fake_system if args.dry_run else load_system(args.system)
    metrics = None if args.no_ragas else build_ragas(args.judge)

    rows = []  # one row per (run, query, judge_repeat)
    for run in range(1, args.runs + 1):
        print(f"\n=== run {run}/{args.runs} ===")
        for g in golden:
            t0 = time.perf_counter()
            out = system(g["question"])
            latency = time.perf_counter() - t0          # end-to-end, wall clock, excludes judging

            retrieved = out.get("cited_pages", []) if args.retrieved == "cited" else out["fetched_pages"]
            pm = page_metrics(retrieved, g["gold_pages"])
            sample = {"q": g["question"], "a": out["answer"], "ref": g["reference"], "ctx": contexts_from(out)}

            for jr in range(args.judge_repeats):
                rm = asyncio.run(score_sample(metrics, sample)) if metrics else {}
                rows.append({"run": run, "judge_repeat": jr + 1, "id": g["id"],
                             "retrieved": sorted(set(retrieved)), "gold": g["gold_pages"],
                             **pm, **rm, "latency_s": latency, "timings": out.get("timings", {})})
            print(f"  {g['id']}: pages={sorted(set(retrieved))} gold={g['gold_pages']} "
                  f"P={pm['precision']:.2f} R={pm['recall']:.2f} latency={latency:.2f}s")

    Path(args.out).write_text(json.dumps(rows, indent=2))

    # ---- per-query table: mean [min-max] across all runs/judge repeats
    cols = ["hit", "recall", "precision"] + ([] if args.no_ragas else RAGAS_METRICS) + ["latency_s"]
    short = {"answer_relevancy": "ans_rel", "answer_correctness": "ans_corr", "faithfulness": "faith",
             "context_precision": "ctx_prec", "context_recall": "ctx_rec", "latency_s": "latency"}
    head = ["id"] + [short.get(c, c) for c in cols]
    print("\n" + " | ".join(head))
    print(" | ".join("---" for _ in head))
    for g in golden:
        r = [x for x in rows if x["id"] == g["id"]]
        print(" | ".join([g["id"]] + [fmt([x.get(c) for x in r]) for c in cols]))

    # ---- summary: macro-average per run, then mean and range across runs
    print("\nSUMMARY (macro-average over queries; range across runs)")
    for c in cols:
        per_run = []
        for run in range(1, args.runs + 1):
            v = [x.get(c) for x in rows if x["run"] == run and x.get(c) is not None]
            if v:
                per_run.append(statistics.mean(v))
        print(f"  {c:20s} {fmt(per_run)}")
    lat = [x["latency_s"] for x in rows if x["judge_repeat"] == 1]
    print(f"  latency P50/P95 (s)  {pct(lat, .5):.2f} / {pct(lat, .95):.2f}   (n={len(lat)} calls)")
    print(f"\nRaw rows written to {args.out}")


if __name__ == "__main__":
    main()
