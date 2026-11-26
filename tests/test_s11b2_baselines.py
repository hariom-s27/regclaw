"""Offline Part E tests for eval.s11.hybrid (HY/HY5), eval.s11.whole_doc (WD), and guarded embeddings."""
import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace as NS

import httpx
import pytest

import _netguard
from eval.s11 import answer, hybrid, whole_doc
from eval.s11.ledger import CostLedger, UsageLogger, WriteOnceError

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-key-not-real"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("REGCLAW_STUB", "1")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    _netguard.block_network(monkeypatch, "s11b2 baselines tests forbid network")


class _CountingEmbedClient:
    """Fake async embeddings client mapping text substrings to deterministic vectors."""

    def __init__(self, vec_for_text):
        self.vec_for_text = vec_for_text
        self.calls = []
        self.embeddings = NS(create=self.create)

    async def create(self, *, model, input):
        batch = list(input)
        self.calls.append({"model": model, "input": batch})
        data = [
            NS(index=i, embedding=list(self.vec_for_text(txt)))
            for i, txt in enumerate(batch)
        ]
        return NS(data=data)


class _ScriptedAnthropic:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = NS(create=self.create)

    def create(self, **kw):
        snap = dict(kw)
        if "messages" in snap:
            snap["messages"] = copy.deepcopy(snap["messages"])
        self.requests.append(snap)
        return self.responses.pop(0)


def _text(t, stop="end_turn", model="claude-sonnet-5-5"):
    return NS(
        content=[NS(type="text", text=t)] if t else [],
        stop_reason=stop,
        model=model,
        usage=NS(input_tokens=20, output_tokens=10, cache_read_input_tokens=0, cache_creation_input_tokens=0),
    )


def _verdict_ok(evidence="Evidence"):
    return NS(
        content=[
            NS(
                type="tool_use",
                input={"verdicts": [{"claim_id": 1, "verdict": "supported", "evidence": evidence, "reason": ""}]},
            )
        ],
        stop_reason="tool_use",
        model="claude-haiku-5-5",
        usage=NS(input_tokens=12, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0),
    )


def test_rrf_hand_computed_two_list_example_and_missing_bm25_page():
    # bm25_list has only pages 10 and 20 (rank 1 and 2); page 2 and page 30 have bm25 == 0 -> omitted.
    # dense_list has all 4 non-empty pages: 2 (rank 1), 20 (rank 2), 10 (rank 3), 30 (rank 4).
    # Scores:
    # - page 10: 1/61 + 1/63
    # - page 20: 1/62 + 1/62  (Wait: test exact tie between page 2 and page 10 by adjusting!)
    bm25_list = [(10, 2.0), (2, 1.0)]  # rank 1 -> 10, rank 2 -> 2
    dense_list = [(2, 0.9), (10, 0.8), (20, 0.7), (30, 0.6)]  # page 2: 1/62+1/61 == page 10: 1/61+1/62 -> exact tie!

    fused = hybrid.rrf_fuse(bm25_list, dense_list, non_empty_pages=[2, 10, 20, 30], k_rrf=60)
    assert [row["page"] for row in fused] == [2, 10, 20, 30]  # tie -> lower int page (2 < 10)

    by_page = {row["page"]: row for row in fused}
    assert by_page[2]["rrf_score"] == pytest.approx(1 / 62 + 1 / 61)
    assert by_page[10]["rrf_score"] == pytest.approx(1 / 61 + 1 / 62)
    # Missing from bm25_list receives 0 BM25 contribution (dense-only)
    assert by_page[20]["bm25_rank"] is None
    assert by_page[20]["rrf_score"] == pytest.approx(1 / 63)
    assert by_page[30]["rrf_score"] == pytest.approx(1 / 64)


def test_bm25_uses_repo_scorer_numeric_ties_and_positive_only():
    # Equal TF/length on pages 2 and 10 -> score tie resolves 2 before 10 (numeric, not "10" < "2")
    pages = {
        2: "storage cold chain temperature",
        10: "storage cold chain temperature",
        3: "unrelated ethics committee quorum",
    }
    non_empty, empty = hybrid.partition_pages(pages)
    assert empty == []
    bm25_hits = hybrid.bm25_rank_pages("storage", non_empty)
    assert [p for p, _ in bm25_hits] == [2, 10]  # page 3 had score 0 and is excluded


def test_empty_pages_excluded_from_retrieval_and_listed_in_meta(tmp_path):
    pages = {
        1: "",
        2: "   \n\t ",
        3: "biosafety containment level",
        10: "informed consent procedure",
    }
    client = _CountingEmbedClient(
        lambda txt: [1.0, 0.0] if ("biosafety" in txt or "query" in txt) else [0.0, 1.0]
    )
    out = hybrid.rank_hybrid_pages(
        "biosafety",
        pages,
        cache_dir=tmp_path / "cache",
        embed_client=client,
    )
    assert out["empty_pages"] == [1, 2]
    assert out["non_empty_pages"] == [3, 10]
    assert [r["page"] for r in out["ranking"]] == [3, 10]
    sent_inputs = [inp for call in client.calls for inp in call["input"]]
    assert "" not in sent_inputs and "   \n\t " not in sent_inputs
    assert len(sent_inputs) == 3  # query + 2 non-empty pages


def test_k_for_budget_rules():
    assert hybrid.k_for(0) == (5, True)
    assert hybrid.k_for(1) == (1, False)
    assert hybrid.k_for(3) == (3, False)
    assert hybrid.k_for(40) == (40, False)
    assert hybrid.k_for(55) == (40, False)
    for bad in (-1, True, False, 1.5):
        with pytest.raises(ValueError):
            hybrid.k_for(bad)  # type: ignore[arg-type]


def test_embedding_cache_write_once_reused_zero_calls_and_batches_of_16(tmp_path):
    # 35 non-empty pages + 1 query = 36 unique texts -> batches [16, 16, 4]
    pages = {i: f"unique clause content page {i}" for i in range(1, 36)}
    client1 = _CountingEmbedClient(lambda txt: [1.0, float(len(txt) % 7)])
    cache_dir = tmp_path / "emb_cache"

    out1 = hybrid.rank_hybrid_pages("unique clause query", pages, cache_dir=cache_dir, embed_client=client1)
    assert [len(c["input"]) for c in client1.calls] == [16, 16, 4]
    assert out1["embed_stats"] == {"cache_hits": 0, "cache_misses": 36, "api_batches": 3, "api_inputs": 36}

    # Second run reuses cache completely: 0 API calls
    client2 = _CountingEmbedClient(lambda txt: pytest.fail("should not call embeddings API on cache hit"))
    out2 = hybrid.rank_hybrid_pages("unique clause query", pages, cache_dir=cache_dir, embed_client=client2)
    assert client2.calls == []
    assert out2["embed_stats"] == {"cache_hits": 36, "cache_misses": 0, "api_batches": 0, "api_inputs": 0}
    assert out2["ranked_pages"] == out1["ranked_pages"]

    # Cache keys include model + deployment
    assert hybrid.embedding_cache_key("abc", model="m1", deployment="d1") != hybrid.embedding_cache_key(
        "abc", model="m1", deployment="d2"
    )

    # Direct write to an existing cache key raises WriteOnceError
    with pytest.raises(WriteOnceError):
        hybrid.save_embedding_cache(
            cache_dir,
            "unique clause query",
            model=out1["embedding_model"],
            deployment=out1["embedding_deployment"],
            embedding=[1.0, 2.0],
        )


def test_guarded_async_openai_embeddings_logs_and_checks_model_version(monkeypatch, tmp_path):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://res.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", SECRET)
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    pi_client = importlib.import_module("pi_client")
    guard = pi_client.ModelVersionGuard()

    observed_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        observed_requests.append(body)
        n = len(body["input"])
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "text-embedding-3-small-v1",
                "data": [{"object": "embedding", "index": i, "embedding": [1.0, float(i)]} for i in range(n)],
                "usage": {"prompt_tokens": 5 * n, "total_tokens": 5 * n},
            },
        )

    transport = httpx.MockTransport(handler)
    pages = {1: "Page one text", 2: "Page two text"}
    out = hybrid.rank_hybrid_pages(
        "Page one",
        pages,
        cache_dir=tmp_path / "emb_cache",
        usage_log=usage_log,
        guard=guard,
        inner_transport=transport,
    )
    assert len(observed_requests) == 1
    assert out["ranked_pages"][0] == 1
    assert guard.observed_models["text-embedding-3-small"] == "text-embedding-3-small-v1"

    usage_rows = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(usage_rows) == 1
    assert usage_rows[0]["lane"] == "embedding" and usage_rows[0]["cost_usd"] is None


def test_hy5_is_first_5_of_same_ranking_and_retrieval_only(tmp_path):
    pages = {i: f"guideline topic {i} alpha" for i in range(1, 9)}
    client = _CountingEmbedClient(lambda txt: [1.0, 0.1 * len(txt)])
    cache_dir = tmp_path / "cache"

    full = hybrid.rank_hybrid_pages("topic 1", pages, cache_dir=cache_dir, embed_client=client)
    hy5_out = tmp_path / "hy5"
    rec = hybrid.run_hy5_retrieval(
        "gtp-01",
        "topic 1",
        pages,
        cache_dir=cache_dir,
        embed_client=client,
        out_dir=hy5_out,
    )
    assert rec["system"] == "HY5"
    assert rec["k"] == 5
    assert rec["selected_pages"] == full["ranked_pages"][:5]
    assert json.loads((hy5_out / "gtp-01.json").read_text(encoding="utf-8")) == rec


def test_hy_end_to_end_matched_budget_and_wd_116_page_blocks_with_empty_headers(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    cache_dir = tmp_path / "emb_cache"

    # 1) HY end-to-end with k_for(2) == (2, False), exact 15-key retrieval record + exact 16-key answer record
    pages = {
        1: "Storage shall be at 2-8 C.",
        2: "",
        3: "Labeling includes batch number.",
        4: "Transport uses insulated boxes.",
    }
    emb_client = _CountingEmbedClient(
        lambda txt: [1.0, 0.0] if ("storage" in txt.lower() or "insulated" in txt.lower()) else [0.1, 1.0]
    )
    hy_sdk = _ScriptedAnthropic(
        _text("Storage shall be at 2-8 C [p. 1]."),
        _verdict_ok(evidence="Storage shall be at 2-8 C."),
    )
    hy_res = hybrid.run_hy(
        "gtp-01",
        "What storage temperature is required?",
        pages,
        pi_distinct_pages=2,
        cache_dir=cache_dir,
        ledger=ledger,
        usage_log=usage_log,
        embed_client=emb_client,
        out_dir=tmp_path / "hy_ans",
        retrieval_out_dir=tmp_path / "hy_ret",
        sdk_client=hy_sdk,
    )
    ret = hy_res["retrieval"]
    assert tuple(ret.keys()) == hybrid.HY_RETRIEVAL_KEYS
    assert len(ret) == 15
    assert ret["k"] == 2 and ret["k_flagged"] is False
    assert ret["empty_pages"] == [2] and ret["non_empty_pages"] == [1, 3, 4]
    assert ret["selected_pages"] == [row["page"] for row in ret["ranking"][:2]]
    assert ret["k_rrf"] == 60 and ret["bm25_k1"] == 1.2 and ret["bm25_b"] == 0.75
    assert hy_res["answer"]["system"] == "HY"
    assert tuple(hy_res["answer"].keys()) == answer.RESULT_KEYS
    assert (tmp_path / "hy_ans" / "gtp-01.json").exists()
    assert (tmp_path / "hy_ret" / "gtp-01.json").exists()

    # 2) WD end-to-end on synthetic 116-page PDF with empty pages preserving headers
    pdf_path = tmp_path / "synthetic_116.pdf"
    with pymupdf.open() as doc:
        for p in range(1, 117):
            page = doc.new_page()
            if p not in (5, 50):
                page.insert_text((72, 72), f"Clause on physical page {p}.")
        doc.save(pdf_path)

    all_pages, empty_wd = whole_doc.extract_all_pages(pdf_path)
    assert len(all_pages) == 116 and empty_wd == [5, 50]
    wd_meta = whole_doc.build_wd_document_meta(all_pages)
    assert wd_meta["total_pages"] == 116
    assert "=== PDF page 5 ===\n\n\n=== PDF page 6 ===" in wd_meta["document_text"]
    headers = [line for line in wd_meta["document_text"].splitlines() if line.startswith("=== PDF page ")]
    assert len(headers) == 116
    assert headers[0] == "=== PDF page 1 ===" and headers[-1] == "=== PDF page 116 ==="

    wd_sdk = _ScriptedAnthropic(
        _text("Clause applies on page 10 [p. 10]."),
        _verdict_ok(evidence="Clause on physical page 10."),
    )
    wd_res = whole_doc.run_wd(
        "gtp-01",
        "Which clause applies on page 10?",
        pdf_path=pdf_path,
        ledger=ledger,
        usage_log=usage_log,
        out_dir=tmp_path / "wd_ans",
        meta_out_dir=tmp_path / "wd_meta",
        sdk_client=wd_sdk,
    )
    assert wd_res["system"] == "WD" and len(wd_res["pages_given"]) == 116
    assert tuple(wd_res.keys()) == answer.RESULT_KEYS
    assert len(wd_sdk.requests[0]["system"]) == 2
    assert wd_sdk.requests[0]["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert "=== PDF page 50 ===\n\n\n=== PDF page 51 ===" in wd_sdk.requests[0]["system"][1]["text"]


def test_fresh_subprocess_import_hybrid_and_whole_doc_from_repo_root():
    for mod in ("eval.s11.hybrid", "eval.s11.whole_doc"):
        plain = subprocess.run(
            [sys.executable, "-c", f"import {mod}"],
            cwd=ROOT,
            env={**os.environ, "REGCLAW_STUB": "1", "LANGFUSE_ENABLED": "false"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert plain.returncode == 0, f"{mod}: {plain.stderr}"

        guarded_code = textwrap.dedent(
            f"""
            import builtins
            import os
            import socket
            from pathlib import Path

            data_dir = Path("data").resolve()

            def deny_net(*a, **k):
                raise AssertionError("Network forbidden")

            socket.socket.connect = deny_net
            orig_open = builtins.open

            def guarded_open(file, *a, **k):
                p = Path(os.fspath(file)).resolve() if not isinstance(file, int) else None
                assert p is None or (p != data_dir and not p.is_relative_to(data_dir)), f"data access forbidden: {{p}}"
                return orig_open(file, *a, **k)

            builtins.open = guarded_open
            import {mod}
            assert hasattr({mod}, "__file__")
            """
        ).strip()
        guarded = subprocess.run(
            [sys.executable, "-c", guarded_code],
            cwd=ROOT,
            env={**os.environ, "REGCLAW_STUB": "1", "LANGFUSE_ENABLED": "false"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert guarded.returncode == 0, f"{mod}: {guarded.stderr}"
