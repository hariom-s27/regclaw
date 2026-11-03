"""R22 P5: PyPDF2 vs PyMuPDF page-text parity tool (fakes always; a tiny real PDF if both parsers exist)."""
import importlib
import json
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('Network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', deny)


@pytest.fixture
def pp(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'pageindex'))
    return importlib.import_module('parser_parity')


def test_whitespace_only_differences_are_identical(pp):
    assert pp.similarity('Storage at\n2-8 C', 'Storage  at 2-8 C\n') == 1.0
    assert pp.similarity('', '  \n') == 1.0


def test_compare_flags_pages_below_threshold(pp):
    tree = ['Chapter 1 Scope', 'Totally different words here', 'Annex A']
    fetch = ['Chapter 1  Scope', 'Storage shall be at 2-8 C', 'Annex A\n']
    r = pp.compare(tree, fetch, threshold=0.9)
    assert [row['page'] for row in r['pages']] == [1, 2, 3]       # 1-based physical pages
    assert r['pages_below_threshold'] == [2] and r['page_count_match']
    assert r['pages'][0]['similarity'] == 1.0 and r['pages'][1]['similarity'] < 0.9


def test_page_count_mismatch_scores_missing_pages_zero(pp):
    r = pp.compare(['a', 'b'], ['a'], threshold=0.5)
    assert not r['page_count_match'] and r['pages_below_threshold'] == [2]
    assert r['pages'][1] == {'page': 2, 'similarity': 0.0, 'chars_pypdf2': 1, 'chars_pymupdf': None}


def test_threshold_is_validated(pp):
    with pytest.raises(ValueError):
        pp.compare([], [], threshold=1.5)


def test_report_with_injected_extractors_and_cli_json(pp, tmp_path, monkeypatch, capsys):
    r = pp.parity_report('x.pdf', 0.9, tree_extractor=lambda p: ['same', 'abc'],
                         fetch_extractor=lambda p: ['same', 'xyz'])
    assert r['pdf'] == 'x.pdf' and r['pages_below_threshold'] == [2]
    monkeypatch.setattr(pp, 'parity_report', lambda pdf, t: pp.compare(['same'], ['same'], t) | {'pdf': pdf})
    out = tmp_path / 'parity.json'
    assert pp.main(['x.pdf', '--threshold', '0.8', '--json', str(out)]) == 0
    assert json.loads(out.read_text())['threshold'] == 0.8
    assert '0 page(s)' in capsys.readouterr().out


def test_tiny_generated_pdf_both_parsers(pp, tmp_path):
    pymupdf = pytest.importorskip('pymupdf')
    pytest.importorskip('PyPDF2')          # not in CI (decision: CI unchanged); runs where installed
    pdf = tmp_path / 'tiny.pdf'
    with pymupdf.open() as doc:
        for text in ('Chapter 1 Scope of the guideline', 'Storage shall be at 2 to 8 C', ''):
            page = doc.new_page()
            if text:
                page.insert_text((72, 72), text)
        doc.save(pdf)
    r = pp.parity_report(str(pdf), threshold=0.9)
    assert r['pages_pypdf2'] == r['pages_pymupdf'] == 3 and r['page_count_match']
    assert r['pages_below_threshold'] == []           # simple text: both parsers agree
    assert r['pages'][2]['chars_pymupdf'] == 0         # the blank page stays blank and 1-based
