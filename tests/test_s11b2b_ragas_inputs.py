"""Offline tests for eval.s11.ragas_inputs (input assembly, empty context, validity, proxy, flags)."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ragas = pytest.importorskip("ragas")

from ragas.embeddings.base import BaseRagasEmbedding  # noqa: E402
from ragas.llms.base import InstructorBaseRagasLLM  # noqa: E402
from ragas.metrics.collections.answer_correctness.util import (  # noqa: E402
    ClassificationWithReason,
    StatementsWithReason,
)
from ragas.metrics.collections.answer_relevancy.util import AnswerRelevanceOutput  # noqa: E402
from ragas.metrics.collections.faithfulness.util import (  # noqa: E402
    NLIStatementOutput,
    StatementFaithfulnessAnswer,
    StatementGeneratorOutput,
)

import _netguard  # noqa: E402
from eval.s11 import ragas_inputs  # noqa: E402
from eval.s11.ledger import WriteOnceError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("REGCLAW_STUB", "1")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    _netguard.block_network(monkeypatch, "s11b2b ragas_inputs tests forbid network")


class _ScriptedFakeLLM(InstructorBaseRagasLLM):
    """Fake InstructorBaseRagasLLM returning deterministic structured outputs per prompt/model."""

    def __init__(self, handler):
        self._handler = handler
        self.calls: list[tuple[str, str]] = []

    def generate(self, prompt: str, response_model):
        self.calls.append((response_model.__name__, prompt))
        out = self._handler(prompt, response_model)
        if isinstance(out, BaseException):
            raise out
        return out

    async def agenerate(self, prompt: str, response_model):
        return self.generate(prompt, response_model)


class _DeterministicFakeEmbedding(BaseRagasEmbedding):
    """Non-zero deterministic embedding vectors without network calls."""

    def embed_text(self, text: str, **kwargs):
        base = float(len(text or "") % 7 + 1)
        return [1.0, base / 10.0, 0.5]

    async def aembed_text(self, text: str, **kwargs):
        return self.embed_text(text, **kwargs)


def _default_handler(prompt: str, response_model):
    name = response_model.__name__
    if name == "StatementGeneratorOutput":
        return StatementGeneratorOutput(statements=["Samples stay at 2-8 C."])
    if name == "NLIStatementOutput":
        return NLIStatementOutput(
            statements=[
                StatementFaithfulnessAnswer(
                    statement="Samples stay at 2-8 C.",
                    reason="Page states 2-8 C.",
                    verdict=1,
                )
            ]
        )
    if name == "AnswerRelevanceOutput":
        return AnswerRelevanceOutput(question="What storage temperature is required?", noncommittal=0)
    if name == "ClassificationWithReason":
        return ClassificationWithReason(
            TP=[StatementsWithReason(statement="Samples stay at 2-8 C.", reason="Matches ref.")],
            FP=[],
            FN=[],
        )
    raise AssertionError(f"Unexpected response_model {name}")


def _golden_item(qid: str = "gtp-01", *, answerable: bool = True, question: str = "Q?", reference: str = "Ref.") -> dict:
    return {
        "id": qid,
        "type": "single_fact" if answerable else "unanswerable_near_miss",
        "answerable": answerable,
        "question": question,
        "reference": reference if answerable else "The guideline does not state this.",
        "reference_claims": ["Ref."] if answerable else [],
        "gold_page_sets": [[10]] if answerable else [],
        "unanswerable_note": "" if answerable else "Not stated.",
    }


def _result(
    *,
    system: str = "PI",
    qid: str = "gtp-01",
    pages_given: list[int] | None = None,
    answer: str = "Store at 2-8 C [p. 10].",
    status: str = "ok",
    refusal: bool = False,
) -> dict:
    return {
        "system": system,
        "question_id": qid,
        "pages_given": [10] if pages_given is None else pages_given,
        "answer_pre_repair": answer,
        "answer": answer,
        "repaired": False,
        "dropped_sentences": [],
        "refusal": refusal,
        "check_status": "checked",
        "stop_reason": "end_turn",
        "status": status,
        "model_returned": "claude-sonnet-5-5",
        "verifier_model_returned": "claude-haiku-5-5",
        "effort": "medium",
        "usage": {},
        "latency_s": 0.4,
    }


def test_included_and_excluded_sets_with_reasons(tmp_path):
    golden_by_id = {
        "gtp-01": _golden_item("gtp-01", answerable=True, question="Q1?", reference="Ref1."),
        "gtp-02": _golden_item("gtp-02", answerable=True, question="Q2?", reference="Ref2."),
        "gtp-03": _golden_item("gtp-03", answerable=True, question="Q3?", reference="Ref3."),
        "gtp-13": _golden_item("gtp-13", answerable=False, question="Q13?"),
    }
    records = [
        _result(qid="gtp-01", pages_given=[10], answer="Answer 1 [p. 10].", status="ok", refusal=False),
        _result(qid="gtp-02", pages_given=[10], answer="", status="failed:max_turns", refusal=False),
        _result(qid="gtp-03", pages_given=[10], answer="The guideline does not state this.", status="ok", refusal=True),
        _result(qid="gtp-13", pages_given=[10], answer="The guideline does not state this.", status="ok", refusal=True),
    ]
    out = ragas_inputs.build_ragas_inputs(
        records,
        golden_by_id,
        {10: "Page ten text."},
        run_id="run-1",
        out_jsonl=tmp_path / "inputs.jsonl",
    )
    assert [row["question_id"] for row in out["included"]] == ["gtp-01"]
    assert out["excluded"] == [
        {"question_id": "gtp-02", "system": "PI", "reason": "failed_item"},
        {"question_id": "gtp-03", "system": "PI", "reason": "refusal"},
        {"question_id": "gtp-13", "system": "PI", "reason": "unanswerable"},
    ]
    assert out["excluded_by_reason"] == {"unanswerable": 1, "failed_item": 1, "refusal": 1}

    # All excluded -> included == 0 -> valid False ("no_included_items")
    res_empty = ragas_inputs.evaluate_ragas_run(
        [records[1], records[2], records[3]],
        golden_by_id,
        {10: "Page ten text."},
        run_id="run-empty",
        system="PI",
        inputs_jsonl=tmp_path / "inputs_empty.jsonl",
        recorder_path=tmp_path / "rec_empty.jsonl",
        repeats=1,
        llm_override=_ScriptedFakeLLM(_default_handler),
        emb_override=_DeterministicFakeEmbedding(),
    )
    assert res_empty["included_count"] == 0
    for m_name in ragas_inputs.RAGAS_METRICS:
        m = res_empty["metrics"][m_name]
        assert m["valid"] is False
        assert m["valid_reason"] == "no_included_items"
        assert m["mean"] is None
        assert m["counts"]["included"] == 0
        assert m["counts"]["excluded"] == 3


def test_strip_citations_valid_malformed_punctuation_and_bullets():
    raw = (
        "- Store samples at 2-8 C [p. 10] , then label [pp. 11–12] .\n"
        "- Keep records [p. x] ; audit [pp. 9-7] : ok [P. 5].\n"
        "[p. 3]\n"
        "Summary holds [pp. 1-2] [p. 4]."
    )
    expected = (
        "- Store samples at 2-8 C, then label.\n"
        "- Keep records; audit: ok.\n"
        "Summary holds."
    )
    assert ragas_inputs.strip_citations(raw) == expected


def test_contexts_first_seen_r_order_drops_empty_keeps_reference_and_write_once(tmp_path):
    golden_by_id = {
        "gtp-01": _golden_item("gtp-01", question="Golden Q?", reference="Golden Reference Answer."),
    }
    rec = _result(qid="gtp-01", pages_given=[12, 5, 12, 8, 3], answer="Answer [p. 12].")
    page_texts = {
        12: "  Page twelve plain text.  \n",
        5: "   \n\t ",
        8: "Page eight text.",
        3: "",
    }
    jsonl_path = tmp_path / "ragas_in.jsonl"
    out = ragas_inputs.build_ragas_inputs([rec], golden_by_id, page_texts, run_id="r1", out_jsonl=jsonl_path)

    row = out["included"][0]
    assert tuple(row.keys()) == ragas_inputs.RAGAS_INPUT_KEYS
    assert row["rules_version"] == "s11-metrics-v1"
    assert row["user_input"] == "Golden Q?"
    assert row["response"] == "Answer."
    assert row["retrieved_contexts"] == ["Page twelve plain text.", "Page eight text."]
    assert row["kept_pages"] == [12, 8]
    assert row["dropped_empty_pages"] == [5, 3]
    assert row["reference"] == "Golden Reference Answer."
    assert len(row["input_sha256"]) == 64

    # Deterministic input_sha256 & exact JSONL line
    row2, _ = ragas_inputs.build_ragas_input_row(rec, golden_by_id["gtp-01"], page_texts, run_id="r1")
    assert row2["input_sha256"] == row["input_sha256"]
    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0]) == row

    # Write-once refuses overwrite
    with pytest.raises(WriteOnceError):
        ragas_inputs.write_ragas_inputs_jsonl([row], jsonl_path)


def test_writer_requires_path_and_creates_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake_llm = _ScriptedFakeLLM(_default_handler)
    row = {
        "rules_version": "s11-metrics-v1",
        "run_id": "r1",
        "system": "PI",
        "question_id": "gtp-01",
        "user_input": "Q?",
        "response": "A.",
        "retrieved_contexts": ["Ctx."],
        "kept_pages": [1],
        "dropped_empty_pages": [],
        "reference": "Ref.",
        "input_sha256": "a" * 64,
    }

    with pytest.raises(TypeError):
        ragas_inputs.write_ragas_inputs_jsonl([row])  # type: ignore[call-arg]
    for bad in (None, "", "   "):
        with pytest.raises(ValueError, match="out_jsonl"):
            ragas_inputs.write_ragas_inputs_jsonl([row], bad)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        ragas_inputs.RecordingRagasLLM(fake_llm)  # type: ignore[call-arg]
    for bad in (None, "", "   "):
        with pytest.raises(ValueError, match="record_path"):
            ragas_inputs.RecordingRagasLLM(fake_llm, bad)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        ragas_inputs.evaluate_ragas_run([], {}, {}, run_id="r1", system="PI")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="recorder_path"):
        ragas_inputs.evaluate_ragas_run(
            [],
            {},
            {},
            run_id="r1",
            system="PI",
            inputs_jsonl=tmp_path / "in.jsonl",
            use_proxy=True,
            recorder_path=None,
            llm_override=fake_llm,
            emb_override=_DeterministicFakeEmbedding(),
        )

    assert list(tmp_path.iterdir()) == []


def test_empty_context_rule_gives_zero_no_judge_call_and_scores_others(tmp_path):
    golden_by_id = {"gtp-01": _golden_item("gtp-01", question="Q?", reference="Store samples at 2-8 C.")}
    rec = _result(qid="gtp-01", pages_given=[10], answer="Store samples at 2-8 C [p. 10].")
    fake_llm = _ScriptedFakeLLM(_default_handler)

    res = ragas_inputs.evaluate_ragas_run(
        [rec],
        golden_by_id,
        {10: "   \n"},
        run_id="r1",
        system="PI",
        inputs_jsonl=tmp_path / "inputs.jsonl",
        recorder_path=tmp_path / "rec.jsonl",
        repeats=1,
        llm_override=fake_llm,
        emb_override=_DeterministicFakeEmbedding(),
    )
    item = res["items"][0]
    assert item["metrics"]["faithfulness"] == {"value": 0.0, "is_nan": False, "error_type": None}
    assert item["flags"]["faith_zero_no_context"] is True
    assert item["metrics"]["answer_relevancy"]["value"] is not None
    assert item["metrics"]["answer_correctness"]["value"] is not None

    rec_lines = [
        json.loads(ln)
        for ln in (tmp_path / "rec.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert all(row["metric"] != "faithfulness" for row in rec_lines)
    assert res["metrics"]["faithfulness"]["valid"] is True
    assert res["metrics"]["faithfulness"]["mean"] == 0.0
    assert res["metrics"]["faithfulness"]["counts"]["zero_no_context"] == 1


def test_nan_from_empty_statements_and_exception_invalidate_run(tmp_path):
    golden_by_id = {"gtp-01": _golden_item("gtp-01", question="Q?", reference="Ref.")}
    rec = _result(qid="gtp-01", pages_given=[10], answer="Something [p. 10].")

    def nan_and_err_handler(prompt: str, response_model):
        if response_model.__name__ == "StatementGeneratorOutput" and "Context" not in prompt:
            # Returns empty statements -> Faithfulness returns NaN; AnswerCorrectness gives factuality=1.0
            return StatementGeneratorOutput(statements=[])
        if response_model.__name__ == "AnswerRelevanceOutput":
            return RuntimeError("simulated judge failure")
        return _default_handler(prompt, response_model)

    res = ragas_inputs.evaluate_ragas_run(
        [rec],
        golden_by_id,
        {10: "Page ten."},
        run_id="r1",
        system="PI",
        inputs_jsonl=tmp_path / "inputs.jsonl",
        recorder_path=tmp_path / "rec.jsonl",
        repeats=1,
        llm_override=_ScriptedFakeLLM(nan_and_err_handler),
        emb_override=_DeterministicFakeEmbedding(),
    )
    item = res["items"][0]
    assert item["metrics"]["faithfulness"] == {"value": None, "is_nan": True, "error_type": None}
    assert item["metrics"]["answer_relevancy"] == {"value": None, "is_nan": False, "error_type": "RuntimeError"}
    assert item["metrics"]["answer_correctness"]["value"] is not None

    assert res["metrics"]["faithfulness"]["valid"] is False
    assert res["metrics"]["faithfulness"]["mean"] is None
    assert res["metrics"]["faithfulness"]["counts"]["nan"] == 1
    assert res["metrics"]["faithfulness"]["counts"]["errors"] == 0

    assert res["metrics"]["answer_relevancy"]["valid"] is False
    assert res["metrics"]["answer_relevancy"]["mean"] is None
    assert res["metrics"]["answer_relevancy"]["counts"]["errors"] == 1

    assert res["metrics"]["answer_correctness"]["valid"] is True
    assert item["flags"]["faith_no_statements"] is True
    assert item["flags"]["corr_no_statements"] is True


def test_proxy_isinstance_jsonl_fields_write_once_and_identical_scores(tmp_path):
    golden_by_id = {"gtp-01": _golden_item("gtp-01", question="What storage temperature?", reference="Store at 2-8 C.")}
    rec = _result(qid="gtp-01", pages_given=[10], answer="Store at 2-8 C [p. 10].")
    page_texts = {10: "Store at 2-8 C."}

    # Proxy passes isinstance inside all three real collections metrics
    rec_path = tmp_path / "recorder.jsonl"
    metrics_with_proxy, proxy = ragas_inputs.build_s11_ragas_metrics(
        llm_override=_ScriptedFakeLLM(_default_handler),
        emb_override=_DeterministicFakeEmbedding(),
        recorder_path=rec_path,
        run_id="r1",
        system="PI",
    )
    assert isinstance(proxy, InstructorBaseRagasLLM)
    for m_name in ragas_inputs.RAGAS_METRICS:
        assert isinstance(metrics_with_proxy[m_name][0].llm, ragas_inputs.RecordingRagasLLM)
        assert isinstance(metrics_with_proxy[m_name][0].llm, InstructorBaseRagasLLM)

    # Sync generate forwarding also logs one JSONL line
    sync_out = proxy.bound(question_id="sync-1", repeat=2, metric="faithfulness").generate(
        "sync prompt", StatementGeneratorOutput
    )
    assert sync_out.statements == ["Samples stay at 2-8 C."]

    # Overwrite refused at init
    with pytest.raises(WriteOnceError):
        ragas_inputs.RecordingRagasLLM(_ScriptedFakeLLM(_default_handler), rec_path)

    # Identical scores with vs without proxy
    res_no_proxy = ragas_inputs.evaluate_ragas_run(
        [rec],
        golden_by_id,
        page_texts,
        run_id="r1",
        system="PI",
        inputs_jsonl=tmp_path / "in_a.jsonl",
        use_proxy=False,
        repeats=2,
        llm_override=_ScriptedFakeLLM(_default_handler),
        emb_override=_DeterministicFakeEmbedding(),
    )
    res_proxy = ragas_inputs.evaluate_ragas_run(
        [rec],
        golden_by_id,
        page_texts,
        run_id="r1",
        system="PI",
        inputs_jsonl=tmp_path / "in_b.jsonl",
        recorder_path=tmp_path / "rec_b.jsonl",
        use_proxy=True,
        repeats=2,
        llm_override=_ScriptedFakeLLM(_default_handler),
        emb_override=_DeterministicFakeEmbedding(),
    )
    for i in range(len(res_no_proxy["items"])):
        assert res_proxy["items"][i]["metrics"] == res_no_proxy["items"][i]["metrics"]
    for m_name in ragas_inputs.RAGAS_METRICS:
        assert res_proxy["metrics"][m_name]["mean"] == pytest.approx(res_no_proxy["metrics"][m_name]["mean"])

    # Verify JSONL schema: 1 line per returned object (per repeat: 2 Faith + 3 Rel + 3 Corr = 8 -> 16 total)
    rows = [
        json.loads(ln)
        for ln in (tmp_path / "rec_b.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert len(rows) == 16
    for r in rows:
        assert tuple(r.keys()) == ragas_inputs.RECORDER_FIELDS
        assert r["run_id"] == "r1" and r["system"] == "PI" and r["question_id"] == "gtp-01"
        assert r["repeat"] in (1, 2)
        assert r["metric"] in ragas_inputs.RAGAS_METRICS
        assert isinstance(r["output"], dict) and isinstance(r["timestamp"], str) and r["timestamp"]


def test_derived_flags_rel_and_corr_reference_empty_and_wd_label_and_judge_settings(tmp_path, monkeypatch):
    golden_by_id = {"gtp-01": _golden_item("gtp-01", question="Q?", reference="Ref.")}
    rec_wd = _result(system="WD", qid="gtp-01", pages_given=[10], answer="Answer [p. 10].")

    # Case 1: AnswerRelevanceOutput returns empty question -> rel_no_questions;
    # StatementGeneratorOutput returns empty ONLY on reference call (2nd call for answer_correctness) -> corr_no_statements
    corr_calls = {"count": 0}

    def handler_no_q(prompt: str, response_model):
        name = response_model.__name__
        if name == "AnswerRelevanceOutput":
            return AnswerRelevanceOutput(question="", noncommittal=0)
        if name == "StatementGeneratorOutput":
            # In AnswerCorrectness, response is evaluated first, reference second
            if "Ref." in prompt:
                corr_calls["count"] += 1
                return StatementGeneratorOutput(statements=[])
            return StatementGeneratorOutput(statements=["One claim."])
        return _default_handler(prompt, response_model)

    res1 = ragas_inputs.evaluate_ragas_run(
        [rec_wd],
        golden_by_id,
        {10: "Page ten."},
        run_id="r1",
        system="WD",
        inputs_jsonl=tmp_path / "in1.jsonl",
        recorder_path=tmp_path / "rec1.jsonl",
        repeats=1,
        llm_override=_ScriptedFakeLLM(handler_no_q),
        emb_override=_DeterministicFakeEmbedding(),
    )
    assert res1["metrics"]["faithfulness"]["label"] == "not like-for-like (whole document as context)"
    flags1 = res1["items"][0]["flags"]
    assert flags1["rel_no_questions"] is True
    assert flags1["rel_all_noncommittal"] is False
    assert flags1["corr_no_statements"] is True
    assert flags1["faith_no_statements"] is False

    # Case 2: All generated questions noncommittal -> rel_all_noncommittal True, AnswerRelevancy == 0.0
    def handler_noncommittal(prompt: str, response_model):
        if response_model.__name__ == "AnswerRelevanceOutput":
            return AnswerRelevanceOutput(question="What does it say?", noncommittal=1)
        return _default_handler(prompt, response_model)

    res2 = ragas_inputs.evaluate_ragas_run(
        [rec_wd],
        golden_by_id,
        {10: "Page ten."},
        run_id="r2",
        system="WD",
        inputs_jsonl=tmp_path / "in2.jsonl",
        recorder_path=tmp_path / "rec2.jsonl",
        repeats=1,
        llm_override=_ScriptedFakeLLM(handler_noncommittal),
        emb_override=_DeterministicFakeEmbedding(),
    )
    assert res2["items"][0]["metrics"]["answer_relevancy"]["value"] == 0.0
    assert res2["items"][0]["flags"]["rel_all_noncommittal"] is True
    assert res2["items"][0]["flags"]["rel_no_questions"] is False

    # Verify eval_pageindex judge_kwargs settings reuse (no network)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://res.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key-not-real")
    seen_llm_calls = []

    def fake_llm_factory(model, **kw):
        seen_llm_calls.append((model, kw))
        return _ScriptedFakeLLM(_default_handler)

    monkeypatch.setattr(ragas_inputs, "llm_factory", fake_llm_factory)
    monkeypatch.setattr(ragas_inputs, "embedding_factory", lambda *a, **kw: _DeterministicFakeEmbedding())
    monkeypatch.setattr(
        ragas_inputs.eval_pageindex,
        "azure_async_client",
        lambda **kw: object(),
    )
    ragas_inputs.build_s11_ragas_metrics("gpt-4.1-mini")
    ragas_inputs.build_s11_ragas_metrics("gpt-5-mini")
    assert seen_llm_calls[0][0] == "gpt-4.1-mini"
    assert seen_llm_calls[0][1]["max_tokens"] == 8192
    assert "reasoning_effort" not in seen_llm_calls[0][1]
    assert seen_llm_calls[1][0] == "gpt-5-mini"
    assert seen_llm_calls[1][1]["max_tokens"] == 8192
    assert seen_llm_calls[1][1]["reasoning_effort"] == "low"


def test_fresh_subprocess_import_ragas_inputs_under_netguard():
    guarded_code = textwrap.dedent(
        """
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
            assert p is None or (p != data_dir and not p.is_relative_to(data_dir)), f"data access forbidden: {p}"
            return orig_open(file, *a, **k)

        builtins.open = guarded_open
        import eval.s11.ragas_inputs
        assert hasattr(eval.s11.ragas_inputs, "RecordingRagasLLM")
        """
    ).strip()
    proc = subprocess.run(
        [sys.executable, "-c", guarded_code],
        cwd=ROOT,
        env={**os.environ, "REGCLAW_STUB": "1", "LANGFUSE_ENABLED": "false"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
