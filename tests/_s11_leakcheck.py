"""Leak-check test helper ensuring zero source words or non-ASCII letters appear in anonymized outputs."""
from __future__ import annotations

import re
import unicodedata

# Fixed schema identifiers emitted by eval.s11.tree_audit diagnostics that must not trigger false positives.
_ALLOWED_SCHEMA_TOKENS = frozenset(
    {
        "accepted",
        "annexure_numeral",
        "annexure_numerals",
        "annexures",
        "best_score",
        "contents_parse_by_extractor",
        "continuation",
        "decision",
        "decision_counts",
        "decision_counts_by_extractor",
        "doc_pages",
        "entries",
        "entry_index",
        "expected_start",
        "extractor",
        "extractor_counts",
        "extractor_disagreements",
        "fallback_explanations",
        "false",
        "forecast",
        "headings_skipped",
        "is_annexure",
        "join_size",
        "largest_span",
        "line",
        "no_page",
        "null",
        "numbered",
        "offset",
        "offset_fallback",
        "offset_fallbacks",
        "order_breaks",
        "page",
        "printed_page",
        "pymupdf",
        "pypdf2",
        "rules_version",
        "shape",
        "skipped",
        "skipped:empty",
        "skipped:header",
        "skipped:heading",
        "skipped:orphan_number",
        "spans_gt_10",
        "spans_gt_10_tokens_lt_20000_pymupdf",
        "spans_gt_10_tokens_lt_20000_pypdf2",
        "starts_with_annexure",
        "title_word_count",
        "toc_pages",
        "true",
        "wide",
        "window",
    }
)

_SOURCE_WORD_RE = re.compile(r"[^\W\d_]{3,}")
_SCHEMA_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_:]*")


def _strip_schema_tokens(text: str) -> str:
    return _SCHEMA_TOKEN_RE.sub(
        lambda m: " " if m.group(0) in _ALLOWED_SCHEMA_TOKENS else m.group(0),
        text,
    )


def assert_no_source_words(output: str, sources: list[str]) -> None:
    """Assert no >=3-letter source word (exact or casefolded) nor non-ASCII letter leaks into output."""
    scrubbed = _strip_schema_tokens(output)
    scrubbed_cf = scrubbed.casefold()

    source_words: set[str] = set()
    non_ascii_letters: set[str] = set()
    for src in sources:
        for w in _SOURCE_WORD_RE.findall(src):
            source_words.add(w)
        for ch in src:
            if ord(ch) > 127 and unicodedata.category(ch).startswith("L"):
                non_ascii_letters.add(ch)

    for word in sorted(source_words):
        if word in scrubbed or word.casefold() in scrubbed_cf:
            raise AssertionError(f"Source word {word!r} leaked into output")

    for ch in sorted(non_ascii_letters):
        if ch in scrubbed or ch.casefold() in scrubbed_cf:
            raise AssertionError(f"Non-ASCII source letter {ch!r} leaked into output")
