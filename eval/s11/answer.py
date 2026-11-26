"""Shared S11 answer step for PI, HY, and WD with citation check, repair, and write-once records."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
import time
from typing import Any, Callable

import anthropic

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "pageindex") not in sys.path:
    sys.path.insert(0, str(ROOT / "pageindex"))

from eval.s11.ledger import CostLedger, UsageLogger, write_once_json  # noqa: E402
from pi_agent import (  # noqa: E402
    EXPECTED_REFUSAL,
    RULES_PATH,
    _add_usage,
    _empty_usage,
    _extract_usage,
    _failure_status,
    _text_of,
    answer_question,
    format_page_blocks,
    load_answer_rules,
    refusal_detected,
    sum_thinking_tokens,
    verify_and_repair,
)
from pi_client import AnthropicRetryClient  # noqa: E402
from pi_common import Settings, load_settings  # noqa: E402

RESULT_KEYS = (
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

USAGE_SUBKEYS = ("input", "output", "cache_read", "cache_write", "thinking_tokens")


def _usage_block(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "input": int(raw.get("input_tokens", 0) or 0),
        "output": int(raw.get("output_tokens", 0) or 0),
        "cache_read": int(raw.get("cache_read_input_tokens", 0) or 0),
        "cache_write": int(raw.get("cache_creation_input_tokens", 0) or 0),
        "thinking_tokens": raw.get("thinking_tokens"),
    }


def _pack_s11_usage(ans: dict[str, Any], ver: dict[str, Any]) -> dict[str, dict[str, Any]]:
    a = _usage_block(ans)
    v = _usage_block(ver)
    total = {
        "input": a["input"] + v["input"],
        "output": a["output"] + v["output"],
        "cache_read": a["cache_read"] + v["cache_read"],
        "cache_write": a["cache_write"] + v["cache_write"],
        "thinking_tokens": sum_thinking_tokens(a["thinking_tokens"], v["thinking_tokens"]),
    }
    return {"answer": a, "verifier": v, "total": total}


def _make_record(
    *,
    system: str,
    question_id: str,
    pages_given: list[int],
    answer_pre_repair: str,
    answer: str,
    repaired: bool,
    dropped_sentences: list[str],
    refusal: bool,
    check_status: str,
    stop_reason: str | None,
    status: str,
    model_returned: str | None,
    verifier_model_returned: str | None,
    effort: str,
    usage: dict[str, dict[str, Any]],
    latency_s: float,
) -> dict[str, Any]:
    return {
        "system": system,
        "question_id": question_id,
        "pages_given": sorted(int(p) for p in pages_given),
        "answer_pre_repair": answer_pre_repair,
        "answer": answer,
        "repaired": bool(repaired),
        "dropped_sentences": list(dropped_sentences),
        "refusal": bool(refusal),
        "check_status": check_status,
        "stop_reason": stop_reason,
        "status": status,
        "model_returned": model_returned,
        "verifier_model_returned": verifier_model_returned,
        "effort": effort,
        "usage": usage,
        "latency_s": round(float(latency_s), 4),
    }


def _save_record(record: dict[str, Any], out_dir: Path | str | None, question_id: str) -> None:
    if out_dir is not None:
        write_once_json(Path(out_dir) / f"{question_id}.json", record)


def build_hy_request(
    question: str,
    pages: dict[int, str],
    *,
    s: Settings,
    rules_text: str | None = None,
) -> dict[str, Any]:
    """Build HY request: cached rules system block + ascending page blocks and question in user turn."""
    rules = rules_text if rules_text is not None else load_answer_rules()
    page_blocks = format_page_blocks(pages)
    user_text = f"{page_blocks}\n\nQuestion: {question}" if page_blocks else f"Question: {question}"
    return {
        "model": s.agent_model,
        "max_tokens": s.agent_max_tokens,
        "timeout": s.agent_timeout_s,
        "output_config": {"effort": s.answer_effort},
        "system": [{"type": "text", "text": rules, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user_text}],
    }


def build_wd_request(
    question: str,
    all_pages: dict[int, str],
    *,
    s: Settings,
    rules_text: str | None = None,
) -> dict[str, Any]:
    """Build WD request: rules block + cached 116-page document block in system; question in user turn."""
    rules = rules_text if rules_text is not None else load_answer_rules()
    doc_text = format_page_blocks(all_pages)
    return {
        "model": s.agent_model,
        "max_tokens": s.agent_max_tokens,
        "timeout": s.agent_timeout_s,
        "output_config": {"effort": s.answer_effort},
        "system": [
            {"type": "text", "text": rules},
            {"type": "text", "text": doc_text, "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [{"role": "user", "content": f"Question: {question}"}],
    }


def _classify_answer_response(resp: Any) -> tuple[str, str | None, str | None]:
    stop_reason = getattr(resp, "stop_reason", None)
    text = _text_of(resp)
    if stop_reason == "refusal":
        return text, "model_refusal", stop_reason
    if stop_reason == "max_tokens":
        return text, "max_tokens", stop_reason
    if not text:
        return text, "empty_answer", stop_reason
    return text, None, stop_reason


def answer_from_pages(
    *,
    system_name: str,
    question_id: str,
    question: str,
    pages: dict[int, str],
    s: Settings | None = None,
    ledger: CostLedger,
    usage_log: UsageLogger,
    request_builder: Callable[..., dict[str, Any]],
    sdk_client: Any = None,
    verifier_sdk_client: Any = None,
    out_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Run one HY or WD item using the shared check/repair engine and return an exact S11 result record."""
    s = s or load_settings()
    t0 = time.perf_counter()
    req = request_builder(question, pages, s=s)
    agent_sdk = sdk_client if sdk_client is not None else anthropic.Anthropic(max_retries=0, timeout=s.agent_timeout_s)
    agent_client = AnthropicRetryClient(
        agent_sdk,
        ledger=ledger,
        usage_log=usage_log,
        lane="answer",
        timeout_s=s.agent_timeout_s,
        default_max_tokens=s.agent_max_tokens,
    )

    u_ans = _empty_usage()
    ans_models: list[str] = []
    resp1 = agent_client.messages.create(**req)
    _add_usage(u_ans, _extract_usage(resp1))
    if getattr(resp1, "model", None):
        ans_models.append(resp1.model)
    text1, fail1, stop1 = _classify_answer_response(resp1)
    pages_given = sorted(int(p) for p in pages)

    if fail1:
        record = _make_record(
            system=system_name,
            question_id=question_id,
            pages_given=pages_given,
            answer_pre_repair="",
            answer="",
            repaired=False,
            dropped_sentences=[],
            refusal=False,
            check_status="check_skipped_failed",
            stop_reason=stop1,
            status=_failure_status(fail1),
            model_returned=ans_models[-1] if ans_models else None,
            verifier_model_returned=None,
            effort=s.answer_effort,
            usage=_pack_s11_usage(u_ans, _empty_usage()),
            latency_s=time.perf_counter() - t0,
        )
        _save_record(record, out_dir, question_id)
        return record

    history = list(req["messages"]) + [{"role": "assistant", "content": resp1.content}]

    def _verifier_factory() -> AnthropicRetryClient:
        v_sdk = (
            verifier_sdk_client
            if verifier_sdk_client is not None
            else (sdk_client if sdk_client is not None else anthropic.Anthropic(max_retries=0, timeout=s.verifier_timeout_s))
        )
        return AnthropicRetryClient(
            v_sdk,
            ledger=ledger,
            usage_log=usage_log,
            lane="verifier",
            timeout_s=s.verifier_timeout_s,
            default_max_tokens=s.verifier_max_tokens,
        )

    def _repair_pages(feedback: str) -> tuple[str, str | None, str | None]:
        repair_msgs = history + [{"role": "user", "content": feedback}]
        repair_req = {**req, "messages": repair_msgs}
        resp2 = agent_client.messages.create(**repair_req)
        _add_usage(u_ans, _extract_usage(resp2))
        if getattr(resp2, "model", None):
            ans_models.append(resp2.model)
        return _classify_answer_response(resp2)

    vr = verify_and_repair(
        _verifier_factory,
        s,
        text1,
        lambda: dict(pages),
        _repair_pages,
        initial_stop_reason=stop1,
        can_fetch=False,
    )
    ver_models = vr["verifier_models"]
    record = _make_record(
        system=system_name,
        question_id=question_id,
        pages_given=pages_given,
        answer_pre_repair=vr["answer_pre_repair"],
        answer=vr["answer"],
        repaired=vr["repaired"],
        dropped_sentences=vr["dropped_sentences"],
        refusal=vr["refusal"],
        check_status=vr["check_status"],
        stop_reason=vr["stop_reason"],
        status=vr["status"],
        model_returned=ans_models[-1] if ans_models else None,
        verifier_model_returned=ver_models[-1] if ver_models else None,
        effort=s.answer_effort,
        usage=_pack_s11_usage(u_ans, vr["ver_usage"]),
        latency_s=time.perf_counter() - t0,
    )
    _save_record(record, out_dir, question_id)
    return record


def run_hy_answer(
    question_id: str,
    question: str,
    pages: dict[int, str],
    *,
    s: Settings | None = None,
    ledger: CostLedger,
    usage_log: UsageLogger,
    out_dir: Path | str | None = None,
    sdk_client: Any = None,
    verifier_sdk_client: Any = None,
) -> dict[str, Any]:
    """Execute one HY answer item and optionally write once to out_dir / '<question_id>.json'."""
    return answer_from_pages(
        system_name="HY",
        question_id=question_id,
        question=question,
        pages=pages,
        s=s,
        ledger=ledger,
        usage_log=usage_log,
        request_builder=build_hy_request,
        sdk_client=sdk_client,
        verifier_sdk_client=verifier_sdk_client,
        out_dir=out_dir,
    )


def run_wd_answer(
    question_id: str,
    question: str,
    all_pages: dict[int, str],
    *,
    s: Settings | None = None,
    ledger: CostLedger,
    usage_log: UsageLogger,
    out_dir: Path | str | None = None,
    sdk_client: Any = None,
    verifier_sdk_client: Any = None,
) -> dict[str, Any]:
    """Execute one WD answer item and optionally write once to out_dir / '<question_id>.json'."""
    return answer_from_pages(
        system_name="WD",
        question_id=question_id,
        question=question,
        pages=all_pages,
        s=s,
        ledger=ledger,
        usage_log=usage_log,
        request_builder=build_wd_request,
        sdk_client=sdk_client,
        verifier_sdk_client=verifier_sdk_client,
        out_dir=out_dir,
    )


def run_pi_answer(
    question_id: str,
    question: str,
    *,
    family: str,
    version: str | None = None,
    as_of: date | None = None,
    s: Settings | None = None,
    ledger: CostLedger,
    usage_log: UsageLogger,
    out_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Execute one PI answer item via pi_agent.answer_question and normalize to S11 result schema."""
    s = s or load_settings()
    raw = answer_question(
        question,
        family=family,
        version=version,
        as_of=as_of,
        s=s,
        ledger=ledger,
        usage_log=usage_log,
    )
    agent_models = raw["models"]["agent"]
    ver_models = raw["models"]["verifier"]
    record = _make_record(
        system="PI",
        question_id=question_id,
        pages_given=raw["fetched_pages"],
        answer_pre_repair=raw["answer_pre_repair"],
        answer=raw["answer"],
        repaired=raw["repaired"],
        dropped_sentences=raw["dropped_sentences"],
        refusal=raw["refusal"],
        check_status=raw["check_status"],
        stop_reason=raw["stop_reason"],
        status=raw["status"],
        model_returned=agent_models[-1] if agent_models else None,
        verifier_model_returned=ver_models[-1] if ver_models else None,
        effort=raw.get("effort", s.answer_effort),
        usage=_pack_s11_usage(raw["usage"]["agent"], raw["usage"]["verifier"]),
        latency_s=raw["latency_s"],
    )
    _save_record(record, out_dir, question_id)
    return record


__all__ = [
    "EXPECTED_REFUSAL",
    "RESULT_KEYS",
    "RULES_PATH",
    "USAGE_SUBKEYS",
    "answer_from_pages",
    "build_hy_request",
    "build_wd_request",
    "format_page_blocks",
    "load_answer_rules",
    "refusal_detected",
    "run_hy_answer",
    "run_pi_answer",
    "run_wd_answer",
    "verify_and_repair",
]
