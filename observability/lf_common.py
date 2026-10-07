"""Shared Langfuse setup for the RegClaw / PageIndex rebuild.

Verified against langfuse==4.17.0 (PyPI, released 2026-10-05).

Env vars (.env):
    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY
    LANGFUSE_BASE_URL   <- v4 name. LANGFUSE_HOST still works but is deprecated.
    LANGFUSE_SAMPLE_RATE (optional, default 1.0 -- keep 1.0 for evaluation runs)
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Optional

from dotenv import load_dotenv

load_dotenv()

# Backward compat with the older env.example, which used LANGFUSE_HOST.
if not os.getenv("LANGFUSE_BASE_URL") and os.getenv("LANGFUSE_HOST"):
    os.environ["LANGFUSE_BASE_URL"] = os.environ["LANGFUSE_HOST"]

from langfuse import get_client, propagate_attributes  # noqa: E402

langfuse = get_client()  # singleton, configured from env


@contextmanager
def traced_task(
    name: str,
    *,
    session_id: str,
    agent: str,
    user_id: str = "rebuild",
    tags: Optional[list[str]] = None,
    input: object = None,
) -> Iterator[dict]:
    """One agent task = one trace with an `agent`-typed root observation.

    Why a custom root?
      * Task completion rate needs a per-task outcome. We write it as
        root metadata {"task_status": "completed" | "incomplete" | "failed"},
        which run_summary.py reads back from Observations API v2.
      * propagate_attributes() must be entered as early as possible so that
        session_id / tags reach EVERY child observation (v4 data model).

    Usage:
        with traced_task("literature-agent", session_id=sid, agent="literature") as task:
            ...
            task["output"] = answer      # mark as completed
    """
    with langfuse.start_as_current_observation(
        as_type="agent", name=name, input=input
    ) as root:
        with propagate_attributes(
            trace_name=name,
            session_id=session_id,
            user_id=user_id,
            tags=tags or [],
            metadata={"agent": agent},  # v4: dict[str, str], values <= 200 chars
        ):
            task: dict = {"output": None}
            try:
                yield task
            except Exception as e:
                root.update(
                    level="ERROR",
                    status_message=(str(e) or type(e).__name__)[:500],
                    metadata={"agent": agent, "task_status": "failed"},
                )
                raise
            else:
                done = task["output"] is not None
                root.update(
                    output=task["output"],
                    metadata={
                        "agent": agent,
                        "task_status": "completed" if done else "incomplete",
                    },
                )
