"""parser_parity.py — offline check that PageIndex's tree text and the agent's fetch text agree.

R22 P5: PageIndex (50a71b6) builds the tree on PyPDF2 page text (get_page_tokens default),
while pi_common.fetch_pages reads PyMuPDF. If the two parsers disagree on a page, the
tree's titles/summaries describe different text from what the agent reads. This tool
REPORTS per-page similarity and the pages below a threshold; it does not choose a parser.

All page numbers are 1-based PHYSICAL PDF indices (same convention as the rest of Part II).
No network, no LLM calls.

Usage
  python parser_parity.py guidelines_GTP.pdf
  python parser_parity.py guidelines_GTP.pdf --threshold 0.95 --json parity.json
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from typing import Callable

DEFAULT_THRESHOLD = 0.90


def pypdf2_pages(pdf_path: str) -> list[str]:
    """Per-page text exactly as PageIndex's get_page_tokens(pdf_parser="PyPDF2") reads it."""
    import PyPDF2
    return [page.extract_text() or "" for page in PyPDF2.PdfReader(pdf_path).pages]


def pymupdf_pages(pdf_path: str) -> list[str]:
    """Per-page text exactly as pi_common.fetch_pages reads it (page.get_text())."""
    import pymupdf
    with pymupdf.open(pdf_path) as doc:
        return [page.get_text() for page in doc]


def normalise(text: str) -> str:
    """Collapse whitespace only: line breaks and spacing differ between parsers by design."""
    return " ".join((text or "").split())


def similarity(a: str, b: str) -> float:
    """difflib ratio on whitespace-normalised text; two empty pages count as identical."""
    a, b = normalise(a), normalise(b)
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def compare(tree_pages: list[str], fetch_pages: list[str], threshold: float = DEFAULT_THRESHOLD) -> dict:
    """Per-page comparison. A page missing on one side scores 0.0."""
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    n = max(len(tree_pages), len(fetch_pages))
    rows = []
    for i in range(n):
        a = tree_pages[i] if i < len(tree_pages) else None
        b = fetch_pages[i] if i < len(fetch_pages) else None
        ratio = 0.0 if a is None or b is None else similarity(a, b)
        rows.append({"page": i + 1, "similarity": round(ratio, 4),
                     "chars_pypdf2": None if a is None else len(normalise(a)),
                     "chars_pymupdf": None if b is None else len(normalise(b))})
    below = [r["page"] for r in rows if r["similarity"] < threshold]
    ratios = [r["similarity"] for r in rows]
    return {"threshold": threshold, "pages_pypdf2": len(tree_pages), "pages_pymupdf": len(fetch_pages),
            "page_count_match": len(tree_pages) == len(fetch_pages),
            "min_similarity": min(ratios) if ratios else None,
            "mean_similarity": round(sum(ratios) / len(ratios), 4) if ratios else None,
            "pages_below_threshold": below, "pages": rows}


def parity_report(pdf_path: str, threshold: float = DEFAULT_THRESHOLD,
                  tree_extractor: Callable[[str], list[str]] = pypdf2_pages,
                  fetch_extractor: Callable[[str], list[str]] = pymupdf_pages) -> dict:
    report = compare(tree_extractor(pdf_path), fetch_extractor(pdf_path), threshold)
    report["pdf"] = str(pdf_path)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pdf")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help=f"report pages with similarity below this (default {DEFAULT_THRESHOLD})")
    ap.add_argument("--json", help="also write the full per-page report here")
    a = ap.parse_args(argv)
    r = parity_report(a.pdf, a.threshold)
    print(f"pdf             : {r['pdf']}")
    print(f"pages           : PyPDF2 {r['pages_pypdf2']} / PyMuPDF {r['pages_pymupdf']}"
          + ("" if r["page_count_match"] else "  <- PAGE COUNT MISMATCH"))
    print(f"similarity      : mean {r['mean_similarity']}  min {r['min_similarity']}")
    print(f"below {r['threshold']:<10}: {len(r['pages_below_threshold'])} page(s) {r['pages_below_threshold'][:50]}")
    for row in r["pages"]:
        if row["page"] in r["pages_below_threshold"]:
            print(f"  p.{row['page']:<4} sim={row['similarity']:.3f} "
                  f"chars PyPDF2={row['chars_pypdf2']} PyMuPDF={row['chars_pymupdf']}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2)
    return 0   # report only: the parser choice is a human decision


if __name__ == "__main__":
    sys.exit(main())
