"""Offline tests for eval.s11.golden using synthetic PyMuPDF PDFs and fake JSONs in tmp_path."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

import _netguard
from eval.s11 import freeze, golden

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    _netguard.block_network(monkeypatch, "golden tests forbid network")


def _build_synthetic_pdf(tmp_path: Path, page_texts: dict[int, str], total_pages: int = 116) -> tuple[Path, str]:
    pymupdf = pytest.importorskip("pymupdf")
    pdf_path = tmp_path / "synthetic.pdf"
    with pymupdf.open() as doc:
        for p in range(1, total_pages + 1):
            page = doc.new_page()
            text = page_texts.get(p)
            if text:
                page.insert_text((72, 72), text)
        doc.save(pdf_path)
    sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    return pdf_path, sha


def _answerable_item(item_id: str, item_type: str = "single_fact", pages: list[int] | None = None) -> dict:
    return {
        "id": item_id,
        "type": item_type,
        "answerable": True,
        "question": f"Synthetic question for {item_id} about protocol storage limits?",
        "reference": f"Synthetic reference answer for {item_id} using novel phrasing.",
        "reference_claims": [
            f"Storage temperature stays 2-8 C for {item_id} samples.",
        ],
        "gold_page_sets": [pages or [12]],
        "unanswerable_note": "",
    }


def _unanswerable_item(item_id: str, item_type: str = "unanswerable_near_miss") -> dict:
    return {
        "id": item_id,
        "type": item_type,
        "answerable": False,
        "question": f"Does the document specify fee refund timeline for {item_id}?",
        "reference": "The guideline does not state this.",
        "reference_claims": [],
        "gold_page_sets": [],
        "unanswerable_note": f"Checked annexures and chapter headings for {item_id}; omitted.",
    }


def _valid_draft(pdf_sha: str, *, include_extra: bool = False) -> dict:
    ans_types = [
        "single_fact",
        "multi_page",
        "annexure",
        "cross_reference",
        "condition",
    ]
    test_items = []
    for idx in range(1, 13):
        test_items.append(_answerable_item(f"gtp-{idx:02d}", ans_types[(idx - 1) % len(ans_types)], [12]))
    for idx in range(13, 17):
        utype = "unanswerable_near_miss" if idx <= 14 else "unanswerable_out_of_scope"
        test_items.append(_unanswerable_item(f"gtp-{idx:02d}", utype))
    dev_items = [
        _answerable_item("dev-01", "single_fact", [12]),
        _answerable_item("dev-02", "condition", [12]),
        _unanswerable_item("dev-03", "unanswerable_out_of_scope"),
    ]
    doc = {
        "pdf_sha256": pdf_sha,
        "page_convention": golden.EXPECTED_PAGE_CONVENTION,
        "test": test_items,
        "dev": dev_items,
    }
    if include_extra:
        doc["corpus"] = "SYNTHETIC_CORPUS_SENTINEL"
        doc["status"] = "DRAFT_STATUS_SENTINEL"
    return doc


def _write_json(path: Path, obj: object) -> Path:
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    return path


def test_valid_draft_with_corpus_and_status_passes_without_printing_them(tmp_path, capsys):
    pdf_path, pdf_sha = _build_synthetic_pdf(
        tmp_path,
        {12: "Storage temperature stays 2-8 C for samples across chambers."},
    )
    draft = _valid_draft(pdf_sha, include_extra=True)
    draft_path = _write_json(tmp_path / "draft.json", draft)

    rc = golden.main(
        ["check", "--golden", str(draft_path), "--pdf", str(pdf_path)],
        expected_pdf_sha256=pdf_sha,
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "test_answerable=12 test_unanswerable=4" in captured.out
    assert "dev_count=3" in captured.out
    assert "verbatim=PASS" in captured.out
    assert "SYNTHETIC_CORPUS_SENTINEL" not in captured.out + captured.err
    assert "DRAFT_STATUS_SENTINEL" not in captured.out + captured.err


def test_schema_rejects_unknown_top_fields_and_invalid_corpus_status(tmp_path):
    pdf_path, pdf_sha = _build_synthetic_pdf(tmp_path, {12: "Storage temperature stays 2-8 C."})

    # Draft with extra unknown key ("notes", or "schema") refused
    for bad_key in ("notes", "schema"):
        bad = _valid_draft(pdf_sha)
        bad[bad_key] = "bad"
        p = _write_json(tmp_path / f"bad_{bad_key}.json", bad)
        assert golden.main(["check", "--golden", str(p), "--pdf", str(pdf_path)], expected_pdf_sha256=pdf_sha) == 2

    # Non-string corpus or status refused
    for key in ("corpus", "status"):
        bad = _valid_draft(pdf_sha)
        bad[key] = 123
        p = _write_json(tmp_path / f"bad_type_{key}.json", bad)
        assert golden.main(["check", "--golden", str(p), "--pdf", str(pdf_path)], expected_pdf_sha256=pdf_sha) == 2

    # Bad page_convention refused
    bad_conv = _valid_draft(pdf_sha)
    bad_conv["page_convention"] = "1-based physical pages"
    p_conv = _write_json(tmp_path / "bad_conv.json", bad_conv)
    assert golden.main(["check", "--golden", str(p_conv), "--pdf", str(pdf_path)], expected_pdf_sha256=pdf_sha) == 2


def test_schema_rejects_item_rules_bools_counts_and_page_bounds(tmp_path):
    pdf_path, pdf_sha = _build_synthetic_pdf(tmp_path, {12: "Storage temperature stays 2-8 C."})

    def run_bad(mutator, name):
        doc = _valid_draft(pdf_sha)
        mutator(doc)
        p = _write_json(tmp_path / f"{name}.json", doc)
        assert golden.main(["check", "--golden", str(p), "--pdf", str(pdf_path)], expected_pdf_sha256=pdf_sha) == 2

    # Unknown item field
    run_bad(lambda d: d["test"][0].update({"extra_field": 1}), "unknown_item_field")
    # Missing item field
    run_bad(lambda d: d["test"][0].pop("unanswerable_note"), "missing_item_field")
    # Wrong test count (15 instead of 16)
    run_bad(lambda d: d["test"].pop(), "wrong_test_count")
    # Wrong dev count
    run_bad(lambda d: d["dev"].pop(), "wrong_dev_count")
    # Bad id (gtp-17 instead of gtp-16)
    run_bad(lambda d: d["test"][15].update({"id": "gtp-17"}), "bad_id")
    # Duplicate id
    run_bad(lambda d: d["test"][1].update({"id": "gtp-01"}), "dup_id")
    # Wrong answerable/unanswerable split (11 + 5)
    run_bad(
        lambda d: d["test"][11].update({
            "type": "unanswerable_near_miss",
            "answerable": False,
            "reference": "The guideline does not state this.",
            "reference_claims": [],
            "gold_page_sets": [],
            "unanswerable_note": "Not stated.",
        }),
        "wrong_split",
    )
    # Type vs answerable mismatch
    run_bad(lambda d: d["test"][0].update({"type": "unanswerable_near_miss"}), "type_ans_mismatch")
    run_bad(lambda d: d["test"][12].update({"type": "single_fact"}), "type_unans_mismatch")
    # Strict bool check: answerable == "true" refused
    run_bad(lambda d: d["test"][0].update({"answerable": "true"}), "answerable_str_true")
    # Strict int (not bool) check: page True refused
    run_bad(lambda d: d["test"][0].update({"gold_page_sets": [[True]]}), "page_bool_true")
    # Page 0 and page 117 refused
    run_bad(lambda d: d["test"][0].update({"gold_page_sets": [[0]]}), "page_zero")
    run_bad(lambda d: d["test"][0].update({"gold_page_sets": [[117]]}), "page_117")
    # Unsorted and duplicate gold set refused
    run_bad(lambda d: d["test"][0].update({"gold_page_sets": [[15, 12]]}), "unsorted_set")
    run_bad(lambda d: d["test"][0].update({"gold_page_sets": [[12, 12]]}), "duplicate_set_page")
    run_bad(lambda d: d["test"][0].update({"gold_page_sets": [[]]}), "empty_inner_set")
    # Answerable with note refused
    run_bad(lambda d: d["test"][0].update({"unanswerable_note": "extra"}), "ans_with_note")
    # Answerable with 0 or 5 claims refused
    run_bad(lambda d: d["test"][0].update({"reference_claims": []}), "zero_claims")
    run_bad(lambda d: d["test"][0].update({"reference_claims": ["a", "b", "c", "d", "e"]}), "five_claims")
    # Unanswerable with pages, claims, wrong refusal sentence, or empty note refused
    run_bad(lambda d: d["test"][12].update({"gold_page_sets": [[12]]}), "unans_with_pages")
    run_bad(lambda d: d["test"][12].update({"reference_claims": ["x"]}), "unans_with_claims")
    run_bad(lambda d: d["test"][12].update({"reference": "Not stated."}), "wrong_refusal_sentence")
    run_bad(lambda d: d["test"][12].update({"unanswerable_note": "  "}), "empty_unans_note")


def test_pdf_sha_mismatch_refused(tmp_path):
    pdf_path, pdf_sha = _build_synthetic_pdf(tmp_path, {12: "Storage temperature stays 2-8 C."})
    draft_path = _write_json(tmp_path / "draft.json", _valid_draft("0" * 64))
    assert golden.main(["check", "--golden", str(draft_path), "--pdf", str(pdf_path)], expected_pdf_sha256=pdf_sha) == 2

    draft_ok_file = _write_json(tmp_path / "draft_ok.json", _valid_draft(pdf_sha))
    assert (
        golden.main(["check", "--golden", str(draft_ok_file), "--pdf", str(pdf_path)], expected_pdf_sha256="f" * 64)
        == 2
    )


def test_verbatim_10_word_run_fails_and_9_word_passes_without_printing_text(tmp_path, capsys):
    pdf_sentence = (
        "Alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike"
    )
    pdf_path, pdf_sha = _build_synthetic_pdf(tmp_path, {12: pdf_sentence})

    # 9 consecutive words pass (exit 0)
    nine_words = "alpha, bravo. charlie-delta echo! foxtrot golf hotel india"
    doc_9 = _valid_draft(pdf_sha)
    doc_9["test"][0]["question"] = f"What does {nine_words} mean here?"
    p9 = _write_json(tmp_path / "d9.json", doc_9)
    assert golden.main(["check", "--golden", str(p9), "--pdf", str(pdf_path)], expected_pdf_sha256=pdf_sha) == 0
    capsys.readouterr()

    # 10 consecutive words fail (exit 1) even across punctuation/case differences, and never print text
    ten_words = "Alpha, bravo; charlie-delta echo: foxtrot golf hotel india juliet"
    doc_10 = _valid_draft(pdf_sha)
    doc_10["test"][0]["reference"] = f"The policy states {ten_words} applies."
    p10 = _write_json(tmp_path / "d10.json", doc_10)
    rc = golden.main(["check", "--golden", str(p10), "--pdf", str(pdf_path)], expected_pdf_sha256=pdf_sha)
    out = capsys.readouterr()
    assert rc == 1
    assert "VERBATIM id=gtp-01 field=reference windows=1" in out.out
    assert "foxtrot" not in out.out + out.err
    assert "juliet" not in out.out + out.err


def test_text_layer_hand_example_flags_no_terms_and_show_terms(tmp_path, capsys):
    # Page 12 has "toxic" (matches "toxicity" via 5-char prefix), "20" (does NOT match "25"),
    # and "dosing" (does NOT match 4-char "dose" because len < 5 requires exact token match).
    # Page 13 has all terms matching cleanly.
    pdf_path, pdf_sha = _build_synthetic_pdf(
        tmp_path,
        {
            12: "SECRET_PAGE_WORD toxic reactions at 20 mg during dosing protocol",
            13: "Toxicity stays low at 25 mg per dose during protocol.",
        },
    )
    doc = _valid_draft(pdf_sha)
    # Claim terms on gtp-01: "toxicity" (len>=5 -> 5-char match on "toxic"), "25" (digit -> miss),
    # "dose" (len<5 -> miss vs "dosing"), "protocol" (exact match).
    # On set 0 ([12]): 2/4 = 0.50 (NOT < 0.50); add one missing term "cohort" -> 2/5 = 0.40 (< 0.50 -> FLAG!).
    # On set 1 ([13]): 4/5 = 0.80 (>= 0.50 -> no flag).
    doc["test"][0]["reference_claims"] = ["Toxicity in 25 cohort dose protocol."]
    doc["test"][0]["gold_page_sets"] = [[12], [13]]

    # gtp-02 claim has 0 checkable terms (all stopwords or len < 4 without digits) -> NO_TERMS + FLAG
    doc["test"][1]["reference_claims"] = ["It can be so for all the old DNA."]
    doc["test"][1]["gold_page_sets"] = [[13]]

    p = _write_json(tmp_path / "tl.json", doc)
    rc = golden.main(
        ["check", "--golden", str(p), "--pdf", str(pdf_path), "--show-terms"],
        expected_pdf_sha256=pdf_sha,
    )
    out = capsys.readouterr()
    assert rc == 0  # warning-only, exit code unaffected
    assert "gtp-01 set=0 min_share=0.40 below_0.50=1 FLAG" in out.out
    assert "gtp-01 set=1 min_share=0.80 below_0.50=0" in out.out
    assert "FLAG" not in [ln for ln in out.out.splitlines() if "gtp-01 set=1" in ln][0]
    assert "gtp-02 set=0 min_share=none below_0.50=0 NO_TERMS FLAG" in out.out
    assert "missing_terms=['25', 'cohort', 'dose']" in out.out
    assert "SECRET_PAGE_WORD" not in out.out + out.err


def test_freeze_write_once_format_sorted_ids_no_extra_and_load_frozen(tmp_path):
    pdf_path, pdf_sha = _build_synthetic_pdf(
        tmp_path,
        {12: "Storage temperature stays 2-8 C for samples across chambers."},
    )
    draft = _valid_draft(pdf_sha, include_extra=True)
    # Shuffle item order in draft to verify freeze sorts test & dev by id
    draft["test"] = list(reversed(draft["test"]))
    draft["dev"] = list(reversed(draft["dev"]))
    # Include non-ASCII character to verify ensure_ascii=False
    draft["test"][0]["question"] = "What is the storage rule in °C for gtp-16?"
    draft_path = _write_json(tmp_path / "draft.json", draft)

    out_rel = "eval/s11/golden_v1.json"
    manifest_rel = "eval/s11/manifests/golden_v1.json"
    assert (
        golden.main(
            [
                "freeze",
                "--golden", str(draft_path),
                "--pdf", str(pdf_path),
                "--out", out_rel,
                "--manifest", manifest_rel,
            ],
            root=tmp_path,
            expected_pdf_sha256=pdf_sha,
            now="2026-11-26T00:00:00+00:00",
        )
        == 0
    )

    out_file = tmp_path / out_rel
    manifest_file = tmp_path / manifest_rel
    raw = out_file.read_bytes()
    assert b"\r\n" not in raw
    text = raw.decode("utf-8")
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert "°C" in text  # ensure_ascii=False

    frozen_obj = json.loads(text)
    assert list(frozen_obj.keys()) == ["schema", "pdf_sha256", "page_convention", "test", "dev"]
    assert frozen_obj["schema"] == "s11-golden-v2"
    assert "corpus" not in frozen_obj and "status" not in frozen_obj
    assert [it["id"] for it in frozen_obj["test"]] == list(golden.EXPECTED_TEST_IDS)
    assert [it["id"] for it in frozen_obj["dev"]] == list(golden.EXPECTED_DEV_IDS)
    assert list(frozen_obj["test"][0].keys()) == list(golden.ITEM_FIELDS)
    assert list(frozen_obj["dev"][0].keys()) == list(golden.ITEM_FIELDS)

    # Manifest written via freeze.py passes check_manifest
    ok, _ = freeze.check_manifest(manifest_rel, root=tmp_path)
    assert ok is True

    # Write-once refusal when --out exists or --manifest exists (exit 3)
    assert (
        golden.main(
            ["freeze", "--golden", str(draft_path), "--pdf", str(pdf_path), "--out", out_rel, "--manifest", "m2.json"],
            root=tmp_path,
            expected_pdf_sha256=pdf_sha,
        )
        == 3
    )
    assert not (tmp_path / "m2.json").exists()

    assert (
        golden.main(
            ["freeze", "--golden", str(draft_path), "--pdf", str(pdf_path), "--out", "g2.json", "--manifest", manifest_rel],
            root=tmp_path,
            expected_pdf_sha256=pdf_sha,
        )
        == 3
    )
    assert not (tmp_path / "g2.json").exists()

    # Valid frozen golden loads cleanly via load_frozen_golden
    loaded = golden.load_frozen_golden(out_rel, manifest_rel, root=tmp_path, expected_pdf_sha256=pdf_sha)
    assert loaded == frozen_obj

    # Tampered frozen golden triggers manifest mismatch refusal
    tampered = copy.deepcopy(frozen_obj)
    tampered["test"][0]["question"] = "Tampered question?"
    out_file.write_text(json.dumps(tampered, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(golden.GoldenValidationError, match="MISMATCH"):
        golden.load_frozen_golden(out_rel, manifest_rel, root=tmp_path, expected_pdf_sha256=pdf_sha)


def test_freeze_refuses_on_verbatim_or_schema_failure_without_writing(tmp_path):
    pdf_sentence = "One two three four five six seven eight nine ten eleven twelve"
    pdf_path, pdf_sha = _build_synthetic_pdf(tmp_path, {12: pdf_sentence})
    draft = _valid_draft(pdf_sha)
    draft["test"][0]["reference"] = f"See: {pdf_sentence}."
    bad_verb = _write_json(tmp_path / "verb.json", draft)

    assert (
        golden.main(
            [
                "freeze",
                "--golden", str(bad_verb),
                "--pdf", str(pdf_path),
                "--out", "eval/s11/golden_v1.json",
                "--manifest", "eval/s11/manifests/golden_v1.json",
            ],
            root=tmp_path,
            expected_pdf_sha256=pdf_sha,
        )
        == 1
    )
    assert not (tmp_path / "eval/s11/golden_v1.json").exists()
    assert not (tmp_path / "eval/s11/manifests/golden_v1.json").exists()

    # Missing file / invalid JSON root exits 2
    bad_root = _write_json(tmp_path / "list.json", [])
    assert (
        golden.main(
            [
                "freeze",
                "--golden", str(bad_root),
                "--pdf", str(pdf_path),
                "--out", "eval/s11/golden_v1.json",
                "--manifest", "eval/s11/manifests/golden_v1.json",
            ],
            root=tmp_path,
            expected_pdf_sha256=pdf_sha,
        )
        == 2
    )
    assert not (tmp_path / "eval/s11/golden_v1.json").exists()


def test_fresh_subprocess_import_golden_from_repo_root():
    # Plain `python -c "import eval.s11.golden"` from repo root
    plain = subprocess.run(
        [sys.executable, "-c", "import eval.s11.golden"],
        cwd=ROOT,
        env={**os.environ, "REGCLAW_STUB": "1", "LANGFUSE_ENABLED": "false"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert plain.returncode == 0, plain.stderr

    # With network and data/ filesystem guards active before import
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
        import eval.s11.golden
        assert hasattr(eval.s11.golden, "check_golden")
        """
    ).strip()
    guarded = subprocess.run(
        [sys.executable, "-c", guarded_code],
        cwd=ROOT,
        env={**os.environ, "REGCLAW_STUB": "1", "LANGFUSE_ENABLED": "false"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert guarded.returncode == 0, guarded.stderr
