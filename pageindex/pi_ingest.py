"""Offline stage: PDF -> SHA-256 -> cache check -> PageIndex tree -> tree.json -> manifest.

Usage:
  python pi_ingest.py path/to/guideline.pdf --family cdsco-gdp --version 2019 \
      --title "Good Distribution Practices ..." --effective-date 2019-01-01
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from importlib import metadata
from pathlib import Path

from pi_common import (PAGEINDEX_PIN_SHA, DocRecord, Settings, index_backend,
                       installed_pageindex_commit, load_manifest, load_settings, model_route,
                       now_utc, page_count, save_manifest, sha256_file, text_layer_report)


def _pageindex_version() -> str:
    try:
        return metadata.version("pageindex")
    except metadata.PackageNotFoundError:
        return "unknown (source checkout)"


def build_tree(pdf_path: str, s: Settings) -> dict:
    """Run PageIndex in-process. Returns PageIndex's own output dict unchanged.

    R22 (PageIndex 50a71b6): PageIndex's default mode is now "flash", so the standard
    (LLM-built tree) pipeline is called directly via page_index_main; flash runs only when
    index_mode: flash is set explicitly (labelled comparison, ADR 0006). Index and summary
    models are always passed explicitly (P2).
    """
    # _llm_backend is PageIndex's (private) per-call kwargs override for LiteLLM: it is
    # merged into every completion of the indexing lane. Private API: re-check on upgrade.
    #   anthropic/ route: {"max_tokens": litellm_max_tokens} so Claude does not truncate
    #                     long TOC JSON (PageIndex raises on finish_reason == "length").
    #   Azure route:      {"api_base", "api_key"} only, NO max_tokens cap (P3, P4).
    backend = index_backend(s)      # validates models, route, deployment names, credentials
    from pageindex.utils import ConfigLoader, _llm_backend

    token = _llm_backend.set(backend or None)
    try:
        if s.index_mode == "flash":
            from pageindex.flash import page_index_flash
            from pageindex.flash.api import flash_rejection_reason
            result = page_index_flash(pdf_path, summary=True,
                                      summary_model=s.summary_model,
                                      optimize="full", optimize_model=s.summary_model,
                                      use_embedded_toc=True)
            reason = flash_rejection_reason(result, standard_hint="index_mode: standard")
            if reason:
                raise RuntimeError(reason)
            return result
        if s.index_mode != "standard":   # Settings validates this too; never fall through
            raise ValueError(f"unknown index_mode {s.index_mode!r}")

        from pageindex.page_index_classic import page_index_main
        opt = ConfigLoader().load({
            "index_model": s.index_model,
            "summary_model": s.summary_model,
            "toc_check_page_num": s.toc_check_page_num,
            "max_page_num_each_node": s.max_page_num_each_node,
            "max_token_num_each_node": s.max_token_num_each_node,
            "if_add_node_id": s.if_add_node_id,
            "if_add_node_summary": s.if_add_node_summary,
            "if_add_doc_description": s.if_add_doc_description,
            "if_add_node_text": s.if_add_node_text,
        })
        return page_index_main(pdf_path, opt)   # calls asyncio.run internally
    finally:
        _llm_backend.reset(token)


def pageindex_commit_or_refuse(allow_unpinned: bool) -> str:
    """Installed PageIndex commit if it is the pin; otherwise refuse, or (only when
    explicitly allowed) return "unpinned: <commit>" for the manifest."""
    commit = installed_pageindex_commit()
    if commit == PAGEINDEX_PIN_SHA:
        return commit
    if not allow_unpinned:
        raise RuntimeError(f"pageindex commit {commit} is not the pin {PAGEINDEX_PIN_SHA}. Install "
                           "pageindex/requirements_partII.txt, or pass --allow-unpinned-pageindex "
                           "(settings: allow_unpinned_pageindex: true) to record an unpinned build.")
    print(f"[warn] UNPINNED pageindex commit {commit} (pin {PAGEINDEX_PIN_SHA[:7]}); "
          "recorded in the manifest", file=sys.stderr)
    return f"unpinned: {commit}"


def ingest(pdf: str, family: str, version: str, title: str | None = None,
           effective_date: date | None = None, force: bool = False,
           s: Settings | None = None, allow_unpinned: bool | None = None) -> DocRecord:
    """allow_unpinned=None -> settings key allow_unpinned_pageindex (default False)."""
    s = s or load_settings()
    allow_unpinned = s.allow_unpinned_pageindex if allow_unpinned is None else allow_unpinned
    store = Path(s.store_dir)
    (store / "pdfs").mkdir(parents=True, exist_ok=True)
    (store / "trees").mkdir(parents=True, exist_ok=True)

    sha = sha256_file(pdf)
    m = load_manifest(s)
    doc_id = f"{family}@{version}"

    # 1) cache check: identical bytes already indexed -> skip the expensive build
    hit = m.by_hash(sha)
    if hit and Path(hit.tree_path).exists() and not force:
        print(f"[cache] {pdf} already indexed as {hit.doc_id} (sha256 {sha[:12]}…)", file=sys.stderr)
        return hit

    # 1b) PageIndex pin (R22 D1): a tree built by another commit is refused unless allowed
    #     explicitly; then the manifest records "unpinned: <commit>". Checked before any work.
    commit = pageindex_commit_or_refuse(allow_unpinned)

    # 2) same doc_id but different bytes -> the file was modified: rebuild + replace record
    if doc_id in m.documents and m.documents[doc_id].sha256 != sha:
        print(f"[update] {doc_id} content changed; re-indexing", file=sys.stderr)

    # 3) scanned-PDF guard (no text layer -> OCR first, don't build an empty tree)
    tl = text_layer_report(pdf)
    if tl["scanned"]:
        raise RuntimeError(f"{pdf}: {tl['empty_pages']}/{tl['pages']} pages have no text "
                           "layer. Flag for OCR (e.g. ocrmypdf) and re-run.")

    # 4) content-addressed copy so the manifest never points at a moved/edited file
    key = sha[:16]
    pdf_copy = store / "pdfs" / f"{key}.pdf"
    if not pdf_copy.exists():
        shutil.copy2(pdf, pdf_copy)

    # 5) build + persist
    result = build_tree(str(pdf_copy), s)
    tree_path = store / "trees" / f"{key}.json"
    tree_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    # 6) register
    rec = DocRecord(
        doc_id=doc_id, family=family, version=version,
        title=title or result.get("doc_title") or result.get("doc_name") or Path(pdf).stem,
        effective_date=effective_date, sha256=sha,
        pdf_path=str(pdf_copy), tree_path=str(tree_path),
        page_count=page_count(pdf_copy),
        doc_description=result.get("doc_description", ""),
        index_mode=s.index_mode,
        index_model=s.index_model if s.index_mode == "standard" else s.summary_model,
        pageindex_version=_pageindex_version(), built_at=now_utc(),
        summary_model=s.summary_model, index_route=model_route(s.summary_model),
        pageindex_commit=commit,
    )
    m.documents[doc_id] = rec
    save_manifest(s, m)
    n_nodes = sum(1 for _ in _walk(result.get("structure", [])))
    print(f"[built] {doc_id}: {rec.page_count} pages, {n_nodes} nodes -> {tree_path}", file=sys.stderr)
    return rec


def _walk(nodes):
    for n in nodes or []:
        yield n
        yield from _walk(n.get("nodes"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--family", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--title")
    ap.add_argument("--effective-date", type=date.fromisoformat)
    ap.add_argument("--force", action="store_true", help="rebuild even if cached")
    ap.add_argument("--allow-unpinned-pageindex", action="store_true", default=None,
                    help="build with a PageIndex commit other than the pin; recorded as 'unpinned: <commit>'")
    a = ap.parse_args(argv)
    r = ingest(a.pdf, a.family, a.version, a.title, a.effective_date, a.force,
               allow_unpinned=a.allow_unpinned_pageindex)
    print(r.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
