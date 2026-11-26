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
    from .pi_client import AnthropicRetryClient
    from .pi_common import (Settings, fetch_pages, load_manifest, load_settings, load_tree,
                            node_map, outline, parse_page_spec, resolve_edition, subtree_span)
else:
    from pi_client import AnthropicRetryClient
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

SYSTEM_TMPL = """You answer questions about one regulatory document using ONLY its text.

Document: {title} (edition/version: {version}; {pages} PDF pages)
Description: {description}

How to work:
1. Read the outline below and pick the sections likely to contain the answer. Use get_node to inspect a section's children if needed.
2. Call fetch_pages with tight page ranges for those sections. If the question spans several sections (definitions + requirements + annexures), fetch each.
3. Stop fetching as soon as the fetched text answers the question. Budget: at most {max_total} pages in total.
4. Answer concisely. After EVERY sentence that states a fact from the document, add a citation in the form [p. N] or [pp. N-M] using the PDF page numbers shown in the fetched text headers. Cite only pages you actually fetched.
5. If the fetched text does not answer the question, say so plainly. Never use outside knowledge to fill gaps.

Outline (node_id, title, PDF page span, summary):
{outline}
"""


@dataclass
class Session:
    rec: object
    tree: dict
    nodes: dict
    s: Settings
    fetched: dict[int, str] = field(default_factory=dict)   # page -> text
    tool_calls: list[dict] = field(default_factory=list)
    # R22 R7: set by run_agent when the navigator fails ("max_tokens", "empty_answer",
    # "max_turns"); None means the last run_agent call produced an answer.
    failure: str | None = None
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
            out = [f"=== PDF page {p} ===\n{sess.fetched[p].strip()}"
                   for p in pages if p in sess.fetched]
            if not out:
                return f"No such pages. Document has {sess.rec.page_count} pages.", True
            return "\n\n".join(out), False
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


def _empty_usage() -> dict[str, int]:
    return {k: 0 for k in _USAGE_KEYS}


def _extract_usage(resp) -> dict[str, int]:
    ru = getattr(resp, "usage", None)
    return {
        "input_tokens": getattr(ru, "input_tokens", 0) or 0,
        "output_tokens": getattr(ru, "output_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(ru, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(ru, "cache_creation_input_tokens", 0) or 0,
        "calls": 1,
    }


def _add_usage(dst: dict[str, int], src: dict[str, int]) -> None:
    for k in _USAGE_KEYS:
        dst[k] += src.get(k, 0)


def _pack_usage(agent: dict[str, int], verifier: dict[str, int], turns: int) -> dict:
    total = {k: agent[k] + verifier[k] for k in _USAGE_KEYS}
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
    - stop_reason "max_tokens" or an empty final text fails immediately (no internal retry);
    - using all agent_max_turns turns without a final answer is a failure (sess.failure = "max_turns").
    On failure the returned answer is "" and the caller must not score it as an answer.
    """
    s = sess.s
    sess.failure = None
    system_text = SYSTEM_TMPL.format(
        title=sess.rec.title, version=sess.rec.version, pages=sess.rec.page_count,
        description=sess.rec.doc_description or "(none)", max_total=s.max_pages_total,
        outline=outline(sess.tree["structure"], max_depth=2, char_budget=s.overview_char_budget))
    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
    messages = prior or [{"role": "user", "content": question}]
    if extra_user_msg:
        messages.append({"role": "user", "content": extra_user_msg})
    usage = {**_empty_usage(), "turns": 0}

    turn = 0
    while turn < s.agent_max_turns:
        with stage('pageindex.navigate', as_type='generation', model=s.agent_model) as observation:
            resp = client.messages.create(
                model=s.agent_model, max_tokens=s.agent_max_tokens, system=system, tools=TOOLS,
                tool_choice={"type": "auto"}, messages=messages, timeout=s.agent_timeout_s)
            record_response(observation, resp)
        _add_usage(usage, _extract_usage(resp))
        usage["turns"] += 1
        if getattr(resp, "model", None):
            sess.agent_models.append(resp.model)
        bad = ("max_tokens" if resp.stop_reason == "max_tokens"
               else "empty_answer" if resp.stop_reason != "tool_use" and not _text_of(resp)
               else None)
        if bad:
            log.error("navigator %s (stop_reason=%s); item fails", bad, resp.stop_reason)
            sess.failure = bad
            return "", messages, usage
        messages.append({"role": "assistant", "content": resp.content})
        turn += 1
        if resp.stop_reason != "tool_use":
            return _text_of(resp), messages, usage
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                out, err = _run_tool(sess, b.name, b.input or {})
                sess.tool_calls.append({"tool": b.name, "input": b.input, "error": err})
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": out, "is_error": err})
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


def verify_with_usage(client: anthropic.Anthropic, s: Settings, answer: str,
                      fetched: dict[int, str]) -> tuple[list[dict], dict[str, int], str | None]:
    claims = split_claims(answer)
    if not claims:
        return [], _empty_usage(), None
    page_block = "\n\n".join(f"=== PDF page {p} ===\n{t.strip()}" for p, t in sorted(fetched.items()))
    claim_block = "\n".join(
        f"{c['id']}. {c['text']}\n   cited pages: {c['pages'] or 'NONE'}"
        + ("" if all(p in fetched for p in c["pages"]) else "  (WARNING: cites a page that was never fetched)")
        for c in claims)
    with stage('pageindex.verify', as_type='generation', model=s.verifier_model) as observation:
        resp = client.messages.create(
            model=s.verifier_model, max_tokens=s.verifier_max_tokens, tools=[VERDICT_TOOL],
            tool_choice={"type": "tool", "name": "report_verdicts"},
            messages=[{"role": "user", "content": VERIFY_PROMPT.format(pages=page_block, claims=claim_block)}],
            timeout=s.verifier_timeout_s)
        record_response(observation, resp)
    v_usage = _extract_usage(resp)
    resp_model = getattr(resp, "model", None)
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
                       repaired=False, answer_pre_repair="", verifier_models=verifier_models)
    answer_pre_repair = answer
    verifier_client = AnthropicRetryClient(
        anthropic.Anthropic(max_retries=0, timeout=s.verifier_timeout_s),
        ledger=ledger, usage_log=usage_log, lane="verifier",
        timeout_s=s.verifier_timeout_s, default_max_tokens=s.verifier_max_tokens,
    )
    verdicts, vu1, vm1 = verify_with_usage(verifier_client, s, answer, sess.fetched)
    _add_usage(u_ver, vu1)
    if vm1:
        verifier_models.append(vm1)
    repaired = False
    dropped_sentences: list[str] = []

    bad = [v for v in verdicts if v["verdict"] in FAIL]
    if bad:  # one repair round: tell the navigator exactly what failed
        repaired = True
        feedback = "Citation check failed for these sentences:\n" + "\n".join(
            f"- \"{v['text']}\" -> {v['verdict']}: {v['reason']}" for v in bad) + (
            "\nRewrite the full answer. Fix or remove each failing sentence. You may fetch more "
            "pages. Keep the same citation format.")
        prev_agent_retries = agent_client.retries
        answer, history, u2 = run_agent(agent_client, sess, question, extra_user_msg=feedback, prior=history)
        _add_usage(u_agent, u2)
        agent_turns += u2["turns"]
        sess.retries += (agent_client.retries - prev_agent_retries)
        if sess.failure:   # the repair round is an agent call too: same failure rule (R7)
            sess.retries += verifier_client.retries
            return _failed(question, rec, sess, _pack_usage(u_agent, u_ver, agent_turns), t0,
                           repaired=True, answer_pre_repair=answer_pre_repair, verifier_models=verifier_models)
        verdicts, vu2, vm2 = verify_with_usage(verifier_client, s, answer, sess.fetched)
        _add_usage(u_ver, vu2)
        if vm2:
            verifier_models.append(vm2)
        bad = [v for v in verdicts if v["verdict"] in FAIL]
        if bad:  # still failing: drop those sentences rather than ship unsupported claims
            dropped_sentences = [v["text"] for v in bad]
            keep = [v["text"] for v in verdicts if v["verdict"] not in FAIL]
            answer = " ".join(keep) + (
                f"\n\n[Note: {len(bad)} statement(s) removed because they could not be "
                "verified against the cited pages.]")
    sess.retries += verifier_client.retries

    # R7: every cited page must be in the fetched set. verify() already marks such claims
    # unsupported; record them and never let one reach cited_pages.
    cited_not_fetched = sorted({p for v in verdicts for p in v["pages"] if p not in sess.fetched})
    cited = sorted({p for v in verdicts if v["verdict"] not in FAIL for p in v["pages"]
                    if p in sess.fetched})
    return {
        "question": question, "doc_id": rec.doc_id, "answer": answer,
        "answer_pre_repair": answer_pre_repair, "status": "ok",
        "cited_pages": cited, "cited_not_fetched": cited_not_fetched,
        "fetched_pages": sorted(sess.fetched),
        "verdicts": verdicts, "repaired": repaired, "dropped_sentences": dropped_sentences,
        "tool_calls": sess.tool_calls, "usage": _pack_usage(u_agent, u_ver, agent_turns),
        "models": {"agent": list(dict.fromkeys(sess.agent_models)),
                   "verifier": list(dict.fromkeys(verifier_models))},
        "timeouts": {"agent_timeout_s": s.agent_timeout_s, "verifier_timeout_s": s.verifier_timeout_s},
        "retries": sess.retries, "latency_s": round(time.perf_counter() - t0, 2),
        # keep page texts for Ragas (R7): retrieved_contexts
        "contexts": [sess.fetched[p] for p in sorted(sess.fetched)],
    }


def _failed(question: str, rec, sess: Session, usage: dict, t0: float, repaired: bool,
            answer_pre_repair: str = "", verifier_models: list[str] | None = None) -> dict:
    """A navigator failure is a SYSTEM OUTCOME (R7): no answer, nothing cited; the
    evaluation keeps the item and scores it as a miss (eval_pageindex.py)."""
    return {
        "question": question, "doc_id": rec.doc_id, "answer": "",
        "answer_pre_repair": answer_pre_repair,
        "status": f"failed:{sess.failure}",
        "cited_pages": [], "cited_not_fetched": [], "fetched_pages": sorted(sess.fetched),
        "verdicts": [], "repaired": repaired, "dropped_sentences": [],
        "tool_calls": sess.tool_calls, "usage": usage,
        "models": {"agent": list(dict.fromkeys(sess.agent_models)),
                   "verifier": list(dict.fromkeys(verifier_models or []))},
        "timeouts": {"agent_timeout_s": sess.s.agent_timeout_s, "verifier_timeout_s": sess.s.verifier_timeout_s},
        "retries": sess.retries, "latency_s": round(time.perf_counter() - t0, 2),
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
