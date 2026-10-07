"""Propose EU MDR physical page ranges without changing YAML or slicing PDFs."""

from pathlib import Path

import pymupdf


ROOT = Path(__file__).resolve().parents[1]
BOUNDARIES = (
    "Article 1", "Article 11", "ANNEX I", "ANNEX II", "ANNEX III", "ANNEX IV",
    "ANNEX XIV", "ANNEX XV", "ANNEX XVI",
)


def find_boundaries(pdf_path):
    canonical = {heading.casefold(): heading for heading in BOUNDARIES}
    matches = {heading: [] for heading in BOUNDARIES}
    with pymupdf.open(pdf_path) as document:
        for page_number, page in enumerate(document, 1):
            lines = [" ".join(line.split()) for line in page.get_text().splitlines()]
            first_line = next((line for line in lines if line), "")
            for index, line in enumerate(lines):
                heading = canonical.get(line.casefold())
                if heading is not None:
                    title = next((text for text in lines[index + 1:] if text), "")
                    matches[heading].append({"page": page_number, "line": line, "title": title,
                                             "first_line": first_line, "is_first_line": line == first_line})
    for heading, candidates in matches.items():
        if len(candidates) != 1:
            raise ValueError(f"Expected one standalone heading for {heading}; found {len(candidates)}")
    boundaries = {heading: matches[heading][0] for heading in BOUNDARIES}
    positions = [boundaries[heading]["page"] for heading in BOUNDARIES]
    if positions != sorted(set(positions)):
        raise ValueError("Boundary headings must appear on distinct pages in document order")
    return boundaries


def propose_ranges(boundaries):
    parts = []
    for name, start, stop in (
        ("Articles 1-10", "Article 1", "Article 11"),
        ("Annexes I-III", "ANNEX I", "ANNEX IV"),
        ("Annexes XIV-XV", "ANNEX XIV", "ANNEX XVI"),
    ):
        boundary = boundaries[stop]
        end = boundary["page"] - 1 if boundary["is_first_line"] else boundary["page"]
        parts.append({"name": name, "pages": [boundaries[start]["page"], end],
                      "start_marker": start, "stop_marker": stop})
    return parts


def main():
    boundaries = find_boundaries(ROOT / "data" / "pdfs" / "eu-mdr_full.pdf")
    parts = propose_ranges(boundaries)
    print("| Standalone heading | Physical page | First title line |")
    print("| --- | --- | --- |")
    for heading, boundary in boundaries.items():
        print(f"| {boundary['line']} | {boundary['page']} | {boundary['title']} |")
    print("\nProposed 1-based inclusive ranges (YAML unchanged):")
    for part in parts:
        pages = part["pages"]
        print(f"{part['name']}: {pages} ({pages[1] - pages[0] + 1} pages); "
              f"start_marker={part['start_marker']!r}, stop_marker={part['stop_marker']!r}")
    print("\nFirst extracted text-line checks:")
    for heading in ("Article 1", "Article 11", "ANNEX IV", "ANNEX XVI"):
        boundary = boundaries[heading]
        print(f"p.{boundary['page']} {heading}: first={boundary['is_first_line']}; "
              f"first_line={boundary['first_line']!r}")
    print(f"Total sliced pages: {sum(part['pages'][1] - part['pages'][0] + 1 for part in parts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
