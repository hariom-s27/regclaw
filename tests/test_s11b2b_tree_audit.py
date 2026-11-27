"""Offline tests for eval.s11.tree_audit and S11b2b-24 key_items rendering."""
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
from types import SimpleNamespace as NS

import pytest

import _netguard
from _s11_leakcheck import assert_no_source_words
from eval.s11 import tree_audit
from eval.s11.ledger import WriteOnceError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("REGCLAW_STUB", "1")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    _netguard.block_network(monkeypatch, "s11b2b tree_audit tests forbid network")


def _sample_contents_and_tree():
    """Build a synthetic 116-page Contents + matching tree covering 16..115 with 7 annexures."""
    # Pages 16..115 = 100 pages total; split across 20 sections (each <= 10 pages, including 7 annexures)
    specs = []
    cur = 16
    for idx in range(1, 14):
        span = 5
        specs.append((f"{idx}. Section {idx}", cur, cur + span - 1, False))
        cur += span
    # cur == 81; remaining 81..115 = 35 pages across 7 annexures (5 pages each)
    romans = ["I", "II", "III", "IV", "V", "VI", "VII"]
    for idx, rom in enumerate(romans):
        specs.append((f"Annexure {rom}: Schedule {idx + 1}", cur, cur + 4, False))
        cur += 5
    assert cur - 1 == 115

    entries = []
    nodes = []
    for i, (title, s_pg, e_pg, is_intro) in enumerate(specs):
        printed = s_pg - 11
        entries.append(
            {
                "index": i,
                "toc_page": 10 if i < 10 else 11,
                "title": title,
                "printed_page": printed,
                "expected_start": s_pg,
                "is_annexure": tree_audit.is_annexure_title(title),
                "annexure_numeral": tree_audit.extract_annexure_numeral(title),
                "parse_fallback": False,
            }
        )
        nodes.append(
            {
                "node_id": f"{i + 1:04d}",
                "title": title,
                "start_index": s_pg,
                "end_index": e_pg,
            }
        )
    tree = {"doc_name": "synthetic_guideline.pdf", "structure": nodes}
    return entries, tree


def test_normalizer_annexure_guard_and_intro_exclusion():
    assert tree_audit.normalize_title("  ３．２．１  Ethical   Issues ... 24 ") == "ethical issues"
    assert tree_audit.normalize_title("3 Scope (intro)") == "scope"
    assert tree_audit.normalize_title("3. Scope — Overview ··· 12-15") == "scope overview"
    assert tree_audit.normalize_title("Annexure I: Informed Consent ... 85") == "informed consent"
    assert tree_audit.normalize_title("Annexure I") == ""
    assert tree_audit.normalize_title("2019 National Guideline") == "2019 national guideline"

    assert tree_audit.is_annexure_title("Annexure I: Forms") is True
    assert tree_audit.is_annexure_title("ANNEXURE 7 - Checklist") is True
    assert tree_audit.is_annexure_title("Appendix I: Forms") is False

    # Unequal annexure numerals never match even when suffix is identical/long
    assert (
        tree_audit.fuzzy_title_score(
            "Annexure I: Standard Operating Procedures for Institutional Ethics Committee",
            "Annexure II: Standard Operating Procedures for Institutional Ethics Committee",
        )
        == 0.0
    )
    assert tree_audit.fuzzy_title_score("Annexure I", "Annexure II") == 0.0
    assert tree_audit.fuzzy_title_score("Annexure IV: Forms", "Annexure IV - Forms") == 100.0

    # G1 label-only title rules & S11b2b-27 label removal
    assert tree_audit.fuzzy_title_score("Composition", "Annexure I", numeral_a="I") == 100.0
    assert tree_audit.fuzzy_title_score("Composition", "Annexure II", numeral_a="I") == 0.0
    assert tree_audit.fuzzy_title_score("Composition", "Annexure I", numeral_a=None) == 0.0
    assert tree_audit.fuzzy_title_score("Composition", "Annexure I: Composition", numeral_a="I") == 100.0
    assert tree_audit.fuzzy_title_score("Composition", "Composition", numeral_a="I") == 100.0
    assert tree_audit.fuzzy_title_score("Composition", "Annexure II: Composition", numeral_a="I") == 0.0

    # Intro nodes excluded from Contents matching, still counted as leaves
    entries, tree = _sample_contents_and_tree()
    intro_node = {
        "node_id": "0000",
        "title": "1. Section 1 (intro)",
        "start_index": 16,
        "end_index": 20,
    }
    tree["structure"][0] = intro_node
    res = tree_audit.audit_tree(tree, entries)
    va = res["variants"]["A"]
    assert va["intro_node_count"] == 1
    assert va["criteria"]["presence_95pct"]["matched"] == 19
    assert va["criteria"]["leaf_coverage_no_gaps"]["pass"] is True
    assert va["criteria"]["leaves_le_10_pages"]["pass"] is True


def test_one_to_one_matching_tie_breaks_and_variants_a_b_c():
    entries, tree = _sample_contents_and_tree()
    assert tree_audit.audit_tree(tree, entries)["overall_pass"] is True

    # Merge Section 2 into Section 1 key_items (parent spans 16..25, Section 2 expected at 21)
    sec2_title = tree["structure"][1]["title"]
    tree["structure"][0]["end_index"] = 25
    tree["structure"][0]["key_items"] = [sec2_title]
    tree["structure"].pop(1)

    _, unmerged_tree = _sample_contents_and_tree()
    # Pre-merge snapshot without node_id -> gets pre-0000.. report ids (F2)
    pre_snap = {"structure": [{k: v for k, v in n.items() if k != "node_id"} for n in unmerged_tree["structure"]]}

    res = tree_audit.audit_tree(tree, entries, pre_merge_tree=pre_snap)
    assert res["variants"]["A"]["criteria"]["presence_95pct"]["matched"] == 19
    assert res["variants"]["B"]["criteria"]["presence_95pct"]["matched"] == 20
    assert res["variants"]["B"]["pass"] is True
    assert res["variants"]["C"]["pass"] is True
    assert res["variants"]["C"]["matches"][0]["report_id"] == "pre-0000"

    # Variant B Condition 2 check: node start_index - 1 <= expected_start <= end_index + 1,
    # and Condition 3 NEVER counts key_items (needs 7 distinct nodes matched by title)
    entries2, tree2 = _sample_contents_and_tree()
    annex7_title = tree2["structure"][-1]["title"]
    tree2["structure"][-2]["end_index"] = 115
    tree2["structure"][-2]["key_items"] = [annex7_title]
    tree2["structure"].pop(-1)
    res2 = tree_audit.audit_tree(tree2, entries2)
    assert res2["variants"]["B"]["criteria"]["presence_95pct"]["pass"] is True
    assert res2["variants"]["B"]["criteria"]["start_within_1_page_95pct"]["pass"] is True
    assert res2["variants"]["B"]["criteria"]["all_annexures_distinct_nodes"]["matched_distinct_nodes"] == 6
    assert res2["variants"]["B"]["criteria"]["all_annexures_distinct_nodes"]["pass"] is False
    assert res2["variants"]["B"]["pass"] is False

    # Tie-breaking: highest score -> start nearest expected_start -> lower node_id
    single_entry = [
        {
            "index": 0,
            "toc_page": 10,
            "title": "Common Topic",
            "printed_page": 9,
            "expected_start": 20,
            "is_annexure": False,
            "annexure_numeral": None,
            "parse_fallback": False,
        }
    ]
    nodes_dist = [
        {"node_id": "0001", "title": "Common Topic", "start_index": 30, "end_index": 32},
        {"node_id": "0010", "title": "Common Topic", "start_index": 21, "end_index": 23},
    ]
    audit_dist = tree_audit.audit_tree_variant(
        nodes_dist,
        single_entry,
        variant="A",
        allow_key_items=False,
        contained_start_check=False,
        coverage_start=20,
        coverage_end=21,
        expected_annexures=0,
    )
    assert audit_dist["matches"][0]["report_id"] == "0010"

    # Equal distance -> lower report id wins
    nodes_id_tie = [
        {"node_id": "0010", "title": "Common Topic", "start_index": 21, "end_index": 22},
        {"node_id": "0002", "title": "Common Topic", "start_index": 19, "end_index": 20},
    ]
    audit_id_tie = tree_audit.audit_tree_variant(
        nodes_id_tie,
        single_entry,
        variant="A",
        allow_key_items=False,
        contained_start_check=False,
        coverage_start=20,
        coverage_end=20,
        expected_annexures=0,
    )
    assert audit_id_tie["matches"][0]["report_id"] == "0002"


def test_audit_failure_modes_coverage_order_range_and_leaf_span():
    entries, base_tree = _sample_contents_and_tree()

    # Coverage gap on page 55
    gap_tree = json.loads(json.dumps(base_tree))
    gap_tree["structure"][7]["end_index"] = 54  # originally 51..55
    res_gap = tree_audit.audit_tree(gap_tree, entries)
    assert res_gap["overall_pass"] is False
    assert res_gap["variants"]["A"]["criteria"]["leaf_coverage_no_gaps"]["uncovered_pages"] == [55]

    # Oversized leaf (> 10 pages)
    big_tree = json.loads(json.dumps(base_tree))
    big_tree["structure"][0]["end_index"] = 26  # 16..26 = 11 pages
    res_big = tree_audit.audit_tree(big_tree, entries)
    assert res_big["variants"]["A"]["criteria"]["leaves_le_10_pages"]["oversized_leaf_ids"] == ["0001"]
    assert res_big["overall_pass"] is False

    # Decreasing start_index
    dec_tree = json.loads(json.dumps(base_tree))
    dec_tree["structure"][3]["start_index"] = 20
    res_dec = tree_audit.audit_tree(dec_tree, entries)
    assert res_dec["variants"]["A"]["criteria"]["starts_non_decreasing"]["pass"] is False
    assert res_dec["overall_pass"] is False

    # end < start
    bad_range = json.loads(json.dumps(base_tree))
    bad_range["structure"][2]["start_index"] = 29
    bad_range["structure"][2]["end_index"] = 27
    res_range = tree_audit.audit_tree(bad_range, entries)
    assert res_range["variants"]["A"]["criteria"]["end_ge_start_all_nodes"]["bad_node_ids"] == ["0003"]
    assert res_range["overall_pass"] is False


def test_contents_parser_offset_window_disagreements_and_forecast_d2_d3():
    assert tree_audit.FORECAST_MAX_PAGES_PER_NODE == 10
    assert tree_audit.FORECAST_MAX_TOKENS_PER_NODE == 20000

    # PyMuPDF layout: number on next line, empty line before number, 2-line wrap, section no_page,
    # Annexure heading skipped, "<title>\nAnnexure <roman>\n<number>", and trailing no_page buffer.
    mu_p10 = "\n".join(
        [
            "Contents",
            "1 Scope",
            "",
            "5",
            "2.1 Oversight and",
            "Review Process",
            "9",
            "3 Section without page replaced next",
            "4 Earlier Jump",
            "20",
            "5 Big Section",
            "18",
        ]
    )
    mu_p11 = "\n".join(
        [
            "Annexures",
            "Ethics Forms",
            "Annexure I",
            "50",
            "Audit Checklist",
            "Annexure V I",
            "60",
            "Closing Note",
            "Annexure V II",
            "104",
            "Unclosed trailing line without page",
        ]
    )

    # PyPDF2 layout: one-line "<title> [Annexure <roman>] <number>", wrapped title, split "V I" / "V II"
    p2_p10 = "\n".join(
        [
            "CONTENTS",
            "1 Scope 5",
            "2.1 Oversight and",
            "Review Process 9",
            "3 Section without page replaced next",
            "4 Earlier Jump 20",
            "5 Big Section 18",
        ]
    )
    p2_p11 = "\n".join(
        [
            "Annexure",
            "Ethics Forms Annexure I 50",
            "Audit Checklist",
            "Annexure V I 60",
            "Closing Note Annexure V II 104",
            "Unclosed trailing line without page",
        ]
    )

    mupdf_pages = {p: "" for p in range(1, 117)}
    pypdf2_pages = {p: "" for p in range(1, 117)}
    mupdf_pages[10], mupdf_pages[11] = mu_p10, mu_p11
    pypdf2_pages[10], pypdf2_pages[11] = p2_p10, p2_p11

    # Section 1 expected 5+11=16: PyMuPDF single-line match at 16; PyPDF2 at 18 -> disagreement
    mupdf_pages[16] = "1 Scope\nBody starts"
    pypdf2_pages[18] = "1 Scope\nBody starts"
    # Section 2.1 expected 9+11=20: PyMuPDF misses; PyPDF2 matches via 3-line consecutive join on page 22
    pypdf2_pages[22] = "Header line\nOversight\nand Review\nProcess\nFooter"
    # Section 4 expected 20+11=31: PyMuPDF matches via 2-line join on page 31; put false line on TOC page 11 too
    mupdf_pages[31] = "4 Earlier\nJump\nText"
    mupdf_pages[11] += "\nEarlier Jump"
    # Annexure I (50+11=61): body page has label-only "Annexure I" on PyMuPDF page 61
    mupdf_pages[61] = "Annexure I\nDifferent subtitle line"
    # Annexure VI (60+11=71): body page matches split numeral 2-line join "Annexure\nV I" on PyPDF2 page 72
    pypdf2_pages[72] = "Annexure\nV I\nCommittee Checklist"
    # Tokens check on Big Section (29..60 = 32 pages)
    mupdf_pages[30] = "a" * 4000
    pypdf2_pages[30] = "a" * 8000

    analysis = tree_audit.analyze_pdf_contents(
        pymupdf_pages_by_num=mupdf_pages,
        pypdf2_pages_by_num=pypdf2_pages,
    )
    assert analysis["entry_count"] == 7
    assert analysis["annexures"] == 3
    assert analysis["annexure_numerals"] == ["I", "VI", "VII"]
    assert analysis["no_page"] == 2
    assert analysis["headings_skipped"] == 1
    assert analysis["contents_parse_by_extractor"]["pymupdf"] == {
        "entries": 7,
        "annexures": 3,
        "annexure_numerals": ["I", "VI", "VII"],
        "no_page": 2,
        "headings_skipped": 1,
    }
    assert analysis["contents_parse_by_extractor"]["pypdf2"] == {
        "entries": 7,
        "annexures": 3,
        "annexure_numerals": ["I", "VI", "VII"],
        "no_page": 2,
        "headings_skipped": 1,
    }
    assert [e["title"] for e in analysis["entries"]] == [
        "1 Scope",
        "2.1 Oversight and Review Process",
        "4 Earlier Jump",
        "5 Big Section",
        "Ethics Forms",
        "Audit Checklist",
        "Closing Note",
    ]
    assert analysis["extractor_disagreements"] == 1
    assert analysis["extractor_counts"] == {"pymupdf": 3, "pypdf2": 2, "offset_fallback": 2}
    assert analysis["offset_fallbacks"] == 2

    e_map = {e["index"]: e for e in analysis["entries"]}
    assert e_map[0]["matched_extractor"] == "pymupdf" and e_map[0]["resolved_start"] == 16
    assert e_map[1]["matched_extractor"] == "pypdf2" and e_map[1]["resolved_start"] == 22
    assert e_map[2]["matched_extractor"] == "pymupdf" and e_map[2]["resolved_start"] == 31
    assert e_map[3]["matched_extractor"] == "offset_fallback" and e_map[3]["offset_flag"] == "offset_fallback"
    assert e_map[4]["matched_extractor"] == "pymupdf" and e_map[4]["resolved_start"] == 61
    assert e_map[5]["matched_extractor"] == "pypdf2" and e_map[5]["resolved_start"] == 72

    fc = analysis["forecast"]
    # Contents order preserved: expected_starts = [16, 20, 31, 29, 61, 71, 115] -> 1 order break (29 < 31)
    assert fc["order_breaks"] == 1
    assert fc["entries"] == 7
    assert fc["spans_gt_10"] == 3  # 2.1 (20..30=11), 5 (29..60=32), Audit Checklist (71..114=44)
    assert fc["spans_gt_10_tokens_lt_20000_pymupdf"] == 3
    assert fc["spans_gt_10_tokens_lt_20000_pypdf2"] == 3
    assert fc["largest_span"] == 44


def test_schema_and_cli_rules_f3_f4_d4(tmp_path, capsys):
    entries, tree = _sample_contents_and_tree()

    # F3 / Q-D3: accept doc_description + pi_ingest telemetry keys, report ignored_keys
    tree_with_extras = {
        **tree,
        "doc_description": "Sensitive guideline description never printed",
        "open_reservations": [],
        "wrapper_calls": 3,
        "custom_logger_calls": 3,
    }
    audited = tree_audit.audit_tree(tree_with_extras, entries)
    assert audited["ignored_keys"] == [
        "doc_description",
        "open_reservations",
        "wrapper_calls",
        "custom_logger_calls",
    ]
    assert audited["overall_pass"] is True

    # Unknown top-level key rejected
    with pytest.raises(ValueError, match="Unsupported top-level"):
        tree_audit.audit_tree({**tree, "unexpected_key": 123}, entries)

    # F4: --tree and --out required on audit
    with pytest.raises(SystemExit):
        tree_audit.main(["audit", "--out", str(tmp_path / "out.json")])
    with pytest.raises(SystemExit):
        tree_audit.main(["audit", "--tree", str(tmp_path / "tree.json")])

    # D4: --show-entries removed
    with pytest.raises(SystemExit):
        tree_audit.main(["contents", "--pdf", str(tmp_path / "x.pdf"), "--show-entries"])

    tree_path = tmp_path / "tree.json"
    contents_path = tmp_path / "contents.json"
    out_audit = tmp_path / "audit_out.json"
    tree_path.write_text(json.dumps(tree_with_extras), encoding="utf-8")
    contents_path.write_text(json.dumps({"entries": entries}), encoding="utf-8")

    rc = tree_audit.main(
        [
            "audit",
            "--tree",
            str(tree_path),
            "--contents-json",
            str(contents_path),
            "--out",
            str(out_audit),
        ]
    )
    assert rc == 0
    stdout_audit = capsys.readouterr().out
    assert "Sensitive guideline description" not in stdout_audit
    assert "Annexure I: Schedule 1" not in stdout_audit
    assert "1. Section 1" not in stdout_audit
    assert json.loads(stdout_audit)["overall_pass"] is True

    # Write-once refuses overwrite on existing --out
    with pytest.raises(WriteOnceError):
        tree_audit.main(
            [
                "audit",
                "--tree",
                str(tree_path),
                "--contents-json",
                str(contents_path),
                "--out",
                str(out_audit),
            ]
        )


def test_contents_real_synthetic_pdf_pymupdf_and_pypdf2_parity(tmp_path, capsys):
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("PyPDF2")

    pdf_path = tmp_path / "synthetic_gtp.pdf"
    with pymupdf.open() as doc:
        for p in range(1, 25):
            page = doc.new_page()
            if p == 10:
                page.insert_text((72, 72), "CONTENTS\n1. Scope ............. 2\n2. Consent ........... 6")
            elif p == 13:
                page.insert_text((72, 72), "1. Scope\nDetails on scope")
            elif p == 17:
                page.insert_text((72, 72), "2. Consent\nDetails on consent")
            else:
                page.insert_text((72, 72), f"Body page {p}")
        doc.save(pdf_path)

    out_c = tmp_path / "contents_out.json"
    rc = tree_audit.main(["contents", "--pdf", str(pdf_path), "--toc-pages", "10", "--out", str(out_c)])
    assert rc == 0
    stdout = capsys.readouterr().out
    assert "Consent" not in stdout and "Scope" not in stdout
    summary = json.loads(stdout)
    assert summary["entries"] == 2
    assert summary["offset_fallbacks"] == 0
    assert summary["extractor_disagreements"] == 0
    assert summary["forecast"]["largest_span"] == 8  # 17..24

    with pytest.raises(WriteOnceError):
        tree_audit.main(["contents", "--pdf", str(pdf_path), "--toc-pages", "10", "--out", str(out_c)])


def test_key_items_outline_and_get_node_s11b2b_24(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "pageindex"))
    pi_common = importlib.import_module("pi_common")
    pi_agent = importlib.import_module("pi_agent")

    # Byte-identical outline and get_node output when key_items absent or empty
    no_ki_struct = [
        {
            "node_id": "0001",
            "title": "Scope",
            "start_index": 1,
            "end_index": 5,
            "summary": "Covers scope",
            "nodes": [
                {"node_id": "0002", "title": "Child", "start_index": 2, "end_index": 3, "summary": "Sub"}
            ],
        }
    ]
    empty_ki_struct = [
        {
            "node_id": "0001",
            "title": "Scope",
            "start_index": 1,
            "end_index": 5,
            "summary": "Covers scope",
            "key_items": ["   ", ""],
            "nodes": [
                {
                    "node_id": "0002",
                    "title": "Child",
                    "start_index": 2,
                    "end_index": 3,
                    "summary": "Sub",
                    "key_items": [],
                }
            ],
        }
    ]
    assert pi_common.outline(no_ki_struct) == "[0001] Scope (pp. 1-5) [+1 sub-sections] — Covers scope\n  [0002] Child (pp. 2-3) — Sub"
    assert pi_common.outline(empty_ki_struct) == pi_common.outline(no_ki_struct)

    # _line placement right after page span and before [+N sub-sections], with 160-char cap
    ki_line = pi_common._line(
        {
            "node_id": "0003",
            "title": "Chapter 3",
            "start_index": 10,
            "end_index": 20,
            "key_items": ["Item A", "Item B"],
            "summary": "Summary here",
            "nodes": [{"node_id": "0004", "title": "Sub", "start_index": 11, "end_index": 12}],
        },
        depth=1,
        summary_chars=160,
    )
    assert ki_line == "[0003] Chapter 3 (pp. 10-20) · also: Item A; Item B [+1 sub-sections] — Summary here"

    long_ki = ["X" * 100, "Y" * 100]
    capped_line = pi_common._line(
        {"node_id": "0005", "title": "Long", "start_index": 5, "end_index": 5, "key_items": long_ki},
        depth=1,
        summary_chars=160,
    )
    expected_ki = (("X" * 100) + "; " + ("Y" * 100))[:160] + "…"
    assert capped_line == f"[0005] Long (p. 5) · also: {expected_ki}"

    # Budget degrade drops key_items first while preserving summaries
    degrade_tree = [
        {
            "node_id": "0001",
            "title": "Section",
            "start_index": 1,
            "end_index": 4,
            "key_items": ["Alpha item", "Beta item"],
            "summary": "Important summary",
        }
    ]
    full_out = pi_common.outline(degrade_tree, char_budget=5000)
    no_ki_expected = "[0001] Section (pp. 1-4) — Important summary"
    assert "· also: Alpha item; Beta item" in full_out
    degraded = pi_common.outline(degrade_tree, char_budget=len(no_ki_expected))
    assert degraded == no_ki_expected

    # get_node with key_items absent vs present on node and child (Q-D1 160-char truncation)
    node_plain = {"node_id": "0001", "title": "Root", "start_index": 1, "end_index": 10, "nodes": no_ki_struct[0]["nodes"]}
    sess_plain = pi_agent.Session(
        rec=NS(title="Doc", version="1", page_count=10, doc_description="", doc_id="d@1", pdf_path="x.pdf"),
        tree={"structure": [node_plain]},
        nodes=pi_common.node_map([node_plain]),
        s=pi_common.Settings(),
    )
    raw_plain, err_plain = pi_agent._run_tool(sess_plain, "get_node", {"node_id": "0001"})
    assert err_plain is False
    obj_plain = json.loads(raw_plain)
    assert "key_items" not in obj_plain and "key_items" not in obj_plain["children"][0]

    # Overflowing Q-D1 test: 1st item 50 chars + "; " + 2nd item 130 chars (overflow -> 160-52 = 108 chars + "…"), 3rd dropped
    it1 = "A" * 50
    it2 = "B" * 130
    it3 = "C" * 20
    node_ki = {
        "node_id": "0001",
        "title": "Root",
        "start_index": 1,
        "end_index": 10,
        "key_items": [it1, it2, it3],
        "nodes": [
            {
                "node_id": "0002",
                "title": "Child",
                "start_index": 2,
                "end_index": 5,
                "key_items": ["Sub 1", "Sub 2"],
            }
        ],
    }
    sess_ki = pi_agent.Session(
        rec=NS(title="Doc", version="1", page_count=10, doc_description="", doc_id="d@1", pdf_path="x.pdf"),
        tree={"structure": [node_ki]},
        nodes=pi_common.node_map([node_ki]),
        s=pi_common.Settings(),
    )
    raw_ki, err_ki = pi_agent._run_tool(sess_ki, "get_node", {"node_id": "0001"})
    assert err_ki is False
    obj_ki = json.loads(raw_ki)
    assert obj_ki["key_items"] == [it1, ("B" * 108) + "…"]
    assert obj_ki["children"][0]["key_items"] == ["Sub 1", "Sub 2"]


def test_fresh_subprocess_import_tree_audit_under_netguard():
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
        import eval.s11.tree_audit
        assert hasattr(eval.s11.tree_audit, "audit_tree")
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


def test_contents_shapes_mode_anonymizes_letters_and_reports_decisions(monkeypatch, capsys):
    import unicodedata

    long_body = "ZQJWX " * 20  # > 80 chars
    p10 = "\n".join(
        [
            "CONTENTS",
            "   ",
            "1. ZQJ  \t  WXÉ",
            "42",
            "2. ZQJ wrap β",
            "WXJ part",
            "",
            "58",
            "99",
        ]
    )
    p11 = "\n".join(
        [
            "Annexures",
            "ZQJX Forms",
            "Annexure IV",
            "85",
            "annexure V I - QZWX",
            long_body,
        ]
    )
    mupdf_map = {p: "" for p in range(1, 117)}
    mupdf_map[10] = p10
    mupdf_map[11] = p11
    pdf2_map = {p: "" for p in range(1, 117)}
    pdf2_map[10] = "ZQX 12"
    pdf2_map[11] = "ANNEXURE\nZQJX Forms Annexure I 85"
    sources = [p10, p11, pdf2_map[10], pdf2_map[11]]

    diag = tree_audit.analyze_contents_shapes(
        pymupdf_pages_by_num=mupdf_map,
        pypdf2_pages_by_num=pdf2_map,
    )
    for rec in diag["lines"]:
        shp = rec["shape"]
        assert len(shp) <= 80
        assert "  " not in shp
        assert_no_source_words(shp, sources)
        for ch in shp:
            if unicodedata.category(ch).startswith("L"):
                assert ch == "a"
            if ch.isdigit():
                assert ch == "9"

    mu_lines = [r for r in diag["lines"] if r["extractor"] == "pymupdf"]
    assert [(r["page"], r["line_index"], r["parser_decision"], r["starts_with_annexure"]) for r in mu_lines] == [
        (10, 0, "skipped:header", False),
        (10, 1, "skipped:empty", False),
        (10, 2, "continuation", False),
        (10, 3, "accepted", False),
        (10, 4, "continuation", False),
        (10, 5, "continuation", False),
        (10, 6, "skipped:empty", False),
        (10, 7, "accepted", False),
        (10, 8, "skipped:orphan_number", False),
        (11, 0, "skipped:heading", True),
        (11, 1, "continuation", False),
        (11, 2, "continuation", True),
        (11, 3, "accepted", False),
        (11, 4, "continuation", True),
        (11, 5, "continuation", False),
    ]
    assert mu_lines[2]["shape"] == "9. aaa aaa"
    assert len(mu_lines[-1]["shape"]) == 80

    assert diag["decision_counts_by_extractor"] == {
        "pymupdf": {
            "skipped:header": 1,
            "skipped:empty": 2,
            "continuation": 7,
            "accepted": 3,
            "skipped:orphan_number": 1,
            "skipped:heading": 1,
        },
        "pypdf2": {
            "accepted": 2,
            "skipped:heading": 1,
        },
    }
    assert diag["decision_counts"] == {
        "skipped:header": 1,
        "skipped:empty": 2,
        "continuation": 7,
        "accepted": 5,
        "skipped:orphan_number": 1,
        "skipped:heading": 2,
    }

    # CLI contents --shapes prints one line per text line + counts; R2 checks zero source words / non-ASCII letters
    fake_mupdf_list = [mupdf_map[p] for p in range(1, 117)]
    fake_pdf2_list = [pdf2_map[p] for p in range(1, 117)]
    monkeypatch.setattr(tree_audit, "pymupdf_pages", lambda p: fake_mupdf_list)
    monkeypatch.setattr(tree_audit, "pypdf2_pages", lambda p: fake_pdf2_list)
    rc = tree_audit.main(["contents", "--pdf", "synthetic.pdf", "--shapes"])
    assert rc == 0
    stdout = capsys.readouterr().out
    assert_no_source_words(stdout, sources)
    parsed_shapes = []
    for out_line in stdout.splitlines():
        m_shp = re.search(r"shape=(\".*\")\s*$", out_line)
        if m_shp:
            val = json.loads(m_shp.group(1))
            parsed_shapes.append(val)
            for ch in val:
                if unicodedata.category(ch).startswith("L"):
                    assert ch == "a"
    assert len(parsed_shapes) == len(diag["lines"])
    assert "starts_with_annexure=true decision=skipped:heading" in stdout
    assert "decision_counts" in stdout


def test_rule4_known_limit_trailing_dot_is_continuation():
    pages = {
        10: "\n".join(
            [
                "1 Scope",
                "3 Section without page",
                "4. Earlier Jump",
                "20",
            ]
        ),
        11: "",
    }
    parsed = tree_audit.parse_contents_pages(pages, toc_pages=(10, 11), max_printed_page=116)
    assert parsed["entry_count"] == 1
    assert parsed["no_page"] == 1
    assert parsed["entries"][0]["title"] == "3 Section without page 4. Earlier Jump"
    assert parsed["entries"][0]["printed_page"] == 20


def test_leakcheck_helper_catches_source_words_and_ignores_schema_words():
    assert_no_source_words('{"headings_skipped": 1, "entries": 24, "is_annexure": true}', ["Headings Zzz", "Qqq"])
    with pytest.raises(AssertionError, match="Zzz"):
        assert_no_source_words('{"headings_skipped": 1, "leaked": "Zzz"}', ["Zzz Qqq"])
    with pytest.raises(AssertionError, match="Zzz"):
        assert_no_source_words('{"headings_skipped": 1, "leaked": "zzz"}', ["Zzz Qqq"])
    with pytest.raises(AssertionError):
        assert_no_source_words('{"headings_skipped": 1, "leaked": "β"}', ["Section β"])


def test_explain_fallbacks_reports_scores_joins_and_leaks_zero_letters(monkeypatch, capsys):
    mu_p10 = "\n".join(
        [
            "Contents",
            "1 Zzz Qqq",  # printed 2 -> expected 13 (window 10..16 excl 10-11 -> 12..16)
            "2",
            "3 Kkk Zzz βββ Ééé",  # printed 25 -> expected 36 (window 33..39; wide 26..46)
            "25",
        ]
    )
    mu_p11 = "\n".join(
        [
            "Annexures",
            "Qqq Zzz Kkk Form",  # printed 40 -> expected 51 (window 48..54; wide 41..61)
            "Annexure III",
            "40",
        ]
    )
    p2_p10 = "1 Zzz Qqq 2\n3 Kkk Zzz βββ Ééé 25"
    p2_p11 = "Annexures\nQqq Zzz Kkk Form Annexure III 40"

    mupdf_pages = {p: "" for p in range(1, 117)}
    pypdf2_pages = {p: "" for p in range(1, 117)}
    mupdf_pages[10], mupdf_pages[11] = mu_p10, mu_p11
    pypdf2_pages[10], pypdf2_pages[11] = p2_p10, p2_p11

    # Entry 0 (index 0): matches inside window at page 13 on PyMuPDF -> NOT an offset_fallback
    mupdf_pages[13] = "1 Zzz Qqq"

    # Entry 1 (index 1, printed 25, expected 36): window [33..39], wide [26..46]
    # Window has only 2-line partial match scoring 84.615 < 90 on PyMuPDF page 35; wide has 3-line exact match on page 42
    mupdf_pages[35] = "3 Kkk Zzz\nβββ"
    mupdf_pages[42] = "3 Kkk\nZzz βββ\nÉéé"
    # PyPDF2 has 1-line partial match 63.636 < 90 on page 37 and exact 2-line match at wide page 30
    pypdf2_pages[37] = "3 Kkk Zzz"
    pypdf2_pages[30] = "3 Kkk Zzz\nβββ Ééé"

    # Entry 2 (index 2, printed 40, expected 51, Annexure III): window [48..54], wide [41..61]
    # Partial 60.87 < 90 on PyMuPDF page 50; exact 1-line match on PyMuPDF wide page 58; PyPDF2 empty
    mupdf_pages[50] = "Qqq Zzz"
    mupdf_pages[58] = "Qqq Zzz Kkk Form"

    sources = [
        mu_p10,
        mu_p11,
        p2_p10,
        p2_p11,
        mupdf_pages[13],
        mupdf_pages[35],
        mupdf_pages[42],
        pypdf2_pages[37],
        pypdf2_pages[30],
        mupdf_pages[50],
        mupdf_pages[58],
    ]

    monkeypatch.setattr(tree_audit, "pymupdf_pages", lambda p: [mupdf_pages[i] for i in range(1, 117)])
    monkeypatch.setattr(tree_audit, "pypdf2_pages", lambda p: [pypdf2_pages[i] for i in range(1, 117)])

    rc = tree_audit.main(["contents", "--pdf", "synthetic.pdf", "--explain-fallbacks"])
    assert rc == 0
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert payload["offset_fallbacks"] == 2
    exps = payload["fallback_explanations"]
    assert len(exps) == 2

    e1 = exps[0]
    assert e1["entry_index"] == 1
    assert e1["printed_page"] == 25
    assert e1["expected_start"] == 36
    assert e1["is_annexure"] is False
    assert e1["annexure_numeral"] is None
    assert e1["numbered"] is True
    assert e1["title_word_count"] == 5
    assert e1["pymupdf"]["window"] == {"best_score": 84.615, "page": 35, "join_size": 2}
    assert e1["pymupdf"]["wide"] == {"best_score": 100.0, "page": 42, "join_size": 3}
    assert e1["pypdf2"]["window"] == {"best_score": 63.636, "page": 37, "join_size": 1}
    assert e1["pypdf2"]["wide"] == {"best_score": 100.0, "page": 30, "join_size": 2}

    e2 = exps[1]
    assert e2["entry_index"] == 2
    assert e2["printed_page"] == 40
    assert e2["expected_start"] == 51
    assert e2["is_annexure"] is True
    assert e2["annexure_numeral"] == "III"
    assert e2["numbered"] is False
    assert e2["title_word_count"] == 4
    assert e2["pymupdf"]["window"] == {"best_score": 60.87, "page": 50, "join_size": 1}
    assert e2["pymupdf"]["wide"] == {"best_score": 100.0, "page": 58, "join_size": 1}
    assert e2["pypdf2"]["window"] == {"best_score": 0.0, "page": None, "join_size": None}
    assert e2["pypdf2"]["wide"] == {"best_score": 0.0, "page": None, "join_size": None}

    # R2 leak check across entire output (masking only the verified explicit "III" numeral fields)
    redacted = json.loads(stdout)
    redacted["annexure_numerals"] = []
    redacted["contents_parse_by_extractor"]["pymupdf"]["annexure_numerals"] = []
    redacted["contents_parse_by_extractor"]["pypdf2"]["annexure_numerals"] = []
    for row in redacted["fallback_explanations"]:
        row["annexure_numeral"] = None
    assert_no_source_words(json.dumps(redacted, ensure_ascii=False), sources)

    # Verify pages 10-11 are excluded from both window and expected +/- 10 searches
    toc_only_pages = {p: "" for p in range(1, 117)}
    toc_only_pages[10] = "1 Zzz Qqq"
    toc_only_pages[11] = "1 Zzz Qqq"
    toc_excl_analysis = {
        "doc_pages": 116,
        "entries": [
            {
                "index": 0,
                "printed_page": 1,
                "expected_start": 12,
                "title": "1 Zzz Qqq",
                "is_annexure": False,
                "annexure_numeral": None,
                "matched_extractor": "offset_fallback",
            }
        ],
    }
    excl = tree_audit.explain_contents_fallbacks(toc_excl_analysis, toc_only_pages, toc_only_pages)
    assert excl[0]["pymupdf"]["window"] == {"best_score": 0.0, "page": None, "join_size": None}
    assert excl[0]["pymupdf"]["wide"] == {"best_score": 0.0, "page": None, "join_size": None}


def test_variant_b_title_match_3_pages_early_fails_cond2():
    entries = [
        {
            "index": 0,
            "toc_page": 10,
            "title": "1 Scope",
            "printed_page": 9,
            "expected_start": 20,
            "is_annexure": False,
            "annexure_numeral": None,
        }
    ]
    structure = [
        {
            "node_id": "0001",
            "title": "1 Scope",
            "start_index": 17,
            "end_index": 25,
        }
    ]
    res = tree_audit.audit_tree_variant(
        structure,
        entries,
        variant="B",
        allow_key_items=True,
        contained_start_check=True,
        coverage_start=17,
        coverage_end=25,
        expected_annexures=0,
    )
    assert res["matches"][0]["match_source"] == "title"
    cond2 = res["criteria"]["start_within_1_page_95pct"]
    assert cond2["within_tolerance"] == 0
    assert cond2["matched"] == 1
    assert cond2["rate"] == 0.0
    assert cond2["pass"] is False


@pytest.mark.parametrize("expected_start", [19, 22, 25])
def test_variant_b_key_item_match_inside_span_pm1_passes_cond2(expected_start):
    entries = [
        {
            "index": 0,
            "toc_page": 10,
            "title": "Subtopic A",
            "printed_page": expected_start - 11,
            "expected_start": expected_start,
            "is_annexure": False,
            "annexure_numeral": None,
        }
    ]
    structure = [
        {
            "node_id": "0001",
            "title": "Parent Node",
            "start_index": 20,
            "end_index": 24,
            "key_items": ["Subtopic A"],
        }
    ]
    res = tree_audit.audit_tree_variant(
        structure,
        entries,
        variant="B",
        allow_key_items=True,
        contained_start_check=True,
        coverage_start=20,
        coverage_end=24,
        expected_annexures=0,
    )
    assert res["matches"][0]["match_source"] == "key_item"
    cond2 = res["criteria"]["start_within_1_page_95pct"]
    assert cond2["within_tolerance"] == 1
    assert cond2["matched"] == 1
    assert cond2["rate"] == 1.0
    assert cond2["pass"] is True


@pytest.mark.parametrize("expected_start", [18, 26])
def test_variant_b_key_item_match_outside_span_pm1_fails_cond2(expected_start):
    entries = [
        {
            "index": 0,
            "toc_page": 10,
            "title": "Subtopic A",
            "printed_page": expected_start - 11,
            "expected_start": expected_start,
            "is_annexure": False,
            "annexure_numeral": None,
        }
    ]
    structure = [
        {
            "node_id": "0001",
            "title": "Parent Node",
            "start_index": 20,
            "end_index": 24,
            "key_items": ["Subtopic A"],
        }
    ]
    res = tree_audit.audit_tree_variant(
        structure,
        entries,
        variant="B",
        allow_key_items=True,
        contained_start_check=True,
        coverage_start=20,
        coverage_end=24,
        expected_annexures=0,
    )
    assert res["matches"][0]["match_source"] == "key_item"
    cond2 = res["criteria"]["start_within_1_page_95pct"]
    assert cond2["within_tolerance"] == 0
    assert cond2["matched"] == 1
    assert cond2["rate"] == 0.0
    assert cond2["pass"] is False


def test_variant_a_unchanged_on_existing_fixture():
    entries, clean_tree = _sample_contents_and_tree()
    clean_res = tree_audit.audit_tree(clean_tree, entries)
    clean_a = clean_res["variants"]["A"]
    assert clean_a["pass"] is True
    assert clean_a["criteria"]["presence_95pct"]["matched"] == 20
    assert clean_a["criteria"]["start_within_1_page_95pct"]["within_tolerance"] == 20
    assert clean_a["criteria"]["start_within_1_page_95pct"]["rate"] == 1.0

    # Merge Sec 2 (expected_start=21) into Sec 1 leaf (16..25, within 15..26);
    # shift Sec 3 title start from 26 to 24 (abs(24-26)=2 > 1 even though 24..30 spans 26).
    entries2, mod_tree = _sample_contents_and_tree()
    sec2_title = mod_tree["structure"][1]["title"]
    mod_tree["structure"][0]["end_index"] = 25
    mod_tree["structure"][0]["key_items"] = [sec2_title]
    mod_tree["structure"].pop(1)
    assert "nodes" not in mod_tree["structure"][0]
    mod_tree["structure"][1]["start_index"] = 24  # shifted Sec 3

    mod_res = tree_audit.audit_tree(mod_tree, entries2)
    va = mod_res["variants"]["A"]
    assert va["criteria"]["presence_95pct"]["matched"] == 19
    assert va["criteria"]["start_within_1_page_95pct"]["within_tolerance"] == 18
    assert va["criteria"]["start_within_1_page_95pct"]["rate"] == 0.947368
    assert va["criteria"]["start_within_1_page_95pct"]["pass"] is False
    assert va["pass"] is False
    assert mod_res["overall_pass"] is False

    vb = mod_res["variants"]["B"]
    assert vb["criteria"]["presence_95pct"]["matched"] == 20
    assert vb["criteria"]["start_within_1_page_95pct"]["within_tolerance"] == 19
    assert vb["criteria"]["start_within_1_page_95pct"]["rate"] == 0.95
    assert vb["criteria"]["start_within_1_page_95pct"]["pass"] is True
    assert vb["pass"] is True
