"""Offline tests for eval.s11.tree_build (S11b2c-06, K4, K5, paid_lock, R2 leak check)."""
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import textwrap
import threading
from types import ModuleType

import pytest

import _netguard
from _s11_leakcheck import assert_no_source_words
from eval.s11 import tree_build
from eval.s11.ledger import WriteOnceError, paid_lock

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("REGCLAW_STUB", "1")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    _netguard.block_network(monkeypatch, "s11b2c tree_build tests forbid network")


def _make_fake_classic():
    class FakeClassic:
        async def check_title_appearance(item, page_list, start_index=1, model=None):
            return {"list_index": item.get("list_index"), "answer": "yes"}

        async def verify_toc(page_list, list_result, start_index=1, N=None, model=None):
            return 1.0, []

        async def meta_processor(page_list, mode=None, toc_content=None, toc_page_list=None, start_index=1, opt=None, logger=None):
            return [{"title": "S1", "physical_index": start_index}]

        def merge_tree(structure):
            for node in structure:
                kids = node.pop("nodes", None) or []
                if kids:
                    node["key_items"] = [k["title"] for k in kids]
            return structure

    return FakeClassic


def _patch_build_env(monkeypatch, fake_classic, fake_build_fn):
    fake_pkg = ModuleType("pageindex")
    fake_pkg.page_index_classic = fake_classic
    monkeypatch.setitem(sys.modules, "pageindex", fake_pkg)
    monkeypatch.setitem(sys.modules, "pageindex.page_index_classic", fake_classic)
    monkeypatch.setattr(tree_build.pi_ingest, "build_tree", fake_build_fn)
    monkeypatch.setattr(tree_build, "installed_pageindex_commit", lambda: tree_build.PAGEINDEX_PIN_SHA)


def _dummy_pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 synthetic\n")
    return pdf


_LOCK_HOLDER_CHILD = """
import queue, socket, sys, threading
from pathlib import Path
lock_path = Path(sys.argv[1]).resolve()
def denied(*a, **k): raise AssertionError('child network forbidden')
socket.socket.connect = denied
from eval.s11.ledger import paid_lock
with paid_lock(lock_path=lock_path):
    print('READY', flush=True)
    q = queue.Queue()
    threading.Thread(target=lambda: q.put(sys.stdin.readline()), daemon=True).start()
    q.get(timeout=30)
"""


@contextmanager
def _holding_lock_child(lock_path: Path):
    proc = subprocess.Popen(
        [sys.executable, "-B", "-c", _LOCK_HOLDER_CHILD, str(lock_path)],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready = queue.Queue()
    reader = threading.Thread(target=lambda: ready.put(proc.stdout.readline()), daemon=True)
    reader.start()
    try:
        assert ready.get(timeout=30) == "READY\n"
        yield proc
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=30)
        reader.join(timeout=30)
        for s in (proc.stdin, proc.stdout, proc.stderr):
            s.close()


def test_tree_build_refuses_stub_mode_or_missing_real_flag(tmp_path, monkeypatch):
    pdf = _dummy_pdf(tmp_path)
    builds_root = tmp_path / "tree_builds"
    calls = []
    _patch_build_env(monkeypatch, _make_fake_classic(), lambda *a, **k: calls.append(a) or {"structure": []})

    common_args = [
        "build",
        "--pdf",
        str(pdf),
        "--builds-root",
        str(builds_root),
        "--ledger",
        str(tmp_path / "ledger.jsonl"),
        "--usage-log",
        str(tmp_path / "usage.jsonl"),
        "--lock",
        str(tmp_path / "s11.lock"),
    ]

    # Case 1: REGCLAW_STUB=1 (set by autouse fixture) even with --real -> refused
    rc_stub = tree_build.main([*common_args, "--real"])
    assert rc_stub == 2
    assert not builds_root.exists()
    assert calls == []

    # Case 2: REGCLAW_STUB unset but --real omitted -> refused
    monkeypatch.delenv("REGCLAW_STUB", raising=False)
    rc_no_real = tree_build.main(common_args)
    assert rc_no_real == 2
    assert not builds_root.exists()
    assert calls == []


def test_tree_build_refuses_third_build_and_existing_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("REGCLAW_STUB", raising=False)
    pdf = _dummy_pdf(tmp_path)
    builds_root = tmp_path / "tree_builds"
    fake_classic = _make_fake_classic()
    calls = []

    def fake_build(pdf_arg, settings, *, ledger, usage_log):
        calls.append(pdf_arg)
        return {"doc_name": "doc.pdf", "structure": [{"title": "S1", "node_id": "0000", "start_index": 1, "end_index": 2}]}

    _patch_build_env(monkeypatch, fake_classic, fake_build)
    base_args = [
        "build",
        "--pdf",
        str(pdf),
        "--builds-root",
        str(builds_root),
        "--real",
        "--ledger",
        str(tmp_path / "ledger.jsonl"),
        "--usage-log",
        str(tmp_path / "usage.jsonl"),
        "--lock",
        str(tmp_path / "s11.lock"),
    ]

    assert tree_build.main(base_args) == 0
    assert tree_build.main(base_args) == 0
    assert (builds_root / "build_1" / "tree.json").is_file()
    assert (builds_root / "build_2" / "tree.json").is_file()

    # Third build refused, nothing written
    rc3 = tree_build.main(base_args)
    assert rc3 == 3
    assert not (builds_root / "build_3").exists()
    assert len(calls) == 2

    # Existing target folder refused write-once (pre-existing build_2 without build_1 -> next target build_2 exists)
    builds_root2 = tmp_path / "tree_builds2"
    pre_b2 = builds_root2 / "build_2"
    pre_b2.mkdir(parents=True)
    sentinel = pre_b2 / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    rc_exists = tree_build.main(
        [
            "build",
            "--pdf",
            str(pdf),
            "--builds-root",
            str(builds_root2),
            "--real",
            "--ledger",
            str(tmp_path / "ledger2.jsonl"),
            "--usage-log",
            str(tmp_path / "usage2.jsonl"),
            "--lock",
            str(tmp_path / "s11_2.lock"),
        ]
    )
    assert rc_exists == 3
    assert list(pre_b2.iterdir()) == [sentinel]
    assert sentinel.read_text(encoding="utf-8") == "keep"

    # Direct override targeting an existing folder also raises WriteOnceError
    with pytest.raises(WriteOnceError):
        tree_build.run_tree_build(
            pdf,
            real=True,
            ledger_path=tmp_path / "ledger2.jsonl",
            usage_log_path=tmp_path / "usage2.jsonl",
            lock_path=tmp_path / "s11_2.lock",
            classic_mod=fake_classic,
            build_tree_fn=fake_build,
            build_dir_override=pre_b2,
        )


def test_tree_build_refuses_held_paid_lock(tmp_path, monkeypatch):
    monkeypatch.delenv("REGCLAW_STUB", raising=False)
    pdf = _dummy_pdf(tmp_path)
    builds_root = tmp_path / "locked_builds"
    lock_path = tmp_path / "runs" / "s11_paid.lock"
    calls = []
    _patch_build_env(
        monkeypatch,
        _make_fake_classic(),
        lambda *a, **k: calls.append(a) or {"doc_name": "doc.pdf", "structure": []},
    )

    args = [
        "build",
        "--pdf",
        str(pdf),
        "--builds-root",
        str(builds_root),
        "--real",
        "--ledger",
        str(tmp_path / "runs" / "ledger.jsonl"),
        "--usage-log",
        str(tmp_path / "runs" / "usage.jsonl"),
        "--lock",
        str(lock_path),
    ]

    with _holding_lock_child(lock_path):
        rc_locked = tree_build.main(args)
        assert rc_locked == 3
        assert not builds_root.exists()
        assert calls == []

    # After child releases lock, build succeeds cleanly
    assert tree_build.main(args) == 0
    assert (builds_root / "build_1" / "build_manifest.json").is_file()


def test_tree_build_instrument_restored_after_fake_build_tree_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("REGCLAW_STUB", raising=False)
    pdf = _dummy_pdf(tmp_path)
    builds_root = tmp_path / "fail_builds"
    lock_path = tmp_path / "s11.lock"
    fake_classic = _make_fake_classic()
    orig_meta = fake_classic.meta_processor
    orig_verify = fake_classic.verify_toc
    orig_check = fake_classic.check_title_appearance
    orig_merge = fake_classic.merge_tree
    cwd_before = Path.cwd()

    def failing_build(pdf_arg, settings, *, ledger, usage_log):
        assert fake_classic.merge_tree is not orig_merge
        raise RuntimeError("simulated build boom")

    _patch_build_env(monkeypatch, fake_classic, failing_build)
    rc = tree_build.main(
        [
            "build",
            "--pdf",
            str(pdf),
            "--builds-root",
            str(builds_root),
            "--real",
            "--ledger",
            str(tmp_path / "ledger.jsonl"),
            "--usage-log",
            str(tmp_path / "usage.jsonl"),
            "--lock",
            str(lock_path),
        ]
    )
    assert rc == 1
    assert Path.cwd() == cwd_before
    assert fake_classic.meta_processor is orig_meta
    assert fake_classic.verify_toc is orig_verify
    assert fake_classic.check_title_appearance is orig_check
    assert fake_classic.merge_tree is orig_merge

    b1 = builds_root / "build_1"
    assert not (b1 / "pre_merge_snapshot.json").exists()
    assert not (b1 / "tree.json").exists()
    assert not (b1 / "build_manifest.json").exists()

    # Paid lock must be released even after build failure
    with paid_lock(lock_path=lock_path) as acquired:
        assert acquired == lock_path


def test_tree_build_chdir_jsonlogger_manifest_and_zero_leak(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("REGCLAW_STUB", raising=False)
    pdf = _dummy_pdf(tmp_path)
    builds_root = tmp_path / "ok_builds"
    repo_logs_dir = ROOT / "logs"
    repo_logs_before = set(repo_logs_dir.iterdir()) if repo_logs_dir.exists() else set()
    fake_classic = _make_fake_classic()
    seen = {}

    def fake_build(pdf_arg, settings, *, ledger, usage_log):
        seen["cwd"] = Path.cwd()
        seen["pdf_arg"] = pdf_arg
        seen["settings"] = settings

        # Simulate PageIndex JsonLogger writing to ./logs/<pdf>_<ts>.json relative to CWD
        Path("logs").mkdir(parents=True, exist_ok=True)
        Path("logs/doc.pdf_20261127_180000.json").write_text(
            json.dumps([{"toc_header": "Zephyr Quokka"}], indent=2),
            encoding="utf-8",
        )

        # Reserve & settle 1 Sonnet call: 1000 in ($2/1M) + 500 out ($10/1M) = 0.007 USD
        res = ledger.reserve(
            call_id="tree-1",
            lane="tree",
            model="claude-sonnet-5-5",
            attempt=1,
            input_estimate=1000,
            max_tokens=1000,
        )
        ledger.settle(res.reservation_id, input_tokens=1000, output_tokens=500, status="ok")

        structure = [
            {
                "title": "Marmot Wombat",
                "start_index": 1,
                "end_index": 2,
                "nodes": [
                    {"title": "Ocelot Narwhal", "start_index": 1, "end_index": 2},
                ],
            }
        ]
        fake_classic.merge_tree(structure)
        structure[0]["node_id"] = "0000"
        return {
            "doc_name": "doc.pdf",
            "doc_description": "Pangolin Axolotl",
            "structure": structure,
            "open_reservations": [],
            "wrapper_calls": 1,
            "custom_logger_calls": 1,
        }

    _patch_build_env(monkeypatch, fake_classic, fake_build)
    rc = tree_build.main(
        [
            "build",
            "--pdf",
            str(pdf),
            "--builds-root",
            str(builds_root),
            "--real",
            "--ledger",
            str(tmp_path / "ledger.jsonl"),
            "--usage-log",
            str(tmp_path / "usage.jsonl"),
            "--lock",
            str(tmp_path / "s11.lock"),
        ]
    )
    assert rc == 0
    stdout = capsys.readouterr().out

    b1 = builds_root / "build_1"
    assert seen["cwd"] == b1.resolve()
    assert Path(seen["pdf_arg"]).is_absolute()
    assert (set(repo_logs_dir.iterdir()) if repo_logs_dir.exists() else set()) == repo_logs_before

    tree_path = b1 / "tree.json"
    snap_path = b1 / "pre_merge_snapshot.json"
    log_path = b1 / "build_log.json"
    manifest_path = b1 / "build_manifest.json"
    for p in (tree_path, snap_path, log_path, manifest_path):
        assert p.is_file()
    assert not (b1 / "logs").exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == tree_build.BUILD_MANIFEST_SCHEMA
    assert manifest["build_name"] == "build_1"
    assert manifest["build_number"] == 1
    assert manifest["pageindex_commit"] == tree_build.PAGEINDEX_PIN_SHA
    assert "litellm_version" in manifest and isinstance(manifest["litellm_version"], str)
    assert manifest["start_utc"] and manifest["end_utc"]
    assert Path(manifest["build_log"]) == log_path.resolve()
    assert manifest["tree_sha256"] == hashlib.sha256(tree_path.read_bytes()).hexdigest()

    cfg = manifest["config"]
    assert cfg["index_mode"] == "standard"
    assert cfg["index_model"] == "anthropic/claude-sonnet-5-5"
    assert cfg["summary_model"] == "anthropic/claude-sonnet-5-5"
    assert cfg["toc_check_page_num"] == 20
    assert cfg["max_page_num_each_node"] == 10
    assert cfg["max_token_num_each_node"] == 20000
    assert cfg["if_add_node_summary"] == "yes"
    assert cfg["if_add_node_text"] == "no"
    assert cfg["overrides"] == {"max_page_num_each_node": None, "max_token_num_each_node": None}

    totals = manifest["ledger_totals"]
    assert Decimal(totals["spent_usd_before"]) == Decimal("0")
    assert Decimal(totals["spent_usd_after"]) == Decimal("0.007")
    assert Decimal(totals["build_spent_usd"]) == Decimal("0.007")
    assert Decimal(totals["reserved_usd"]) == Decimal("0")
    assert Decimal(totals["remaining_usd"]) == Decimal("59.993")
    assert Decimal(totals["cap_usd"]) == Decimal("60.0")

    assert manifest["counts"]["node_count"] == 1
    assert manifest["counts"]["pre_merge_call_count"] == 1
    assert_no_source_words(stdout, ["Zephyr Quokka", "Marmot Wombat", "Ocelot Narwhal", "Pangolin Axolotl"])


def test_tree_build_k4_cli_overrides_recorded_and_printed(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("REGCLAW_STUB", raising=False)
    pdf = _dummy_pdf(tmp_path)
    builds_root = tmp_path / "k4_builds"
    captured_settings = []

    def fake_build(pdf_arg, settings, *, ledger, usage_log):
        captured_settings.append(settings)
        return {"doc_name": "doc.pdf", "structure": [{"title": "S1", "node_id": "0000", "start_index": 1, "end_index": 2}]}

    _patch_build_env(monkeypatch, _make_fake_classic(), fake_build)
    rc = tree_build.main(
        [
            "build",
            "--pdf",
            str(pdf),
            "--builds-root",
            str(builds_root),
            "--real",
            "--max-page-num-each-node",
            "6",
            "--max-token-num-each-node",
            "8000",
            "--ledger",
            str(tmp_path / "ledger.jsonl"),
            "--usage-log",
            str(tmp_path / "usage.jsonl"),
            "--lock",
            str(tmp_path / "s11.lock"),
        ]
    )
    assert rc == 0
    stdout = capsys.readouterr().out
    s = captured_settings[0]
    assert s.max_page_num_each_node == 6
    assert s.max_token_num_each_node == 8000
    assert s.toc_check_page_num == 20
    assert s.index_mode == "standard"
    assert s.index_model == "anthropic/claude-sonnet-5-5"
    assert s.summary_model == "anthropic/claude-sonnet-5-5"

    manifest = json.loads((builds_root / "build_1" / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["config"]["max_page_num_each_node"] == 6
    assert manifest["config"]["max_token_num_each_node"] == 8000
    assert manifest["config"]["overrides"] == {
        "max_page_num_each_node": 6,
        "max_token_num_each_node": 8000,
    }
    out_obj = json.loads(stdout)
    assert out_obj["config"]["max_page_num_each_node"] == 6
    assert out_obj["config"]["max_token_num_each_node"] == 8000

    # Invalid node limit refused before creating folder
    rc_bad = tree_build.main(
        [
            "build",
            "--pdf",
            str(pdf),
            "--builds-root",
            str(tmp_path / "k4_bad"),
            "--real",
            "--max-page-num-each-node",
            "0",
            "--ledger",
            str(tmp_path / "ledger.jsonl"),
            "--usage-log",
            str(tmp_path / "usage.jsonl"),
            "--lock",
            str(tmp_path / "s11.lock"),
        ]
    )
    assert rc_bad == 2
    assert not (tmp_path / "k4_bad").exists()


def test_tree_build_subprocess_import_and_pageindex_compat():
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
        import eval.s11.tree_build
        assert hasattr(eval.s11.tree_build, "run_tree_build")
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

    pageindex_utils = pytest.importorskip("pageindex.utils")
    s = tree_build.build_registered_settings()
    opt = pageindex_utils.ConfigLoader().load(
        {
            "index_model": s.index_model,
            "summary_model": s.summary_model,
            "toc_check_page_num": s.toc_check_page_num,
            "max_page_num_each_node": s.max_page_num_each_node,
            "max_token_num_each_node": s.max_token_num_each_node,
            "if_add_node_id": s.if_add_node_id,
            "if_add_node_summary": s.if_add_node_summary,
            "if_add_doc_description": s.if_add_doc_description,
            "if_add_node_text": s.if_add_node_text,
        }
    )
    assert opt.index_model == "anthropic/claude-sonnet-5-5"
    assert opt.summary_model == "anthropic/claude-sonnet-5-5"
    assert opt.toc_check_page_num == 20
    assert opt.max_page_num_each_node == 10
    assert opt.max_token_num_each_node == 20000
    assert opt.if_add_node_summary == "yes"
    assert opt.if_add_node_text == "no"
