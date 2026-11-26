"""Offline tests for Azure judge GuardedAsyncTransport, ModelVersionGuard, and eval invalidation."""
import asyncio
import importlib
import inspect
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
import sys

import httpx
import openai
import pytest

import _netguard
from eval.s11.ledger import USAGE_FIELDS, UsageLogger

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-key-not-real"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("REGCLAW_STUB", "1")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    _netguard.block_network(monkeypatch, "judge guard tests forbid network")


@pytest.fixture
def mods(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "pageindex"))
    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    return NS(
        client=importlib.import_module("pi_client"),
        eval_pi=importlib.import_module("eval_pageindex"),
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_q6_genuine_async_openai_has_no_ledger_param_and_retries_logs(mods, tmp_path):
    """Q6 + Q1: build_guarded_async_openai returns a real openai.AsyncOpenAI and takes no ledger param."""
    assert "ledger" not in inspect.signature(mods.client.build_guarded_async_openai).parameters
    assert "ledger" not in inspect.signature(mods.eval_pi.azure_async_client).parameters

    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    guard = mods.client.ModelVersionGuard()
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)

    outcomes = iter([
        httpx.Response(429, json={"error": {"message": "rate limited"}}),
        httpx.Response(503, json={"error": {"message": "unavailable"}}),
        httpx.Response(200, json={
            "id": "chat-1",
            "object": "chat.completion",
            "model": "gpt-4.1-mini-2025-04-14",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 12, "total_tokens": 62},
        }),
        httpx.Response(200, json={
            "object": "list",
            "model": "text-embedding-3-small",
            "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
            "usage": {"prompt_tokens": 8, "total_tokens": 8},
        }),
        httpx.Response(400, json={"error": {"message": "bad request"}}),
    ])
    transport = httpx.MockTransport(lambda req: next(outcomes))
    client = mods.client.build_guarded_async_openai(
        base_url="https://res.openai.azure.com/openai/v1/",
        api_key=SECRET,
        timeout=180.0,
        usage_log=usage_log,
        guard=guard,
        inner_transport=transport,
        sleep_fn=fake_sleep,
    )
    assert isinstance(client, openai.AsyncOpenAI)

    async def run_calls():
        r_chat = await client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": "SECRET_JUDGE_PROMPT"}],
        )
        r_emb = await client.embeddings.create(
            model="text-embedding-3-small",
            input="SECRET_EMBED_INPUT",
        )
        with pytest.raises(openai.BadRequestError):
            await client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": "bad"}],
            )
        await client.close()
        return r_chat, r_emb

    r_chat, r_emb = asyncio.run(run_calls())
    assert r_chat.model == "gpt-4.1-mini-2025-04-14"
    assert r_emb.model == "text-embedding-3-small"
    assert sleeps == [2.0, 4.0]  # 400 was not retried
    assert guard.observed_models == {
        "gpt-4.1-mini": "gpt-4.1-mini-2025-04-14",
        "text-embedding-3-small": "text-embedding-3-small",
    }
    assert guard.violations == []

    rows = _read_jsonl(tmp_path / "usage.jsonl")
    assert [r["lane"] for r in rows] == ["judge", "judge", "judge", "embedding", "judge"]
    assert [r["status"] for r in rows] == ["error", "error", "ok", "ok", "error"]
    assert [r["status_code"] for r in rows] == [429, 503, 200, 200, 400]
    assert all(r["provider"] == "azure_openai" and r["cost_usd"] is None for r in rows)
    assert all(tuple(r.keys()) == USAGE_FIELDS for r in rows)
    raw = (tmp_path / "usage.jsonl").read_text(encoding="utf-8")
    assert "SECRET_JUDGE_PROMPT" not in raw and "SECRET_EMBED_INPUT" not in raw and SECRET not in raw


def test_real_instructor_accepts_guarded_async_openai(mods, tmp_path):
    """Q6 verification: installed instructor.from_openai accepts build_guarded_async_openai without ClientError."""
    instructor = pytest.importorskip("instructor")
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    client = mods.client.build_guarded_async_openai(
        base_url="https://res.openai.azure.com/openai/v1/",
        api_key=SECRET,
        timeout=180.0,
        usage_log=usage_log,
        inner_transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})),
    )
    patched = instructor.from_openai(client, mode=instructor.Mode.JSON)
    assert patched is not None
    asyncio.run(client.close())


def test_model_version_guard_raises_immediately_without_retry(mods, tmp_path):
    usage_log = UsageLogger(tmp_path / "usage.jsonl")
    guard = mods.client.ModelVersionGuard()
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)

    outcomes = iter([
        httpx.Response(200, json={
            "id": "1", "object": "chat.completion", "model": "gpt-4.1-mini-2025-04-14",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "a"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }),
        httpx.Response(200, json={
            "id": "2", "object": "chat.completion", "model": "gpt-4.1-mini-2026-01-01",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "b"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        }),
    ])
    requests_sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests_sent.append(req)
        return next(outcomes)

    client = mods.client.build_guarded_async_openai(
        base_url="https://res.openai.azure.com/openai/v1/",
        api_key=SECRET,
        timeout=180.0,
        usage_log=usage_log,
        guard=guard,
        inner_transport=httpx.MockTransport(handler),
        sleep_fn=fake_sleep,
    )

    async def run_calls():
        await client.chat.completions.create(model="gpt-4.1-mini", messages=[{"role": "user", "content": "hi"}])
        with pytest.raises(mods.client.ModelVersionChangedError, match="2026-01-01") as exc_info:
            await client.chat.completions.create(model="gpt-4.1-mini", messages=[{"role": "user", "content": "hi"}])
        assert isinstance(exc_info.value, openai.OpenAIError)
        await client.close()

    asyncio.run(run_calls())
    assert len(requests_sent) == 2
    assert sleeps == []
    assert len(guard.violations) == 1
    assert guard.violations[0]["expected_model"] == "gpt-4.1-mini-2025-04-14"
    assert guard.violations[0]["observed_model"] == "gpt-4.1-mini-2026-01-01"
    rows = _read_jsonl(tmp_path / "usage.jsonl")
    assert rows[-1]["status"] == "model_version_changed"


def test_change4_eval_pageindex_invalidates_run_and_exits_nonzero_on_guard_violation(
    mods, monkeypatch, tmp_path, capsys
):
    """CHANGE 4: eval_pageindex checks guard.violations after scoring and marks manifest invalid (exit != 0)."""
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://res.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", SECRET)

    captured_guard = []

    class Metric:
        def __init__(self, name, guard):
            self.name = name
            self.guard = guard

        async def ascore(self, **kw):
            if self.name == "Faithfulness":
                # Simulate guard observing normal model first, then silent drift recorded on guard
                self.guard.check(deployment="gpt-4.1-mini", returned_model="gpt-4.1-mini-2025-04-14", lane="judge")
                try:
                    self.guard.check(deployment="gpt-4.1-mini", returned_model="gpt-4.1-mini-2026-02-01", lane="judge")
                except mods.client.ModelVersionChangedError:
                    # Even if metric scoring itself returned a score instead of counting a judge error,
                    # CHANGE 4 requires eval_pageindex to check guard.violations after scoring.
                    pass
            return NS(value=0.9)

    fake_mods = {
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

    def llm_factory(model, **kw):
        captured_guard.append(kw["client"]._model_guard)
        return NS(model=model, guard=kw["client"]._model_guard)

    fake_mods["ragas.llms"].llm_factory = llm_factory
    fake_mods["ragas.embeddings.base"].embedding_factory = lambda provider, **kw: NS(provider=provider)
    for cls in ("Faithfulness", "AnswerRelevancy", "AnswerCorrectness"):
        setattr(
            fake_mods["ragas.metrics.collections"],
            cls,
            lambda cls=cls, **kw: Metric(cls, kw["llm"].guard),
        )
    for name, m in fake_mods.items():
        monkeypatch.setitem(sys.modules, name, m)

    golden = tmp_path / "golden.json"
    golden.write_text(json.dumps([{"id": "q1", "question": "Q?", "reference": "R", "gold_pages": [12]}]))
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("judge_model: gpt-4.1-mini\njudge_repeats: 1\n")
    out = tmp_path / "rows.json"

    code = mods.eval_pi.main([
        "--golden", str(golden),
        "--dry-run",
        "--runs", "1",
        "--settings", str(cfg),
        "--usage-log", str(tmp_path / "usage.jsonl"),
        "--out", str(out),
    ])
    assert code == 2
    manifest = json.loads((tmp_path / "rows.manifest.json").read_text(encoding="utf-8"))
    assert manifest["valid"] is False
    assert manifest["exit_code"] == 2
    assert len(manifest["model_version_guard"]["violations"]) == 1
    captured = capsys.readouterr()
    assert "WITHHELD" in captured.out
    assert "model version drift" in captured.err
