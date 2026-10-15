"""Online stage: resolve edition -> load tree -> Claude Sonnet navigates with tools ->
answer with page citations -> Claude Haiku verifies each claim against fetched text.

Usage:
  python pi_agent.py --family cdsco-gdp "What are the temperature requirements for storage?"
  python pi_agent.py --family cdsco-gdp --as-of 2020-06-01 "..."
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import anthropic
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from observability.tracing import stage, traced, record_response

from pi_common import (Settings, fetch_pages, load_manifest, load_settings, load_tree,
                       node_map, outline, parse_page_spec, resolve_edition, subtree_span)

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


def run_agent(client: anthropic.Anthropic, sess: Session, question: str,
              extra_user_msg: str | None = None, prior: list | None = None) -> tuple[str, list, dict]:
    s = sess.s
    system = SYSTEM_TMPL.format(
        title=sess.rec.title, version=sess.rec.version, pages=sess.rec.page_count,
        description=sess.rec.doc_description or "(none)", max_total=s.max_pages_total,
        outline=outline(sess.tree["structure"], max_depth=2, char_budget=s.overview_char_budget))
    messages = prior or [{"role": "user", "content": question}]
    if extra_user_msg:
        messages.append({"role": "user", "content": extra_user_msg})
    usage = {"input_tokens": 0, "output_tokens": 0, "turns": 0}

    for turn in range(s.agent_max_turns + 1):
        last = turn == s.agent_max_turns
        with stage('pageindex.navigate', as_type='generation', model=s.agent_model) as observation:
            resp = client.messages.create(
                model=s.agent_model, max_tokens=4096, system=system, tools=TOOLS,
                tool_choice={"type": "none"} if last else {"type": "auto"},
                messages=messages)
            record_response(observation, resp)
        usage["input_tokens"] += resp.usage.input_tokens
        usage["output_tokens"] += resp.usage.output_tokens
        usage["turns"] += 1
        messages.append({"role": "assistant", "content": resp.content})
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
    return _text_of(resp), messages, usage


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


def verify(client: anthropic.Anthropic, s: Settings, answer: str,
           fetched: dict[int, str]) -> list[dict]:
    claims = split_claims(answer)
    if not claims:
        return []
    page_block = "\n\n".join(f"=== PDF page {p} ===\n{t.strip()}" for p, t in sorted(fetched.items()))
    claim_block = "\n".join(
        f"{c['id']}. {c['text']}\n   cited pages: {c['pages'] or 'NONE'}"
        + ("" if all(p in fetched for p in c["pages"]) else "  (WARNING: cites a page that was never fetched)")
        for c in claims)
    with stage('pageindex.verify', as_type='generation', model=s.verifier_model) as observation:
        resp = client.messages.create(
            model=s.verifier_model, max_tokens=4096, tools=[VERDICT_TOOL],
            tool_choice={"type": "tool", "name": "report_verdicts"},
            messages=[{"role": "user", "content": VERIFY_PROMPT.format(pages=page_block, claims=claim_block)}])
        record_response(observation, resp)
    block = next(b for b in resp.content if b.type == "tool_use")
    by_id = {v["claim_id"]: v for v in block.input.get("verdicts", [])}
    out = []
    for c in claims:
        v = dict(by_id.get(c["id"], {"verdict": "unsupported", "evidence": "",
                                      "reason": "verifier returned no verdict"}))
        # deterministic guards on top of the LLM judge
        if any(p not in fetched for p in c["pages"]):
            v["verdict"], v["reason"] = "unsupported", "cites a page that was never fetched"
        elif v["verdict"] == "supported" and v.get("evidence"):
            cited_text = _norm(" ".join(fetched[p] for p in c["pages"]))
            if _norm(v["evidence"])[:80] not in cited_text:
                v["verdict"] = "partially_supported"
                v["reason"] += " | evidence quote not found verbatim on cited pages"
        out.append({**c, **v})
    return out


FAIL = {"unsupported", "partially_supported", "needs_citation"}


@traced('pageindex.agent', as_type='agent')
def answer_question(question: str, family: str, version: str | None = None,
                    as_of: date | None = None, s: Settings | None = None) -> dict:
    s = s or load_settings()
    t0 = time.perf_counter()
    rec = resolve_edition(load_manifest(s), family, version, as_of)
    tree = load_tree(rec.tree_path)
    sess = Session(rec=rec, tree=tree, nodes=node_map(tree["structure"]), s=s)
    client = anthropic.Anthropic()

    answer, history, usage = run_agent(client, sess, question)
    verdicts = verify(client, s, answer, sess.fetched)
    repaired = False

    bad = [v for v in verdicts if v["verdict"] in FAIL]
    if bad:  # one repair round: tell the navigator exactly what failed
        repaired = True
        feedback = "Citation check failed for these sentences:\n" + "\n".join(
            f"- \"{v['text']}\" -> {v['verdict']}: {v['reason']}" for v in bad) + (
            "\nRewrite the full answer. Fix or remove each failing sentence. You may fetch more "
            "pages. Keep the same citation format.")
        answer, history, u2 = run_agent(client, sess, question, extra_user_msg=feedback, prior=history)
        for k in usage:
            usage[k] += u2[k]
        verdicts = verify(client, s, answer, sess.fetched)
        bad = [v for v in verdicts if v["verdict"] in FAIL]
        if bad:  # still failing: drop those sentences rather than ship unsupported claims
            keep = [v["text"] for v in verdicts if v["verdict"] not in FAIL]
            answer = " ".join(keep) + (
                f"\n\n[Note: {len(bad)} statement(s) removed because they could not be "
                "verified against the cited pages.]")

    cited = sorted({p for v in verdicts if v["verdict"] not in FAIL for p in v["pages"]})
    return {
        "question": question, "doc_id": rec.doc_id, "answer": answer,
        "cited_pages": cited, "fetched_pages": sorted(sess.fetched),
        "verdicts": verdicts, "repaired": repaired, "tool_calls": sess.tool_calls,
        "usage": usage, "latency_s": round(time.perf_counter() - t0, 2),
        # keep page texts for Ragas (R7): retrieved_contexts
        "contexts": [sess.fetched[p] for p in sorted(sess.fetched)],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--family", required=True)
    ap.add_argument("--version")
    ap.add_argument("--as-of", type=date.fromisoformat)
    ap.add_argument("--json", action="store_true", help="print full result JSON")
    a = ap.parse_args()
    r = answer_question(a.question, a.family, a.version, a.as_of)
    if a.json:
        print(json.dumps({k: v for k, v in r.items() if k != "contexts"}, indent=2, default=str))
    else:
        print(r["answer"])
        print(f"\n[{r['doc_id']}] fetched={r['fetched_pages']} cited={r['cited_pages']} "
              f"repaired={r['repaired']} latency={r['latency_s']}s", file=sys.stderr)
