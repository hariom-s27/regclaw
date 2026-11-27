"""Offline tests for eval.s11.tree_instrument (real mode, F1 verify_toc contextvar, F2 pre-merge, D5)."""
import asyncio
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace as NS

import pytest

import _netguard
from eval.s11 import tree_audit, tree_instrument
from eval.s11.ledger import WriteOnceError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("REGCLAW_STUB", "1")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    _netguard.block_network(monkeypatch, "s11b2b tree_instrument tests forbid network")


def _make_fake_classic():
    class FakeClassic:
        async def check_title_appearance(item, page_list, start_index=1, model=None):
            if item.get("fail_me"):
                raise RuntimeError("boom")
            return {"list_index": item.get("list_index"), "answer": "yes"}

        async def verify_toc(page_list, list_result, start_index=1, N=None, model=None):
            tasks = [
                FakeClassic.check_title_appearance(it, page_list, start_index, model)
                for it in list_result
            ]
            res = await asyncio.gather(*tasks, return_exceptions=True)
            skipped = sum(1 for r in res if isinstance(r, BaseException))
            return (1.0 if not skipped else 0.7), []

        async def meta_processor(page_list, mode=None, toc_content=None, toc_page_list=None, start_index=1, opt=None, logger=None):
            # Emulate PageIndex's hardcoded logger bug alongside real recursive fallbacks
            if mode == "process_toc_with_page_numbers" and start_index == 1:
                return await FakeClassic.meta_processor(
                    page_list,
                    mode="process_toc_no_page_numbers",
                    start_index=start_index,
                    opt=opt,
                    logger=logger,
                )
            return [{"title": "S1", "physical_index": start_index}]

        def merge_tree(structure):
            for node in structure:
                kids = node.pop("nodes", None) or []
                if kids:
                    node["key_items"] = [k["title"] for k in kids]
            return structure

    return FakeClassic


def test_required_snapshot_path_and_write_once(tmp_path):
    fake_mod = _make_fake_classic()

    with pytest.raises(TypeError):
        with tree_instrument.instrument_tree_build():  # type: ignore[call-arg]
            pass

    for bad in (None, "", "   "):
        with pytest.raises(ValueError, match="snapshot_path"):
            with tree_instrument.instrument_tree_build(bad, classic_mod=fake_mod):  # type: ignore[arg-type]
                pass

    snap = tmp_path / "snap.json"
    snap.write_text("already here", encoding="utf-8")
    with pytest.raises(WriteOnceError):
        with tree_instrument.instrument_tree_build(snap, classic_mod=fake_mod):
            pass

    fresh = tmp_path / "fresh_snap.json"
    with tree_instrument.instrument_tree_build(fresh, classic_mod=fake_mod) as state:
        asyncio.run(fake_mod.meta_processor([], mode="process_toc_with_page_numbers", start_index=1))
        assert state.real_processing_mode == "process_toc_no_page_numbers"
    assert fresh.exists()
    with pytest.raises(WriteOnceError):
        with tree_instrument.instrument_tree_build(fresh, classic_mod=fake_mod):
            pass


def test_real_meta_processor_mode_vs_hardcoded_log_and_child_split(tmp_path):
    fake_mod = _make_fake_classic()
    snap = tmp_path / "snap.json"

    with tree_instrument.instrument_tree_build(snap, classic_mod=fake_mod) as state:
        async def _run():
            # Root call falls back process_toc_with_page_numbers -> process_toc_no_page_numbers
            await fake_mod.meta_processor(list(range(10)), mode="process_toc_with_page_numbers", start_index=1)
            # Subsequent process_large_node_recursively runs start_index=35 with mode=process_no_toc
            await fake_mod.meta_processor(list(range(5)), mode="process_no_toc", start_index=35)

        asyncio.run(_run())

    assert state.real_processing_mode == "process_toc_no_page_numbers"
    assert len(state.meta_processor_calls) == 3
    assert [c["mode"] for c in state.meta_processor_calls] == [
        "process_toc_with_page_numbers",
        "process_toc_no_page_numbers",
        "process_no_toc",
    ]


def test_verify_toc_contextvar_counts_only_inside_verify_toc_f1(tmp_path):
    # Confirm F1 uses contextvar without any inspect/frame hacks
    src = inspect.getsource(tree_instrument)
    assert "_getframe" not in src and "inspect.stack" not in src

    fake_mod = _make_fake_classic()
    snap = tmp_path / "snap.json"

    with tree_instrument.instrument_tree_build(snap, classic_mod=fake_mod) as state:
        async def _run():
            # 1 raising check outside verify_toc (e.g. fix_incorrect_toc) -> NOT counted
            with pytest.raises(RuntimeError):
                await fake_mod.check_title_appearance({"list_index": 0, "fail_me": True}, [])

            # Run verify_toc concurrently with another failing outside task
            async def outside_fail():
                await asyncio.sleep(0)
                with pytest.raises(RuntimeError):
                    await fake_mod.check_title_appearance({"list_index": 99, "fail_me": True}, [])

            await asyncio.gather(
                fake_mod.verify_toc(
                    [],
                    [
                        {"list_index": 0, "physical_index": 20, "fail_me": False},
                        {"list_index": 1, "physical_index": 25, "fail_me": True},
                        {"list_index": 2, "physical_index": 30, "fail_me": True},
                    ],
                ),
                outside_fail(),
            )

        asyncio.run(_run())

    assert state.skipped_verify_count == 2
    assert [e["list_index"] for e in state.skipped_verify_errors] == [1, 2]


def test_merge_tree_wraps_call_site_only_one_snapshot_per_call_f2_d5(tmp_path):
    fake_mod = _make_fake_classic()
    orig_check = fake_mod.check_title_appearance
    orig_verify = fake_mod.verify_toc
    orig_meta = fake_mod.meta_processor
    orig_merge = fake_mod.merge_tree

    # Separate tree_optimize namespace must NOT be wrapped per D5
    other_opt = NS(merge_tree=lambda s: s)
    orig_other_merge = other_opt.merge_tree

    snap = tmp_path / "pre_merge.json"
    struct = [
        {
            "title": "Chapter 1",
            "start_index": 16,
            "end_index": 18,
            "nodes": [
                {"title": "Sub 1.1", "start_index": 16, "end_index": 18},
            ],
        }
    ]

    with tree_instrument.instrument_tree_build(snap, classic_mod=fake_mod) as state:
        assert other_opt.merge_tree is orig_other_merge
        fake_mod.merge_tree(struct)
        fake_mod.merge_tree([{"title": "Solo", "start_index": 19, "end_index": 20}])

    assert fake_mod.check_title_appearance is orig_check
    assert fake_mod.verify_toc is orig_verify
    assert fake_mod.meta_processor is orig_meta
    assert fake_mod.merge_tree is orig_merge

    assert len(state.snapshots) == 2
    # Live struct was mutated post-merge and does NOT carry pre-0000 ids
    assert "nodes" not in struct[0] and "key_items" in struct[0] and "node_id" not in struct[0]

    saved = json.loads(snap.read_text(encoding="utf-8"))
    assert saved["pre_merge_call_count"] == 2
    assert saved["snapshots"][0]["pre_node_count"] == 2
    assert saved["snapshots"][0]["post_node_count"] == 1
    assert saved["snapshots"][0]["structure"][0]["node_id"] == "pre-0000"
    assert saved["snapshots"][0]["structure"][0]["nodes"][0]["node_id"] == "pre-0001"
    assert saved["snapshots"][1]["structure"][0]["node_id"] == "pre-0000"


def test_unpatches_on_exception_and_creates_no_snapshot_file(tmp_path):
    fake_mod = _make_fake_classic()
    orig_merge = fake_mod.merge_tree
    snap = tmp_path / "aborted.json"

    with pytest.raises(RuntimeError, match="abort"):
        with tree_instrument.instrument_tree_build(snap, classic_mod=fake_mod):
            raise RuntimeError("abort")

    assert fake_mod.merge_tree is orig_merge
    assert not snap.exists()


def test_installed_pageindex_integration_under_netguard(tmp_path, monkeypatch):
    classic = pytest.importorskip("pageindex.page_index_classic")
    tree_opt = pytest.importorskip("pageindex.tree_optimize")

    orig_other_merge = tree_opt.merge_tree
    orig_classic_merge = classic.merge_tree

    async def scripted_acompletion(model=None, prompt=None, **kw):
        if "First Item" in (prompt or ""):
            return '{"answer": "yes"}'
        raise RuntimeError("simulated verify failure")

    monkeypatch.setattr(classic, "llm_acompletion", scripted_acompletion)
    snap = tmp_path / "real_pkg_snap.json"

    with tree_instrument.instrument_tree_build(snap) as state:
        # D5: pageindex.tree_optimize.merge_tree must remain unpatched
        assert tree_opt.merge_tree is orig_other_merge
        assert classic.merge_tree is not orig_classic_merge

        page_list = [("First Item body", 10), ("Second Item body", 10), ("Third page", 10)]
        toc = [
            {"title": "First Item", "physical_index": 1},
            {"title": "Second Item", "physical_index": 2},
        ]
        acc, incorrect = asyncio.run(classic.verify_toc(page_list, toc, start_index=1, model="mock"))
        assert acc == 1.0 and incorrect == []

        # Subtree of 2 tiny 1-page children -> S=2 <= tree_cost=1+1=2 -> merge_tree collapses children into key_items
        structure = [
            {
                "title": "Parent Section",
                "start_index": 16,
                "end_index": 17,
                "nodes": [
                    {"title": "Child One", "start_index": 16, "end_index": 16},
                    {"title": "Child Two", "start_index": 17, "end_index": 17},
                ],
            }
        ]
        classic.merge_tree(structure)

    assert classic.merge_tree is orig_classic_merge
    assert state.skipped_verify_count == 1
    assert len(state.snapshots) == 1
    # Live structure was merged into key_items while snapshot kept both children
    assert "nodes" not in structure[0] and structure[0]["key_items"] == ["Child One", "Child Two"]
    snap_nodes = state.snapshots[0]["structure"]
    assert snap_nodes[0]["node_id"] == "pre-0000"
    assert [c["node_id"] for c in snap_nodes[0]["nodes"]] == ["pre-0001", "pre-0002"]

    # Pre-merge snapshot loads cleanly into Variant C in tree_audit
    saved = json.loads(snap.read_text(encoding="utf-8"))
    entries = [
        {"index": 0, "toc_page": 10, "title": "Child One", "printed_page": 5, "expected_start": 16, "is_annexure": False, "annexure_numeral": None, "parse_fallback": False},
        {"index": 1, "toc_page": 10, "title": "Child Two", "printed_page": 6, "expected_start": 17, "is_annexure": False, "annexure_numeral": None, "parse_fallback": False},
    ]
    res = tree_audit.audit_tree(
        {"doc_name": "x.pdf", "structure": structure},
        entries,
        pre_merge_tree=saved,
        coverage_start=16,
        coverage_end=17,
        expected_annexures=0,
    )
    assert res["variants"]["C"]["criteria"]["presence_95pct"]["pass"] is True
    assert [m["report_id"] for m in res["variants"]["C"]["matches"]] == ["pre-0001", "pre-0002"]


def test_fresh_subprocess_import_tree_instrument_under_netguard():
    guarded_code = textwrap.dedent(
        """
        import builtins
        import os
        import socket
        from pathlib import Path

        data_dir = Path("data").resolve()

        def deny_net(*a, **k):
            raise AssertionError("Network forbidden")

        socket.socket.connect = deny_net
        orig_open = builtins.open

        def guarded_open(file, *a, **k):
            p = Path(os.fspath(file)).resolve() if not isinstance(file, int) else None
            assert p is None or (p != data_dir and not p.is_relative_to(data_dir)), f"data access forbidden: {p}"
            return orig_open(file, *a, **k)

        builtins.open = guarded_open
        import eval.s11.tree_instrument
        assert hasattr(eval.s11.tree_instrument, "instrument_tree_build")
        """
    ).strip()
    proc = subprocess.run(
        [sys.executable, "-c", guarded_code],
        cwd=ROOT,
        env={**os.environ, "REGCLAW_STUB": "1", "LANGFUSE_ENABLED": "false"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
