"""Offline fake-mode tests for S11 Part C run driver (S11b2c-02..05, -07, K1-K3, K6-K9, paid lock)."""
from __future__ import annotations

import asyncio
import copy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
from types import SimpleNamespace
from typing import Any

import anthropic
import fitz
import httpx
import openai
import pytest

from eval.s11 import answer as s11_answer
from eval.s11 import driver as s11_driver
from eval.s11 import hybrid as s11_hybrid
from eval.s11 import whole_doc as s11_wd
from eval.s11.freeze import freeze_manifest
from eval.s11.golden import (
    EXPECTED_DEV_IDS,
    EXPECTED_PAGE_CONVENTION,
    EXPECTED_REFUSAL,
    EXPECTED_TEST_IDS,
)
from eval.s11.ledger import (
    CapExceededError,
    CostLedger,
    PaidLockHeld,
    UsageLogger,
    WriteOnceError,
    paid_lock,
)
import pi_agent
from pi_client import AnthropicRetryClient, ModelVersionChangedError, ModelVersionGuard
from pi_common import Settings, sha256_file
from tests._s11_leakcheck import assert_no_source_words

_SYNTH_PDF_TEXTS = [
    "Zephyr Quokka Zorbik Vexel.",
    "Marmot Wombat Plonko Snorf.",
]
_SYNTH_TREE_DESC = "Pangolin Axolotl"
_SYNTH_TREE_SUMMARY = "Ocelot Narwhal"
_SYNTH_GOLDEN_Q_WORD = "Kinkajou Platypus"
_SYNTH_GOLDEN_REF_WORD = "Capybara Meerkat"
_SYNTH_GOLDEN_NOTE_WORD = "Binturong Quetzal"
_SYNTH_SOURCES = [
    *_SYNTH_PDF_TEXTS,
    _SYNTH_TREE_DESC,
    _SYNTH_TREE_SUMMARY,
    _SYNTH_GOLDEN_Q_WORD,
    _SYNTH_GOLDEN_REF_WORD,
    _SYNTH_GOLDEN_NOTE_WORD,
]


def _make_fake_pdf(path: Path, texts: list[str] | None = None) -> Path:
    doc = fitz.open()
    for text in (texts or _SYNTH_PDF_TEXTS):
        page = doc.new_page()
        page.insert_text((72, 72), text)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def _make_fake_tree(path: Path, *, max_page: int = 2) -> tuple[Path, str]:
    payload = {
        "doc_name": "synth_doc.pdf",
        "doc_description": _SYNTH_TREE_DESC,
        "structure": [
            {
                "title": "Zephyr Quokka",
                "node_id": "0001",
                "start_index": 1,
                "end_index": max_page,
                "summary": _SYNTH_TREE_SUMMARY,
                "nodes": [],
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path, sha256_file(path)


def _setup_fixture_repo(tmp_path: Path) -> dict[str, Any]:
    pdf_path = _make_fake_pdf(tmp_path / "doc.pdf")
    pdf_sha = sha256_file(pdf_path)
    tree_path, tree_sha = _make_fake_tree(tmp_path / "tree.json")

    test_items: list[dict[str, Any]] = []
    for idx, qid in enumerate(EXPECTED_TEST_IDS):
        if idx < 12:
            test_items.append(
                {
                    "id": qid,
                    "type": "single_fact",
                    "answerable": True,
                    "question": f"What does {_SYNTH_GOLDEN_Q_WORD} item {idx + 1} specify?",
                    "reference": f"{_SYNTH_GOLDEN_REF_WORD} reference clause {idx + 1}.",
                    "reference_claims": [f"{_SYNTH_GOLDEN_REF_WORD} claim {idx + 1}."],
                    "gold_page_sets": [[1]],
                    "unanswerable_note": "",
                }
            )
        else:
            test_items.append(
                {
                    "id": qid,
                    "type": "unanswerable_near_miss",
                    "answerable": False,
                    "question": f"Does {_SYNTH_GOLDEN_Q_WORD} mention missing item {idx + 1}?",
                    "reference": EXPECTED_REFUSAL,
                    "reference_claims": [],
                    "gold_page_sets": [],
                    "unanswerable_note": f"{_SYNTH_GOLDEN_NOTE_WORD} absent note {idx + 1}.",
                }
            )

    dev_items = [
        {
            "id": qid,
            "type": "single_fact",
            "answerable": True,
            "question": f"What does {_SYNTH_GOLDEN_Q_WORD} dev item {idx + 1} specify?",
            "reference": f"{_SYNTH_GOLDEN_REF_WORD} dev reference {idx + 1}.",
            "reference_claims": [f"{_SYNTH_GOLDEN_REF_WORD} dev claim {idx + 1}."],
            "gold_page_sets": [[1]],
            "unanswerable_note": "",
        }
        for idx, qid in enumerate(EXPECTED_DEV_IDS)
    ]

    golden_rel = "eval/s11/golden_v1.json"
    golden_path = tmp_path / golden_rel
    golden_path.parent.mkdir(parents=True, exist_ok=True)
    golden_payload = {
        "schema": "s11-golden-v2",
        "pdf_sha256": pdf_sha,
        "page_convention": EXPECTED_PAGE_CONVENTION,
        "test": test_items,
        "dev": dev_items,
    }
    golden_path.write_text(json.dumps(golden_payload, indent=2) + "\n", encoding="utf-8")

    aux_specs = [
        ("specs/S11_PREREGISTRATION.md", "eval/s11/manifests/prereg_v1.json", "prereg\n"),
        ("specs/S11_PREREG_ADDENDUM_A1.md", "eval/s11/manifests/prereg_addendum_a1.json", "a1\n"),
        ("specs/S11_PREREG_ADDENDUM_A2.md", "eval/s11/manifests/prereg_addendum_a2.json", "a2\n"),
        ("eval/s11/prompts/answer_rules_v1.txt", "eval/s11/manifests/answer_rules_v1.json", "rules\n"),
    ]
    for src_rel, man_rel, content in aux_specs:
        src_abs = tmp_path / src_rel
        src_abs.parent.mkdir(parents=True, exist_ok=True)
        src_abs.write_text(content, encoding="utf-8")
        freeze_manifest([src_rel], man_rel, root=tmp_path, now="2026-11-27T00:00:00Z")

    golden_manifest_rel = "eval/s11/manifests/golden_v1.json"
    freeze_manifest([golden_rel], golden_manifest_rel, root=tmp_path, now="2026-11-27T00:00:00Z")

    runs_root = tmp_path / "runs"
    cache_dir = tmp_path / "embed_cache"
    ledger_path = tmp_path / "ledger.jsonl"
    usage_path = tmp_path / "usage.jsonl"
    lock_path = tmp_path / "paid.lock"
    stop_file = tmp_path / "s11_STOP"

    return {
        "root": tmp_path,
        "pdf_path": pdf_path,
        "pdf_sha": pdf_sha,
        "tree_path": tree_path,
        "tree_sha": tree_sha,
        "golden_path": golden_path,
        "golden_manifest_path": tmp_path / golden_manifest_rel,
        "runs_root": runs_root,
        "cache_dir": cache_dir,
        "ledger_path": ledger_path,
        "usage_path": usage_path,
        "lock_path": lock_path,
        "stop_file": stop_file,
    }


def _ok_record(
    system: str,
    qid: str,
    *,
    pages_given: list[int] | None = None,
    answer: str = "Statement [p. 1].",
    refusal: bool = False,
    status: str = "ok",
    stop_reason: str | None = "end_turn",
    effort: str = "medium",
    model_returned: str = "claude-sonnet-5-5-20260929",
    verifier_model_returned: str | None = "claude-haiku-5-5-20260929",
) -> dict[str, Any]:
    return {
        "system": system,
        "question_id": qid,
        "pages_given": list(pages_given if pages_given is not None else [1]),
        "answer_pre_repair": answer,
        "answer": answer,
        "repaired": False,
        "dropped_sentences": [],
        "refusal": refusal,
        "check_status": "check_skipped_refusal" if refusal else ("checked" if status == "ok" else "check_skipped_failed"),
        "stop_reason": stop_reason,
        "status": status,
        "model_returned": model_returned,
        "verifier_model_returned": verifier_model_returned,
        "effort": effort,
        "usage": {
            "answer": {"input": 100, "output": 20, "cache_read": 0, "cache_write": 0, "thinking_tokens": None},
            "verifier": {"input": 40, "output": 10, "cache_read": 0, "cache_write": 0, "thinking_tokens": None},
            "total": {"input": 140, "output": 30, "cache_read": 0, "cache_write": 0, "thinking_tokens": None},
        },
        "latency_s": 0.01,
    }


def _hy_result(qid: str, *, pi_pages: int, effort: str = "medium") -> dict[str, Any]:
    k_val, k_flagged = s11_hybrid.k_for(pi_pages)
    pages = [1] if pi_pages >= 0 else []
    ret = {
        "question_id": qid,
        "pi_distinct_pages": pi_pages,
        "k": k_val,
        "k_flagged": k_flagged,
        "selected_pages": pages,
        "ranking": [{"page": 1, "rrf_score": 0.016, "bm25_rank": 1, "bm25_score": 1.0, "dense_rank": 1, "dense_score": 0.9}],
        "empty_pages": [],
        "non_empty_pages": [1, 2],
        "bm25_hits": [1],
        "embed_stats": {"cache_hits": 3, "cache_misses": 0, "api_batches": 0, "api_inputs": 0},
        "k_rrf": 60,
        "bm25_k1": 1.2,
        "bm25_b": 0.75,
        "embedding_model": "text-embedding-3-small",
        "embedding_deployment": "text-embedding-3-small",
    }
    return {"answer": _ok_record("HY", qid, pages_given=pages, effort=effort), "retrieval": ret}


def _hy5_record(qid: str) -> dict[str, Any]:
    return {
        "system": "HY5",
        "question_id": qid,
        "k": 5,
        "selected_pages": [1, 2],
        "ranking": [],
        "empty_pages": [],
        "non_empty_pages": [1, 2],
        "bm25_hits": [1],
        "embed_stats": {"cache_hits": 3, "cache_misses": 0, "api_batches": 0, "api_inputs": 0},
        "k_rrf": 60,
        "bm25_k1": 1.2,
        "bm25_b": 0.75,
        "embedding_model": "text-embedding-3-small",
        "embedding_deployment": "text-embedding-3-small",
    }


def _write_dev_effort_via_run(fix: dict[str, Any], *, effort_override: str | None = None) -> dict[str, Any]:
    def _pi(qid, q, *, s, **kw):
        status = "failed:empty_answer" if (effort_override == "high" and qid == "dev-01") else "ok"
        ans = "" if status != "ok" else "Statement [p. 1]."
        return _ok_record("PI", qid, pages_given=[1], answer=ans, status=status, effort=s.answer_effort)

    def _hy(qid, q, pages, *, pi_distinct_pages, s, **kw):
        return _hy_result(qid, pi_pages=pi_distinct_pages, effort=s.answer_effort)

    def _wd(qid, q, *, s, **kw):
        return _ok_record("WD", qid, pages_given=[1, 2], effort=s.answer_effort)

    return s11_driver.run_dev(
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=_pi,
        hy_runner=_hy,
        wd_runner=_wd,
    )


def _ant_msg(
    *,
    text: str = "",
    stop_reason: str = "end_turn",
    model: str = "claude-sonnet-5-5-test",
    tool_uses: list[tuple[str, str, dict[str, Any]]] | None = None,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> Any:
    blocks: list[Any] = []
    if text:
        blocks.append(SimpleNamespace(type="text", text=text))
    for tid, tname, tinp in (tool_uses or []):
        blocks.append(SimpleNamespace(type="tool_use", id=tid, name=tname, input=copy.deepcopy(tinp)))
    return SimpleNamespace(
        model=model,
        stop_reason=stop_reason,
        content=blocks,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


class _FakeAnthropicMessages:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(copy.deepcopy(kwargs))
        if not self._responses:
            raise AssertionError("No fake Anthropic responses remaining")
        nxt = self._responses.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


class _FakeAnthropicSDK:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = _FakeAnthropicMessages(responses)


def _http_status_err(cls: type[Any], code: int, msg: str = "err") -> Exception:
    req = httpx.Request("POST", "https://api.example.invalid/v1/messages")
    resp = httpx.Response(status_code=code, request=req)
    return cls(msg, response=resp, body=None)


# --------------------------------------------------------------------------- R1 tests

def test_order_dev_sorted():
    got = s11_driver.compute_run_order(("dev-03", "dev-01", "dev-02"), is_dev=True)
    assert got == ["dev-01", "dev-02", "dev-03"]


def test_order_main_deterministic_and_distinct():
    orders: dict[int, list[str]] = {}
    shas: dict[int, str] = {}
    for run_num in (1, 2, 3):
        expected = sorted(EXPECTED_TEST_IDS)
        random.Random(f"S11-v1|order|{run_num}").shuffle(expected)
        got = s11_driver.compute_run_order(EXPECTED_TEST_IDS, run_num=run_num, is_dev=False)
        assert got == expected
        assert len(got) == 16
        orders[run_num] = got
        shas[run_num] = s11_driver.order_sha256(got)
        assert shas[run_num] == s11_driver.order_sha256(list(got))

    assert orders[1] != orders[2]
    assert orders[1] != orders[3]
    assert orders[2] != orders[3]


def test_main_r1_system_sequence_and_hy5_once(tmp_path: Path):
    fix = _setup_fixture_repo(tmp_path)
    _write_dev_effort_via_run(fix)

    trace: list[tuple[str, str]] = []

    def _pi(qid, q, **kw):
        trace.append(("PI", qid))
        return _ok_record("PI", qid, pages_given=[1, 2])

    def _hy(qid, q, pages, *, pi_distinct_pages, **kw):
        trace.append(("HY", qid))
        return _hy_result(qid, pi_pages=pi_distinct_pages)

    def _hy5(qid, q, pages, **kw):
        trace.append(("HY5", qid))
        return _hy5_record(qid)

    def _wd(qid, q, **kw):
        trace.append(("WD", qid))
        return _ok_record("WD", qid, pages_given=[1, 2])

    manifest = s11_driver.run_main(
        run_num=1,
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=_pi,
        hy_runner=_hy,
        hy5_runner=_hy5,
        wd_runner=_wd,
    )
    order_r1 = s11_driver.compute_run_order(EXPECTED_TEST_IDS, run_num=1, is_dev=False)
    expected_trace = (
        [("PI", q) for q in order_r1]
        + [("HY", q) for q in order_r1]
        + [("HY5", q) for q in order_r1]
        + [("WD", q) for q in order_r1]
    )
    assert trace == expected_trace
    assert len(trace) == 64
    part1 = fix["runs_root"] / "main_r1" / "part_1"
    assert len(list((part1 / "hy5").glob("*.json"))) == 16
    assert len(list((part1 / "hy_retrieval").glob("*.json"))) == 16
    assert len(list((part1 / "wd_meta").glob("*.json"))) == 16
    assert manifest["order"]["systems"] == ["PI", "HY", "HY5", "WD"]
    assert manifest["counts"]["expected_total_items"] == 64


def test_main_r2_r3_omit_hy5(tmp_path: Path):
    fix = _setup_fixture_repo(tmp_path)
    _write_dev_effort_via_run(fix)

    for run_num in (2, 3):
        trace: list[tuple[str, str]] = []

        def _hy5(*args, **kwargs):
            raise AssertionError("HY5 must not run in main runs 2 or 3")

        manifest = s11_driver.run_main(
            run_num=run_num,
            pdf_path=fix["pdf_path"],
            tree_path=fix["tree_path"],
            tree_sha=fix["tree_sha"],
            runs_root=fix["runs_root"],
            cache_dir=fix["cache_dir"],
            golden_path=fix["golden_path"],
            golden_manifest_path=fix["golden_manifest_path"],
            ledger_path=fix["ledger_path"],
            usage_log_path=fix["usage_path"],
            lock_path=fix["lock_path"],
            stop_file=fix["stop_file"],
            repo_root=fix["root"],
            expected_pdf_sha256=fix["pdf_sha"],
            allow_stub_for_test=True,
            pi_runner=lambda qid, q, **kw: trace.append(("PI", qid)) or _ok_record("PI", qid, pages_given=[1]),
            hy_runner=lambda qid, q, pages, pi_distinct_pages, **kw: trace.append(("HY", qid)) or _hy_result(qid, pi_pages=pi_distinct_pages),
            hy5_runner=_hy5,
            wd_runner=lambda qid, q, **kw: trace.append(("WD", qid)) or _ok_record("WD", qid, pages_given=[1, 2]),
        )
        assert len(trace) == 48
        assert manifest["order"]["systems"] == ["PI", "HY", "WD"]
        assert not (fix["runs_root"] / f"main_r{run_num}" / "part_1" / "hy5").exists()


def test_dev_runs_9_items_at_medium_and_writes_effort_json(tmp_path: Path):
    fix = _setup_fixture_repo(tmp_path)
    trace: list[tuple[str, str, str]] = []

    manifest = s11_driver.run_dev(
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=lambda qid, q, s, **kw: trace.append(("PI", qid, s.answer_effort)) or _ok_record("PI", qid, pages_given=[1], effort=s.answer_effort),
        hy_runner=lambda qid, q, pages, pi_distinct_pages, s, **kw: trace.append(("HY", qid, s.answer_effort)) or _hy_result(qid, pi_pages=pi_distinct_pages, effort=s.answer_effort),
        hy5_runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("HY5 must not run in dev")),
        wd_runner=lambda qid, q, s, **kw: trace.append(("WD", qid, s.answer_effort)) or _ok_record("WD", qid, pages_given=[1, 2], effort=s.answer_effort),
    )
    assert trace == [
        ("PI", "dev-01", "medium"),
        ("PI", "dev-02", "medium"),
        ("PI", "dev-03", "medium"),
        ("HY", "dev-01", "medium"),
        ("HY", "dev-02", "medium"),
        ("HY", "dev-03", "medium"),
        ("WD", "dev-01", "medium"),
        ("WD", "dev-02", "medium"),
        ("WD", "dev-03", "medium"),
    ]
    assert manifest["status"] == "completed"
    effort_file = fix["runs_root"] / "dev_r1" / "effort.json"
    assert effort_file.is_file()
    eff_doc = json.loads(effort_file.read_text(encoding="utf-8"))
    assert eff_doc["chosen_effort"] == "medium"
    assert eff_doc["dev_items_checked"] == 9


def test_pi_uses_run_local_manifest_and_unchanged_pi_agent(tmp_path: Path, monkeypatch):
    fix = _setup_fixture_repo(tmp_path)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")

    def _fake_pi_anthropic_factory(*args, timeout=None, **kwargs):
        if timeout == 180.0:
            return _FakeAnthropicSDK(
                [
                    _ant_msg(
                        tool_uses=[
                            ("v1", "report_verdicts", {"verdicts": [{"claim_id": 1, "verdict": "supported", "evidence": "Zephyr", "reason": "ok"}]})
                        ],
                        stop_reason="tool_use",
                        model="claude-haiku-5-5-test",
                    )
                ]
            )
        return _FakeAnthropicSDK(
            [
                _ant_msg(tool_uses=[("t1", "fetch_pages", {"pages": "1"})], stop_reason="tool_use"),
                _ant_msg(text="Zephyr statement [p. 1].", stop_reason="end_turn"),
            ]
        )

    monkeypatch.setattr(pi_agent.anthropic, "Anthropic", _fake_pi_anthropic_factory)

    async def _emb_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads((await request.aread()).decode("utf-8"))
        inputs = body["input"]
        data = [{"object": "embedding", "index": i, "embedding": [1.0, 0.0, 0.0]} for i in range(len(inputs))]
        return httpx.Response(
            200,
            json={"object": "list", "model": "text-embedding-3-small", "data": data, "usage": {"prompt_tokens": 10}},
            request=request,
        )

    transport = httpx.MockTransport(_emb_handler)

    def _real_hy(qid, q, pages, *, pi_distinct_pages, cache_dir, s, ledger, usage_log, guard, **kw):
        sdk = _FakeAnthropicSDK(
            [
                _ant_msg(text="HY statement [p. 1].", stop_reason="end_turn"),
                _ant_msg(
                    tool_uses=[
                        ("v1", "report_verdicts", {"verdicts": [{"claim_id": 1, "verdict": "supported", "evidence": "Zephyr", "reason": "ok"}]})
                    ],
                    stop_reason="tool_use",
                    model="claude-haiku-5-5-test",
                ),
            ]
        )
        return s11_hybrid.run_hy(
            qid,
            q,
            pages,
            pi_distinct_pages=pi_distinct_pages,
            cache_dir=cache_dir,
            s=s,
            ledger=ledger,
            usage_log=usage_log,
            guard=guard,
            inner_transport=transport,
            sdk_client=sdk,
        )

    def _real_wd(qid, q, *, all_pages, s, ledger, usage_log, **kw):
        sdk = _FakeAnthropicSDK(
            [
                _ant_msg(text="WD statement [p. 1].", stop_reason="end_turn"),
                _ant_msg(
                    tool_uses=[
                        ("v1", "report_verdicts", {"verdicts": [{"claim_id": 1, "verdict": "supported", "evidence": "Zephyr", "reason": "ok"}]})
                    ],
                    stop_reason="tool_use",
                    model="claude-haiku-5-5-test",
                ),
            ]
        )
        return s11_wd.run_wd(
            qid,
            q,
            all_pages=all_pages,
            s=s,
            ledger=ledger,
            usage_log=usage_log,
            sdk_client=sdk,
        )

    manifest = s11_driver.run_dev(
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        hy_runner=_real_hy,
        wd_runner=_real_wd,
    )
    assert manifest["status"] == "completed"
    pi_store_manifest = fix["runs_root"] / "dev_r1" / "part_1" / "_pi_store" / "manifest.json"
    assert pi_store_manifest.is_file()
    man_data = json.loads(pi_store_manifest.read_text(encoding="utf-8"))
    rec = man_data["documents"]["gtp@2019"]
    assert Path(rec["tree_path"]).resolve() == fix["tree_path"].resolve()
    assert Path(rec["pdf_path"]).resolve() == fix["pdf_path"].resolve()
    pi_item = json.loads((fix["runs_root"] / "dev_r1" / "part_1" / "pi" / "dev-01.json").read_text(encoding="utf-8"))
    assert pi_item["pages_given"] == [1]
    assert pi_item["status"] == "ok"


def test_tree_sha_and_pdf_sha_mismatch_refused_before_item_1(tmp_path: Path):
    fix = _setup_fixture_repo(tmp_path)
    called: list[str] = []

    with pytest.raises(s11_driver.DriverValidationError, match="Tree SHA-256 mismatch"):
        s11_driver.run_dev(
            pdf_path=fix["pdf_path"],
            tree_path=fix["tree_path"],
            tree_sha="0" * 64,
            runs_root=fix["runs_root"],
            golden_path=fix["golden_path"],
            golden_manifest_path=fix["golden_manifest_path"],
            repo_root=fix["root"],
            expected_pdf_sha256=fix["pdf_sha"],
            allow_stub_for_test=True,
            pi_runner=lambda *a, **k: called.append("pi") or {},
        )
    assert called == []
    assert not (fix["runs_root"] / "dev_r1" / "part_1").exists()

    other_pdf = _make_fake_pdf(tmp_path / "other.pdf", ["Completely different text"])
    with pytest.raises(s11_driver.DriverValidationError, match="PDF SHA-256 mismatch"):
        s11_driver.run_dev(
            pdf_path=other_pdf,
            tree_path=fix["tree_path"],
            tree_sha=fix["tree_sha"],
            runs_root=fix["runs_root"],
            golden_path=fix["golden_path"],
            golden_manifest_path=fix["golden_manifest_path"],
            repo_root=fix["root"],
            expected_pdf_sha256=fix["pdf_sha"],
            allow_stub_for_test=True,
            pi_runner=lambda *a, **k: called.append("pi") or {},
        )
    assert called == []


def test_classify_outcome_all_failure_table_rows():
    assert s11_driver.classify_record(_ok_record("PI", "gtp-01", status="ok", refusal=False)) == "content_ok"
    assert s11_driver.classify_record(_ok_record("PI", "gtp-13", status="ok", refusal=True, answer=EXPECTED_REFUSAL)) == "content_refusal"
    assert s11_driver.classify_record(_hy5_record("gtp-01")) == "content_ok"

    for bad_status in (
        "failed:max_turns",
        "failed:empty_answer",
        "failed:max_tokens",
        "failed:model_context_window_exceeded",
        "model_refusal",
        "failed:verifier_max_tokens",
        "failed:verifier_model_context_window_exceeded",
    ):
        assert s11_driver.classify_record(_ok_record("PI", "gtp-01", status=bad_status)) == "content_failure"

    req = httpx.Request("GET", "https://api.example.invalid")
    infra_excs: list[BaseException] = [
        anthropic.APIConnectionError(request=req),
        openai.APIConnectionError(request=req),
        httpx.ConnectError("conn", request=req),
        ConnectionError("drop"),
        anthropic.APITimeoutError(request=req),
        openai.APITimeoutError(request=req),
        httpx.ReadTimeout("timeout", request=req),
        TimeoutError("timed out"),
        SimpleNamespace(status_code=408),
        _http_status_err(anthropic.RateLimitError, 429),
        _http_status_err(openai.RateLimitError, 429),
        _http_status_err(anthropic.InternalServerError, 500),
        _http_status_err(anthropic.InternalServerError, 502),
        _http_status_err(openai.InternalServerError, 504),
        _http_status_err(anthropic.APIStatusError, 529),
    ]
    for exc in infra_excs:
        info = s11_driver.classify_exception(exc)  # type: ignore[arg-type]
        assert info["kind"] == "infra_error", f"Expected infra_error for {exc!r}, got {info}"

    stop_excs: list[BaseException] = [
        _http_status_err(anthropic.BadRequestError, 400),
        _http_status_err(anthropic.AuthenticationError, 401),
        _http_status_err(openai.PermissionDeniedError, 403),
        _http_status_err(anthropic.NotFoundError, 404),
        _http_status_err(anthropic.APIStatusError, 413),
        _http_status_err(openai.UnprocessableEntityError, 422),
        ModelVersionChangedError(deployment="d", expected_model="a", observed_model="b", lane="embedding"),
        CapExceededError("cap"),
        PaidLockHeld("locked"),
        WriteOnceError("exists"),
        RuntimeError("unexpected"),
    ]
    for exc in stop_excs:
        info = s11_driver.classify_exception(exc)  # type: ignore[arg-type]
        assert info["kind"] == "immediate_stop", f"Expected immediate_stop for {exc!r}, got {info}"


def test_infra_retry_once_then_success_resets_counter(tmp_path: Path):
    fix = _setup_fixture_repo(tmp_path)
    _write_dev_effort_via_run(fix)
    order_r1 = s11_driver.compute_run_order(EXPECTED_TEST_IDS, run_num=1, is_dev=False)
    first_q = order_r1[0]
    seen_first = 0

    def _pi(qid, q, **kw):
        nonlocal seen_first
        if qid == first_q:
            seen_first += 1
            if seen_first == 1:
                raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.example.invalid"))
        return _ok_record("PI", qid, pages_given=[1])

    manifest = s11_driver.run_main(
        run_num=1,
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=_pi,
        hy_runner=lambda qid, q, p, pi_distinct_pages, **kw: _hy_result(qid, pi_pages=pi_distinct_pages),
        hy5_runner=lambda qid, q, p, **kw: _hy5_record(qid),
        wd_runner=lambda qid, q, **kw: _ok_record("WD", qid, pages_given=[1, 2]),
    )
    assert seen_first == 2
    assert manifest["status"] == "completed"
    assert manifest["consec_infra_errors_at_end"] == 0
    assert len(manifest["counts"]["infra_retried_items"]) == 1


def test_infra_twice_records_failed_and_continues(tmp_path: Path):
    fix = _setup_fixture_repo(tmp_path)
    _write_dev_effort_via_run(fix)
    order_r1 = s11_driver.compute_run_order(EXPECTED_TEST_IDS, run_num=1, is_dev=False)
    q0 = order_r1[0]
    calls_q0 = 0

    def _pi(qid, q, **kw):
        nonlocal calls_q0
        if qid == q0:
            calls_q0 += 1
            raise _http_status_err(anthropic.APIStatusError, 529, "overloaded")
        return _ok_record("PI", qid, pages_given=[1])

    manifest = s11_driver.run_main(
        run_num=1,
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=_pi,
        hy_runner=lambda qid, q, p, pi_distinct_pages, **kw: _hy_result(qid, pi_pages=pi_distinct_pages),
        hy5_runner=lambda qid, q, p, **kw: _hy5_record(qid),
        wd_runner=lambda qid, q, **kw: _ok_record("WD", qid, pages_given=[1, 2]),
    )
    assert calls_q0 == 2
    assert manifest["status"] == "completed"
    assert manifest["counts"]["infra_twice_in_part"] == 1
    assert manifest["consec_infra_errors_at_end"] == 0
    saved_q0 = json.loads((fix["runs_root"] / "main_r1" / "part_1" / "pi" / f"{q0}.json").read_text(encoding="utf-8"))
    assert saved_q0["status"] == "failed:infra_twice"
    assert saved_q0["pages_given"] == []


def test_three_consecutive_infra_then_success_does_not_stop(tmp_path: Path):
    fix = _setup_fixture_repo(tmp_path)
    _write_dev_effort_via_run(fix)
    order_r1 = s11_driver.compute_run_order(EXPECTED_TEST_IDS, run_num=1, is_dev=False)
    q0, q1 = order_r1[0], order_r1[1]
    q1_calls = 0

    def _pi(qid, q, **kw):
        nonlocal q1_calls
        if qid == q0:
            raise _http_status_err(anthropic.APIStatusError, 408, "req timeout")
        if qid == q1:
            q1_calls += 1
            if q1_calls == 1:
                raise _http_status_err(anthropic.RateLimitError, 429, "rate limit")
        return _ok_record("PI", qid, pages_given=[1])

    manifest = s11_driver.run_main(
        run_num=1,
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=_pi,
        hy_runner=lambda qid, q, p, pi_distinct_pages, **kw: _hy_result(qid, pi_pages=pi_distinct_pages),
        hy5_runner=lambda qid, q, p, **kw: _hy5_record(qid),
        wd_runner=lambda qid, q, **kw: _ok_record("WD", qid, pages_given=[1, 2]),
    )
    assert q1_calls == 2
    assert manifest["status"] == "completed"
    assert not (fix["runs_root"] / "main_r1" / "part_1" / "STOPPED").exists()
    assert manifest["consec_infra_errors_at_end"] == 0


def test_four_consecutive_infra_stops_run_writes_stopped(tmp_path: Path):
    fix = _setup_fixture_repo(tmp_path)
    _write_dev_effort_via_run(fix)
    order_r1 = s11_driver.compute_run_order(EXPECTED_TEST_IDS, run_num=1, is_dev=False)
    q0, q1, q2 = order_r1[0], order_r1[1], order_r1[2]
    calls: dict[str, int] = {}

    def _pi(qid, q, **kw):
        calls[qid] = calls.get(qid, 0) + 1
        if qid in (q0, q1):
            raise _http_status_err(anthropic.InternalServerError, 503, "unavailable")
        return _ok_record("PI", qid, pages_given=[1])

    rc = s11_driver.main(
        [
            "main",
            "--run",
            "1",
            "--pdf",
            str(fix["pdf_path"]),
            "--tree",
            str(fix["tree_path"]),
            "--tree-sha",
            fix["tree_sha"],
            "--runs-root",
            str(fix["runs_root"]),
            "--cache-dir",
            str(fix["cache_dir"]),
            "--golden",
            str(fix["golden_path"]),
            "--golden-manifest",
            str(fix["golden_manifest_path"]),
            "--ledger",
            str(fix["ledger_path"]),
            "--usage-log",
            str(fix["usage_path"]),
            "--lock",
            str(fix["lock_path"]),
            "--stop-file",
            str(fix["stop_file"]),
            "--real",
        ],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=_pi,
        hy_runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("HY should not run after PI STOPPED")),
    )
    assert rc != 0
    assert calls == {q0: 2, q1: 2}
    assert q2 not in calls

    part1 = fix["runs_root"] / "main_r1" / "part_1"
    assert (part1 / "STOPPED").is_file()
    man = json.loads((part1 / "run_manifest.json").read_text(encoding="utf-8"))
    assert man["status"] == "stopped"
    assert man["stop_reason"] == "consecutive_infra_errors"
    assert man["consec_infra_errors_at_end"] == 4
    assert man["counts"]["infra_twice_in_part"] == 2

    rec_q0 = json.loads((part1 / "pi" / f"{q0}.json").read_text(encoding="utf-8"))
    rec_q1 = json.loads((part1 / "pi" / f"{q1}.json").read_text(encoding="utf-8"))
    assert rec_q0["status"] == "failed:infra_twice"
    assert rec_q1["status"] == "failed:infra_twice"


def test_content_failure_never_retried(tmp_path: Path):
    fix = _setup_fixture_repo(tmp_path)
    _write_dev_effort_via_run(fix)
    order_r1 = s11_driver.compute_run_order(EXPECTED_TEST_IDS, run_num=1, is_dev=False)
    statuses = [
        "failed:max_turns",
        "failed:empty_answer",
        "failed:max_tokens",
        "failed:model_context_window_exceeded",
        "model_refusal",
        "failed:verifier_max_tokens",
        "failed:verifier_model_context_window_exceeded",
    ]
    forced = {order_r1[i]: statuses[i] for i in range(len(statuses))}
    calls: dict[str, int] = {}

    def _pi(qid, q, **kw):
        calls[qid] = calls.get(qid, 0) + 1
        if qid in forced:
            return _ok_record("PI", qid, pages_given=[1], answer="", status=forced[qid], stop_reason="max_tokens")
        return _ok_record("PI", qid, pages_given=[1])

    manifest = s11_driver.run_main(
        run_num=1,
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=_pi,
        hy_runner=lambda qid, q, p, pi_distinct_pages, **kw: _hy_result(qid, pi_pages=pi_distinct_pages),
        hy5_runner=lambda qid, q, p, **kw: _hy5_record(qid),
        wd_runner=lambda qid, q, **kw: _ok_record("WD", qid, pages_given=[1, 2]),
    )
    assert all(calls[qid] == 1 for qid in order_r1)
    assert manifest["status"] == "completed"
    assert manifest["counts"]["content_failed_in_part"] == len(statuses)
    assert manifest["consec_infra_errors_at_end"] == 0


@pytest.mark.parametrize(
    ("exc_factory", "expected_reason_suffix"),
    [
        (lambda: _http_status_err(anthropic.BadRequestError, 400), "http_400"),
        (lambda: _http_status_err(anthropic.AuthenticationError, 401), "http_401"),
        (lambda: _http_status_err(openai.PermissionDeniedError, 403), "http_403"),
        (lambda: _http_status_err(anthropic.NotFoundError, 404), "http_404"),
        (lambda: _http_status_err(anthropic.APIStatusError, 413), "http_413"),
        (lambda: ModelVersionChangedError(deployment="emb", expected_model="v1", observed_model="v2", lane="embedding"), "ModelVersionChangedError"),
        (lambda: CapExceededError("cap"), "CapExceededError"),
    ],
)
def test_k1_immediate_stop_no_retry(tmp_path: Path, exc_factory, expected_reason_suffix: str):
    fix = _setup_fixture_repo(tmp_path)
    _write_dev_effort_via_run(fix)
    pi_calls = 0

    def _pi(qid, q, **kw):
        nonlocal pi_calls
        pi_calls += 1
        raise exc_factory()

    rc = s11_driver.main(
        [
            "main",
            "--run",
            "1",
            "--pdf",
            str(fix["pdf_path"]),
            "--tree",
            str(fix["tree_path"]),
            "--tree-sha",
            fix["tree_sha"],
            "--runs-root",
            str(fix["runs_root"]),
            "--cache-dir",
            str(fix["cache_dir"]),
            "--golden",
            str(fix["golden_path"]),
            "--golden-manifest",
            str(fix["golden_manifest_path"]),
            "--ledger",
            str(fix["ledger_path"]),
            "--usage-log",
            str(fix["usage_path"]),
            "--lock",
            str(fix["lock_path"]),
            "--stop-file",
            str(fix["stop_file"]),
            "--real",
        ],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=_pi,
    )
    assert rc != 0
    assert pi_calls == 1
    part1 = fix["runs_root"] / "main_r1" / "part_1"
    assert (part1 / "STOPPED").is_file()
    man = json.loads((part1 / "run_manifest.json").read_text(encoding="utf-8"))
    assert man["status"] == "stopped"
    assert man["stop_reason"] == f"immediate_stop:{expected_reason_suffix}"


def test_k6_pi_client_context_window_exceeded(tmp_path: Path):
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    sdk = _FakeAnthropicSDK(
        [
            _ant_msg(
                text="partial text",
                stop_reason="model_context_window_exceeded",
                model="claude-sonnet-5-5-test",
                input_tokens=100,
                output_tokens=50,
            )
        ]
    )
    client = AnthropicRetryClient(sdk, ledger=ledger, usage_log=usage_log, lane="answer")
    client.messages.create(
        model="claude-sonnet-5-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": "hi"}],
    )
    rows = [json.loads(line) for line in usage_log.path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["status"] == "model_context_window_exceeded"
    assert rows[0]["error_type"] == "model_context_window_exceeded"
    assert rows[0]["status_code"] == 200
    assert ledger.spent_usd > Decimal("0")


def test_k6_pi_agent_nav_and_verifier_context_window_exceeded(tmp_path: Path, monkeypatch):
    pdf_path = _make_fake_pdf(tmp_path / "doc.pdf")
    tree_path, _ = _make_fake_tree(tmp_path / "tree.json")
    s = s11_driver._write_pi_run_local_manifest(
        tmp_path / "store",
        pdf_abs=pdf_path,
        pdf_sha=sha256_file(pdf_path),
        tree_abs=tree_path,
        tree_doc_description=_SYNTH_TREE_DESC,
        effort="medium",
        total_pages=2,
    )
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")

    nav_sdk = _FakeAnthropicSDK([_ant_msg(text="", stop_reason="model_context_window_exceeded")])
    monkeypatch.setattr(pi_agent.anthropic, "Anthropic", lambda *a, **k: nav_sdk)
    res_nav = pi_agent.answer_question("q", family="gtp", version="2019", s=s, ledger=ledger, usage_log=usage_log)
    assert res_nav["status"] == "failed:model_context_window_exceeded"
    assert res_nav["stop_reason"] == "model_context_window_exceeded"
    assert res_nav["check_status"] == "check_skipped_failed"
    assert res_nav["answer"] == ""

    clients = [
        _FakeAnthropicSDK(
            [
                _ant_msg(tool_uses=[("t1", "fetch_pages", {"pages": "1"})], stop_reason="tool_use"),
                _ant_msg(text="Claim text [p. 1].", stop_reason="end_turn"),
            ]
        ),
        _FakeAnthropicSDK(
            [
                _ant_msg(text="", stop_reason="model_context_window_exceeded", model="claude-haiku-5-5-test"),
            ]
        ),
    ]
    monkeypatch.setattr(pi_agent.anthropic, "Anthropic", lambda *a, **k: clients.pop(0))
    res_ver = pi_agent.answer_question("q", family="gtp", version="2019", s=s, ledger=ledger, usage_log=usage_log)
    assert res_ver["status"] == "failed:verifier_model_context_window_exceeded"
    assert res_ver["check_status"] == "verifier_model_context_window_exceeded"
    assert res_ver["stop_reason"] == "model_context_window_exceeded"
    assert res_ver["answer"] == ""


def test_k6_answer_py_hy_wd_context_window_exceeded(tmp_path: Path):
    s = Settings(store_dir=tmp_path / "store", answer_effort="medium")
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")

    gen_sdk = _FakeAnthropicSDK([_ant_msg(text="", stop_reason="model_context_window_exceeded")])
    hy_res = s11_answer.run_hy_answer(
        "gtp-01",
        "q?",
        {1: "page one text"},
        s=s,
        ledger=ledger,
        usage_log=usage_log,
        sdk_client=gen_sdk,
    )
    assert hy_res["status"] == "failed:model_context_window_exceeded"
    assert hy_res["stop_reason"] == "model_context_window_exceeded"
    assert hy_res["check_status"] == "check_skipped_failed"

    wd_gen = _FakeAnthropicSDK([_ant_msg(text="Claim text [p. 1].", stop_reason="end_turn")])
    wd_ver = _FakeAnthropicSDK([_ant_msg(text="", stop_reason="model_context_window_exceeded", model="claude-haiku-5-5-test")])
    wd_res = s11_answer.run_wd_answer(
        "gtp-02",
        "q?",
        {1: "page one text"},
        s=s,
        ledger=ledger,
        usage_log=usage_log,
        sdk_client=wd_gen,
        verifier_sdk_client=wd_ver,
    )
    assert wd_res["status"] == "failed:verifier_model_context_window_exceeded"
    assert wd_res["check_status"] == "verifier_model_context_window_exceeded"
    assert wd_res["stop_reason"] == "model_context_window_exceeded"


def test_stop_switch_checked_before_every_item(tmp_path: Path):
    fix = _setup_fixture_repo(tmp_path)
    _write_dev_effort_via_run(fix)

    pi_calls = 0

    def _pi(qid, q, **kw):
        nonlocal pi_calls
        pi_calls += 1
        if pi_calls == 2:
            fix["stop_file"].write_text("stop\n", encoding="utf-8")
        return _ok_record("PI", qid, pages_given=[1])

    rc = s11_driver.main(
        [
            "main",
            "--run",
            "1",
            "--pdf",
            str(fix["pdf_path"]),
            "--tree",
            str(fix["tree_path"]),
            "--tree-sha",
            fix["tree_sha"],
            "--runs-root",
            str(fix["runs_root"]),
            "--cache-dir",
            str(fix["cache_dir"]),
            "--golden",
            str(fix["golden_path"]),
            "--golden-manifest",
            str(fix["golden_manifest_path"]),
            "--ledger",
            str(fix["ledger_path"]),
            "--usage-log",
            str(fix["usage_path"]),
            "--lock",
            str(fix["lock_path"]),
            "--stop-file",
            str(fix["stop_file"]),
            "--real",
        ],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=_pi,
    )
    assert rc != 0
    assert pi_calls == 2
    part1 = fix["runs_root"] / "main_r1" / "part_1"
    assert (part1 / "STOPPED").is_file()
    man1 = json.loads((part1 / "run_manifest.json").read_text(encoding="utf-8"))
    assert man1["stop_reason"] == "stop_file"

    # Also test stop file already present before item 1 -> 0 items run
    calls_r2 = 0
    rc2 = s11_driver.main(
        [
            "main",
            "--run",
            "2",
            "--pdf",
            str(fix["pdf_path"]),
            "--tree",
            str(fix["tree_path"]),
            "--tree-sha",
            fix["tree_sha"],
            "--runs-root",
            str(fix["runs_root"]),
            "--cache-dir",
            str(fix["cache_dir"]),
            "--golden",
            str(fix["golden_path"]),
            "--golden-manifest",
            str(fix["golden_manifest_path"]),
            "--ledger",
            str(fix["ledger_path"]),
            "--usage-log",
            str(fix["usage_path"]),
            "--lock",
            str(fix["lock_path"]),
            "--stop-file",
            str(fix["stop_file"]),
            "--real",
        ],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("No items should run when stop file exists before item 1")),
    )
    assert rc2 != 0
    assert calls_r2 == 0
    assert (fix["runs_root"] / "main_r2" / "part_1" / "STOPPED").is_file()


def test_resume_creates_part_2_skips_recorded_and_couples_hy(tmp_path: Path):
    fix = _setup_fixture_repo(tmp_path)
    _write_dev_effort_via_run(fix)
    order_r1 = s11_driver.compute_run_order(EXPECTED_TEST_IDS, run_num=1, is_dev=False)
    q0, q1 = order_r1[0], order_r1[1]

    # Part 1 hits 4 consecutive infra failures on q0 (2) + q1 (2) -> writes q1 failed:infra_twice then STOPPED.
    calls_p1: list[tuple[str, str]] = []

    def _pi_p1(qid, q, **kw):
        calls_p1.append(("PI", qid))
        if qid in (q0, q1):
            raise _http_status_err(anthropic.InternalServerError, 503, "down")
        return _ok_record("PI", qid, pages_given=[1])

    m1 = s11_driver.run_main(
        run_num=1,
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=_pi_p1,
    )
    assert m1["status"] == "stopped"
    assert (fix["runs_root"] / "main_r1" / "part_1" / "pi" / f"{q1}.json").is_file()

    # Part 2 resumes -> MUST NOT re-run q0 or q1, runs q2..q15 in order_r1, couples HY (q0 & q1 -> pi_pages=0 -> k_fallback_5)
    part1_q1_snapshot = (fix["runs_root"] / "main_r1" / "part_1" / "pi" / f"{q1}.json").read_bytes()
    calls_p2: list[tuple[str, str]] = []
    hy_budget_seen: dict[str, int] = {}

    def _pi_p2(qid, q, **kw):
        calls_p2.append(("PI", qid))
        if qid in (q0, q1):
            raise AssertionError(f"Resume must never re-run already recorded {qid}")
        if qid == order_r1[2]:
            return _ok_record("PI", qid, pages_given=[], answer="", status="failed:max_turns", stop_reason="max_turns")
        return _ok_record("PI", qid, pages_given=[1, 2])

    def _hy_p2(qid, q, p, *, pi_distinct_pages, guard, **kw):
        calls_p2.append(("HY", qid))
        hy_budget_seen[qid] = pi_distinct_pages
        guard.check(deployment="text-embedding-3-small", returned_model="text-embedding-3-small-v1", lane="embedding")
        if qid == order_r1[0]:
            # Record one HY item then stop to test mid-HY resume in part_3
            fix["stop_file"].write_text("pause\n", encoding="utf-8")
        return _hy_result(qid, pi_pages=pi_distinct_pages)

    m2 = s11_driver.run_main(
        run_num=1,
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=_pi_p2,
        hy_runner=_hy_p2,
    )
    assert m2["part_name"] == "part_2"
    assert m2["status"] == "stopped"
    assert m2["counts"]["skipped_prior_items"] == 2
    assert calls_p2 == [("PI", q) for q in order_r1[2:]] + [("HY", q0)]
    assert hy_budget_seen[q0] == 0
    assert (fix["runs_root"] / "main_r1" / "part_1" / "pi" / f"{q1}.json").read_bytes() == part1_q1_snapshot

    # Clear stop file -> part_3 resumes mid-HY, couples HY to PI records written in BOTH part_1 and part_2, finishes HY5+WD
    fix["stop_file"].unlink()
    calls_p3: list[tuple[str, str]] = []

    def _hy_p3(qid, q, p, *, pi_distinct_pages, guard, **kw):
        calls_p3.append(("HY", qid))
        hy_budget_seen[qid] = pi_distinct_pages
        # Guard must already be seeded with text-embedding-3-small-v1 from part_2
        assert guard.observed_models.get("text-embedding-3-small") == "text-embedding-3-small-v1"
        return _hy_result(qid, pi_pages=pi_distinct_pages)

    m3 = s11_driver.run_main(
        run_num=1,
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("PI already complete")),
        hy_runner=_hy_p3,
        hy5_runner=lambda qid, q, p, **kw: calls_p3.append(("HY5", qid)) or _hy5_record(qid),
        wd_runner=lambda qid, q, **kw: calls_p3.append(("WD", qid)) or _ok_record("WD", qid, pages_given=[1, 2]),
    )
    assert m3["part_name"] == "part_3"
    assert m3["status"] == "completed"
    assert calls_p3 == (
        [("HY", q) for q in order_r1[1:]]
        + [("HY5", q) for q in order_r1]
        + [("WD", q) for q in order_r1]
    )
    assert hy_budget_seen[q1] == 0
    assert hy_budget_seen[order_r1[2]] == 0
    assert hy_budget_seen[order_r1[3]] == 2

    # Check seeded ModelVersionGuard rejects embedding model drift in resumed part_2 of run 2
    order_r2 = s11_driver.compute_run_order(EXPECTED_TEST_IDS, run_num=2, is_dev=False)

    def _hy_r2_p1(qid, q, p, *, guard, **kw):
        guard.check(deployment="text-embedding-3-small", returned_model="text-embedding-3-small-v1", lane="embedding")
        if qid == order_r2[0]:
            fix["stop_file"].write_text("stop\n", encoding="utf-8")
        return _hy_result(qid, pi_pages=1)

    m_r2_p1 = s11_driver.run_main(
        run_num=2,
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=lambda qid, q, **kw: _ok_record("PI", qid, pages_given=[1]),
        hy_runner=_hy_r2_p1,
    )
    assert m_r2_p1["status"] == "stopped"
    fix["stop_file"].unlink()

    def _hy_r2_p2(qid, q, p, *, guard, **kw):
        guard.check(deployment="text-embedding-3-small", returned_model="text-embedding-3-small-v2", lane="embedding")
        return _hy_result(qid, pi_pages=1)

    m_r2_p2 = s11_driver.run_main(
        run_num=2,
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        hy_runner=_hy_r2_p2,
    )
    assert m_r2_p2["status"] == "stopped"
    assert m_r2_p2["stop_reason"] == "immediate_stop:ModelVersionChangedError"


def test_write_once_refuses_completed_run_and_existing_effort_and_cli_real_guard(tmp_path: Path, monkeypatch):
    fix = _setup_fixture_repo(tmp_path)
    _write_dev_effort_via_run(fix)

    # Re-running already-complete dev refuses (effort file write-once guard fires before remaining check)
    with pytest.raises(WriteOnceError, match="already completed|existing dev effort file"):
        s11_driver.run_dev(
            pdf_path=fix["pdf_path"],
            tree_path=fix["tree_path"],
            tree_sha=fix["tree_sha"],
            runs_root=fix["runs_root"],
            golden_path=fix["golden_path"],
            golden_manifest_path=fix["golden_manifest_path"],
            lock_path=fix["lock_path"],
            repo_root=fix["root"],
            expected_pdf_sha256=fix["pdf_sha"],
            allow_stub_for_test=True,
        )

    # Re-running an already-completed main run (no dev-effort overwrite guard) refuses with "already completed"
    main_r2 = s11_driver.run_main(
        run_num=2,
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=lambda qid, q, **kw: _ok_record("PI", qid, pages_given=[1]),
        hy_runner=lambda qid, q, p, pi_distinct_pages, **kw: _hy_result(qid, pi_pages=pi_distinct_pages),
        wd_runner=lambda qid, q, **kw: _ok_record("WD", qid, pages_given=[1, 2]),
    )
    assert main_r2["status"] == "completed"
    with pytest.raises(WriteOnceError, match="already completed"):
        s11_driver.run_main(
            run_num=2,
            pdf_path=fix["pdf_path"],
            tree_path=fix["tree_path"],
            tree_sha=fix["tree_sha"],
            runs_root=fix["runs_root"],
            cache_dir=fix["cache_dir"],
            golden_path=fix["golden_path"],
            golden_manifest_path=fix["golden_manifest_path"],
            ledger_path=fix["ledger_path"],
            usage_log_path=fix["usage_path"],
            lock_path=fix["lock_path"],
            stop_file=fix["stop_file"],
            repo_root=fix["root"],
            expected_pdf_sha256=fix["pdf_sha"],
            allow_stub_for_test=True,
        )
    assert not (fix["runs_root"] / "main_r2" / "part_2").exists()

    # Pre-existing --effort-file refuses before creating part_1 of a fresh runs_root
    existing_eff = tmp_path / "custom_effort.json"
    existing_eff.write_text("{}\n", encoding="utf-8")
    fresh_runs = tmp_path / "fresh_runs"
    with pytest.raises(WriteOnceError, match="existing dev effort file"):
        s11_driver.run_dev(
            pdf_path=fix["pdf_path"],
            tree_path=fix["tree_path"],
            tree_sha=fix["tree_sha"],
            runs_root=fresh_runs,
            effort_file=existing_eff,
            golden_path=fix["golden_path"],
            golden_manifest_path=fix["golden_manifest_path"],
            lock_path=fix["lock_path"],
            repo_root=fix["root"],
            expected_pdf_sha256=fix["pdf_sha"],
            allow_stub_for_test=True,
        )
    assert not fresh_runs.exists()

    # CLI without --real refuses (exit 2)
    rc_no_real = s11_driver.main(
        [
            "dev",
            "--pdf",
            str(fix["pdf_path"]),
            "--tree",
            str(fix["tree_path"]),
            "--tree-sha",
            fix["tree_sha"],
            "--runs-root",
            str(fresh_runs),
        ],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
    )
    assert rc_no_real == 2

    # CLI with --real while REGCLAW_STUB=1 (set by conftest) refuses (exit 2)
    monkeypatch.setenv("REGCLAW_STUB", "1")
    rc_stub = s11_driver.main(
        [
            "dev",
            "--pdf",
            str(fix["pdf_path"]),
            "--tree",
            str(fix["tree_path"]),
            "--tree-sha",
            fix["tree_sha"],
            "--runs-root",
            str(fresh_runs),
            "--real",
        ],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
    )
    assert rc_stub == 2


def test_hy_budget_coupling_flags_and_missing_pi_refusal(tmp_path: Path):
    assert s11_driver.hy_k_flag(0) == "k_fallback_5"
    assert s11_driver.hy_k_flag(7) is None
    assert s11_driver.hy_k_flag(40) is None
    assert s11_driver.hy_k_flag(45) == "k_capped_40"

    fix = _setup_fixture_repo(tmp_path)
    _write_dev_effort_via_run(fix)
    order_r1 = s11_driver.compute_run_order(EXPECTED_TEST_IDS, run_num=1, is_dev=False)

    def _pi(qid, q, **kw):
        if qid == order_r1[0]:
            return _ok_record("PI", qid, pages_given=[])
        if qid == order_r1[1]:
            return _ok_record("PI", qid, pages_given=list(range(1, 46)))
        return _ok_record("PI", qid, pages_given=[1, 2, 3])

    man = s11_driver.run_main(
        run_num=1,
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=_pi,
        hy_runner=lambda qid, q, p, pi_distinct_pages, **kw: _hy_result(qid, pi_pages=pi_distinct_pages),
        hy5_runner=lambda qid, q, p, **kw: _hy5_record(qid),
        wd_runner=lambda qid, q, **kw: _ok_record("WD", qid, pages_given=[1, 2]),
    )
    assert man["counts"]["hy_budget_flags"] == [
        {"question_id": order_r1[0], "pi_distinct_pages": 0, "k": 5, "flag": "k_fallback_5"},
        {"question_id": order_r1[1], "pi_distinct_pages": 45, "k": 40, "flag": "k_capped_40"},
    ]

    with pytest.raises(s11_driver.DriverValidationError, match="Missing PI record"):
        s11_driver.resolve_hy_pi_pages({}, "gtp-01")
    assert s11_driver.resolve_hy_pi_pages({("PI", "gtp-01"): {"pages_given": [3, 1, 3]}}, "gtp-01") == 2


def test_hy_refuses_inside_running_event_loop(tmp_path: Path):
    cache = tmp_path / "cache"
    s11_hybrid.save_embedding_cache(
        cache,
        "q",
        model="text-embedding-3-small",
        deployment="text-embedding-3-small",
        embedding=[1.0, 0.0],
    )
    s11_hybrid.save_embedding_cache(
        cache,
        "page text",
        model="text-embedding-3-small",
        deployment="text-embedding-3-small",
        embedding=[1.0, 0.0],
    )
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    s = Settings(store_dir=tmp_path / "store")

    async def _probe() -> None:
        with pytest.raises(RuntimeError, match="event loop"):
            s11_hybrid.load_or_compute_embeddings(
                {"q": "q"},
                cache_dir=cache,
                model="text-embedding-3-small",
                deployment="text-embedding-3-small",
            )
        with pytest.raises(RuntimeError, match="event loop"):
            s11_hybrid.run_hy(
                "gtp-01",
                "q",
                {1: "page text"},
                pi_distinct_pages=1,
                cache_dir=cache,
                s=s,
                ledger=ledger,
                usage_log=usage_log,
            )
        with pytest.raises(RuntimeError, match="event loop"):
            s11_hybrid.run_hy5_retrieval(
                "gtp-01",
                "q",
                {1: "page text"},
                cache_dir=cache,
                s=s,
                usage_log=usage_log,
            )

    asyncio.run(_probe())


def test_effort_rule_k3_medium_vs_high_and_main_validation(tmp_path: Path):
    # Pure compute_effort_decision unit checks
    all_ok = {(sys_name, qid): _ok_record(sys_name, qid, status="ok") for sys_name in ("PI", "HY", "WD") for qid in EXPECTED_DEV_IDS}
    all_ok[("PI", "dev-02")] = _ok_record("PI", "dev-02", status="ok", refusal=True, answer=EXPECTED_REFUSAL)
    dec_med = s11_driver.compute_effort_decision(all_ok, EXPECTED_DEV_IDS)
    assert dec_med["chosen_effort"] == "medium"
    assert dec_med["failed_items"] == []

    one_fail = dict(all_ok)
    one_fail[("WD", "dev-03")] = _ok_record("WD", "dev-03", status="failed:empty_answer", answer="")
    dec_high = s11_driver.compute_effort_decision(one_fail, EXPECTED_DEV_IDS)
    assert dec_high["chosen_effort"] == "high"
    assert len(dec_high["failed_items"]) == 1

    fix = _setup_fixture_repo(tmp_path)
    # Missing effort.json blocks main before any call
    called: list[str] = []
    with pytest.raises(s11_driver.DriverValidationError, match="Missing dev effort decision file"):
        s11_driver.run_main(
            run_num=1,
            pdf_path=fix["pdf_path"],
            tree_path=fix["tree_path"],
            tree_sha=fix["tree_sha"],
            runs_root=fix["runs_root"],
            golden_path=fix["golden_path"],
            golden_manifest_path=fix["golden_manifest_path"],
            repo_root=fix["root"],
            expected_pdf_sha256=fix["pdf_sha"],
            allow_stub_for_test=True,
            pi_runner=lambda *a, **k: called.append("pi") or {},
        )
    assert called == []

    # Trigger high effort from failing dev-01 and confirm main receives answer_effort == "high"
    _write_dev_effort_via_run(fix, effort_override="high")
    eff_doc = json.loads((fix["runs_root"] / "dev_r1" / "effort.json").read_text(encoding="utf-8"))
    assert eff_doc["chosen_effort"] == "high"

    seen_effort: set[str] = set()
    man = s11_driver.run_main(
        run_num=1,
        pdf_path=fix["pdf_path"],
        tree_path=fix["tree_path"],
        tree_sha=fix["tree_sha"],
        runs_root=fix["runs_root"],
        cache_dir=fix["cache_dir"],
        golden_path=fix["golden_path"],
        golden_manifest_path=fix["golden_manifest_path"],
        ledger_path=fix["ledger_path"],
        usage_log_path=fix["usage_path"],
        lock_path=fix["lock_path"],
        stop_file=fix["stop_file"],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=lambda qid, q, s, **kw: seen_effort.add(s.answer_effort) or _ok_record("PI", qid, effort=s.answer_effort),
        hy_runner=lambda qid, q, p, pi_distinct_pages, s, **kw: seen_effort.add(s.answer_effort) or _hy_result(qid, pi_pages=pi_distinct_pages, effort=s.answer_effort),
        hy5_runner=lambda qid, q, p, **kw: _hy5_record(qid),
        wd_runner=lambda qid, q, s, **kw: seen_effort.add(s.answer_effort) or _ok_record("WD", qid, effort=s.answer_effort),
    )
    assert seen_effort == {"high"}
    assert man["settings"]["effort"] == "high"

    # Mismatched tree_sha in effort.json must refuse before item 1
    bad_eff_path = fix["runs_root"] / "dev_r1" / "bad_effort.json"
    bad_eff = dict(eff_doc)
    bad_eff["tree_sha256"] = "f" * 64
    bad_eff_path.write_text(json.dumps(bad_eff), encoding="utf-8")
    with pytest.raises(s11_driver.DriverValidationError, match="tree_sha256 does not match"):
        s11_driver.run_main(
            run_num=2,
            pdf_path=fix["pdf_path"],
            tree_path=fix["tree_path"],
            tree_sha=fix["tree_sha"],
            runs_root=fix["runs_root"],
            effort_file=bad_eff_path,
            golden_path=fix["golden_path"],
            golden_manifest_path=fix["golden_manifest_path"],
            lock_path=fix["lock_path"],
            repo_root=fix["root"],
            expected_pdf_sha256=fix["pdf_sha"],
            allow_stub_for_test=True,
        )


def test_paid_lock_held_and_released_on_stop_and_manifest_zero_leak(tmp_path: Path, capsys):
    fix = _setup_fixture_repo(tmp_path)

    with paid_lock(lock_path=fix["lock_path"]):
        with pytest.raises(PaidLockHeld):
            s11_driver.run_dev(
                pdf_path=fix["pdf_path"],
                tree_path=fix["tree_path"],
                tree_sha=fix["tree_sha"],
                runs_root=fix["runs_root"],
                golden_path=fix["golden_path"],
                golden_manifest_path=fix["golden_manifest_path"],
                lock_path=fix["lock_path"],
                repo_root=fix["root"],
                expected_pdf_sha256=fix["pdf_sha"],
                allow_stub_for_test=True,
            )
    assert not (fix["runs_root"] / "dev_r1" / "part_1").exists()

    # Run dev via CLI and verify zero leak in stdout + all manifests + Decimal ledger totals
    rc = s11_driver.main(
        [
            "dev",
            "--pdf",
            str(fix["pdf_path"]),
            "--tree",
            str(fix["tree_path"]),
            "--tree-sha",
            fix["tree_sha"],
            "--runs-root",
            str(fix["runs_root"]),
            "--cache-dir",
            str(fix["cache_dir"]),
            "--golden",
            str(fix["golden_path"]),
            "--golden-manifest",
            str(fix["golden_manifest_path"]),
            "--ledger",
            str(fix["ledger_path"]),
            "--usage-log",
            str(fix["usage_path"]),
            "--lock",
            str(fix["lock_path"]),
            "--stop-file",
            str(fix["stop_file"]),
            "--real",
        ],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=lambda qid, q, **kw: _ok_record("PI", qid, pages_given=[1]),
        hy_runner=lambda qid, q, p, pi_distinct_pages, **kw: _hy_result(qid, pi_pages=pi_distinct_pages),
        wd_runner=lambda qid, q, **kw: _ok_record("WD", qid, pages_given=[1, 2]),
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert_no_source_words(captured.out + "\n" + captured.err, _SYNTH_SOURCES)

    part1 = fix["runs_root"] / "dev_r1" / "part_1"
    man_text = (part1 / "run_manifest.json").read_text(encoding="utf-8")
    events_text = (part1 / "events.jsonl").read_text(encoding="utf-8")
    effort_text = (fix["runs_root"] / "dev_r1" / "effort.json").read_text(encoding="utf-8")
    assert_no_source_words(man_text + "\n" + events_text + "\n" + effort_text, _SYNTH_SOURCES)

    man = json.loads(man_text)
    assert man["tree_sha256"] == fix["tree_sha"]
    assert man["pdf_sha256"] == fix["pdf_sha"]
    assert set(man["frozen_sha256"].keys()) == set(s11_driver.FREEZE_MANIFESTS)
    assert man["settings"]["anthropic_version"] == "2023-06-01"
    assert man["settings"]["temperature"] is None
    assert man["settings"]["top_p"] is None
    assert man["settings"]["top_k"] is None
    assert isinstance(man["ledger_totals"]["cap_usd"], str)
    assert Decimal(man["ledger_totals"]["cap_usd"]) == Decimal("60.0")

    # Verify lock released even after a K1 immediate stop
    rc_stop = s11_driver.main(
        [
            "main",
            "--run",
            "1",
            "--pdf",
            str(fix["pdf_path"]),
            "--tree",
            str(fix["tree_path"]),
            "--tree-sha",
            fix["tree_sha"],
            "--runs-root",
            str(fix["runs_root"]),
            "--cache-dir",
            str(fix["cache_dir"]),
            "--golden",
            str(fix["golden_path"]),
            "--golden-manifest",
            str(fix["golden_manifest_path"]),
            "--ledger",
            str(fix["ledger_path"]),
            "--usage-log",
            str(fix["usage_path"]),
            "--lock",
            str(fix["lock_path"]),
            "--stop-file",
            str(fix["stop_file"]),
            "--real",
        ],
        repo_root=fix["root"],
        expected_pdf_sha256=fix["pdf_sha"],
        allow_stub_for_test=True,
        pi_runner=lambda *a, **k: (_ for _ in ()).throw(_http_status_err(anthropic.AuthenticationError, 401)),
    )
    assert rc_stop != 0
    with paid_lock(lock_path=fix["lock_path"]):
        pass
