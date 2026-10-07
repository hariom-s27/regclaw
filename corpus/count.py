"""Count physical pages, text layers, modal words, and approximate headings."""

from collections import Counter
import csv
from pathlib import Path
import re

import pymupdf


ROOT = Path(__file__).resolve().parents[1]
MODALS = ("shall", "should", "must", "may")
MODAL_RE = re.compile(r"\b(?:shall|should|must|may)\b")
HEADING_RE = re.compile(
    r"^[ \t]*(?:\d+(?:\.\d+)+\.?|[IVX]+\.|\u00a7\s?\d+\.\d+)(?=\s|$)",
    re.MULTILINE,
)
FIELDS = ("document", "pages", "text_layer_pages", *MODALS, "headings")


def count_modals(text):
    counts = Counter(MODAL_RE.findall(text.lower()))
    return {word: counts[word] for word in MODALS}


def count_headings(text):
    return len(HEADING_RE.findall(text))


def count_pdf(pdf):
    document = (pymupdf.open(stream=pdf, filetype="pdf") if isinstance(pdf, bytes)
                else pymupdf.open(pdf))
    with document:
        stats = {"pages": len(document), "text_layer_pages": 0,
                 **dict.fromkeys(MODALS, 0), "headings": 0}
        for page in document:
            text = page.get_text()
            stats["text_layer_pages"] += bool(text.strip())
            stats["headings"] += count_headings(text)
            for word, count in count_modals(text).items():
                stats[word] += count
    return stats


def collect_stats(pdf_dir):
    pdf_dir = Path(pdf_dir)
    rows = []
    for path in sorted(pdf_dir.iterdir() if pdf_dir.exists() else []):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        if path.name == "eu-mdr_full.pdf" and (pdf_dir / "eu-mdr.pdf").exists():
            continue
        rows.append({"document": path.stem, **count_pdf(path)})
    return rows


def markdown_table(rows):
    lines = ["| " + " | ".join(FIELDS) + " |",
             "| " + " | ".join("---" for _ in FIELDS) + " |"]
    lines.extend("| " + " | ".join(str(row[field]) for field in FIELDS) + " |" for row in rows)
    return "\n".join(lines)


def main():
    rows = collect_stats(ROOT / "data" / "pdfs")
    destination = ROOT / "data" / "corpus_stats.csv"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(markdown_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
