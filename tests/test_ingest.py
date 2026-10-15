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
    first_notes = (tmp_path / 'data/footnotes.json').read_bytes()
    _, skipped = run(tmp_path)
    assert set(skipped) == set(configs)
    assert (tmp_path / 'data/clauses.json').read_bytes() == first
    assert (tmp_path / 'data/footnotes.json').read_bytes() == first_notes
    configs['fda-nitinol']['max_depth'] = 2
    (tmp_path / 'ingest/sources.yaml').write_text(yaml.safe_dump(configs))
    _, skipped = run(tmp_path)
    assert skipped == ['fda-iso10993-1']
    assert {n['clause_id'].split(':')[0] for n in json.loads(
        (tmp_path / 'data/footnotes.json').read_text())} == set(configs)
    run(tmp_path, doc='fda-nitinol')
    assert (tmp_path / 'data/footnotes.json').read_bytes() == first_notes
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


def test_footnotes_link_by_superscript_and_last_body_line(monkeypatch):
    body = styled('The first clause has a citation and sufficient body text to establish the document body font size.', bold=False, y=150, block=1)
    body.spans += [dict(text='31', size=8, flags=1)]
    body.text += '31'
    r = parse_lines(monkeypatch, 'fda-nitinol', [
        styled('I. First section', y=100), body,
        styled('II. Second section', y=200, block=2),
        styled('The second clause owns the final body line and retains its complete original regulatory requirements.', bold=False, y=230, block=3),
        styled('31 ISO 10993-4:2017 Biological evaluation.', 8, False, y=650, block=4),
        styled('30 day continuation of the first footnote.', 8, False, y=663, block=4),
        styled('32 ISO 18562-3 Another reference.', 8, False, y=680, block=5),
    ])
    assert r['stats']['footnotes_linked'] == dict(marker=1, page_fallback=1, unlinked=0)
    first, fallback = r['footnotes']
    assert first['clause_id'] == 'fda-nitinol:I' and first['marker'] == '31'
    assert first['page'] == 1 and '30 day continuation' in first['text']
    assert fallback['clause_id'] == 'fda-nitinol:II' and fallback['marker'] is None
    assert all('ISO' not in c['text'] and 'citation.31' not in c['text'] for c in r['clauses'])
    assert r['stats']['quality_report']['Q7']['ratio'] == 1
    validate(r['clauses'], CONFIG)


def test_footnote_fallback_uses_same_page_not_clause_start(monkeypatch):
    lines = [styled('I. Heading', y=100),
             styled('This body starts on the first page.', bold=False, y=140, block=1),
             styled('This body continues on the second page.', bold=False, y=140, block=2),
             styled('1 ISO 10993-10:2021 reference.', 8, False, y=680, block=3)]
    for i, l in enumerate(lines):
        l.page = 1 if i < 2 else 2
        l.line_id = f'{l.page}:{i}'
    monkeypatch.setattr('ingest.ingest.extract_lines', lambda *args: (lines, 2, []))
    r = parse_document('sample.pdf', 'fda-nitinol', CONFIG['fda-nitinol'])
    assert r['footnotes'][0]['clause_id'] == 'fda-nitinol:I'
    assert r['footnotes'][0]['page'] == 2 and r['footnotes'][0]['marker'] is None


def test_small_appendix_table_rows_are_not_footnotes(monkeypatch):
    r = parse_lines(monkeypatch, 'imdrf-n47', [
        styled('Appendix B', 14, y=50),
        styled('Guidance on Essential Principles', 14, y=70),
        styled('The table below provides guidance on meeting the essential principles of safety and performance. '
               'The listed standards do not encompass every requirement for each essential principle.',
               12, False, y=100, block=4),
        styled('5.12', 10, False, y=550, block=1),
        styled('IEC 62366-1 IEC/TR 62366-2', 10, False, x=410, y=550, block=1),
        styled('5.13', 10, False, y=650, block=2),
        styled('ISO 22442', 10, False, x=410, y=650, block=2),
        styled('6.1', 10, False, y=680, block=3),
        styled('ISO 10993', 10, False, x=410, y=680, block=3),
    ])
    assert not r['footnotes']
    cs = {c['number']: c for c in r['clauses']}
    assert cs['Appendix B.5.13']['text'] == 'ISO 22442'
    assert cs['Appendix B.6.1']['text'] == 'ISO 10993'


def test_styled_footnote_marker_without_space_and_numeric_continuation(monkeypatch):
    body = styled('The guidance body refers to the available scientific evidence and its citation in this footnote.',
                  12, False, y=150, block=1)
    body.spans.append(dict(text='41', size=8, flags=1))
    body.text += '41'
    note = styled('41Available at the reference library.', 8, False, y=650, block=2)
    note.spans = [dict(text='41', size=5, flags=0), dict(text='Available at the reference library.', size=8, flags=0)]
    r = parse_lines(monkeypatch, 'fda-nitinol', [styled('I. Introduction', y=100), body, note,
        styled('2012 Sep 4;84(17):7274-82.', 8, False, y=665, block=2)])
    assert len(r['footnotes']) == 1
    assert r['footnotes'][0]['marker'] == '41'
    assert r['footnotes'][0]['text'] == 'Available at the reference library. 2012 Sep 4;84(17):7274-82.'


def test_epa_appendix_real_heading_depth_tables_and_detached_labels(monkeypatch):
    assert heading(styled('Appendix A to Subpart O of Part 63'), CONFIG['epa-63o'], []) is None
    r = parse_lines(monkeypatch, 'epa-63o', [
        styled('Subpart O—Ethylene Oxide Emissions Standards', y=20),
        styled('§ 63.368 Implementation.', x=36, y=50),
        styled('(c) The final regulatory paragraph shall remain here.', x=60, y=80),
        styled('Appendix A to Subpart O of Part 63—Monitoring Provisions for EtO CEMS', x=36, y=120),
        styled('1. Applicability', 13.5, False, x=36, y=150),
        styled('2. Monitoring of EtO Emissions', 13.5, False, x=36, y=240),
        styled('These provisions apply to the following facilities.', bold=False, y=180),
        styled('This is the monitoring section body.', bold=False, y=270),
        styled('11.5.3 Each electronic quarterly report shall include:', bold=False, y=300),
        styled('11.5.3.1 The date of report generation;', bold=False, y=325),
        styled('Field | Value | Unit', bold=False, y=350),
        styled('EtO | 10 | lb/hr', bold=False, y=375),
        styled('Refer to the requirements in §', bold=False, y=400),
        styled('75.20(b)(3)(iv) of this chapter.', bold=False, y=425),
        styled('11.5.4 Compliance certification shall be submitted.', bold=False, y=450),
    ])
    cs = {c['number']: c for c in r['clauses']}
    assert 'Appendix' not in cs['§63.368(c)']['text']
    assert cs['Appendix A.1']['text'].startswith('These provisions')
    assert cs['Appendix A.2']['text'].startswith('This is the monitoring')
    c = cs['Appendix A.11.5.3']
    assert '11.5.3.1' in c['text'] and 'EtO | 10 | lb/hr' in c['text']
    assert '75.20(b)(3)(iv)' in c['text']
    assert 'Appendix A.11.5.3.1' not in cs and 'Appendix A.75' not in cs
    assert r['stats']['quality_report']['Q7']['ratio'] == 1
    validate(r['clauses'], CONFIG)


def test_india_annexure_forms_and_editorial_notes_everywhere(monkeypatch):
    r = parse_lines(monkeypatch, 'in-mdr', [
        styled('22. Requirements for licence', 14, y=50),
        styled('The applicant shall provide the following records.', 14, False, y=80, block=1),
        styled('1. Inserted vide GSR 30(E) dt. 15-1-2019.', 10, False, y=110, block=2),
        styled('EIGHTH SCHEDULE', 14, y=160, block=3),
        styled('9. Import of medical devices', 14, y=180, block=4),
        styled('All provisions apply to the devices specified here.', 14, False, y=210, block=5),
        styled('ANNEXURE', 14, y=240, block=6),
        styled('(See rule 19A)', 14, False, y=260, block=7),
        styled('1. Disposable hypodermic syringes', 14, y=280, block=8),
        styled('FORM MD-1', 14, y=310, block=9),
        styled('1. Name of applicant', 14, y=340, block=10),
        styled('ANNEXURE', 14, y=360, block=11),
        styled('2. Manufacturing site', 14, y=380, block=12),
        styled('Form MD-2', 14, y=400, block=13),
        styled('The certificate fields remain in this form.', 14, False, y=430, block=14),
        styled('REFERENCES', 14, y=470, block=15),
        styled('51.', 10, False, y=500, block=16),
        styled('Substituted for "former wording" vide GSR 30(E).', 10, False, y=500, block=16),
        styled('Prior to substitution, the rule read as follows:', 10, False, y=520, block=17),
        styled('The former rule shall not be retained.', 10, False, y=540, block=18),
        styled('52. Inserted, ibid.', 10, False, y=560, block=19),
    ])
    cs = {c['number']: c for c in r['clauses']}
    assert r['stats']['amendment_notes'] == 3
    assert cs['Form MD-1']['parent'] is None
    assert '1. Name' in cs['Form MD-1']['text'] and '2. Manufacturing' in cs['Form MD-1']['text']
    assert 'ANNEXURE' in cs['Form MD-1']['text']  # form's own attachment stays inside
    assert 'See rule 19A' in cs['Annexure (rule 19A)']['text']
    assert 'FORM MD' not in cs['Schedule 8.9']['text']
    assert not any('GSR' in c['text'] or 'former rule' in c['text'] or 'REFERENCES' in c['text'] for c in cs.values())
    assert r['stats']['quality_report']['Q7']['ratio'] == 1
    validate(r['clauses'], CONFIG)


def test_india_form_with_editorial_superscript_prefix():
    spans = [dict(text='65', size=9, flags=20), dict(text='[Form MD-41', size=14, flags=20)]
    text = ''.join(s['text'] for s in body_spans(spans))
    assert heading(bold_line(text), CONFIG['in-mdr'], [])['number'] == 'Form MD-41'


def test_unnumbered_front_matter_only_before_numbered_headings(monkeypatch):
    r = parse_lines(monkeypatch, 'imdrf-n47', [
        styled('Preface', 14, y=50),
        styled('The document was produced by the forum.', 12, False, y=80, block=1),
        styled('Introduction', 14, y=120, block=2),
        styled('A course of action is preferred but not necessarily required.', 12, False, y=150, block=3),
        styled('1.0 Scope', 14, y=200, block=4),
        styled('The scope covers medical devices and their manufacture.', 12, False, y=230, block=5),
        styled('Introduction', 14, y=270, block=6),
        styled('This later word does not open another clause.', 12, False, y=300, block=7),
    ])
    cs = {c['number']: c for c in r['clauses']}
    assert set(cs) == {'Introduction', '1'}
    assert any(d['number'] == 'Preface' and d['reason'] == 'skipped_section' for d in r['drops'])
    assert 'preferred but not necessarily required' in cs['Introduction']['text']
    assert cs['Introduction']['title'] == 'Introduction' and cs['1']['parent'] is None
    assert 'Introduction' in cs['1']['text']
    r = parse_lines(monkeypatch, 'imdrf-n47', [
        styled('Introduction', 12, False, y=50),  # ordinary body, no heading style
        styled('1.0 Scope', 14, y=100, block=1),
        styled('The scope covers medical devices.', 12, False, y=130, block=2),
    ])
    assert [c['number'] for c in r['clauses']] == ['1']


def test_eu_real_definition_wrap_updates_previous(monkeypatch):
    # eu-mdr.pdf p4, lines 25–27. The first line ends in "of";
    # the consumed continuation ends in ";" before the next definition.
    r = parse_lines(monkeypatch, 'eu-mdr', [
        styled('Article 2', y=50), styled('Definitions', y=70),
        styled("(7) 'generic device group' means a set of devices having the same or similar intended purposes or a commonality of",
               bold=False, x=72.57, y=477.58),
        styled('technology allowing them to be classified in a generic manner not reflecting specific characteristics;',
               bold=False, x=87.53, y=488.13),
        styled("(8) 'single-use device' means a device that is intended to be used on one individual during a single procedure;",
               bold=False, x=72.57, y=512.16),
    ])
    cs = {c['number']: c for c in r['clauses']}
    assert set(cs) == {'Art 2(7)', 'Art 2(8)'}
    assert cs['Art 2(7)']['text'].endswith('specific characteristics;')
    assert "'single-use device'" not in cs['Art 2(7)']['text']
    assert cs['Art 2(8)']['text'].startswith("(8) 'single-use device'")
    assert not r['warnings']
    assert r['stats']['quality_report']['Q7']['ratio'] == 1


def test_eu_real_article_5_letter_return_after_capped_romans(monkeypatch):
    # eu-mdr.pdf p9, lines 18–38, with the physical Roman/letter indents.
    label = styled('5.', bold=False, x=67.35, y=322.41)
    label.bbox = (67.35, 322.41, 76.69, 332.41)
    r = parse_lines(monkeypatch, 'eu-mdr', [
        styled('Article 5', y=50),
        styled('Placing on the market and putting into service', y=70), label,
        styled('With the exception of the relevant general safety and performance requirements set out in Annex I, the',
               bold=False, x=88.95, y=322.41),
        styled('requirements of this Regulation shall not apply to devices, manufactured and used only within health institutions',
               bold=False, x=67.35, y=333.02),
        styled('established in the Union, provided that all of the following conditions are met:',
               bold=False, x=67.35, y=343.56),
        styled('(a)  the devices are not transferred to another legal entity,', bold=False, x=67.35, y=363.91),
        styled('(b)  manufacture and use of the devices occur under appropriate quality management systems,',
               bold=False, x=67.35, y=384.27),
        styled("(c)  the health institution justifies in its documentation that the target patient group's specific needs cannot be met, or",
               bold=False, x=67.35, y=404.62),
        styled('cannot be met at the appropriate level of performance by an equivalent device available on the market,',
               bold=False, x=82.26, y=415.16),
        styled('(d)  the health institution provides information upon request on the use of such devices to its competent authority,',
               bold=False, x=67.35, y=435.52),
        styled('which shall include a justification of their manufacturing, modification and use;',
               bold=False, x=82.26, y=446.06),
        styled('(e)  the health institution draws up a declaration which it shall make publicly available, including:',
               bold=False, x=67.35, y=466.41),
        styled('(i)  the name and address of the manufacturing health institution;', bold=False, x=82.26, y=486.77),
        styled('(ii)  the details necessary to identify the devices;', bold=False, x=82.26, y=507.12),
        styled('(iii)  a declaration that the devices meet the general safety and performance requirements set out in Annex I to this',
               bold=False, x=82.26, y=527.47),
        styled('Regulation and, where applicable, information on which requirements are not fully met with a reasoned justifi',
               bold=False, x=99.27, y=538.02),
        styled('cation therefor,', bold=False, x=99.27, y=548.56),
        styled('(f)  the health institution draws up documentation that makes it possible to have an understanding of the manufacturing',
               bold=False, x=67.35, y=568.91),
        styled('facility, the manufacturing process, the design and performance data of the devices, including the intended purpose,',
               bold=False, x=82.26, y=579.46),
        styled('and that is sufficiently detailed to enable the competent authority to ascertain that the general safety and',
               bold=False, x=82.26, y=590.06),
        styled('performance requirements set out in Annex I to this Regulation are met;', bold=False, x=82.26, y=600.61),
    ])
    cs = {c['number']: c for c in r['clauses']}
    assert set(cs) == {'Art 5(5)'} | {'Art 5(5)(' + t + ')' for t in 'abcdef'}
    assert all('(' + t + ')' in cs['Art 5(5)(e)']['text'] for t in ('i', 'ii', 'iii'))
    assert '(i)' not in cs['Art 5(5)(f)']['text']
    assert all(cs['Art 5(5)(' + t + ')']['parent'] == 'eu-mdr:Art 5(5)' for t in 'abcdef')
    assert r['stats']['depth_merges'] == 3
    assert not r['warnings']
    assert r['stats']['quality_report']['Q7']['ratio'] == 1


def test_eu_real_article_2_roman_v_and_short_points(monkeypatch):
    # eu-mdr.pdf p7, lines 23–32. Short (a) and nested (v) are real text.
    r = parse_lines(monkeypatch, 'eu-mdr', [
        styled('Article 2', y=50), styled('Definitions', y=70),
        styled("(58) 'serious adverse event' means any adverse event that led to any of the following:",
               bold=False, x=67.46, y=402.52),
        styled('(a)  death,', bold=False, x=87.53, y=426.56),
        styled('(b)  serious deterioration in the health of the subject, that resulted in any of the following:',
               bold=False, x=87.53, y=450.60),
        styled('(i)  life-threatening illness or injury,', bold=False, x=102.27, y=474.63),
        styled('(ii)  permanent impairment of a body structure or a body function,', bold=False, x=102.27, y=498.67),
        styled('(iii)  hospitalisation or prolongation of patient hospitalisation,', bold=False, x=102.27, y=522.77),
        styled('(iv)  medical or surgical intervention to prevent life-threatening illness or injury or permanent impairment to',
               bold=False, x=102.27, y=546.80),
        styled('a body structure or a body function,', bold=False, x=119.28, y=557.35),
        styled('(v)  chronic disease,', bold=False, x=102.27, y=581.39),
        styled('(c)  foetal distress, foetal death or a congenital physical or mental impairment or birth defect;',
               bold=False, x=87.53, y=605.42),
    ])
    cs = {c['number']: c for c in r['clauses']}
    assert set(cs) == {'Art 2(58)', 'Art 2(58)(a)', 'Art 2(58)(b)', 'Art 2(58)(c)'}
    assert cs['Art 2(58)(a)']['text'] == '(a) death,'
    assert cs['Art 2(58)(b)']['text'].endswith('(v) chronic disease,')
    assert r['stats']['depth_merges'] == 5 and not r['warnings']
    assert r['stats']['quality_report']['Q7']['ratio'] == 1


def test_eu_ambiguous_successor_precedes_indent_and_first_child():
    stack = [node('Art 5', 1, 'article'), node('Art 5(5)', 2, 'decimal'),
             dict(node('Art 5(5)(h)', 3, 'point'), _x0=67.35)]
    assert heading(styled('(i) Successor', x=82.26), CONFIG['eu-mdr'], stack)['number'] == 'Art 5(5)(i)'
    stack[-1] = dict(node('Art 5(5)(b)', 3, 'point'), _x0=67.35)
    assert heading(styled('(ii) Cannot open here', x=82.26), CONFIG['eu-mdr'], stack) is None
    h = heading(styled('(i) First Roman child', x=82.26), CONFIG['eu-mdr'], stack)
    assert (h['number'], h['kind'], h['depth']) == ('Art 5(5)(b)(i)', 'roman_point', 4)


def test_epa_named_tables_are_separate_and_keep_rows_and_citations(monkeypatch):
    assert heading(styled('Table 1 to Subpart O of Part 63'), CONFIG['epa-63o'], []) is None
    r = parse_lines(monkeypatch, 'epa-63o', [
        styled('Subpart O—Ethylene Oxide Emissions Standards', y=20),
        styled('§ 63.368 Implementation.', x=36.75, y=50),
        styled('(c)', bold=False, x=60.93, y=80),
        styled('The authorities that cannot be delegated to State, local, or Tribal agencies are as specified in paragraphs',
               bold=False, x=82.08, y=100),
        styled('(c)(1) through (5) of this section.', bold=False, x=82.08, y=120),
        styled('(1) Approval of alternatives to the requirements in § 63.362 and § 63.363.', bold=False, x=84, y=140),
        styled('Table 1 to Subpart O of Part 63—Standards for SCVs', x=36.75, y=160),
        styled('For which . . .', bold=False, y=180),
        styled('1. Existing SCV', bold=False, x=36.75, y=200),
        styled('(a) A table row remains inside the named table.', bold=False, x=60.93, y=220),
        styled('§ 63.365.', bold=False, x=36.75, y=240),
        styled('Continuously reduce EtO emissions by 99 percent.', bold=False, y=260),
        styled('Table 2 to Subpart O of Part 63—Standards for ARVs', x=36.75, y=280),
        styled('Continuously reduce EtO emissions by 99.9 percent.', bold=False, y=300),
        styled('Appendix A to Subpart O of Part 63—Monitoring Provisions for EtO CEMS', x=36.75, y=340),
        styled('1. Applicability', x=36.75, y=360),
        styled('These provisions apply to the following facilities.', bold=False, y=380),
    ])
    cs = {c['number']: c for c in r['clauses']}
    assert set(cs) == {'§63.368(c)', 'Table 1', 'Table 2', 'Appendix A.1'}
    assert all(cs[t]['parent'] is None for t in ('Table 1', 'Table 2'))
    assert '(1) Approval' in cs['§63.368(c)']['text']
    assert 'Table' not in cs['§63.368(c)']['text']
    assert all(t in cs['Table 1']['text'] for t in ('1. Existing SCV', '(a) A table row', '§ 63.365.'))
    assert '99.9 percent' not in cs['Table 1']['text']
    assert cs['Table 2']['title'] == 'Standards for ARVs'
    assert r['stats']['quality_report']['Q7']['ratio'] == 1
    validate(r['clauses'], CONFIG)


@pytest.mark.parametrize('doc', ['fda-iso10993-1', 'fda-sterility-510k', 'fda-chem-analysis',
                                 'fda-nitinol', 'fda-knee', 'imdrf-n47'])
def test_boilerplate_preface_is_logged_as_skipped_section(monkeypatch, doc):
    text = '1. Introduction' if doc == 'fda-knee' else '1.0 Scope' if doc == 'imdrf-n47' else 'I. Introduction'
    body_heading = styled(text, 14, y=150, block=2)
    if doc == 'fda-knee':
        body_heading.spans[0]['font'] = 'Merriweather-Bold'
    r = parse_lines(monkeypatch, doc, [
        styled('Preface', 14, y=50),
        styled('Public Comment: submit comments and request additional copies.', bold=False, y=80, block=1),
        body_heading,
        styled('The guidance provides regulatory recommendations for medical devices.', bold=False, y=180, block=3),
    ])
    assert len(r['clauses']) == 1 and r['clauses'][0]['number'] != 'Preface'
    assert not any('Public Comment' in c['text'] for c in r['clauses'])
    assert any(d['number'] == 'Preface' and d['reason'] == 'skipped_section' for d in r['drops'])
    assert r['stats']['quality_report']['Q7']['ratio'] == 1


def test_india_ep_regulatory_preface_is_retained(monkeypatch):
    r = parse_lines(monkeypatch, 'in-ep', [
        styled('Preface', 14, y=50),
        styled('This document is prepared under the provisions of rule 6 of Medical Devices', bold=False, y=80),
        styled('Rules, 2017 to provide binding guidance for use in the manufacturing of', bold=False, y=100),
        styled('medical devices which are intended to be sold in India. This document provides', bold=False, y=120),
        styled('1. Introduction', 14, y=180, block=1),
        styled('The essential principles apply to medical devices and their manufacture.', bold=False, y=210, block=2),
    ])
    assert r['clauses'][0]['number'] == 'Preface'
    assert 'rule 6' in r['clauses'][0]['text'] and 'binding guidance' in r['clauses'][0]['text']
    assert not any(d['reason'] == 'skipped_section' for d in r['drops'])
