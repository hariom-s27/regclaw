"""Offline tests for eval.s11.metrics (retrieval R/C, C1-C5 grounding, status rules, write-once records)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

import _netguard
from eval.s11 import metrics
from eval.s11.ledger import WriteOnceError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("REGCLAW_STUB", "1")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    _netguard.block_network(monkeypatch, "s11b2b metrics tests forbid network")


def _golden_ans(qid: str = "gtp-01", gold_sets: list[list[int]] | None = None) -> dict:
    return {
        "id": qid,
        "type": "multi_page",
        "answerable": True,
        "question": "What storage rules apply?",
        "reference": "Store samples cold with labels.",
        "reference_claims": ["Store samples cold."],
        "gold_page_sets": gold_sets if gold_sets is not None else [[10, 11], [20, 21, 22]],
        "unanswerable_note": "",
    }


def _golden_unans(qid: str = "gtp-13") -> dict:
    return {
        "id": qid,
        "type": "unanswerable_near_miss",
        "answerable": False,
        "question": "Does it specify refund fee?",
        "reference": "The guideline does not state this.",
        "reference_claims": [],
        "gold_page_sets": [],
        "unanswerable_note": "Checked fees; omitted.",
    }


def _result(
    *,
    system: str = "PI",
    qid: str = "gtp-01",
    pages_given: list[int] | None = None,
    answer: str = "Store at 2-8 C [p. 10].",
    status: str = "ok",
    refusal: bool = False,
) -> dict:
    return {
        "system": system,
        "question_id": qid,
        "pages_given": [10, 11] if pages_given is None else pages_given,
        "answer_pre_repair": answer,
        "answer": answer,
        "repaired": False,
        "dropped_sentences": [],
        "refusal": refusal,
        "check_status": "checked",
        "stop_reason": "end_turn",
        "status": status,
        "model_returned": "claude-sonnet-5-5",
        "verifier_model_returned": "claude-haiku-5-5",
        "effort": "medium",
        "usage": {},
        "latency_s": 0.5,
    }


def test_retrieval_metrics_two_gold_sets_best_recall_and_empty_precision_na():
    # Two gold sets: G1 = {10, 11}, G2 = {20, 21, 22}, U = {10, 11, 20, 21, 22}
    # X = {10, 20, 21, 99} -> |X & G1|/|G1| = 1/2 = 0.5, |X & G2|/|G2| = 2/3 -> best recall = 2/3
    # precision = |X & U| / |X| = 3 / 4 = 0.75
    hit, rec, prec, prec_na = metrics.compute_set_metrics({10, 20, 21, 99}, [[10, 11], [20, 21, 22]])
    assert hit is True
    assert rec == pytest.approx(2 / 3)
    assert prec == pytest.approx(0.75)
    assert prec_na is None

    # Empty set -> hit False, recall 0.0, precision None ("empty_set")
    hit0, rec0, prec0, prec0_na = metrics.compute_set_metrics(set(), [[10, 11], [20, 21, 22]])
    assert hit0 is False and rec0 == 0.0 and prec0 is None and prec0_na == "empty_set"


def test_citation_parser_single_range_endash_and_malformed_cases():
    # Valid: [p. N], [pp. N-M], en dash, optional spaces, N == M
    res = metrics.parse_citations("See [p. 4], [pp. 5-7], [pp. 10–12], [ pp. 20 - 20 ], [p.2].")
    assert res.pages == [4, 5, 6, 7, 10, 11, 12, 20, 2]
    assert res.malformed == []

    # Page 0 and 117 parse into C (not malformed); checked by C1 range 1..116
    bounds = metrics.parse_citations("Edge [p. 0] and [p. 117].")
    assert bounds.pages == [0, 117]
    assert bounds.malformed == []

    # Malformed bracket forms
    bad_samples = [
        "[p. x]",
        "[pp. 9-7]",
        "[p. 5-7]",
        "[pp. 5]",
        "[p. 1, 2]",
        "[p. 1 2]",
        "[P. 5]",
        "[PP. 5-7]",
        "[p. ]",
        "[pp. a-b]",
    ]
    joined = " ".join(f"Text {b}." for b in bad_samples)
    bad = metrics.parse_citations(joined)
    assert bad.pages == []
    assert bad.malformed == bad_samples


@pytest.mark.parametrize(
    "answer,pages_given,exp_c1,exp_c2,exp_malformed,exp_c",
    [
        ("Fact [pp. 5-7].", [5, 6, 7, 8], True, True, 0, [5, 6, 7]),
        ("Fact [pp. 5–7].", [5, 6, 7], True, True, 0, [5, 6, 7]),
        ("Bad [p. x] and ok [p. 5].", [5], False, True, 1, [5]),
        ("Reversed [pp. 9-7] and ok [p. 5].", [5], False, True, 1, [5]),
        ("Zero [p. 0].", [1, 2], False, False, 0, [0]),
        ("Over [p. 117].", [1, 2], False, False, 0, [117]),
        ("Not fetched [p. 9].", [5, 6], True, False, 0, [9]),
        ("Mixed [p. 5] and [p. 9].", [5, 6], True, False, 0, [5, 9]),
    ],
)
def test_c1_and_c2_bounds_malformed_and_not_in_r(answer, pages_given, exp_c1, exp_c2, exp_malformed, exp_c):
    g = _golden_ans(gold_sets=[[5, 6, 7]])
    rec = _result(pages_given=pages_given, answer=answer)
    m = metrics.score_result_record(rec, g, {p: "Fact." for p in range(1, 117)}, run_id="r1")
    assert m["c1"] is exp_c1 and m["c1_na"] is None
    assert m["c2"] is exp_c2 and m["c2_na"] is None
    assert m["malformed_citations"] == exp_malformed
    assert m["C"] == exp_c


@pytest.mark.parametrize(
    "answerable,status,refusal,answer,expected_hit_c",
    [
        (True, "ok", False, "Fact [p. 10].", True),
        (True, "ok", False, "Miss [p. 50].", False),
        (True, "ok", False, "No citation here.", False),
        (True, "failed:max_turns", False, "", False),
        (True, "ok", True, "The guideline does not state this.", False),
        (False, "ok", False, "Wrongly answered [p. 10].", None),
        (False, "ok", True, "The guideline does not state this.", None),
        (False, "failed:max_tokens", False, "", None),
    ],
)
def test_c3_equals_hit_c_parametrised(answerable, status, refusal, answer, expected_hit_c):
    g = _golden_ans(gold_sets=[[10, 11]]) if answerable else _golden_unans()
    rec = _result(pages_given=[10, 50], answer=answer, status=status, refusal=refusal)
    m = metrics.score_result_record(rec, g, {10: "Fact.", 50: "Miss."}, run_id="r1")
    assert m["c3"] is m["hit_C"]
    assert m["c3"] is expected_hit_c
    assert m["c3_na"] == m["hit_C_na"]


def test_status_rules_all_rows_and_exact_na_reasons():
    page_texts = {10: "Store samples at 2-8 C.", 11: "Label samples clearly."}

    # 1) Answerable + ok + zero citations -> C1/C2/precision_C n/a, C4 = 0.0
    m_nocite = metrics.score_result_record(
        _result(pages_given=[10], answer="Store samples cold."),
        _golden_ans(gold_sets=[[10, 11]]),
        page_texts,
        run_id="r1",
    )
    assert m_nocite["hit_R"] is True and m_nocite["recall_R"] == pytest.approx(0.5) and m_nocite["precision_R"] == pytest.approx(1.0)
    assert m_nocite["hit_C"] is False and m_nocite["recall_C"] == 0.0 and m_nocite["c3"] is False
    assert m_nocite["precision_C"] is None and m_nocite["precision_C_na"] == "empty_set"
    assert m_nocite["c1"] is None and m_nocite["c1_na"] == "no_citations"
    assert m_nocite["c2"] is None and m_nocite["c2_na"] == "no_citations"
    assert m_nocite["c4"] == 0.0 and m_nocite["c4_na"] is None
    assert m_nocite["c5"] is None and m_nocite["c5_na"] == "no_citations"

    # 2) Answerable + failed item -> R computed; hit_C=False, recall_C=0.0, c3=False; C1,C2,C4,C5,precision_C n/a ("failed_item")
    m_fail = metrics.score_result_record(
        _result(pages_given=[10], answer="", status="failed:max_turns"),
        _golden_ans(gold_sets=[[10, 11]]),
        page_texts,
        run_id="r1",
    )
    assert m_fail["hit_R"] is True and m_fail["recall_R"] == pytest.approx(0.5)
    assert m_fail["hit_C"] is False and m_fail["recall_C"] == 0.0 and m_fail["c3"] is False
    for key in ("precision_C", "c1", "c2", "c4", "c5", "numbers_checked", "sentences"):
        assert m_fail[key] is None and m_fail[f"{key}_na"] == "failed_item"

    # 3) Answerable + refusal -> hit_C=False, recall_C=0.0, c3=False; C1,C2,C4,C5,precision_C n/a ("refusal")
    m_ref = metrics.score_result_record(
        _result(pages_given=[10], answer="The guideline does not state this.", refusal=True),
        _golden_ans(gold_sets=[[10, 11]]),
        page_texts,
        run_id="r1",
    )
    assert m_ref["hit_R"] is True and m_ref["hit_C"] is False and m_ref["recall_C"] == 0.0 and m_ref["c3"] is False
    for key in ("precision_C", "c1", "c2", "c4", "c5", "numbers_checked", "sentences"):
        assert m_ref[key] is None and m_ref[f"{key}_na"] == "refusal"

    # 4) Unanswerable + refusal -> retrieval and C3 n/a ("unanswerable"); C1,C2,C4,C5 n/a ("refusal")
    m_uref = metrics.score_result_record(
        _result(qid="gtp-13", pages_given=[10], answer="The guideline does not state this.", refusal=True),
        _golden_unans("gtp-13"),
        page_texts,
        run_id="r1",
    )
    for key in ("hit_R", "recall_R", "precision_R", "hit_C", "recall_C", "precision_C", "c3"):
        assert m_uref[key] is None and m_uref[f"{key}_na"] == "unanswerable"
    for key in ("c1", "c2", "c4", "c5"):
        assert m_uref[key] is None and m_uref[f"{key}_na"] == "refusal"

    # 5) Unanswerable + non-refusal answered -> retrieval and C3 n/a ("unanswerable"); C1,C2,C4,C5 computed
    m_uans = metrics.score_result_record(
        _result(qid="gtp-13", pages_given=[10], answer="Store samples at 2-8 C [p. 10].", refusal=False),
        _golden_unans("gtp-13"),
        page_texts,
        run_id="r1",
    )
    for key in ("hit_R", "recall_R", "precision_R", "hit_C", "recall_C", "precision_C", "c3"):
        assert m_uans[key] is None and m_uans[f"{key}_na"] == "unanswerable"
    assert m_uans["c1"] is True and m_uans["c2"] is True
    assert m_uans["c4"] == pytest.approx(1.0) and m_uans["c5"] == pytest.approx(1.0)

    # 6) HY5 answerable and HY5 unanswerable
    hy5_rec = {"system": "HY5", "question_id": "gtp-01", "k": 5, "selected_pages": [10, 99]}
    m_hy5 = metrics.score_hy5_record(hy5_rec, _golden_ans(gold_sets=[[10, 11]]), run_id="r1")
    assert m_hy5["hit_R"] is True and m_hy5["recall_R"] == pytest.approx(0.5) and m_hy5["precision_R"] == pytest.approx(0.5)
    assert m_hy5["C"] == [] and m_hy5["malformed_citations"] == 0
    for key in (
        "status",
        "refusal",
        "hit_C",
        "recall_C",
        "precision_C",
        "c1",
        "c2",
        "c3",
        "c4",
        "c5",
        "numbers_checked",
        "numbers_found",
        "numbers_uncited",
        "sentences",
        "cited_sentences",
    ):
        assert m_hy5[key] is None and m_hy5[f"{key}_na"] == "not_applicable_system"

    m_hy5_u = metrics.score_hy5_record(
        {"system": "HY5", "question_id": "gtp-13", "k": 5, "selected_pages": [10]},
        _golden_unans("gtp-13"),
        run_id="r1",
    )
    for key in ("hit_R", "recall_R", "precision_R"):
        assert m_hy5_u[key] is None and m_hy5_u[f"{key}_na"] == "unanswerable"
    assert m_hy5_u["c1"] is None and m_hy5_u["c1_na"] == "not_applicable_system"


def test_split_sentences_all_required_cases():
    # Decimals + citation before full stop + citation after full stop + two citations in a row
    txt = (
        "The cutoff value is 3.5 mg per dose [p. 4]. "
        "Samples must stay cold. [p. 5] [p. 6] "
        "20 samples passed review [p. 7]. "
        "\"Quoted rule applies here\" [p. 8]. "
        "(Additional note stays separate.) [p. 9]"
    )
    sents = metrics.split_sentences(txt)
    assert sents == [
        "The cutoff value is 3.5 mg per dose [p. 4].",
        "Samples must stay cold. [p. 5] [p. 6]",
        "20 samples passed review [p. 7].",
        '"Quoted rule applies here" [p. 8].',
        "(Additional note stays separate.) [p. 9]",
    ]

    # All 13 abbreviations (case-insensitive)
    for abbr in (
        "e.g.",
        "E.G.",
        "i.e.",
        "etc.",
        "viz.",
        "vs.",
        "no.",
        "Nos.",
        "fig.",
        "Sec.",
        "approx.",
        "Dr.",
        "Mr.",
        "Ms.",
    ):
        s = f"Check {abbr} Item details here [p. 2]."
        assert metrics.split_sentences(s) == [s], f"unexpected split after {abbr}"

    # List markers on separate lines + heading line + 2-word fragment
    bullets = (
        "# Heading skipped\n\n"
        "Too short.\n"
        "1. First numbered requirement holds [p. 1].\n"
        "2) Second marker requirement holds [p. 2].\n"
        "- Dash bullet requirement holds [p. 3].\n"
        "* Star bullet requirement holds [p. 4].\n"
        "• Dot bullet requirement holds [p. 5]."
    )
    bsents = metrics.split_sentences(bullets)
    assert len(bsents) == 5
    assert bsents[0].startswith("1. First")
    assert bsents[1].startswith("2) Second")
    assert bsents[2].startswith("- Dash")
    assert bsents[3].startswith("* Star")
    assert bsents[4].startswith("• Dot")

    # Refusal splits into 1 sentence
    assert metrics.split_sentences("The guideline does not state this.") == ["The guideline does not state this."]

    # Malformed-only citation does not count as cited for C4
    g = _golden_ans(gold_sets=[[10]])
    m_mal = metrics.score_result_record(
        _result(pages_given=[10], answer="Samples stay cold [p. x]. Samples are labeled [p. 10]."),
        g,
        {10: "Samples stay cold and labeled."},
        run_id="r1",
    )
    assert m_mal["sentences"] == 2
    assert m_mal["cited_sentences"] == 1
    assert m_mal["c4"] == pytest.approx(0.5)
    assert m_mal["c1"] is False


def test_c5_found_not_found_thousands_decimal_uncited_and_cross_sentence_isolation():
    page_texts = {
        10: "Store 1000 samples at 3.5 C for 14 days in Phase2 with 2mg buffer.",
        11: "Batch size is 2,500 vials.",
        12: "Other limit is 99 days.",
    }
    answer = (
        "1. Store 1,000 samples at 3.5 C [p. 10]. "
        "Keep 2500 vials ready [p. 11]. "
        "Check 99 limits here [p. 10]. "
        "Missing 777 threshold [p. 10]. "
        "See 3 and 5 not matched by decimal [p. 10]. "
        "Compare Phase2 and 2mg tokens [p. 10]. "
        "Uncited sentence mentions 42 items."
    )
    # Cited sentences:
    # - s1: 1,000 (->1000, found on p10), 3.5 (found on p10); marker "1." ignored -> 2/2
    # - s2: 2500 (found as "2,500" on p11) -> 1/1
    # - s3: 99 (only on p12, NOT on p10 cited by s3!) -> 0/1
    # - s4: 777 (not found) -> 0/1
    # - s5: 3 and 5 (p10 only has 3.5, decimal guard blocks both "3" and "5") -> 0/2
    # - s6: Phase2 and 2mg (adjacent to letters -> 0 tokens checked)
    # - s7 (uncited): 42 -> numbers_uncited = 1
    sents = metrics.split_sentences(answer)
    assert len(sents) == 7
    c5_res = metrics.compute_c5(sents, page_texts)
    assert c5_res == {
        "numbers_checked": 7,
        "numbers_found": 3,
        "numbers_uncited": 1,
        "c5": pytest.approx(3 / 7),
        "c5_na": None,
    }

    # Cited sentence with no numbers -> c5 None ("no_citations")
    c5_empty = metrics.compute_c5(["Store cold [p. 10]."], page_texts)
    assert c5_empty == {
        "numbers_checked": 0,
        "numbers_found": 0,
        "numbers_uncited": 0,
        "c5": None,
        "c5_na": "no_citations",
    }


def test_metric_record_exact_fields_and_write_once(tmp_path):
    out_file = tmp_path / "metrics" / "gtp-01.json"
    rec = _result(
        pages_given=[10, 11],
        answer="SECRET_ANSWER_SENTINEL store 2-8 C [p. 10].",
    )
    g = _golden_ans(gold_sets=[[10, 11]])
    g["question"] = "SECRET_QUESTION_SENTINEL"
    g["reference"] = "SECRET_REF_SENTINEL"
    page_texts = {10: "SECRET_PAGE_SENTINEL 2-8 C.", 11: "More text."}

    scored = metrics.score_result_record(rec, g, page_texts, run_id="run-1", out_path=out_file)
    assert tuple(scored.keys()) == metrics.METRIC_RECORD_KEYS
    assert len(scored) == 44
    assert scored["rules_version"] == "s11-metrics-v1"
    assert scored["run_id"] == "run-1"
    assert scored["R"] == [10, 11]
    assert scored["C"] == [10]

    raw_text = out_file.read_text(encoding="utf-8")
    loaded = json.loads(raw_text)
    assert loaded == scored
    for sentinel in ("SECRET_ANSWER_SENTINEL", "SECRET_QUESTION_SENTINEL", "SECRET_REF_SENTINEL", "SECRET_PAGE_SENTINEL"):
        assert sentinel not in raw_text

    with pytest.raises(WriteOnceError):
        metrics.score_result_record(rec, g, page_texts, run_id="run-1", out_path=out_file)


def test_fresh_subprocess_import_metrics_from_repo_root():
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
        import eval.s11.metrics
        assert hasattr(eval.s11.metrics, "score_result_record")
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
