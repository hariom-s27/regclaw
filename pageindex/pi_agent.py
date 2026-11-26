"""Online stage: resolve edition -> load tree -> Claude Sonnet navigates with tools ->
answer with page citations -> Claude Haiku verifies each claim against fetched text.

Usage:
  python pi_agent.py --family cdsco-gdp "What are the temperature requirements for storage?"
  python pi_agent.py --family cdsco-gdp --as-of 2020-06-01 "..."
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

import anthropic
if __package__ in (None, ''):
    _root_dir = str(Path(__file__).resolve().parents[1])
    _here = str(Path(__file__).resolve().parent)
    if _root_dir not in sys.path:
        sys.path.insert(0, _root_dir)
    if _here not in sys.path:
        sys.path.insert(0, _here)
from observability.tracing import stage, traced, record_response

if __package__:
    from .pi_client import AnthropicRetryClient, _assert_ends_with_user, _extract_thinking_tokens
    from .pi_common import (Settings, fetch_pages, load_manifest, load_settings, load_tree,
                            node_map, outline, parse_page_spec, resolve_edition, subtree_span)
else:
    from pi_client import AnthropicRetryClient, _assert_ends_with_user, _extract_thinking_tokens
    from pi_common import (Settings, fetch_pages, load_manifest, load_settings, load_tree,
                           node_map, outline, parse_page_spec, resolve_edition, subtree_span)

log = logging.getLogger("pageindex.agent")   # stderr via logging; never prints the question

CITE_RE = re.compile(r"\[(?:p|pp)\.\s*([0-9][0-9,\s\-–]*)\]")

# ----------------------------------------------------------------------------- tools
TOOLS = [
    {
        "name": "get_tree_overview",
        "description": ("Return the document outline: node_id, title, page span and a short "
                        "summary per section, down to max_depth levels. Call this when the "
                        "outline in the system prompt is not detailed enough."),
        "input_schema": {"type": "object", "properties": {
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 6, "default": 3}}},
    },
    {
        "name": "get_node",
        "description": ("Return one section: its full summary, page span (including all "
                        "sub-sections), its parent, and its direct children with their summaries. "
                        "Use it to decide which pages to read."),
        "input_schema": {"type": "object", "properties": {
            "node_id": {"type": "string", "description": "4-digit id from the outline, e.g. '0007'"}},
            "required": ["node_id"]},
    },
    {
        "name": "fetch_pages",
        "description": ("Read the actual text of PDF pages. Page numbers are PDF page indices "
                        "as shown in the outline (NOT printed page numbers). Use tight ranges. "
                        "Format: '5', '5-8', or '3,7,10-12'. You may call it several times."),
        "input_schema": {"type": "object", "properties": {
            "pages": {"type": "string", "pattern": r"^\d+(-\d+)?(,\s*\d+(-\d+)?)*$"}},
            "required": ["pages"]},
    },
]

RULES_PATH = Path(__file__).resolve().parents[1] / "eval" / "s11" / "prompts" / "answer_rules_v1.txt"
EXPECTED_REFUSAL = "The guideline does not state this."
_REFUSAL_STRIP_CHARS = "\"'“”‘’*_`"

NAV_TMPL = """Document: {title} (edition/version: {version}; {pages} PDF pages)
Description: {description}

How to work:
1. Read the outline below and pick the sections likely to contain the answer. Use get_node to inspect a section's children if needed.
2. Call fetch_pages with tight page ranges for those sections. If the question spans several sections (definitions + requirements + annexures), fetch each.
3. Stop fetching as soon as the fetched text answers the question. Budget: at most {max_total} pages in total.

Outline (node_id, title, PDF page span, summary):
{outline}"""


def load_answer_rules(path: Path | str = RULES_PATH) -> str:
    """Load the frozen shared answer rules text without its single trailing LF."""
    text = Path(path).read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


def format_page_blocks(pages: dict[int, str]) -> str:
    """Render pages in ascending physical PDF page order using '=== PDF page N ===\\n<text>'."""
    return "\n\n".join(
        f"=== PDF page {int(p)} ===\n{(text or '').strip()}"
        for p, text in sorted(pages.items(), key=lambda kv: int(kv[0]))
    )


def refusal_detected(text: str) -> bool:
    """Return True when normalized answer text starts with the exact S11 refusal sentence."""
    if not text:
        return False
    s = text
    prev = None
    while s != prev:
        prev = s
        s = s.strip().strip(_REFUSAL_STRIP_CHARS)
    s = re.sub(r"\s+", " ", s)
    prev = None
    while s != prev:
        prev = s
        s = s.strip().strip(_REFUSAL_STRIP_CHARS)
    return s.casefold().startswith(EXPECTED_REFUSAL.casefold())


def build_pi_system_text(rec: Any, structure: list[dict], s: Settings, rules_text: str | None = None) -> str:
    """Build single cached PI system prompt: shared answer rules + navigation lines + outline."""
    rules = rules_text if rules_text is not None else load_answer_rules()
    nav_outline = NAV_TMPL.format(
        title=rec.title,
        version=rec.version,
        pages=rec.page_count,
        description=rec.doc_description or "(none)",
        max_total=s.max_pages_total,
        outline=outline(structure, max_depth=2, char_budget=s.overview_char_budget),
    )
    return f"{rules}\n\n{nav_outline}"


@dataclass
class Session:
    rec: object
    tree: dict
    nodes: dict
    s: Settings
    fetched: dict[int, str] = field(default_factory=dict)   # page -> text
    tool_calls: list[dict] = field(default_factory=list)
    # R22 R7 / S11b2: set when navigator/generator fails ("max_tokens", "empty_answer",
    # "max_turns", "model_refusal", "verifier_max_tokens"); None means success.
    failure: str | None = None
    last_stop_reason: str | None = None
    retries: int = 0
    agent_models: list[str] = field(default_factory=list)


@traced('pageindex.tool', as_type='tool')
def _run_tool(sess: Session, name: str, args: dict) -> tuple[str, bool]:
    try:
        if name == "get_tree_overview":
            return outline(sess.tree["structure"], max_depth=int(args.get("max_depth", 3)),
                           char_budget=sess.s.overview_char_budget), False
        if name == "get_node":
            n = sess.nodes.get(str(args["node_id"]))
            if not n:
                return f"Unknown node_id '{args['node_id']}'.", True
            a, b = subtree_span(n)
            parent = next((p for p in sess.nodes.values()
                           if n in (p.get("nodes") or [])), None)
            kids = [{"node_id": c.get("node_id"), "title": c.get("title"),
                     "pages": list(subtree_span(c)), "summary": c.get("summary", "")}
                    for c in n.get("nodes") or []]
            return json.dumps({"node_id": n.get("node_id"), "title": n.get("title"),
                               "pages": [a, b], "summary": n.get("summary", ""),
                               "parent": parent and {"node_id": parent.get("node_id"),
                                                     "title": parent.get("title")},
                               "children": kids}, ensure_ascii=False), False
        if name == "fetch_pages":
            pages = parse_page_spec(args["pages"])
            new = [p for p in pages if p not in sess.fetched]
            if len(pages) > sess.s.max_pages_per_fetch:
                return (f"Too many pages ({len(pages)}) in one call; max "
                        f"{sess.s.max_pages_per_fetch}. Narrow the range."), True
            if len(sess.fetched) + len(new) > sess.s.max_pages_total:
                return (f"Page budget exhausted ({len(sess.fetched)} pages read). "
                        "Answer with what you have."), True
            sess.fetched.update(fetch_pages(sess.rec.pdf_path, new))
            sub = {p: sess.fetched[p] for p in pages if p in sess.fetched}
            if not sub:
                return f"No such pages. Document has {sess.rec.page_count} pages.", True
            return format_page_blocks(sub), False
        return f"Unknown tool {name}", True
    except (ValueError, KeyError) as e:
        return f"Tool error: {e}", True


def _text_of(resp) -> str:
    return "".join(b.text for b in resp.content if b.type == "text").strip()


_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "calls",
)


def sum_thinking_tokens(a: int | None, b: int | None) -> int | None:
    """Sum reported thinking tokens; return None when neither operand reported them."""
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


def _failure_status(failure: str) -> str:
    return "model_refusal" if failure == "model_refusal" else f"failed:{failure}"


def _empty_usage() -> dict[str, Any]:
    return {**{k: 0 for k in _USAGE_KEYS}, "thinking_tokens": None}


def _extract_usage(resp) -> dict[str, Any]:
    ru = getattr(resp, "usage", None)
    return {
        "input_tokens": getattr(ru, "input_tokens", 0) or 0,
        "output_tokens": getattr(ru, "output_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(ru, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(ru, "cache_creation_input_tokens", 0) or 0,
        "calls": 1,
        "thinking_tokens": _extract_thinking_tokens(ru),
    }


def _add_usage(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k in _USAGE_KEYS:
        dst[k] += src.get(k, 0)
    dst["thinking_tokens"] = sum_thinking_tokens(dst.get("thinking_tokens"), src.get("thinking_tokens"))


def _pack_usage(agent: dict[str, Any], verifier: dict[str, Any], turns: int) -> dict[str, Any]:
    total: dict[str, Any] = {k: agent[k] + verifier[k] for k in _USAGE_KEYS}
    total["thinking_tokens"] = sum_thinking_tokens(agent.get("thinking_tokens"), verifier.get("thinking_tokens"))
    return {
        "agent": dict(agent),
        "verifier": dict(verifier),
        "total": total,
        "input_tokens": agent["input_tokens"],
        "output_tokens": agent["output_tokens"],
        "turns": turns,
    }


def run_agent(client: anthropic.Anthropic, sess: Session, question: str,
              extra_user_msg: str | None = None, prior: list | None = None) -> tuple[str, list, dict]:
    """Navigator loop. S11 §3/§8:
    - stop_reason "refusal", "max_tokens", or an empty final text fails immediately (no internal retry);
    - using all agent_max_turns turns without a final answer is a failure (sess.failure = "max_turns").
    On failure the returned answer is "" and the caller must not score it as an answer.
    """
    s = sess.s
    sess.failure = None
    sess.last_stop_reason = None
    system_text = build_pi_system_text(sess.rec, sess.tree["structure"], s)
    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
    messages = list(prior) if prior else [{"role": "user", "content": question}]
    if extra_user_msg:
        messages.append({"role": "user", "content": extra_user_msg})
    usage = {**_empty_usage(), "turns": 0}

    turn = 0
    while turn < s.agent_max_turns:
        _assert_ends_with_user(messages)
        with stage('pageindex.navigate', as_type='generation', model=s.agent_model) as observation:
            resp = client.messages.create(
                model=s.agent_model, max_tokens=s.agent_max_tokens, system=system, tools=TOOLS,
                tool_choice={"type": "auto"}, output_config={"effort": s.answer_effort},
                messages=messages, timeout=s.agent_timeout_s)
            record_response(observation, resp)
        _add_usage(usage, _extract_usage(resp))
        usage["turns"] += 1
        if getattr(resp, "model", None):
            sess.agent_models.append(resp.model)
        stop_reason = getattr(resp, "stop_reason", None)
        sess.last_stop_reason = stop_reason
        bad = (
            "model_refusal" if stop_reason == "refusal"
            else "max_tokens" if stop_reason == "max_tokens"
            else "empty_answer" if stop_reason != "tool_use" and not _text_of(resp)
            else None
        )
        if bad:
            log.error("navigator %s (stop_reason=%s); item fails", bad, stop_reason)
            sess.failure = bad
            return "", messages, usage
        messages.append({"role": "assistant", "content": resp.content})
        turn += 1
        if stop_reason != "tool_use":
            return _text_of(resp), messages, usage
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                out, err = _run_tool(sess, b.name, b.input or {})
                sess.tool_calls.append({"tool": b.name, "input": b.input, "error": err})
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": out, "is_error": err})
        if not results:
            log.error("navigator tool_use with empty tool blocks; item fails")
            sess.failure = "empty_answer"
            return "", messages, usage
        messages.append({"role": "user", "content": results})
    log.error("navigator hit agent_max_turns=%d without a final answer; item fails", s.agent_max_turns)
    sess.failure = "max_turns"
    return "", messages, usage


# ----------------------------------------------------------------------------- verification
VERDICT_TOOL = {
    "name": "report_verdicts",
    "description": "Report one verdict per numbered claim.",
    "input_schema": {"type": "object", "properties": {"verdicts": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "claim_id": {"type": "integer"},
            "verdict": {"type": "string",
                        "enum": ["supported", "partially_supported", "unsupported",
                                 "needs_citation", "no_factual_content"]},
            "evidence": {"type": "string",
                         "description": "Short verbatim quote from the cited page(s) that supports the claim; empty if none."},
            "reason": {"type": "string"}},
        "required": ["claim_id", "verdict", "evidence", "reason"]}}},
        "required": ["verdicts"]},
}

VERIFY_PROMPT = """You are a strict citation checker for regulatory answers.
For each numbered claim, decide whether the text of ITS OWN cited pages supports it.
- Judge only against the pages listed for that claim, not other pages and not your own knowledge.
- "supported": every fact in the claim (numbers, conditions, obligations like shall/should/may) is stated on the cited pages.
- "partially_supported": some facts are there, some are missing or altered.
- "unsupported": cited pages do not support it (or contradict it).
- "needs_citation": the claim states a document fact but has no citation.
- "no_factual_content": connective or meta sentence (e.g. "In summary, ...") with no new fact.
Quote short verbatim evidence when supported.

<pages>
{pages}
</pages>

<claims>
{claims}
</claims>"""


def _cited_pages(sentence: str) -> list[int]:
    pages: set[int] = set()
    for m in CITE_RE.finditer(sentence):
        try:
            pages.update(parse_page_spec(m.group(1).replace("–", "-").replace(" ", "")))
        except ValueError:
            pass
    return sorted(pages)


def split_claims(answer: str) -> list[dict]:
    # Sentence split that keeps citations whole: never split inside "[p. 12]"
    # (next char a digit), nor before a citation placed after the full stop ("[").
    parts = re.split(r"(?<=[.!?\]])\s+(?=[A-Z(•*\-])|\n+", answer)
    claims = []
    for p in (x.strip() for x in parts):
        if len(p) < 3:
            continue
        claims.append({"id": len(claims) + 1, "text": p, "pages": _cited_pages(p)})
    return claims


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().lower()


def _append_reason(existing: str, note: str) -> str:
    return f"{existing} | {note}" if existing else note


class _VerifierStopError(Exception):
    """Internal exception raised when Haiku verifier stops with max_tokens or refusal."""

    def __init__(
        self,
        *,
        status: str,
        stop_reason: str,
        usage: dict[str, Any],
        model_returned: str | None,
    ) -> None:
        self.status = status
        self.stop_reason = stop_reason
        self.usage = usage
        self.model_returned = model_returned
        super().__init__(f"verifier stopped ({stop_reason}): {status}")


def verify_with_usage(client: anthropic.Anthropic, s: Settings, answer: str,
                      fetched: dict[int, str]) -> tuple[list[dict], dict[str, Any], str | None]:
    claims = split_claims(answer)
    if not claims:
        return [], _empty_usage(), None
    page_block = format_page_blocks(fetched)
    claim_block = "\n".join(
        f"{c['id']}. {c['text']}\n   cited pages: {c['pages'] or 'NONE'}"
        + ("" if all(p in fetched for p in c["pages"]) else "  (WARNING: cites a page that was never fetched)")
        for c in claims)
    verify_messages = [{"role": "user", "content": VERIFY_PROMPT.format(pages=page_block, claims=claim_block)}]
    _assert_ends_with_user(verify_messages)
    with stage('pageindex.verify', as_type='generation', model=s.verifier_model) as observation:
        resp = client.messages.create(
            model=s.verifier_model, max_tokens=s.verifier_max_tokens, tools=[VERDICT_TOOL],
            tool_choice={"type": "tool", "name": "report_verdicts"},
            output_config={"effort": s.verifier_effort},
            messages=verify_messages,
            timeout=s.verifier_timeout_s)
        record_response(observation, resp)
    v_usage = _extract_usage(resp)
    resp_model = getattr(resp, "model", None)
    stop_reason = getattr(resp, "stop_reason", None)
    if stop_reason in ("max_tokens", "refusal"):
        status = "model_refusal" if stop_reason == "refusal" else "failed:verifier_max_tokens"
        log.error("verifier %s (stop_reason=%s); item fails", status, stop_reason)
        raise _VerifierStopError(
            status=status,
            stop_reason=stop_reason,
            usage=v_usage,
            model_returned=resp_model,
        )
    block = next((b for b in getattr(resp, "content", []) if getattr(b, "type", None) == "tool_use"), None)
    if block is None or not isinstance(getattr(block, "input", None), dict):
        log.warning("verifier returned no valid tool_use block (model=%s)", resp_model)
        out = [{**c, "verdict": "unsupported", "evidence": "", "reason": "verifier returned no tool_use block"}
               for c in claims]
        return out, v_usage, resp_model
    by_id = {v["claim_id"]: v for v in block.input.get("verdicts", [])}
    out = []
    for c in claims:
        v = dict(by_id.get(c["id"], {"verdict": "unsupported", "evidence": "",
                                      "reason": "verifier returned no verdict"}))
        # deterministic guards on top of the LLM judge
        if any(p not in fetched for p in c["pages"]):
            v["verdict"], v["reason"] = "unsupported", "cites a page that was never fetched"
        elif v.get("verdict") == "supported":
            ev_text = (v.get("evidence") or "").strip()
            if not c["pages"]:
                v["verdict"] = "needs_citation"
                v["reason"] = _append_reason(v.get("reason", ""), "claim has no cited pages")
            elif not ev_text:
                v["verdict"] = "partially_supported"
                v["reason"] = _append_reason(v.get("reason", ""), "missing verbatim evidence quote")
            else:
                cited_text = _norm(" ".join(fetched[p] for p in c["pages"]))
                if _norm(ev_text)[:80] not in cited_text:
                    v["verdict"] = "partially_supported"
                    v["reason"] = _append_reason(v.get("reason", ""), "evidence quote not found verbatim on cited pages")
        out.append({**c, **v})
    return out, v_usage, resp_model


def verify(client: anthropic.Anthropic, s: Settings, answer: str,
           fetched: dict[int, str]) -> list[dict]:
    out, _, _ = verify_with_usage(client, s, answer, fetched)
    return out


FAIL = {"unsupported", "partially_supported", "needs_citation"}


def verify_and_repair(
    verifier_client: Any,
    s: Settings,
    initial_answer: str,
    pages_fn: Callable[[], dict[int, str]],
    repair_fn: Callable[[str], tuple[str, str | None, str | None]],
    *,
    initial_stop_reason: str | None = None,
    can_fetch: bool = True,
) -> dict[str, Any]:
    """Shared Haiku citation check, refusal-skip, one-round repair, and sentence-drop engine."""
    answer = initial_answer
    answer_pre_repair = initial_answer
    repaired = False
    dropped_sentences: list[str] = []
    verdicts: list[dict] = []
    u_ver = _empty_usage()
    verifier_models: list[str] = []
    terminal_stop_reason = initial_stop_reason
    active_verifier = None

    def _get_verifier() -> Any:
        nonlocal active_verifier
        if active_verifier is None:
            active_verifier = verifier_client() if callable(verifier_client) else verifier_client
        return active_verifier

    def _verifier_retries() -> int:
        return getattr(active_verifier, "retries", 0) if active_verifier is not None else 0

    def _run_check(text: str) -> tuple[list[dict] | None, str | None, str | None]:
        try:
            v_list, v_use, v_model = verify_with_usage(_get_verifier(), s, text, pages_fn())
        except _VerifierStopError as exc:
            _add_usage(u_ver, exc.usage)
            if exc.model_returned:
                verifier_models.append(exc.model_returned)
            return None, exc.status, exc.stop_reason
        _add_usage(u_ver, v_use)
        if v_model:
            verifier_models.append(v_model)
        return v_list, None, None

    if refusal_detected(answer):
        return {
            "status": "ok",
            "answer": answer,
            "answer_pre_repair": answer_pre_repair,
            "repaired": False,
            "dropped_sentences": [],
            "refusal": True,
            "check_status": "check_skipped_refusal",
            "stop_reason": terminal_stop_reason,
            "verdicts": [],
            "ver_usage": u_ver,
            "verifier_models": verifier_models,
            "verifier_retries": 0,
        }

    verdicts_1, stop_status, stop_reason = _run_check(answer)
    if stop_status is not None:
        return {
            "status": stop_status,
            "answer": "",
            "answer_pre_repair": answer_pre_repair,
            "repaired": False,
            "dropped_sentences": [],
            "refusal": False,
            "check_status": "verifier_refusal" if stop_reason == "refusal" else "verifier_max_tokens",
            "stop_reason": stop_reason,
            "verdicts": [],
            "ver_usage": u_ver,
            "verifier_models": verifier_models,
            "verifier_retries": _verifier_retries(),
        }
    assert verdicts_1 is not None
    verdicts = verdicts_1

    bad = [v for v in verdicts if v["verdict"] in FAIL]
    if bad:
        repaired = True
        if can_fetch:
            tail = "\nRewrite the full answer. Fix or remove each failing sentence. You may fetch more pages. Keep the same citation format."
        else:
            tail = "\nRewrite the full answer using only the given pages. Fix or remove each failing sentence. Keep the same citation format."
        feedback = (
            "Citation check failed for these sentences:\n"
            + "\n".join(f"- \"{v['text']}\" -> {v['verdict']}: {v['reason']}" for v in bad)
            + tail
        )
        answer2, repair_failure, repair_stop = repair_fn(feedback)
        if repair_stop is not None:
            terminal_stop_reason = repair_stop
        if repair_failure:
            return {
                "status": _failure_status(repair_failure),
                "answer": "",
                "answer_pre_repair": answer_pre_repair,
                "repaired": True,
                "dropped_sentences": [],
                "refusal": False,
                "check_status": "check_skipped_failed",
                "stop_reason": terminal_stop_reason,
                "verdicts": [],
                "ver_usage": u_ver,
                "verifier_models": verifier_models,
                "verifier_retries": _verifier_retries(),
            }
        answer = answer2
        if refusal_detected(answer):
            return {
                "status": "ok",
                "answer": answer,
                "answer_pre_repair": answer_pre_repair,
                "repaired": True,
                "dropped_sentences": [],
                "refusal": True,
                "check_status": "check_skipped_refusal",
                "stop_reason": terminal_stop_reason,
                "verdicts": [],
                "ver_usage": u_ver,
                "verifier_models": verifier_models,
                "verifier_retries": _verifier_retries(),
            }
        verdicts_2, stop_status2, stop_reason2 = _run_check(answer)
        if stop_status2 is not None:
            return {
                "status": stop_status2,
                "answer": "",
                "answer_pre_repair": answer_pre_repair,
                "repaired": True,
                "dropped_sentences": [],
                "refusal": False,
                "check_status": "verifier_refusal" if stop_reason2 == "refusal" else "verifier_max_tokens",
                "stop_reason": stop_reason2,
                "verdicts": [],
                "ver_usage": u_ver,
                "verifier_models": verifier_models,
                "verifier_retries": _verifier_retries(),
            }
        assert verdicts_2 is not None
        verdicts = verdicts_2
        bad = [v for v in verdicts if v["verdict"] in FAIL]
        if bad:
            dropped_sentences = [v["text"] for v in bad]
            keep = [v["text"] for v in verdicts if v["verdict"] not in FAIL]
            answer = " ".join(keep) + (
                f"\n\n[Note: {len(bad)} statement(s) removed because they could not be "
                "verified against the cited pages.]"
            )

    return {
        "status": "ok",
        "answer": answer,
        "answer_pre_repair": answer_pre_repair,
        "repaired": repaired,
        "dropped_sentences": dropped_sentences,
        "refusal": False,
        "check_status": "checked",
        "stop_reason": terminal_stop_reason,
        "verdicts": verdicts,
        "ver_usage": u_ver,
        "verifier_models": verifier_models,
        "verifier_retries": _verifier_retries(),
    }


@traced('pageindex.agent', as_type='agent')
def answer_question(question: str, family: str, version: str | None = None,
                    as_of: date | None = None, s: Settings | None = None,
                    *, ledger=None, usage_log=None) -> dict:
    s = s or load_settings()
    t0 = time.perf_counter()
    if ledger is None or usage_log is None:
        raise ValueError("answer_question requires both ledger and usage_log")
    rec = resolve_edition(load_manifest(s), family, version, as_of)
    tree = load_tree(rec.tree_path)
    sess = Session(rec=rec, tree=tree, nodes=node_map(tree["structure"]), s=s)
    log.info("pageindex timeouts: agent_timeout_s=%.1f verifier_timeout_s=%.1f",
             s.agent_timeout_s, s.verifier_timeout_s)
    agent_client = AnthropicRetryClient(
        anthropic.Anthropic(max_retries=0, timeout=s.agent_timeout_s),
        ledger=ledger, usage_log=usage_log, lane="agent",
        timeout_s=s.agent_timeout_s, default_max_tokens=s.agent_max_tokens,
    )
    u_agent = _empty_usage()
    u_ver = _empty_usage()
    verifier_models: list[str] = []

    answer, history, u1 = run_agent(agent_client, sess, question)
    _add_usage(u_agent, u1)
    agent_turns = u1["turns"]
    sess.retries += agent_client.retries
    if sess.failure:
        return _failed(question, rec, sess, _pack_usage(u_agent, u_ver, agent_turns), t0,
                       repaired=False, answer_pre_repair="", verifier_models=verifier_models,
                       status=_failure_status(sess.failure), check_status="check_skipped_failed")

    def _make_verifier_client() -> AnthropicRetryClient:
        return AnthropicRetryClient(
            anthropic.Anthropic(max_retries=0, timeout=s.verifier_timeout_s),
            ledger=ledger, usage_log=usage_log, lane="verifier",
            timeout_s=s.verifier_timeout_s, default_max_tokens=s.verifier_max_tokens,
        )

    def _repair_pi(feedback: str) -> tuple[str, str | None, str | None]:
        nonlocal history, agent_turns
        prev_retries = agent_client.retries
        ans2, history, u2 = run_agent(agent_client, sess, question, extra_user_msg=feedback, prior=history)
        _add_usage(u_agent, u2)
        agent_turns += u2["turns"]
        sess.retries += (agent_client.retries - prev_retries)
        return ans2, sess.failure, sess.last_stop_reason

    vr = verify_and_repair(
        _make_verifier_client,
        s,
        answer,
        lambda: sess.fetched,
        _repair_pi,
        initial_stop_reason=sess.last_stop_reason,
        can_fetch=True,
    )
    _add_usage(u_ver, vr["ver_usage"])
    verifier_models.extend(vr["verifier_models"])
    sess.retries += vr["verifier_retries"]

    if vr["status"] != "ok":
        return _failed(
            question, rec, sess, _pack_usage(u_agent, u_ver, agent_turns), t0,
            repaired=vr["repaired"], answer_pre_repair=vr["answer_pre_repair"],
            verifier_models=verifier_models, status=vr["status"],
            check_status=vr["check_status"], stop_reason=vr["stop_reason"],
        )

    verdicts = vr["verdicts"]
    final_answer = vr["answer"]
    # R7: every cited page must be in the fetched set. verify() already marks such claims
    # unsupported; record them and never let one reach cited_pages.
    cited_not_fetched = sorted({p for v in verdicts for p in v["pages"] if p not in sess.fetched})
    cited = sorted({p for v in verdicts if v["verdict"] not in FAIL for p in v["pages"]
                    if p in sess.fetched})
    dedup_ver_models = list(dict.fromkeys(verifier_models))
    return {
        "question": question, "doc_id": rec.doc_id, "answer": final_answer,
        "answer_pre_repair": vr["answer_pre_repair"], "status": "ok",
        "refusal": vr["refusal"], "check_status": vr["check_status"],
        "stop_reason": vr["stop_reason"], "effort": s.answer_effort,
        "cited_pages": cited, "cited_not_fetched": cited_not_fetched,
        "fetched_pages": sorted(sess.fetched),
        "verdicts": verdicts, "repaired": vr["repaired"],
        "dropped_sentences": vr["dropped_sentences"],
        "tool_calls": sess.tool_calls, "usage": _pack_usage(u_agent, u_ver, agent_turns),
        "models": {"agent": list(dict.fromkeys(sess.agent_models)),
                   "verifier": dedup_ver_models},
        "verifier_model_returned": dedup_ver_models[-1] if dedup_ver_models else None,
        "timeouts": {"agent_timeout_s": s.agent_timeout_s, "verifier_timeout_s": s.verifier_timeout_s},
        "retries": sess.retries, "latency_s": round(time.perf_counter() - t0, 4),
        # keep page texts for Ragas (R7): retrieved_contexts
        "contexts": [sess.fetched[p] for p in sorted(sess.fetched)],
    }


def _failed(question: str, rec, sess: Session, usage: dict, t0: float, repaired: bool,
            answer_pre_repair: str = "", verifier_models: list[str] | None = None,
            *, status: str | None = None, check_status: str = "check_skipped_failed",
            stop_reason: str | None = None) -> dict:
    """A navigator or verifier stop failure is a SYSTEM OUTCOME (R7): no answer, nothing cited;
    the evaluation keeps the item and scores it as a miss (eval_pageindex.py)."""
    eff_status = status if status is not None else _failure_status(sess.failure or "unknown")
    dedup_ver_models = list(dict.fromkeys(verifier_models or []))
    return {
        "question": question, "doc_id": rec.doc_id, "answer": "",
        "answer_pre_repair": answer_pre_repair,
        "status": eff_status,
        "refusal": False, "check_status": check_status,
        "stop_reason": stop_reason if stop_reason is not None else sess.last_stop_reason,
        "effort": sess.s.answer_effort,
        "cited_pages": [], "cited_not_fetched": [], "fetched_pages": sorted(sess.fetched),
        "verdicts": [], "repaired": repaired, "dropped_sentences": [],
        "tool_calls": sess.tool_calls, "usage": usage,
        "models": {"agent": list(dict.fromkeys(sess.agent_models)),
                   "verifier": dedup_ver_models},
        "verifier_model_returned": dedup_ver_models[-1] if dedup_ver_models else None,
        "timeouts": {"agent_timeout_s": sess.s.agent_timeout_s, "verifier_timeout_s": sess.s.verifier_timeout_s},
        "retries": sess.retries, "latency_s": round(time.perf_counter() - t0, 4),
        "contexts": [sess.fetched[p] for p in sorted(sess.fetched)],
    }


if __name__ == "__main__":
    from eval.s11.ledger import CostLedger, UsageLogger, default_s11_paths

    default_ledger, default_usage, _ = default_s11_paths()
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--family", required=True)
    ap.add_argument("--version")
    ap.add_argument("--as-of", type=date.fromisoformat)
    ap.add_argument("--ledger", default=str(default_ledger))
    ap.add_argument("--usage-log", default=str(default_usage))
    ap.add_argument("--json", action="store_true", help="print full result JSON")
    a = ap.parse_args()
    r = answer_question(
        a.question,
        a.family,
        a.version,
        a.as_of,
        ledger=CostLedger(Path(a.ledger)),
        usage_log=UsageLogger(Path(a.usage_log)),
    )
    if a.json:
        print(json.dumps({k: v for k, v in r.items() if k != "contexts"}, indent=2, default=str))
    else:
        print(r["answer"] or f"[{r['status']}] no answer")
        print(f"\n[{r['doc_id']}] status={r['status']} fetched={r['fetched_pages']} cited={r['cited_pages']} "
              f"repaired={r['repaired']} latency={r['latency_s']}s", file=sys.stderr)
