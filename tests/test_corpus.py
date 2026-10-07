"""Offline corpus checks; PDF fixtures are generated locally with PyMuPDF."""

import hashlib
import json
from contextlib import nullcontext
from datetime import datetime
import sys
from types import SimpleNamespace

import pymupdf
import pytest

from corpus.count import collect_stats, count_pdf, markdown_table
from corpus import download
from corpus.download import Source, download_source, fetch_pdf, is_pdf, read_sources, refresh_manifest, slice_eu_mdr
from corpus.make_scanned_test import make_scanned_test
from corpus.propose_eu_ranges import find_boundaries, propose_ranges


def pdf_bytes(text="", pages=1):
    with pymupdf.open() as document:
        for index in range(pages):
            page = document.new_page()
            if text:
                page.insert_text((40, 40), text, fontsize=10)
        return document.tobytes()


@pytest.mark.parametrize("text, expected", [
    ("SHALL shall Shall should MUST may MAY", {"shall": 3, "should": 1, "must": 1, "may": 2}),
    ("shallower shouldering mustang maybe Mayday", dict.fromkeys(("shall", "should", "must", "may"), 0)),
    ("shall, should; must. (may)", dict.fromkeys(("shall", "should", "must", "may"), 1)),
    ("", dict.fromkeys(("shall", "should", "must", "may"), 0)),
])
def test_whole_word_modals_in_memory_pdf(text, expected):
    stats = count_pdf(pdf_bytes(text))
    assert {word: stats[word] for word in expected} == expected


@pytest.mark.parametrize("text, expected", [
    ("1.2 Scope\n2.3.4. Responsibilities", 2),
    ("I. Scope\nIV. Obligations\nXIV. Annex", 3),
    ("\u00a7 63.362 Emissions\n\u00a763.363 Records", 2),
    ("Text mentions 1.2 and IV. inline\n1 Scope\nIV no period\n2.3word\nAPPENDIX", 0),
])
def test_heading_patterns_in_memory_pdf(text, expected):
    assert count_pdf(pdf_bytes(text))["headings"] == expected


def test_pdf_signature_accepts_generated_pdf():
    assert is_pdf(pdf_bytes("The device should be evaluated."))


@pytest.mark.parametrize("content", [b"", b"PDF", b" %PDF-1.7", b"<html>blocked</html>"])
def test_pdf_signature_rejects_other_content(content):
    assert not is_pdf(content)


def test_text_layer_pages_with_image_only_page():
    with pymupdf.open(stream=pdf_bytes("The device shall comply."), filetype="pdf") as original:
        pixmap = original[0].get_pixmap(dpi=150, alpha=False)
        page = original.new_page()
        page.insert_image(page.rect, pixmap=pixmap)
        stats = count_pdf(original.tobytes())
    assert stats["pages"] == 2
    assert stats["text_layer_pages"] == 1
    assert stats["shall"] == 1


class Response:
    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        pass


class OfflineSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return Response(response)


def test_download_retries_and_validates_pdf(monkeypatch):
    monkeypatch.setattr("corpus.download.time.sleep", lambda seconds: None)
    content = pdf_bytes("1.1 Scope")
    session = OfflineSession([OSError("offline"), b"<html>blocked</html>", content])
    assert fetch_pdf("https://example.invalid/guidance", session) == content
    assert len(session.calls) == 3
    assert all(call[1]["timeout"] == 60 for call in session.calls)
    assert all(call[1]["headers"]["User-Agent"].startswith("Mozilla/") for call in session.calls)


def test_failed_download_records_manual_without_creating_pdf(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("corpus.download.time.sleep", lambda seconds: None)
    source = Source("in-ep", "https://example.invalid/india.pdf")
    session = OfflineSession([OSError("offline")] * 3)
    entry = download_source(source, tmp_path, session)
    assert entry == {"url": source.url, "bytes": 0, "sha256": None,
                     "downloaded_at": None, "status": "manual"}
    assert len(session.calls) == 3
    assert not (tmp_path / source.filename).exists()
    assert "MANUAL: download in-ep from https://example.invalid/india.pdf " \
           "and save as data/pdfs/in-ep.pdf" in capsys.readouterr().out


def test_marked_manual_source_is_not_requested(tmp_path, capsys):
    source = Source("epa-63o", "https://example.invalid/epa", manual=True)
    session = OfflineSession([])
    assert download_source(source, tmp_path, session)["status"] == "manual"
    assert session.calls == []
    assert "MANUAL: download epa-63o" in capsys.readouterr().out


def test_existing_pdf_skips_network_and_preserves_timestamp(tmp_path):
    source = Source("fda-iso10993-1", "https://example.invalid/fda")
    content = pdf_bytes("The device should comply.")
    path = tmp_path / source.filename
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    previous = {"sha256": digest, "downloaded_at": "2026-10-08T00:00:00+00:00"}
    session = OfflineSession([])
    entry = download_source(source, tmp_path, session, previous)
    assert entry["status"] == "ok"
    assert entry["bytes"] == len(content) and entry["sha256"] == digest
    assert entry["downloaded_at"] == previous["downloaded_at"]
    assert path.read_bytes() == content and session.calls == []


def test_bad_existing_file_is_not_overwritten(tmp_path, capsys):
    source = Source("fda-iso10993-1", "https://example.invalid/fda")
    path = tmp_path / source.filename
    path.write_bytes(b"<html>not a PDF</html>")
    assert download_source(source, tmp_path, OfflineSession([]))["status"] == "manual"
    assert path.read_bytes() == b"<html>not a PDF</html>"
    assert "MANUAL:" in capsys.readouterr().out


def test_source_table_preserves_encoded_url_and_flags(tmp_path):
    spec = tmp_path / "sources.md"
    spec.write_text(
        "| Doc code | Document | Source URL | Notes |\n"
        "|---|---|---|---|\n"
        "| `in-mdr` | India | https://example.invalid/mdr,%202017%20(1).pdf | Keep encoding |\n"
        "| `fda-knee` | Knee | Page: https://example.invalid/knee | MANUAL: unverified |\n"
        "| `who-decon` (optional) | WHO | https://example.invalid/who | OPTIONAL: skip if it fails |\n",
        encoding="utf-8",
    )
    sources = read_sources(spec)
    assert len(sources) == 3
    assert sources[0].url == "https://example.invalid/mdr,%202017%20(1).pdf"
    assert sources[1].manual and sources[2].optional


def test_pending_eu_ranges_do_not_slice(tmp_path, capsys):
    config = tmp_path / "slices.yaml"
    config.write_text("parts:\n  - name: Articles 1-10\n    pages: TODO\n", encoding="utf-8")
    assert not slice_eu_mdr(tmp_path, config)
    assert not (tmp_path / "eu-mdr.pdf").exists()
    assert "REMINDER:" in capsys.readouterr().out


def test_eu_slicing_uses_one_based_inclusive_physical_pages(tmp_path):
    full_path = tmp_path / "eu-mdr_full.pdf"
    with pymupdf.open() as document:
        for number in range(1, 5):
            document.new_page().insert_text((40, 40), f"Physical page {number}")
        document.save(full_path)
    config = tmp_path / "slices.yaml"
    config.write_text("parts:\n  - name: Articles 1-10\n    pages: [2, 3]\n", encoding="utf-8")
    assert slice_eu_mdr(tmp_path, config)
    with pymupdf.open(tmp_path / "eu-mdr.pdf") as sliced:
        assert len(sliced) == 2
        assert [page.get_text().strip() for page in sliced] == ["Physical page 2", "Physical page 3"]
    assert full_path.exists()


@pytest.mark.parametrize("pages", ["[0, 2]", "[2, 1]", "[1, 9]", "[true, 2]"])
def test_invalid_eu_ranges_do_not_write_output(tmp_path, pages):
    (tmp_path / "eu-mdr_full.pdf").write_bytes(pdf_bytes(pages=3))
    config = tmp_path / "slices.yaml"
    config.write_text(f"parts:\n  - name: Annex I\n    pages: {pages}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        slice_eu_mdr(tmp_path, config)
    assert not (tmp_path / "eu-mdr.pdf").exists()


def test_counts_skip_full_eu_file_when_slice_exists(tmp_path):
    (tmp_path / "eu-mdr_full.pdf").write_bytes(pdf_bytes(pages=3))
    (tmp_path / "eu-mdr.pdf").write_bytes(pdf_bytes("A manufacturer shall comply."))
    rows = collect_stats(tmp_path)
    assert [row["document"] for row in rows] == ["eu-mdr"]
    assert rows[0]["pages"] == 1 and rows[0]["shall"] == 1
    assert "| eu-mdr | 1 | 1 | 1 | 0 | 0 | 0 | 0 |" in markdown_table(rows)


def test_scanned_fixture_preserves_pages_and_has_no_text(tmp_path):
    source = tmp_path / "fda-iso10993-1.pdf"
    source.write_bytes(pdf_bytes("1.1 The device should comply.", pages=2))
    destination = tmp_path / "scanned_test.pdf"
    assert make_scanned_test(source, destination) == 2
    stats = count_pdf(destination)
    assert stats == {"pages": 2, "text_layer_pages": 0,
                     "shall": 0, "should": 0, "must": 0, "may": 0, "headings": 0}


def test_refresh_accepts_manual_pdf_and_preserves_existing_entries(tmp_path):
    content = pdf_bytes("The device should comply.")
    (tmp_path / "fda-iso10993-1.pdf").write_bytes(content)
    manifest_path = tmp_path / "manifest.json"
    existing = {"url": "https://example.invalid/india", "status": "ok", "sha256": "unchanged"}
    manifest_path.write_text(json.dumps({
        "fda-iso10993-1": {"url": "https://example.invalid/fda", "status": "manual"},
        "in-mdr": existing,
    }), encoding="utf-8")
    assert refresh_manifest(manifest_path, tmp_path) == (["fda-iso10993-1"], [])
    refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = refreshed["fda-iso10993-1"]
    assert entry["status"] == "ok_manual" and entry["bytes"] == len(content)
    assert entry["sha256"] == hashlib.sha256(content).hexdigest()
    assert datetime.fromisoformat(entry["downloaded_at"]).tzinfo is not None
    assert entry["url"] == "https://example.invalid/fda" and refreshed["in-mdr"] == existing
    before_second_refresh = manifest_path.read_bytes()
    assert refresh_manifest(manifest_path, tmp_path) == ([], [])
    assert manifest_path.read_bytes() == before_second_refresh


def test_refresh_leaves_missing_and_invalid_pdfs_manual(tmp_path, capsys):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "fda-knee": {"url": "https://example.invalid/knee", "status": "manual"},
        "epa-63o": {"url": "https://example.invalid/epa", "status": "manual"},
    }), encoding="utf-8")
    (tmp_path / "epa-63o.pdf").write_bytes(b"<html>not a PDF</html>")
    original = manifest_path.read_bytes()
    assert refresh_manifest(manifest_path, tmp_path) == ([], ["fda-knee", "epa-63o"])
    assert manifest_path.read_bytes() == original
    output = capsys.readouterr().out
    assert "MISSING: data/pdfs/fda-knee.pdf" in output and "File does not start with %PDF" in output


@pytest.mark.parametrize("previous_status", ["manual", "ok_manual"])
def test_refresh_marks_knee_as_html_print_with_source_url(tmp_path, previous_status):
    content = pdf_bytes("Knee Joint Prostheses Class II Special Controls Guidance")
    (tmp_path / "fda-knee.pdf").write_bytes(content)
    url = "https://www.fda.gov/regulatory-information/search-fda-guidance-documents/knee"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"fda-knee": {
        "url": url, "status": previous_status, "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "downloaded_at": "2026-10-08T00:00:00+00:00",
    }}), encoding="utf-8")
    assert refresh_manifest(manifest_path, tmp_path) == (["fda-knee"], [])
    entry = json.loads(manifest_path.read_text(encoding="utf-8"))["fda-knee"]
    assert entry["status"] == "ok_manual_html_print"
    assert entry["url"] == url
    assert entry["source_url"] == download.FDA_KNEE_HTML_URL
    assert entry["bytes"] == len(content) and entry["sha256"] == hashlib.sha256(content).hexdigest()
    assert datetime.fromisoformat(entry["downloaded_at"]).tzinfo is not None
    first_refresh = manifest_path.read_bytes()
    assert refresh_manifest(manifest_path, tmp_path) == ([], [])
    assert manifest_path.read_bytes() == first_refresh


def test_refresh_updates_metadata_when_accepted_manual_pdf_changes(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"epa-63o": {
        "url": "https://example.invalid/epa", "status": "ok_manual", "bytes": 1,
        "sha256": "old", "downloaded_at": "2026-10-07T00:00:00+00:00",
    }}), encoding="utf-8")
    content = pdf_bytes("Subpart O: The facility must comply.")
    (tmp_path / "epa-63o.pdf").write_bytes(content)
    assert refresh_manifest(manifest_path, tmp_path) == (["epa-63o"], [])
    entry = json.loads(manifest_path.read_text(encoding="utf-8"))["epa-63o"]
    assert entry["status"] == "ok_manual" and entry["bytes"] == len(content)
    assert entry["sha256"] == hashlib.sha256(content).hexdigest()
    assert datetime.fromisoformat(entry["downloaded_at"]).tzinfo is not None


@pytest.mark.parametrize("code, status", [
    ("fda-iso10993-1", "ok_manual"), ("fda-knee", "ok_manual_html_print"),
])
def test_download_preserves_manual_provenance(tmp_path, code, status):
    source = Source(code, "https://example.invalid/guidance")
    content = pdf_bytes("The device should comply.")
    (tmp_path / source.filename).write_bytes(content)
    previous = {"status": status, "sha256": hashlib.sha256(content).hexdigest(),
                "downloaded_at": "2026-10-08T00:00:00+00:00"}
    if code == "fda-knee":
        previous["source_url"] = source.url
    session = OfflineSession([])
    entry = download_source(source, tmp_path, session, previous)
    assert entry["status"] == status and entry["downloaded_at"] == previous["downloaded_at"]
    assert entry.get("source_url") == previous.get("source_url")
    assert session.calls == []


def test_download_summary_counts_manual_successes(tmp_path, monkeypatch, capsys):
    content = pdf_bytes("The device should comply.")
    sources = [Source("fda-iso10993-1", "https://example.invalid/fda"),
               Source("fda-knee", "https://example.invalid/knee", manual=True)]
    manifest_path = tmp_path / "manifest.json"
    previous = {}
    for source, status in zip(sources, ("ok_manual", "ok_manual_html_print")):
        (tmp_path / source.filename).write_bytes(content)
        previous[source.code] = {"url": source.url, "status": status,
                                 "sha256": hashlib.sha256(content).hexdigest(),
                                 "downloaded_at": "2026-10-08T00:00:00+00:00"}
    previous["fda-knee"]["source_url"] = sources[1].url
    manifest_path.write_text(json.dumps(previous), encoding="utf-8")
    monkeypatch.setattr(download, "PDF_DIR", tmp_path)
    monkeypatch.setattr(download, "MANIFEST", manifest_path)
    monkeypatch.setattr(download, "read_sources", lambda: sources)
    monkeypatch.setattr(download, "slice_eu_mdr", lambda: False)
    monkeypatch.setattr(sys, "argv", ["download.py"])
    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(
        Session=lambda: nullcontext(OfflineSession([])),
    ))
    assert download.main() == 0
    assert "Manifest: 2 entries, 2 ok, 0 manual" in capsys.readouterr().out
    current = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [current[source.code]["status"] for source in sources] == [
        "ok_manual", "ok_manual_html_print",
    ]
    assert current["fda-knee"]["source_url"] == sources[1].url


def boundary_pdf(tmp_path, omitted=None, duplicate=False, prefix_pages=None):
    headings = [
        (2, "Article 1"), (4, "Article 11"), (6, "ANNEX I"), (8, "ANNEX II"),
        (10, "ANNEX III"), (12, "ANNEX IV"), (15, "ANNEX XIV"),
        (17, "ANNEX XV"), (19, "ANNEX XVI"),
    ]
    path = tmp_path / "eu-mdr_full.pdf"
    with pymupdf.open() as document:
        for number in range(1, 21):
            page = document.new_page()
            text = ("Cross-reference: Article 1, ANNEX I and ANNEX XIV.\n"
                    if prefix_pages is None or number in prefix_pages else "")
            if duplicate and number == 1:
                text += "ANNEX I\nDuplicate heading\n"
            for page_number, heading in headings:
                if number == page_number and heading != omitted:
                    text += heading + "\nTitle of " + heading
            page.insert_text((40, 40), text, fontsize=10)
        document.save(path)
    return path


def test_boundary_detection_ignores_cross_references_and_proposes_ranges(tmp_path):
    boundaries = find_boundaries(boundary_pdf(tmp_path))
    assert boundaries["Article 1"]["page"] == 2
    assert boundaries["Article 1"]["line"] == "Article 1"
    assert boundaries["Article 1"]["title"] == "Title of Article 1"
    assert not boundaries["Article 1"]["is_first_line"]
    assert boundaries["ANNEX XIV"]["page"] == 15
    parts = propose_ranges(boundaries)
    assert [part["pages"] for part in parts] == [[2, 4], [6, 12], [15, 19]]
    assert [(part["start_marker"], part["stop_marker"]) for part in parts] == [
        ("Article 1", "Article 11"), ("ANNEX I", "ANNEX IV"), ("ANNEX XIV", "ANNEX XVI"),
    ]


def test_top_of_page_stop_headings_exclude_the_boundary_page(tmp_path):
    boundaries = find_boundaries(boundary_pdf(tmp_path, prefix_pages=set()))
    assert boundaries["Article 1"]["is_first_line"]
    assert [part["pages"] for part in propose_ranges(boundaries)] == [[2, 3], [6, 11], [15, 18]]


def test_only_continued_stop_boundary_is_included(tmp_path):
    boundaries = find_boundaries(boundary_pdf(tmp_path, prefix_pages={2, 4}))
    assert boundaries["Article 11"]["first_line"].startswith("Cross-reference:")
    assert not boundaries["Article 1"]["is_first_line"]
    assert [part["pages"] for part in propose_ranges(boundaries)] == [[2, 4], [6, 11], [15, 18]]


def test_boundary_detection_rejects_missing_heading(tmp_path):
    with pytest.raises(ValueError, match="ANNEX XVI; found 0"):
        find_boundaries(boundary_pdf(tmp_path, omitted="ANNEX XVI"))


def test_boundary_detection_rejects_duplicate_heading(tmp_path):
    with pytest.raises(ValueError, match="ANNEX I; found 2"):
        find_boundaries(boundary_pdf(tmp_path, duplicate=True))
