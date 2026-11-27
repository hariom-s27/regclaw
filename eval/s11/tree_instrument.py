"""S11 PageIndex build instrumentation: real mode, verify_toc skips (F1), and pre-merge snapshots (F2, D5)."""
from __future__ import annotations

from contextlib import contextmanager
import contextvars
import copy
from dataclasses import dataclass, field
import functools
import importlib
from pathlib import Path
import sys
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "pageindex") not in sys.path:
    sys.path.insert(0, str(ROOT / "pageindex"))

from eval.s11.ledger import WriteOnceError, write_once_json  # noqa: E402
from pi_common import iter_nodes  # noqa: E402

TREE_INSTRUMENT_SCHEMA = "s11-tree-instrument-v1"

_IN_VERIFY_TOC: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "s11_in_pageindex_verify_toc",
    default=False,
)


def _require_snapshot_path(snapshot_path: Path | str) -> Path:
    if snapshot_path is None or not str(snapshot_path).strip():
        raise ValueError("snapshot_path is required and cannot be empty or None")
    return Path(snapshot_path)


def assign_pre_merge_ids(structure: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deep-copy structure and assign 'pre-0000', 'pre-0001', ... node_ids in pre-order (F2)."""
    cloned = copy.deepcopy(structure)
    for idx, (node, _) in enumerate(iter_nodes(cloned)):
        node["node_id"] = f"pre-{idx:04d}"
    return cloned


@dataclass
class TreeInstrumentState:
    """Collected build telemetry from one instrumented PageIndex run."""

    snapshot_path: Path
    meta_processor_calls: list[dict[str, Any]] = field(default_factory=list)
    skipped_verify_count: int = 0
    skipped_verify_errors: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)

    @property
    def real_processing_mode(self) -> str | None:
        root_calls = [c for c in self.meta_processor_calls if c.get("start_index") == 1]
        if root_calls:
            return root_calls[-1].get("mode")
        if self.meta_processor_calls:
            return self.meta_processor_calls[-1].get("mode")
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": TREE_INSTRUMENT_SCHEMA,
            "real_processing_mode": self.real_processing_mode,
            "meta_processor_calls": list(self.meta_processor_calls),
            "skipped_verify_count": int(self.skipped_verify_count),
            "skipped_verify_errors": list(self.skipped_verify_errors),
            "pre_merge_call_count": len(self.snapshots),
            "snapshots": list(self.snapshots),
        }


@contextmanager
def instrument_tree_build(
    snapshot_path: Path | str,
    *,
    classic_mod: Any | None = None,
) -> Iterator[TreeInstrumentState]:
    """Instrument pageindex.page_index_classic during a build without mutating site-packages on disk.

    Captures:
    - Real `meta_processor` modes (despite PageIndex's hardcoded log string)
    - `skipped_verify` exceptions raised by `check_title_appearance` only while `verify_toc` runs (F1)
    - One pre-merge deep-copy snapshot per `pageindex.page_index_classic.merge_tree` call with
      `'pre-0000'..` IDs (F2, D5), persisted write-once to the required `snapshot_path`.
    """
    target = _require_snapshot_path(snapshot_path)
    if target.exists():
        raise WriteOnceError(f"Refusing to overwrite existing snapshot file: {target}")

    mod = classic_mod if classic_mod is not None else importlib.import_module("pageindex.page_index_classic")
    orig_meta_processor = getattr(mod, "meta_processor")
    orig_verify_toc = getattr(mod, "verify_toc")
    orig_check_title = getattr(mod, "check_title_appearance")
    orig_merge_tree = getattr(mod, "merge_tree")

    state = TreeInstrumentState(snapshot_path=target)

    @functools.wraps(orig_meta_processor)
    async def wrapped_meta_processor(*args: Any, **kwargs: Any) -> Any:
        page_list = kwargs["page_list"] if "page_list" in kwargs else (args[0] if args else None)
        mode = kwargs["mode"] if "mode" in kwargs else (args[1] if len(args) > 1 else None)
        start_index = kwargs["start_index"] if "start_index" in kwargs else (args[4] if len(args) > 4 else 1)
        state.meta_processor_calls.append(
            {
                "call_index": len(state.meta_processor_calls),
                "mode": mode,
                "start_index": start_index,
                "page_count": len(page_list) if page_list is not None else None,
            }
        )
        return await orig_meta_processor(*args, **kwargs)

    @functools.wraps(orig_verify_toc)
    async def wrapped_verify_toc(*args: Any, **kwargs: Any) -> Any:
        token = _IN_VERIFY_TOC.set(True)
        try:
            return await orig_verify_toc(*args, **kwargs)
        finally:
            _IN_VERIFY_TOC.reset(token)

    @functools.wraps(orig_check_title)
    async def wrapped_check_title(*args: Any, **kwargs: Any) -> Any:
        try:
            return await orig_check_title(*args, **kwargs)
        except BaseException as exc:
            if _IN_VERIFY_TOC.get():
                item = args[0] if args else kwargs.get("item")
                state.skipped_verify_count += 1
                state.skipped_verify_errors.append(
                    {
                        "list_index": item.get("list_index") if isinstance(item, dict) else None,
                        "physical_index": item.get("physical_index") if isinstance(item, dict) else None,
                        "error_type": type(exc).__name__,
                    }
                )
            raise

    @functools.wraps(orig_merge_tree)
    def wrapped_merge_tree(structure: list[dict[str, Any]], *args: Any, **kwargs: Any) -> Any:
        snapshot = assign_pre_merge_ids(structure)
        pre_count = sum(1 for _ in iter_nodes(snapshot))
        res = orig_merge_tree(structure, *args, **kwargs)
        post_struct = res if isinstance(res, list) else structure
        post_count = sum(1 for _ in iter_nodes(post_struct))
        state.snapshots.append(
            {
                "call_index": len(state.snapshots),
                "pre_node_count": pre_count,
                "post_node_count": post_count,
                "structure": snapshot,
            }
        )
        return res

    setattr(mod, "meta_processor", wrapped_meta_processor)
    setattr(mod, "verify_toc", wrapped_verify_toc)
    setattr(mod, "check_title_appearance", wrapped_check_title)
    setattr(mod, "merge_tree", wrapped_merge_tree)

    try:
        yield state
    finally:
        setattr(mod, "meta_processor", orig_meta_processor)
        setattr(mod, "verify_toc", orig_verify_toc)
        setattr(mod, "check_title_appearance", orig_check_title)
        setattr(mod, "merge_tree", orig_merge_tree)

    write_once_json(target, state.to_dict())


__all__ = [
    "TREE_INSTRUMENT_SCHEMA",
    "TreeInstrumentState",
    "assign_pre_merge_ids",
    "instrument_tree_build",
]
