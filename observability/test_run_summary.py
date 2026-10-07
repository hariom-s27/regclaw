"""Offline test of the summary math with hand-built observation rows (no API keys).

Run: python test_run_summary.py
"""
from run_summary import percentile, summarize, render


def obs(i, trace, typ, *, root=False, parent=None, level="DEFAULT", latency=None,
        ttft=None, usage=None, cost=None, meta=None):
    return {"id": i, "trace_id": trace, "type": typ, "is_root_observation": root,
            "parent_observation_id": parent, "level": level, "latency": latency,
            "time_to_first_token": ttft, "usage_details": usage, "total_cost": cost,
            "metadata": meta}


rows = [
    # trace A: completed, 2 model calls, 1 tool call
    obs("a0", "A", "AGENT", root=True, latency=4.0, meta={"agent": "literature", "task_status": "completed"}),
    obs("a1", "A", "GENERATION", parent="a0", latency=1.5, ttft=0.4, usage={"input": 100, "output": 20}, cost=0.001),
    obs("a2", "A", "TOOL", parent="a0", latency=0.3),
    obs("a3", "A", "GENERATION", parent="a0", latency=2.0, ttft=0.6, usage={"input": 300, "output": 50, "total": 350}, cost=0.002),
    # trace B: tool error, agent recovered and completed
    obs("b0", "B", "AGENT", root=True, latency=3.0, meta={"agent": "pageindex", "task_status": "completed"}),
    obs("b1", "B", "GENERATION", parent="b0", latency=1.0, usage={"input": 80, "output": 10}),
    obs("b2", "B", "TOOL", parent="b0", level="ERROR"),
    # trace C: failed
    obs("c0", "C", "AGENT", root=True, level="ERROR", latency=1.0, meta={"agent": "pageindex", "task_status": "failed"}),
    obs("c1", "C", "GENERATION", parent="c0", latency=0.9, usage={"input": 50, "output": 0}),
]

s = summarize(rows, window_s=600)
assert s.traces == 3
assert s.model_calls == 4 and s.tool_calls == 2 and s.tool_errors == 1
assert s.total_tokens == 120 + 350 + 90 + 50
assert s.completed_tasks == 2 and s.failed_executions == 1
assert s.error_observations == 2
assert abs(s.total_cost_usd - 0.003) < 1e-9
assert percentile([4.0, 3.0, 1.0], 95) == 4.0 and percentile([4.0, 3.0, 1.0], 50) == 3.0
assert summarize(rows, 600, agent="pageindex").traces == 2
print(render(s))
print("\nall assertions passed")
