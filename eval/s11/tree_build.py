"""S11 tree build CLI: registered PageIndex config, max-2 write-once build dirs, chdir logs, and manifest."""
from __future__ import annotations

import argparse
import contextlib
from decimal import Decimal
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "pageindex") not in sys.path:
    sys.path.insert(0, str(ROOT / "pageindex"))

from eval.s11.ledger import (  # noqa: E402
    CostLedger,
    PaidLockHeld,
    UsageLogger,
    WriteOnceError,
    default_s11_paths,
    now_utc_iso,
    paid_lock,
    write_once_json,
)
from eval.s11.tree_instrument import instrument_tree_build  # noqa: E402
from pi_common import (  # noqa: E402
    PAGEINDEX_PIN_SHA,
    Settings,
    installed_pageindex_commit,
    iter_nodes,
    sha256_file,
)
import pi_ingest  # noqa: E402

BUILD_MANIFEST_SCHEMA = "s11-tree-build-v1"
MAX_BUILDS = 2
DEFAULT_BUILDS_ROOT = Path("data") / "s11" / "tree"

REGISTERED_INDEX_MODE = "standard"
REGISTERED_INDEX_MODEL = "anthropic/claude-sonnet-5-5"
REGISTERED_SUMMARY_MODEL = "anthropic/claude-sonnet-5-5"
REGISTERED_TOC_CHECK_PAGE_NUM = 20
REGISTERED_MAX_PAGE_NUM_EACH_NODE = 10
REGISTERED_MAX_TOKEN_NUM_EACH_NODE = 20000
REGISTERED_IF_ADD_NODE_ID = "yes"
REGISTERED_IF_ADD_NODE_SUMMARY = "yes"
REGISTERED_IF_ADD_DOC_DESCRIPTION = "yes"
REGISTERED_IF_ADD_NODE_TEXT = "no"
REGISTERED_LITELLM_MAX_TOKENS = 16000
REGISTERED_TREE_RESERVE_MAX_TOKENS = 32000

_BUILD_DIR_RE = re.compile(r"^build_(\d+)$")


class BuildLimitError(RuntimeError):
    """Raised when a build would exceed the max 2 builds limit."""


class RealModeRequiredError(RuntimeError):
    """Raised when real-mode CLI preconditions (--real and REGCLAW_STUB unset) are not met."""


def _litellm_version() -> str:
    try:
        return metadata.version("litellm")
    except metadata.PackageNotFoundError:
        return "not installed"


def _validate_positive_int(val: int, name: str) -> int:
    if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
        raise ValueError(f"{name} must be a positive integer, got {val!r}")
    return int(val)


def build_registered_settings(
    *,
    max_page_num_each_node: int | None = None,
    max_token_num_each_node: int | None = None,
) -> Settings:
    """Return Settings locked to S11 registered tree-build defaults plus optional K4 node-limit overrides."""
    eff_max_pages = (
        REGISTERED_MAX_PAGE_NUM_EACH_NODE
        if max_page_num_each_node is None
        else _validate_positive_int(max_page_num_each_node, "max_page_num_each_node")
    )
    eff_max_tokens = (
        REGISTERED_MAX_TOKEN_NUM_EACH_NODE
        if max_token_num_each_node is None
        else _validate_positive_int(max_token_num_each_node, "max_token_num_each_node")
    )
    return Settings(
        index_mode=REGISTERED_INDEX_MODE,
        index_model=REGISTERED_INDEX_MODEL,
        summary_model=REGISTERED_SUMMARY_MODEL,
        toc_check_page_num=REGISTERED_TOC_CHECK_PAGE_NUM,
        max_page_num_each_node=eff_max_pages,
        max_token_num_each_node=eff_max_tokens,
        if_add_node_id=REGISTERED_IF_ADD_NODE_ID,
        if_add_node_summary=REGISTERED_IF_ADD_NODE_SUMMARY,
        if_add_doc_description=REGISTERED_IF_ADD_DOC_DESCRIPTION,
        if_add_node_text=REGISTERED_IF_ADD_NODE_TEXT,
        litellm_max_tokens=REGISTERED_LITELLM_MAX_TOKENS,
        tree_reserve_max_tokens=REGISTERED_TREE_RESERVE_MAX_TOKENS,
    )


def _check_real_mode(*, real: bool, allow_stub_for_test: bool) -> None:
    if allow_stub_for_test:
        return
    if not real:
        raise RealModeRequiredError("Refusing tree build without --real flag")
    if "REGCLAW_STUB" in os.environ:
        raise RealModeRequiredError("Refusing tree build while REGCLAW_STUB is set; unset REGCLAW_STUB first")


def _resolve_abs_path(raw: Path | str, base: Path | None = None) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p.resolve()
    anchor = base if base is not None else Path.cwd()
    return (anchor / p).resolve()


def next_build_dir(builds_root: Path | str, *, max_builds: int = MAX_BUILDS) -> tuple[Path, int]:
    """Return (next_build_path, build_num) under builds_root, enforcing max_builds and write-once."""
    root = Path(builds_root)
    if root.exists() and not root.is_dir():
        raise ValueError(f"builds_root exists and is not a directory: {root}")
    existing_nums: list[int] = []
    if root.exists():
        for entry in root.iterdir():
            m = _BUILD_DIR_RE.match(entry.name)
            if m:
                num = int(m.group(1))
                if num < 1 or num > max_builds:
                    raise BuildLimitError(
                        f"Unexpected existing build entry {entry.name!r} outside 1..{max_builds}"
                    )
                existing_nums.append(num)
    if len(existing_nums) >= max_builds:
        raise BuildLimitError(
            f"Refusing build #{len(existing_nums) + 1}: max {max_builds} builds already exist under {root}"
        )
    next_num = len(existing_nums) + 1
    target = root / f"build_{next_num}"
    if target.exists() or target.is_symlink():
        raise WriteOnceError(f"Refusing to overwrite existing build folder: {target}")
    return target, next_num


def _finalize_build_log(build_dir: Path, build_log_path: Path) -> None:
    if build_log_path.exists() or build_log_path.is_symlink():
        raise WriteOnceError(f"Refusing to overwrite existing build log: {build_log_path}")
    logs_subdir = build_dir / "logs"
    log_files = sorted(logs_subdir.glob("*.json")) if logs_subdir.is_dir() else []
    if len(log_files) == 1:
        log_files[0].rename(build_log_path)
    elif len(log_files) > 1:
        merged = [json.loads(p.read_text(encoding="utf-8")) for p in log_files]
        write_once_json(build_log_path, merged)
        for p in log_files:
            p.unlink()
    else:
        write_once_json(build_log_path, {"events": [], "note": "no_pageindex_json_logger_output"})
    if logs_subdir.is_dir() and not any(logs_subdir.iterdir()):
        logs_subdir.rmdir()


def _build_counts(tree_result: dict[str, Any], inst_state: Any) -> dict[str, Any]:
    structure = tree_result.get("structure") or []
    nodes_all = list(iter_nodes(structure))
    leaf_count = sum(1 for node, _ in nodes_all if not (node.get("nodes") or []))
    return {
        "node_count": len(nodes_all),
        "leaf_count": leaf_count,
        "wrapper_calls": tree_result.get("wrapper_calls"),
        "custom_logger_calls": tree_result.get("custom_logger_calls"),
        "open_reservations": len(tree_result.get("open_reservations") or []),
        "real_processing_mode": inst_state.real_processing_mode,
        "skipped_verify_count": int(inst_state.skipped_verify_count),
        "pre_merge_call_count": len(inst_state.snapshots),
    }


def _ledger_totals(ledger: CostLedger, spent_before: Decimal) -> dict[str, str]:
    spent_after = ledger.spent_usd
    return {
        "spent_usd_before": str(spent_before),
        "spent_usd_after": str(spent_after),
        "build_spent_usd": str(spent_after - spent_before),
        "reserved_usd": str(ledger.reserved_usd),
        "remaining_usd": str(ledger.remaining_usd),
        "cap_usd": str(ledger.cap_usd),
    }


def run_tree_build(
    pdf_path: Path | str,
    *,
    builds_root: Path | str = DEFAULT_BUILDS_ROOT,
    real: bool = False,
    max_page_num_each_node: int | None = None,
    max_token_num_each_node: int | None = None,
    ledger_path: Path | str | None = None,
    usage_log_path: Path | str | None = None,
    lock_path: Path | str | None = None,
    allow_stub_for_test: bool = False,
    build_tree_fn: Callable[..., dict[str, Any]] | None = None,
    classic_mod: Any | None = None,
    build_dir_override: Path | str | None = None,
) -> dict[str, Any]:
    """Execute one write-once instrumented PageIndex build inside contextlib.chdir(build_dir)."""
    _check_real_mode(real=real, allow_stub_for_test=allow_stub_for_test)
    settings = build_registered_settings(
        max_page_num_each_node=max_page_num_each_node,
        max_token_num_each_node=max_token_num_each_node,
    )

    caller_cwd = Path.cwd()
    pdf_abs = _resolve_abs_path(pdf_path, caller_cwd)
    if not pdf_abs.is_file():
        raise FileNotFoundError(f"PDF file not found: {pdf_abs}")

    builds_root_abs = _resolve_abs_path(builds_root, caller_cwd)
    default_ledger, default_usage, default_lock = default_s11_paths(ROOT)
    ledger_abs = _resolve_abs_path(ledger_path, caller_cwd) if ledger_path is not None else default_ledger.resolve()
    usage_abs = _resolve_abs_path(usage_log_path, caller_cwd) if usage_log_path is not None else default_usage.resolve()
    lock_abs = _resolve_abs_path(lock_path, caller_cwd) if lock_path is not None else default_lock.resolve()

    build_fn = build_tree_fn if build_tree_fn is not None else pi_ingest.build_tree

    with paid_lock(lock_path=lock_abs):
        if build_dir_override is not None:
            build_dir = _resolve_abs_path(build_dir_override, caller_cwd)
            if build_dir.exists() or build_dir.is_symlink():
                raise WriteOnceError(f"Refusing to overwrite existing build folder: {build_dir}")
            m = _BUILD_DIR_RE.match(build_dir.name)
            build_num = int(m.group(1)) if m else 1
        else:
            build_dir, build_num = next_build_dir(builds_root_abs)

        try:
            build_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise WriteOnceError(f"Refusing to overwrite existing build folder: {build_dir}") from exc

        snapshot_path = build_dir / "pre_merge_snapshot.json"
        tree_path = build_dir / "tree.json"
        build_log_path = build_dir / "build_log.json"
        manifest_path = build_dir / "build_manifest.json"

        ledger = CostLedger(ledger_abs)
        usage_log = UsageLogger(usage_abs)
        spent_before = ledger.spent_usd
        start_utc = now_utc_iso()

        with contextlib.chdir(build_dir):
            with instrument_tree_build(snapshot_path, classic_mod=classic_mod) as inst_state:
                tree_result = build_fn(
                    str(pdf_abs),
                    settings,
                    ledger=ledger,
                    usage_log=usage_log,
                )

        end_utc = now_utc_iso()
        _finalize_build_log(build_dir, build_log_path)
        write_once_json(tree_path, tree_result)
        tree_sha256 = hashlib.sha256(tree_path.read_bytes()).hexdigest()

        manifest = {
            "schema": BUILD_MANIFEST_SCHEMA,
            "build_name": build_dir.name,
            "build_number": build_num,
            "pdf_path": str(pdf_abs),
            "pdf_sha256": sha256_file(pdf_abs),
            "config": {
                "index_mode": settings.index_mode,
                "index_model": settings.index_model,
                "summary_model": settings.summary_model,
                "toc_check_page_num": settings.toc_check_page_num,
                "max_page_num_each_node": settings.max_page_num_each_node,
                "max_token_num_each_node": settings.max_token_num_each_node,
                "if_add_node_id": settings.if_add_node_id,
                "if_add_node_summary": settings.if_add_node_summary,
                "if_add_doc_description": settings.if_add_doc_description,
                "if_add_node_text": settings.if_add_node_text,
                "litellm_max_tokens": settings.litellm_max_tokens,
                "tree_reserve_max_tokens": settings.tree_reserve_max_tokens,
                "overrides": {
                    "max_page_num_each_node": max_page_num_each_node,
                    "max_token_num_each_node": max_token_num_each_node,
                },
            },
            "pageindex_commit": installed_pageindex_commit(),
            "pageindex_pin_sha": PAGEINDEX_PIN_SHA,
            "litellm_version": _litellm_version(),
            "start_utc": start_utc,
            "end_utc": end_utc,
            "build_log": str(build_log_path),
            "pre_merge_snapshot": str(snapshot_path),
            "tree_path": str(tree_path),
            "tree_sha256": tree_sha256,
            "counts": _build_counts(tree_result, inst_state),
            "ledger_totals": _ledger_totals(ledger, spent_before),
        }
        write_once_json(manifest_path, manifest)
        return manifest


def format_build_summary(manifest: dict[str, Any]) -> str:
    """Format strictly non-text build summary (counts, SHAs, config, UTC, ledger totals) for stdout."""
    summary = {
        "schema": manifest["schema"],
        "build_name": manifest["build_name"],
        "build_number": manifest["build_number"],
        "pdf_sha256": manifest["pdf_sha256"],
        "config": manifest["config"],
        "pageindex_commit": manifest["pageindex_commit"],
        "litellm_version": manifest["litellm_version"],
        "start_utc": manifest["start_utc"],
        "end_utc": manifest["end_utc"],
        "build_log": Path(manifest["build_log"]).name,
        "tree_sha256": manifest["tree_sha256"],
        "counts": manifest["counts"],
        "ledger_totals": manifest["ledger_totals"],
    }
    return json.dumps(summary, indent=2, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    default_ledger, default_usage, default_lock = default_s11_paths(ROOT)
    ap = argparse.ArgumentParser(
        prog="python -m eval.s11.tree_build",
        description="S11 write-once instrumented PageIndex tree builder (max 2 builds).",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="Run one write-once tree build (build_1 or build_2)")
    p_build.add_argument("--pdf", required=True, help="Path to source PDF")
    p_build.add_argument("--builds-root", default=str(DEFAULT_BUILDS_ROOT), help="Builds root directory (default: data/s11/tree)")
    p_build.add_argument("--real", action="store_true", help="Required explicit flag confirming a live real-mode build")
    p_build.add_argument("--max-page-num-each-node", type=int, default=None, help="K4 override for max_page_num_each_node (default: 10)")
    p_build.add_argument("--max-token-num-each-node", type=int, default=None, help="K4 override for max_token_num_each_node (default: 20000)")
    p_build.add_argument("--ledger", default=str(default_ledger), help="Cost ledger JSONL path")
    p_build.add_argument("--usage-log", default=str(default_usage), help="Usage log JSONL path")
    p_build.add_argument("--lock", default=str(default_lock), help="Paid lock file path")

    args = ap.parse_args(argv)
    if args.command != "build":
        return 2

    try:
        manifest = run_tree_build(
            args.pdf,
            builds_root=args.builds_root,
            real=args.real,
            max_page_num_each_node=args.max_page_num_each_node,
            max_token_num_each_node=args.max_token_num_each_node,
            ledger_path=args.ledger,
            usage_log_path=args.usage_log,
            lock_path=args.lock,
        )
    except (RealModeRequiredError, ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except (BuildLimitError, WriteOnceError, PaidLockHeld) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(format_build_summary(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "BUILD_MANIFEST_SCHEMA",
    "BuildLimitError",
    "DEFAULT_BUILDS_ROOT",
    "MAX_BUILDS",
    "REGISTERED_IF_ADD_DOC_DESCRIPTION",
    "REGISTERED_IF_ADD_NODE_ID",
    "REGISTERED_IF_ADD_NODE_SUMMARY",
    "REGISTERED_IF_ADD_NODE_TEXT",
    "REGISTERED_INDEX_MODE",
    "REGISTERED_INDEX_MODEL",
    "REGISTERED_LITELLM_MAX_TOKENS",
    "REGISTERED_MAX_PAGE_NUM_EACH_NODE",
    "REGISTERED_MAX_TOKEN_NUM_EACH_NODE",
    "REGISTERED_SUMMARY_MODEL",
    "REGISTERED_TOC_CHECK_PAGE_NUM",
    "REGISTERED_TREE_RESERVE_MAX_TOKENS",
    "RealModeRequiredError",
    "build_registered_settings",
    "format_build_summary",
    "main",
    "next_build_dir",
    "run_tree_build",
]
