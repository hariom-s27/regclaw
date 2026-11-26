"""Whole-document (WD) S11 baseline: all physical PDF pages in ascending order with headers."""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "pageindex") not in sys.path:
    sys.path.insert(0, str(ROOT / "pageindex"))

from eval.s11.answer import format_page_blocks, run_wd_answer  # noqa: E402
from eval.s11.ledger import CostLedger, UsageLogger, write_once_json  # noqa: E402
from pi_common import Settings, fetch_pages, load_settings, page_count  # noqa: E402


def extract_all_pages(pdf_path: Path | str) -> tuple[dict[int, str], list[int]]:
    """Load every 1-based physical PDF page in ascending order and list empty pages."""
    total = page_count(pdf_path)
    raw = fetch_pages(pdf_path, list(range(1, total + 1)))
    all_pages = {p: raw.get(p, "") for p in range(1, total + 1)}
    empty_pages = [p for p in range(1, total + 1) if not (all_pages[p] or "").strip()]
    return all_pages, empty_pages


def build_wd_document_meta(all_pages: dict[int, str]) -> dict[str, Any]:
    """Build WD formatted multi-page block string (keeping empty headers) and page metadata."""
    ordered = sorted(int(p) for p in all_pages)
    normalized = {p: all_pages[p] for p in ordered}
    empty_pages = [p for p in ordered if not (normalized[p] or "").strip()]
    non_empty_pages = [p for p in ordered if p not in set(empty_pages)]
    return {
        "total_pages": len(ordered),
        "page_numbers": ordered,
        "empty_pages": empty_pages,
        "non_empty_pages": non_empty_pages,
        "document_text": format_page_blocks(normalized),
    }


def run_wd(
    question_id: str,
    question: str,
    *,
    all_pages: dict[int, str] | None = None,
    pdf_path: Path | str | None = None,
    s: Settings | None = None,
    ledger: CostLedger,
    usage_log: UsageLogger,
    out_dir: Path | str | None = None,
    meta_out_dir: Path | str | None = None,
    sdk_client: Any = None,
    verifier_sdk_client: Any = None,
) -> dict[str, Any]:
    """Run WD answer pipeline on all PDF pages; returns the exact 16-key S11 result record."""
    if all_pages is None:
        if pdf_path is None:
            raise ValueError("run_wd requires either all_pages or pdf_path")
        all_pages, _ = extract_all_pages(pdf_path)

    s = s or load_settings()
    if meta_out_dir is not None:
        meta = build_wd_document_meta(all_pages)
        write_once_json(
            Path(meta_out_dir) / f"{question_id}.json",
            {
                "question_id": question_id,
                "total_pages": meta["total_pages"],
                "empty_pages": meta["empty_pages"],
                "non_empty_pages": meta["non_empty_pages"],
            },
        )

    return run_wd_answer(
        question_id,
        question,
        all_pages,
        s=s,
        ledger=ledger,
        usage_log=usage_log,
        out_dir=out_dir,
        sdk_client=sdk_client,
        verifier_sdk_client=verifier_sdk_client,
    )


__all__ = [
    "build_wd_document_meta",
    "extract_all_pages",
    "run_wd",
]
