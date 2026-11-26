"""Token pre-counting helper for exact S11 WD and first-turn PI requests via pi_client.

Usage (no paid calls; count_tokens logs 0 USD and never reserves against CostLedger):
  .venv\\Scripts\\python -m eval.s11.precount --pdf <pdf> --tree <tree.json> --question "..."
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import anthropic

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "pageindex") not in sys.path:
    sys.path.insert(0, str(ROOT / "pageindex"))

from eval.s11.answer import build_wd_request  # noqa: E402
from eval.s11.ledger import CostLedger, UsageLogger, default_s11_paths, write_once_json  # noqa: E402
from pi_agent import TOOLS, build_pi_system_text  # noqa: E402
from pi_client import AnthropicRetryClient  # noqa: E402
from pi_common import Settings, fetch_pages, load_settings, load_tree, page_count  # noqa: E402


def build_wd_precount_request(
    pdf_path: Path | str,
    question: str,
    *,
    s: Settings | None = None,
) -> dict[str, Any]:
    """Build exact WD count_tokens payload from all PDF pages."""
    s = s or load_settings()
    total = page_count(pdf_path)
    all_pages = fetch_pages(pdf_path, list(range(1, total + 1)))
    wd = build_wd_request(question, all_pages, s=s)
    return {
        "model": wd["model"],
        "system": wd["system"],
        "messages": wd["messages"],
        "output_config": wd["output_config"],
    }


def build_pi_first_precount_request(
    tree_path: Path | str,
    question: str,
    *,
    s: Settings | None = None,
    title: str = "GTP Guideline",
    version: str = "2019",
    page_count_val: int = 116,
    doc_description: str = "",
) -> dict[str, Any]:
    """Build exact first-turn PI navigator count_tokens payload from a tree outline JSON."""
    s = s or load_settings()
    tree = load_tree(tree_path)
    desc = doc_description or str(tree.get("doc_description") or "")
    rec = SimpleNamespace(
        title=title,
        version=version,
        page_count=int(page_count_val),
        doc_description=desc,
    )
    system_text = build_pi_system_text(rec, tree["structure"], s)
    return {
        "model": s.agent_model,
        "system": [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        "tools": TOOLS,
        "tool_choice": {"type": "auto"},
        "output_config": {"effort": s.answer_effort},
        "messages": [{"role": "user", "content": question}],
    }


def count_precount_requests(
    client: AnthropicRetryClient,
    wd_req: dict[str, Any],
    pi_req: dict[str, Any],
) -> dict[str, int]:
    """Call messages.count_tokens on WD and first-turn PI requests and return input token counts."""
    wd_res = client.messages.count_tokens(**wd_req)
    pi_res = client.messages.count_tokens(**pi_req)
    return {
        "wd_input_tokens": int(wd_res.input_tokens),
        "pi_first_input_tokens": int(pi_res.input_tokens),
    }


def main(argv: list[str] | None = None) -> int:
    default_ledger, default_usage, _ = default_s11_paths()
    ap = argparse.ArgumentParser(description="Pre-count input tokens for WD and first-turn PI requests.")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--tree", required=True)
    ap.add_argument("--question", default="What is the scope of these guidelines?")
    ap.add_argument("--title", default="GTP Guideline")
    ap.add_argument("--version", default="2019")
    ap.add_argument("--settings")
    ap.add_argument("--ledger", default=str(default_ledger))
    ap.add_argument("--usage-log", default=str(default_usage))
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    s = load_settings(Path(args.settings)) if args.settings else load_settings()
    wd_req = build_wd_precount_request(args.pdf, args.question, s=s)
    total_pages = page_count(args.pdf)
    pi_req = build_pi_first_precount_request(
        args.tree,
        args.question,
        s=s,
        title=args.title,
        version=args.version,
        page_count_val=total_pages,
    )
    client = AnthropicRetryClient(
        anthropic.Anthropic(max_retries=0, timeout=s.agent_timeout_s),
        ledger=CostLedger(Path(args.ledger)),
        usage_log=UsageLogger(Path(args.usage_log)),
        lane="count_tokens",
        timeout_s=s.agent_timeout_s,
    )
    counts = count_precount_requests(client, wd_req, pi_req)
    if args.out:
        write_once_json(args.out, counts)
    print(f"wd_input_tokens={counts['wd_input_tokens']} pi_first_input_tokens={counts['pi_first_input_tokens']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
