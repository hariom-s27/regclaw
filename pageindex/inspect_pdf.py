"""inspect_pdf.py — check a PDF before PageIndex (R2 follow-up) and find gold pages for the golden set.

Reports: page count, text layer (OCR needed?), embedded bookmarks, page labels, printed-vs-PDF page
offset, and the pages that look like a printed table of contents. All page numbers are 1-based
PHYSICAL PDF indices — the same convention as PageIndex trees, pi_agent citations and golden.json.

Usage
  python inspect_pdf.py guidelines_GTP.pdf                     # summary
  python inspect_pdf.py guidelines_GTP.pdf --dump-toc          # + bookmarks and printed TOC text
  python inspect_pdf.py guidelines_GTP.pdf --find "GTAEC" "secretariat" "nucleic acid"
  python inspect_pdf.py guidelines_GTP.pdf --page 23           # print one page's text to read it
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter

import pymupdf  # PyMuPDF >= 1.24 (import name 'pymupdf'; 'fitz' is the deprecated alias)

EMPTY_CHARS = 25          # a page with fewer characters than this counts as "no text"
OCR_THRESHOLD = 0.5       # more than half the pages empty -> OCR before indexing
TOC_LINE = re.compile(r"(\.{3,}|…{2,}|\s{2,})\s*\d{1,4}\s*$|\s\d{1,4}\s*$")
FOOTER_NUM = re.compile(r"^\s*(?:page\s+)?(\d{1,4})(?:\s+of\s+\d+)?\s*$", re.I)


def text_stats(doc) -> tuple[list[int], int]:
    lens = [len(p.get_text().strip()) for p in doc]
    return lens, sum(1 for n in lens if n < EMPTY_CHARS)


def printed_number(page) -> int | None:
    """Look for a bare page number in the first/last 3 non-empty lines (header or footer)."""
    lines = [l for l in page.get_text().splitlines() if l.strip()]
    for line in lines[-3:][::-1] + lines[:3]:
        if m := FOOTER_NUM.match(line):
            return int(m.group(1))
    return None


def page_offset(doc) -> tuple[int | None, int]:
    """Most common (PDF index - printed number), over pages where a number was found."""
    diffs = [i + 1 - n for i, p in enumerate(doc) if (n := printed_number(p)) is not None]
    if not diffs:
        return None, 0
    off, votes = Counter(diffs).most_common(1)[0]
    return off, votes


def toc_pages(doc, scan: int = 20) -> list[int]:
    """Pages in the first `scan` pages where >= 5 lines end in a page number (dot leaders etc.)."""
    out = []
    for i, p in enumerate(doc):
        if i >= scan:
            break
        lines = [l for l in p.get_text().splitlines() if l.strip()]
        hits = sum(1 for l in lines if TOC_LINE.search(l) and re.search(r"[A-Za-z]{3,}", l))
        if hits >= 5:
            out.append(i + 1)
    return out


def labels(doc) -> list[str]:
    try:
        return [p.get_label() or "" for p in doc]
    except Exception:
        return []


def find(doc, terms: list[str], width: int = 80) -> None:
    for t in terms:
        rx = re.compile(re.escape(t), re.I)
        pages = []
        for i, p in enumerate(doc):
            txt = " ".join(p.get_text().split())
            if m := rx.search(txt):
                a = max(0, m.start() - width // 2)
                pages.append((i + 1, len(rx.findall(txt)), txt[a:a + width]))
        print(f"\n== '{t}': {len(pages)} page(s)")
        for pg, n, snip in pages:
            print(f"  p.{pg:<4} x{n:<3} …{snip}…")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--dump-toc", action="store_true")
    ap.add_argument("--find", nargs="+", metavar="TERM")
    ap.add_argument("--page", type=int, help="print the text of this 1-based PDF page")
    a = ap.parse_args()

    doc = pymupdf.open(a.pdf)
    if doc.needs_pass:
        print("PDF is password-protected/encrypted -> PageIndex (PyPDF2) needs pycryptodome; may still fail.")
    n = doc.page_count
    lens, empty = text_stats(doc)
    off, votes = page_offset(doc)
    lab = labels(doc)
    tocp = toc_pages(doc)
    bm = doc.get_toc()

    print(f"file          : {a.pdf}")
    print(f"pages         : {n}")
    print(f"text layer    : {n - empty}/{n} pages with text "
          f"(median {sorted(lens)[n // 2] if n else 0} chars/page)")
    if n and empty / n > OCR_THRESHOLD:
        print("  -> OCR NEEDED before PageIndex (e.g. `ocrmypdf in.pdf out.pdf`; needs tesseract)")
    elif empty:
        print(f"  -> {empty} near-empty page(s): {[i + 1 for i, l in enumerate(lens) if l < EMPTY_CHARS][:20]}")
    print(f"bookmarks     : {len(bm)} entries" + (" (Flash mode can use these)" if bm else ""))
    print(f"page labels   : {'set, e.g. ' + ', '.join(lab[:5]) if any(lab) else 'none'}")
    if off is None:
        print("print offset  : no printed page numbers found")
    else:
        print(f"print offset  : PDF index = printed number + {off}  ({votes} pages agree)")
    print(f"printed TOC   : {'PDF pages ' + str(tocp) if tocp else 'not detected in first 20 pages'}")

    if a.dump_toc:
        print("\n--- embedded bookmarks (level, title, PDF page) ---")
        for lvl, title, pg in bm:
            print(f"{'  ' * (lvl - 1)}{title}  [p.{pg}]")
        for pg in tocp:
            print(f"\n--- printed TOC text, PDF page {pg} ---\n{doc[pg - 1].get_text().strip()}")
    if a.find:
        find(doc, a.find)
    if a.page:
        print(f"\n--- PDF page {a.page} ---\n{doc[a.page - 1].get_text().strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
