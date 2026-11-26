"""Offline tests for pageindex.pi_client AnthropicRetryClient."""
from decimal import Decimal
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import anthropic
import httpx
import pytest

import _netguard
from eval.s11.ledger import CapExceededError, CostLedger, USAGE_FIELDS, UsageLogger

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("REGCLAW_STUB", "1")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    _netguard.block_network(monkeypatch, "client tests forbid network")


@pytest.fixture
def client_cls(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "pageindex"))
    return importlib.import_module("pi_client").AnthropicRetryClient


def _req():
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_err(cls, status_code: int):
    resp = httpx.Response(status_code=status_code, request=_req())
    return cls(f"HTTP {status_code}", response=resp, body={})


def _ok(model="claude-sonnet-5-5-20260901", inp=100, out=50, cr=200, cw=100):
    return NS(
        model=model,
        stop_reason="end_turn",
        content=[NS(type="text", text="SECRET_ANSWER_SENTINEL")],
        usage=NS(
            input_tokens=inp,
            output_tokens=out,
            cache_read_input_tokens=cr,
            cache_creation_input_tokens=cw,
        ),
    )


class ScriptedSDK:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.messages = NS(create=self.create)

    def create(self, **kw):
        self.calls.append(kw)
        item = self.outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_retry_backoff_per_attempt_reserve_settle_and_success(client_cls, tmp_path):
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    sleeps = []
    sdk = ScriptedSDK([
        _status_err(anthropic.RateLimitError, 429),
        _status_err(anthropic.InternalServerError, 500),
        _ok(),
    ])
    wrapped = client_cls(sdk, ledger=ledger, usage_log=usage_log, lane="agent", timeout_s=300.0, sleep_fn=sleeps.append)

    resp = wrapped.messages.create(
        model="claude-sonnet-5-5",
        max_tokens=1000,
        system=[{"type": "text", "text": "SECRET_PROMPT_SENTINEL", "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "SECRET_QUESTION_SENTINEL"}],
    )
    assert resp.model == "claude-sonnet-5-5-20260901"
    assert sleeps == [2.0, 4.0]
    assert wrapped.retries == 2
    assert len(sdk.calls) == 3

    ledger_rows = _read_jsonl(tmp_path / "ledger.jsonl")
    assert len(ledger_rows) == 6  # 3 reserves + 3 settles
    reserves = [r for r in ledger_rows if r["event"] == "reserve"]
    settles = [r for r in ledger_rows if r["event"] == "settle"]
    assert len({r["reservation_id"] for r in reserves}) == 3
    assert len({r["call_id"] for r in reserves}) == 1
    assert settles[0]["actual_usd"] == 0.0 and settles[0]["retained"] is False
    assert settles[1]["actual_usd"] == 0.0 and settles[1]["retained"] is False
    # (100*2 + 50*10 + 200*0.10 + 100*2.50) / 1M = (200 + 500 + 20 + 250) / 1M = 0.00097
    assert Decimal(str(settles[2]["actual_usd"])) == Decimal("0.00097")
    assert ledger.reserved_usd == Decimal("0")

    usage_rows = _read_jsonl(tmp_path / "usage.jsonl")
    assert [r["attempt"] for r in usage_rows] == [1, 2, 3]
    assert [r["status"] for r in usage_rows] == ["error", "error", "ok"]
    assert [r["status_code"] for r in usage_rows] == [429, 500, 200]
    assert len({r["call_id"] for r in usage_rows}) == 1
    assert all(tuple(r.keys()) == USAGE_FIELDS for r in usage_rows)

    raw_logs = (tmp_path / "ledger.jsonl").read_text() + (tmp_path / "usage.jsonl").read_text()
    for secret in ("SECRET_PROMPT_SENTINEL", "SECRET_QUESTION_SENTINEL", "SECRET_ANSWER_SENTINEL"):
        assert secret not in raw_logs


def test_400_bad_request_does_not_retry_and_settles_zero(client_cls, tmp_path):
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    sleeps = []
    sdk = ScriptedSDK([_status_err(anthropic.BadRequestError, 400), _ok()])
    wrapped = client_cls(sdk, ledger=ledger, usage_log=usage_log, lane="verifier", timeout_s=120.0, sleep_fn=sleeps.append)

    with pytest.raises(anthropic.BadRequestError):
        wrapped.messages.create(model="claude-haiku-5-5", max_tokens=500, messages=[{"role": "user", "content": "x"}])
    assert sleeps == []
    assert wrapped.retries == 0
    assert len(sdk.calls) == 1
    assert ledger.spent_usd == Decimal("0")
    assert ledger.reserved_usd == Decimal("0")


def test_timeout_retains_reserve_and_exhaustion_checks_cap(client_cls, tmp_path):
    # First attempt: timeout retains reserve; second attempt succeeds.
    # Tight cap allows 2 attempts total, then blocks a subsequent call after retained spend!
    # Reserve per sonnet 1000 max_tokens, 3 chars -> inp_est=1: (1*2 + 1000*10)/1M = 0.010002
    ledger = CostLedger(tmp_path / "ledger.jsonl", cap_usd="0.025")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    sleeps = []
    sdk = ScriptedSDK([
        anthropic.APITimeoutError(request=_req()),
        _ok(inp=100, out=100, cr=0, cw=0),
    ])
    wrapped = client_cls(sdk, ledger=ledger, usage_log=usage_log, lane="agent", timeout_s=300.0, sleep_fn=sleeps.append)
    resp = wrapped.messages.create(model="claude-sonnet-5-5", max_tokens=1000, messages=[{"role": "user", "content": "abc"}])
    assert resp.stop_reason == "end_turn"
    assert sleeps == [2.0]

    rows = _read_jsonl(tmp_path / "ledger.jsonl")
    settles = [r for r in rows if r["event"] == "settle"]
    assert settles[0]["retained"] is True and settles[0]["actual_usd"] == settles[0]["reserved_usd"]
    # Second call needs 0.010002, remaining is 0.025 - (0.010002 + 0.0012) = 0.013798 -> succeeds once,
    # Wait: test immediate exhaustion with cap exceeded:
    with pytest.raises(CapExceededError):
        wrapped.messages.create(model="claude-sonnet-5-5", max_tokens=2000, messages=[{"role": "user", "content": "abc"}])
    usage_rows = _read_jsonl(tmp_path / "usage.jsonl")
    assert usage_rows[-1]["status"] == "cap_refused"


def test_connection_error_retries_three_times_and_settles_zero(client_cls, tmp_path):
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    sleeps = []
    sdk = ScriptedSDK([
        anthropic.APIConnectionError(request=_req()),
        anthropic.APIConnectionError(request=_req()),
        anthropic.APIConnectionError(request=_req()),
    ])
    wrapped = client_cls(sdk, ledger=ledger, usage_log=usage_log, lane="verifier", sleep_fn=sleeps.append)
    with pytest.raises(anthropic.APIConnectionError):
        wrapped.messages.create(model="claude-haiku-5-5", max_tokens=500, messages=[{"role": "user", "content": "hello"}])
    assert sleeps == [2.0, 4.0]
    assert wrapped.retries == 2
    assert len(sdk.calls) == 3
    assert ledger.spent_usd == Decimal("0")
    assert ledger.reserved_usd == Decimal("0")


def test_importing_pi_client_sets_litellm_local_model_cost_map(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "pageindex"))
    importlib.import_module("pi_client")
    assert os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP") == "True"

    pi_client_path = ROOT / "pageindex" / "pi_client.py"
    for initial, expected in ((None, "True"), ("False", "False")):
        if initial is None:
            monkeypatch.delenv("LITELLM_LOCAL_MODEL_COST_MAP", raising=False)
        else:
            monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", initial)
        mod_name = f"_pi_client_env_probe_{initial or 'unset'}"
        spec = importlib.util.spec_from_file_location(mod_name, str(pi_client_path))
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, mod_name, mod)
        spec.loader.exec_module(mod)
        assert os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP") == expected
