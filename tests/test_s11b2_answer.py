"""Offline Part D tests for answer_rules_v1.txt, refusal_detected, shared check/repair, HY/WD/PI requests, and result records."""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace as NS

import pytest

import _netguard
from eval.s11 import answer
from eval.s11.ledger import CostLedger, UsageLogger, WriteOnceError

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_RULES_SHA256 = "57681cc4ace83407a4dbc26c6cda08f3409bfebdf5cf449f093e8cb015ec3875"
EXPECTED_RULES_BYTES = 848


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("REGCLAW_STUB", "1")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    _netguard.block_network(monkeypatch, "s11b2 answer tests forbid network")


@pytest.fixture
def mods(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "pageindex"))
    return NS(
        common=importlib.import_module("pi_common"),
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


def _text(t, stop="end_turn", model="claude-sonnet-5-5", usage=None):
    return NS(
        content=[NS(type="text", text=t)] if t else [],
        stop_reason=stop,
        model=model,
        usage=usage or _usage(),
    )


def _tool(name, args, tid="t1", model="claude-sonnet-5-5", usage=None):
    return NS(
        content=[NS(type="tool_use", name=name, input=args, id=tid)],
        stop_reason="tool_use",
        model=model,
        usage=usage or _usage(),
    )


def _verdicts(*items, stop="tool_use", model="claude-haiku-5-5", usage=None):
    return NS(
        content=[NS(type="tool_use", input={"verdicts": list(items)})],
        stop_reason=stop,
        model=model,
        usage=usage or _usage(15, 5),
    )


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


def _patch_pi_io(monkeypatch, agent_mod, *, pages=None):
    rec = NS(
        title="GTP Guideline",
        version="2019",
        page_count=116,
        doc_description="Gene therapy guideline",
        doc_id="gtp@2019",
        pdf_path="gtp.pdf",
        tree_path="tree.json",
    )
    page_map = pages or {2: "Storage shall be at 2-8 C."}
    monkeypatch.setattr(agent_mod, "resolve_edition", lambda *a: rec)
    monkeypatch.setattr(agent_mod, "load_manifest", lambda s: None)
    monkeypatch.setattr(
        agent_mod,
        "load_tree",
        lambda p: {"structure": [{"node_id": "0001", "title": "Scope", "start_index": 1, "end_index": 2}]},
    )
    monkeypatch.setattr(agent_mod, "fetch_pages", lambda path, req: {p: page_map[p] for p in req if p in page_map})
    return rec


def test_answer_rules_file_sha_length_lf_and_no_trailing_spaces():
    raw_bytes = answer.RULES_PATH.read_bytes()
    assert len(raw_bytes) == EXPECTED_RULES_BYTES
    assert b"\r" not in raw_bytes
    assert raw_bytes.endswith(b"\n") and not raw_bytes.endswith(b"\n\n")
    assert hashlib.sha256(raw_bytes).hexdigest() == EXPECTED_RULES_SHA256

    loaded = answer.load_answer_rules()
    assert len(loaded.encode("utf-8")) == EXPECTED_RULES_BYTES - 1
    assert not loaded.endswith("\n")
    for line in loaded.splitlines():
        assert line == line.rstrip(" \t"), f"trailing whitespace on line: {line!r}"


def test_load_answer_rules_normalizes_crlf_copy(tmp_path):
    raw_bytes = answer.RULES_PATH.read_bytes()
    crlf_copy = tmp_path / "answer_rules_crlf.txt"
    crlf_copy.write_bytes(raw_bytes.replace(b"\n", b"\r\n"))
    assert answer.load_answer_rules(crlf_copy) == answer.load_answer_rules()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("The guideline does not state this.", True),
        ("**The guideline does not state this.**", True),
        ("The guideline does not state this. No timeline is given for phase III.", True),
        ("  *\"`the   guideline\n  does NOT  state this.`\"*  ", True),
        ("**The guideline does not state this.** Additional details on p. 4 are unrelated.", True),
        ("Storage must be at 2-8 C [p. 2].", False),
        ("", False),
        ("   ", False),
    ],
)
def test_refusal_detected_cases(text, expected):
    assert answer.refusal_detected(text) is expected


def test_refusal_skips_haiku_check_across_pi_hy_wd(mods, monkeypatch, tmp_path):
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")

    # 1) HY refusal skips verifier completely
    hy_sdk = _ScriptedAnthropic(_text("**The guideline does not state this.**"))
    r_hy = answer.run_hy_answer("gtp-13", "Q?", {2: "Unrelated text."}, s=mods.common.Settings(), ledger=ledger, usage_log=usage_log, sdk_client=hy_sdk)
    assert len(hy_sdk.requests) == 1
    assert r_hy["refusal"] is True
    assert r_hy["check_status"] == "check_skipped_refusal"
    assert r_hy["verifier_model_returned"] is None
    assert r_hy["status"] == "ok"

    # 2) WD refusal skips verifier completely
    wd_sdk = _ScriptedAnthropic(_text("The guideline does not state this."))
    r_wd = answer.run_wd_answer("gtp-13", "Q?", {i: "" for i in range(1, 117)}, s=mods.common.Settings(), ledger=ledger, usage_log=usage_log, sdk_client=wd_sdk)
    assert len(wd_sdk.requests) == 1
    assert r_wd["refusal"] is True
    assert r_wd["check_status"] == "check_skipped_refusal"
    assert r_wd["verifier_model_returned"] is None
    assert r_wd["status"] == "ok"

    # 3) PI refusal also skips verifier (Answer 5)
    _patch_pi_io(monkeypatch, mods.agent)
    pi_sdk = _ScriptedAnthropic(
        _tool("fetch_pages", {"pages": "2"}),
        _text("The guideline does not state this."),
    )
    monkeypatch.setattr(mods.agent.anthropic, "Anthropic", lambda **kw: pi_sdk)
    r_pi = answer.run_pi_answer("gtp-13", "Q?", family="gtp", s=mods.common.Settings(), ledger=ledger, usage_log=usage_log)
    assert len(pi_sdk.requests) == 2
    assert r_pi["refusal"] is True
    assert r_pi["check_status"] == "check_skipped_refusal"
    assert r_pi["verifier_model_returned"] is None
    assert r_pi["status"] == "ok"

    # 4) Refusal after a repair round also skips the second Haiku check
    hy_repair_refusal = _ScriptedAnthropic(
        _text("Unsupported claim [p. 2]."),
        _verdicts({"claim_id": 1, "verdict": "unsupported", "evidence": "", "reason": "not on page"}),
        _text("The guideline does not state this."),
    )
    r_rep = answer.run_hy_answer("gtp-14", "Q?", {2: "Unrelated."}, s=mods.common.Settings(), ledger=ledger, usage_log=usage_log, sdk_client=hy_repair_refusal)
    assert len(hy_repair_refusal.requests) == 3
    assert r_rep["repaired"] is True and r_rep["refusal"] is True
    assert r_rep["check_status"] == "check_skipped_refusal"
    assert r_rep["verifier_model_returned"] == "claude-haiku-5-5"


def test_hy_and_wd_request_shapes_and_pi_prompt_order(mods, monkeypatch):
    s = mods.common.Settings()
    rules = answer.load_answer_rules()

    # HY: single cached rules block; user holds ascending page blocks (stripped, empty keeps header) + "\n\nQuestion: "
    hy_req = answer.build_hy_request("What dose?", {5: "  Page five text  \n", 3: "   ", 1: " Page one "}, s=s)
    assert hy_req["max_tokens"] == 32000 and hy_req["timeout"] == 600.0
    assert hy_req["system"] == [{"type": "text", "text": rules, "cache_control": {"type": "ephemeral"}}]
    assert hy_req["messages"] == [
        {
            "role": "user",
            "content": "=== PDF page 1 ===\nPage one\n\n=== PDF page 3 ===\n\n\n=== PDF page 5 ===\nPage five text\n\nQuestion: What dose?",
        }
    ]

    # WD: 2 system blocks (rules + all 116 page blocks with cache_control), user is "Question: " + q
    all_pages = {p: (f"Text on {p}" if p in (1, 116) else "") for p in range(1, 117)}
    wd_req = answer.build_wd_request("What scope?", all_pages, s=s)
    assert wd_req["max_tokens"] == 32000 and wd_req["timeout"] == 600.0
    assert len(wd_req["system"]) == 2
    assert wd_req["system"][0] == {"type": "text", "text": rules}
    assert wd_req["system"][1]["cache_control"] == {"type": "ephemeral"}
    doc_text = wd_req["system"][1]["text"]
    headers = [line for line in doc_text.splitlines() if line.startswith("=== PDF page ")]
    assert len(headers) == 116
    assert headers[0] == "=== PDF page 1 ===" and headers[-1] == "=== PDF page 116 ==="
    assert "=== PDF page 2 ===\n\n\n=== PDF page 3 ===\n" in doc_text
    assert wd_req["messages"] == [{"role": "user", "content": "Question: What scope?"}]

    # PI: rules + blank line + nav lines 75-81 + blank line + outline; removed lines 73/82/83 absent; fetch_pages uses .strip()
    rec = _patch_pi_io(monkeypatch, mods.agent, pages={5: "  Five  ", 2: "\n  Two\n "})
    sess = mods.agent.Session(rec=rec, tree={"structure": [{"node_id": "0001", "title": "Scope", "start_index": 1, "end_index": 2}]}, nodes={}, s=s)
    pi_sdk = _ScriptedAnthropic(
        _tool("fetch_pages", {"pages": "5,2"}),
        _text("Fact [p. 2]."),
    )
    mods.agent.run_agent(pi_sdk, sess, "Scope?")
    sys_text = pi_sdk.requests[0]["system"][0]["text"]
    assert sys_text.startswith(rules + "\n\nDocument: GTP Guideline")
    assert "\nHow to work:\n1. Read the outline below" in sys_text
    assert "\n\nOutline (node_id, title, PDF page span, summary):\n[0001] Scope" in sys_text
    for removed in (
        "using ONLY its text",
        "4. Answer concisely.",
        "5. If the fetched text does not answer the question",
        "Never use outside knowledge to fill gaps",
    ):
        assert removed not in sys_text
    assert pi_sdk.requests[1]["messages"][-1]["role"] == "user"
    turn2_user_content = pi_sdk.requests[1]["messages"][-1]["content"]
    assert turn2_user_content[0]["content"] == "=== PDF page 2 ===\nTwo\n\n=== PDF page 5 ===\nFive"


def test_max_tokens_and_model_refusal_fail_immediately_without_retry(mods, monkeypatch, tmp_path):
    s = mods.common.Settings()
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")

    # 1) HY Sonnet max_tokens -> failed:max_tokens, 0 retries, 0 verifier calls
    hy_max = _ScriptedAnthropic(_text("Partial cut off", stop="max_tokens"))
    r_max = answer.run_hy_answer("q1", "Q?", {2: "Text"}, s=s, ledger=ledger, usage_log=usage_log, sdk_client=hy_max)
    assert len(hy_max.requests) == 1
    assert r_max["status"] == "failed:max_tokens" and r_max["stop_reason"] == "max_tokens" and r_max["answer"] == ""
    assert r_max["check_status"] == "check_skipped_failed" and r_max["verifier_model_returned"] is None

    # 2) WD Sonnet refusal -> status "model_refusal" in both record and usage.jsonl
    wd_ref = _ScriptedAnthropic(_text("Cannot comply", stop="refusal", usage=_usage(30, 5)))
    r_ref = answer.run_wd_answer("q2", "Q?", {2: "Text"}, s=s, ledger=ledger, usage_log=usage_log, sdk_client=wd_ref)
    assert len(wd_ref.requests) == 1
    assert r_ref["status"] == "model_refusal" and r_ref["stop_reason"] == "refusal" and r_ref["answer"] == ""
    assert r_ref["refusal"] is False

    # 3) PI Sonnet refusal -> status "model_refusal", 0 retries
    _patch_pi_io(monkeypatch, mods.agent)
    pi_ref = _ScriptedAnthropic(_text("Refused", stop="refusal"))
    monkeypatch.setattr(mods.agent.anthropic, "Anthropic", lambda **kw: pi_ref)
    r_pi_ref = answer.run_pi_answer("q3", "Q?", family="gtp", s=s, ledger=ledger, usage_log=usage_log)
    assert len(pi_ref.requests) == 1
    assert r_pi_ref["status"] == "model_refusal" and r_pi_ref["stop_reason"] == "refusal" and r_pi_ref["answer"] == ""

    # 4) Haiku verifier stop_reason="max_tokens" -> _VerifierStopError -> failed:verifier_max_tokens, no retry, unchecked answer NOT kept
    with pytest.raises(mods.agent._VerifierStopError) as exc_info:
        mods.agent.verify_with_usage(
            _ScriptedAnthropic(_verdicts({"claim_id": 1, "verdict": "supported", "evidence": "Text", "reason": ""}, stop="max_tokens")),
            s,
            "Claim [p. 2].",
            {2: "Text"},
        )
    assert exc_info.value.status == "failed:verifier_max_tokens"

    hy_ver_max = _ScriptedAnthropic(
        _text("Claim [p. 2]."),
        _verdicts({"claim_id": 1, "verdict": "supported", "evidence": "Text", "reason": ""}, stop="max_tokens"),
    )
    r_vmax = answer.run_hy_answer("q4", "Q?", {2: "Text"}, s=s, ledger=ledger, usage_log=usage_log, sdk_client=hy_ver_max)
    assert len(hy_ver_max.requests) == 2
    assert r_vmax["status"] == "failed:verifier_max_tokens"
    assert r_vmax["answer"] == "" and r_vmax["answer_pre_repair"] == "Claim [p. 2]."
    assert r_vmax["check_status"] == "verifier_max_tokens"

    # 5) Haiku verifier stop_reason="refusal" during post-repair check -> model_refusal, no unchecked answer kept
    hy_ver_ref = _ScriptedAnthropic(
        _text("Bad claim [p. 2]."),
        _verdicts({"claim_id": 1, "verdict": "unsupported", "evidence": "", "reason": "bad"}),
        _text("Repaired claim [p. 2]."),
        _verdicts(stop="refusal"),
    )
    r_vref = answer.run_hy_answer("q5", "Q?", {2: "Text"}, s=s, ledger=ledger, usage_log=usage_log, sdk_client=hy_ver_ref)
    assert len(hy_ver_ref.requests) == 4
    assert r_vref["status"] == "model_refusal" and r_vref["repaired"] is True and r_vref["answer"] == ""
    assert r_vref["answer_pre_repair"] == "Bad claim [p. 2]."

    usage_rows = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    statuses = [row["status"] for row in usage_rows]
    assert "model_refusal" in statuses


def test_shared_check_repair_drop_thinking_sum_and_exact_16_key_write_once_record(mods, tmp_path):
    s = mods.common.Settings()
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    pages = {2: "Storage shall be at 2-8 C."}
    out_dir = tmp_path / "run_hy"

    # Check mixed thinking_tokens: call 1 reports 120, call 2 (verifier) None, call 3 (repair) 80, call 4 (verifier) 15
    sdk = _ScriptedAnthropic(
        _text("Wrong initial claim [p. 2].", model="claude-sonnet-5-5-a", usage=_usage(100, 20, 0, 50, thinking_tokens=120)),
        _verdicts(
            {"claim_id": 1, "verdict": "unsupported", "evidence": "", "reason": "not stated"},
            model="claude-haiku-5-5-v1",
            usage=_usage(40, 10, thinking_tokens=None),
        ),
        _text("Storage shall be at 2-8 C [p. 2]. Extra unsupported claim [p. 2].", model="claude-sonnet-5-5-b", usage=_usage(30, 15, 90, 0, thinking_tokens=80)),
        _verdicts(
            {"claim_id": 1, "verdict": "supported", "evidence": "Storage shall be at 2-8 C.", "reason": ""},
            {"claim_id": 2, "verdict": "unsupported", "evidence": "", "reason": "still wrong"},
            model="claude-haiku-5-5-v2",
            usage=_usage(45, 12, thinking_tokens=15),
        ),
    )
    rec = answer.run_hy_answer(
        "gtp-01",
        "What is the storage temperature?",
        pages,
        s=s,
        ledger=ledger,
        usage_log=usage_log,
        out_dir=out_dir,
        sdk_client=sdk,
    )

    expected_keys = (
        "system",
        "question_id",
        "pages_given",
        "answer_pre_repair",
        "answer",
        "repaired",
        "dropped_sentences",
        "refusal",
        "check_status",
        "stop_reason",
        "status",
        "model_returned",
        "verifier_model_returned",
        "effort",
        "usage",
        "latency_s",
    )
    assert tuple(rec.keys()) == expected_keys
    assert set(rec["usage"].keys()) == {"answer", "verifier", "total"}
    for sub in ("answer", "verifier", "total"):
        assert tuple(rec["usage"][sub].keys()) == ("input", "output", "cache_read", "cache_write", "thinking_tokens")

    assert rec["system"] == "HY" and rec["question_id"] == "gtp-01"
    assert rec["pages_given"] == [2]
    assert rec["answer_pre_repair"] == "Wrong initial claim [p. 2]."
    assert "Storage shall be at 2-8 C [p. 2]." in rec["answer"]
    assert "Extra unsupported claim [p. 2]." not in rec["answer"]
    assert rec["repaired"] is True and rec["dropped_sentences"] == ["Extra unsupported claim [p. 2]."]
    assert rec["refusal"] is False and rec["check_status"] == "checked"
    assert rec["stop_reason"] == "end_turn" and rec["status"] == "ok"
    assert rec["model_returned"] == "claude-sonnet-5-5-b"
    assert rec["verifier_model_returned"] == "claude-haiku-5-5-v2"
    assert rec["effort"] == "medium"
    assert rec["usage"]["answer"] == {"input": 130, "output": 35, "cache_read": 90, "cache_write": 50, "thinking_tokens": 200}
    assert rec["usage"]["verifier"] == {"input": 85, "output": 22, "cache_read": 0, "cache_write": 0, "thinking_tokens": 15}
    assert rec["usage"]["total"] == {"input": 215, "output": 57, "cache_read": 90, "cache_write": 50, "thinking_tokens": 215}

    saved = json.loads((out_dir / "gtp-01.json").read_text(encoding="utf-8"))
    assert saved == rec
    with pytest.raises(WriteOnceError):
        answer.run_hy_answer("gtp-01", "Q?", pages, s=s, ledger=ledger, usage_log=usage_log, out_dir=out_dir, sdk_client=_ScriptedAnthropic(_text("The guideline does not state this.")))

    # Absent thinking_tokens on all calls stays None (never silent 0)
    sdk_none = _ScriptedAnthropic(_text("The guideline does not state this.", usage=_usage(10, 5, thinking_tokens=None)))
    rec_none = answer.run_hy_answer("gtp-02", "Q?", pages, s=s, ledger=ledger, usage_log=usage_log, sdk_client=sdk_none)
    assert rec_none["usage"]["answer"]["thinking_tokens"] is None
    assert rec_none["usage"]["verifier"]["thinking_tokens"] is None
    assert rec_none["usage"]["total"]["thinking_tokens"] is None


def test_fresh_subprocess_import_answer_from_repo_root():
    plain = subprocess.run(
        [sys.executable, "-c", "import eval.s11.answer"],
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
        import eval.s11.answer
        assert hasattr(eval.s11.answer, "run_hy_answer")
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
