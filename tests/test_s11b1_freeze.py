"""Offline tests for eval.s11.freeze on temporary files inside pytest's tmp_path only."""
import hashlib
import json
from pathlib import Path

import pytest

import _netguard
from eval.s11 import freeze


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    _netguard.block_network(monkeypatch, "freeze tests forbid network")


def test_freeze_writes_schema_and_normalises_crlf(tmp_path):
    lf_bytes = b"line one\nline two\n"
    crlf_bytes = b"line one\r\nline two\r\n"
    (tmp_path / "lf.txt").write_bytes(lf_bytes)
    (tmp_path / "crlf.txt").write_bytes(crlf_bytes)

    _, doc_lf = freeze.freeze_manifest(["lf.txt"], "manifests/lf.json", root=tmp_path, now="2026-11-26T00:00:00+00:00")
    manifest_path, doc_crlf = freeze.freeze_manifest(
        ["crlf.txt"], "manifests/crlf.json", root=tmp_path, now="2026-11-26T00:00:00+00:00"
    )

    assert set(doc_crlf) == {"schema", "created_utc", "files"}
    assert doc_crlf["schema"] == freeze.SCHEMA
    assert doc_crlf["created_utc"] == "2026-11-26T00:00:00+00:00"
    assert len(doc_crlf["files"]) == 1
    entry_lf = doc_lf["files"][0]
    entry_crlf = doc_crlf["files"][0]
    assert set(entry_crlf) == {"path", "sha256_lf", "bytes_lf"}
    assert entry_crlf["sha256_lf"] == entry_lf["sha256_lf"] == hashlib.sha256(lf_bytes).hexdigest()
    assert entry_crlf["bytes_lf"] == entry_lf["bytes_lf"] == len(lf_bytes)
    assert b"\r\n" not in manifest_path.read_bytes()


def test_freeze_multiple_files_sorted_and_posix_paths(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "b.md").write_bytes(b"bravo\r\n")
    (tmp_path / "sub" / "a.md").write_bytes(b"alpha\n")

    manifest_path, doc = freeze.freeze_manifest(["b.md", r"sub\a.md"], r"out\m.json", root=tmp_path)
    assert manifest_path == tmp_path / "out" / "m.json"
    assert [f["path"] for f in doc["files"]] == ["b.md", "sub/a.md"]
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["files"][0]["sha256_lf"] == hashlib.sha256(b"bravo\n").hexdigest()
    assert saved["files"][1]["sha256_lf"] == hashlib.sha256(b"alpha\n").hexdigest()


def test_freeze_refuses_existing_manifest_write_once(tmp_path, capsys):
    (tmp_path / "doc.md").write_bytes(b"v1\n")
    assert freeze.main(["freeze", "--manifest", "m.json", "doc.md"], root=tmp_path) == 0
    original = (tmp_path / "m.json").read_bytes()

    (tmp_path / "doc.md").write_bytes(b"v2\n")
    with pytest.raises(FileExistsError):
        freeze.freeze_manifest(["doc.md"], "m.json", root=tmp_path)
    assert freeze.main(["freeze", "--manifest", "m.json", "doc.md"], root=tmp_path) == 3
    assert "REFUSED" in capsys.readouterr().err
    assert (tmp_path / "m.json").read_bytes() == original


def test_freeze_refuses_existing_symlink_manifest(tmp_path):
    (tmp_path / "doc.md").write_bytes(b"v1\n")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(tmp_path / "missing_target.json")
    except OSError:
        pytest.skip("symlinks unavailable in this environment")
    assert freeze.main(["freeze", "--manifest", "link.json", "doc.md"], root=tmp_path) == 3


def test_check_passes_when_line_endings_change_crlf_lf(tmp_path, capsys):
    target = tmp_path / "spec.md"
    target.write_bytes(b"alpha\nbeta\n")
    assert freeze.main(["freeze", "--manifest", "m.json", "spec.md"], root=tmp_path) == 0

    target.write_bytes(b"alpha\r\nbeta\r\n")
    assert freeze.main(["check", "--manifest", "m.json"], root=tmp_path) == 0
    assert "OK m.json" in capsys.readouterr().out


def test_check_fails_on_modification_missing_or_corrupt_manifest(tmp_path, capsys):
    target = tmp_path / "spec.md"
    target.write_bytes(b"original\n")
    assert freeze.main(["freeze", "--manifest", "m.json", "spec.md"], root=tmp_path) == 0

    target.write_bytes(b"modified\n")
    assert freeze.main(["check", "--manifest", "m.json"], root=tmp_path) == 1
    assert "MISMATCH" in capsys.readouterr().err

    target.unlink()
    assert freeze.main(["check", "--manifest", "m.json"], root=tmp_path) == 1
    assert "missing" in capsys.readouterr().err

    (tmp_path / "bad.json").write_text('{"schema": "wrong"}', encoding="utf-8")
    assert freeze.main(["check", "--manifest", "bad.json"], root=tmp_path) == 2
    assert freeze.main(["check", "--manifest", "absent.json"], root=tmp_path) == 2


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        " ",
        ".",
        "../outside.txt",
        "sub/../../outside.txt",
        "/abs/path.txt",
        "C:drive.txt",
        ".env",
        ".env.local",
        ".git/config",
        "sub/.env",
    ],
)
def test_path_guards_reject_unsafe_inputs(tmp_path, bad_path):
    (tmp_path / "ok.txt").write_bytes(b"ok\n")
    assert freeze.main(["freeze", "--manifest", "m.json", bad_path], root=tmp_path) == 2
    assert not (tmp_path / "m.json").exists()
    assert freeze.main(["freeze", "--manifest", bad_path, "ok.txt"], root=tmp_path) == 2


def test_path_guards_reject_duplicates_and_manifest_collision(tmp_path):
    (tmp_path / "ok.txt").write_bytes(b"ok\n")
    assert freeze.main(["freeze", "--manifest", "m.json", "ok.txt", "./ok.txt"], root=tmp_path) == 2
    assert not (tmp_path / "m.json").exists()
    assert freeze.main(["freeze", "--manifest", "ok.txt", "ok.txt"], root=tmp_path) == 2
    assert not (tmp_path / "m.json").exists()


def test_path_guards_reject_symlink_escape(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"secret\n")
    link = repo_root / "escape.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable in this Windows environment")
    assert freeze.main(["freeze", "--manifest", "m.json", "escape.txt"], root=repo_root) == 2
    assert not (repo_root / "m.json").exists()


def test_cli_freeze_and_check_roundtrip(tmp_path, capsys):
    secret_marker = "UNIQUE_BODY_TEXT_NOT_PRINTED"
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "S11.md").write_bytes(f"{secret_marker}\r\n".encode("utf-8"))
    expected_sha = hashlib.sha256(f"{secret_marker}\n".encode("utf-8")).hexdigest()

    rc_freeze = freeze.main(
        ["freeze", "--manifest", "eval/s11/manifests/prereg_v1.json", "specs/S11.md"],
        root=tmp_path,
    )
    out_freeze = capsys.readouterr().out
    assert rc_freeze == 0
    assert expected_sha in out_freeze
    assert secret_marker not in out_freeze

    rc_check = freeze.main(["check", "--manifest", "eval/s11/manifests/prereg_v1.json"], root=tmp_path)
    out_check = capsys.readouterr().out
    assert rc_check == 0
    assert expected_sha in out_check
    assert secret_marker not in out_check
