"""(C) Fetch observations for a time window or session and print the run-level summary.

Uses Observations API v2 through the SDK: langfuse.api.observations.get_many(...)
(signature verified on langfuse==4.17.0). Needs a Langfuse v4 server
(Langfuse Cloud, or self-hosted v4). On self-hosted v3 use
langfuse.api.legacy.observations_v1 instead.

Examples:
    python run_summary.py --session rebuild-1a2b3c4d
    python run_summary.py --hours 6
    python run_summary.py --hours 24 --agent literature

Unit notes (from Langfuse docs / sample payloads):
    Observations v2 `latency` and `time_to_first_token` are in SECONDS.
    Metrics API latency / timeToFirstToken are in MILLISECONDS. Don't mix them.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import json
from pathlib import Path
import time
from collections import defaultdict
from dataclasses import dataclass, field

MODEL_TYPES = {"GENERATION", "EMBEDDING"}  # the types that carry usage/cost
TOOL_TYPES = {"TOOL"}
FIELDS = "core,basic,usage,metrics,metadata,model"


# ---------------------------------------------------------------- fetching
def fetch_observations(lf, start: dt.datetime, end: dt.datetime,
                       session_id: str | None = None, page_size: int = 1000,
                       pause_s: float = 2.1) -> list:
    """Cursor-paginate Observations API v2. Always time-bounded (docs requirement).

    pause_s keeps us under the Hobby limit of 30 requests/min on this endpoint.
    """
    rows, cursor = [], None
    while True:
        resp = lf.api.observations.get_many(
            fields=FIELDS,
            limit=page_size,
            cursor=cursor,
            session_id=session_id,
            from_start_time=start,
            to_start_time=end,
        )
        rows.extend(resp.data)
        cursor = resp.meta.cursor if resp.meta else None
        if not cursor:
            return rows
        time.sleep(pause_s)


# ---------------------------------------------------------------- metrics
def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (no interpolation). Langfuse's own server-side
    percentile may interpolate differently, so small mismatches are expected."""
    if not values:
        return None
    s = sorted(values)
    k = max(1, math.ceil(p / 100 * len(s)))
    return s[k - 1]


def _get(o, name, default=None):
    """Works for SDK objects (attribute access) and dicts (tests)."""
    if isinstance(o, dict):
        parts = name.split('_')
        camel = parts[0] + ''.join(p.title() for p in parts[1:])
        return o.get(name, o.get(camel, default))
    return getattr(o, name, default)


def timestamp(o, field):
    value = _get(o, field)
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value


def interval(o):
    start, end = timestamp(o, 'start_time'), timestamp(o, 'end_time')
    return (start, end) if start is not None and end is not None and end >= start else None


def duration(o):
    bounds = interval(o)
    return (bounds[1] - bounds[0]).total_seconds() if bounds else None


def exclusive(o, observations):
    """Subtract the union of direct-child intervals, clipped to the parent.

    Descendants are already contained in child time. Overlapping children must
    not be counted twice. An incomplete child makes self time unavailable.
    """
    bounds = interval(o)
    if bounds is None:
        return None
    start, end = bounds
    spans = []
    for child in observations:
        if _get(child, 'parent_observation_id') != _get(o, 'id'):
            continue
        child_bounds = interval(child)
        if child_bounds is None:
            return None
        a, b = max(start, child_bounds[0]), min(end, child_bounds[1])
        if b > a:
            spans.append((a, b))
    covered, cursor = 0., start
    for a, b in sorted(spans):
        if b > cursor:
            covered += (b - max(a, cursor)).total_seconds()
            cursor = b
    return (end-start).total_seconds() - covered


def fixture_rows(path):
    """Read one v2 response page, a list of pages, or a list of observations."""
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if isinstance(payload, dict):
        return payload['data']
    return [row for page in payload for row in page['data']] if payload and 'data' in payload[0] else payload


def _meta(o) -> dict:
    m = _get(o, "metadata")
    return m if isinstance(m, dict) else {}


def _level(o) -> str:
    lv = _get(o, "level")
    return str(getattr(lv, "value", lv) or "DEFAULT").upper()


def _tokens(o) -> int:
    u = _get(o, "usage_details") or {}
    return int(u.get("total", sum(v for k, v in u.items() if k != "total")))


@dataclass
class Summary:
    traces: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    error_observations: int = 0
    failed_executions: int = 0
    completed_tasks: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    trace_latencies_s: list = field(default_factory=list)
    gen_latencies_s: list = field(default_factory=list)
    ttft_s: list = field(default_factory=list)
    window_s: float = 0.0
    observation_latencies_s: dict = field(default_factory=lambda: defaultdict(list))
    provenance: list = field(default_factory=list)


def summarize(rows: list, window_s: float, agent: str | None = None) -> Summary:
    by_trace: dict[str, list] = defaultdict(list)
    for o in rows:
        by_trace[_get(o, "trace_id")].append(o)

    s = Summary(window_s=window_s)
    for trace_id, obs in by_trace.items():
        roots = [o for o in obs if _get(o, "is_root_observation")] or \
                [o for o in obs if not _get(o, "parent_observation_id")]
        root = roots[0] if roots else None
        if agent and _meta(root or {}).get("agent") != agent:
            continue

        s.traces += 1
        root_elapsed = duration(root or {})
        if root_elapsed is None:  # Compatibility for the original latency-only fixture.
            root_elapsed = _get(root or {}, 'latency')
        if root_elapsed is not None:
            s.trace_latencies_s.append(float(root_elapsed))

        trace_failed = False
        for o in obs:
            metadata = _meta(o)
            if metadata.get('provider'):
                identity = {k: metadata.get(k) for k in
                            ('provider', 'model', 'model_version', 'provisional', 'reasoning_effort')}
                if identity not in s.provenance:
                    s.provenance.append(identity)
            elapsed = duration(o)
            if elapsed is not None:
                s.observation_latencies_s[_get(o, 'name', '(unnamed)')].append(elapsed)
            if _get(o, 'name') == 'retriever':
                self_time = exclusive(o, obs)
                if self_time is not None:
                    s.observation_latencies_s['retriever.self'].append(self_time)
            t = str(_get(o, "type", "")).upper()
            is_err = _level(o) == "ERROR"
            s.error_observations += is_err
            trace_failed |= is_err
            if t in MODEL_TYPES:
                s.model_calls += 1
                s.total_tokens += _tokens(o)
                s.total_cost_usd += float(_get(o, "total_cost") or 0.0)
                elapsed = duration(o)
                if elapsed is None:
                    elapsed = _get(o, 'latency')
                if elapsed is not None:
                    s.gen_latencies_s.append(float(elapsed))
                if _get(o, "time_to_first_token") is not None:
                    s.ttft_s.append(float(_get(o, "time_to_first_token")))
            elif t in TOOL_TYPES:
                s.tool_calls += 1
                s.tool_errors += is_err

        status = _meta(root or {}).get("task_status")
        s.completed_tasks += status == "completed"
        # failed execution = root errored OR any observation errored and task not completed
        s.failed_executions += (status == "failed") or (trace_failed and status != "completed")
    return s


def render(s: Summary) -> str:
    def ms(x):
        return "n/a" if x is None else f"{x * 1000:,.0f} ms"

    def rate(a, b):
        return "n/a" if b == 0 else f"{a / b:.1%}"

    mean_ttft = sum(s.ttft_s) / len(s.ttft_s) if s.ttft_s else None
    per_min = s.traces / (s.window_s / 60) if s.window_s else 0
    lines = [
        ("Tasks (traces)", f"{s.traces}"),
        ("Task completion rate", rate(s.completed_tasks, s.traces)),
        ("Failed executions", f"{s.failed_executions}"),
        ("Total model calls", f"{s.model_calls}"),
        ("Total tool calls", f"{s.tool_calls}"),
        ("Tool error rate", rate(s.tool_errors, s.tool_calls)),
        ("Error observations", f"{s.error_observations}"),
        ("Total tokens", f"{s.total_tokens:,}"),
        ("Total cost (USD)", f"{s.total_cost_usd:.4f}"),
        ("Task latency P50", ms(percentile(s.trace_latencies_s, 50))),
        ("Task latency P95", ms(percentile(s.trace_latencies_s, 95))),
        ("Model-call latency P50", ms(percentile(s.gen_latencies_s, 50))),
        ("Model-call latency P95", ms(percentile(s.gen_latencies_s, 95))),
        ("Mean TTFT (streamed calls)", ms(mean_ttft) + f"  (n={len(s.ttft_s)})"),
        ("Throughput", f"{per_min:.2f} tasks/min over {s.window_s / 60:.0f} min window"),
    ]
    w = max(len(k) for k, _ in lines)
    output = '\n'.join(f'{k.ljust(w)} | {v}' for k, v in lines)
    output += '\n\nObservation | N | P50 | P95 (endTime - startTime)'
    for name, values in sorted(s.observation_latencies_s.items()):
        output += f'\n{name} | {len(values)} | {ms(percentile(values, 50))} | {ms(percentile(values, 95))}'
    output += '\nP95 from <100 runs is noisy. Parent and child durations are never summed.'
    if s.provenance:
        output += '\nProvenance: ' + json.dumps(s.provenance, sort_keys=True)
    return output


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--session")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--agent", help="filter on root metadata agent=<name>")
    ap.add_argument('--fixture', type=Path, help='Offline Observations API v2 JSON page(s).')
    a = ap.parse_args(argv)

    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(hours=a.hours)
    if a.fixture:
        rows = fixture_rows(a.fixture)
    else:
        from langfuse import get_client
        rows = fetch_observations(get_client(), start, end, session_id=a.session)
    window = (end - start).total_seconds()
    # For a session, measure throughput over the session's actual span.
    if a.session and rows:
        t0 = min(timestamp(o, 'start_time') for o in rows)
        t1 = max(timestamp(o, 'end_time') or timestamp(o, 'start_time') for o in rows)
        window = max((t1 - t0).total_seconds(), 1.0)
    print(f"Fetched {len(rows)} observations "
          f"({'session ' + a.session if a.session else f'last {a.hours}h'})\n")
    print(render(summarize(rows, window, agent=a.agent)))


if __name__ == "__main__":
    main()
