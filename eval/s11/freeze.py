"""Write-once CRLF->LF SHA-256 manifest tool for S11 artifacts (stdlib only).

Usage:
  .venv\\Scripts\\python -m eval.s11.freeze freeze --manifest eval/s11/manifests/prereg_v1.json specs/S11_PREREGISTRATION.md
  .venv\\Scripts\\python -m eval.s11.freeze check --manifest eval/s11/manifests/prereg_v1.json

Exit codes:
  0  manifest written or check passed
  1  check mismatch or listed file missing
  2  invalid path, arguments, or manifest schema
  3  refused: target manifest already exists (write-once)
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

SCHEMA = "s11-manifest.v1"
ROOT = Path(__file__).resolve().parents[2]
_HEX_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class FreezeError(ValueError):
    """Path, input, or manifest validation error without exposing file contents."""


def normalise_lf(raw: bytes) -> bytes:
    """Normalise CRLF line endings to LF in memory."""
    return raw.replace(b"\r\n", b"\n")


def _has_forbidden_part(parts: tuple[str, ...]) -> bool:
    return any(
        part == ".git" or part.casefold() == ".git" or part.casefold().startswith(".env")
        for part in parts
    )


def validate_rel_path(root: Path, rel_path: str) -> tuple[Path, Path, str]:
    """Validate a repo-relative path and return (lexical_path, resolved_path, posix_rel)."""
    if not isinstance(rel_path, str) or not rel_path or rel_path != rel_path.strip():
        raise FreezeError("path must be a non-empty repo-relative string without surrounding whitespace")
    if ":" in rel_path or rel_path.startswith(("/", "\\")):
        raise FreezeError(f"path must be repo-relative without drive or leading slash: {rel_path!r}")
    norm = rel_path.replace("\\", "/")
    posix = PurePosixPath(norm)
    if posix.is_absolute() or not posix.parts or ".." in posix.parts:
        raise FreezeError(f"invalid repo-relative path: {rel_path!r}")
    if _has_forbidden_part(posix.parts):
        raise FreezeError(f"forbidden path component in {rel_path!r}")

    resolved_root = Path(root).resolve()
    lexical = resolved_root.joinpath(*posix.parts)
    resolved = lexical.resolve()
    if not resolved.is_relative_to(resolved_root) or resolved == resolved_root:
        raise FreezeError(f"path escapes repository root: {rel_path!r}")
    rel_resolved = resolved.relative_to(resolved_root)
    if _has_forbidden_part(rel_resolved.parts):
        raise FreezeError(f"resolved path enters forbidden location: {rel_path!r}")
    return lexical, resolved, posix.as_posix()


def file_entry(root: Path, rel_path: str) -> dict:
    """Compute {path, sha256_lf, bytes_lf} for one validated repo-relative file."""
    lexical, resolved, posix_rel = validate_rel_path(root, rel_path)
    if lexical.is_symlink() and not resolved.is_file():
        raise FreezeError(f"target file not found: {posix_rel}")
    if not resolved.is_file():
        raise FreezeError(f"target file not found: {posix_rel}")
    lf = normalise_lf(resolved.read_bytes())
    return {
        "path": posix_rel,
        "sha256_lf": hashlib.sha256(lf).hexdigest(),
        "bytes_lf": len(lf),
    }


def freeze_manifest(
    files: list[str],
    manifest_rel: str,
    *,
    root: Path = ROOT,
    now: str | None = None,
) -> tuple[Path, dict]:
    """Create a write-once JSON manifest for one or more repo-relative files."""
    if not files:
        raise FreezeError("at least one file path is required")
    manifest_lex, manifest_res, manifest_posix = validate_rel_path(root, manifest_rel)

    seen_posix: set[str] = set()
    seen_resolved: set[Path] = set()
    validated_rels: list[str] = []
    for rel in files:
        _, resolved, posix_rel = validate_rel_path(root, rel)
        if posix_rel in seen_posix or resolved in seen_resolved:
            raise FreezeError(f"duplicate input path: {posix_rel}")
        if posix_rel == manifest_posix or resolved == manifest_res:
            raise FreezeError(f"manifest path cannot match an input file: {manifest_posix}")
        seen_posix.add(posix_rel)
        seen_resolved.add(resolved)
        validated_rels.append(rel)

    if manifest_lex.exists() or manifest_lex.is_symlink():
        raise FileExistsError(f"manifest already exists (write-once): {manifest_posix}")

    entries = [file_entry(root, rel) for rel in validated_rels]

    entries.sort(key=lambda item: item["path"])
    doc = {
        "schema": SCHEMA,
        "created_utc": now or datetime.now(timezone.utc).isoformat(),
        "files": entries,
    }
    payload = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    manifest_lex.parent.mkdir(parents=True, exist_ok=True)
    with manifest_lex.open("x", encoding="utf-8", newline="\n") as fh:
        fh.write(payload)
    return manifest_lex, doc


def _validate_manifest_doc(root: Path, doc: object) -> list[dict]:
    if not isinstance(doc, dict) or set(doc) != {"schema", "created_utc", "files"}:
        raise FreezeError("manifest must have exact keys: schema, created_utc, files")
    if doc["schema"] != SCHEMA:
        raise FreezeError(f"unsupported manifest schema {doc['schema']!r}; expected {SCHEMA!r}")
    if not isinstance(doc["created_utc"], str) or not doc["created_utc"].strip():
        raise FreezeError("manifest created_utc must be a non-empty string")
    files = doc["files"]
    if not isinstance(files, list) or not files:
        raise FreezeError("manifest files must be a non-empty list")

    seen: set[str] = set()
    validated: list[dict] = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256_lf", "bytes_lf"}:
            raise FreezeError("each file entry must have exact keys: path, sha256_lf, bytes_lf")
        path_val = item["path"]
        _, _, posix_rel = validate_rel_path(root, path_val)
        if path_val != posix_rel:
            raise FreezeError(f"manifest path must be normalised POSIX: {path_val!r}")
        if posix_rel in seen:
            raise FreezeError(f"duplicate path in manifest: {posix_rel}")
        seen.add(posix_rel)
        sha_val = item["sha256_lf"]
        if not isinstance(sha_val, str) or not _HEX_SHA_RE.match(sha_val):
            raise FreezeError(f"invalid sha256_lf for {posix_rel}")
        bytes_val = item["bytes_lf"]
        if type(bytes_val) is not int or bytes_val < 0:
            raise FreezeError(f"invalid bytes_lf for {posix_rel}")
        validated.append(item)
    return validated


def check_manifest(manifest_rel: str, *, root: Path = ROOT) -> tuple[bool, list[str]]:
    """Recompute LF-normalised sha256 and byte count for every file in a manifest."""
    manifest_lex, manifest_res, manifest_posix = validate_rel_path(root, manifest_rel)
    if manifest_lex.is_symlink() and not manifest_res.is_file():
        raise FreezeError(f"manifest not found: {manifest_posix}")
    if not manifest_res.is_file():
        raise FreezeError(f"manifest not found: {manifest_posix}")
    try:
        doc = json.loads(manifest_res.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreezeError(f"unreadable or invalid JSON manifest {manifest_posix}") from exc

    entries = _validate_manifest_doc(root, doc)
    messages: list[str] = []
    all_ok = True
    for expected in entries:
        rel = expected["path"]
        try:
            actual = file_entry(root, rel)
        except (FreezeError, OSError):
            all_ok = False
            messages.append(f"MISMATCH {rel}: file missing or unreadable")
            continue
        if actual["sha256_lf"] != expected["sha256_lf"] or actual["bytes_lf"] != expected["bytes_lf"]:
            all_ok = False
            messages.append(
                f"MISMATCH {rel}: expected sha256_lf={expected['sha256_lf']} bytes_lf={expected['bytes_lf']}, "
                f"actual sha256_lf={actual['sha256_lf']} bytes_lf={actual['bytes_lf']}"
            )
        else:
            messages.append(
                f"OK {rel}: sha256_lf={actual['sha256_lf']} bytes_lf={actual['bytes_lf']}"
            )
    return all_ok, messages


def main(argv: list[str] | None = None, *, root: Path = ROOT, now: str | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.s11.freeze",
        description="Write-once CRLF->LF SHA-256 manifest tool.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_freeze = sub.add_parser("freeze", help="write a new write-once manifest")
    p_freeze.add_argument("--manifest", required=True, help="repo-relative manifest output path")
    p_freeze.add_argument("files", nargs="+", help="one or more repo-relative file paths")

    p_check = sub.add_parser("check", help="verify files against an existing manifest")
    p_check.add_argument("--manifest", required=True, help="repo-relative manifest path")

    args = parser.parse_args(argv)
    if args.command == "freeze":
        try:
            _, doc = freeze_manifest(args.files, args.manifest, root=root, now=now)
        except FileExistsError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 3
        except (FreezeError, OSError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"written {PurePosixPath(args.manifest.replace(chr(92), '/')).as_posix()} "
              f"(schema={doc['schema']}, files={len(doc['files'])})")
        for entry in doc["files"]:
            print(f"  {entry['path']}: sha256_lf={entry['sha256_lf']} bytes_lf={entry['bytes_lf']}")
        return 0

    try:
        ok, messages = check_manifest(args.manifest, root=root)
    except (FreezeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    manifest_posix = PurePosixPath(args.manifest.replace("\\", "/")).as_posix()
    if ok:
        print(f"OK {manifest_posix} ({len(messages)} file(s) verified)")
        for msg in messages:
            print(f"  {msg}")
        return 0

    print(f"MISMATCH {manifest_posix}", file=sys.stderr)
    for msg in messages:
        stream = sys.stderr if msg.startswith("MISMATCH ") else sys.stdout
        print(f"  {msg}", file=stream)
    return 1


if __name__ == "__main__":
    sys.exit(main())
