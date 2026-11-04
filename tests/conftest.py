"""Repo-wide test default: stub mode for every test under tests/, as `python tasks.py test` sets it.

Plain `pytest` (without tasks.py) must never build a live LLM client just because ANTHROPIC_API_KEY or
AZURE_OPENAI_API_KEY is in the OS environment (llm.provider.live_configured() is False only when
REGCLAW_STUB == '1'). Tests that check the non-stub path override this inside the test with
monkeypatch.setenv('REGCLAW_STUB', '0') / delenv. No network blocking here (some env-only tests download).
"""
import pytest


@pytest.fixture(autouse=True)
def regclaw_stub(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
