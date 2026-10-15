"""Synthetic official-source fixtures, independent of the frozen PDF parser."""
import pytest
from ingest.structure_gold import parse_ecfr, parse_eurlex, score, wilson, normalise, coverage


def test_ecfr_projection(tmp_path):
    path = tmp_path/'gold.xml'
    path.write_text('''<ROOT><DIV8 TYPE="SECTION" N="63.362"><HEAD>§ 63.362 Standards.</HEAD>
      <P>(a) First body</P><P>(1) Nested text</P><P>(i) Roman body</P><P>(b) Second body</P></DIV8>
      <DIV9 TYPE="APPENDIX"><HEAD>Appendix A to Subpart O</HEAD><P>11. Appendix body</P>
      <P>11.5. Method body</P><P>11.5.1 Third level</P><P>11.5.1.1 Capped body</P></DIV9>
      <DIV9 TYPE="APPENDIX"><HEAD>Table 4 to Subpart O</HEAD><P>Full table body</P></DIV9>
      <DIV8 TYPE="SECTION" N="63.369"><HEAD>Outside slice</HEAD><P>No</P></DIV8></ROOT>''', encoding='utf-8')
    rows = {u['number']: u for u in parse_ecfr(path)}
    assert set(rows) == {'§63.362(a)', '§63.362(b)', 'Appendix A.11', 'Appendix A.11.5', 'Appendix A.11.5.1', 'Table 4'}
    assert rows['§63.362(a)']['parent'] is None
    assert 'Roman body' in rows['§63.362(a)']['text']
    assert 'Capped body' in rows['Appendix A.11.5.1']['text']
    assert rows['Appendix A.11.5']['parent'] == 'epa-63o:Appendix A.11'


def test_eurlex_projection(tmp_path):
    path = tmp_path/'gold.html'
    path.write_text('''<html><body><p>Article 1</p><p>Scope introduction.</p><p>1. First paragraph</p>
      <table><tr><td>(a)</td><td>Letter point</td></tr><tr><td>(i)</td><td>Nested Roman</td></tr>
      <tr><td>(b)</td><td>Second point</td></tr></table><p>Article 11</p><p>Out of scope</p>
      <p>ANNEX I</p><p>CHAPTER I</p><p>10.4.1. Chemical properties</p><p>(a) Merged letter</p>
      <p>12. Monitoring plan.</p><p>ANNEX IV</p><p>1. Outside</p><p>ANNEX XV</p><p>CHAPTER II</p>
      <p>2. Own body</p><p>2.3. Child body</p><p>2.3.4.5. Capped</p></body></html>''', encoding='utf-8')
    rows = {u['number']: u for u in parse_eurlex(path)}
    assert set(rows) == {'Art 1', 'Art 1(1)', 'Art 1(1)(a)', 'Art 1(1)(b)', 'Annex I.10.4.1', 'Annex XV.II.2', 'Annex XV.II.2.3'}
    assert 'Nested Roman' in rows['Art 1(1)(a)']['text']
    assert rows['Annex XV.II.2']['parent'] is None
    assert rows['Annex XV.II.2.3']['parent'] == 'eu-mdr:Annex XV.II.2'
    assert 'Capped' in rows['Annex XV.II.2.3']['text']


def test_score_and_intervals():
    gold = [dict(official_id='d:1', number='1', parent=None, text='“ABC” — ﬁ'),
            dict(official_id='d:2', number='2', parent=None, text='Missing')]
    actual = [dict(id='d:1', doc='d', parent=None, text='"abc" - fi'),
              dict(id='d:3', doc='d', parent=None, text='Extra')]
    result = score(gold, actual, 'd')
    assert result['precision'] == result['recall'] == result['f1'] == .5
    assert result['parent_accuracy'] == result['coverage'] == 1
    assert result['missing'][0]['id'] == 'd:2'
    assert result['extra'][0]['id'] == 'd:3'
    assert wilson(1, 2) == pytest.approx([.0945312057, .9054687943])
    assert wilson(0, 0) == [0, 1]
    assert coverage('abcdef', 'abc') == .5
    assert normalise('Ａ  B') == 'a b'


def test_reject_unrecognised_sources(tmp_path):
    path = tmp_path/'empty'
    path.write_text('<html><p>No structure</p></html>', encoding='utf-8')
    with pytest.raises(ValueError):
        parse_eurlex(path)
    with pytest.raises(ValueError):
        parse_ecfr(path)
