"""HY and HY5 hybrid page-retrieval baselines (BM25 + guarded Azure embeddings + RRF k=60)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "pageindex") not in sys.path:
    sys.path.insert(0, str(ROOT / "pageindex"))

from eval.s11.answer import run_hy_answer  # noqa: E402
from eval.s11.ledger import CostLedger, UsageLogger, write_once_json  # noqa: E402
from pi_client import ModelVersionGuard, build_guarded_async_openai  # noqa: E402
from pi_common import (  # noqa: E402
    JUDGE_TIMEOUT_S,
    Settings,
    azure_credentials,
    azure_v1_base_url,
    check_deployment_names,
    load_settings,
)
from retrieval_eval.bm25 import BM25  # noqa: E402

EMBED_BATCH_SIZE = 16
RRF_K = 60
HY_MAX_K = 40
HY_FALLBACK_K = 5
HY5_K = 5
BM25_K1 = 1.2
BM25_B = 0.75

HY_RETRIEVAL_KEYS = (
    "question_id",
    "pi_distinct_pages",
    "k",
    "k_flagged",
    "selected_pages",
    "ranking",
    "empty_pages",
    "non_empty_pages",
    "bm25_hits",
    "embed_stats",
    "k_rrf",
    "bm25_k1",
    "bm25_b",
    "embedding_model",
    "embedding_deployment",
)


def _valid_page_num(p: int) -> int:
    if isinstance(p, bool) or not isinstance(p, int) or p < 1:
        raise ValueError(f"Page numbers must be positive ints, got {p!r}")
    return p


def partition_pages(pages: dict[int, str]) -> tuple[dict[int, str], list[int]]:
    """Split pages into non-empty stripped texts (keyed by int page asc) and sorted empty page ints."""
    non_empty: dict[int, str] = {}
    empty_pages: list[int] = []
    for raw_p in sorted(pages, key=lambda k: int(k)):
        p = _valid_page_num(raw_p)
        cleaned = (pages[raw_p] or "").strip()
        if cleaned:
            non_empty[p] = cleaned
        else:
            empty_pages.append(p)
    return non_empty, empty_pages


def k_for(pi_distinct_pages: int) -> tuple[int, bool]:
    """Matched reading budget rule: 0 -> (5, True); n >= 1 -> (min(n, 40), False)."""
    if isinstance(pi_distinct_pages, bool) or not isinstance(pi_distinct_pages, int) or pi_distinct_pages < 0:
        raise ValueError(f"pi_distinct_pages must be a non-negative int, got {pi_distinct_pages!r}")
    if pi_distinct_pages == 0:
        return HY_FALLBACK_K, True
    return min(pi_distinct_pages, HY_MAX_K), False


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embedding_cache_key(text: str, *, model: str, deployment: str) -> str:
    """Deterministic hex cache ID from sha256(text) + model + deployment."""
    material = f"{_text_sha256(text)}\0{model}\0{deployment}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def save_embedding_cache(
    cache_dir: Path | str,
    text: str,
    *,
    model: str,
    deployment: str,
    embedding: list[float],
) -> Path:
    """Persist one cached embedding vector write-once under cache_dir / '<sha256_hex>.json'."""
    key_hex = embedding_cache_key(text, model=model, deployment=deployment)
    payload = {
        "text_sha256": _text_sha256(text),
        "model": str(model),
        "deployment": str(deployment),
        "embedding": [float(x) for x in embedding],
    }
    return write_once_json(Path(cache_dir) / f"{key_hex}.json", payload)


def _read_cached_embedding(path: Path, *, text_sha: str, model: str, deployment: str) -> list[float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        data.get("text_sha256") != text_sha
        or data.get("model") != model
        or data.get("deployment") != deployment
        or not isinstance(data.get("embedding"), list)
    ):
        raise ValueError(f"Embedding cache mismatch at {path}")
    return [float(x) for x in data["embedding"]]


def _build_guarded_embedding_client(
    *,
    deployment: str,
    usage_log: UsageLogger | None,
    guard: ModelVersionGuard | None,
    timeout_s: float,
    inner_transport: Any = None,
) -> Any:
    check_deployment_names({"embedding_model": deployment})
    if usage_log is None:
        raise ValueError("Guarded embedding client requires usage_log when cache misses occur")
    endpoint, api_key = azure_credentials()
    return build_guarded_async_openai(
        base_url=azure_v1_base_url(endpoint),
        api_key=api_key,
        timeout=timeout_s,
        usage_log=usage_log,
        guard=guard,
        inner_transport=inner_transport,
    )


def _assert_no_running_event_loop() -> None:
    """Fail loudly if called inside an active asyncio event loop (PR-69)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "Hybrid retrieval functions are synchronous and cannot be called inside a running asyncio event loop"
    )


def load_or_compute_embeddings(
    items: dict[Any, str],
    *,
    cache_dir: Path | str,
    model: str,
    deployment: str,
    client: Any = None,
    usage_log: UsageLogger | None = None,
    guard: ModelVersionGuard | None = None,
    timeout_s: float = JUDGE_TIMEOUT_S,
    inner_transport: Any = None,
) -> tuple[dict[Any, list[float]], dict[str, int]]:
    """Load cached embeddings or fetch uncached inputs via guarded Azure AsyncOpenAI in batches of 16."""
    _assert_no_running_event_loop()
    cache_root = Path(cache_dir)
    vectors: dict[Any, list[float]] = {}
    pending_by_hex: dict[str, tuple[str, str]] = {}
    hex_to_ids: dict[str, list[Any]] = {}
    missing_hexes: list[str] = []
    cache_hits = 0
    cache_misses = 0

    for item_id, raw_text in items.items():
        text = raw_text or ""
        text_sha = _text_sha256(text)
        key_hex = embedding_cache_key(text, model=model, deployment=deployment)
        cache_path = cache_root / f"{key_hex}.json"
        if cache_path.exists():
            vectors[item_id] = _read_cached_embedding(cache_path, text_sha=text_sha, model=model, deployment=deployment)
            cache_hits += 1
            continue
        cache_misses += 1
        if key_hex not in pending_by_hex:
            pending_by_hex[key_hex] = (text, text_sha)
            hex_to_ids[key_hex] = []
            missing_hexes.append(key_hex)
        hex_to_ids[key_hex].append(item_id)

    api_batches = 0
    if missing_hexes:
        async def _fetch_missing() -> None:
            nonlocal api_batches
            owns_client = client is None
            emb_client = (
                client
                if client is not None
                else _build_guarded_embedding_client(
                    deployment=deployment,
                    usage_log=usage_log,
                    guard=guard,
                    timeout_s=timeout_s,
                    inner_transport=inner_transport,
                )
            )
            try:
                for start in range(0, len(missing_hexes), EMBED_BATCH_SIZE):
                    batch_hexes = missing_hexes[start : start + EMBED_BATCH_SIZE]
                    batch_texts = [pending_by_hex[h][0] for h in batch_hexes]
                    api_batches += 1
                    resp = await emb_client.embeddings.create(model=deployment, input=batch_texts)
                    ordered_data = sorted(resp.data, key=lambda d: int(getattr(d, "index", 0)))
                    if len(ordered_data) != len(batch_hexes):
                        raise RuntimeError(
                            f"Embedding batch returned {len(ordered_data)} vectors for {len(batch_hexes)} inputs"
                        )
                    for key_hex, item_obj in zip(batch_hexes, ordered_data):
                        vec = [float(x) for x in item_obj.embedding]
                        raw_txt, _sha = pending_by_hex[key_hex]
                        save_embedding_cache(cache_root, raw_txt, model=model, deployment=deployment, embedding=vec)
                        for target_id in hex_to_ids[key_hex]:
                            vectors[target_id] = vec
            finally:
                if owns_client and hasattr(emb_client, "close"):
                    await emb_client.close()

        asyncio.run(_fetch_missing())

    return vectors, {
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "api_batches": api_batches,
        "api_inputs": len(missing_hexes),
    }


def bm25_rank_pages(query: str, non_empty_pages: dict[int, str]) -> list[tuple[int, float]]:
    """Score non-empty pages with repo retrieval_eval.bm25.BM25 and keep score > 0 only."""
    if not non_empty_pages:
        return []
    clauses = [{"id": int(p), "title": "", "text": non_empty_pages[p]} for p in sorted(non_empty_pages)]
    scorer = BM25(clauses, k1=BM25_K1, b=BM25_B)
    ranked = scorer.rank(query, k=len(clauses))
    return [(int(cid), float(score)) for cid, score in ranked if score > 0.0]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"Embedding dimension mismatch: {len(a)} != {len(b)}")
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def dense_rank_pages(query_vec: list[float], page_vecs: dict[int, list[float]]) -> list[tuple[int, float]]:
    """Rank every non-empty page by descending cosine similarity, ties broken by lower int page."""
    scored = [(int(p), float(_cosine_similarity(query_vec, vec))) for p, vec in page_vecs.items()]
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored


def rrf_fuse(
    bm25_list: list[tuple[int, float]],
    dense_list: list[tuple[int, float]],
    *,
    non_empty_pages: Iterable[int],
    k_rrf: int = RRF_K,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion over BM25 (>0 only) and dense (all non-empty pages), tie -> lower page."""
    bm25_ranks = {int(p): (idx, float(score)) for idx, (p, score) in enumerate(bm25_list, start=1)}
    dense_ranks = {int(p): (idx, float(score)) for idx, (p, score) in enumerate(dense_list, start=1)}
    rows: list[dict[str, Any]] = []
    for p in sorted({int(x) for x in non_empty_pages}):
        b_entry = bm25_ranks.get(p)
        d_entry = dense_ranks.get(p)
        rrf_score = (1.0 / (k_rrf + b_entry[0]) if b_entry is not None else 0.0) + (
            1.0 / (k_rrf + d_entry[0]) if d_entry is not None else 0.0
        )
        rows.append(
            {
                "page": p,
                "rrf_score": rrf_score,
                "bm25_rank": b_entry[0] if b_entry is not None else None,
                "bm25_score": b_entry[1] if b_entry is not None else 0.0,
                "dense_rank": d_entry[0] if d_entry is not None else None,
                "dense_score": d_entry[1] if d_entry is not None else 0.0,
            }
        )
    rows.sort(key=lambda row: (-row["rrf_score"], row["page"]))
    return rows


def rank_hybrid_pages(
    question: str,
    pages: dict[int, str],
    *,
    cache_dir: Path | str,
    s: Settings | None = None,
    model: str | None = None,
    deployment: str | None = None,
    usage_log: UsageLogger | None = None,
    guard: ModelVersionGuard | None = None,
    embed_client: Any = None,
    inner_transport: Any = None,
) -> dict[str, Any]:
    """Compute full RRF hybrid ranking over non-empty pages while listing excluded empty pages."""
    _assert_no_running_event_loop()
    s = s or load_settings()
    emb_model = model or s.embedding_model
    emb_deployment = deployment or s.embedding_model
    non_empty, empty_pages = partition_pages(pages)
    bm25_list = bm25_rank_pages(question, non_empty)

    if non_empty:
        embed_inputs: dict[Any, str] = {"query": question}
        for p in sorted(non_empty):
            embed_inputs[p] = non_empty[p]
        vectors, embed_stats = load_or_compute_embeddings(
            embed_inputs,
            cache_dir=cache_dir,
            model=emb_model,
            deployment=emb_deployment,
            client=embed_client,
            usage_log=usage_log,
            guard=guard,
            timeout_s=s.judge_timeout_s,
            inner_transport=inner_transport,
        )
        query_vec = vectors["query"]
        page_vecs = {p: vectors[p] for p in sorted(non_empty)}
        dense_list = dense_rank_pages(query_vec, page_vecs)
        fused = rrf_fuse(bm25_list, dense_list, non_empty_pages=non_empty.keys(), k_rrf=RRF_K)
    else:
        fused = []
        embed_stats = {"cache_hits": 0, "cache_misses": 0, "api_batches": 0, "api_inputs": 0}

    return {
        "empty_pages": empty_pages,
        "non_empty_pages": sorted(non_empty),
        "bm25_hits": [p for p, _ in bm25_list],
        "ranking": fused,
        "ranked_pages": [row["page"] for row in fused],
        "embed_stats": embed_stats,
        "k_rrf": RRF_K,
        "bm25_k1": BM25_K1,
        "bm25_b": BM25_B,
        "embedding_model": emb_model,
        "embedding_deployment": emb_deployment,
    }


def _build_hy_retrieval_record(
    *,
    question_id: str,
    pi_distinct_pages: int,
    k: int,
    k_flagged: bool,
    ranked_data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "pi_distinct_pages": pi_distinct_pages,
        "k": k,
        "k_flagged": k_flagged,
        "selected_pages": list(ranked_data["ranked_pages"][:k]),
        "ranking": ranked_data["ranking"],
        "empty_pages": ranked_data["empty_pages"],
        "non_empty_pages": ranked_data["non_empty_pages"],
        "bm25_hits": ranked_data["bm25_hits"],
        "embed_stats": ranked_data["embed_stats"],
        "k_rrf": ranked_data["k_rrf"],
        "bm25_k1": ranked_data["bm25_k1"],
        "bm25_b": ranked_data["bm25_b"],
        "embedding_model": ranked_data["embedding_model"],
        "embedding_deployment": ranked_data["embedding_deployment"],
    }


def run_hy5_retrieval(
    question_id: str,
    question: str,
    pages: dict[int, str],
    *,
    cache_dir: Path | str,
    s: Settings | None = None,
    model: str | None = None,
    deployment: str | None = None,
    usage_log: UsageLogger | None = None,
    guard: ModelVersionGuard | None = None,
    embed_client: Any = None,
    inner_transport: Any = None,
    out_dir: Path | str | None = None,
) -> dict[str, Any]:
    """HY5 retrieval-only baseline: top 5 pages of the shared RRF ranking."""
    ranked_data = rank_hybrid_pages(
        question,
        pages,
        cache_dir=cache_dir,
        s=s,
        model=model,
        deployment=deployment,
        usage_log=usage_log,
        guard=guard,
        embed_client=embed_client,
        inner_transport=inner_transport,
    )
    record = {
        "system": "HY5",
        "question_id": question_id,
        "k": HY5_K,
        "selected_pages": list(ranked_data["ranked_pages"][:HY5_K]),
        "ranking": ranked_data["ranking"],
        "empty_pages": ranked_data["empty_pages"],
        "non_empty_pages": ranked_data["non_empty_pages"],
        "bm25_hits": ranked_data["bm25_hits"],
        "embed_stats": ranked_data["embed_stats"],
        "k_rrf": ranked_data["k_rrf"],
        "bm25_k1": ranked_data["bm25_k1"],
        "bm25_b": ranked_data["bm25_b"],
        "embedding_model": ranked_data["embedding_model"],
        "embedding_deployment": ranked_data["embedding_deployment"],
    }
    if out_dir is not None:
        write_once_json(Path(out_dir) / f"{question_id}.json", record)
    return record


def run_hy(
    question_id: str,
    question: str,
    pages: dict[int, str],
    *,
    pi_distinct_pages: int,
    cache_dir: Path | str,
    s: Settings | None = None,
    model: str | None = None,
    deployment: str | None = None,
    ledger: CostLedger,
    usage_log: UsageLogger,
    guard: ModelVersionGuard | None = None,
    embed_client: Any = None,
    inner_transport: Any = None,
    out_dir: Path | str | None = None,
    retrieval_out_dir: Path | str | None = None,
    sdk_client: Any = None,
    verifier_sdk_client: Any = None,
) -> dict[str, Any]:
    """Run HY end-to-end: RRF top-k_for(pi_distinct_pages) -> optional retrieval JSON -> 16-key answer JSON."""
    s = s or load_settings()
    k, k_flagged = k_for(pi_distinct_pages)
    ranked_data = rank_hybrid_pages(
        question,
        pages,
        cache_dir=cache_dir,
        s=s,
        model=model,
        deployment=deployment,
        usage_log=usage_log,
        guard=guard,
        embed_client=embed_client,
        inner_transport=inner_transport,
    )
    retrieval_record = _build_hy_retrieval_record(
        question_id=question_id,
        pi_distinct_pages=pi_distinct_pages,
        k=k,
        k_flagged=k_flagged,
        ranked_data=ranked_data,
    )
    if retrieval_out_dir is not None:
        write_once_json(Path(retrieval_out_dir) / f"{question_id}.json", retrieval_record)

    selected_pages_map = {p: pages[p] for p in retrieval_record["selected_pages"]}
    answer_record = run_hy_answer(
        question_id,
        question,
        selected_pages_map,
        s=s,
        ledger=ledger,
        usage_log=usage_log,
        out_dir=out_dir,
        sdk_client=sdk_client,
        verifier_sdk_client=verifier_sdk_client,
    )
    return {"answer": answer_record, "retrieval": retrieval_record}


__all__ = [
    "BM25_B",
    "BM25_K1",
    "EMBED_BATCH_SIZE",
    "HY5_K",
    "HY_FALLBACK_K",
    "HY_MAX_K",
    "HY_RETRIEVAL_KEYS",
    "RRF_K",
    "bm25_rank_pages",
    "dense_rank_pages",
    "embedding_cache_key",
    "k_for",
    "load_or_compute_embeddings",
    "partition_pages",
    "rank_hybrid_pages",
    "rrf_fuse",
    "run_hy",
    "run_hy5_retrieval",
    "save_embedding_cache",
]
