"""S11 evaluation run driver: dev pilot (K3 effort rule), main runs 1-3, HY5, resume, retries, and lock."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import random
import re
import sys
from typing import Any, Callable, Sequence

import anthropic
import httpx

try:
    import anthropic._constants as _anthropic_constants
except ImportError:  # pragma: no cover
    _anthropic_constants = None  # type: ignore[assignment]

try:
    from openai import APIConnectionError as _OpenAIConnectionError
except ImportError:  # pragma: no cover
    class _OpenAIConnectionError(Exception):  # type: ignore[no-redef]
        pass

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "pageindex") not in sys.path:
    sys.path.insert(0, str(ROOT / "pageindex"))

from eval.s11.answer import EXPECTED_REFUSAL, RESULT_KEYS, run_pi_answer  # noqa: E402
from eval.s11.freeze import FreezeError, check_manifest, validate_rel_path  # noqa: E402
from eval.s11.golden import (  # noqa: E402
    EXPECTED_DEV_IDS,
    EXPECTED_PDF_SHA256,
    EXPECTED_TEST_IDS,
    GoldenValidationError,
    load_frozen_golden,
)
from eval.s11.hybrid import (  # noqa: E402
    BM25_B,
    BM25_K1,
    HY5_K,
    HY_FALLBACK_K,
    HY_MAX_K,
    RRF_K,
    k_for,
    run_hy,
    run_hy5_retrieval,
)
from eval.s11.ledger import (  # noqa: E402
    CapExceededError,
    CostLedger,
    PaidLockHeld,
    UsageLogger,
    WriteOnceError,
    default_s11_paths,
    now_utc_iso,
    paid_lock,
    write_once_json,
)
from eval.s11.whole_doc import build_wd_document_meta, extract_all_pages, run_wd  # noqa: E402
from pi_client import (  # noqa: E402
    ModelVersionChangedError,
    ModelVersionGuard,
    _extract_status_code,
    _is_connection_exception,
    _is_timeout_exception,
)
from pi_common import (  # noqa: E402
    PAGEINDEX_PIN_SHA,
    DocRecord,
    Manifest,
    Settings,
    installed_pageindex_commit,
    page_count,
    save_manifest,
    sha256_file,
)

RUN_MANIFEST_SCHEMA = "s11-run-manifest-v1"
EFFORT_SCHEMA = "s11-effort-v1"

DEFAULT_RUNS_ROOT = Path("data") / "s11" / "runs"
DEFAULT_EMBED_CACHE_DIR = Path("data") / "s11" / "embed_cache"
DEFAULT_STOP_FILE = Path("data") / "runs" / "s11_STOP"
DEFAULT_GOLDEN_PATH = Path("eval") / "s11" / "golden_v1.json"
DEFAULT_GOLDEN_MANIFEST_PATH = Path("eval") / "s11" / "manifests" / "golden_v1.json"

FREEZE_MANIFESTS: tuple[str, ...] = (
    "eval/s11/manifests/prereg_v1.json",
    "eval/s11/manifests/prereg_addendum_a1.json",
    "eval/s11/manifests/prereg_addendum_a2.json",
    "eval/s11/manifests/golden_v1.json",
    "eval/s11/manifests/answer_rules_v1.json",
)

SYSTEM_DIRS: dict[str, str] = {
    "PI": "pi",
    "HY": "hy",
    "HY5": "hy5",
    "WD": "wd",
}

CONTENT_FAILURE_STATUSES: frozenset[str] = frozenset({
    "failed:max_turns",
    "failed:empty_answer",
    "failed:max_tokens",
    "failed:model_context_window_exceeded",
    "model_refusal",
    "failed:verifier_max_tokens",
    "failed:verifier_model_context_window_exceeded",
})

INFRA_HTTP_CODES: frozenset[int] = frozenset({408, 429})
MAX_CONSECUTIVE_INFRA = 3

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_PART_DIR_RE = re.compile(r"^part_(\d+)$")


class DriverValidationError(ValueError):
    """Raised when pre-flight driver arguments, checksums, effort rules, or invariants fail."""


class RealModeRequiredError(RuntimeError):
    """Raised when CLI real-mode preconditions (--real and REGCLAW_STUB unset) are not met."""


class _AbortRun(RuntimeError):
    """Internal control-flow exception signaling an immediate or circuit-breaker stop."""

    def __init__(self, stop_reason: str, detail: str = "") -> None:
        super().__init__(detail or stop_reason)
        self.stop_reason = stop_reason
        self.detail = detail


def _check_real_mode(*, real: bool, allow_stub_for_test: bool) -> None:
    if allow_stub_for_test:
        return
    if not real:
        raise RealModeRequiredError("Refusing run without --real flag")
    if "REGCLAW_STUB" in os.environ:
        raise RealModeRequiredError("Refusing run while REGCLAW_STUB is set; unset REGCLAW_STUB first")


def _resolve_abs_path(raw: Path | str, base: Path | None = None) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p.resolve()
    anchor = base if base is not None else Path.cwd()
    return (anchor / p).resolve()


def _normalize_sha256(value: str, label: str = "sha256") -> str:
    cleaned = str(value or "").strip().lower()
    if not _SHA256_HEX_RE.match(cleaned):
        raise DriverValidationError(f"{label} must be 64 hex chars, got {value!r}")
    return cleaned


def _pkg_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "not installed"


def _anthropic_api_version() -> str:
    if _anthropic_constants is None:
        return "unknown"
    return str(getattr(_anthropic_constants, "ANTHROPIC_API_VERSION", "unknown"))


def _git_head_info(repo_root: Path) -> dict[str, str | None]:
    head_path = Path(repo_root) / ".git" / "HEAD"
    if not head_path.is_file():
        return {"head": None, "commit": None}
    raw = head_path.read_text(encoding="utf-8").strip()
    if raw.startswith("ref:"):
        ref_rel = raw.split(":", 1)[1].strip()
        ref_file = Path(repo_root) / ".git" / ref_rel
        commit = ref_file.read_text(encoding="utf-8").strip() if ref_file.is_file() else None
        return {"head": ref_rel, "commit": commit}
    return {"head": raw, "commit": raw}


def compute_run_order(
    question_ids: Sequence[str],
    *,
    run_num: int | None = None,
    is_dev: bool = False,
) -> list[str]:
    """Compute pre-registered question order: sorted for dev; Random('S11-v1|order|r') for main."""
    ordered = sorted(str(q) for q in question_ids)
    if is_dev:
        return ordered
    if run_num not in (1, 2, 3) or isinstance(run_num, bool):
        raise DriverValidationError(f"Main run_num must be 1, 2, or 3, got {run_num!r}")
    rng = random.Random(f"S11-v1|order|{run_num}")
    rng.shuffle(ordered)
    return ordered


def order_sha256(question_ids: Sequence[str]) -> str:
    """Compute SHA-256 hex digest of an ordered question ID sequence."""
    payload = json.dumps(list(question_ids), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def systems_for_run(*, is_dev: bool, run_num: int) -> tuple[str, ...]:
    """Return ordered system sequence: dev or main r=2,3 -> (PI,HY,WD); main r=1 -> (PI,HY,HY5,WD)."""
    if is_dev:
        return ("PI", "HY", "WD")
    if run_num == 1:
        return ("PI", "HY", "HY5", "WD")
    if run_num in (2, 3):
        return ("PI", "HY", "WD")
    raise DriverValidationError(f"Unsupported run_num {run_num!r}")


def hy_k_flag(pi_distinct_pages: int) -> str | None:
    """Return HY reading-budget flag string ('k_fallback_5' / 'k_capped_40' / None)."""
    k, k_flagged = k_for(pi_distinct_pages)
    if k_flagged:
        return "k_fallback_5"
    if pi_distinct_pages > HY_MAX_K and k == HY_MAX_K:
        return "k_capped_40"
    return None


def resolve_hy_pi_pages(records: dict[tuple[str, str], dict[str, Any]], qid: str) -> int:
    """Return distinct PI page count for qid or raise DriverValidationError if PI record is missing."""
    pi_rec = records.get(("PI", qid))
    if pi_rec is None:
        raise DriverValidationError(f"Missing PI record required for HY coupling on {qid}")
    return len({int(p) for p in pi_rec.get("pages_given", [])})


def classify_record(record: dict[str, Any]) -> str:
    """Classify a completed system result record as content_ok, content_refusal, or content_failure."""
    if not isinstance(record, dict):
        raise DriverValidationError(f"Result record must be dict, got {type(record).__name__}")
    if record.get("system") == "HY5":
        return "content_ok"
    status = str(record.get("status") or "")
    if status == "ok":
        return "content_refusal" if record.get("refusal") else "content_ok"
    if status in CONTENT_FAILURE_STATUSES:
        return "content_failure"
    raise DriverValidationError(f"Unexpected result status {status!r} for {record.get('question_id')!r}")


def classify_exception(exc: BaseException) -> dict[str, Any]:
    """Classify an exception per K1/K7 into infra_error (retry once) vs immediate_stop (K1)."""
    if isinstance(
        exc,
        (
            ModelVersionChangedError,
            CapExceededError,
            PaidLockHeld,
            WriteOnceError,
            DriverValidationError,
            RealModeRequiredError,
        ),
    ):
        return {"kind": "immediate_stop", "reason": type(exc).__name__, "status_code": None}

    if _is_timeout_exception(exc):
        return {
            "kind": "infra_error",
            "reason": f"timeout:{type(exc).__name__}",
            "status_code": _extract_status_code(exc),
        }

    code = _extract_status_code(exc)
    if code is not None:
        if code in INFRA_HTTP_CODES or code >= 500:
            return {"kind": "infra_error", "reason": f"http_{code}", "status_code": code}
        return {"kind": "immediate_stop", "reason": f"http_{code}", "status_code": code}

    if _is_connection_exception(exc) or isinstance(exc, _OpenAIConnectionError):
        return {
            "kind": "infra_error",
            "reason": f"connection:{type(exc).__name__}",
            "status_code": None,
        }

    return {"kind": "immediate_stop", "reason": type(exc).__name__, "status_code": None}


def compute_effort_decision(
    dev_records: dict[tuple[str, str], dict[str, Any]],
    dev_ids: Sequence[str] = EXPECTED_DEV_IDS,
) -> dict[str, Any]:
    """Apply K3 / §3 effort rule across all 9 dev items (MEDIUM unless any failure or turn cap)."""
    failed_items: list[dict[str, Any]] = []
    checked = 0
    for sys_name in ("PI", "HY", "WD"):
        for qid in dev_ids:
            rec = dev_records.get((sys_name, qid))
            if rec is None:
                raise DriverValidationError(f"Cannot compute effort: missing dev record for {sys_name}/{qid}")
            checked += 1
            status = str(rec.get("status") or "")
            stop_reason = rec.get("stop_reason")
            if status != "ok" or stop_reason == "max_turns":
                failed_items.append(
                    {
                        "system": sys_name,
                        "question_id": qid,
                        "status": status,
                        "stop_reason": stop_reason,
                    }
                )
    return {
        "chosen_effort": "high" if failed_items else "medium",
        "dev_items_checked": checked,
        "failed_items": failed_items,
    }


def _make_infra_twice_record(
    *,
    system: str,
    question_id: str,
    effort: str,
    pages_given: list[int] | None = None,
) -> dict[str, Any]:
    return {
        "system": system,
        "question_id": question_id,
        "pages_given": sorted(int(p) for p in (pages_given or [])),
        "answer_pre_repair": "",
        "answer": "",
        "repaired": False,
        "dropped_sentences": [],
        "refusal": False,
        "check_status": "check_skipped_failed",
        "stop_reason": "infra_twice",
        "status": "failed:infra_twice",
        "model_returned": None,
        "verifier_model_returned": None,
        "effort": effort,
        "usage": {
            "answer": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "thinking_tokens": None},
            "verifier": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "thinking_tokens": None},
            "total": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "thinking_tokens": None},
        },
        "latency_s": 0.0,
    }


def _make_hy5_infra_twice_record(*, question_id: str, s: Settings) -> dict[str, Any]:
    return {
        "system": "HY5",
        "question_id": question_id,
        "k": HY5_K,
        "selected_pages": [],
        "ranking": [],
        "empty_pages": [],
        "non_empty_pages": [],
        "bm25_hits": [],
        "embed_stats": {"cache_hits": 0, "cache_misses": 0, "api_batches": 0, "api_inputs": 0},
        "k_rrf": RRF_K,
        "bm25_k1": BM25_K1,
        "bm25_b": BM25_B,
        "embedding_model": s.embedding_model,
        "embedding_deployment": s.embedding_model,
        "status": "failed:infra_twice",
        "stop_reason": "infra_twice",
    }


def _append_event(events_path: Path, event: dict[str, Any]) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with open(events_path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def _ledger_totals(ledger: CostLedger, spent_before: Decimal) -> dict[str, str]:
    spent_after = ledger.spent_usd
    return {
        "spent_usd_before": str(spent_before),
        "spent_usd_after": str(spent_after),
        "part_spent_usd": str(spent_after - spent_before),
        "reserved_usd": str(ledger.reserved_usd),
        "remaining_usd": str(ledger.remaining_usd),
        "cap_usd": str(ledger.cap_usd),
    }


def _module_shas(repo_root: Path) -> dict[str, str | None]:
    rels = {
        "driver_sha256": "eval/s11/driver.py",
        "answer_sha256": "eval/s11/answer.py",
        "hybrid_sha256": "eval/s11/hybrid.py",
        "whole_doc_sha256": "eval/s11/whole_doc.py",
        "pi_agent_sha256": "pageindex/pi_agent.py",
        "pi_client_sha256": "pageindex/pi_client.py",
    }
    out: dict[str, str | None] = {}
    for key, rel in rels.items():
        p = Path(repo_root) / rel
        out[key] = sha256_file(p) if p.is_file() else None
    return out


def _verify_freeze_manifests(
    manifest_rels: Sequence[str],
    *,
    repo_root: Path,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for rel in manifest_rels:
        try:
            ok, messages = check_manifest(rel, root=repo_root)
            _, resolved, posix_rel = validate_rel_path(repo_root, rel)
        except FreezeError as exc:
            raise DriverValidationError(f"Freeze manifest check failed for {rel}: {exc}") from exc
        if not ok:
            raise DriverValidationError(f"Freeze manifest mismatch for {rel}: {'; '.join(messages)}")
        manifest_doc = json.loads(resolved.read_text(encoding="utf-8"))
        out[posix_rel] = {
            "manifest_sha256": sha256_file(resolved),
            "files": [
                {"path": f["path"], "sha256_lf": f["sha256_lf"], "bytes_lf": f["bytes_lf"]}
                for f in manifest_doc["files"]
            ],
        }
    return out


def _seed_guard_from_prior(prior_manifests: list[dict[str, Any]]) -> ModelVersionGuard:
    guard = ModelVersionGuard()
    for man in prior_manifests:
        counts = man.get("counts") or {}
        models_ret = counts.get("models_returned") or {}
        observed = models_ret.get("azure_embeddings_observed") or {}
        if isinstance(observed, dict):
            for deployment, model_name in observed.items():
                if deployment and model_name:
                    guard.check(deployment=str(deployment), returned_model=str(model_name), lane="embedding")
    return guard


def _load_prior_run_state(run_dir: Path) -> tuple[int, dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    if run_dir.exists() and not run_dir.is_dir():
        raise DriverValidationError(f"Run path exists and is not a directory: {run_dir}")
    numbered: list[tuple[int, Path]] = []
    if run_dir.exists():
        for entry in run_dir.iterdir():
            if not entry.is_dir():
                continue
            m = _PART_DIR_RE.match(entry.name)
            if m:
                numbered.append((int(m.group(1)), entry))
    numbered.sort(key=lambda item: item[0])
    expected_nums = list(range(1, len(numbered) + 1))
    if [n for n, _ in numbered] != expected_nums:
        raise DriverValidationError(
            f"Non-contiguous part directories under {run_dir}: {[p.name for _, p in numbered]}"
        )

    completed: dict[tuple[str, str], dict[str, Any]] = {}
    prior_manifests: list[dict[str, Any]] = []
    for _, part_path in numbered:
        for sys_name, sys_dir in SYSTEM_DIRS.items():
            sub = part_path / sys_dir
            if not sub.is_dir():
                continue
            for rec_path in sorted(sub.glob("*.json")):
                qid = rec_path.stem
                if (sys_name, qid) in completed:
                    raise WriteOnceError(f"Duplicate saved record for {sys_name}/{qid} in {part_path}")
                completed[(sys_name, qid)] = json.loads(rec_path.read_text(encoding="utf-8"))
        man_path = part_path / "run_manifest.json"
        if man_path.is_file():
            prior_manifests.append(json.loads(man_path.read_text(encoding="utf-8")))
    return len(numbered) + 1, completed, prior_manifests


def _write_pi_run_local_manifest(
    pi_store_dir: Path,
    *,
    pdf_abs: Path,
    pdf_sha: str,
    tree_abs: Path,
    tree_doc_description: str,
    effort: str,
    total_pages: int,
) -> Settings:
    rec = DocRecord(
        doc_id="gtp@2019",
        family="gtp",
        title="ICMR-DBT National Guidelines for Gene Therapy Product Development and Clinical Trials",
        version="2019",
        effective_date=date(2019, 11, 1),
        superseded=False,
        sha256=pdf_sha,
        pdf_path=str(pdf_abs),
        tree_path=str(tree_abs),
        page_count=int(total_pages),
        doc_description=tree_doc_description,
        index_mode="standard",
        index_model="anthropic/claude-sonnet-5-5",
        pageindex_version=_pkg_version("pageindex"),
        built_at=datetime.now(timezone.utc),
        summary_model="anthropic/claude-sonnet-5-5",
        index_route="anthropic",
        pageindex_commit=installed_pageindex_commit(),
    )
    pi_settings = Settings(store_dir=pi_store_dir, answer_effort=effort)
    save_manifest(pi_settings, Manifest(documents={rec.doc_id: rec}))
    return pi_settings


def _load_and_validate_effort(
    effort_abs: Path,
    *,
    pdf_sha: str,
    tree_sha: str,
    golden_manifest_sha: str,
) -> dict[str, Any]:
    if not effort_abs.is_file():
        raise DriverValidationError(f"Missing dev effort decision file: {effort_abs}")
    try:
        doc = json.loads(effort_abs.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriverValidationError(f"Unreadable effort.json at {effort_abs}: {exc}") from exc
    if doc.get("schema") != EFFORT_SCHEMA:
        raise DriverValidationError(f"Unexpected effort.json schema: {doc.get('schema')!r}")
    chosen = doc.get("chosen_effort")
    if chosen not in ("medium", "high"):
        raise DriverValidationError(f"Invalid chosen_effort in effort.json: {chosen!r}")
    if doc.get("pdf_sha256") != pdf_sha:
        raise DriverValidationError("effort.json pdf_sha256 does not match current PDF")
    if doc.get("tree_sha256") != tree_sha:
        raise DriverValidationError("effort.json tree_sha256 does not match current tree_sha")
    if doc.get("golden_manifest_sha256") != golden_manifest_sha:
        raise DriverValidationError("effort.json golden_manifest_sha256 does not match golden manifest")
    return doc


def _validate_tree_file(tree_abs: Path, expected_sha: str) -> tuple[str, dict[str, Any]]:
    norm_expected = _normalize_sha256(expected_sha, "--tree-sha")
    if not tree_abs.is_file():
        raise FileNotFoundError(f"Tree file not found: {tree_abs}")
    actual_sha = sha256_file(tree_abs)
    if actual_sha != norm_expected:
        raise DriverValidationError(
            f"Tree SHA-256 mismatch for {tree_abs}: expected {norm_expected}, got {actual_sha}"
        )
    try:
        payload = json.loads(tree_abs.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriverValidationError(f"Invalid tree JSON at {tree_abs}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("structure"), list):
        raise DriverValidationError(f"Tree JSON missing 'structure' list at {tree_abs}")
    return actual_sha, payload


def _persist_system_output(
    *,
    part_dir: Path,
    sys_name: str,
    qid: str,
    raw_result: dict[str, Any],
    all_pages: dict[int, str],
) -> dict[str, Any]:
    if sys_name == "HY":
        if isinstance(raw_result, dict) and "answer" in raw_result and "retrieval" in raw_result and isinstance(raw_result["answer"], dict):
            ans_rec = raw_result["answer"]
            ret_rec = raw_result["retrieval"]
            write_once_json(part_dir / "hy_retrieval" / f"{qid}.json", ret_rec)
            write_once_json(part_dir / "hy" / f"{qid}.json", ans_rec)
            return ans_rec
        write_once_json(part_dir / "hy" / f"{qid}.json", raw_result)
        return raw_result

    if sys_name == "WD":
        meta = build_wd_document_meta(all_pages)
        write_once_json(
            part_dir / "wd_meta" / f"{qid}.json",
            {
                "question_id": qid,
                "total_pages": meta["total_pages"],
                "empty_pages": meta["empty_pages"],
                "non_empty_pages": meta["non_empty_pages"],
            },
        )
        write_once_json(part_dir / "wd" / f"{qid}.json", raw_result)
        return raw_result

    sub_dir = SYSTEM_DIRS[sys_name]
    write_once_json(part_dir / sub_dir / f"{qid}.json", raw_result)
    return raw_result


def _execute_run(
    *,
    mode: str,
    run_num: int,
    pdf_path: Path | str,
    tree_path: Path | str,
    tree_sha: str,
    runs_root: Path | str = DEFAULT_RUNS_ROOT,
    cache_dir: Path | str = DEFAULT_EMBED_CACHE_DIR,
    effort_file: Path | str | None = None,
    golden_path: Path | str = DEFAULT_GOLDEN_PATH,
    golden_manifest_path: Path | str = DEFAULT_GOLDEN_MANIFEST_PATH,
    ledger_path: Path | str | None = None,
    usage_log_path: Path | str | None = None,
    lock_path: Path | str | None = None,
    stop_file: Path | str = DEFAULT_STOP_FILE,
    real: bool = False,
    allow_stub_for_test: bool = False,
    repo_root: Path = ROOT,
    expected_pdf_sha256: str = EXPECTED_PDF_SHA256,
    freeze_manifests: Sequence[str] = FREEZE_MANIFESTS,
    pi_runner: Callable[..., dict[str, Any]] | None = None,
    hy_runner: Callable[..., dict[str, Any]] | None = None,
    hy5_runner: Callable[..., dict[str, Any]] | None = None,
    wd_runner: Callable[..., dict[str, Any]] | None = None,
    page_extractor: Callable[[Path | str], tuple[dict[int, str], list[int]]] | None = None,
) -> dict[str, Any]:
    _check_real_mode(real=real, allow_stub_for_test=allow_stub_for_test)
    caller_cwd = Path.cwd()
    is_dev = mode == "dev"
    if mode not in ("dev", "main"):
        raise DriverValidationError(f"Invalid mode {mode!r}")

    pdf_abs = _resolve_abs_path(pdf_path, caller_cwd)
    if not pdf_abs.is_file():
        raise FileNotFoundError(f"PDF file not found: {pdf_abs}")
    tree_abs = _resolve_abs_path(tree_path, caller_cwd)
    actual_tree_sha, tree_payload = _validate_tree_file(tree_abs, tree_sha)

    runs_root_abs = _resolve_abs_path(runs_root, caller_cwd)
    run_name = "dev_r1" if is_dev else f"main_r{run_num}"
    run_dir = runs_root_abs / run_name
    effort_abs = (
        _resolve_abs_path(effort_file, caller_cwd)
        if effort_file is not None
        else (runs_root_abs / "dev_r1" / "effort.json")
    )
    cache_dir_abs = _resolve_abs_path(cache_dir, caller_cwd)
    stop_file_abs = _resolve_abs_path(stop_file, caller_cwd)
    golden_abs = _resolve_abs_path(golden_path, repo_root)
    golden_manifest_abs = _resolve_abs_path(golden_manifest_path, repo_root)

    default_ledger, default_usage, default_lock = default_s11_paths(repo_root)
    ledger_abs = _resolve_abs_path(ledger_path, caller_cwd) if ledger_path is not None else default_ledger.resolve()
    usage_abs = _resolve_abs_path(usage_log_path, caller_cwd) if usage_log_path is not None else default_usage.resolve()
    lock_abs = _resolve_abs_path(lock_path, caller_cwd) if lock_path is not None else default_lock.resolve()

    frozen_shas = _verify_freeze_manifests(freeze_manifests, repo_root=repo_root)
    try:
        golden_doc = load_frozen_golden(
            golden_abs,
            golden_manifest_abs,
            root=repo_root,
            expected_pdf_sha256=expected_pdf_sha256,
        )
    except GoldenValidationError as exc:
        raise DriverValidationError(str(exc)) from exc

    pdf_sha = sha256_file(pdf_abs)
    if pdf_sha != golden_doc["pdf_sha256"]:
        raise DriverValidationError(
            f"PDF SHA-256 mismatch: expected {golden_doc['pdf_sha256']}, got {pdf_sha}"
        )
    golden_manifest_sha = sha256_file(golden_manifest_abs)

    effort_doc: dict[str, Any] | None = None
    effort_file_sha: str | None = None
    if is_dev:
        if effort_abs.exists() or effort_abs.is_symlink():
            raise WriteOnceError(f"Refusing to overwrite existing dev effort file: {effort_abs}")
        effort = "medium"
        items_list = golden_doc["dev"]
        if tuple(item["id"] for item in items_list) != EXPECTED_DEV_IDS:
            raise DriverValidationError("Frozen golden dev IDs do not match EXPECTED_DEV_IDS")
    else:
        effort_doc = _load_and_validate_effort(
            effort_abs,
            pdf_sha=pdf_sha,
            tree_sha=actual_tree_sha,
            golden_manifest_sha=golden_manifest_sha,
        )
        effort = effort_doc["chosen_effort"]
        effort_file_sha = sha256_file(effort_abs)
        items_list = golden_doc["test"]
        if len(items_list) != len(EXPECTED_TEST_IDS) or tuple(item["id"] for item in items_list) != EXPECTED_TEST_IDS:
            raise DriverValidationError("Frozen golden test IDs do not match EXPECTED_TEST_IDS")

    item_by_id = {item["id"]: item for item in items_list}
    order = compute_run_order(list(item_by_id), run_num=run_num, is_dev=is_dev)
    order_sha = order_sha256(order)
    systems = systems_for_run(is_dev=is_dev, run_num=run_num)
    full_schedule: list[tuple[str, str]] = [(sys_name, qid) for sys_name in systems for qid in order]

    with paid_lock(lock_path=lock_abs):
        part_num, prior_completed, prior_manifests = _load_prior_run_state(run_dir)
        remaining = [pair for pair in full_schedule if pair not in prior_completed]
        if not remaining:
            raise WriteOnceError(
                f"Refusing run: all {len(full_schedule)} items in {run_name} already completed across {part_num - 1} part(s)"
            )

        run_dir.mkdir(parents=True, exist_ok=True)
        part_dir = run_dir / f"part_{part_num}"
        try:
            part_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise WriteOnceError(f"Refusing to overwrite existing part directory: {part_dir}") from exc

        events_path = part_dir / "events.jsonl"
        manifest_path = part_dir / "run_manifest.json"
        stopped_path = part_dir / "STOPPED"

        extractor = page_extractor if page_extractor is not None else extract_all_pages
        all_pages, _ = extractor(pdf_abs)
        pi_settings = _write_pi_run_local_manifest(
            part_dir / "_pi_store",
            pdf_abs=pdf_abs,
            pdf_sha=pdf_sha,
            tree_abs=tree_abs,
            tree_doc_description=str(tree_payload.get("doc_description") or ""),
            effort=effort,
            total_pages=len(all_pages) if all_pages else page_count(pdf_abs),
        )
        shared_settings = Settings(store_dir=pi_settings.store_dir, answer_effort=effort)

        ledger = CostLedger(ledger_abs)
        usage_log = UsageLogger(usage_abs)
        spent_before = ledger.spent_usd
        guard = _seed_guard_from_prior(prior_manifests)

        all_records: dict[tuple[str, str], dict[str, Any]] = dict(prior_completed)
        consec_infra = 0
        ok_in_part = 0
        refusal_in_part = 0
        content_failed_in_part = 0
        infra_twice_in_part = 0
        completed_in_part = 0
        infra_retried_items: list[dict[str, Any]] = []
        hy_budget_flags: list[dict[str, Any]] = []
        gen_models: set[str] = set()
        ver_models: set[str] = set()

        start_utc = now_utc_iso()
        final_status = "completed"
        stop_reason: str | None = None
        stop_detail = ""
        manifest_out: dict[str, Any] = {}

        pi_fn = pi_runner if pi_runner is not None else run_pi_answer
        hy_fn = hy_runner if hy_runner is not None else run_hy
        hy5_fn = hy5_runner if hy5_runner is not None else run_hy5_retrieval
        wd_fn = wd_runner if wd_runner is not None else run_wd

        def _invoke_item(sys_name: str, qid: str) -> dict[str, Any]:
            question = str(item_by_id[qid]["question"])
            if sys_name == "PI":
                return pi_fn(
                    qid,
                    question,
                    family="gtp",
                    version="2019",
                    s=pi_settings,
                    ledger=ledger,
                    usage_log=usage_log,
                    out_dir=None,
                )
            if sys_name == "HY":
                pi_pages = resolve_hy_pi_pages(all_records, qid)
                return hy_fn(
                    qid,
                    question,
                    all_pages,
                    pi_distinct_pages=pi_pages,
                    cache_dir=cache_dir_abs,
                    s=shared_settings,
                    ledger=ledger,
                    usage_log=usage_log,
                    guard=guard,
                    out_dir=None,
                    retrieval_out_dir=None,
                )
            if sys_name == "HY5":
                return hy5_fn(
                    qid,
                    question,
                    all_pages,
                    cache_dir=cache_dir_abs,
                    s=shared_settings,
                    usage_log=usage_log,
                    guard=guard,
                    out_dir=None,
                )
            if sys_name == "WD":
                return wd_fn(
                    qid,
                    question,
                    all_pages=all_pages,
                    s=shared_settings,
                    ledger=ledger,
                    usage_log=usage_log,
                    out_dir=None,
                    meta_out_dir=None,
                )
            raise DriverValidationError(f"Unknown system {sys_name!r}")

        try:
            for sys_name, qid in remaining:
                if stop_file_abs.exists():
                    final_status = "stopped"
                    stop_reason = "stop_file"
                    stop_detail = f"Stop file present: {stop_file_abs}"
                    _append_event(
                        events_path,
                        {"ts_utc": now_utc_iso(), "event": "stop_file_detected", "system": sys_name, "question_id": qid},
                    )
                    break

                hy_pi_pages: int | None = None
                hy_flag: str | None = None
                if sys_name == "HY":
                    hy_pi_pages = resolve_hy_pi_pages(all_records, qid)
                    hy_flag = hy_k_flag(hy_pi_pages)

                for attempt in (1, 2):
                    try:
                        raw_res = _invoke_item(sys_name, qid)
                    except Exception as exc:
                        info = classify_exception(exc)
                        if info["kind"] == "immediate_stop":
                            _append_event(
                                events_path,
                                {
                                    "ts_utc": now_utc_iso(),
                                    "event": "immediate_stop",
                                    "system": sys_name,
                                    "question_id": qid,
                                    "attempt": attempt,
                                    "reason": info["reason"],
                                    "status_code": info["status_code"],
                                },
                            )
                            raise _AbortRun(f"immediate_stop:{info['reason']}", str(exc)) from exc

                        consec_infra += 1
                        _append_event(
                            events_path,
                            {
                                "ts_utc": now_utc_iso(),
                                "event": "infra_error",
                                "system": sys_name,
                                "question_id": qid,
                                "attempt": attempt,
                                "consec_infra": consec_infra,
                                "reason": info["reason"],
                                "status_code": info["status_code"],
                            },
                        )
                        if attempt == 1:
                            if consec_infra > MAX_CONSECUTIVE_INFRA:
                                raise _AbortRun("consecutive_infra_errors") from exc
                            infra_retried_items.append(
                                {
                                    "system": sys_name,
                                    "question_id": qid,
                                    "reason": info["reason"],
                                    "status_code": info["status_code"],
                                }
                            )
                            continue

                        fail_rec = (
                            _make_hy5_infra_twice_record(question_id=qid, s=shared_settings)
                            if sys_name == "HY5"
                            else _make_infra_twice_record(system=sys_name, question_id=qid, effort=effort)
                        )
                        write_once_json(part_dir / SYSTEM_DIRS[sys_name] / f"{qid}.json", fail_rec)
                        all_records[(sys_name, qid)] = fail_rec
                        completed_in_part += 1
                        infra_twice_in_part += 1
                        _append_event(
                            events_path,
                            {
                                "ts_utc": now_utc_iso(),
                                "event": "infra_twice",
                                "system": sys_name,
                                "question_id": qid,
                                "consec_infra": consec_infra,
                            },
                        )
                        if consec_infra > MAX_CONSECUTIVE_INFRA:
                            raise _AbortRun("consecutive_infra_errors") from exc
                        break

                    consec_infra = 0
                    persisted = _persist_system_output(
                        part_dir=part_dir,
                        sys_name=sys_name,
                        qid=qid,
                        raw_result=raw_res,
                        all_pages=all_pages,
                    )
                    all_records[(sys_name, qid)] = persisted
                    completed_in_part += 1

                    kind = classify_record(persisted)
                    if kind == "content_ok":
                        ok_in_part += 1
                    elif kind == "content_refusal":
                        refusal_in_part += 1
                    else:
                        content_failed_in_part += 1

                    if sys_name == "HY" and hy_flag is not None and hy_pi_pages is not None:
                        k_val, _ = k_for(hy_pi_pages)
                        hy_budget_flags.append(
                            {
                                "question_id": qid,
                                "pi_distinct_pages": hy_pi_pages,
                                "k": k_val,
                                "flag": hy_flag,
                            }
                        )
                    if persisted.get("model_returned"):
                        gen_models.add(str(persisted["model_returned"]))
                    if persisted.get("verifier_model_returned"):
                        ver_models.add(str(persisted["verifier_model_returned"]))

                    _append_event(
                        events_path,
                        {
                            "ts_utc": now_utc_iso(),
                            "event": "item_done",
                            "system": sys_name,
                            "question_id": qid,
                            "attempt": attempt,
                            "outcome": kind,
                            "status": persisted.get("status", "HY5_ok"),
                            "stop_reason": persisted.get("stop_reason"),
                            "hy_budget_flag": hy_flag,
                        },
                    )
                    break

        except _AbortRun as abort:
            final_status = "stopped"
            stop_reason = abort.stop_reason
            stop_detail = abort.detail
        except Exception as unexpected:
            final_status = "stopped"
            stop_reason = f"immediate_stop:{type(unexpected).__name__}"
            stop_detail = str(unexpected)

        end_utc = now_utc_iso()
        if final_status == "stopped":
            write_once_json(
                stopped_path,
                {
                    "schema": "s11-stopped-v1",
                    "run_name": run_name,
                    "part_name": part_dir.name,
                    "stopped_utc": end_utc,
                    "stop_reason": stop_reason,
                    "consec_infra": consec_infra,
                },
            )
        elif is_dev and len(all_records) == len(full_schedule):
            decision = compute_effort_decision(all_records, dev_ids=order)
            effort_payload = {
                "schema": EFFORT_SCHEMA,
                "chosen_effort": decision["chosen_effort"],
                "decided_at_utc": end_utc,
                "dev_run_dir": str(run_dir),
                "golden_manifest_sha256": golden_manifest_sha,
                "pdf_sha256": pdf_sha,
                "tree_sha256": actual_tree_sha,
                "dev_items_checked": decision["dev_items_checked"],
                "failed_items": decision["failed_items"],
            }
            write_once_json(effort_abs, effort_payload)
            effort_file_sha = sha256_file(effort_abs)

        manifest_out = {
            "schema": RUN_MANIFEST_SCHEMA,
            "run_name": run_name,
            "mode": mode,
            "run_number": run_num,
            "part_name": part_dir.name,
            "part_number": part_num,
            "status": final_status,
            "stop_reason": stop_reason,
            "stop_detail": stop_detail if final_status == "stopped" else None,
            "consec_infra_errors_at_end": consec_infra,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "pdf_path": str(pdf_abs),
            "pdf_sha256": pdf_sha,
            "tree_path": str(tree_abs),
            "tree_sha256": actual_tree_sha,
            "frozen_sha256": frozen_shas,
            "golden_schema": golden_doc["schema"],
            "golden_manifest_sha256": golden_manifest_sha,
            "order": {
                "seed": None if is_dev else f"S11-v1|order|{run_num}",
                "question_ids": order,
                "order_sha256": order_sha,
                "systems": list(systems),
            },
            "settings": {
                "effort": effort,
                "effort_source": "dev_default_medium" if is_dev else str(effort_abs),
                "effort_file_sha256": effort_file_sha,
                "agent_model": shared_settings.agent_model,
                "verifier_model": shared_settings.verifier_model,
                "embedding_model": shared_settings.embedding_model,
                "embedding_deployment": shared_settings.embedding_model,
                "agent_max_turns": shared_settings.agent_max_turns,
                "max_pages_per_fetch": shared_settings.max_pages_per_fetch,
                "max_pages_total": shared_settings.max_pages_total,
                "agent_max_tokens": shared_settings.agent_max_tokens,
                "verifier_max_tokens": shared_settings.verifier_max_tokens,
                "agent_timeout_s": shared_settings.agent_timeout_s,
                "verifier_timeout_s": shared_settings.verifier_timeout_s,
                "judge_timeout_s": shared_settings.judge_timeout_s,
                "anthropic_version": _anthropic_api_version(),
                "hy_fallback_k": HY_FALLBACK_K,
                "hy_max_k": HY_MAX_K,
                "hy5_k": HY5_K,
                "rrf_k": RRF_K,
                "bm25_k1": BM25_K1,
                "bm25_b": BM25_B,
                "temperature": None,
                "top_p": None,
                "top_k": None,
                "refusal_sentence": EXPECTED_REFUSAL,
            },
            "code_and_packages": {
                "git": _git_head_info(repo_root),
                "pageindex_commit": installed_pageindex_commit(),
                "pageindex_pin_sha": PAGEINDEX_PIN_SHA,
                "python_version": platform.python_version(),
                "anthropic_sdk_version": _pkg_version("anthropic"),
                "openai_sdk_version": _pkg_version("openai"),
                "httpx_version": _pkg_version("httpx"),
                "litellm_version": _pkg_version("litellm"),
                **_module_shas(repo_root),
            },
            "counts": {
                "expected_total_items": len(full_schedule),
                "skipped_prior_items": len(prior_completed),
                "completed_in_part": completed_in_part,
                "total_recorded_items": len(all_records),
                "ok_in_part": ok_in_part,
                "refusal_in_part": refusal_in_part,
                "content_failed_in_part": content_failed_in_part,
                "infra_twice_in_part": infra_twice_in_part,
                "infra_retried_items": infra_retried_items,
                "hy_budget_flags": hy_budget_flags,
                "models_returned": {
                    "generator": sorted(gen_models),
                    "verifier": sorted(ver_models),
                    "azure_embeddings_observed": guard.observed_models,
                    "azure_model_violations": guard.violations,
                },
            },
            "ledger_totals": _ledger_totals(ledger, spent_before),
        }
        write_once_json(manifest_path, manifest_out)
        return manifest_out


def run_dev(**kwargs: Any) -> dict[str, Any]:
    """Run S11 dev pilot (3 dev questions x PI/HY/WD at MEDIUM effort) and decide K3 effort."""
    return _execute_run(mode="dev", run_num=1, **kwargs)


def run_main(*, run_num: int, **kwargs: Any) -> dict[str, Any]:
    """Run S11 main evaluation run r in {1, 2, 3} with optional HY5 on r=1."""
    return _execute_run(mode="main", run_num=run_num, **kwargs)


def format_run_summary(manifest: dict[str, Any]) -> str:
    """Format strictly non-leaking run summary (IDs, status, SHAs, counts, ledger totals)."""
    summary = {
        "schema": manifest["schema"],
        "run_name": manifest["run_name"],
        "part_name": manifest["part_name"],
        "status": manifest["status"],
        "stop_reason": manifest["stop_reason"],
        "effort": manifest["settings"]["effort"],
        "tree_sha256": manifest["tree_sha256"],
        "pdf_sha256": manifest["pdf_sha256"],
        "order_sha256": manifest["order"]["order_sha256"],
        "counts": manifest["counts"],
        "ledger_totals": manifest["ledger_totals"],
    }
    return json.dumps(summary, indent=2, ensure_ascii=False)


def _add_common_cli_args(parser: argparse.ArgumentParser) -> None:
    default_ledger, default_usage, default_lock = default_s11_paths(ROOT)
    parser.add_argument("--pdf", required=True, help="Path to source PDF")
    parser.add_argument("--tree", required=True, help="Path to audited tree.json")
    parser.add_argument("--tree-sha", required=True, help="Expected SHA-256 hex digest of --tree")
    parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT), help="Runs root (default: data/s11/runs)")
    parser.add_argument("--cache-dir", default=str(DEFAULT_EMBED_CACHE_DIR), help="Embedding cache dir (default: data/s11/embed_cache)")
    parser.add_argument("--effort-file", default=None, help="Override path for dev_r1/effort.json")
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN_PATH), help="Golden JSON path")
    parser.add_argument("--golden-manifest", default=str(DEFAULT_GOLDEN_MANIFEST_PATH), help="Golden manifest JSON path")
    parser.add_argument("--ledger", default=str(default_ledger), help="Cost ledger JSONL path")
    parser.add_argument("--usage-log", default=str(default_usage), help="Usage log JSONL path")
    parser.add_argument("--lock", default=str(default_lock), help="Paid lock file path")
    parser.add_argument("--stop-file", default=str(DEFAULT_STOP_FILE), help="Stop-switch file path (default: data/runs/s11_STOP)")
    parser.add_argument("--real", action="store_true", help="Required flag confirming a live real-mode run")


def main(
    argv: list[str] | None = None,
    *,
    repo_root: Path = ROOT,
    expected_pdf_sha256: str = EXPECTED_PDF_SHA256,
    freeze_manifests: Sequence[str] = FREEZE_MANIFESTS,
    allow_stub_for_test: bool = False,
    pi_runner: Callable[..., dict[str, Any]] | None = None,
    hy_runner: Callable[..., dict[str, Any]] | None = None,
    hy5_runner: Callable[..., dict[str, Any]] | None = None,
    wd_runner: Callable[..., dict[str, Any]] | None = None,
    page_extractor: Callable[[Path | str], tuple[dict[int, str], list[int]]] | None = None,
) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m eval.s11.driver",
        description="S11 evaluation run driver (dev pilot with K3 effort rule; main runs 1-3 with HY5 on run 1).",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_dev = sub.add_parser("dev", help="Execute dev pilot (3 questions x PI/HY/WD at MEDIUM) and write effort.json")
    _add_common_cli_args(p_dev)

    p_main = sub.add_parser("main", help="Execute main evaluation run r in {1, 2, 3}")
    p_main.add_argument("--run", type=int, choices=(1, 2, 3), required=True, help="Main run number (1, 2, or 3)")
    _add_common_cli_args(p_main)

    args = ap.parse_args(argv)
    common = dict(
        pdf_path=args.pdf,
        tree_path=args.tree,
        tree_sha=args.tree_sha,
        runs_root=args.runs_root,
        cache_dir=args.cache_dir,
        effort_file=args.effort_file,
        golden_path=args.golden,
        golden_manifest_path=args.golden_manifest,
        ledger_path=args.ledger,
        usage_log_path=args.usage_log,
        lock_path=args.lock,
        stop_file=args.stop_file,
        real=args.real,
        allow_stub_for_test=allow_stub_for_test,
        repo_root=repo_root,
        expected_pdf_sha256=expected_pdf_sha256,
        freeze_manifests=freeze_manifests,
        pi_runner=pi_runner,
        hy_runner=hy_runner,
        hy5_runner=hy5_runner,
        wd_runner=wd_runner,
        page_extractor=page_extractor,
    )
    try:
        manifest = run_dev(**common) if args.command == "dev" else run_main(run_num=args.run, **common)
    except (RealModeRequiredError, DriverValidationError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except (WriteOnceError, PaidLockHeld) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(format_run_summary(manifest))
    return 0 if manifest["status"] == "completed" else 4


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "CONTENT_FAILURE_STATUSES",
    "DEFAULT_EMBED_CACHE_DIR",
    "DEFAULT_GOLDEN_MANIFEST_PATH",
    "DEFAULT_GOLDEN_PATH",
    "DEFAULT_RUNS_ROOT",
    "DEFAULT_STOP_FILE",
    "DriverValidationError",
    "EFFORT_SCHEMA",
    "FREEZE_MANIFESTS",
    "INFRA_HTTP_CODES",
    "MAX_CONSECUTIVE_INFRA",
    "RUN_MANIFEST_SCHEMA",
    "RealModeRequiredError",
    "classify_exception",
    "classify_record",
    "compute_effort_decision",
    "compute_run_order",
    "format_run_summary",
    "hy_k_flag",
    "main",
    "order_sha256",
    "resolve_hy_pi_pages",
    "run_dev",
    "run_main",
    "systems_for_run",
]
