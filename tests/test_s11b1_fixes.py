"""Offline tests for S11b1-08 Part II fixes (Gaps 1, 3, 8, 9, 11, and timeouts)."""
import contextvars
import importlib
import importlib.util
import json
import logging
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS

import pytest

import _netguard

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-key-not-real"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    _netguard.block_network(monkeypatch, "s11b1 fixes tests forbid network")


@pytest.fixture
def mods(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "pageindex"))
    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    return NS(
        common=importlib.import_module("pi_common"),
        ingest=importlib.import_module("pi_ingest"),
        agent=importlib.import_module("pi_agent"),
        inspect_pdf=importlib.import_module("inspect_pdf"),
        adapter=importlib.import_module("pageindex_adapter"),
        eval_pi=importlib.import_module("eval_pageindex"),
    )


def _usage(inp=10, out=5, cr=0, cc=0):
    return NS(
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=cr,
        cache_creation_input_tokens=cc,
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


def _verdicts(*items, model="claude-haiku-5-5", usage=None):
    return NS(
        content=[NS(type="tool_use", input={"verdicts": list(items)})],
        model=model,
        usage=usage or _usage(8, 3),
    )


class _ScriptedAnthropic:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = NS(create=self.create)

    def create(self, **kw):
        self.requests.append(kw)
        return self.responses.pop(0)


def _patch_agent_io(monkeypatch, agent_mod, *, pages=None):
    rec = NS(
        title="GTP Guideline",
        version="2019",
        page_count=116,
        doc_description="GTP doc",
        doc_id="gtp@2019",
        pdf_path="gtp.pdf",
        tree_path="tree.json",
    )
    page_map = pages or {2: "Storage shall be at 2-8 C."}
    monkeypatch.setattr(agent_mod, "resolve_edition", lambda *a: rec)
    monkeypatch.setattr(agent_mod, "load_manifest", lambda s: None)
    monkeypatch.setattr(agent_mod, "load_tree", lambda p: {"structure": [{"node_id": "0001", "title": "Intro", "start_index": 1, "end_index": 2}]})
    monkeypatch.setattr(agent_mod, "fetch_pages", lambda path, req: {p: page_map[p] for p in req if p in page_map})
    return rec


@pytest.fixture
def fake_ragas(monkeypatch):
    rec = NS(llm=None, emb=None, calls=[], script=lambda name, kw: 0.9)
    modules = {
        n: ModuleType(n)
        for n in (
            "ragas",
            "ragas.llms",
            "ragas.embeddings",
            "ragas.embeddings.base",
            "ragas.metrics",
            "ragas.metrics.collections",
        )
    }

    class Metric:
        def __init__(self, name):
            self.name = name

        async def ascore(self, **kw):
            rec.calls.append((self.name, kw))
            return NS(value=rec.script(self.name, kw))

    modules["ragas.llms"].llm_factory = lambda model, **kw: rec.__setattr__("llm", (model, kw)) or NS(model=model)
    modules["ragas.embeddings.base"].embedding_factory = (
        lambda provider, **kw: rec.__setattr__("emb", (provider, kw)) or NS(provider=provider)
    )
    for cls in ("Faithfulness", "AnswerRelevancy", "AnswerCorrectness"):
        setattr(modules["ragas.metrics.collections"], cls, lambda cls=cls, **kw: Metric(cls))
    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return rec


def test_verifier_model_is_claude_haiku_5_5(mods):
    assert mods.common.Settings().verifier_model == "claude-haiku-5-5"
    assert mods.common.load_settings().verifier_model == "claude-haiku-5-5"


def test_timeout_and_max_token_settings_defaults_and_validation(mods):
    assert mods.common.AGENT_TIMEOUT_S == 600.0
    assert mods.common.VERIFIER_TIMEOUT_S == 180.0
    assert mods.common.JUDGE_TIMEOUT_S == 180.0
    for s in (mods.common.Settings(), mods.common.load_settings()):
        assert s.agent_timeout_s == 600.0
        assert s.verifier_timeout_s == 180.0
        assert s.judge_timeout_s == 180.0
        assert s.agent_max_tokens == 32000
        assert s.verifier_max_tokens == 16000

    for field in ("agent_timeout_s", "verifier_timeout_s", "judge_timeout_s"):
        with pytest.raises(ValueError):
            mods.common.Settings(**{field: 0})
        with pytest.raises(ValueError):
            mods.common.Settings(**{field: float("inf")})
    for field in ("agent_max_tokens", "verifier_max_tokens"):
        with pytest.raises(ValueError):
            mods.common.Settings(**{field: 0})


def test_no_temperature_top_p_or_top_k_sent_by_run_agent_or_verify(mods, monkeypatch, tmp_path):
    from eval.s11.ledger import CostLedger, UsageLogger

    _patch_agent_io(monkeypatch, mods.agent)
    bad_verdict = {"claim_id": 1, "verdict": "unsupported", "evidence": "", "reason": "no"}
    ok_verdict = {"claim_id": 1, "verdict": "supported", "evidence": "Storage shall be at 2-8 C.", "reason": ""}
    client = _ScriptedAnthropic(
        _tool("fetch_pages", {"pages": "2"}),
        _text("Unverified claim [p. 2]."),
        _verdicts(bad_verdict),
        _text("Storage shall be at 2-8 C [p. 2]."),
        _verdicts(ok_verdict),
    )
    monkeypatch.setattr(mods.agent.anthropic, "Anthropic", lambda **kw: client)
    r = mods.agent.answer_question(
        "Storage?",
        "gtp",
        s=mods.common.Settings(),
        ledger=CostLedger(tmp_path / "ledger.jsonl"),
        usage_log=UsageLogger(tmp_path / "usage.jsonl"),
    )
    assert r["status"] == "ok" and len(client.requests) == 5
    for req in client.requests:
        for forbidden in ("temperature", "top_p", "top_k"):
            assert forbidden not in req
    assert client.requests[0]["max_tokens"] == 32000
    assert client.requests[2]["max_tokens"] == 16000


def test_pi_agent_passes_max_retries_zero_timeouts_and_logs(mods, monkeypatch, caplog, tmp_path):
    from eval.s11.ledger import CostLedger, UsageLogger

    _patch_agent_io(monkeypatch, mods.agent)
    client_inits = []
    client = _ScriptedAnthropic(
        _tool("fetch_pages", {"pages": "2"}),
        _text("Storage shall be at 2-8 C [p. 2]."),
        _verdicts({"claim_id": 1, "verdict": "supported", "evidence": "Storage shall be at 2-8 C.", "reason": ""}),
    )

    def fake_anthropic(**kw):
        client_inits.append(kw)
        return client

    monkeypatch.setattr(mods.agent.anthropic, "Anthropic", fake_anthropic)
    with caplog.at_level(logging.INFO, logger="pageindex.agent"):
        r = mods.agent.answer_question(
            "Storage?",
            "gtp",
            s=mods.common.Settings(),
            ledger=CostLedger(tmp_path / "ledger.jsonl"),
            usage_log=UsageLogger(tmp_path / "usage.jsonl"),
        )

    assert client_inits == [
        {"max_retries": 0, "timeout": 600.0},
        {"max_retries": 0, "timeout": 180.0},
    ]
    assert client.requests[0]["timeout"] == 600.0
    assert client.requests[2]["timeout"] == 180.0
    assert r["timeouts"] == {"agent_timeout_s": 600.0, "verifier_timeout_s": 180.0}
    assert "agent_timeout_s=600.0" in caplog.text and "verifier_timeout_s=180.0" in caplog.text


def test_eval_pageindex_passes_judge_timeout_and_logs_manifest(mods, fake_ragas, monkeypatch, tmp_path, capsys):
    import httpx

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://res.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", SECRET)
    made = []
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kw: made.append(kw) or NS(kind="async"))

    golden = tmp_path / "g.json"
    golden.write_text(json.dumps([{"id": "q1", "question": "Q?", "reference": "R", "gold_pages": [12]}]), encoding="utf-8")
    cfg = tmp_path / "s.yaml"
    cfg.write_text("judge_model: gpt-4.1-mini\njudge_repeats: 1\njudge_timeout_s: 222.0\n", encoding="utf-8")
    out = tmp_path / "out.json"

    code = mods.eval_pi.main([
        "--golden", str(golden),
        "--dry-run",
        "--runs", "1",
        "--settings", str(cfg),
        "--usage-log", str(tmp_path / "usage.jsonl"),
        "--out", str(out),
    ])
    assert code == 0
    assert len(made) == 1
    az_kw = dict(made[0])
    assert isinstance(az_kw.pop("http_client"), httpx.AsyncClient)
    assert az_kw == {"base_url": "https://res.openai.azure.com/openai/v1/", "api_key": SECRET,
                     "max_retries": 0, "timeout": 222.0}
    manifest = json.loads((tmp_path / "out.manifest.json").read_text(encoding="utf-8"))
    assert manifest["ragas"]["judge_timeout_s"] == 222.0
    assert "timeout=222.0s" in capsys.readouterr().err


def test_run_agent_sends_ephemeral_cache_control_on_system(mods):
    rec = NS(title="Doc", version="v1", page_count=5, doc_description="desc", doc_id="gtp@2019", pdf_path="x.pdf")
    sess = mods.agent.Session(
        rec=rec,
        tree={"structure": [{"node_id": "0001", "title": "Scope", "start_index": 1, "end_index": 2}]},
        nodes={},
        s=mods.common.Settings(),
    )
    client = _ScriptedAnthropic(_text("Answer [p. 1]."))
    answer, _, _ = mods.agent.run_agent(client, sess, "What is the scope?")
    assert answer == "Answer [p. 1]."
    sys_blocks = client.requests[0]["system"]
    assert isinstance(sys_blocks, list) and len(sys_blocks) == 1
    assert sys_blocks[0]["type"] == "text"
    assert sys_blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "[0001] Scope" in sys_blocks[0]["text"]


def test_usage_accumulates_cache_and_verifier_tokens_and_models(mods, monkeypatch, tmp_path):
    from eval.s11.ledger import CostLedger, UsageLogger

    ledger = CostLedger(tmp_path / "ledger.jsonl")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    _patch_agent_io(monkeypatch, mods.agent)
    client = _ScriptedAnthropic(
        _tool("fetch_pages", {"pages": "2"}, model="claude-sonnet-5-5-nav", usage=_usage(100, 20, 0, 50)),
        _text("Wrong claim [p. 2].", model="claude-sonnet-5-5-nav", usage=_usage(30, 10, 80, 0)),
        _verdicts(
            {"claim_id": 1, "verdict": "unsupported", "evidence": "", "reason": "bad"},
            model="claude-haiku-5-5-ver",
            usage=_usage(40, 15, 0, 0),
        ),
        _text("Storage shall be at 2-8 C [p. 2].", model="claude-sonnet-5-5-nav", usage=_usage(25, 12, 90, 0)),
        _verdicts(
            {"claim_id": 1, "verdict": "supported", "evidence": "Storage shall be at 2-8 C.", "reason": ""},
            model="claude-haiku-5-5-ver",
            usage=_usage(45, 14, 0, 0),
        ),
    )
    monkeypatch.setattr(mods.agent.anthropic, "Anthropic", lambda **kw: client)
    r = mods.agent.answer_question("Storage?", "gtp", s=mods.common.Settings(), ledger=ledger, usage_log=usage_log)
    u = r["usage"]
    expected_agent = {
        "input_tokens": 155,
        "output_tokens": 42,
        "cache_read_input_tokens": 170,
        "cache_creation_input_tokens": 50,
        "calls": 3,
        "thinking_tokens": None,
    }
    expected_verifier = {
        "input_tokens": 85,
        "output_tokens": 29,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "calls": 2,
        "thinking_tokens": None,
    }
    expected_total = {
        "input_tokens": expected_agent["input_tokens"] + expected_verifier["input_tokens"],
        "output_tokens": expected_agent["output_tokens"] + expected_verifier["output_tokens"],
        "cache_read_input_tokens": expected_agent["cache_read_input_tokens"] + expected_verifier["cache_read_input_tokens"],
        "cache_creation_input_tokens": expected_agent["cache_creation_input_tokens"] + expected_verifier["cache_creation_input_tokens"],
        "calls": expected_agent["calls"] + expected_verifier["calls"],
        "thinking_tokens": None,
    }
    assert u["agent"] == expected_agent
    assert u["verifier"] == expected_verifier
    assert u["total"] == expected_total
    assert u["input_tokens"] == expected_agent["input_tokens"]
    assert u["output_tokens"] == expected_agent["output_tokens"]
    assert u["turns"] == 3
    assert r["models"] == {"agent": ["claude-sonnet-5-5-nav"], "verifier": ["claude-haiku-5-5-ver"]}


def test_verify_guards_missing_tool_use_and_empty_evidence(mods):
    s = mods.common.Settings()
    no_tool_client = _ScriptedAnthropic(_text("I forgot to call report_verdicts."))
    out_missing = mods.agent.verify(no_tool_client, s, "Storage is cold [p. 2].", {2: "Storage is cold."})
    assert out_missing[0]["verdict"] == "unsupported"
    assert "no tool_use" in out_missing[0]["reason"]

    guard_client = _ScriptedAnthropic(
        _verdicts(
            {"claim_id": 1, "verdict": "supported", "evidence": "Storage is cold.", "reason": ""},
            {"claim_id": 2, "verdict": "supported", "evidence": "   ", "reason": ""},
            {"claim_id": 3, "verdict": "supported", "evidence": "Unmatched quote text here.", "reason": ""},
        )
    )
    ans = "No citation claim. Empty evidence claim [p. 2]. Wrong quote claim [p. 2]."
    out_guarded = mods.agent.verify(guard_client, s, ans, {2: "Storage is cold and dry."})
    assert out_guarded[0]["verdict"] == "needs_citation"
    assert out_guarded[1]["verdict"] == "partially_supported"
    assert "missing verbatim evidence" in out_guarded[1]["reason"]
    assert out_guarded[2]["verdict"] == "partially_supported"
    assert "not found verbatim" in out_guarded[2]["reason"]


def test_pre_post_repair_dropped_sentences_and_eval_scoring(mods, fake_ragas, monkeypatch, tmp_path):
    from eval.s11.ledger import CostLedger, UsageLogger

    ledger = CostLedger(tmp_path / "ledger.jsonl")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    _patch_agent_io(monkeypatch, mods.agent, pages={2: "Storage shall be at 2-8 C."})
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://res.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", SECRET)

    # 1) Clean pass: repaired=False, answer_pre_repair == answer, dropped_sentences == []
    clean_client = _ScriptedAnthropic(
        _tool("fetch_pages", {"pages": "2"}),
        _text("Storage shall be at 2-8 C [p. 2]."),
        _verdicts({"claim_id": 1, "verdict": "supported", "evidence": "Storage shall be at 2-8 C.", "reason": ""}),
    )
    monkeypatch.setattr(mods.agent.anthropic, "Anthropic", lambda **kw: clean_client)
    r_clean = mods.agent.answer_question("Storage?", "gtp", s=mods.common.Settings(), ledger=ledger, usage_log=usage_log)
    assert r_clean["repaired"] is False
    assert r_clean["answer_pre_repair"] == r_clean["answer"] == "Storage shall be at 2-8 C [p. 2]."
    assert r_clean["dropped_sentences"] == []

    # 2) Repair still fails on second claim -> dropped_sentences populated, pre_repair preserved
    drop_client = _ScriptedAnthropic(
        _tool("fetch_pages", {"pages": "2"}),
        _text("Initial bad claim [p. 9]."),
        _verdicts({"claim_id": 1, "verdict": "unsupported", "evidence": "", "reason": "bad"}),
        _text("Storage shall be at 2-8 C [p. 2]. Unverified extra [p. 2]."),
        _verdicts(
            {"claim_id": 1, "verdict": "supported", "evidence": "Storage shall be at 2-8 C.", "reason": ""},
            {"claim_id": 2, "verdict": "unsupported", "evidence": "", "reason": "still bad"},
        ),
    )
    monkeypatch.setattr(mods.agent.anthropic, "Anthropic", lambda **kw: drop_client)
    r_drop = mods.agent.answer_question("Storage?", "gtp", s=mods.common.Settings(), ledger=ledger, usage_log=usage_log)
    assert r_drop["repaired"] is True
    assert r_drop["answer_pre_repair"] == "Initial bad claim [p. 9]."
    assert r_drop["dropped_sentences"] == ["Unverified extra [p. 2]."]
    assert "Storage shall be at 2-8 C [p. 2]." in r_drop["answer"]
    assert "Unverified extra [p. 2]." not in r_drop["answer"]

    # 3) Repair fails with max_tokens -> answer == "", answer_pre_repair preserved
    fail_repair_client = _ScriptedAnthropic(
        _tool("fetch_pages", {"pages": "2"}),
        _text("Pre-repair text [p. 9]."),
        _verdicts({"claim_id": 1, "verdict": "unsupported", "evidence": "", "reason": "bad"}),
        _text("Partial", stop="max_tokens"),
    )
    monkeypatch.setattr(mods.agent.anthropic, "Anthropic", lambda **kw: fail_repair_client)
    r_fail = mods.agent.answer_question("Storage?", "gtp", s=mods.common.Settings(), ledger=ledger, usage_log=usage_log)
    assert r_fail["status"] == "failed:max_tokens"
    assert r_fail["repaired"] is True
    assert r_fail["answer"] == ""
    assert r_fail["answer_pre_repair"] == "Pre-repair text [p. 9]."
    assert r_fail["dropped_sentences"] == []

    # 4) eval_pageindex keeps final answer for Ragas scoring while saving pre/post fields
    metrics = mods.eval_pi.build_ragas("gpt-4.1-mini", client=NS(kind="fake"))
    eval_out = {
        "answer": "Final post-repair answer [p. 2].",
        "answer_pre_repair": "Pre-repair answer [p. 9].",
        "repaired": True,
        "dropped_sentences": ["Bad sentence [p. 9]."],
        "fetched_pages": [2],
        "page_text": {2: "Storage shall be at 2-8 C."},
        "cited_pages": [2],
        "status": "ok",
    }
    rows = mods.eval_pi.evaluate_item(
        {"id": "q1", "question": "Storage?", "reference": "2-8 C", "gold_pages": [2]},
        eval_out,
        metrics,
        judge_repeats=1,
        retrieved_mode="fetched",
        run=1,
        latency=0.1,
    )
    assert all(kw["response"] == "Final post-repair answer [p. 2]." for _, kw in fake_ragas.calls)
    assert rows[0]["answer"] == "Final post-repair answer [p. 2]."
    assert rows[0]["answer_pre_repair"] == "Pre-repair answer [p. 9]."
    assert rows[0]["repaired"] is True
    assert rows[0]["dropped_sentences"] == ["Bad sentence [p. 9]."]


def test_store_dir_resolves_relative_to_pageindex_regardless_of_cwd(mods, monkeypatch, tmp_path):
    expected = (ROOT / "pageindex" / "store").resolve()
    monkeypatch.chdir(tmp_path)
    assert mods.common.load_settings().store_dir == expected
    assert mods.common.Settings(store_dir="store").store_dir == expected
    assert mods.common.Settings(store_dir="./store").store_dir == expected


def test_parse_page_spec_rejects_zero_and_validates_positive_pages(mods):
    for bad in ("0", "0-3", "1,0", "3-0"):
        with pytest.raises(ValueError):
            mods.common.parse_page_spec(bad)
    assert mods.common.parse_page_spec("1, 3-5") == [1, 3, 4, 5]


def test_build_tree_pin_cli_settings_and_standalone_imports(mods, monkeypatch, tmp_path):
    # Loading pi_ingest / pi_agent directly without pageindex/ pre-added to sys.path works cleanly.
    monkeypatch.setattr(sys, "path", [p for p in sys.path if Path(p).resolve() != (ROOT / "pageindex").resolve()])
    for mod_name, rel in (("standalone_pi_ingest", "pageindex/pi_ingest.py"),
                          ("standalone_pi_agent", "pageindex/pi_agent.py")):
        spec = importlib.util.spec_from_file_location(mod_name, ROOT / rel)
        loaded = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, mod_name, loaded)
        spec.loader.exec_module(loaded)
        assert hasattr(loaded, "build_tree" if "ingest" in rel else "answer_question")

    # build_tree enforces pin directly before calling pageindex
    backend = contextvars.ContextVar("b", default=None)
    fake_utils = ModuleType("pageindex.utils")
    fake_utils.ConfigLoader = lambda: NS(load=lambda x: NS(**x))
    fake_utils._llm_backend = backend
    fake_classic = ModuleType("pageindex.page_index_classic")
    called = []
    fake_classic.page_index_main = lambda pdf, opt: called.append(pdf) or {"structure": []}
    monkeypatch.setitem(sys.modules, "pageindex", ModuleType("pageindex"))
    monkeypatch.setitem(sys.modules, "pageindex.utils", fake_utils)
    monkeypatch.setitem(sys.modules, "pageindex.page_index_classic", fake_classic)

    monkeypatch.setattr(mods.ingest, "installed_pageindex_commit", lambda: "0" * 40)
    with pytest.raises(RuntimeError, match="not the pin"):
        mods.ingest.build_tree("doc.pdf", mods.common.Settings(store_dir=tmp_path / "store"))
    assert called == []

    # --settings reaches ingest() cleanly
    cfg = tmp_path / "custom.yaml"
    cfg.write_text("index_mode: flash\nverifier_model: claude-haiku-5-5\n", encoding="utf-8")
    seen = {}

    def fake_ingest(*args, **kw):
        seen.update(kw)
        return mods.common.DocRecord(
            doc_id="g@1", family="g", title="t", version="1", sha256="0" * 64,
            pdf_path="p", tree_path="t", page_count=1, index_mode="flash",
            index_model="anthropic/x", pageindex_version="0.2.10", built_at="2026-11-26T00:00:00Z",
        )

    monkeypatch.setattr(mods.ingest, "ingest", fake_ingest)
    assert mods.ingest.main(["x.pdf", "--family", "g", "--version", "1", "--settings", str(cfg)]) == 0
    assert seen["s"].index_mode == "flash"


def test_pageindex_adapter_forwards_contract_and_repair_fields(mods, monkeypatch, tmp_path):
    from eval.s11.ledger import CostLedger, UsageLogger

    fake_result = {
        "answer": "Final [p. 2].",
        "answer_pre_repair": "Pre [p. 9].",
        "fetched_pages": [2],
        "contexts": ["Page 2 text"],
        "cited_pages": [2],
        "latency_s": 0.42,
        "usage": {"agent": {}, "verifier": {}, "total": {}, "input_tokens": 1, "output_tokens": 1, "turns": 1},
        "repaired": True,
        "dropped_sentences": ["Dropped [p. 9]."],
        "models": {"agent": ["claude-sonnet-5-5"], "verifier": ["claude-haiku-5-5"]},
        "timeouts": {"agent_timeout_s": 300.0, "verifier_timeout_s": 120.0},
        "status": "ok",
        "cited_not_fetched": [],
        "retries": 0,
    }
    monkeypatch.setattr(mods.adapter, "answer_question", lambda q, fam, ver, **kw: fake_result)
    mods.adapter.configure(
        ledger=CostLedger(tmp_path / "ledger.jsonl"),
        usage_log=UsageLogger(tmp_path / "usage.jsonl"),
    )
    try:
        out = mods.adapter.run("Q?")
    finally:
        mods.adapter.configure()
    assert out["answer"] == "Final [p. 2]."
    assert out["answer_pre_repair"] == "Pre [p. 9]."
    assert out["repaired"] is True
    assert out["dropped_sentences"] == ["Dropped [p. 9]."]
    assert out["models"] == fake_result["models"]
    assert out["timeouts"] == fake_result["timeouts"]
    assert out["page_text"] == {2: "Page 2 text"}


def test_inspect_pdf_ignores_header_year_and_computes_offset(mods, tmp_path):
    fake_page_with_year_only = NS(get_text=lambda: "2019\nNational Guidelines for Gene Therapy\nBody paragraph")
    assert mods.inspect_pdf.printed_number(fake_page_with_year_only) is None

    fake_page_header_year_footer_num = NS(get_text=lambda: "2019\nBody text\n20\n")
    assert mods.inspect_pdf.printed_number(fake_page_header_year_footer_num) == 20

    pymupdf = pytest.importorskip("pymupdf")
    pdf = tmp_path / "offset.pdf"
    with pymupdf.open() as doc:
        for phys in range(1, 16):
            page = doc.new_page()
            if phys >= 12:
                printed = phys - 11
                page.insert_text((72, 72), "2019\nICMR-DBT Guideline\nBody text here\n" + str(printed))
            else:
                page.insert_text((72, 72), "2019\nFront matter")
        doc.save(pdf)
    with pymupdf.open(pdf) as doc:
        off, votes = mods.inspect_pdf.page_offset(doc)
        assert off == 11 and votes == 4


def test_requirements_pins_aligned():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    part2 = (ROOT / "pageindex" / "requirements_partII.txt").read_text(encoding="utf-8")
    for pkg in ("openai==2.54.0", "anthropic==1.14.0", "litellm==1.106.0", "PyPDF2==3.0.1", "PyMuPDF==1.28.2"):
        assert re.search(rf"(?m)^{re.escape(pkg)}\b", req), f"missing {pkg} in requirements.txt"
    for pkg in (
        "litellm==1.106.0",
        "ragas==0.4.3",
        "openai==2.54.0",
        "openai-agents==0.20.0",
        "PyPDF2==3.0.1",
        "PyMuPDF==1.28.2",
    ):
        assert re.search(rf"(?m)^{re.escape(pkg)}\b", part2), f"missing {pkg} in pageindex/requirements_partII.txt"
    assert "50a71b686d0397df7cb193c33806eb035574b890" in part2
