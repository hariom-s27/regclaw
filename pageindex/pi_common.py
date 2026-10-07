"""Shared pieces for the PageIndex rebuild: settings, manifest, tree helpers, PDF access.

Page numbers everywhere in this project are 1-based PHYSICAL PDF page indices
(page 1 = first page of the file), which is what PageIndex writes into
start_index / end_index. PyMuPDF is 0-based, so we always convert with p - 1.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()
ROOT = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- settings
class Settings(BaseModel):
    store_dir: Path = ROOT / "store"
    # "standard" = PageIndex classic pipeline: the LLM finds the TOC / headings and
    # writes titles (matches the internship report). "flash" = layout-based, LLM only
    # for summaries + tree expansion (released Aug 2026, after the internship).
    index_mode: str = "standard"
    # LiteLLM model names (used inside PageIndex). Provider prefix is required for Claude.
    index_model: str = "anthropic/claude-sonnet-5-5"
    summary_model: str = "anthropic/claude-sonnet-5-5"
    # Anthropic SDK model names (used by our agent + verifier).
    agent_model: str = "claude-sonnet-5-5"
    verifier_model: str = "claude-haiku-4-5-20251001"
    # PageIndex config.yaml options (defaults copied from the repo's config.yaml)
    toc_check_page_num: int = 20
    max_page_num_each_node: int = 10
    max_token_num_each_node: int = 20000
    if_add_node_id: str = "yes"
    if_add_node_summary: str = "yes"
    if_add_doc_description: str = "yes"   # repo default is "no"; we want it for the manifest
    if_add_node_text: str = "no"          # keep text in the PDF, not in tree.json
    # LiteLLM sends no max_tokens from PageIndex; give Claude room for long TOC JSON.
    litellm_max_tokens: int = 16000
    # agent budgets
    agent_max_turns: int = 12
    max_pages_per_fetch: int = 12
    max_pages_total: int = 40
    overview_char_budget: int = 24000


def load_settings(path: Path = ROOT / "pi_settings.yaml") -> Settings:
    data = {}
    if Path(path).exists():
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    return Settings(**data)


# --------------------------------------------------------------------------- manifest
class DocRecord(BaseModel):
    doc_id: str                         # "<family>@<version>", e.g. "cdsco-gdp@2019"
    family: str                         # stable name for all editions of one guideline
    title: str
    version: str
    effective_date: Optional[date] = None
    superseded: bool = False
    sha256: str
    pdf_path: str                       # content-addressed copy inside store/pdfs
    tree_path: str                      # store/trees/<sha16>.json (PageIndex output, untouched)
    page_count: int
    doc_description: str = ""
    index_mode: str
    index_model: str
    pageindex_version: str
    built_at: datetime


class Manifest(BaseModel):
    documents: dict[str, DocRecord] = Field(default_factory=dict)

    def by_hash(self, sha: str) -> Optional[DocRecord]:
        return next((d for d in self.documents.values() if d.sha256 == sha), None)


def manifest_path(s: Settings) -> Path:
    return Path(s.store_dir) / "manifest.json"


def load_manifest(s: Settings) -> Manifest:
    p = manifest_path(s)
    if not p.exists():
        return Manifest()
    return Manifest.model_validate_json(p.read_text(encoding="utf-8"))


def save_manifest(s: Settings, m: Manifest) -> None:
    p = manifest_path(s)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(m.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, p)  # atomic: a crash never leaves a half-written manifest


def resolve_edition(m: Manifest, family: str, version: Optional[str] = None,
                    as_of: Optional[date] = None) -> DocRecord:
    """Pick the edition to answer from.
    - explicit version -> exact match
    - as_of date      -> newest edition whose effective_date <= as_of
    - neither         -> newest non-superseded edition
    """
    cands = [d for d in m.documents.values() if d.family == family]
    if not cands:
        raise KeyError(f"No documents registered for family '{family}'")
    if version:
        for d in cands:
            if d.version == version:
                return d
        raise KeyError(f"{family} has no version '{version}' "
                       f"(have: {sorted(d.version for d in cands)})")
    if as_of:
        cands = [d for d in cands if d.effective_date is None or d.effective_date <= as_of]
    else:
        live = [d for d in cands if not d.superseded]
        cands = live or cands
    if not cands:
        raise KeyError(f"No edition of {family} in force on {as_of}")
    return max(cands, key=lambda d: (d.effective_date or date.min, d.built_at))


# --------------------------------------------------------------------------- hashing / pdf
def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def text_layer_report(pdf_path: str | Path, min_chars: int = 25) -> dict:
    """Detect scanned / image-only PDFs before spending LLM calls on them."""
    import pdfplumber
    empty, total = 0, 0
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            total += 1
            if len((page.extract_text() or "").strip()) < min_chars:
                empty += 1
    return {"pages": total, "empty_pages": empty,
            "scanned": total > 0 and empty / total > 0.9}


_SPEC = re.compile(r"^\s*\d+(\s*-\s*\d+)?(\s*,\s*\d+(\s*-\s*\d+)?)*\s*$")


def parse_page_spec(spec: str) -> list[int]:
    """'5', '3,7', '5-10', '1-3,7,9-12' -> sorted unique ints (same grammar as
    PageIndex's get_page_content tool)."""
    if not _SPEC.match(spec or ""):
        raise ValueError(f"Bad page spec '{spec}'. Use e.g. '5', '5-8' or '3,7,10-12'.")
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = (int(x) for x in part.split("-"))
            if a > b:
                raise ValueError(f"Range '{part}' has start > end")
            pages.update(range(a, b + 1))
        else:
            pages.add(int(part))
    return sorted(pages)


def fetch_pages(pdf_path: str | Path, pages: list[int]) -> dict[int, str]:
    """Physical 1-based page numbers -> page text, via PyMuPDF (0-based internally)."""
    import fitz  # PyMuPDF
    out = {}
    with fitz.open(pdf_path) as doc:
        for p in pages:
            if 1 <= p <= doc.page_count:
                out[p] = doc[p - 1].get_text()
    return out


def page_count(pdf_path: str | Path) -> int:
    import fitz
    with fitz.open(pdf_path) as doc:
        return doc.page_count


# --------------------------------------------------------------------------- tree helpers
def load_tree(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def iter_nodes(nodes: list[dict], depth: int = 1) -> Iterator[tuple[dict, int]]:
    for n in nodes or []:
        yield n, depth
        yield from iter_nodes(n.get("nodes") or [], depth + 1)


def node_map(structure: list[dict]) -> dict[str, dict]:
    return {n["node_id"]: n for n, _ in iter_nodes(structure) if n.get("node_id")}


def subtree_span(node: dict) -> tuple[int, int]:
    """Page span of a node INCLUDING its descendants.
    In the classic pipeline a parent's own end_index can stop where its first child
    starts, so never trust a parent's end_index to cover the whole chapter."""
    starts, ends = [], []
    for n, _ in iter_nodes([node]):
        if n.get("start_index") is not None:
            starts.append(n["start_index"])
        if n.get("end_index") is not None:
            ends.append(n["end_index"])
    return (min(starts) if starts else None, max(ends) if ends else None)


def _line(n: dict, depth: int, summary_chars: int) -> str:
    a, b = subtree_span(n)
    pages = f"p. {a}" if a == b else f"pp. {a}-{b}"
    kids = len(n.get("nodes") or [])
    more = f" [+{kids} sub-sections]" if kids else ""
    s = (n.get("summary") or "").replace("\n", " ").strip()
    if summary_chars and s:
        s = " — " + (s[:summary_chars] + "…" if len(s) > summary_chars else s)
    else:
        s = ""
    return f"{'  ' * (depth - 1)}[{n.get('node_id', '?')}] {n.get('title', '')} ({pages}){more}{s}"


def outline(structure: list[dict], max_depth: int = 2, summary_chars: int = 160,
            char_budget: int = 24000) -> str:
    """Compact, token-cheap view of the tree. Degrades gracefully: if the outline
    is over budget, drop summaries, then drop a level."""
    for depth_cap, chars in ((max_depth, summary_chars), (max_depth, 0),
                             (max(1, max_depth - 1), 0)):
        lines = [_line(n, d, chars) for n, d in iter_nodes(structure) if d <= depth_cap]
        text = "\n".join(lines)
        if len(text) <= char_budget:
            return text
    return text[:char_budget] + "\n…(truncated; use get_node to drill down)"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
