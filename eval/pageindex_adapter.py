"""Adapter: makes pi_agent.answer_question() match the run(question) contract of eval_pageindex.py.

Why: eval_pageindex.py (R7) expects run(question) -> {answer, fetched_pages, page_text{page: text}, ...}
     pi_agent.py (R5) exposes answer_question(question, family, ...) -> {answer, fetched_pages, contexts[...], ...}

Run from the rebuild/pageindex folder (so pi_settings.yaml and the store/ paths resolve):
    cd rebuild/pageindex
    PI_FAMILY=gtp python ../eval/eval_pageindex.py --golden ../eval/golden.json \
        --system pageindex_adapter:run --runs 3

Note: pi_agent returns fetched_pages SORTED, not in fetch order. Page-level hit/recall/precision
are set-based so they are unaffected; the reported Ragas metrics (faithfulness, answer relevancy,
answer correctness) do not depend on context order. A failed agent item (R22 R7) arrives with
status "failed:<reason>" and answer "" and is scored as a system outcome by eval_pageindex.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "pageindex"))
from eval.s11.ledger import CostLedger, UsageLogger, default_s11_paths  # noqa: E402
from pi_agent import answer_question  # noqa: E402

FAMILY = os.environ.get("PI_FAMILY", "gtp")
VERSION = os.environ.get("PI_VERSION") or None

_LEDGER: CostLedger | None = None
_USAGE_LOG: UsageLogger | None = None


def configure(*, ledger: CostLedger | None = None, usage_log: UsageLogger | None = None) -> None:
    """Override default data/runs/s11/ driver ledger and usage_log paths."""
    global _LEDGER, _USAGE_LOG
    _LEDGER = ledger
    _USAGE_LOG = usage_log


def _driver_logs() -> tuple[CostLedger, UsageLogger]:
    default_ledger, default_usage, _ = default_s11_paths(_REPO_ROOT)
    return (
        _LEDGER if _LEDGER is not None else CostLedger(default_ledger),
        _USAGE_LOG if _USAGE_LOG is not None else UsageLogger(default_usage),
    )


def run(question: str) -> dict:
    ledger, usage_log = _driver_logs()
    r = answer_question(question, FAMILY, VERSION, ledger=ledger, usage_log=usage_log)
    return {
        "answer": r["answer"],
        "answer_pre_repair": r.get("answer_pre_repair", r["answer"]),
        "fetched_pages": r["fetched_pages"],
        "page_text": dict(zip(r["fetched_pages"], r["contexts"])),
        "cited_pages": r["cited_pages"],
        "timings": {"total": r["latency_s"]},
        "usage": r["usage"],
        "repaired": r["repaired"],
        "dropped_sentences": r.get("dropped_sentences", []),
        "models": r.get("models", {}),
        "timeouts": r.get("timeouts", {}),
        # R22 R7: "ok" or "failed:<max_tokens|empty_answer|max_turns>" (a system outcome)
        "status": r.get("status", "ok"),
        "cited_not_fetched": r.get("cited_not_fetched", []),
        "retries": r.get("retries", 0),
    }
