"""Offline tests for eval.s11.ledger: pi_prices.yaml, CostLedger, UsageLogger, paid_lock, write_once, Q3."""
from contextlib import contextmanager
from decimal import Decimal
import importlib
import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as NS

import pytest

import _netguard
from eval.s11.ledger import (
    AlreadySettledError,
    CapExceededError,
    CostLedger,
    DuplicateReservationError,
    PaidLockHeld,
    USAGE_FIELDS,
    UnknownModelPriceError,
    UnknownReservationError,
    UsageLogger,
    WriteOnceError,
    compute_actual_cost_usd,
    default_s11_paths,
    estimate_input_tokens,
    estimate_reserve_cost_usd,
    load_prices,
    paid_lock,
    write_once_json,
)

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-key-not-real"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("REGCLAW_STUB", "1")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    _netguard.block_network(monkeypatch, "ledger tests forbid network")


def test_prices_table_rates_haiku_tiers_and_azure_non_billable(caplog):
    prices = load_prices(ROOT / "pageindex" / "pi_prices.yaml")
    assert prices.cap_usd == Decimal("60.0")
    assert set(prices.models) == {
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "gpt-4.1-mini",
        "gpt-5-mini",
        "text-embedding-3-small",
    }
    assert "o4-mini" not in prices.models

    # Sonnet exact rates: $2/1M in, $10/1M out, $0.20/1M cache_read, $2.50/1M cache_write_5m
    sonnet_cost = compute_actual_cost_usd(
        prices,
        "claude-sonnet-5-5",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
        cache_creation_input_tokens=1_000_000,
    )
    assert sonnet_cost == Decimal("14.7000000000")

    # Haiku tier boundary: <= 100K vs > 100K (prompt = input + cache_read + cache_write)
    lo = compute_actual_cost_usd(prices, "claude-haiku-5-5", input_tokens=100_000, output_tokens=1_000_000)
    assert lo == Decimal("0.5100000000")  # 0.10*0.1 + 0.50*1.0
    hi = compute_actual_cost_usd(prices, "claude-haiku-5-5", input_tokens=100_001, output_tokens=1_000_000)
    assert hi == Decimal("2.5500005000")  # 100001*0.50/1M + 2.50

    # Haiku non-zero cache tokens trigger tier flip + warning + 1.0x / 1.25x input pricing
    with caplog.at_level(logging.WARNING, logger="s11.ledger"):
        haiku_cached = compute_actual_cost_usd(
            prices,
            "claude-haiku-5-5",
            input_tokens=60_000,
            output_tokens=0,
            cache_read_input_tokens=30_000,
            cache_creation_input_tokens=20_000,  # total prompt = 110_000 -> >100K tier ($0.50/1M)
        )
    assert "Haiku response reported non-zero cache tokens" in caplog.text
    # (60000 * 0.50 + 30000 * 0.50 + 20000 * 0.625) / 1M = 57500 / 1M = 0.0575
    assert haiku_cached == Decimal("0.0575000000")

    # Azure models never bill against cap and return None cost
    for az in ("gpt-4.1-mini", "gpt-5-mini", "text-embedding-3-small"):
        assert prices.models[az].bill_against_cap is False
        assert compute_actual_cost_usd(prices, az, input_tokens=1000, output_tokens=500) is None

    with pytest.raises(UnknownModelPriceError):
        compute_actual_cost_usd(prices, "unknown-model-x", input_tokens=10)


def test_input_estimate_and_reserve_tiers(tmp_path):
    assert estimate_input_tokens(0) == 1
    assert estimate_input_tokens(2) == 1
    assert estimate_input_tokens(300) == 100

    prices = load_prices(ROOT / "pageindex" / "pi_prices.yaml")
    # Haiku reserve uses tier of input_estimate
    r_lo = estimate_reserve_cost_usd(prices, "claude-haiku-5-5", input_estimate=100_000, max_tokens=1000)
    assert r_lo == Decimal("0.0105000000")
    r_hi = estimate_reserve_cost_usd(prices, "claude-haiku-5-5", input_estimate=100_001, max_tokens=1000)
    assert r_hi == Decimal("0.0525005000")

    # Azure cannot reserve
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    with pytest.raises(ValueError, match="bill_against_cap=false"):
        ledger.reserve(call_id="c1", lane="judge", model="gpt-4.1-mini", attempt=1, input_estimate=100, max_tokens=100)


def test_ledger_cap_reserve_settle_timeout_retained_and_reload(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = CostLedger(ledger_path, cap_usd="0.20")

    # Sonnet 1M chars -> ~1M tokens? Use exact values: inp_est=10000, max_tokens=10000
    # reserve = (10000*2.0 + 10000*10.0) / 1M = 0.12
    r1 = ledger.reserve(call_id="c1", lane="agent", model="claude-sonnet-5-5", attempt=1,
                        input_estimate=10_000, max_tokens=10_000)
    assert r1.reserved_usd == Decimal("0.1200000000")
    assert ledger.committed_usd == Decimal("0.1200000000")
    assert ledger.remaining_usd == Decimal("0.0800000000")

    # Second reserve of 0.12 exceeds $0.20 cap -> refused BEFORE append
    lines_before = ledger_path.read_text(encoding="utf-8").splitlines()
    with pytest.raises(CapExceededError, match="Cost cap exceeded"):
        ledger.reserve(call_id="c2", lane="agent", model="claude-sonnet-5-5", attempt=1,
                       input_estimate=10_000, max_tokens=10_000)
    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == len(lines_before)

    # Error settles at 0 -> frees entire 0.12
    st1 = ledger.settle(r1.reservation_id, status="error")
    assert st1.actual_usd == Decimal("0") and st1.retained is False
    assert ledger.remaining_usd == Decimal("0.20")

    # Timeout retains full reserved amount against cap permanently
    r2 = ledger.reserve(call_id="c3", lane="agent", model="claude-sonnet-5-5", attempt=1,
                        input_estimate=10_000, max_tokens=10_000)
    st2 = ledger.settle(r2.reservation_id, status="timeout", timeout_retained=True)
    assert st2.actual_usd == Decimal("0.1200000000") and st2.retained is True
    assert ledger.spent_usd == Decimal("0.1200000000")
    assert ledger.remaining_usd == Decimal("0.0800000000")

    # Reload from disk preserves exact totals
    reloaded = CostLedger(ledger_path, cap_usd="0.20")
    assert reloaded.spent_usd == Decimal("0.1200000000")
    assert reloaded.reserved_usd == Decimal("0")
    assert reloaded.remaining_usd == Decimal("0.0800000000")


def test_ledger_single_use_ids_and_write_once_json(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = CostLedger(ledger_path)
    res = ledger.reserve(call_id="c1", lane="agent", model="claude-sonnet-5-5", attempt=1,
                         input_estimate=10, max_tokens=100, reservation_id="fixed-1")
    with pytest.raises(DuplicateReservationError):
        ledger.reserve(call_id="c1", lane="agent", model="claude-sonnet-5-5", attempt=1,
                       input_estimate=10, max_tokens=100, reservation_id="fixed-1")
    with pytest.raises(UnknownReservationError):
        ledger.settle("does-not-exist", status="error")
    ledger.settle(res.reservation_id, input_tokens=10, output_tokens=5, status="ok")
    with pytest.raises(AlreadySettledError):
        ledger.settle(res.reservation_id, status="error")

    # Write-once JSON refuses overwrite
    out_json = tmp_path / "artifact.json"
    write_once_json(out_json, {"ok": 1})
    with pytest.raises(WriteOnceError):
        write_once_json(out_json, {"ok": 2})
    assert json.loads(out_json.read_text(encoding="utf-8")) == {"ok": 1}


CHILD = """
import os, queue, socket, sys, threading
from pathlib import Path
root = Path(sys.argv[1]).resolve()
def denied(*a, **k): raise AssertionError('child network forbidden')
socket.socket.connect = denied
from eval.s11.ledger import paid_lock
with paid_lock(root):
    print('READY', flush=True)
    q = queue.Queue()
    threading.Thread(target=lambda: q.put(sys.stdin.readline()), daemon=True).start()
    q.get(timeout=30)
"""


@contextmanager
def _holding_child(root: Path):
    proc = subprocess.Popen(
        [sys.executable, "-B", "-c", CHILD, str(root)],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready = queue.Queue()
    reader = threading.Thread(target=lambda: ready.put(proc.stdout.readline()), daemon=True)
    reader.start()
    try:
        assert ready.get(timeout=30) == "READY\n"
        yield proc
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=30)
        reader.join(timeout=30)
        for s in (proc.stdin, proc.stdout, proc.stderr):
            s.close()


def test_paid_lock_contender_and_cleanup(tmp_path):
    with _holding_child(tmp_path):
        before = time.monotonic()
        with pytest.raises(PaidLockHeld, match="another paid S11 process is running"):
            with paid_lock(tmp_path):
                pytest.fail("contender acquired lock")
        assert time.monotonic() - before < 5.0
    # After release, re-acquire succeeds cleanly
    with paid_lock(tmp_path) as lock_path:
        assert lock_path == tmp_path / "data" / "runs" / "s11_paid.lock"


def test_usage_logger_fields_settings_and_q3_requirements(monkeypatch, tmp_path):
    usage_path = tmp_path / "usage.jsonl"
    logger = UsageLogger(usage_path)
    row = logger.log(
        provider="anthropic",
        lane="agent",
        model_requested="claude-sonnet-5-5",
        model_returned="claude-sonnet-5-5",
        attempt=1,
        status="ok",
        status_code=200,
        error_type=None,
        input_tokens=10,
        output_tokens=20,
        cache_read_input_tokens=5,
        cache_creation_input_tokens=0,
        cost_usd=0.000221,
        latency_s=0.123,
        timeout_s=300.0,
        call_id="ant-1",
    )
    assert tuple(row.keys()) == USAGE_FIELDS
    assert json.loads(usage_path.read_text(encoding="utf-8").strip()) == row

    # Default CLI/driver paths live under data/runs/s11/ without touching disk
    dl, du, dk = default_s11_paths(tmp_path)
    assert dl == tmp_path / "data" / "runs" / "s11" / "ledger.jsonl"
    assert du == tmp_path / "data" / "runs" / "s11" / "usage.jsonl"
    assert dk == tmp_path / "data" / "runs" / "s11_paid.lock"

    # Settings includes positive tree_reserve_max_tokens = 32000
    monkeypatch.syspath_prepend(str(ROOT / "pageindex"))
    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    common = importlib.import_module("pi_common")
    agent = importlib.import_module("pi_agent")
    ingest = importlib.import_module("pi_ingest")
    eval_pi = importlib.import_module("eval_pageindex")

    assert common.Settings().tree_reserve_max_tokens == 32000
    assert common.load_settings().tree_reserve_max_tokens == 32000
    with pytest.raises(ValueError):
        common.Settings(tree_reserve_max_tokens=0)

    # Q3: answer_question, build_tree, and real azure_async_client refuse missing ledger/usage_log
    monkeypatch.setattr(agent, "resolve_edition", lambda *a: NS(tree_path="t.json", title="T", version="1", page_count=1, doc_description=""))
    monkeypatch.setattr(agent, "load_manifest", lambda s: None)
    with pytest.raises(ValueError, match="ledger and usage_log"):
        agent.answer_question("q", "gtp", s=common.Settings())

    monkeypatch.setattr(ingest, "installed_pageindex_commit", lambda: common.PAGEINDEX_PIN_SHA)
    with pytest.raises(ValueError, match="ledger and usage_log"):
        ingest.build_tree("doc.pdf", common.Settings(store_dir=tmp_path / "store"))

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://res.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", SECRET)
    with pytest.raises(ValueError, match="usage_log"):
        eval_pi.azure_async_client()
