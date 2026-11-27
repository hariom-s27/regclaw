"""Offline S11 retrieval metrics, citation grounding checks (C1-C5), and write-once records."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "pageindex") not in sys.path:
    sys.path.insert(0, str(ROOT / "pageindex"))

from eval.s11.ledger import write_once_json  # noqa: E402
from pi_agent import refusal_detected  # noqa: E402
from pi_common import parse_page_spec  # noqa: E402

METRICS_RULES_VERSION = "s11-metrics-v1"
MIN_PAGE = 1
MAX_PAGE = 116

NA_REASONS = frozenset({
    "unanswerable",
    "no_citations",
    "empty_set",
    "failed_item",
    "refusal",
    "not_applicable_system",
})

ABBREVIATIONS = (
    "e.g.",
    "i.e.",
    "etc.",
    "viz.",
    "vs.",
    "no.",
    "nos.",
    "fig.",
    "sec.",
    "approx.",
    "dr.",
    "mr.",
    "ms.",
)

# Matches any bracketed span starting with "p." or "pp." (case-insensitive) for strict parsing/stripping.
CITE_BRACKET_RE = re.compile(r"\[\s*(pp?)\.([^\[\]]*)\]", re.IGNORECASE)
_SINGLE_INT_RE = re.compile(r"^[0-9]+$", re.ASCII)
_RANGE_INT_RE = re.compile(r"^([0-9]+)\s*[-–]\s*([0-9]+)$", re.ASCII)

_LEADING_LIST_NUM_RE = re.compile(r"^\s*\d+\.$")
_LEADING_LIST_MARKER_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s*")
_MARKDOWN_SYMBOLS_RE = re.compile(r"[*_`#~>]+")
_ABBREV_END_RE = re.compile(
    r"(?:^|[\s(\[\"'\u201c\u2018])("
    + "|".join(re.escape(abbr) for abbr in ABBREVIATIONS)
    + r")$",
    re.IGNORECASE,
)

# Number extraction for exploratory C5 (ASCII digits and letters).
SENTENCE_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![A-Za-z0-9])",
    re.ASCII,
)
PAGE_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?<!\d\.)(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![A-Za-z0-9]|\.\d)",
    re.ASCII,
)

NON_NULLABLE_KEYS = (
    "rules_version",
    "run_id",
    "system",
    "question_id",
    "answerable",
    "R",
    "C",
    "malformed_citations",
)

NULLABLE_METRIC_KEYS = (
    "status",
    "refusal",
    "hit_R",
    "recall_R",
    "precision_R",
    "hit_C",
    "recall_C",
    "precision_C",
    "c1",
    "c2",
    "c3",
    "c4",
    "c5",
    "numbers_checked",
    "numbers_found",
    "numbers_uncited",
    "sentences",
    "cited_sentences",
)

METRIC_RECORD_KEYS = (
    "rules_version",
    "run_id",
    "system",
    "question_id",
    "answerable",
    "status",
    "status_na",
    "refusal",
    "refusal_na",
    "R",
    "C",
    "malformed_citations",
    "hit_R",
    "hit_R_na",
    "recall_R",
    "recall_R_na",
    "precision_R",
    "precision_R_na",
    "hit_C",
    "hit_C_na",
    "recall_C",
    "recall_C_na",
    "precision_C",
    "precision_C_na",
    "c1",
    "c1_na",
    "c2",
    "c2_na",
    "c3",
    "c3_na",
    "c4",
    "c4_na",
    "c5",
    "c5_na",
    "numbers_checked",
    "numbers_checked_na",
    "numbers_found",
    "numbers_found_na",
    "numbers_uncited",
    "numbers_uncited_na",
    "sentences",
    "sentences_na",
    "cited_sentences",
    "cited_sentences_na",
)


@dataclass(frozen=True)
class CitationParseResult:
    """Parsed page list (first-seen distinct ints) and malformed bracket strings."""

    pages: list[int]
    malformed: list[str]


def _expand_range_preserving_zero(start: int, end: int) -> list[int]:
    if start >= 1:
        return parse_page_spec(f"{start}-{end}")
    if end >= 1:
        return [0, *parse_page_spec(f"1-{end}")]
    return [0]


def parse_citations(text: str) -> CitationParseResult:
    """Parse strict S11 citations '[p. N]' and '[pp. N-M]' (N <= M) and collect malformed ones."""
    pages: list[int] = []
    seen: set[int] = set()
    malformed: list[str] = []

    for m in CITE_BRACKET_RE.finditer(text or ""):
        raw = m.group(0)
        prefix = m.group(1)
        body = m.group(2).strip()
        if prefix not in ("p", "pp"):
            malformed.append(raw)
            continue

        if prefix == "p":
            if not _SINGLE_INT_RE.fullmatch(body):
                malformed.append(raw)
                continue
            n = int(body)
            expanded = parse_page_spec(str(n)) if n >= 1 else [0]
        else:
            rm = _RANGE_INT_RE.fullmatch(body)
            if not rm:
                malformed.append(raw)
                continue
            n, m_end = int(rm.group(1)), int(rm.group(2))
            if m_end < n:
                malformed.append(raw)
                continue
            expanded = _expand_range_preserving_zero(n, m_end)

        for p in expanded:
            if p not in seen:
                seen.add(p)
                pages.append(p)

    return CitationParseResult(pages=pages, malformed=malformed)


def _is_sentence_start_char(ch: str) -> bool:
    return ch.isupper() or ch.isdigit() or ch in "(\"'“‘”’"


def _consume_trailing_citations(line: str, pos: int) -> int:
    """Advance past one or more space-separated citation brackets right after punctuation."""
    cur = pos
    while True:
        ws = cur
        while ws < len(line) and line[ws] in " \t":
            ws += 1
        m = CITE_BRACKET_RE.match(line, ws)
        if not m:
            break
        cur = m.end()
    return cur


def _has_at_least_three_words(fragment: str) -> bool:
    cleaned = CITE_BRACKET_RE.sub(" ", fragment)
    cleaned = _LEADING_LIST_MARKER_RE.sub(" ", cleaned)
    cleaned = _MARKDOWN_SYMBOLS_RE.sub(" ", cleaned)
    words = [tok for tok in cleaned.split() if any(ch.isalnum() for ch in tok)]
    return len(words) >= 3


def split_sentences(text: str) -> list[str]:
    """Split answer text into sentences using the S11b2b-05 rules for C4 and C5."""
    sentences: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        cite_spans = [(m.start(), m.end()) for m in CITE_BRACKET_RE.finditer(line)]
        start = 0
        i = 0
        while i < len(line):
            ch = line[i]
            if ch not in ".!?":
                i += 1
                continue
            if any(a <= i < b for a, b in cite_spans):
                i += 1
                continue
            if ch == "." and i > 0 and line[i - 1].isdigit() and i + 1 < len(line) and line[i + 1].isdigit():
                i += 1
                continue
            if ch == "." and _LEADING_LIST_NUM_RE.match(line[: i + 1]):
                i += 1
                continue
            if ch == "." and _ABBREV_END_RE.search(line[: i + 1]):
                i += 1
                continue

            end_pos = _consume_trailing_citations(line, i + 1)
            k = end_pos
            while k < len(line) and line[k] in " \t":
                k += 1

            is_boundary = False
            if k == len(line):
                is_boundary = True
            elif k > end_pos and _is_sentence_start_char(line[k]):
                is_boundary = True

            if is_boundary:
                frag = line[start:end_pos].strip()
                if _has_at_least_three_words(frag):
                    sentences.append(frag)
                start = k
                i = k
                continue

            i += 1

        if start < len(line):
            tail = line[start:].strip()
            if _has_at_least_three_words(tail):
                sentences.append(tail)

    return sentences


def _normalise_number_token(raw: str) -> str:
    return raw.replace(",", "")


def extract_numbers_in_sentence(sentence: str) -> list[str]:
    """Strip citations and line-start list markers, then return normalised numeric tokens."""
    without_cites = CITE_BRACKET_RE.sub(" ", sentence or "")
    cleaned = _LEADING_LIST_MARKER_RE.sub("", without_cites, count=1)
    return [_normalise_number_token(m.group(0)) for m in SENTENCE_NUMBER_RE.finditer(cleaned)]


def extract_page_number_tokens(page_text: str) -> set[str]:
    """Extract normalised whole-token numbers from one page's PyMuPDF text."""
    return {_normalise_number_token(m.group(0)) for m in PAGE_NUMBER_RE.finditer(page_text or "")}


def compute_set_metrics(
    pages: set[int],
    gold_page_sets: list[list[int]],
) -> tuple[bool, float, float | None, str | None]:
    """Compute (hit, recall, precision, precision_na) against gold_page_sets."""
    gold_sets = [set(gs) for gs in gold_page_sets if gs]
    union = set().union(*gold_sets) if gold_sets else set()
    if not union:
        raise ValueError("gold_page_sets must contain at least one page for answerable metrics")

    inter_union = pages & union
    hit = bool(inter_union)
    recall = max(len(pages & gs) / len(gs) for gs in gold_sets)
    if not pages:
        return hit, recall, None, "empty_set"
    return hit, recall, len(inter_union) / len(pages), None


def compute_c5(sentences: list[str], page_texts: dict[int, str]) -> dict[str, Any]:
    """Compute exploratory numeric grounding C5 over split sentences and PyMuPDF page texts."""
    page_token_cache: dict[int, set[str]] = {}
    numbers_checked = 0
    numbers_found = 0
    numbers_uncited = 0

    for sent in sentences:
        cited_pages = parse_citations(sent).pages
        nums = extract_numbers_in_sentence(sent)
        if not cited_pages:
            numbers_uncited += len(nums)
            continue
        for tok in nums:
            numbers_checked += 1
            found = False
            for p in cited_pages:
                if p not in page_token_cache:
                    page_token_cache[p] = extract_page_number_tokens(page_texts.get(p, ""))
                if tok in page_token_cache[p]:
                    found = True
                    break
            if found:
                numbers_found += 1

    if numbers_checked == 0:
        return {
            "numbers_checked": 0,
            "numbers_found": 0,
            "numbers_uncited": numbers_uncited,
            "c5": None,
            "c5_na": "no_citations",
        }
    return {
        "numbers_checked": numbers_checked,
        "numbers_found": numbers_found,
        "numbers_uncited": numbers_uncited,
        "c5": numbers_found / numbers_checked,
        "c5_na": None,
    }


def _set_na(out: dict[str, Any], fields: tuple[str, ...], reason: str) -> None:
    for field in fields:
        out[field] = None
        out[f"{field}_na"] = reason


def _set_val(out: dict[str, Any], field: str, value: Any, na_reason: str | None = None) -> None:
    if na_reason is not None and na_reason not in NA_REASONS:
        raise ValueError(f"Unknown na_reason {na_reason!r}")
    out[field] = value
    out[f"{field}_na"] = na_reason


def _blank_record(
    *,
    run_id: str | int,
    system: str,
    question_id: str,
    answerable: bool,
    r_sorted: list[int],
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "rules_version": METRICS_RULES_VERSION,
        "run_id": run_id,
        "system": system,
        "question_id": question_id,
        "answerable": bool(answerable),
    }
    for key in NULLABLE_METRIC_KEYS:
        if key in ("hit_R", "recall_R", "precision_R"):
            continue
        out[key] = None
        out[f"{key}_na"] = None
    out["R"] = r_sorted
    out["C"] = []
    out["malformed_citations"] = 0
    ordered: dict[str, Any] = {}
    for key in METRIC_RECORD_KEYS:
        ordered[key] = out.get(key)
    return ordered


def _assign_r_metrics(
    out: dict[str, Any],
    r_set: set[int],
    answerable: bool,
    gold_page_sets: list[list[int]],
) -> None:
    if not answerable:
        _set_na(out, ("hit_R", "recall_R", "precision_R"), "unanswerable")
        return
    hit_r, rec_r, prec_r, prec_r_na = compute_set_metrics(r_set, gold_page_sets)
    _set_val(out, "hit_R", hit_r)
    _set_val(out, "recall_R", rec_r)
    _set_val(out, "precision_R", prec_r, prec_r_na)


def _compute_answer_grounding(
    out: dict[str, Any],
    *,
    answer: str,
    r_set: set[int],
    answerable: bool,
    gold_page_sets: list[list[int]],
    page_texts: dict[int, str],
) -> None:
    parsed = parse_citations(answer)
    c_set = set(parsed.pages)
    out["C"] = sorted(c_set)
    out["malformed_citations"] = len(parsed.malformed)
    has_any_cite_bracket = bool(c_set or parsed.malformed)

    if not answerable:
        _set_na(out, ("hit_C", "recall_C", "precision_C", "c3"), "unanswerable")
    else:
        hit_c, rec_c, prec_c, prec_c_na = compute_set_metrics(c_set, gold_page_sets)
        _set_val(out, "hit_C", hit_c)
        _set_val(out, "recall_C", rec_c)
        _set_val(out, "precision_C", prec_c, prec_c_na)
        _set_val(out, "c3", hit_c)

    if not has_any_cite_bracket:
        _set_na(out, ("c1", "c2"), "no_citations")
    else:
        c1_ok = (len(parsed.malformed) == 0) and all(MIN_PAGE <= p <= MAX_PAGE for p in c_set)
        _set_val(out, "c1", c1_ok)
        if not c_set:
            _set_val(out, "c2", None, "no_citations")
        else:
            _set_val(out, "c2", c_set.issubset(r_set))

    sents = split_sentences(answer)
    cited_sents = sum(1 for s in sents if parse_citations(s).pages)
    _set_val(out, "sentences", len(sents))
    _set_val(out, "cited_sentences", cited_sents)

    if len(sents) == 0:
        if not has_any_cite_bracket:
            _set_val(out, "c4", 0.0)
        else:
            _set_val(out, "c4", None, "empty_set")
    else:
        _set_val(out, "c4", cited_sents / len(sents))

    c5_res = compute_c5(sents, page_texts)
    _set_val(out, "numbers_checked", c5_res["numbers_checked"])
    _set_val(out, "numbers_found", c5_res["numbers_found"])
    _set_val(out, "numbers_uncited", c5_res["numbers_uncited"])
    _set_val(out, "c5", c5_res["c5"], c5_res["c5_na"])


def _ordered_record(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: raw[key] for key in METRIC_RECORD_KEYS}


def score_hy5_record(
    hy5_record: dict[str, Any],
    golden_item: dict[str, Any],
    *,
    run_id: str | int,
    out_path: Path | str | None = None,
) -> dict[str, Any]:
    """Score one HY5 retrieval-only record and optionally persist write-once JSON."""
    answerable = bool(golden_item["answerable"])
    gold_sets = golden_item.get("gold_page_sets") or []
    r_list = sorted({int(p) for p in hy5_record.get("selected_pages", [])})
    r_set = set(r_list)

    out = _blank_record(
        run_id=run_id,
        system="HY5",
        question_id=str(golden_item["id"]),
        answerable=answerable,
        r_sorted=r_list,
    )
    _assign_r_metrics(out, r_set, answerable, gold_sets)
    _set_na(
        out,
        (
            "status",
            "refusal",
            "hit_C",
            "recall_C",
            "precision_C",
            "c1",
            "c2",
            "c3",
            "c4",
            "c5",
            "numbers_checked",
            "numbers_found",
            "numbers_uncited",
            "sentences",
            "cited_sentences",
        ),
        "not_applicable_system",
    )
    final = _ordered_record(out)
    if out_path is not None:
        write_once_json(out_path, final)
    return final


def score_result_record(
    record: dict[str, Any],
    golden_item: dict[str, Any],
    page_texts: dict[int, str],
    *,
    run_id: str | int,
    out_path: Path | str | None = None,
) -> dict[str, Any]:
    """Score one PI/HY/WD S11 result record and optionally persist write-once JSON."""
    system = str(record["system"])
    if system == "HY5":
        return score_hy5_record(record, golden_item, run_id=run_id, out_path=out_path)

    answerable = bool(golden_item["answerable"])
    gold_sets = golden_item.get("gold_page_sets") or []
    r_list = sorted({int(p) for p in record.get("pages_given", [])})
    r_set = set(r_list)
    status = str(record["status"])
    answer = str(record.get("answer") or "")
    is_refusal = bool(record["refusal"]) if "refusal" in record else refusal_detected(answer)

    out = _blank_record(
        run_id=run_id,
        system=system,
        question_id=str(golden_item["id"]),
        answerable=answerable,
        r_sorted=r_list,
    )
    _set_val(out, "status", status)
    _set_val(out, "refusal", is_refusal)
    _assign_r_metrics(out, r_set, answerable, gold_sets)

    if status != "ok":
        if answerable:
            _set_val(out, "hit_C", False)
            _set_val(out, "recall_C", 0.0)
            _set_val(out, "precision_C", None, "failed_item")
            _set_val(out, "c3", False)
        else:
            _set_na(out, ("hit_C", "recall_C", "precision_C", "c3"), "unanswerable")
        _set_na(
            out,
            (
                "c1",
                "c2",
                "c4",
                "c5",
                "numbers_checked",
                "numbers_found",
                "numbers_uncited",
                "sentences",
                "cited_sentences",
            ),
            "failed_item",
        )
    elif is_refusal:
        if answerable:
            _set_val(out, "hit_C", False)
            _set_val(out, "recall_C", 0.0)
            _set_val(out, "precision_C", None, "refusal")
            _set_val(out, "c3", False)
        else:
            _set_na(out, ("hit_C", "recall_C", "precision_C", "c3"), "unanswerable")
        _set_na(
            out,
            (
                "c1",
                "c2",
                "c4",
                "c5",
                "numbers_checked",
                "numbers_found",
                "numbers_uncited",
                "sentences",
                "cited_sentences",
            ),
            "refusal",
        )
    else:
        _compute_answer_grounding(
            out,
            answer=answer,
            r_set=r_set,
            answerable=answerable,
            gold_page_sets=gold_sets,
            page_texts=page_texts,
        )

    final = _ordered_record(out)
    if out_path is not None:
        write_once_json(out_path, final)
    return final


__all__ = [
    "ABBREVIATIONS",
    "CITE_BRACKET_RE",
    "CitationParseResult",
    "MAX_PAGE",
    "METRIC_RECORD_KEYS",
    "METRICS_RULES_VERSION",
    "MIN_PAGE",
    "NA_REASONS",
    "NON_NULLABLE_KEYS",
    "NULLABLE_METRIC_KEYS",
    "PAGE_NUMBER_RE",
    "SENTENCE_NUMBER_RE",
    "compute_c5",
    "compute_set_metrics",
    "extract_numbers_in_sentence",
    "extract_page_number_tokens",
    "parse_citations",
    "score_hy5_record",
    "score_result_record",
    "split_sentences",
]
