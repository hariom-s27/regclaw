"""(B3) Raw Anthropic SDK tool-use loop traced with Langfuse v4 -- no LangChain.

pip install "langfuse>=4.7" anthropic python-dotenv

Approach: manual GENERATION + TOOL observations (default).
  * Langfuse's docs recommend opentelemetry-instrumentation-anthropic for plain
    Anthropic calls. It captures model + usage automatically.
  * For an agent loop we want three things it does not give us explicitly:
    TOOL-typed observations (for tool error rate), a completion_start_time
    set from the first streamed event (for TTFT), and one observation per
    loop turn. So we instrument manually.
  * Set USE_OTEL_INSTRUMENTOR=1 to use the instrumentor instead. NEVER run
    both: each LLM call would be logged twice and model-call/token counts double.
"""
import datetime as dt
import os
import uuid

import anthropic
from langfuse import observe

from lf_common import langfuse, traced_task

MODEL = os.getenv("AGENT_MODEL", "claude-haiku-4-5")
USE_OTEL = os.getenv("USE_OTEL_INSTRUMENTOR") == "1"

if USE_OTEL:
    # pip install opentelemetry-instrumentation-anthropic
    from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor

    AnthropicInstrumentor().instrument()

client = anthropic.Anthropic()

TOOLS = [
    {
        "name": "fetch_pages",
        "description": "Fetch text of PDF pages start..end (1-based, inclusive).",
        "input_schema": {
            "type": "object",
            "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}},
            "required": ["start", "end"],
        },
    }
]


@observe(as_type="tool", name="fetch_pages")
def fetch_pages(start: int, end: int) -> str:
    # Any exception raised here is recorded by @observe as level="ERROR"
    # (verified in langfuse 4.17.0 _client/observe.py) -> feeds tool error rate.
    if end < start:
        raise ValueError(f"bad page range {start}-{end}")
    return f"(stub text of pages {start}-{end})"


def usage_dict(u) -> dict[str, int]:
    """Anthropic usage -> Langfuse usage_details.

    Anthropic already reports input_tokens EXCLUSIVE of cache reads/writes, which
    matches Langfuse's "mutually exclusive buckets" rule, so no subtraction needed.
    """
    d = {"input": u.input_tokens, "output": u.output_tokens}
    if getattr(u, "cache_read_input_tokens", None):
        d["cache_read_input_tokens"] = u.cache_read_input_tokens
    if getattr(u, "cache_creation_input_tokens", None):
        d["cache_creation_input_tokens"] = u.cache_creation_input_tokens
    return d


def call_claude(messages: list, turn: int):
    """One streamed model call. Streaming is what makes TTFT measurable."""
    kwargs = dict(model=MODEL, max_tokens=1024, tools=TOOLS, messages=messages)
    if USE_OTEL:
        return client.messages.create(**kwargs)  # instrumentor logs the generation

    with langfuse.start_as_current_observation(
        as_type="generation",
        name=f"claude-turn-{turn}",
        model=MODEL,
        input=messages,
        model_parameters={"max_tokens": 1024},
    ) as gen:
        first_token_at = None
        with client.messages.stream(**kwargs) as stream:
            for event in stream:
                if first_token_at is None and event.type == "content_block_delta":
                    first_token_at = dt.datetime.now(dt.timezone.utc)
            final = stream.get_final_message()
        gen.update(
            output=[b.model_dump() for b in final.content],
            usage_details=usage_dict(final.usage),
            completion_start_time=first_token_at,  # Langfuse TTFT = this - start_time
            metadata={"stop_reason": final.stop_reason},
        )
        return final


def run(question: str, session_id: str, max_turns: int = 6) -> str:
    with traced_task(
        "pageindex-navigator",
        session_id=session_id,
        agent="pageindex",
        tags=["anthropic-sdk"],
        input={"question": question},
    ) as task:
        messages = [{"role": "user", "content": question}]
        for turn in range(1, max_turns + 1):
            resp = call_claude(messages, turn)
            messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason != "tool_use":
                task["output"] = "".join(
                    b.text for b in resp.content if b.type == "text"
                )
                return task["output"]
            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                try:
                    out, is_err = fetch_pages(**block.input), False
                except Exception as e:  # already marked ERROR by @observe
                    out, is_err = f"tool error: {e}", True
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": out,
                        "is_error": is_err,
                    }
                )
            messages.append({"role": "user", "content": results})
        return None  # hit max_turns -> task_status = "incomplete"


if __name__ == "__main__":
    sid = f"rebuild-{uuid.uuid4().hex[:8]}"
    print(run("What storage temperature does the guideline require?", sid))
    print("session:", sid)
    langfuse.flush()
