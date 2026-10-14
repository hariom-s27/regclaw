import copy
import json
from pathlib import Path

import pymupdf as fitz
import pytest
import yaml

from ingest.ingest import (Line, TEXT_FLAGS, clean, extract_lines, filter_lines, heading,
                           is_bold, join_text, parse_document, resolve_ambiguous, run,
                           body_spans)
from ingest.validate import validate

CONFIG = yaml.safe_load((Path(__file__).parents[1] / 'ingest/sources.yaml').read_text(encoding='utf-8'))


def bold_line(text, page=1):
    return Line(text, page, spans=[{'text': text, 'font': 'Helvetica-Bold', 'flags': 16}])


def node(number, depth, kind='roman', title='Parent'):
    return {'id': 'x:' + number, 'number': number, '_depth': depth, '_kind': kind, 'title': title}


@pytest.mark.parametrize('doc,text,stack,kind,number', [
    ('fda-iso10993-1', 'III. Risk Management for Biocompatibility Evaluations', [], 'roman', 'III'),
    ('fda-iso10993-1', 'A. Risk Assessment of the Medical Device', [node('III', 1)], 'letter', 'III.A'),
    ('fda-iso10993-1', 'a. Cytotoxicity', [node('III', 1), node('III.A', 2), node('III.A.1', 3)], 'small_letter', 'III.A.1.a'),
    ('fda-chem-analysis', 'V. Test Article Extraction', [], 'roman', 'V'),
    ('fda-nitinol', 'IV. Technical Recommendations', [], 'roman', 'IV'),
    ('fda-nitinol', 'C. Corrosion Testing', [node('IV', 1)], 'letter', 'IV.C'),
    ('eu-mdr', 'Article 1', [], 'article', 'Art 1'),
    ('eu-mdr', 'ANNEX I', [], 'annex', 'Annex I'),
    ('eu-mdr', '10.1. Devices shall be designed and manufactured...', [node('Annex I', 1, 'annex')], 'decimal', 'Annex I.10.1'),
    ('imdrf-n47', '5.0', [], 'decimal', '5'),
    ('in-ep', '5.1 Chemical, physical and biological properties', [node('5', 1)], 'decimal', '5.1'),
    ('in-ep', '5.2 Infection and microbial contamination', [node('5', 1)], 'decimal', '5.2'),
    ('in-ep', '6.12.2', [node('6', 1), node('6.12', 2)], 'decimal', '6.12.2'),
    ('in-mdr', '22. Requirements for grant of manufacturing licence or loan licence', [], 'rule', '22'),
    ('epa-63o', '§ 63.360 Applicability.', [], 'section', '§63.360'),
])
def test_headings(doc, text, stack, kind, number):
    h = heading(bold_line(text), CONFIG[doc], stack)
    assert (h['kind'], h['number']) == (kind, number)


@pytest.mark.parametrize('doc,text,bold', [
    ('fda-iso10993-1', 'III. Risk Management ........ 5', True),
    ('in-ep', 'in accordance with Section 10.4.1 of ISO', True),
    ('fda-iso10993-1', 'Contains Nonbinding Recommendations', True),
    ('eu-mdr', 'L 117/12', True),
    ('eu-mdr', '(1) Council Directive 90/385/EEC', True),
    ('fda-iso10993-1', '1. Provide data', False),
    ('in-ep', '4.2 of ISO 14971 applies', True),
    ('epa-63o', '(c)(8) of this section applies', True),
])
def test_negatives(doc, text, bold):
    line = bold_line(text) if bold else Line(text)
    assert heading(line, CONFIG[doc], []) is None


def test_ambiguity():
    assert resolve_ambiguous('i', {'letter': 'h'}) == ('letter', False)
    assert resolve_ambiguous('i', {'letter': 'a'}) == ('roman', False)
    assert resolve_ambiguous('ii', {}) == (None, False)
    assert resolve_ambiguous('v', {}) == ('letter', True)
    h = node('§63.360(a)~2', 2, 'point')
    assert heading(Line('(i) First child'), CONFIG['epa-63o'], [node('§63.360', 1, 'section'), h])['depth'] == 99
    h = node('§63.360(h)', 2, 'point')
    assert heading(Line('(i) Letter successor'), CONFIG['epa-63o'], [node('§63.360', 1, 'section'), h])['number'] == '§63.360(i)'


def test_article_parent_is_not_a_dotted_prefix_mismatch(tmp_path):
    p = tmp_path / 'article.pdf'
    d = fitz.open(); page = d.new_page()
    for i, text in enumerate(['Article 1', 'Subject matter', '1. Devices shall comply with these requirements.']):
        page.insert_text((40, 50 + i*25), text)
    d.save(p)
    r = parse_document(p, 'eu-mdr', CONFIG['eu-mdr'])
    assert not any(w['code'] == 'PARENT_PREFIX_MISMATCH' for w in r['warnings'])
    assert r['clauses'][0]['title'] == 'Subject matter'


def test_r10_cleaning_and_flags():
    assert join_text(['ISO 10993-', '1 Biological evaluation'], set()) == 'ISO 10993-1 Biological evaluation'
    assert join_text(['bio-', 'compatibility'], {'biocompatibility'}) == 'biocompatibility'
    assert join_text(['pre-', 'market'], set()) == 'pre-market'
    assert clean('ﬁ\u00ad') == 'fi'
    assert is_bold({'font': 'ABCDEF+Arial-BoldMT', 'flags': 0})
    assert is_bold({'font': 'Arial', 'flags': 0, 'char_flags': 8})
    assert not is_bold({'font': 'Arial-Italic', 'flags': 2})
    assert TEXT_FLAGS & fitz.TEXT_COLLECT_STYLES
    assert not TEXT_FLAGS & (fitz.TEXT_DEHYPHENATE | fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_IMAGES)
    assert join_text(['Body text.', 'NOTE More.', 'EXAMPLE One.'], set()).splitlines() == ['Body text.', 'NOTE More.', 'EXAMPLE One.']
    assert heading(bold_line('5.1 New heading'), CONFIG['in-ep'], [], previous='see') is None


def test_header_guard():
    lines = []
    for p in range(1, 5):
        lines += [bold_line('III. Same heading', p), Line('Repeating document header', p, line_id=f'{p}:2'),
                  Line(f'Body unique {chr(64 + p)}', p, line_id=f'{p}:3')]
        lines[-3].line_id = f'{p}:1'
    filter_lines(lines, 4, CONFIG['fda-iso10993-1'])
    assert all(l.role != 'header_footer' for l in lines if l.text.startswith('III.'))
    assert all(l.role == 'header_footer' for l in lines if l.text.startswith('Repeating'))


def make_pdf(path):
    doc = fitz.open()
    for p in range(4):
        page = doc.new_page()
        page.insert_text((40, 20), 'Contains Nonbinding Recommendations', fontsize=10)
        if p == 0:
            for y, t in enumerate(['I. Introduction ........ 2', 'II. Background ........ 3', 'III. Details ........ 4'], 1):
                page.insert_text((40, 50 + 20*y), t, fontname='hebo')
        else:
            page.insert_text((40, 80), ['I. Introduction', 'II. Background', 'III. Details'][p-1], fontname='hebo')
            if p != 2:
                page.insert_text((40, 110), 'Own body text is longer than twenty-five characters and unique ' + str(p))
            page.insert_text((50, 150), 'A. Child section', fontname='hebo')
            page.insert_text((50, 180), 'Child body text with its own content for page ' + str(p))
            page.insert_text((40, 770), '1 This is a small-font footnote.', fontsize=8)
            if p == 3:
                for x in (40, 140, 240, 340):
                    page.draw_line((x, 220), (x, 310))
                for y in (220, 250, 280, 310):
                    page.draw_line((40, y), (340, y))
                for r in range(3):
                    for c in range(3):
                        page.insert_text((45+100*c, 240+30*r), f'Cell {r},{c}', fontsize=10)
    doc.save(path)


def test_synthetic_pdf(tmp_path):
    p = tmp_path / 'test.pdf'
    make_pdf(p)
    r = parse_document(p, 'fda-iso10993-1', CONFIG['fda-iso10993-1'])
    clauses = {c['number']: c for c in r['clauses']}
    assert 'I' in clauses and 'I.A' in clauses
    assert clauses['I.A']['parent'] == 'fda-iso10993-1:I'
    assert clauses['II.A']['parent'] is None  # heading-only II is dropped
    assert 'III.A.T1' in clauses and clauses['III.A.T1']['parent'] == 'fda-iso10993-1:III.A'
    assert 'small-font footnote' not in '\n'.join(c['text'] for c in r['clauses'])
    assert r['stats']['line_filters']['toc'] >= 3
    assert r['stats']['footnote_chars'] > 0
    assert 'header_only' in {d['reason'] for d in r['drops']}
    validate(r['clauses'], CONFIG)


def test_scanned_and_markers(tmp_path):
    p = tmp_path / 'scan.pdf'
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
    pix.clear_with(255)
    page.insert_image(page.rect, pixmap=pix)
    doc.save(p)
    r = parse_document(p, 'scan', CONFIG['who-decon'])
    assert r['ocr']['text_pages'] == 0 and r['clauses'] == []
    p = tmp_path / 'eu.pdf'
    doc = fitz.open(); page = doc.new_page()
    for i, t in enumerate(['(1) Recital before the body.', 'Article 1', 'Subject matter', '1. Devices shall comply with this regulation.', 'Article 11', '1. Excluded stop text.']):
        page.insert_text((40, 50 + i*25), t)
    doc.save(p)
    r = parse_document(p, 'eu-mdr', CONFIG['eu-mdr'], {'parts': [{'start_marker': 'Article 1', 'stop_marker': 'Article 11'}]})
    assert any(d['reason'] == 'outside_markers' for d in r['drops'])
    assert not any('Excluded' in c['text'] or 'Recital' in c['text'] for c in r['clauses'])


def test_cache_and_determinism(tmp_path):
    (tmp_path / 'data/pdfs').mkdir(parents=True)
    (tmp_path / 'ingest').mkdir(); (tmp_path / 'corpus').mkdir()
    configs = {k: copy.deepcopy(CONFIG[k]) for k in ['fda-iso10993-1', 'fda-nitinol']}
    for code in configs:
        make_pdf(tmp_path / f'data/pdfs/{code}.pdf')
    (tmp_path / 'ingest/sources.yaml').write_text(yaml.safe_dump(configs))
    (tmp_path / 'corpus/eu_mdr_slices.yaml').write_text('parts: []')
    (tmp_path / 'data/corpus_manifest.json').write_text(json.dumps({k: {'status': 'ok'} for k in configs}))
    run(tmp_path)
    first = (tmp_path / 'data/clauses.json').read_bytes()
    _, skipped = run(tmp_path)
    assert set(skipped) == set(configs)
    assert (tmp_path / 'data/clauses.json').read_bytes() == first
    configs['fda-nitinol']['max_depth'] = 2
    (tmp_path / 'ingest/sources.yaml').write_text(yaml.safe_dump(configs))
    _, skipped = run(tmp_path)
    assert skipped == ['fda-iso10993-1']
    scan = tmp_path / 'scan.pdf'; d = fitz.open(); d.new_page(); d.save(scan)
    before = (tmp_path / 'data/clauses.json').read_bytes()
    run(tmp_path, pdf=scan, as_code='scanned-test')
    assert (tmp_path / 'data/clauses.json').read_bytes() == before


@pytest.mark.parametrize('change', ['extra', 'duplicate', 'parent', 'page'])
def test_validator_rejects(change):
    c = dict(id='x:1', doc='x', number='1', title='', text='body', parent=None, source_pdf='x.pdf', page=1, informative=False)
    rows = [c]
    if change == 'extra': c['extra'] = 1
    if change == 'duplicate': rows.append(dict(c))
    if change == 'parent': c['parent'] = 'x:missing'
    if change == 'page': c['page'] = 0
    with pytest.raises(ValueError): validate(rows, {'x': {}})


def styled(text, size=12, bold=True, x=72, y=100, block=0):
    return Line(text, bbox=(x, y, x + 300, y + size), block=block,
                spans=[{'text': text, 'size': size, 'font': 'Arial-Bold' if bold else 'Arial',
                        'flags': 16 if bold else 0}])


def parse_lines(monkeypatch, doc, lines):
    for i, line in enumerate(lines):
        line.line_id = f'1:{i + 1}'
    monkeypatch.setattr('ingest.ingest.extract_lines', lambda *args: (lines, 1, []))
    return parse_document('sample.pdf', doc, CONFIG[doc])


@pytest.mark.parametrize('doc,first,second,size,second_bold', [
    ('in-ep', '2. Applicable Standards used to meet essential principles for safety and', 'performance', 14, True),
    ('in-ep', '5.3.1 Where a medical device incorporates, as an integral part, a substance which, if',
     'used separately, may be considered to be a medicinal product or drug as defined', 12, False),
    ('imdrf-n47', '5.0 Essential Principles Applicable to all Medical Devices and IVD Medical', 'Devices', 14, True),
])
def test_real_wrapped_titles(monkeypatch, doc, first, second, size, second_bold):
    r = parse_lines(monkeypatch, doc, [styled(first, size), styled(second, size, second_bold, y=120),
                    styled('Unique body paragraph with sufficient text.', 10, False, y=150)])
    c = r['clauses'][0]
    whole = first.split(' ', 1)[1] + ' ' + second
    if len(whole.split()) > 15 or whole.startswith('Where'):
        assert c['title'] == ''
        assert first + ' ' + second in c['text']
    else:
        assert c['title'] == whole
        assert second not in c['text']


def test_sentence_heading_is_body_with_ancestor_title(monkeypatch):
    r = parse_lines(monkeypatch, 'in-ep', [
        styled('5.3 Medicinal substances:', 14),
        styled('5.3.1 Where a medical device incorporates, as an integral part, a substance which, if'),
        styled('used separately, may be considered to be a medicinal product', y=120),
        styled('Unique body paragraph with sufficient text.', 10, False, y=150)])
    c = r['clauses'][0]
    assert c['title'] == 'Medicinal substances'
    assert 'substance which, if used separately' in c['text']


@pytest.mark.parametrize('text,number', [
    ('4. 1 Medical devices should be designed and manufactured in such a way that, when', '4.1'),
    ('5.1. 2 The devices should be designed, manufactured and packaged in such a way as', '5.1.2'),
    ('5. 10 Protection against mechanical risks:', '5.10'),
])
def test_real_spaced_decimal_numbers(text, number):
    assert heading(bold_line(text), CONFIG['in-ep'], [])['number'] == number


@pytest.mark.parametrize('doc,root,group,child,expected', [
    ('eu-mdr', 'Annex XV', 'CHAPTER II', '1. General requirements', 'Annex XV.II.1'),
    ('eu-mdr', 'Annex XIV', 'PART A', '1. Clinical evaluation', 'Annex XIV.A.1'),
    ('in-mdr', 'Schedule 4', 'PART III', '1. General Information:', 'Schedule 4.Part III.1'),
    ('in-mdr', 'Schedule 7', 'TABLE 4', '3. Preclinical testing', 'Schedule 7.Table 4.3'),
])
def test_s3_9_document_group_prefixes(doc, root, group, child, expected):
    stack = [node(root, 1, 'annex' if doc == 'eu-mdr' else 'schedule')]
    h = heading(bold_line(group), CONFIG[doc], stack)
    stack.append(node(h['number'], h['depth'], 'group'))
    assert heading(bold_line(child), CONFIG[doc], stack)['number'] == expected


def test_real_cross_references_and_deeper_roman_are_not_headings():
    assert heading(Line('5.1.4 below.'), CONFIG['imdrf-n47'], []) is None
    assert heading(styled('(v)', x=108.2), CONFIG['epa-63o'],
                   [node('§63.364', 1, 'section'), node('§63.364(h)', 2, 'point')])['depth'] == 99
    assert heading(styled('(e).', x=155.2), CONFIG['epa-63o'],
                   [node('§63.366', 1, 'section')])['depth'] == 99
    assert heading(styled('§ 63.362 must fulfill all reporting requirements in § 63.10(a)', x=83.25),
                   CONFIG['epa-63o'], []) is None


def test_real_body_lines_survive_header_filter():
    texts = [
        'the references of which have been published in the Official Journal of the European Union, shall be presumed to be in',
        'products, provided that references to those monographs have been published in the Official Journal of the European Union.',
    ]
    lines = [styled(t, bold=False, y=200 + i * 20) for i, t in enumerate(texts)]
    for i, l in enumerate(lines): l.line_id = f'1:{i}'
    filter_lines(lines, 1, CONFIG['eu-mdr'])
    assert all(l.role == 'continuation' for l in lines)
    links = []
    for p in range(1, 5):
        l = styled('(http://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfCFR/CFRSearch.cfm?FR=807.87), including the',
                   bold=False, y=500)
        l.page, l.line_id = p, f'{p}:1'
        links.append(l)
    filter_lines(links, 4, CONFIG['fda-knee'])
    assert all(l.role == 'continuation' for l in links)


def test_real_encoding_and_superscript_exponents():
    assert clean('\uf0b7 \uf0a7 „quotes‟ ‘single’') == '• • "quotes" \'single\''
    spans = [{'text': 'skin. FDA recommends a SAL of 10', 'flags': 4},
             {'text': '-3', 'flags': 5}, {'text': ' for devices', 'flags': 4},
             {'text': '11', 'flags': 5}]
    assert ''.join(s['text'] for s in body_spans(spans)) == 'skin. FDA recommends a SAL of 10-3 for devices'
    spans = [{'text': '25', 'size': 9, 'flags': 20},
             {'text': ' [87A. Registration certificate to sell, stock, exhibit or offer for sale or',
              'size': 14, 'flags': 20}]
    text = ''.join(s['text'] for s in body_spans(spans)).strip()
    assert heading(bold_line(text), CONFIG['in-mdr'], [])['number'] == '87A'


def test_real_eu_short_point_and_inline_reference(monkeypatch):
    r = parse_lines(monkeypatch, 'eu-mdr', [
        styled('Article 1', 14, x=72, y=50),
        styled('Subject matter', 14, y=70),
        styled('1. Devices shall comply with these requirements.', 12, False, y=100),
        styled('(a) the devices are not transferred to another legal entity,', 12, False, y=120),
        styled('defined in point', 12, False, x=339, y=150),
        styled('(1)(c)', 12, False, x=640, y=150),
    ])
    c = next(c for c in r['clauses'] if c['number'] == 'Art 1(1)(a)')
    assert '(a) the devices are not transferred to another legal entity,' in c['text']
    assert '(1)(c)' in c['text']
    assert not any(d['reason'] == 'id_collision' for d in r['drops'])


def test_coverage_does_not_exclude_unowned_body(monkeypatch):
    r = parse_lines(monkeypatch, 'in-ep', [styled('1. Introduction', 14),
                    styled('Unique body paragraph with sufficient text.', 10, False, y=150)])
    q = r['stats']['quality_report']['Q7']
    assert q['ratio'] == 1
    assert q['excluded_chars']['heading_title'] == len('1.Introduction')
    assert q['body_chars'] == len('Uniquebodyparagraphwithsufficienttext.')
    monkeypatch.setattr('ingest.ingest.join_text', lambda *args: '')
    r = parse_lines(monkeypatch, 'in-ep', [styled('1. Introduction', 14),
                    styled('Unique body paragraph with sufficient text.', 10, False, y=150)])
    q = r['stats']['quality_report']['Q7']
    assert q['ratio'] == 0 and q['warning']
    assert 'header_only' not in q['excluded_chars']


def test_ecfr_detached_section_label_does_not_steal_previous_clause(monkeypatch):
    r = parse_lines(monkeypatch, 'epa-63o', [
        styled('Subpart O—Ethylene Oxide Emissions Standards', y=20),
        styled('§ 63.365 Testing and monitoring requirements.', x=36, y=50),
        styled('Earlier body paragraph with sufficient text.', 10, False, y=80),
        styled('§ 63.366 Reporting requirements.', x=36, y=478),
        styled('monitoring plan is subject to the Administrator\'s approval.', 10, False, x=107, y=58),
        styled('(f)', 10, False, x=60.9, y=151),
        styled('Determination of compliance with PTE requirement.', 10, False, x=82, y=151),
        styled('(a)', 10, False, x=60, y=502),
        styled('General requirements. The owner or operator shall submit reports.', 10, False, x=82, y=502),
    ])
    numbers = {c['number'] for c in r['clauses']}
    assert '§63.365(f)' in numbers and '§63.366(a)' in numbers
    assert '§63.366(f)' not in numbers


def test_table_cell_is_not_a_wrapped_title(monkeypatch):
    r = parse_lines(monkeypatch, 'imdrf-n47', [
        styled('Appendix B', 14, y=50),
        styled('5.12', 10, False, x=72, y=573.87),
        styled('IEC 62366-1', 10, False, x=410.47, y=573.87),
        styled('IEC/TR 62366-2', 10, False, x=410.47, y=591.39),
    ])
    c = r['clauses'][0]
    assert c['number'] == 'Appendix B.5.12'
    assert c['text'] == 'IEC 62366-1 IEC/TR 62366-2'


def test_fda_number_and_adjacent_title_share_a_visual_line(monkeypatch):
    label = styled('I.', 18, y=449.18)
    label.bbox = (72, 449.18, 83.46, 467.18)
    r = parse_lines(monkeypatch, 'fda-iso10993-1', [label,
                    styled('Introduction', 18, x=108, y=449.18),
                    styled('FDA has developed this guidance document to assist industry.', 12, False, y=474.68)])
    assert r['clauses'][0]['title'] == 'Introduction'
    assert r['clauses'][0]['text'].startswith('FDA has developed')
