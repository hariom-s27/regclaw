"""S11 Ragas input builder, empty-context & validity rules, and RecordingRagasLLM JSONL proxy."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import contextvars
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any, Callable, TypeVar

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "pageindex") not in sys.path:
    sys.path.insert(0, str(ROOT / "pageindex"))

# Ragas otherwise posts usage events to its own server outside our wrapper.
os.environ["RAGAS_DO_NOT_TRACK"] = "true"

from ragas.embeddings.base import BaseRagasEmbedding, embedding_factory  # noqa: E402
from ragas.llms import llm_factory  # noqa: E402
from ragas.llms.base import InstructorBaseRagasLLM  # noqa: E402
from ragas.metrics.collections import AnswerCorrectness, AnswerRelevancy, Faithfulness  # noqa: E402

import eval.eval_pageindex as eval_pageindex  # noqa: E402
from eval.s11.ledger import WriteOnceError, now_utc_iso  # noqa: E402
from eval.s11.metrics import CITE_BRACKET_RE, METRICS_RULES_VERSION  # noqa: E402
from pi_agent import refusal_detected  # noqa: E402
from pi_client import ModelVersionGuard  # noqa: E402
from pi_common import JUDGE_TIMEOUT_S, check_deployment_names  # noqa: E402

T = TypeVar("T")

RAGAS_METRICS = ("faithfulness", "answer_relevancy", "answer_correctness")
WD_FAITHFULNESS_LABEL = "not like-for-like (whole document as context)"

RAGAS_INPUT_KEYS = (
    "rules_version",
    "run_id",
    "system",
    "question_id",
    "user_input",
    "response",
    "retrieved_contexts",
    "kept_pages",
    "dropped_empty_pages",
    "reference",
    "input_sha256",
)

RECORDER_FIELDS = (
    "run_id",
    "system",
    "question_id",
    "repeat",
    "metric",
    "response_model",
    "output",
    "timestamp",
)

_SPACE_BEFORE_PUNCT_RE = re.compile(r"[ \t]+([.,;:])")
_HSPACE_RUN_RE = re.compile(r"[ \t]+")
_SCOPE_VAR: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "s11_ragas_recorder_scope", default={}
)


def _require_output_path(path: Path | str | None, param_name: str) -> Path:
    if path is None or not str(path).strip():
        raise ValueError(f"{param_name} is required and cannot be empty or None")
    return Path(path)


def strip_citations(text: str) -> str:
    """Remove valid/malformed citations, collapse horizontal spaces per line, keep non-empty lines."""
    lines: list[str] = []
    for raw_line in (text or "").splitlines():
        line = CITE_BRACKET_RE.sub(" ", raw_line)
        line = _HSPACE_RUN_RE.sub(" ", line).strip()
        line = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def compute_ragas_input_sha256(
    *,
    user_input: str,
    response: str,
    retrieved_contexts: list[str],
    reference: str,
) -> str:
    """Compute canonical SHA-256 over the exact judged Ragas input fields."""
    payload = {
        "user_input": user_input,
        "response": response,
        "retrieved_contexts": retrieved_contexts,
        "reference": reference,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_ragas_input_row(
    record: dict[str, Any],
    golden_item: dict[str, Any],
    page_texts: dict[int, str],
    *,
    run_id: str | int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Assemble one Ragas input row or return an exclusion entry with its reason."""
    system = str(record["system"])
    qid = str(golden_item["id"])

    if system == "HY5":
        return None, {"question_id": qid, "system": system, "reason": "not_applicable_system"}
    if not bool(golden_item["answerable"]):
        return None, {"question_id": qid, "system": system, "reason": "unanswerable"}
    if str(record.get("status")) != "ok":
        return None, {"question_id": qid, "system": system, "reason": "failed_item"}

    answer = str(record.get("answer") or "")
    is_refusal = bool(record["refusal"]) if "refusal" in record else refusal_detected(answer)
    if is_refusal:
        return None, {"question_id": qid, "system": system, "reason": "refusal"}

    ordered_r = list(dict.fromkeys(int(p) for p in record.get("pages_given", [])))
    retrieved_contexts: list[str] = []
    kept_pages: list[int] = []
    dropped_empty_pages: list[int] = []

    for p in ordered_r:
        stripped = (page_texts.get(p) or "").strip()
        if stripped:
            retrieved_contexts.append(stripped)
            kept_pages.append(p)
        else:
            dropped_empty_pages.append(p)

    user_input = str(golden_item["question"])
    response = strip_citations(answer)
    reference = str(golden_item["reference"])
    sha = compute_ragas_input_sha256(
        user_input=user_input,
        response=response,
        retrieved_contexts=retrieved_contexts,
        reference=reference,
    )
    row = {
        "rules_version": METRICS_RULES_VERSION,
        "run_id": run_id,
        "system": system,
        "question_id": qid,
        "user_input": user_input,
        "response": response,
        "retrieved_contexts": retrieved_contexts,
        "kept_pages": kept_pages,
        "dropped_empty_pages": dropped_empty_pages,
        "reference": reference,
        "input_sha256": sha,
    }
    return row, None


def write_ragas_inputs_jsonl(rows: list[dict[str, Any]], out_jsonl: Path | str) -> Path:
    """Persist Ragas input rows to a required write-once JSONL file."""
    target = _require_output_path(out_jsonl, "out_jsonl")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    try:
        with open(target, "x", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
    except FileExistsError as exc:
        raise WriteOnceError(f"Refusing to overwrite existing write-once JSONL: {target}") from exc
    return target


def build_ragas_inputs(
    records: list[dict[str, Any]],
    golden_by_id: dict[str, dict[str, Any]],
    page_texts: dict[int, str],
    *,
    run_id: str | int,
    out_jsonl: Path | str | None = None,
) -> dict[str, Any]:
    """Filter included/excluded items, assemble Ragas input rows, and optionally write JSONL."""
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    excluded_by_reason: dict[str, int] = {
        "unanswerable": 0,
        "failed_item": 0,
        "refusal": 0,
    }

    for rec in records:
        qid = str(rec["question_id"])
        golden_item = golden_by_id[qid]
        row, exc = build_ragas_input_row(rec, golden_item, page_texts, run_id=run_id)
        if row is not None:
            included.append(row)
        elif exc is not None:
            excluded.append(exc)
            reason = exc["reason"]
            excluded_by_reason[reason] = excluded_by_reason.get(reason, 0) + 1

    written_path: Path | None = None
    if out_jsonl is not None:
        written_path = write_ragas_inputs_jsonl(included, out_jsonl)

    return {
        "included": included,
        "excluded": excluded,
        "excluded_by_reason": excluded_by_reason,
        "out_jsonl": str(written_path) if written_path is not None else None,
    }


class _JsonlWriteOnceSink:
    """Thread-safe write-once JSONL sink created exclusively at init."""

    def __init__(self, path: Path | str) -> None:
        target = _require_output_path(path, "record_path")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(target, "x", encoding="utf-8", newline="\n") as fh:
                fh.flush()
                os.fsync(fh.fileno())
        except FileExistsError as exc:
            raise WriteOnceError(f"Refusing to overwrite existing write-once recorder JSONL: {target}") from exc
        self.path = target
        self._lock = threading.Lock()
        self.rows: list[dict[str, Any]] = []

    def append(self, row: dict[str, Any]) -> None:
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with self._lock:
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            self.rows.append(row)


class RecordingRagasLLM(InstructorBaseRagasLLM):
    """InstructorBaseRagasLLM proxy forwarding unchanged while logging structured judge outputs."""

    def __init__(
        self,
        inner: InstructorBaseRagasLLM,
        record_path: Path | str,
        *,
        run_id: str | int = "",
        system: str = "",
        question_id: str = "",
        repeat: int = 1,
        metric: str = "",
        _sink: _JsonlWriteOnceSink | None = None,
    ) -> None:
        if not isinstance(inner, InstructorBaseRagasLLM):
            raise TypeError(f"inner must be InstructorBaseRagasLLM, got {type(inner).__name__}")
        self._inner = inner
        self._sink = _sink if _sink is not None else _JsonlWriteOnceSink(record_path)
        self.record_path = self._sink.path
        self._meta: dict[str, Any] = {
            "run_id": run_id,
            "system": system,
            "question_id": question_id,
            "repeat": int(repeat),
            "metric": metric,
        }

    @property
    def recorded_rows(self) -> list[dict[str, Any]]:
        return list(self._sink.rows)

    def bound(
        self,
        *,
        run_id: str | int | None = None,
        system: str | None = None,
        question_id: str | None = None,
        repeat: int | None = None,
        metric: str | None = None,
    ) -> "RecordingRagasLLM":
        """Return a proxy view sharing the same write-once sink with updated context defaults."""
        return RecordingRagasLLM(
            self._inner,
            self.record_path,
            run_id=self._meta["run_id"] if run_id is None else run_id,
            system=self._meta["system"] if system is None else system,
            question_id=self._meta["question_id"] if question_id is None else question_id,
            repeat=self._meta["repeat"] if repeat is None else int(repeat),
            metric=self._meta["metric"] if metric is None else metric,
            _sink=self._sink,
        )

    @contextmanager
    def scope(
        self,
        *,
        run_id: str | int | None = None,
        system: str | None = None,
        question_id: str | None = None,
        repeat: int | None = None,
        metric: str | None = None,
    ):
        """Temporarily bind context metadata for sync/async calls inside the current task."""
        current = dict(_SCOPE_VAR.get({}))
        updates = {
            "run_id": run_id,
            "system": system,
            "question_id": question_id,
            "repeat": None if repeat is None else int(repeat),
            "metric": metric,
        }
        for key, val in updates.items():
            if val is not None:
                current[key] = val
        token = _SCOPE_VAR.set(current)
        try:
            yield self
        finally:
            _SCOPE_VAR.reset(token)

    def _effective_meta(self) -> dict[str, Any]:
        scoped = _SCOPE_VAR.get({})
        merged = dict(self._meta)
        for key in ("run_id", "system", "question_id", "repeat", "metric"):
            if scoped.get(key) is not None:
                merged[key] = scoped[key]
        return merged

    def _record_output(self, response_model: Any, result: Any) -> None:
        meta = self._effective_meta()
        model_name = getattr(response_model, "__name__", None) or type(result).__name__
        output_data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        row = {
            "run_id": meta["run_id"],
            "system": meta["system"],
            "question_id": meta["question_id"],
            "repeat": int(meta["repeat"]),
            "metric": meta["metric"],
            "response_model": str(model_name),
            "output": output_data,
            "timestamp": now_utc_iso(),
        }
        self._sink.append(row)

    def generate(self, prompt: str, response_model: type[T]) -> T:
        result = self._inner.generate(prompt, response_model)
        self._record_output(response_model, result)
        return result

    async def agenerate(self, prompt: str, response_model: type[T]) -> T:
        result = await self._inner.agenerate(prompt, response_model)
        self._record_output(response_model, result)
        return result


def derive_judge_flags(
    recorded_rows: list[dict[str, Any]],
    *,
    zero_no_context: bool = False,
) -> dict[str, bool]:
    """Derive per-item Ragas diagnostic flags from recorded judge outputs."""
    faith_no_statements = any(
        row.get("metric") == "faithfulness"
        and row.get("response_model") == "StatementGeneratorOutput"
        and len((row.get("output") or {}).get("statements") or []) == 0
        for row in recorded_rows
    )
    corr_no_statements = any(
        row.get("metric") == "answer_correctness"
        and row.get("response_model") == "StatementGeneratorOutput"
        and len((row.get("output") or {}).get("statements") or []) == 0
        for row in recorded_rows
    )

    rel_outputs = [
        row.get("output") or {}
        for row in recorded_rows
        if row.get("metric") == "answer_relevancy"
        and row.get("response_model") == "AnswerRelevanceOutput"
    ]
    non_empty_rel = [out for out in rel_outputs if out.get("question")]
    rel_no_questions = bool(rel_outputs) and len(non_empty_rel) == 0
    rel_all_noncommittal = bool(non_empty_rel) and all(bool(out.get("noncommittal")) for out in non_empty_rel)

    return {
        "faith_zero_no_context": bool(zero_no_context),
        "faith_no_statements": faith_no_statements,
        "corr_no_statements": corr_no_statements,
        "rel_no_questions": rel_no_questions,
        "rel_all_noncommittal": rel_all_noncommittal,
    }


def build_s11_ragas_metrics(
    judge: str | None = None,
    *,
    embedding_model: str = "text-embedding-3-small",
    client: Any = None,
    timeout: float | None = None,
    usage_log: Any = None,
    guard: ModelVersionGuard | None = None,
    llm_override: InstructorBaseRagasLLM | None = None,
    emb_override: BaseRagasEmbedding | None = None,
    recorder_path: Path | str | None = None,
    run_id: str | int = "",
    system: str = "",
) -> tuple[dict[str, tuple[Any, Callable[[dict[str, Any]], dict[str, Any]]]], RecordingRagasLLM | None]:
    """Build Ragas 0.4.3 collection metrics reusing eval_pageindex judge settings and optional proxy."""
    if llm_override is None or emb_override is None:
        if not judge:
            raise ValueError("judge deployment is required unless llm_override and emb_override are provided")
        check_deployment_names({"judge": judge, "embedding_model": embedding_model})

    effective_timeout = JUDGE_TIMEOUT_S if timeout is None else timeout
    if llm_override is None:
        if client is None:
            client = eval_pageindex.azure_async_client(
                timeout=effective_timeout,
                usage_log=usage_log,
                guard=guard,
            )
        llm = llm_factory(judge, client=client, **eval_pageindex.judge_kwargs(judge))
    else:
        llm = llm_override

    if emb_override is None:
        if client is None:
            client = eval_pageindex.azure_async_client(
                timeout=effective_timeout,
                usage_log=usage_log,
                guard=guard,
            )
        emb = embedding_factory("openai", model=embedding_model, client=client)
    else:
        emb = emb_override

    recorder: RecordingRagasLLM | None = None
    if recorder_path is not None:
        recorder = RecordingRagasLLM(llm, recorder_path, run_id=run_id, system=system)
        faith_llm: InstructorBaseRagasLLM = recorder.bound(metric="faithfulness")
        rel_llm: InstructorBaseRagasLLM = recorder.bound(metric="answer_relevancy")
        corr_llm: InstructorBaseRagasLLM = recorder.bound(metric="answer_correctness")
    else:
        faith_llm = llm
        rel_llm = llm
        corr_llm = llm

    metrics_map = {
        "faithfulness": (
            Faithfulness(llm=faith_llm),
            lambda row: {
                "user_input": row["user_input"],
                "response": row["response"],
                "retrieved_contexts": row["retrieved_contexts"],
            },
        ),
        "answer_relevancy": (
            AnswerRelevancy(llm=rel_llm, embeddings=emb),
            lambda row: {
                "user_input": row["user_input"],
                "response": row["response"],
            },
        ),
        "answer_correctness": (
            AnswerCorrectness(llm=corr_llm, embeddings=emb),
            lambda row: {
                "user_input": row["user_input"],
                "response": row["response"],
                "reference": row["reference"],
            },
        ),
    }
    return metrics_map, recorder


async def _run_one_metric(
    name: str,
    metric_obj: Any,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    try:
        raw_res = await metric_obj.ascore(**kwargs)
        raw_val = getattr(raw_res, "value", raw_res)
    except Exception as exc:
        return {"value": None, "is_nan": False, "error_type": type(exc).__name__}

    if raw_val is None:
        return {"value": None, "is_nan": False, "error_type": "NoneValueError"}
    try:
        val = float(raw_val)
    except (TypeError, ValueError) as exc:
        return {"value": None, "is_nan": False, "error_type": type(exc).__name__}
    if math.isnan(val):
        return {"value": None, "is_nan": True, "error_type": None}
    if not math.isfinite(val):
        return {"value": None, "is_nan": False, "error_type": "OverflowError"}
    return {"value": val, "is_nan": False, "error_type": None}


async def score_s11_item(
    input_row: dict[str, Any],
    metrics_map: dict[str, tuple[Any, Callable[[dict[str, Any]], dict[str, Any]]]],
    *,
    repeat: int,
    recorder: RecordingRagasLLM | None = None,
) -> dict[str, Any]:
    """Score one included Ragas input row across all three metrics for one repeat."""
    qid = str(input_row["question_id"])
    system = str(input_row["system"])
    run_id = input_row["run_id"]
    zero_no_context = len(input_row["retrieved_contexts"]) == 0

    start_idx = len(recorder._sink.rows) if recorder is not None else 0

    async def _score_all() -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        for name in RAGAS_METRICS:
            metric_obj, kw_fn = metrics_map[name]
            if name == "faithfulness" and zero_no_context:
                results[name] = {"value": 0.0, "is_nan": False, "error_type": None}
                continue
            tasks[name] = asyncio.create_task(_run_one_metric(name, metric_obj, kw_fn(input_row)))
        for name, task in tasks.items():
            results[name] = await task
        return results

    if recorder is not None:
        with recorder.scope(run_id=run_id, system=system, question_id=qid, repeat=repeat):
            scores = await _score_all()
        item_recorded = [
            row
            for row in recorder._sink.rows[start_idx:]
            if row.get("run_id") == run_id
            and row.get("system") == system
            and row.get("question_id") == qid
            and int(row.get("repeat", 0)) == int(repeat)
        ]
    else:
        scores = await _score_all()
        item_recorded = []

    flags = derive_judge_flags(item_recorded, zero_no_context=zero_no_context)
    return {
        "run_id": run_id,
        "system": system,
        "question_id": qid,
        "repeat": int(repeat),
        "input_sha256": input_row["input_sha256"],
        "metrics": scores,
        "flags": flags,
    }


def _summarize_metric_runs(
    metric_name: str,
    item_rows: list[dict[str, Any]],
    *,
    included_count: int,
    excluded_count: int,
    excluded_by_reason: dict[str, int],
    system: str,
) -> dict[str, Any]:
    entries = [row["metrics"][metric_name] for row in item_rows]
    nan_count = sum(1 for e in entries if e["is_nan"])
    error_count = sum(1 for e in entries if e["error_type"] is not None)
    zero_no_ctx_count = (
        sum(1 for row in item_rows if row["flags"]["faith_zero_no_context"])
        if metric_name == "faithfulness"
        else 0
    )

    if included_count == 0:
        valid = False
        valid_reason: str | None = "no_included_items"
        mean_val: float | None = None
    elif nan_count > 0 or error_count > 0 or any(
        e["value"] is None or not math.isfinite(e["value"]) for e in entries
    ):
        valid = False
        valid_reason = "nan_or_error"
        mean_val = None
    else:
        valid = True
        valid_reason = None
        vals = [float(e["value"]) for e in entries]
        mean_val = sum(vals) / len(vals)

    summary: dict[str, Any] = {
        "valid": valid,
        "valid_reason": valid_reason,
        "mean": mean_val,
        "counts": {
            "included": included_count,
            "excluded": excluded_count,
            "excluded_by_reason": dict(excluded_by_reason),
            "nan": nan_count,
            "errors": error_count,
            "zero_no_context": zero_no_ctx_count,
        },
    }
    if system == "WD" and metric_name == "faithfulness":
        summary["label"] = WD_FAITHFULNESS_LABEL
    return summary


def evaluate_ragas_run(
    records: list[dict[str, Any]],
    golden_by_id: dict[str, dict[str, Any]],
    page_texts: dict[int, str],
    *,
    run_id: str | int,
    system: str,
    inputs_jsonl: Path | str,
    recorder_path: Path | str | None = None,
    use_proxy: bool = True,
    judge: str | None = None,
    embedding_model: str = "text-embedding-3-small",
    repeats: int = 3,
    client: Any = None,
    timeout: float | None = None,
    usage_log: Any = None,
    guard: ModelVersionGuard | None = None,
    llm_override: InstructorBaseRagasLLM | None = None,
    emb_override: BaseRagasEmbedding | None = None,
) -> dict[str, Any]:
    """Assemble write-once Ragas inputs and score all included items across repeats."""
    if int(repeats) < 1:
        raise ValueError(f"repeats must be >= 1, got {repeats!r}")
    _require_output_path(inputs_jsonl, "inputs_jsonl")
    if use_proxy:
        _require_output_path(recorder_path, "recorder_path")
    elif recorder_path is not None:
        raise ValueError("recorder_path must be None when use_proxy=False")

    assembled = build_ragas_inputs(
        records,
        golden_by_id,
        page_texts,
        run_id=run_id,
        out_jsonl=inputs_jsonl,
    )
    included = assembled["included"]
    excluded = assembled["excluded"]
    excluded_by_reason = assembled["excluded_by_reason"]

    metrics_map, recorder = build_s11_ragas_metrics(
        judge,
        embedding_model=embedding_model,
        client=client,
        timeout=timeout,
        usage_log=usage_log,
        guard=guard,
        llm_override=llm_override,
        emb_override=emb_override,
        recorder_path=recorder_path if use_proxy else None,
        run_id=run_id,
        system=system,
    )

    async def _run_all() -> list[dict[str, Any]]:
        rows_out: list[dict[str, Any]] = []
        for repeat in range(1, int(repeats) + 1):
            for input_row in included:
                rows_out.append(await score_s11_item(input_row, metrics_map, repeat=repeat, recorder=recorder))
        return rows_out

    item_rows = asyncio.run(_run_all())
    metric_summaries = {
        name: _summarize_metric_runs(
            name,
            item_rows,
            included_count=len(included),
            excluded_count=len(excluded),
            excluded_by_reason=excluded_by_reason,
            system=system,
        )
        for name in RAGAS_METRICS
    }

    return {
        "rules_version": METRICS_RULES_VERSION,
        "run_id": run_id,
        "system": system,
        "repeats": int(repeats),
        "inputs_jsonl": assembled["out_jsonl"],
        "recorder_path": str(recorder.record_path) if recorder is not None else None,
        "included_count": len(included),
        "excluded": excluded,
        "excluded_by_reason": excluded_by_reason,
        "items": item_rows,
        "metrics": metric_summaries,
    }


__all__ = [
    "RAGAS_INPUT_KEYS",
    "RAGAS_METRICS",
    "RECORDER_FIELDS",
    "RecordingRagasLLM",
    "WD_FAITHFULNESS_LABEL",
    "build_ragas_input_row",
    "build_ragas_inputs",
    "build_s11_ragas_metrics",
    "compute_ragas_input_sha256",
    "derive_judge_flags",
    "evaluate_ragas_run",
    "score_s11_item",
    "strip_citations",
    "write_ragas_inputs_jsonl",
]
