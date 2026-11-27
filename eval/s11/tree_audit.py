"""S11 tree audit: Contents parser, dual-extractor offset check, forecast, and tree PASS rules."""
from __future__ import annotations

import argparse
import copy
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sys
import unicodedata
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "pageindex") not in sys.path:
    sys.path.insert(0, str(ROOT / "pageindex"))

from eval.s11.ledger import write_once_json  # noqa: E402
from parser_parity import pymupdf_pages, pypdf2_pages  # noqa: E402
from pi_common import iter_nodes  # noqa: E402

TREE_AUDIT_RULES_VERSION = "s11-tree-audit-v1"

DEFAULT_TOC_PAGES = (10, 11)
DEFAULT_BODY_OFFSET = 11
DEFAULT_OFFSET_WINDOW_RADIUS = 3
DEFAULT_COVERAGE_START = 16
DEFAULT_COVERAGE_END = 115
EXPECTED_ANNEXURE_COUNT = 7
TITLE_FUZZY_THRESHOLD = 90.0
PRESENCE_PASS_RATE = 0.95
START_PASS_RATE = 0.95

# S11b2b-20 / D3 named forecast thresholds
FORECAST_MAX_PAGES_PER_NODE = 10
FORECAST_MAX_TOKENS_PER_NODE = 20000

ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {
        "doc_name",
        "structure",
        "doc_description",
        "open_reservations",
        "wrapper_calls",
        "custom_logger_calls",
    }
)
IGNORED_TOP_LEVEL_KEYS = (
    "doc_description",
    "open_reservations",
    "wrapper_calls",
    "custom_logger_calls",
)

_INTRO_SUFFIX_RE = re.compile(r"\s*\(intro\)\s*$", re.IGNORECASE)
_DOT_LEADER_PAGE_RE = re.compile(
    r"(?:\s*[.\u2026·•]{2,}|\s+\.(?:\s*\.)+)\s*(?:\d+(?:\s*[-–]\s*\d+)?)?\s*$"
)
_LEADING_ARABIC_SEC_RE = re.compile(r"^\s*\d+(?:\.\d+)*\.?(?:\s+|$)")
_ANNEXURE_START_RE = re.compile(
    r"^annexure(?:\s*[-–—:.]\s*|\s+)([ivx](?:\s*[ivx])*|\d+)(?=$|[\s.:;,\-–—)])(?:\s*[-–—:.]*\s*)?",
    re.IGNORECASE,
)
_VALID_ROMAN_RE = re.compile(
    r"^(?:l?x{0,3}(?:ix|iv|v?i{0,3})|xl(?:ix|iv|v?i{0,3}))$",
    re.IGNORECASE,
)

# S11b2b-27 Contents state-machine patterns (pages 10-11, both extractors)
_TOC_HEADER_RE = re.compile(r"^(?:contents|table\s+of\s+contents)$", re.IGNORECASE)
_TOC_ANNEX_HEADING_RE = re.compile(r"^annexures?$", re.IGNORECASE)
_TOC_NUMBER_LINE_RE = re.compile(r"^\d{1,3}$")
_TOC_LABEL_LINE_RE = re.compile(
    r"^annexure(?:\s*[-–—:.]?\s+|\s+)([ivx](?:\s*[ivx])*)$",
    re.IGNORECASE,
)
_TOC_SECTION_START_RE = re.compile(r"^\d+(?:\.\d+)*\s+")
_LINE_END_ANNEX_PAGE_RE = re.compile(
    r"^(?P<before>.*?)\bannexure(?:\s*[-–—:.]?\s+|\s+)(?P<roman>[ivx](?:\s*[ivx])*)(?:\s*[.\u2026·•]{2,}\s*|\s+)(?P<page>\d{1,3})\s*$",
    re.IGNORECASE,
)
_LINE_END_PAGE_RE = re.compile(
    r"^(?P<before>.*?\S)(?:\s*[.\u2026·•]{2,}\s*|\s+)(?P<page>\d{1,3})\s*$"
)
_REPORT_ID_NUM_RE = re.compile(r"(\d+)")


def _require_explicit_path(path: Path | str | None, param_name: str) -> Path:
    if path is None or not str(path).strip():
        raise ValueError(f"{param_name} is required and cannot be empty or None")
    return Path(path)


def _roman_to_int(token: str) -> int | None:
    s = re.sub(r"\s+", "", token or "").lower()
    if not s or not _VALID_ROMAN_RE.match(s):
        return None
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    prev = 0
    for ch in reversed(s):
        v = vals[ch]
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    return total


def _int_to_roman_upper(num: int) -> str:
    pairs = [(50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out: list[str] = []
    rem = int(num)
    for val, sym in pairs:
        while rem >= val:
            out.append(sym)
            rem -= val
    return "".join(out)


def _parse_annexure_roman_1_20(raw: str) -> str | None:
    clean = re.sub(r"\s+", "", raw or "").upper()
    val = _roman_to_int(clean)
    if val is not None and 1 <= val <= 20:
        return _int_to_roman_upper(val)
    return None


def _coerce_annexure_numeral(raw: str | None) -> str | None:
    if raw is None:
        return None
    clean = re.sub(r"\s+", "", str(raw)).strip()
    if not clean:
        return None
    if clean.isdigit():
        return str(int(clean))
    rval = _roman_to_int(clean)
    if rval is not None:
        return str(rval)
    return None


def extract_annexure_numeral(title: str) -> str | None:
    """Return canonical annexure numeral string when title starts with 'Annexure' + numeral."""
    s = unicodedata.normalize("NFKC", str(title or "")).casefold().strip()
    s = _INTRO_SUFFIX_RE.sub("", s).strip()
    s = _DOT_LEADER_PAGE_RE.sub("", s).strip()
    m = _ANNEXURE_START_RE.match(s)
    if not m:
        return None
    return _coerce_annexure_numeral(m.group(1))


def is_annexure_title(title: str) -> bool:
    """True iff title begins with 'Annexure' + numeral ('appendix' never counts)."""
    return extract_annexure_numeral(title) is not None


def is_intro_node(node: dict[str, Any]) -> bool:
    """True for PageIndex '(intro)' nodes excluded from Contents matching."""
    title = unicodedata.normalize("NFKC", str(node.get("title") or "")).rstrip()
    return title.casefold().endswith(" (intro)")


def normalize_title(title: str) -> str:
    """Normalize a section title per D1 + S11b2b-27 + G1 (strips 'Annexure <numeral>' label).

    Order: NFKC -> casefold -> drop trailing ' (intro)' -> remove dot leaders +
    trailing page number -> strip leading 'Annexure <numeral>' label if valid ->
    remove leading arabic section number -> punctuation -> space -> collapse spaces.
    """
    s = unicodedata.normalize("NFKC", str(title or "")).casefold().strip()
    s = _INTRO_SUFFIX_RE.sub("", s).strip()
    s = _DOT_LEADER_PAGE_RE.sub("", s).strip()
    m_ann = _ANNEXURE_START_RE.match(s)
    if m_ann and _coerce_annexure_numeral(m_ann.group(1)) is not None:
        s = s[m_ann.end():].strip()
    else:
        m = _LEADING_ARABIC_SEC_RE.match(s)
        if m:
            rest = s[m.end():].strip()
            first_tok = m.group(0).strip()
            is_bare_year = (
                first_tok.isdigit()
                and len(first_tok) == 4
                and 1900 <= int(first_tok) <= 2099
            )
            if rest and not is_bare_year:
                s = rest
    cleaned_chars = [
        " " if (ch == "_" or unicodedata.category(ch).startswith(("P", "S"))) else ch
        for ch in s
    ]
    return " ".join("".join(cleaned_chars).split())


def fuzzy_title_score(
    title_a: str,
    title_b: str,
    *,
    numeral_a: str | None = None,
    numeral_b: str | None = None,
) -> float:
    """Return 0..100 score enforcing S11b2b-27 label removal & G1 label-only rules."""
    num_a = _coerce_annexure_numeral(numeral_a) if numeral_a is not None else extract_annexure_numeral(title_a)
    num_b = _coerce_annexure_numeral(numeral_b) if numeral_b is not None else extract_annexure_numeral(title_b)

    if num_a is not None and num_b is not None and num_a != num_b:
        return 0.0

    na = normalize_title(title_a)
    nb = normalize_title(title_b)

    # G1: if remaining title on either side is empty after removing Annexure label
    if not na or not nb:
        if num_a is not None and num_b is not None and num_a == num_b:
            return 100.0
        return 0.0

    return SequenceMatcher(None, na, nb).ratio() * 100.0


def _clean_parsed_toc_title(raw_title: str) -> str:
    s = unicodedata.normalize("NFKC", raw_title).strip()
    s = re.sub(r"[\s.\u2026·•]+$", "", s).strip()
    return " ".join(s.split())


def line_shape(line: str, max_chars: int = 80) -> str:
    """Anonymize a text line: Unicode letter->'a', digit->'9', whitespace->' ', max 80 chars."""
    mapped: list[str] = []
    for ch in str(line or ""):
        if ch.isspace():
            mapped.append(" ")
        elif unicodedata.category(ch).startswith("L"):
            mapped.append("a")
        elif ch.isdigit() or unicodedata.category(ch) == "Nd":
            mapped.append("9")
        else:
            mapped.append(ch)
    collapsed = re.sub(r" +", " ", "".join(mapped)).strip()
    return collapsed[:max_chars]


def _load_pdf_pages_map(
    pdf_path: Path | str | None,
    *,
    pymupdf_pages_by_num: dict[int, str] | None = None,
    pypdf2_pages_by_num: dict[int, str] | None = None,
    need_pypdf2: bool = True,
) -> tuple[dict[int, str], dict[int, str]]:
    if pymupdf_pages_by_num is not None:
        mupdf_map = {int(k): str(v or "") for k, v in pymupdf_pages_by_num.items()}
    else:
        if pdf_path is None:
            raise ValueError("pdf_path or pymupdf_pages_by_num is required")
        raw_mupdf = pymupdf_pages(str(pdf_path))
        mupdf_map = {idx + 1: text or "" for idx, text in enumerate(raw_mupdf)}

    if pypdf2_pages_by_num is not None:
        pdf2_map = {int(k): str(v or "") for k, v in pypdf2_pages_by_num.items()}
    elif need_pypdf2:
        if pdf_path is None:
            raise ValueError("pdf_path or pypdf2_pages_by_num is required for PyPDF2 checks")
        raw_pdf2 = pypdf2_pages(str(pdf_path))
        pdf2_map = {idx + 1: text or "" for idx, text in enumerate(raw_pdf2)}
    else:
        pdf2_map = {}
    return mupdf_map, pdf2_map


def parse_contents_pages(
    pages_by_num: dict[int, str],
    *,
    toc_pages: tuple[int, ...] = DEFAULT_TOC_PAGES,
    offset: int = DEFAULT_BODY_OFFSET,
    max_printed_page: int | None = None,
    trace: list[dict[str, Any]] | None = None,
    extractor_label: str = "",
) -> dict[str, Any]:
    """Parse printed Contents pages 10-11 via S11b2b-27 rules 1..6 across both extractors."""
    doc_pages = max(pages_by_num.keys(), default=0)
    upper_printed = max_printed_page if max_printed_page is not None else max(doc_pages, 116)

    entries: list[dict[str, Any]] = []
    no_page = 0
    headings_skipped = 0
    buf_lines: list[str] = []
    buf_label: str | None = None
    buf_toc_page: int | None = None

    def _emit_trace(pg_num: int, line_idx: int, raw: str, decision: str) -> None:
        if trace is None:
            return
        starts_annex = unicodedata.normalize("NFKC", raw).lstrip().casefold().startswith("annexure")
        trace.append(
            {
                "page": int(pg_num),
                "extractor": extractor_label,
                "line_index": int(line_idx),
                "shape": line_shape(raw),
                "starts_with_annexure": starts_annex,
                "parser_decision": decision,
            }
        )

    def _append_entry(raw_title: str, printed: int, label: str | None, toc_pg: int) -> None:
        title_clean = _clean_parsed_toc_title(raw_title)
        entries.append(
            {
                "index": len(entries),
                "toc_page": int(toc_pg),
                "title": title_clean,
                "printed_page": int(printed),
                "expected_start": int(printed) + int(offset),
                "is_annexure": label is not None,
                "annexure_numeral": label,
            }
        )

    def _close_buffer(printed: int, closing_pg: int) -> None:
        nonlocal buf_lines, buf_label, buf_toc_page
        _append_entry(" ".join(buf_lines), printed, buf_label, buf_toc_page or closing_pg)
        buf_lines = []
        buf_label = None
        buf_toc_page = None

    def _flush_as_no_page() -> None:
        nonlocal no_page, buf_lines, buf_label, buf_toc_page
        if buf_lines or buf_label is not None:
            no_page += 1
            buf_lines = []
            buf_label = None
            buf_toc_page = None

    def _try_inline_close(line: str, pg_num: int) -> bool:
        nonlocal buf_lines, buf_label, buf_toc_page
        m_ann = _LINE_END_ANNEX_PAGE_RE.match(line)
        if m_ann:
            roman = _parse_annexure_roman_1_20(m_ann.group("roman"))
            printed = int(m_ann.group("page"))
            before = _clean_parsed_toc_title(m_ann.group("before"))
            if roman is not None and 1 <= printed <= upper_printed and (buf_lines or before):
                title_parts = [*buf_lines, *([before] if before else [])]
                _append_entry(" ".join(title_parts), printed, roman, buf_toc_page or pg_num)
                buf_lines = []
                buf_label = None
                buf_toc_page = None
                return True

        m_num = _LINE_END_PAGE_RE.match(line)
        if m_num:
            printed = int(m_num.group("page"))
            before = _clean_parsed_toc_title(m_num.group("before"))
            if 1 <= printed <= upper_printed and (buf_lines or before):
                title_parts = [*buf_lines, *([before] if before else [])]
                _append_entry(" ".join(title_parts), printed, buf_label, buf_toc_page or pg_num)
                buf_lines = []
                buf_label = None
                buf_toc_page = None
                return True

        return False

    for pg in toc_pages:
        raw_text = pages_by_num.get(pg, "")
        for line_idx, raw_line in enumerate(raw_text.splitlines()):
            line = unicodedata.normalize("NFKC", raw_line).strip()

            # Rule 1: Skip empty lines, Contents header, and bare "Annexure"/"Annexures" headings
            if not line:
                _emit_trace(pg, line_idx, raw_line, "skipped:empty")
                continue
            if _TOC_HEADER_RE.match(line):
                _emit_trace(pg, line_idx, raw_line, "skipped:header")
                continue
            if _TOC_ANNEX_HEADING_RE.match(line):
                headings_skipped += 1
                _emit_trace(pg, line_idx, raw_line, "skipped:heading")
                continue

            # Rule 2: Bare 1-3 digit number line closes buffered entry
            if _TOC_NUMBER_LINE_RE.match(line):
                printed = int(line)
                if buf_lines and 1 <= printed <= upper_printed:
                    _close_buffer(printed, pg)
                    _emit_trace(pg, line_idx, raw_line, "accepted")
                else:
                    _emit_trace(pg, line_idx, raw_line, "skipped:orphan_number")
                continue

            # Rule 3: Standalone "Annexure <roman I..XX>" label line (spaces inside numeral allowed)
            m_lbl = _TOC_LABEL_LINE_RE.match(line)
            if m_lbl:
                roman = _parse_annexure_roman_1_20(m_lbl.group(1))
                if roman is not None:
                    buf_label = roman
                    if buf_toc_page is None:
                        buf_toc_page = int(pg)
                    _emit_trace(pg, line_idx, raw_line, "continuation")
                    continue

            # Rule 4: Section number start replaces non-empty buffer (counted as no_page)
            if _TOC_SECTION_START_RE.match(line):
                _flush_as_no_page()

            # Rule 5: Inline "[Annexure <roman>] <1-3 digits>" closes entry; else append to buffer
            if _try_inline_close(line, pg):
                _emit_trace(pg, line_idx, raw_line, "accepted")
            else:
                buf_lines.append(line)
                if buf_toc_page is None:
                    buf_toc_page = int(pg)
                _emit_trace(pg, line_idx, raw_line, "continuation")

    # Rule 6: End of pages leaves non-empty buffer -> no_page
    _flush_as_no_page()

    annexure_numerals = [e["annexure_numeral"] for e in entries if e["is_annexure"] and e["annexure_numeral"]]
    return {
        "toc_pages": [int(p) for p in toc_pages],
        "offset": int(offset),
        "entries": entries,
        "entry_count": len(entries),
        "annexures": len(annexure_numerals),
        "annexure_numerals": annexure_numerals,
        "no_page": no_page,
        "headings_skipped": headings_skipped,
    }


def _first_matching_page_in_window(
    entry: dict[str, Any],
    window_pages: list[int],
    pages_map: dict[int, str],
) -> tuple[int | None, float]:
    """Match window page using single lines and 2-/3-line consecutive joins (>= 90)."""
    title = str(entry["title"])
    annex_num = entry.get("annexure_numeral")

    for pg in window_pages:
        lines = [unicodedata.normalize("NFKC", ln).strip() for ln in pages_map.get(pg, "").splitlines() if ln.strip()]
        if not lines:
            continue
        candidates: list[str] = []
        n = len(lines)
        for i in range(n):
            candidates.append(lines[i])
            if i + 1 < n:
                candidates.append(f"{lines[i]} {lines[i + 1]}")
            if i + 2 < n:
                candidates.append(f"{lines[i]} {lines[i + 1]} {lines[i + 2]}")

        best = 0.0
        for cand in candidates:
            sc = fuzzy_title_score(title, cand, numeral_a=annex_num)
            if sc > best:
                best = sc
            if best >= TITLE_FUZZY_THRESHOLD:
                return pg, best
    return None, 0.0


def verify_contents_offsets(
    entries: list[dict[str, Any]],
    pymupdf_map: dict[int, str],
    pypdf2_map: dict[int, str],
    *,
    doc_pages: int,
    toc_pages: tuple[int, ...] = DEFAULT_TOC_PAGES,
    offset: int = DEFAULT_BODY_OFFSET,
    radius: int = DEFAULT_OFFSET_WINDOW_RADIUS,
) -> dict[str, Any]:
    """Re-measure per-entry physical start in printed+offset +/- radius (excl. TOC pages) per D2 / S11b2b-27."""
    toc_set = {int(p) for p in toc_pages}
    upper = max(1, int(doc_pages))
    verified_entries: list[dict[str, Any]] = []
    extractor_counts = {"pymupdf": 0, "pypdf2": 0, "offset_fallback": 0}
    extractor_disagreements = 0
    offset_fallbacks = 0

    for entry in entries:
        printed = int(entry["printed_page"])
        nominal = printed + int(offset)
        lo = max(1, nominal - int(radius))
        hi = min(upper, nominal + int(radius))
        window = [p for p in range(lo, hi + 1) if p not in toc_set]

        mupdf_pg, mupdf_score = _first_matching_page_in_window(entry, window, pymupdf_map)
        pdf2_pg, pdf2_score = _first_matching_page_in_window(entry, window, pypdf2_map)

        if mupdf_pg is not None and pdf2_pg is not None and mupdf_pg != pdf2_pg:
            extractor_disagreements += 1

        if mupdf_pg is not None:
            resolved_pg = mupdf_pg
            extractor = "pymupdf"
            flag = None
            match_score = mupdf_score
        elif pdf2_pg is not None:
            resolved_pg = pdf2_pg
            extractor = "pypdf2"
            flag = None
            match_score = pdf2_score
        else:
            resolved_pg = nominal
            extractor = "offset_fallback"
            flag = "offset_fallback"
            match_score = 0.0
            offset_fallbacks += 1

        extractor_counts[extractor] += 1
        updated = dict(entry)
        updated.update(
            {
                "window_start": window[0] if window else None,
                "window_end": window[-1] if window else None,
                "pymupdf_page": mupdf_pg,
                "pypdf2_page": pdf2_pg,
                "resolved_start": resolved_pg,
                "matched_extractor": extractor,
                "match_score": round(match_score, 3),
                "offset_flag": flag,
            }
        )
        verified_entries.append(updated)

    return {
        "entries": verified_entries,
        "offset_fallbacks": offset_fallbacks,
        "extractor_counts": extractor_counts,
        "extractor_disagreements": extractor_disagreements,
    }


def compute_leaf_forecast(
    entries: list[dict[str, Any]],
    pymupdf_map: dict[int, str],
    pypdf2_map: dict[int, str],
    *,
    doc_pages: int,
) -> dict[str, Any]:
    """Compute S11b2b-20 / D3 & Q6 leaf span forecast preserving exact Contents order."""
    spans: list[dict[str, Any]] = []
    order_breaks = 0
    n = len(entries)

    for i, entry in enumerate(entries):
        start = int(entry["expected_start"])
        if i > 0 and start < int(entries[i - 1]["expected_start"]):
            order_breaks += 1
        if i < n - 1:
            end = max(start, int(entries[i + 1]["expected_start"]) - 1)
        else:
            end = max(start, int(doc_pages))
        span_pages = end - start + 1

        lo = max(1, start)
        hi = min(int(doc_pages), end)
        page_range = range(lo, hi + 1) if lo <= hi else ()
        chars_mupdf = sum(len(pymupdf_map.get(p, "")) for p in page_range)
        chars_pdf2 = sum(len(pypdf2_map.get(p, "")) for p in page_range)
        tok_mupdf = chars_mupdf / 4.0
        tok_pdf2 = chars_pdf2 / 4.0

        spans.append(
            {
                "index": i,
                "title": entry["title"],
                "start_page": start,
                "end_page": end,
                "span_pages": span_pages,
                "chars_pymupdf": chars_mupdf,
                "chars_pypdf2": chars_pdf2,
                "approx_tokens_pymupdf": tok_mupdf,
                "approx_tokens_pypdf2": tok_pdf2,
            }
        )

    gt_10 = [s for s in spans if s["span_pages"] > FORECAST_MAX_PAGES_PER_NODE]
    return {
        "entries": len(spans),
        "order_breaks": order_breaks,
        "spans_gt_10": len(gt_10),
        "spans_gt_10_tokens_lt_20000_pymupdf": sum(
            1 for s in gt_10 if s["approx_tokens_pymupdf"] < FORECAST_MAX_TOKENS_PER_NODE
        ),
        "spans_gt_10_tokens_lt_20000_pypdf2": sum(
            1 for s in gt_10 if s["approx_tokens_pypdf2"] < FORECAST_MAX_TOKENS_PER_NODE
        ),
        "largest_span": max((s["span_pages"] for s in spans), default=0),
        "max_pages_threshold": FORECAST_MAX_PAGES_PER_NODE,
        "max_tokens_threshold": FORECAST_MAX_TOKENS_PER_NODE,
        "spans": spans,
    }


def analyze_pdf_contents(
    pdf_path: Path | str | None = None,
    *,
    toc_pages: tuple[int, ...] = DEFAULT_TOC_PAGES,
    offset: int = DEFAULT_BODY_OFFSET,
    radius: int = DEFAULT_OFFSET_WINDOW_RADIUS,
    pymupdf_pages_by_num: dict[int, str] | None = None,
    pypdf2_pages_by_num: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Run full Contents parse on both extractors, offset verification, and leaf forecast."""
    mupdf_map, pdf2_map = _load_pdf_pages_map(
        pdf_path,
        pymupdf_pages_by_num=pymupdf_pages_by_num,
        pypdf2_pages_by_num=pypdf2_pages_by_num,
        need_pypdf2=True,
    )
    doc_pages = max(len(mupdf_map), len(pdf2_map))
    parsed_mupdf = parse_contents_pages(mupdf_map, toc_pages=toc_pages, offset=offset, max_printed_page=doc_pages)
    parsed_pdf2 = parse_contents_pages(pdf2_map, toc_pages=toc_pages, offset=offset, max_printed_page=doc_pages)

    verified = verify_contents_offsets(
        parsed_mupdf["entries"],
        mupdf_map,
        pdf2_map,
        doc_pages=doc_pages,
        toc_pages=toc_pages,
        offset=offset,
        radius=radius,
    )
    forecast = compute_leaf_forecast(
        verified["entries"],
        mupdf_map,
        pdf2_map,
        doc_pages=doc_pages,
    )
    return {
        "rules_version": TREE_AUDIT_RULES_VERSION,
        "doc_pages": doc_pages,
        "toc_pages": parsed_mupdf["toc_pages"],
        "offset": int(offset),
        "entry_count": parsed_mupdf["entry_count"],
        "annexures": parsed_mupdf["annexures"],
        "annexure_numerals": parsed_mupdf["annexure_numerals"],
        "no_page": parsed_mupdf["no_page"],
        "headings_skipped": parsed_mupdf["headings_skipped"],
        "contents_parse_by_extractor": {
            "pymupdf": {
                "entries": parsed_mupdf["entry_count"],
                "annexures": parsed_mupdf["annexures"],
                "annexure_numerals": parsed_mupdf["annexure_numerals"],
                "no_page": parsed_mupdf["no_page"],
                "headings_skipped": parsed_mupdf["headings_skipped"],
            },
            "pypdf2": {
                "entries": parsed_pdf2["entry_count"],
                "annexures": parsed_pdf2["annexures"],
                "annexure_numerals": parsed_pdf2["annexure_numerals"],
                "no_page": parsed_pdf2["no_page"],
                "headings_skipped": parsed_pdf2["headings_skipped"],
            },
        },
        "offset_fallbacks": verified["offset_fallbacks"],
        "extractor_counts": verified["extractor_counts"],
        "extractor_disagreements": verified["extractor_disagreements"],
        "forecast": forecast,
        "entries": verified["entries"],
        "pypdf2_entries": parsed_pdf2["entries"],
    }


def contents_summary_counts(analysis: dict[str, Any]) -> dict[str, Any]:
    """Extract strictly numeric/count summary (no headings or page text) for stdout reporting."""
    fc = analysis["forecast"]
    return {
        "rules_version": analysis["rules_version"],
        "doc_pages": analysis["doc_pages"],
        "toc_pages": analysis["toc_pages"],
        "offset": analysis["offset"],
        "entries": analysis["entry_count"],
        "annexures": analysis["annexures"],
        "annexure_numerals": analysis["annexure_numerals"],
        "no_page": analysis["no_page"],
        "headings_skipped": analysis["headings_skipped"],
        "contents_parse_by_extractor": analysis["contents_parse_by_extractor"],
        "offset_fallbacks": analysis["offset_fallbacks"],
        "extractor_counts": analysis["extractor_counts"],
        "extractor_disagreements": analysis["extractor_disagreements"],
        "forecast": {
            "entries": fc["entries"],
            "order_breaks": fc["order_breaks"],
            "spans_gt_10": fc["spans_gt_10"],
            "spans_gt_10_tokens_lt_20000_pymupdf": fc["spans_gt_10_tokens_lt_20000_pymupdf"],
            "spans_gt_10_tokens_lt_20000_pypdf2": fc["spans_gt_10_tokens_lt_20000_pypdf2"],
            "largest_span": fc["largest_span"],
        },
    }


def _validate_node_tree(nodes: Any, path: str = "structure") -> None:
    if not isinstance(nodes, list):
        raise ValueError(f"{path} must be a list")
    for idx, node in enumerate(nodes):
        loc = f"{path}[{idx}]"
        if not isinstance(node, dict):
            raise ValueError(f"{loc} must be a dict")
        if "title" not in node or not isinstance(node["title"], str):
            raise ValueError(f"{loc}.title must be a str")
        for bound in ("start_index", "end_index"):
            val = node.get(bound)
            if isinstance(val, bool) or not isinstance(val, int):
                raise ValueError(f"{loc}.{bound} must be an int")
        if "node_id" in node and node["node_id"] is not None and not isinstance(node["node_id"], str):
            raise ValueError(f"{loc}.node_id must be a str or None")
        if "key_items" in node and node["key_items"] is not None:
            if not isinstance(node["key_items"], list) or not all(
                isinstance(k, str) for k in node["key_items"]
            ):
                raise ValueError(f"{loc}.key_items must be a list of strings")
        if "nodes" in node and node["nodes"] is not None:
            _validate_node_tree(node["nodes"], f"{loc}.nodes")


def validate_tree_payload(tree: Any) -> dict[str, Any]:
    """Validate tree schema per Q-D3/F3 and return ignored_keys metadata."""
    if not isinstance(tree, dict):
        raise ValueError("Tree JSON top level must be an object")
    keys = set(tree.keys())
    unknown = sorted(keys - ALLOWED_TOP_LEVEL_KEYS)
    if unknown:
        raise ValueError(f"Unsupported top-level tree keys: {unknown}")
    for req in ("doc_name", "structure"):
        if req not in tree:
            raise ValueError(f"Missing required top-level key: {req!r}")
    if not isinstance(tree["doc_name"], str):
        raise ValueError("doc_name must be a str")
    _validate_node_tree(tree["structure"], "structure")
    return {"ignored_keys": [k for k in IGNORED_TOP_LEVEL_KEYS if k in tree]}


def _assign_report_ids(structure: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a deep copy with 'pre-0000'..'pre-NNNN' report_id when node_id is missing (F2)."""
    cloned = copy.deepcopy(structure)
    counter = 0
    for node, depth in iter_nodes(cloned):
        node["_report_id"] = node.get("node_id") or f"pre-{counter:04d}"
        node["_depth"] = depth
        node["_is_leaf"] = not (node.get("nodes") or [])
        node["_is_intro"] = is_intro_node(node)
        counter += 1
    return cloned


def _report_id_sort_key(report_id: str) -> tuple[int, int, str]:
    m = _REPORT_ID_NUM_RE.findall(report_id)
    if m:
        return (0, int(m[-1]), report_id)
    return (1, 0, report_id)


def _match_entries_to_nodes(
    entries: list[dict[str, Any]],
    nodes_preorder: list[dict[str, Any]],
    *,
    allow_key_items: bool,
) -> list[dict[str, Any]]:
    """Run greedy 1-to-1 title matching per D1 (highest score, nearest expected_start, lower id)."""
    candidates: list[tuple[float, int, int, tuple[int, int, str], int, int, dict[str, Any]]] = []

    matchable_nodes = [n for n in nodes_preorder if not n["_is_intro"]]

    for entry_idx, entry in enumerate(entries):
        exp_start = int(entry["expected_start"])
        for node in matchable_nodes:
            rid = node["_report_id"]
            dist = abs(int(node["start_index"]) - exp_start)
            id_key = _report_id_sort_key(rid)

            slots: list[tuple[str, int, str]] = [("title", -1, node["title"])]
            if allow_key_items:
                for ki_idx, ki_text in enumerate(node.get("key_items") or []):
                    if str(ki_text).strip():
                        slots.append(("key_item", ki_idx, str(ki_text)))

            for slot_kind, slot_idx, cand_text in slots:
                score = fuzzy_title_score(
                    entry["title"],
                    cand_text,
                    numeral_a=entry.get("annexure_numeral"),
                )
                if score >= TITLE_FUZZY_THRESHOLD:
                    kind_rank = 0 if slot_kind == "title" else 1
                    candidates.append(
                        (
                            -round(score, 9),
                            dist,
                            kind_rank,
                            id_key,
                            entry_idx,
                            slot_idx,
                            {
                                "entry_index": entry_idx,
                                "report_id": rid,
                                "node_start": int(node["start_index"]),
                                "node_end": int(node["end_index"]),
                                "match_source": slot_kind,
                                "slot_idx": slot_idx,
                                "score": score,
                                "distance": dist,
                                "id_key": id_key,
                            },
                        )
                    )

    candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3], c[4], c[5]))
    used_entries: set[int] = set()
    used_slots: set[tuple[str, str, int]] = set()
    matched: dict[int, dict[str, Any]] = {}

    for _, _, _, _, entry_idx, slot_idx, payload in candidates:
        slot_key = (payload["report_id"], payload["match_source"], slot_idx)
        if entry_idx in used_entries or slot_key in used_slots:
            continue
        used_entries.add(entry_idx)
        used_slots.add(slot_key)
        matched[entry_idx] = payload

    return [matched[i] for i in sorted(matched)]


def audit_tree_variant(
    structure: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    *,
    variant: str,
    allow_key_items: bool,
    contained_start_check: bool,
    coverage_start: int = DEFAULT_COVERAGE_START,
    coverage_end: int = DEFAULT_COVERAGE_END,
    expected_annexures: int = EXPECTED_ANNEXURE_COUNT,
    max_leaf_pages: int = FORECAST_MAX_PAGES_PER_NODE,
) -> dict[str, Any]:
    """Run all 7 structural PASS criteria on one tree view (Variant A, B, or C)."""
    numbered = _assign_report_ids(structure)
    nodes_preorder = [n for n, _ in iter_nodes(numbered)]
    leaves = [n for n in nodes_preorder if n["_is_leaf"]]
    intro_count = sum(1 for n in nodes_preorder if n["_is_intro"])

    matches = _match_entries_to_nodes(entries, nodes_preorder, allow_key_items=allow_key_items)
    match_by_entry = {m["entry_index"]: m for m in matches}

    total_entries = len(entries)
    matched_count = len(matches)
    presence_rate = (matched_count / total_entries) if total_entries > 0 else 0.0
    cond1_pass = total_entries > 0 and presence_rate >= PRESENCE_PASS_RATE

    start_ok_count = 0
    for entry_idx, m in match_by_entry.items():
        exp_start = int(entries[entry_idx]["expected_start"])
        ns, ne = m["node_start"], m["node_end"]
        if contained_start_check:
            ok = (ns - 1) <= exp_start <= (ne + 1)
        else:
            ok = abs(ns - exp_start) <= 1
        if ok:
            start_ok_count += 1
    start_rate = (start_ok_count / matched_count) if matched_count > 0 else 0.0
    cond2_pass = matched_count > 0 and start_rate >= START_PASS_RATE

    annexure_entries = [e for e in entries if e["is_annexure"]]
    annexure_title_nodes: set[str] = set()
    annexure_matched_entries = 0
    for e in annexure_entries:
        m = match_by_entry.get(e["index"])
        # Q6: Condition 3 needs distinct nodes matched via node title only; key_items never count
        if m is not None and m["match_source"] == "title":
            annexure_matched_entries += 1
            annexure_title_nodes.add(m["report_id"])
    cond3_pass = (
        len(annexure_entries) == int(expected_annexures)
        and annexure_matched_entries == int(expected_annexures)
        and len(annexure_title_nodes) == int(expected_annexures)
    )

    covered_pages: set[int] = set()
    oversized_leaf_ids: list[str] = []
    for lf in leaves:
        s_idx, e_idx = int(lf["start_index"]), int(lf["end_index"])
        if e_idx >= s_idx:
            covered_pages.update(range(s_idx, e_idx + 1))
            if (e_idx - s_idx + 1) > int(max_leaf_pages):
                oversized_leaf_ids.append(lf["_report_id"])
        else:
            oversized_leaf_ids.append(lf["_report_id"])

    target_pages = set(range(int(coverage_start), int(coverage_end) + 1))
    uncovered_pages = sorted(target_pages - covered_pages)
    cond4_pass = len(uncovered_pages) == 0

    order_breaks: list[dict[str, Any]] = []
    for i in range(1, len(nodes_preorder)):
        prev_n = nodes_preorder[i - 1]
        cur_n = nodes_preorder[i]
        if int(cur_n["start_index"]) < int(prev_n["start_index"]):
            order_breaks.append(
                {
                    "prev_id": prev_n["_report_id"],
                    "prev_start": int(prev_n["start_index"]),
                    "cur_id": cur_n["_report_id"],
                    "cur_start": int(cur_n["start_index"]),
                }
            )
    cond5_pass = len(order_breaks) == 0

    bad_range_ids = [
        n["_report_id"]
        for n in nodes_preorder
        if int(n["end_index"]) < int(n["start_index"])
    ]
    cond6_pass = len(bad_range_ids) == 0
    cond7_pass = len(oversized_leaf_ids) == 0

    all_pass = all(
        (cond1_pass, cond2_pass, cond3_pass, cond4_pass, cond5_pass, cond6_pass, cond7_pass)
    )
    return {
        "variant": variant,
        "pass": all_pass,
        "node_count": len(nodes_preorder),
        "leaf_count": len(leaves),
        "intro_node_count": intro_count,
        "criteria": {
            "presence_95pct": {
                "pass": cond1_pass,
                "matched": matched_count,
                "total": total_entries,
                "rate": round(presence_rate, 6),
            },
            "start_within_1_page_95pct": {
                "pass": cond2_pass,
                "mode": "range_contained" if contained_start_check else "abs_le_1",
                "within_tolerance": start_ok_count,
                "matched": matched_count,
                "rate": round(start_rate, 6),
            },
            "all_annexures_distinct_nodes": {
                "pass": cond3_pass,
                "expected": int(expected_annexures),
                "contents_annexures": len(annexure_entries),
                "matched_distinct_nodes": len(annexure_title_nodes),
                "matched_node_ids": sorted(annexure_title_nodes, key=_report_id_sort_key),
            },
            "leaf_coverage_no_gaps": {
                "pass": cond4_pass,
                "target_range": [int(coverage_start), int(coverage_end)],
                "uncovered_count": len(uncovered_pages),
                "uncovered_pages": uncovered_pages,
            },
            "starts_non_decreasing": {
                "pass": cond5_pass,
                "break_count": len(order_breaks),
                "breaks": order_breaks,
            },
            "end_ge_start_all_nodes": {
                "pass": cond6_pass,
                "bad_count": len(bad_range_ids),
                "bad_node_ids": bad_range_ids,
            },
            "leaves_le_10_pages": {
                "pass": cond7_pass,
                "max_leaf_pages": int(max_leaf_pages),
                "oversized_count": len(oversized_leaf_ids),
                "oversized_leaf_ids": oversized_leaf_ids,
            },
        },
        "matches": [
            {
                "entry_index": m["entry_index"],
                "report_id": m["report_id"],
                "match_source": m["match_source"],
                "score": round(m["score"], 3),
                "expected_start": int(entries[m["entry_index"]]["expected_start"]),
                "node_start": m["node_start"],
                "node_end": m["node_end"],
            }
            for m in matches
        ],
    }


def _extract_pre_merge_structure(pre_merge_payload: Any) -> list[dict[str, Any]]:
    if isinstance(pre_merge_payload, dict) and "snapshots" in pre_merge_payload:
        snaps = pre_merge_payload.get("snapshots") or []
        if not snaps:
            raise ValueError("pre-merge snapshot file has empty 'snapshots'")
        last = snaps[-1]
        if not isinstance(last, dict) or not isinstance(last.get("structure"), list):
            raise ValueError("pre-merge snapshot entry missing 'structure' list")
        _validate_node_tree(last["structure"], "snapshots[-1].structure")
        return last["structure"]
    if isinstance(pre_merge_payload, dict) and isinstance(pre_merge_payload.get("structure"), list):
        _validate_node_tree(pre_merge_payload["structure"], "pre_merge.structure")
        return pre_merge_payload["structure"]
    raise ValueError("Unsupported pre-merge tree payload format")


def audit_tree(
    tree: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    pre_merge_tree: dict[str, Any] | list[dict[str, Any]] | None = None,
    coverage_start: int = DEFAULT_COVERAGE_START,
    coverage_end: int = DEFAULT_COVERAGE_END,
    expected_annexures: int = EXPECTED_ANNEXURE_COUNT,
    max_leaf_pages: int = FORECAST_MAX_PAGES_PER_NODE,
) -> dict[str, Any]:
    """Validate tree schema (F3) and run Variants A, B, and optional C (pre-merge snapshot, F2)."""
    schema_info = validate_tree_payload(tree)
    structure = tree["structure"]

    variant_a = audit_tree_variant(
        structure,
        entries,
        variant="A",
        allow_key_items=False,
        contained_start_check=False,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        expected_annexures=expected_annexures,
        max_leaf_pages=max_leaf_pages,
    )
    variant_b = audit_tree_variant(
        structure,
        entries,
        variant="B",
        allow_key_items=True,
        contained_start_check=True,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        expected_annexures=expected_annexures,
        max_leaf_pages=max_leaf_pages,
    )

    variants: dict[str, Any] = {"A": variant_a, "B": variant_b}
    if pre_merge_tree is not None:
        if isinstance(pre_merge_tree, list):
            _validate_node_tree(pre_merge_tree, "pre_merge_tree")
            pre_structure = pre_merge_tree
        else:
            pre_structure = _extract_pre_merge_structure(pre_merge_tree)
        variants["C"] = audit_tree_variant(
            pre_structure,
            entries,
            variant="C",
            allow_key_items=False,
            contained_start_check=False,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            expected_annexures=expected_annexures,
            max_leaf_pages=max_leaf_pages,
        )

    return {
        "rules_version": TREE_AUDIT_RULES_VERSION,
        "ignored_keys": schema_info["ignored_keys"],
        "overall_pass": variant_a["pass"],
        "variants": variants,
    }


def audit_summary_counts(audit_result: dict[str, Any]) -> dict[str, Any]:
    """Strip titles/text if any, emitting strictly counts, booleans, ratios, and report node IDs."""
    summary_variants: dict[str, Any] = {}
    for k, v in audit_result["variants"].items():
        summary_variants[k] = {
            "variant": v["variant"],
            "pass": v["pass"],
            "node_count": v["node_count"],
            "leaf_count": v["leaf_count"],
            "intro_node_count": v["intro_node_count"],
            "criteria": v["criteria"],
        }
    return {
        "rules_version": audit_result["rules_version"],
        "ignored_keys": audit_result["ignored_keys"],
        "overall_pass": audit_result["overall_pass"],
        "variants": summary_variants,
    }


def _parse_int_tuple(vals: list[int]) -> tuple[int, ...]:
    return tuple(int(v) for v in vals)


def analyze_contents_shapes(
    pdf_path: Path | str | None = None,
    *,
    toc_pages: tuple[int, ...] = DEFAULT_TOC_PAGES,
    offset: int = DEFAULT_BODY_OFFSET,
    pymupdf_pages_by_num: dict[int, str] | None = None,
    pypdf2_pages_by_num: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Run S11b2b-26 line-shape diagnostic over pages 10-11 for both extractors without raw text."""
    mupdf_map, pdf2_map = _load_pdf_pages_map(
        pdf_path,
        pymupdf_pages_by_num=pymupdf_pages_by_num,
        pypdf2_pages_by_num=pypdf2_pages_by_num,
        need_pypdf2=True,
    )
    doc_pages = max(len(mupdf_map), len(pdf2_map))
    lines: list[dict[str, Any]] = []
    decision_counts: dict[str, int] = {}
    decision_counts_by_extractor: dict[str, dict[str, int]] = {"pymupdf": {}, "pypdf2": {}}

    for ext_name, page_map in (("pymupdf", mupdf_map), ("pypdf2", pdf2_map)):
        ext_trace: list[dict[str, Any]] = []
        parse_contents_pages(
            page_map,
            toc_pages=toc_pages,
            offset=offset,
            max_printed_page=doc_pages,
            trace=ext_trace,
            extractor_label=ext_name,
        )
        ext_counts = decision_counts_by_extractor[ext_name]
        for rec in ext_trace:
            dec = rec["parser_decision"]
            decision_counts[dec] = decision_counts.get(dec, 0) + 1
            ext_counts[dec] = ext_counts.get(dec, 0) + 1
            lines.append(rec)

    return {
        "toc_pages": [int(p) for p in toc_pages],
        "lines": lines,
        "decision_counts": decision_counts,
        "decision_counts_by_extractor": decision_counts_by_extractor,
    }


def _best_match_across_pages(
    entry: dict[str, Any],
    pages_to_search: list[int],
    pages_map: dict[int, str],
    *,
    expected_start: int,
) -> dict[str, Any]:
    """Return best score/page/join_size across 1/2/3-line joins without changing matching rules."""
    title = str(entry["title"])
    annex_num = entry.get("annexure_numeral")
    best_key: tuple[float, int, int, int] | None = None
    best_result: dict[str, Any] = {"best_score": 0.0, "page": None, "join_size": None}

    for pg in pages_to_search:
        lines = [
            unicodedata.normalize("NFKC", ln).strip()
            for ln in pages_map.get(pg, "").splitlines()
            if ln.strip()
        ]
        n = len(lines)
        for join_size in (1, 2, 3):
            for i in range(0, max(0, n - join_size + 1)):
                cand = " ".join(lines[i : i + join_size])
                sc = fuzzy_title_score(title, cand, numeral_a=annex_num)
                cand_key = (round(sc, 9), -abs(pg - expected_start), -pg, -join_size)
                if best_key is None or cand_key > best_key:
                    best_key = cand_key
                    best_result = {
                        "best_score": round(float(sc), 3),
                        "page": int(pg),
                        "join_size": int(join_size),
                    }

    return best_result


def explain_contents_fallbacks(
    analysis: dict[str, Any],
    pymupdf_map: dict[int, str],
    pypdf2_map: dict[str, str],
    *,
    toc_pages: tuple[int, ...] = DEFAULT_TOC_PAGES,
    radius: int = DEFAULT_OFFSET_WINDOW_RADIUS,
    wide_radius: int = 10,
) -> list[dict[str, Any]]:
    """Build strictly numeric/boolean fallback diagnostics for S11b2b-28 (no titles or page text)."""
    doc_pages = max(1, int(analysis.get("doc_pages") or max(len(pymupdf_map), len(pypdf2_map))))
    toc_set = {int(p) for p in toc_pages}
    out: list[dict[str, Any]] = []

    for entry in analysis.get("entries") or []:
        if entry.get("matched_extractor") != "offset_fallback":
            continue
        expected = int(entry["expected_start"])
        win_pages = [
            p
            for p in range(max(1, expected - int(radius)), min(doc_pages, expected + int(radius)) + 1)
            if p not in toc_set
        ]
        wide_pages = [
            p
            for p in range(max(1, expected - int(wide_radius)), min(doc_pages, expected + int(wide_radius)) + 1)
            if p not in toc_set
        ]
        clean_title = unicodedata.normalize("NFKC", str(entry.get("title") or "")).strip()
        numbered = bool(re.match(r"^\d+(?:\.\d+)*\.?\s+", clean_title))

        out.append(
            {
                "entry_index": int(entry["index"]),
                "printed_page": int(entry["printed_page"]),
                "expected_start": expected,
                "is_annexure": bool(entry["is_annexure"]),
                "annexure_numeral": entry.get("annexure_numeral"),
                "numbered": numbered,
                "title_word_count": len(clean_title.split()),
                "pymupdf": {
                    "window": _best_match_across_pages(entry, win_pages, pymupdf_map, expected_start=expected),
                    "wide": _best_match_across_pages(entry, wide_pages, pymupdf_map, expected_start=expected),
                },
                "pypdf2": {
                    "window": _best_match_across_pages(entry, win_pages, pypdf2_map, expected_start=expected),
                    "wide": _best_match_across_pages(entry, wide_pages, pypdf2_map, expected_start=expected),
                },
            }
        )
    return out


def _format_contents_shapes_report(diag: dict[str, Any]) -> str:
    out_lines = [
        f"page={rec['page']} extractor={rec['extractor']} line={rec['line_index']} "
        f"starts_with_annexure={'true' if rec['starts_with_annexure'] else 'false'} "
        f"decision={rec['parser_decision']} shape={json.dumps(rec['shape'], ensure_ascii=False)}"
        for rec in diag["lines"]
    ]
    out_lines.append(
        json.dumps(
            {
                "decision_counts": diag["decision_counts"],
                "decision_counts_by_extractor": diag["decision_counts_by_extractor"],
            },
            ensure_ascii=False,
        )
    )
    return "\n".join(out_lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="S11 tree Contents analyzer and structural auditor")
    sub = ap.add_subparsers(dest="command", required=True)

    ap_c = sub.add_parser("contents", help="Analyze PDF Contents pages, offsets, and leaf forecast")
    ap_c.add_argument("--pdf", required=True, help="Explicit path to guideline PDF")
    ap_c.add_argument("--toc-pages", nargs="+", type=int, default=list(DEFAULT_TOC_PAGES))
    ap_c.add_argument("--offset", type=int, default=DEFAULT_BODY_OFFSET)
    ap_c.add_argument("--radius", type=int, default=DEFAULT_OFFSET_WINDOW_RADIUS)
    ap_c.add_argument(
        "--shapes",
        action="store_true",
        help="Print anonymized line shapes and parser decisions for Contents pages (zero original letters).",
    )
    ap_c.add_argument(
        "--explain-fallbacks",
        action="store_true",
        help="Print strictly numeric diagnostics (window and +/-10 best scores) for offset_fallback entries.",
    )
    ap_c.add_argument(
        "--out",
        default=None,
        help="Optional write-once JSON output path (holds headings; keep under data\\, never commit).",
    )

    ap_a = sub.add_parser("audit", help="Audit a built tree JSON against Contents and §4 rules")
    ap_a.add_argument("--tree", required=True, help="Required path to PageIndex tree JSON")
    ap_a.add_argument("--out", required=True, help="Required write-once JSON output path")
    ap_a.add_argument("--pdf", default=None, help="Path to PDF for Contents & offset reference")
    ap_a.add_argument("--contents-json", default=None, help="Optional pre-computed contents JSON")
    ap_a.add_argument("--pre-merge-tree", default=None, help="Optional pre-merge snapshot JSON for Variant C")
    ap_a.add_argument("--toc-pages", nargs="+", type=int, default=list(DEFAULT_TOC_PAGES))
    ap_a.add_argument("--offset", type=int, default=DEFAULT_BODY_OFFSET)

    args = ap.parse_args(argv)

    if args.command == "contents":
        pdf_path = _require_explicit_path(args.pdf, "--pdf")
        out_path = _require_explicit_path(args.out, "--out") if args.out is not None else None
        if args.shapes and args.explain_fallbacks:
            raise SystemExit("--shapes and --explain-fallbacks cannot be used together")
        if args.shapes:
            if out_path is not None:
                raise SystemExit("--out is not supported with --shapes diagnostic mode")
            diag = analyze_contents_shapes(
                pdf_path,
                toc_pages=_parse_int_tuple(args.toc_pages),
                offset=args.offset,
            )
            print(_format_contents_shapes_report(diag))
            return 0
        analysis = analyze_pdf_contents(
            pdf_path,
            toc_pages=_parse_int_tuple(args.toc_pages),
            offset=args.offset,
            radius=args.radius,
        )
        if out_path is not None:
            write_once_json(out_path, analysis)
        summary = contents_summary_counts(analysis)
        if args.explain_fallbacks:
            mupdf_map, pdf2_map = _load_pdf_pages_map(pdf_path, need_pypdf2=True)
            summary["fallback_explanations"] = explain_contents_fallbacks(
                analysis,
                mupdf_map,
                pdf2_map,
                toc_pages=_parse_int_tuple(args.toc_pages),
                radius=args.radius,
            )
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    if args.command == "audit":
        tree_path = _require_explicit_path(args.tree, "--tree")
        out_path = _require_explicit_path(args.out, "--out")
        if args.contents_json is not None:
            contents_payload = json.loads(Path(args.contents_json).read_text(encoding="utf-8"))
            entries = contents_payload["entries"]
        elif args.pdf is not None:
            pdf_path = _require_explicit_path(args.pdf, "--pdf")
            analysis = analyze_pdf_contents(
                pdf_path,
                toc_pages=_parse_int_tuple(args.toc_pages),
                offset=args.offset,
            )
            entries = analysis["entries"]
        else:
            raise SystemExit("Either --pdf or --contents-json is required for audit")

        tree_payload = json.loads(tree_path.read_text(encoding="utf-8"))
        pre_payload = None
        if args.pre_merge_tree is not None:
            pre_path = _require_explicit_path(args.pre_merge_tree, "--pre-merge-tree")
            pre_payload = json.loads(pre_path.read_text(encoding="utf-8"))

        result = audit_tree(tree_payload, entries, pre_merge_tree=pre_payload)
        write_once_json(out_path, result)
        print(json.dumps(audit_summary_counts(result), indent=2, ensure_ascii=False))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ALLOWED_TOP_LEVEL_KEYS",
    "DEFAULT_BODY_OFFSET",
    "DEFAULT_COVERAGE_END",
    "DEFAULT_COVERAGE_START",
    "DEFAULT_OFFSET_WINDOW_RADIUS",
    "DEFAULT_TOC_PAGES",
    "EXPECTED_ANNEXURE_COUNT",
    "FORECAST_MAX_PAGES_PER_NODE",
    "FORECAST_MAX_TOKENS_PER_NODE",
    "IGNORED_TOP_LEVEL_KEYS",
    "TREE_AUDIT_RULES_VERSION",
    "analyze_contents_shapes",
    "analyze_pdf_contents",
    "audit_summary_counts",
    "audit_tree",
    "audit_tree_variant",
    "compute_leaf_forecast",
    "contents_summary_counts",
    "explain_contents_fallbacks",
    "extract_annexure_numeral",
    "fuzzy_title_score",
    "is_annexure_title",
    "is_intro_node",
    "line_shape",
    "main",
    "normalize_title",
    "parse_contents_pages",
    "validate_tree_payload",
    "verify_contents_offsets",
]
