"""Offline Part D tests for prices, effort/thinking/tool_choice rules, client updates, UsageLogger, and precount."""
import copy
from decimal import Decimal
import importlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace as NS

import anthropic
import httpx2
import pytest

import _netguard
from eval.s11 import answer as s11_answer, precount
from eval.s11.ledger import (
    CostLedger,
    USAGE_FIELDS,
    UsageLogger,
    compute_actual_cost_usd,
    estimate_reserve_cost_usd,
    load_prices,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("REGCLAW_STUB", "1")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    _netguard.block_network(monkeypatch, "s11b2 client update tests forbid network")


@pytest.fixture
def mods(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "pageindex"))
    return NS(
        common=importlib.import_module("pi_common"),
        client=importlib.import_module("pi_client"),
        agent=importlib.import_module("pi_agent"),
    )


def _usage(inp=20, out=10, cr=0, cw=0, thinking_tokens=None):
    details = None if thinking_tokens is None else NS(thinking_tokens=thinking_tokens)
    return NS(
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=cr,
        cache_creation_input_tokens=cw,
        output_tokens_details=details,
    )


class _ScriptedSDK:
    def __init__(self, responses):
        self.responses = list(responses)
        self.create_calls = []
        self.count_calls = []
        self.messages = NS(create=self.create, count_tokens=self.count_tokens)

    def _snap(self, kw):
        snap = dict(kw)
        if "messages" in snap:
            snap["messages"] = copy.deepcopy(snap["messages"])
        return snap

    def create(self, **kw):
        self.create_calls.append(self._snap(kw))
        return self.responses.pop(0)

    def count_tokens(self, **kw):
        self.count_calls.append(self._snap(kw))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_prices_sonnet_cache_read_and_haiku_cache_tiers(caplog):
    raw_yaml = (ROOT / "pageindex" / "pi_prices.yaml").read_text(encoding="utf-8")
    assert "platform.claude.com pricing + prompt-caching docs, checked 2026-11-26" in raw_yaml

    prices = load_prices(ROOT / "pageindex" / "pi_prices.yaml")
    sonnet = prices.models["claude-sonnet-5-5"]
    assert sonnet.cache_read_per_1m == Decimal("0.10")
    assert sonnet.cache_write_5m_per_1m == Decimal("2.50")

    haiku = prices.models["claude-haiku-5-5"]
    assert len(haiku.prompt_tiers) == 2
    assert haiku.prompt_tiers[0].cache_read_per_1m == Decimal("0.01")
    assert haiku.prompt_tiers[0].cache_write_5m_per_1m == Decimal("0.125")
    assert haiku.prompt_tiers[1].cache_read_per_1m == Decimal("0.05")
    assert haiku.prompt_tiers[1].cache_write_5m_per_1m == Decimal("0.625")

    with caplog.at_level(logging.WARNING, logger="s11.ledger"):
        lo_cost = compute_actual_cost_usd(
            prices,
            "claude-haiku-5-5",
            input_tokens=50_000,
            output_tokens=0,
            cache_read_input_tokens=30_000,
            cache_creation_input_tokens=10_000,  # total prompt = 90k <= 100k
        )
        hi_cost = compute_actual_cost_usd(
            prices,
            "claude-haiku-5-5",
            input_tokens=60_000,
            output_tokens=0,
            cache_read_input_tokens=30_000,
            cache_creation_input_tokens=20_000,  # total prompt = 110k > 100k
        )
    assert caplog.text == ""
    assert lo_cost == Decimal("0.0065500000")  # (50k*0.10 + 30k*0.01 + 10k*0.125) / 1M
    assert hi_cost == Decimal("0.0440000000")  # (60k*0.50 + 30k*0.05 + 20k*0.625) / 1M

    # Reserve uses max(input, cache_write_5m) when has_cache_control=True
    r_ctrl = estimate_reserve_cost_usd(
        prices, "claude-haiku-5-5", input_estimate=100_000, max_tokens=1000, has_cache_control=True
    )
    assert r_ctrl == Decimal("0.0130000000")


def test_effort_settings_validation_and_sent_in_request_bodies(mods, tmp_path):
    assert mods.common.Settings().answer_effort == "medium"
    assert mods.common.Settings(answer_effort="high").answer_effort == "high"
    assert mods.common.Settings().verifier_effort == "medium"

    for bad in ("low", "xhigh", "max", "auto", "", None):
        with pytest.raises(ValueError):
            mods.common.Settings(answer_effort=bad)
    for bad_ver in ("low", "high", "xhigh", None):
        with pytest.raises(ValueError):
            mods.common.Settings(verifier_effort=bad_ver)

    s_hi = mods.common.Settings(answer_effort="high")
    rec = NS(title="GTP", version="2019", page_count=10, doc_description="desc", doc_id="g@1", pdf_path="x.pdf")
    sess = mods.agent.Session(rec=rec, tree={"structure": []}, nodes={}, s=s_hi)
    pi_sdk = _ScriptedSDK([NS(content=[NS(type="text", text="Yes [p. 1].")], stop_reason="end_turn", model="s", usage=_usage())])
    mods.agent.run_agent(pi_sdk, sess, "Q?")
    assert pi_sdk.create_calls[0]["output_config"] == {"effort": "high"}

    ver_sdk = _ScriptedSDK([
        NS(
            content=[NS(type="tool_use", input={"verdicts": [{"claim_id": 1, "verdict": "supported", "evidence": "Yes", "reason": ""}]})],
            stop_reason="tool_use",
            model="claude-haiku-5-5",
            usage=_usage(),
        )
    ])
    mods.agent.verify_with_usage(ver_sdk, s_hi, "Yes [p. 1].", {1: "Yes"})
    assert ver_sdk.create_calls[0]["output_config"] == {"effort": "medium"}

    hy_sdk = _ScriptedSDK([
        NS(content=[NS(type="text", text="Yes [p. 1].")], stop_reason="end_turn", model="s", usage=_usage()),
        NS(content=[NS(type="tool_use", input={"verdicts": [{"claim_id": 1, "verdict": "supported", "evidence": "Yes", "reason": ""}]})], stop_reason="tool_use", model="h", usage=_usage()),
    ])
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    s11_answer.run_hy_answer("gtp-01", "Q?", {1: "Yes"}, s=s_hi, ledger=ledger, usage_log=usage_log, sdk_client=hy_sdk)
    assert hy_sdk.create_calls[0]["output_config"] == {"effort": "high"}
    assert hy_sdk.create_calls[1]["output_config"] == {"effort": "medium"}


def test_no_thinking_key_and_no_sonnet_tool_choice_any_or_tool(mods, tmp_path):
    s = mods.common.Settings()
    rec = NS(title="GTP", version="2019", page_count=5, doc_description="desc", doc_id="g@1", pdf_path="x.pdf")
    sess = mods.agent.Session(rec=rec, tree={"structure": []}, nodes={}, s=s)
    pi_sdk = _ScriptedSDK([NS(content=[NS(type="text", text="Yes [p. 1].")], stop_reason="end_turn", model="s", usage=_usage())])
    mods.agent.run_agent(pi_sdk, sess, "Q?")
    assert "thinking" not in pi_sdk.create_calls[0]
    assert pi_sdk.create_calls[0]["tool_choice"] == {"type": "auto"}

    ver_sdk = _ScriptedSDK([
        NS(
            content=[NS(type="tool_use", input={"verdicts": [{"claim_id": 1, "verdict": "supported", "evidence": "Yes", "reason": ""}]})],
            stop_reason="tool_use",
            model="claude-haiku-5-5",
            usage=_usage(),
        )
    ])
    mods.agent.verify_with_usage(ver_sdk, s, "Yes [p. 1].", {1: "Yes"})
    assert "thinking" not in ver_sdk.create_calls[0]
    assert ver_sdk.create_calls[0]["tool_choice"] == {"type": "tool", "name": "report_verdicts"}

    hy_req = s11_answer.build_hy_request("Q?", {1: "Yes"}, s=s)
    wd_req = s11_answer.build_wd_request("Q?", {i: "" for i in range(1, 117)}, s=s)
    for req in (hy_req, wd_req):
        assert "thinking" not in req
        assert req.get("tool_choice") not in ("any", "tool", {"type": "any"}, {"type": "tool"})


def test_pi_agent_passes_content_blocks_verbatim_and_in_order(mods, monkeypatch):
    rec = NS(title="GTP", version="2019", page_count=5, doc_description="", doc_id="g@1", pdf_path="x.pdf")
    sess = mods.agent.Session(rec=rec, tree={"structure": []}, nodes={}, s=mods.common.Settings())
    monkeypatch.setattr(mods.agent, "fetch_pages", lambda path, req: {2: "Storage 2-8 C."})

    b_think = NS(type="thinking", thinking="internal chain", signature="sig_1")
    b_redact = NS(type="redacted_thinking", data="enc_abc")
    b_text = NS(type="text", text="Let me fetch page 2.")
    b_tool = NS(type="tool_use", name="fetch_pages", input={"pages": "2"}, id="tu_1")
    turn1 = NS(content=[b_think, b_redact, b_text, b_tool], stop_reason="tool_use", model="claude-sonnet-5-5", usage=_usage())
    turn2 = NS(content=[NS(type="text", text="Storage 2-8 C [p. 2].")], stop_reason="end_turn", model="claude-sonnet-5-5", usage=_usage())

    seen_turn2_messages = []

    class SnapshotSDK:
        def __init__(self):
            self.responses = [turn1, turn2]
            self.messages = NS(create=self.create)

        def create(self, **kw):
            if len(kw["messages"]) > 1:
                seen_turn2_messages.append(copy.deepcopy(kw["messages"]))
            return self.responses.pop(0)

    sdk = SnapshotSDK()
    ans, _, _ = mods.agent.run_agent(sdk, sess, "Storage?")
    assert ans == "Storage 2-8 C [p. 2]."
    assert len(seen_turn2_messages) == 1
    msgs = seen_turn2_messages[0]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[1]["content"] == [b_think, b_redact, b_text, b_tool]


def test_nonstreaming_max_tokens_32000_with_explicit_timeout_600_allowed_by_real_sdk(mods):
    captured = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured.append({"headers": dict(request.headers), "body": body})
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5-5",
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "content": [{"type": "text", "text": "The guideline does not state this."}],
                "usage": {"input_tokens": 12, "output_tokens": 8},
            },
        )

    s = mods.common.Settings()
    assert s.agent_max_tokens == 32000 and s.agent_timeout_s == 600.0
    assert s.verifier_max_tokens == 16000 and s.verifier_timeout_s == 180.0
    assert s.tree_reserve_max_tokens == 32000 and s.judge_timeout_s == 180.0

    sdk = anthropic.Anthropic(
        api_key="test-key-not-real",
        max_retries=0,
        timeout=s.agent_timeout_s,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    resp = sdk.messages.create(
        model=s.agent_model,
        max_tokens=s.agent_max_tokens,
        timeout=s.agent_timeout_s,
        output_config={"effort": s.answer_effort},
        messages=[{"role": "user", "content": "Question: test"}],
    )
    assert resp.stop_reason == "end_turn"
    assert len(captured) == 1
    assert captured[0]["headers"]["anthropic-version"] == "2023-06-01"
    assert captured[0]["body"]["max_tokens"] == 32000
    assert captured[0]["body"]["output_config"] == {"effort": "medium"}


def test_usage_logger_19_fields_effort_and_thinking_tokens(mods, tmp_path):
    assert len(USAGE_FIELDS) == 19
    assert USAGE_FIELDS[-2:] == ("effort", "thinking_tokens")

    ledger = CostLedger(tmp_path / "ledger.jsonl")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    sdk = _ScriptedSDK([
        NS(model="claude-sonnet-5-5", stop_reason="end_turn", content=[NS(type="text", text="A.")], usage=_usage(50, 20, cr=0, cw=100, thinking_tokens=17)),
        NS(model="claude-sonnet-5-5", stop_reason="end_turn", content=[NS(type="text", text="B.")], usage=_usage(10, 5, cr=100, cw=0, thinking_tokens=None)),
    ])
    wrapped = mods.client.AnthropicRetryClient(sdk, ledger=ledger, usage_log=usage_log, lane="answer", timeout_s=600.0)
    wrapped.messages.create(
        model="claude-sonnet-5-5",
        max_tokens=32000,
        output_config={"effort": "medium"},
        system=[{"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "q1"}],
    )
    wrapped.messages.create(
        model="claude-sonnet-5-5",
        max_tokens=32000,
        output_config={"effort": "high"},
        system=[{"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "q2"}],
    )
    rows = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 2
    assert tuple(rows[0].keys()) == USAGE_FIELDS
    assert rows[0]["effort"] == "medium" and rows[0]["thinking_tokens"] == 17
    assert rows[0]["cache_creation_input_tokens"] == 100 and rows[0]["cache_read_input_tokens"] == 0
    assert rows[1]["effort"] == "high" and rows[1]["thinking_tokens"] is None
    assert rows[1]["cache_read_input_tokens"] == 100 and rows[1]["cache_creation_input_tokens"] == 0


def test_count_tokens_logs_one_line_cost_zero_no_reserve_and_precount_requests(mods, tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    pdf_path = tmp_path / "fake.pdf"
    with pymupdf.open() as doc:
        for i in range(1, 5):
            page = doc.new_page()
            if i != 3:
                page.insert_text((72, 72), f"Synthetic content for page {i}.")
        doc.save(pdf_path)

    tree_path = tmp_path / "tree.json"
    tree_path.write_text(
        json.dumps(
            {
                "doc_description": "Synthetic guideline",
                "structure": [{"node_id": "0001", "title": "Intro", "start_index": 1, "end_index": 4, "summary": "Covers pages 1 to 4."}],
            }
        ),
        encoding="utf-8",
    )

    s = mods.common.Settings()
    wd_req = precount.build_wd_precount_request(pdf_path, "What is on page 1?", s=s)
    pi_req = precount.build_pi_first_precount_request(tree_path, "What is on page 1?", s=s, page_count_val=4)

    assert "max_tokens" not in wd_req and "max_tokens" not in pi_req
    assert len(wd_req["system"]) == 2
    assert wd_req["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert "=== PDF page 3 ===\n\n\n=== PDF page 4 ===" in wd_req["system"][1]["text"]
    assert pi_req["tool_choice"] == {"type": "auto"}
    assert pi_req["output_config"] == {"effort": "medium"}

    ledger_path = tmp_path / "ledger.jsonl"
    usage_path = tmp_path / "usage.jsonl"
    sdk = _ScriptedSDK([NS(input_tokens=1234), NS(input_tokens=456)])
    wrapped = mods.client.AnthropicRetryClient(
        sdk,
        ledger=CostLedger(ledger_path),
        usage_log=UsageLogger(usage_path),
        lane="count_tokens",
        timeout_s=s.agent_timeout_s,
    )
    out = precount.count_precount_requests(wrapped, wd_req, pi_req)
    assert out == {"wd_input_tokens": 1234, "pi_first_input_tokens": 456}
    assert not ledger_path.exists()

    rows = [json.loads(line) for line in usage_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 2
    assert all(r["lane"] == "count_tokens" and r["cost_usd"] == 0.0 and r["input_tokens"] in (1234, 456) for r in rows)
    assert all(r["effort"] == "medium" and r["thinking_tokens"] is None for r in rows)


def test_assistant_prefill_rejected_before_any_anthropic_call(mods, tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    usage_path = tmp_path / "usage.jsonl"
    sdk = _ScriptedSDK([NS(model="m", stop_reason="end_turn", content=[NS(type="text", text="ok")], usage=_usage())])
    wrapped = mods.client.AnthropicRetryClient(
        sdk,
        ledger=CostLedger(ledger_path),
        usage_log=UsageLogger(usage_path),
        lane="agent",
        timeout_s=600.0,
    )

    prefill_msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "partial"}]
    with pytest.raises(ValueError, match="role='user'"):
        wrapped.messages.create(model="claude-sonnet-5-5", max_tokens=100, messages=prefill_msgs)
    with pytest.raises(ValueError, match="role='user'"):
        wrapped.messages.count_tokens(model="claude-sonnet-5-5", messages=prefill_msgs)
    with pytest.raises(ValueError, match="role='user'"):
        wrapped.messages.create(model="claude-sonnet-5-5", max_tokens=100, messages=[])

    rec = NS(title="GTP", version="2019", page_count=5, doc_description="", doc_id="g@1", pdf_path="x.pdf")
    sess = mods.agent.Session(rec=rec, tree={"structure": []}, nodes={}, s=mods.common.Settings())
    with pytest.raises(ValueError, match="role='user'"):
        mods.agent.run_agent(wrapped, sess, "q", prior=prefill_msgs)

    assert sdk.create_calls == []
    assert sdk.count_calls == []
    assert not ledger_path.exists()
    assert not usage_path.exists()


def test_fresh_subprocess_import_precount_from_repo_root():
    plain = subprocess.run(
        [sys.executable, "-c", "import eval.s11.precount"],
        cwd=ROOT,
        env={**os.environ, "REGCLAW_STUB": "1", "LANGFUSE_ENABLED": "false"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert plain.returncode == 0, plain.stderr

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
        import eval.s11.precount
        assert hasattr(eval.s11.precount, "count_precount_requests")
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
    assert guarded.returncode == 0, guarded.stderr
