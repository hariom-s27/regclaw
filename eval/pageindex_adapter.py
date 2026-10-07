"""Adapter: makes pi_agent.answer_question() match the run(question) contract of eval_pageindex.py.

Why: eval_pageindex.py (R7) expects run(question) -> {answer, fetched_pages, page_text{page: text}, ...}
     pi_agent.py (R5) exposes answer_question(question, family, ...) -> {answer, fetched_pages, contexts[...], ...}

Run from the rebuild/pageindex folder (so pi_settings.yaml and the store/ paths resolve):
    cd rebuild/pageindex
    PI_FAMILY=gtp python ../eval/eval_pageindex.py --golden ../eval/golden.json \
        --system pageindex_adapter:run --runs 3

Note: pi_agent returns fetched_pages SORTED, not in fetch order. Page-level hit/recall/precision
are set-based so they are unaffected; Ragas context_precision is order-sensitive and will see
page order, not the order the agent read them in. State this if you report context_precision.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pageindex"))
from pi_agent import answer_question  # noqa: E402

FAMILY = os.environ.get("PI_FAMILY", "gtp")
VERSION = os.environ.get("PI_VERSION") or None


def run(question: str) -> dict:
    r = answer_question(question, FAMILY, VERSION)
    return {
        "answer": r["answer"],
        "fetched_pages": r["fetched_pages"],
        "page_text": dict(zip(r["fetched_pages"], r["contexts"])),
        "cited_pages": r["cited_pages"],
        "timings": {"total": r["latency_s"]},
        "usage": r["usage"],
        "repaired": r["repaired"],
    }
