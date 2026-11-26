"""Offline tests for PageIndex LiteLLM wrapper-first accounting and CustomLogger cross-check (S11b1-06a)."""
import asyncio
import contextvars
from decimal import Decimal
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS

import httpx
import pytest

import _netguard
from eval.s11.ledger import CapExceededError, CostLedger, USAGE_FIELDS, UsageLogger, compute_actual_cost_usd

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("REGCLAW_STUB", "1")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    _netguard.block_network(monkeypatch, "litellm hook tests forbid network")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def mods(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "pageindex"))
    return NS(
        common=importlib.import_module("pi_common"),
        ingest=importlib.import_module("pi_ingest"),
        client=importlib.import_module("pi_client"),
    )


def _install_fake_pageindex_with_litellm(monkeypatch, *, run_fn):
    backend = contextvars.ContextVar("b", default=None)

    fake_litellm = ModuleType("litellm")
    fake_litellm.callbacks = []

    class CustomLogger:
        def __init__(self, *a, **k):
            pass

    fake_litellm.CustomLogger = CustomLogger
    fake_litellm.completion = lambda **kw: NS(model=kw["model"], usage=NS(prompt_tokens=10, completion_tokens=5))
    fake_litellm.acompletion = None

    mods = {
        "litellm": fake_litellm,
        "pageindex": ModuleType("pageindex"),
        "pageindex.utils": ModuleType("pageindex.utils"),
        "pageindex.page_index_classic": ModuleType("pageindex.page_index_classic"),
        "pageindex.flash": ModuleType("pageindex.flash"),
        "pageindex.flash.api": ModuleType("pageindex.flash.api"),
    }
    mods["pageindex.utils"].ConfigLoader = lambda: NS(load=lambda x: NS(**x))
    mods["pageindex.utils"]._llm_backend = backend
    mods["pageindex.page_index_classic"].page_index_main = run_fn
    mods["pageindex.flash"].page_index_flash = lambda *a, **k: {"structure": []}
    mods["pageindex.flash.api"].flash_rejection_reason = lambda *a, **k: None
    for name, m in mods.items():
        monkeypatch.setitem(sys.modules, name, m)
    return fake_litellm


def test_change1_pre_call_swallow_does_not_bypass_wrapper_cap_refusal(mods, tmp_path):
    """CHANGE 1: Non-blocking CustomLogger.log_pre_api_call does not stop calls, so the wrapper refuses."""
    ledger = CostLedger(tmp_path / "ledger.jsonl", cap_usd="0.01")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")

    orig_called = []
    fake_litellm = ModuleType("litellm")
    fake_litellm.callbacks = []

    def simulated_litellm_completion(**kw):
        # Mirror litellm_logging.py: exceptions in log_pre_api_call are swallowed as [Non-Blocking].
        for cb in list(fake_litellm.callbacks):
            try:
                cb.log_pre_api_call(model=kw.get("model"), messages=kw.get("messages"), kwargs=kw)
            except Exception:
                pass
        orig_called.append(kw)
        return NS(model=kw["model"], usage=NS(prompt_tokens=10, completion_tokens=5))

    fake_litellm.completion = simulated_litellm_completion
    fake_litellm.acompletion = None

    with mods.client.litellm_usage_logger(
        ledger, usage_log, tree_reserve_max_tokens=32000, settle_timeout_s=0.05, litellm_module=fake_litellm
    ) as state:
        # 32000 * $10/1M = $0.32 > $0.01 cap -> wrapper blocks before orig_called
        with pytest.raises(CapExceededError):
            fake_litellm.completion(model="anthropic/claude-sonnet-5-5", messages=[{"role": "user", "content": "TOC"}])
    assert orig_called == []
    assert state.wrapper_calls == 0
    assert state.custom_logger_calls == 0
    assert _read_jsonl(tmp_path / "usage.jsonl")[0]["status"] == "cap_refused"


def test_q4_tree_reserve_max_tokens_and_azure_route_skips_ledger(mods, tmp_path):
    ledger = CostLedger(tmp_path / "ledger.jsonl", cap_usd="60.0")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    fake_litellm = ModuleType("litellm")
    fake_litellm.callbacks = []
    forwarded_kwargs = []

    def sim_completion(**kw):
        forwarded_kwargs.append(kw)
        resp = NS(model=kw["model"], usage=NS(prompt_tokens=100, completion_tokens=50))
        for cb in list(fake_litellm.callbacks):
            cb.log_success_event(kw, resp, None, None)
        return resp

    fake_litellm.completion = sim_completion

    with mods.client.litellm_usage_logger(
        ledger, usage_log, tree_reserve_max_tokens=32000, settle_timeout_s=0.05, litellm_module=fake_litellm
    ) as state:
        # Omitted max_tokens reserves with 32000 without adding max_tokens to outgoing kwargs
        fake_litellm.completion(model="anthropic/claude-sonnet-5-5", messages=[{"role": "user", "content": "hi"}])
        # Azure route logs usage with cost_usd=None and never writes to ledger
        fake_litellm.completion(model="openai/gpt-5-mini", messages=[{"role": "user", "content": "hi"}])

    assert (state.wrapper_calls, state.custom_logger_calls) == (2, 2)
    assert "max_tokens" not in forwarded_kwargs[0]
    ledger_rows = _read_jsonl(tmp_path / "ledger.jsonl")
    assert len(ledger_rows) == 2  # only the Anthropic call reserved + settled
    assert ledger_rows[0]["max_tokens"] == 32000
    assert ledger_rows[0]["max_tokens_source"] == "tree_reserve_max_tokens"
    usage_rows = _read_jsonl(tmp_path / "usage.jsonl")
    assert len(usage_rows) == 2
    assert usage_rows[1]["provider"] == "azure_openai" and usage_rows[1]["cost_usd"] is None


def test_wrapper_handles_exception_and_timeout_synchronously_in_sync_and_async(mods, tmp_path):
    """Wrapper settles 0 on non-timeout error and retains reserve on timeout without waiting for callbacks."""
    ledger = CostLedger(tmp_path / "ledger.jsonl", cap_usd="60.0")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    fake_litellm = ModuleType("litellm")
    fake_litellm.callbacks = []

    def fail_sync(**kw):
        raise RuntimeError("boom")

    async def timeout_async(**kw):
        for cb in list(fake_litellm.callbacks):
            # Simulate LiteLLM firing both sync and async failure callbacks for the same call_id
            cb.log_failure_event(kw, None, None, None)
            await cb.async_log_failure_event(kw, None, None, None)
        raise httpx.ReadTimeout("slow")

    fake_litellm.completion = fail_sync
    fake_litellm.acompletion = timeout_async

    with mods.client.litellm_usage_logger(
        ledger, usage_log, tree_reserve_max_tokens=1000, settle_timeout_s=0.02, litellm_module=fake_litellm
    ) as state:
        with pytest.raises(RuntimeError, match="boom"):
            fake_litellm.completion(model="anthropic/claude-sonnet-5-5", messages=[{"role": "user", "content": "a"}])
        with pytest.raises(httpx.ReadTimeout):
            asyncio.run(
                fake_litellm.acompletion(model="anthropic/claude-sonnet-5-5", messages=[{"role": "user", "content": "b"}])
            )

    assert state.wrapper_calls == 2
    assert state.custom_logger_calls == 1  # sync call fired 0 callbacks; async failure deduped to 1
    assert ledger.reserved_usd == Decimal("0")
    assert state.open_reservations == []
    settle_rows = [r for r in _read_jsonl(tmp_path / "ledger.jsonl") if r["event"] == "settle"]
    assert settle_rows[0]["status"] == "error" and settle_rows[0]["actual_usd"] == 0.0 and settle_rows[0]["retained"] is False
    assert settle_rows[1]["status"] == "timeout" and settle_rows[1]["retained"] is True
    assert Decimal(str(settle_rows[1]["actual_usd"])) == ledger.spent_usd > Decimal("0")
    usage_rows = _read_jsonl(tmp_path / "usage.jsonl")
    assert [r["status"] for r in usage_rows] == ["error", "timeout"]
    assert usage_rows[0]["error_type"] == "RuntimeError" and usage_rows[1]["error_type"] == "ReadTimeout"


def test_change2_build_tree_raises_and_saves_no_tree_when_pageindex_swallows_refusal(mods, monkeypatch, tmp_path):
    """CHANGE 2: PageIndex catches/retries and returns a tree despite refusal -> build_tree raises & saves nothing."""
    ledger = CostLedger(tmp_path / "ledger.jsonl", cap_usd="0.01")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    monkeypatch.setattr(mods.ingest, "installed_pageindex_commit", lambda: mods.common.PAGEINDEX_PIN_SHA)
    monkeypatch.setattr(mods.ingest, "text_layer_report", lambda pdf: {"pages": 1, "empty_pages": 0, "scanned": False})
    monkeypatch.setattr(mods.ingest, "page_count", lambda pdf: 1)

    def swallowing_page_index(pdf, opt):
        import litellm
        for _ in range(3):
            try:
                litellm.completion(model=opt.index_model, messages=[{"role": "user", "content": "TOC"}])
            except CapExceededError:
                pass
        return {"doc_name": "partial.pdf", "structure": [{"title": "Swallowed"}]}

    _install_fake_pageindex_with_litellm(monkeypatch, run_fn=swallowing_page_index)
    pdf = tmp_path / "g.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    s = mods.common.Settings(store_dir=tmp_path / "store", litellm_max_tokens=16000)

    with pytest.raises(CapExceededError):
        mods.ingest.ingest(str(pdf), "gtp", "2019", s=s, ledger=ledger, usage_log=usage_log)
    assert not list((tmp_path / "store" / "trees").iterdir())
    assert not (tmp_path / "store" / "manifest.json").exists()


def test_change3_wrapper_settles_with_zero_callbacks_and_raises_on_bypass(mods, monkeypatch, tmp_path, capsys):
    """S11b1-06a: fewer CustomLogger events -> reported, not an error; MORE -> build_tree raises & saves nothing."""
    monkeypatch.setattr(mods.ingest, "installed_pageindex_commit", lambda: mods.common.PAGEINDEX_PIN_SHA)
    monkeypatch.setattr(mods.ingest, "text_layer_report", lambda pdf: {"pages": 1, "empty_pages": 0, "scanned": False})
    monkeypatch.setattr(mods.ingest, "page_count", lambda pdf: 1)

    # Part A: CustomLogger fires 0 events -> wrapper still settles synchronously; mismatch reported, both counts returned
    ledger_ok = CostLedger(tmp_path / "ledger_ok.jsonl", cap_usd="60.0")
    usage_ok = UsageLogger(tmp_path / "usage_ok.jsonl")

    def single_call_page_index(pdf, opt):
        import litellm

        resp = litellm.completion(model=opt.index_model, messages=[{"role": "user", "content": "TOC"}], max_tokens=100)
        return {"structure": [{"title": resp.model}]}

    fake_litellm = _install_fake_pageindex_with_litellm(monkeypatch, run_fn=single_call_page_index)
    fake_litellm.completion = lambda **kw: NS(model="claude-sonnet-5-5", usage=NS(prompt_tokens=100, completion_tokens=20))
    s = mods.common.Settings(store_dir=tmp_path / "store")
    res_ok = mods.ingest.build_tree("doc.pdf", s, ledger=ledger_ok, usage_log=usage_ok, settle_timeout_s=0.05)

    assert res_ok["open_reservations"] == []
    assert res_ok["wrapper_calls"] == 1
    assert res_ok["custom_logger_calls"] == 0
    assert ledger_ok.reserved_usd == Decimal("0") and ledger_ok.spent_usd > Decimal("0")
    assert "fewer calls" in capsys.readouterr().err
    assert len(_read_jsonl(tmp_path / "usage_ok.jsonl")) == 1

    # Part B: A call path bypasses the wrapper (CustomLogger sees MORE calls than wrapper) -> build_tree raises
    ledger_bypass = CostLedger(tmp_path / "ledger_bypass.jsonl", cap_usd="60.0")
    usage_bypass = UsageLogger(tmp_path / "usage_bypass.jsonl")
    fake_litellm2 = _install_fake_pageindex_with_litellm(monkeypatch, run_fn=single_call_page_index)
    captured_orig = fake_litellm2.completion

    def bypassing_page_index(pdf, opt):
        # Invoke the unwrapped reference captured before litellm_usage_logger patched litellm.completion,
        # and notify callbacks directly as real LiteLLM would on an unhooked call path.
        resp = captured_orig(model=opt.index_model, messages=[{"role": "user", "content": "BYPASS"}])
        for cb in list(fake_litellm2.callbacks):
            cb.log_success_event({"litellm_call_id": "bypass-1"}, resp, None, None)
        return {"structure": [{"title": "Bypassed"}]}

    sys.modules["pageindex.page_index_classic"].page_index_main = bypassing_page_index
    pdf = tmp_path / "bypass.pdf"
    pdf.write_bytes(b"%PDF-1.4 bypass")
    with pytest.raises(RuntimeError, match="bypassed"):
        mods.ingest.ingest(str(pdf), "gtp", "2019", s=s, ledger=ledger_bypass, usage_log=usage_bypass)
    assert not list((tmp_path / "store" / "trees").iterdir())
    assert not (tmp_path / "store" / "manifest.json").exists()


def test_change5_real_litellm_mock_response_settles_ledger_via_custom_logger(mods, monkeypatch, tmp_path):
    """CHANGE 5 (S11b1-06a): Real installed litellm mock_response settles & logs synchronously via wrapper."""
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    litellm = pytest.importorskip("litellm")

    ledger = CostLedger(tmp_path / "ledger.jsonl", cap_usd="60.0")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    orig_completion = litellm.completion

    with mods.client.litellm_usage_logger(
        ledger, usage_log, tree_reserve_max_tokens=32000, settle_timeout_s=0.05
    ) as state:
        resp = litellm.completion(
            model="anthropic/claude-sonnet-5-5",
            messages=[{"role": "user", "content": "Summarize node 0001"}],
            max_tokens=200,
            drop_params=True,
            max_retries=0,
            mock_response="Summary of node 0001.",
        )
        assert resp.choices[0].message.content == "Summary of node 0001."
        # Wrapper must have settled & logged synchronously BEFORE context exit (no background-thread wait)
        assert state.wrapper_calls == 1
        assert ledger.open_reservation_ids(state.tracked_reservation_ids) == []
        assert ledger.reserved_usd == Decimal("0")
        assert len(_read_jsonl(tmp_path / "usage.jsonl")) == 1

    assert litellm.completion is orig_completion
    assert state.custom_logger_calls <= state.wrapper_calls
    assert state.open_reservations == []

    expected_cost = compute_actual_cost_usd(
        ledger.prices,
        "claude-sonnet-5-5",
        input_tokens=int(resp.usage.prompt_tokens),
        output_tokens=int(resp.usage.completion_tokens),
    )
    assert expected_cost is not None and ledger.spent_usd == expected_cost

    usage_rows = _read_jsonl(tmp_path / "usage.jsonl")
    assert len(usage_rows) == 1
    row = usage_rows[0]
    assert tuple(row.keys()) == USAGE_FIELDS
    assert row["provider"] == "anthropic" and row["lane"] == "tree" and row["status"] == "ok"
    assert row["status_code"] == 200
    assert row["input_tokens"] == int(resp.usage.prompt_tokens)
    assert row["output_tokens"] == int(resp.usage.completion_tokens)
    assert row["cost_usd"] == pytest.approx(float(expected_cost))
