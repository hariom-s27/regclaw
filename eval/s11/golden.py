"""S11 golden set v2 checker, freeze tool, and verified frozen-golden loader.

Usage:
  .venv\\Scripts\\python -m eval.s11.golden check --golden PATH --pdf PATH [--show-terms]
  .venv\\Scripts\\python -m eval.s11.golden freeze --golden PATH --pdf PATH --out eval/s11/golden_v1.json --manifest eval/s11/manifests/golden_v1.json

Exit codes:
  0  check passed / frozen golden + manifest written
  1  verbatim overlap check failed (>= 10 consecutive PDF words in a field)
  2  schema, path, argument, missing-file, or PDF SHA-256 validation error
  3  refused: --out or --manifest already exists (write-once)
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "pageindex") not in sys.path:
    sys.path.insert(0, str(ROOT / "pageindex"))

from eval.s11.freeze import FreezeError, check_manifest, freeze_manifest, validate_rel_path  # noqa: E402
from pi_common import fetch_pages, page_count, sha256_file  # noqa: E402

SCHEMA = "s11-golden-v2"
EXPECTED_PDF_SHA256 = "5986189705d960d6f03dbd3980fd86e48166e75b242214539428730d0797d1e6"
EXPECTED_PAGE_CONVENTION = "all pages are physical PDF page index (1-based)"
EXPECTED_REFUSAL = "The guideline does not state this."

DRAFT_TEST_KEY = "test"
DRAFT_DEV_KEY = "dev"
DRAFT_EXTRA_STR_KEYS = ("corpus", "status")

MIN_PAGE = 1
MAX_PAGE = 116
TEST_COUNT = 16
TEST_ANSWERABLE_COUNT = 12
TEST_UNANSWERABLE_COUNT = 4
DEV_COUNT = 3
EXPECTED_TEST_IDS = tuple(f"gtp-{i:02d}" for i in range(1, TEST_COUNT + 1))
EXPECTED_DEV_IDS = tuple(f"dev-{i:02d}" for i in range(1, DEV_COUNT + 1))

ITEM_FIELDS = (
    "id",
    "type",
    "answerable",
    "question",
    "reference",
    "reference_claims",
    "gold_page_sets",
    "unanswerable_note",
)

ANSWERABLE_TYPES = frozenset({
    "single_fact",
    "multi_page",
    "annexure",
    "cross_reference",
    "condition",
})
UNANSWERABLE_TYPES = frozenset({
    "unanswerable_near_miss",
    "unanswerable_out_of_scope",
})
ALLOWED_TYPES = ANSWERABLE_TYPES | UNANSWERABLE_TYPES

VERBATIM_WINDOW = 10
TEXT_LAYER_THRESHOLD = 0.50

# Fixed English stopword list for text-layer term filtering (no external download).
STOPWORDS = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "almost", "alone",
    "along", "already", "also", "although", "always", "am", "among", "an", "and",
    "another", "any", "anyone", "anything", "anywhere", "are", "around", "as",
    "at", "back", "be", "became", "because", "become", "becomes", "been",
    "before", "behind", "being", "below", "between", "beyond", "both", "but",
    "by", "can", "cannot", "could", "did", "do", "does", "doing", "done",
    "down", "during", "each", "either", "else", "enough", "even", "ever",
    "every", "everyone", "everything", "few", "for", "from", "further", "get",
    "gets", "got", "had", "has", "have", "having", "he", "her", "here", "hers",
    "herself", "him", "himself", "his", "how", "however", "i", "if", "in",
    "into", "is", "it", "its", "itself", "just", "keep", "kept", "less",
    "made", "make", "makes", "many", "may", "me", "might", "more", "most",
    "much", "must", "my", "myself", "neither", "never", "no", "nobody",
    "none", "nor", "not", "nothing", "now", "of", "off", "often", "on",
    "once", "one", "only", "or", "other", "others", "otherwise", "our", "ours",
    "ourselves", "out", "over", "own", "part", "per", "perhaps", "put",
    "quite", "rather", "same", "see", "seem", "seemed", "seems", "several",
    "shall", "she", "should", "since", "so", "some", "someone", "something",
    "somewhere", "still", "such", "take", "taken", "than", "that", "the",
    "their", "theirs", "them", "themselves", "then", "there", "these", "they",
    "this", "those", "though", "through", "throughout", "thus", "to",
    "together", "too", "toward", "towards", "under", "unless", "until", "up",
    "upon", "us", "used", "using", "very", "was", "we", "well", "were",
    "what", "whatever", "when", "whenever", "where", "whereas", "wherever",
    "whether", "which", "while", "who", "whoever", "whom", "whose", "why",
    "will", "with", "within", "without", "would", "yet", "you", "your",
    "yours", "yourself", "yourselves",
})

_PUNCT_OR_UNDERSCORE_RE = re.compile(r"[^\w\s]|_")
_ALNUM_TOKEN_RE = re.compile(r"[a-z0-9]+")


class GoldenValidationError(ValueError):
    """Schema, ID, field, SHA-256, or manifest validation failure."""


@dataclass(frozen=True)
class VerbatimHit:
    item_id: str
    field: str
    window_count: int


@dataclass(frozen=True)
class TextLayerEntry:
    item_id: str
    set_index: int
    min_share: float | None
    claims_below_half: int
    has_no_terms: bool
    flagged: bool
    claim_details: tuple[tuple[int, float | None, tuple[str, ...]], ...]


@dataclass(frozen=True)
class GoldenCheckResult:
    pdf_sha256: str
    page_count: int
    test_items: list[dict]
    dev_items: list[dict]
    counts_by_type: dict[str, int]
    test_answerable: int
    test_unanswerable: int
    dev_count: int
    verbatim_hits: list[VerbatimHit]
    text_layer: list[TextLayerEntry]
    flag_count: int


def normalise_verbatim(text: str) -> list[str]:
    """Lowercase, replace punctuation/underscores with spaces, collapse whitespace to words."""
    return _PUNCT_OR_UNDERSCORE_RE.sub(" ", text.lower()).split()


def tokenize_alnum(text: str) -> list[str]:
    """Lowercase alphanumeric [a-z0-9]+ tokenizer shared by claim terms and page text."""
    return _ALNUM_TOKEN_RE.findall(text.lower())


def extract_claim_terms(claim: str, stopwords: frozenset[str] = STOPWORDS) -> list[str]:
    """Extract unique checkable claim terms: digit-bearing tokens or non-stopword len >= 4."""
    terms: list[str] = []
    seen: set[str] = set()
    for tok in tokenize_alnum(claim):
        has_digit = any(ch.isdigit() for ch in tok)
        if not has_digit and (len(tok) < 4 or tok in stopwords):
            continue
        if tok not in seen:
            seen.add(tok)
            terms.append(tok)
    return terms


def claim_term_coverage(
    claim: str,
    page_tokens: set[str],
    page_prefixes5: set[str],
) -> tuple[float | None, tuple[str, ...]]:
    """Compute share of claim terms found in a page set (None if 0 checkable terms)."""
    terms = extract_claim_terms(claim)
    if not terms:
        return None, ()
    missing: list[str] = []
    found = 0
    for term in terms:
        if any(ch.isdigit() for ch in term) or len(term) < 5:
            matched = term in page_tokens
        else:
            matched = term[:5] in page_prefixes5
        if matched:
            found += 1
        else:
            missing.append(term)
    return found / len(terms), tuple(missing)


def _validate_item(item: object, idx: int, split_name: str, max_page: int) -> dict:
    if not isinstance(item, dict):
        raise GoldenValidationError(f"{split_name}[{idx}] must be an object")
    expected_keys = set(ITEM_FIELDS)
    actual_keys = set(item.keys())
    extra = sorted(actual_keys - expected_keys)
    missing = sorted(expected_keys - actual_keys)
    if extra or missing:
        raise GoldenValidationError(
            f"{split_name}[{idx}] invalid keys (missing={missing}, unknown={extra})"
        )

    item_id = item["id"]
    if not isinstance(item_id, str) or not item_id.strip():
        raise GoldenValidationError(f"{split_name}[{idx}].id must be a non-empty string")

    item_type = item["type"]
    if not isinstance(item_type, str) or item_type not in ALLOWED_TYPES:
        raise GoldenValidationError(f"{item_id}: invalid type {item_type!r}")

    ans = item["answerable"]
    if type(ans) is not bool:
        raise GoldenValidationError(f"{item_id}: answerable must be a bool, got {type(ans).__name__}")

    if (item_type in UNANSWERABLE_TYPES) != (ans is False):
        raise GoldenValidationError(
            f"{item_id}: type {item_type!r} conflicts with answerable={ans}"
        )

    question = item["question"]
    if not isinstance(question, str) or not question.strip():
        raise GoldenValidationError(f"{item_id}: question must be a non-empty string")

    reference = item["reference"]
    claims = item["reference_claims"]
    page_sets = item["gold_page_sets"]
    note = item["unanswerable_note"]

    if ans:
        if not isinstance(reference, str) or not reference.strip():
            raise GoldenValidationError(f"{item_id}: answerable reference must be non-empty")
        if not isinstance(claims, list) or not (1 <= len(claims) <= 4):
            raise GoldenValidationError(f"{item_id}: reference_claims must have 1-4 items")
        for c_idx, claim in enumerate(claims):
            if not isinstance(claim, str) or not claim.strip():
                raise GoldenValidationError(f"{item_id}: reference_claims[{c_idx}] must be non-empty string")
        if not isinstance(page_sets, list) or not page_sets:
            raise GoldenValidationError(f"{item_id}: gold_page_sets must be a non-empty list")
        for s_idx, page_set in enumerate(page_sets):
            if not isinstance(page_set, list) or not page_set:
                raise GoldenValidationError(f"{item_id}: gold_page_sets[{s_idx}] must be non-empty list")
            for p in page_set:
                if type(p) is not int or not (MIN_PAGE <= p <= max_page):
                    raise GoldenValidationError(
                        f"{item_id}: gold_page_sets[{s_idx}] invalid page {p!r} (expected int in {MIN_PAGE}..{max_page})"
                    )
            for i in range(len(page_set) - 1):
                if page_set[i] >= page_set[i + 1]:
                    raise GoldenValidationError(
                        f"{item_id}: gold_page_sets[{s_idx}] must be strictly sorted and duplicate-free"
                    )
        if note != "":
            raise GoldenValidationError(f"{item_id}: answerable unanswerable_note must be empty string")
    else:
        if type(reference) is not str or reference != EXPECTED_REFUSAL:
            raise GoldenValidationError(
                f"{item_id}: unanswerable reference must equal {EXPECTED_REFUSAL!r}"
            )
        if claims != [] or not isinstance(claims, list):
            raise GoldenValidationError(f"{item_id}: unanswerable reference_claims must be []")
        if page_sets != [] or not isinstance(page_sets, list):
            raise GoldenValidationError(f"{item_id}: unanswerable gold_page_sets must be []")
        if not isinstance(note, str) or not note.strip():
            raise GoldenValidationError(f"{item_id}: unanswerable_note must be a non-empty string")

    return item


def _validate_splits(
    raw_test: object,
    raw_dev: object,
    *,
    max_page: int,
) -> tuple[list[dict], list[dict]]:
    if not isinstance(raw_test, list):
        raise GoldenValidationError("test list must be a JSON array")
    if not isinstance(raw_dev, list):
        raise GoldenValidationError("dev list must be a JSON array")

    test_items = [_validate_item(it, i, "test", max_page) for i, it in enumerate(raw_test)]
    dev_items = [_validate_item(it, i, "dev", max_page) for i, it in enumerate(raw_dev)]

    test_ids = [it["id"] for it in test_items]
    if len(test_ids) != len(set(test_ids)):
        raise GoldenValidationError("duplicate id in test items")
    if len(test_items) != TEST_COUNT or set(test_ids) != set(EXPECTED_TEST_IDS):
        raise GoldenValidationError(
            f"test items must have exact IDs gtp-01..gtp-{TEST_COUNT:02d} (count={len(test_items)})"
        )

    ans_count = sum(1 for it in test_items if it["answerable"])
    unans_count = len(test_items) - ans_count
    if ans_count != TEST_ANSWERABLE_COUNT or unans_count != TEST_UNANSWERABLE_COUNT:
        raise GoldenValidationError(
            f"test items must have {TEST_ANSWERABLE_COUNT} answerable and {TEST_UNANSWERABLE_COUNT} "
            f"unanswerable (got {ans_count}/{unans_count})"
        )

    dev_ids = [it["id"] for it in dev_items]
    if len(dev_ids) != len(set(dev_ids)):
        raise GoldenValidationError("duplicate id in dev items")
    if len(dev_items) != DEV_COUNT or set(dev_ids) != set(EXPECTED_DEV_IDS):
        raise GoldenValidationError(
            f"dev items must have exact IDs dev-01..dev-{DEV_COUNT:02d} (count={len(dev_items)})"
        )

    return test_items, dev_items


def _validate_common_header(
    doc: dict,
    *,
    actual_pdf_sha256: str | None,
    expected_pdf_sha256: str,
) -> str:
    conv = doc["page_convention"]
    if type(conv) is not str or conv != EXPECTED_PAGE_CONVENTION:
        raise GoldenValidationError(
            f"page_convention must equal {EXPECTED_PAGE_CONVENTION!r}"
        )

    file_sha = doc["pdf_sha256"]
    if type(file_sha) is not str or file_sha != expected_pdf_sha256:
        raise GoldenValidationError(
            f"pdf_sha256 mismatch: expected {expected_pdf_sha256}, got {file_sha!r}"
        )
    if actual_pdf_sha256 is not None and file_sha != actual_pdf_sha256:
        raise GoldenValidationError(
            f"pdf_sha256 does not match --pdf sha256 ({actual_pdf_sha256})"
        )
    return file_sha


def validate_draft_doc(
    doc: object,
    *,
    actual_pdf_sha256: str,
    pdf_page_count: int,
    expected_pdf_sha256: str = EXPECTED_PDF_SHA256,
    draft_test_key: str = DRAFT_TEST_KEY,
    draft_dev_key: str = DRAFT_DEV_KEY,
) -> tuple[list[dict], list[dict]]:
    """Validate draft golden JSON shape (S11b2-02/-03/-05 and S11b2-24)."""
    if not isinstance(doc, dict):
        raise GoldenValidationError("golden JSON root must be an object")

    required_top = {"pdf_sha256", "page_convention", draft_test_key, draft_dev_key}
    allowed_top = required_top | set(DRAFT_EXTRA_STR_KEYS)
    actual_top = set(doc.keys())
    unknown = sorted(actual_top - allowed_top)
    missing = sorted(required_top - actual_top)
    if unknown or missing:
        raise GoldenValidationError(
            f"invalid top-level keys (missing={missing}, unknown={unknown})"
        )

    for extra_key in DRAFT_EXTRA_STR_KEYS:
        if extra_key in doc and type(doc[extra_key]) is not str:
            raise GoldenValidationError(f"optional top-level field {extra_key!r} must be a string")

    _validate_common_header(
        doc,
        actual_pdf_sha256=actual_pdf_sha256,
        expected_pdf_sha256=expected_pdf_sha256,
    )
    max_page = min(MAX_PAGE, pdf_page_count)
    return _validate_splits(doc[draft_test_key], doc[draft_dev_key], max_page=max_page)


def validate_frozen_doc(
    doc: object,
    *,
    expected_pdf_sha256: str = EXPECTED_PDF_SHA256,
) -> dict:
    """Validate frozen s11-golden-v2 top-level shape and all items."""
    if not isinstance(doc, dict):
        raise GoldenValidationError("frozen golden JSON root must be an object")

    expected_top = {"schema", "pdf_sha256", "page_convention", "test", "dev"}
    actual_top = set(doc.keys())
    unknown = sorted(actual_top - expected_top)
    missing = sorted(expected_top - actual_top)
    if unknown or missing:
        raise GoldenValidationError(
            f"invalid frozen top-level keys (missing={missing}, unknown={unknown})"
        )
    if doc["schema"] != SCHEMA:
        raise GoldenValidationError(f"unsupported schema {doc['schema']!r}; expected {SCHEMA!r}")

    _validate_common_header(
        doc,
        actual_pdf_sha256=None,
        expected_pdf_sha256=expected_pdf_sha256,
    )
    _validate_splits(doc["test"], doc["dev"], max_page=MAX_PAGE)
    return doc


def find_verbatim_leaks(
    items: list[dict],
    page_texts: dict[int, str],
    *,
    window: int = VERBATIM_WINDOW,
) -> list[VerbatimHit]:
    """Fail if any 10-consecutive-word window of a text field occurs in joined PDF text."""
    joined = " ".join(page_texts[p] for p in sorted(page_texts))
    pdf_words = normalise_verbatim(joined)
    if len(pdf_words) < window:
        return []
    pdf_windows = {
        tuple(pdf_words[i : i + window])
        for i in range(len(pdf_words) - window + 1)
    }

    hits: list[VerbatimHit] = []
    for item in items:
        fields: list[tuple[str, str]] = [
            ("question", item["question"]),
            ("reference", item["reference"]),
            *[
                (f"reference_claims[{idx}]", claim)
                for idx, claim in enumerate(item["reference_claims"])
            ],
            ("unanswerable_note", item["unanswerable_note"]),
        ]
        for field_name, value in fields:
            words = normalise_verbatim(value)
            if len(words) < window:
                continue
            count = sum(
                1
                for i in range(len(words) - window + 1)
                if tuple(words[i : i + window]) in pdf_windows
            )
            if count > 0:
                hits.append(VerbatimHit(item_id=item["id"], field=field_name, window_count=count))
    return hits


def run_text_layer_check(items: list[dict], page_texts: dict[int, str]) -> list[TextLayerEntry]:
    """Compute per-item, per-gold-set claim-term coverage warnings (never alters exit code)."""
    entries: list[TextLayerEntry] = []
    for item in items:
        if not item["answerable"]:
            continue
        for set_idx, page_set in enumerate(item["gold_page_sets"]):
            set_text = "\n".join(page_texts.get(p, "") for p in page_set)
            page_tokens = set(tokenize_alnum(set_text))
            page_prefixes5 = {tok[:5] for tok in page_tokens if len(tok) >= 5}

            details: list[tuple[int, float | None, tuple[str, ...]]] = []
            numeric_shares: list[float] = []
            claims_below_half = 0
            has_no_terms = False

            for c_idx, claim in enumerate(item["reference_claims"]):
                share, missing = claim_term_coverage(claim, page_tokens, page_prefixes5)
                details.append((c_idx, share, missing))
                if share is None:
                    has_no_terms = True
                else:
                    numeric_shares.append(share)
                    if share < TEXT_LAYER_THRESHOLD:
                        claims_below_half += 1

            min_share = min(numeric_shares) if numeric_shares else None
            flagged = claims_below_half > 0 or has_no_terms
            entries.append(
                TextLayerEntry(
                    item_id=item["id"],
                    set_index=set_idx,
                    min_share=min_share,
                    claims_below_half=claims_below_half,
                    has_no_terms=has_no_terms,
                    flagged=flagged,
                    claim_details=tuple(details),
                )
            )
    return entries


def check_golden(
    golden_path: Path | str,
    pdf_path: Path | str,
    *,
    expected_pdf_sha256: str = EXPECTED_PDF_SHA256,
    draft_test_key: str = DRAFT_TEST_KEY,
    draft_dev_key: str = DRAFT_DEV_KEY,
) -> GoldenCheckResult:
    """Validate draft schema, PDF SHA-256, verbatim overlap, and text-layer coverage."""
    g_path = Path(golden_path)
    p_path = Path(pdf_path)
    if not g_path.is_file():
        raise GoldenValidationError(f"golden file not found: {g_path}")
    if not p_path.is_file():
        raise GoldenValidationError(f"PDF file not found: {p_path}")

    try:
        doc = json.loads(g_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoldenValidationError(f"unreadable or invalid golden JSON: {g_path}") from exc

    actual_sha = sha256_file(p_path)
    n_pages = page_count(p_path)
    test_items, dev_items = validate_draft_doc(
        doc,
        actual_pdf_sha256=actual_sha,
        pdf_page_count=n_pages,
        expected_pdf_sha256=expected_pdf_sha256,
        draft_test_key=draft_test_key,
        draft_dev_key=draft_dev_key,
    )

    all_pages = list(range(1, n_pages + 1))
    page_texts = fetch_pages(p_path, all_pages)
    all_items = test_items + dev_items
    verbatim_hits = find_verbatim_leaks(all_items, page_texts)
    text_layer = run_text_layer_check(all_items, page_texts)
    flag_count = sum(1 for entry in text_layer if entry.flagged)

    counts_by_type = {t: 0 for t in sorted(ALLOWED_TYPES)}
    for item in all_items:
        counts_by_type[item["type"]] += 1

    return GoldenCheckResult(
        pdf_sha256=actual_sha,
        page_count=n_pages,
        test_items=test_items,
        dev_items=dev_items,
        counts_by_type=counts_by_type,
        test_answerable=sum(1 for it in test_items if it["answerable"]),
        test_unanswerable=sum(1 for it in test_items if not it["answerable"]),
        dev_count=len(dev_items),
        verbatim_hits=verbatim_hits,
        text_layer=text_layer,
        flag_count=flag_count,
    )


def _ordered_item(item: dict) -> dict:
    return {field: item[field] for field in ITEM_FIELDS}


def build_frozen_golden_dict(res: GoldenCheckResult) -> dict:
    """Build s11-golden-v2 output dict with exact key order and items sorted by id."""
    return {
        "schema": SCHEMA,
        "pdf_sha256": res.pdf_sha256,
        "page_convention": EXPECTED_PAGE_CONVENTION,
        "test": [_ordered_item(it) for it in sorted(res.test_items, key=lambda x: x["id"])],
        "dev": [_ordered_item(it) for it in sorted(res.dev_items, key=lambda x: x["id"])],
    }


def freeze_golden(
    golden_path: Path | str,
    pdf_path: Path | str,
    out_path: str,
    manifest_path: str,
    *,
    root: Path = ROOT,
    expected_pdf_sha256: str = EXPECTED_PDF_SHA256,
    draft_test_key: str = DRAFT_TEST_KEY,
    draft_dev_key: str = DRAFT_DEV_KEY,
    now: str | None = None,
) -> tuple[GoldenCheckResult, Path, Path]:
    """Run hard checks, write golden_v1.json write-once (LF), then write its freeze manifest."""
    try:
        out_lex, out_res, out_posix = validate_rel_path(root, out_path)
        manifest_lex, manifest_res, manifest_posix = validate_rel_path(root, manifest_path)
    except FreezeError as exc:
        raise GoldenValidationError(str(exc)) from exc

    if out_posix == manifest_posix or out_res == manifest_res:
        raise GoldenValidationError("--out and --manifest must be distinct paths")

    res = check_golden(
        golden_path,
        pdf_path,
        expected_pdf_sha256=expected_pdf_sha256,
        draft_test_key=draft_test_key,
        draft_dev_key=draft_dev_key,
    )
    if res.verbatim_hits:
        raise VerbatimCheckError(res)

    if out_lex.exists() or out_lex.is_symlink():
        raise FileExistsError(f"output golden file already exists (write-once): {out_posix}")
    if manifest_lex.exists() or manifest_lex.is_symlink():
        raise FileExistsError(f"manifest already exists (write-once): {manifest_posix}")

    frozen_doc = build_frozen_golden_dict(res)
    payload = json.dumps(frozen_doc, indent=2, ensure_ascii=False) + "\n"
    out_lex.parent.mkdir(parents=True, exist_ok=True)
    with out_lex.open("x", encoding="utf-8", newline="\n") as fh:
        fh.write(payload)

    manifest_written, _ = freeze_manifest([out_posix], manifest_posix, root=root, now=now)
    return res, out_lex, manifest_written


class VerbatimCheckError(RuntimeError):
    """Raised during freeze when verbatim check reports >= 1 overlap."""

    def __init__(self, result: GoldenCheckResult) -> None:
        self.result = result
        super().__init__(f"verbatim check failed with {len(result.verbatim_hits)} hit(s)")


def _to_repo_rel(path_or_rel: Path | str, root: Path) -> str:
    p = Path(path_or_rel)
    if not p.is_absolute():
        return str(path_or_rel)
    resolved_root = Path(root).resolve()
    resolved_target = p.resolve()
    try:
        return resolved_target.relative_to(resolved_root).as_posix()
    except ValueError as exc:
        raise GoldenValidationError(f"path escapes repository root: {path_or_rel}") from exc


def load_frozen_golden(
    path: Path | str,
    manifest: Path | str,
    *,
    root: Path = ROOT,
    expected_pdf_sha256: str = EXPECTED_PDF_SHA256,
) -> dict:
    """Verify freeze manifest via eval.s11.freeze, then validate frozen golden_v1.json."""
    target_rel = _to_repo_rel(path, root)
    manifest_rel = _to_repo_rel(manifest, root)
    try:
        _, target_resolved, target_posix = validate_rel_path(root, target_rel)
        ok, messages = check_manifest(manifest_rel, root=root)
        _, manifest_resolved, _ = validate_rel_path(root, manifest_rel)
    except FreezeError as exc:
        raise GoldenValidationError(str(exc)) from exc

    if not ok:
        raise GoldenValidationError("; ".join(messages))

    manifest_doc = json.loads(manifest_resolved.read_text(encoding="utf-8"))
    manifest_paths = [entry["path"] for entry in manifest_doc["files"]]
    if manifest_paths != [target_posix]:
        raise GoldenValidationError(
            f"manifest {manifest_rel} covers {manifest_paths}, expected [{target_posix}]"
        )

    try:
        doc = json.loads(target_resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoldenValidationError(f"unreadable or invalid frozen golden JSON: {target_posix}") from exc

    return validate_frozen_doc(doc, expected_pdf_sha256=expected_pdf_sha256)


def _format_check_report(res: GoldenCheckResult, *, show_terms: bool) -> list[str]:
    lines: list[str] = ["Text-layer check (warning only):"]
    for entry in res.text_layer:
        min_str = "none" if entry.min_share is None else f"{entry.min_share:.2f}"
        no_terms_tag = " NO_TERMS" if entry.has_no_terms else ""
        flag_tag = " FLAG" if entry.flagged else ""
        lines.append(
            f"  {entry.item_id} set={entry.set_index} min_share={min_str} "
            f"below_0.50={entry.claims_below_half}{no_terms_tag}{flag_tag}"
        )
        if show_terms:
            for c_idx, share, missing in entry.claim_details:
                if share is None:
                    lines.append(f"    claim[{c_idx}] share=none NO_TERMS")
                elif missing:
                    lines.append(
                        f"    claim[{c_idx}] share={share:.2f} missing_terms={list(missing)}"
                    )

    lines.append("Summary:")
    lines.append(f"  test_answerable={res.test_answerable} test_unanswerable={res.test_unanswerable}")
    lines.append(f"  dev_count={res.dev_count}")
    type_parts = " ".join(f"{k}={v}" for k, v in sorted(res.counts_by_type.items()) if v > 0)
    lines.append(f"  types: {type_parts}")
    lines.append(f"  flags={res.flag_count}")
    if res.verbatim_hits:
        lines.append(f"  verbatim=FAIL ({len(res.verbatim_hits)} field(s))")
        for hit in res.verbatim_hits:
            lines.append(f"    VERBATIM id={hit.item_id} field={hit.field} windows={hit.window_count}")
    else:
        lines.append("  verbatim=PASS")
    return lines


def main(
    argv: list[str] | None = None,
    *,
    root: Path = ROOT,
    expected_pdf_sha256: str = EXPECTED_PDF_SHA256,
    now: str | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.s11.golden",
        description="S11 golden v2 checker and write-once freeze CLI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="validate golden draft and run verbatim/text-layer checks")
    p_check.add_argument("--golden", required=True, help="path to golden draft JSON")
    p_check.add_argument("--pdf", required=True, help="path to source PDF")
    p_check.add_argument("--show-terms", action="store_true", help="print missing claim terms on flagged claims")

    p_freeze = sub.add_parser("freeze", help="check draft, write golden_v1.json, and write SHA manifest")
    p_freeze.add_argument("--golden", required=True, help="path to golden draft JSON")
    p_freeze.add_argument("--pdf", required=True, help="path to source PDF")
    p_freeze.add_argument("--out", required=True, help="repo-relative output golden_v1.json path")
    p_freeze.add_argument("--manifest", required=True, help="repo-relative manifest output path")

    args = parser.parse_args(argv)
    if args.command == "check":
        try:
            res = check_golden(
                args.golden,
                args.pdf,
                expected_pdf_sha256=expected_pdf_sha256,
            )
        except GoldenValidationError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        for line in _format_check_report(res, show_terms=args.show_terms):
            print(line)
        return 1 if res.verbatim_hits else 0

    try:
        res, out_written, manifest_written = freeze_golden(
            args.golden,
            args.pdf,
            args.out,
            args.manifest,
            root=root,
            expected_pdf_sha256=expected_pdf_sha256,
            now=now,
        )
    except FileExistsError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    except VerbatimCheckError as exc:
        for line in _format_check_report(exc.result, show_terms=False):
            print(line)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (GoldenValidationError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for line in _format_check_report(res, show_terms=False):
        print(line)
    print(f"written {out_written.as_posix()} and {manifest_written.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
