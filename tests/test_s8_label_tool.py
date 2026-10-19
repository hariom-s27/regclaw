"""S8 Part D labelling tool (s8_label_tool): fake sheets in tmp folders, local 127.0.0.1 server only."""
import csv
import json
from pathlib import Path
import threading
import urllib.request

import pytest

from retrieval_eval import s8_label_tool as t
from retrieval_eval.s8_judging import freeze_audit
from retrieval_eval.s8_labels import read_labels, read_sheet
from retrieval_eval.s8_runs import sheet_csv
from tests.test_s8_labels import label, make_sheet, write_labels

FORBIDDEN = {'grade', 'note', 'system', 'systems', 'rank', 'score', 'arm', 'variant', 'prelabel', 'query_asks',
             'clause_states', 'evidence_span'}
LONG = 34_099


@pytest.fixture
def sheet(tmp_path):
    rows = make_sheet(tmp_path / 'sheet.csv', {'Q10': 3, 'Q1': 2})
    rows[0]['text'] = ('A' * 99 + '\n') * (LONG // 100) + 'Z' * (LONG % 100)
    rows[1]['grade'], rows[1]['note'] = '3', 'LEAK'  # must never reach the page even if filled
    (tmp_path / 'sheet.csv').write_text(sheet_csv(rows), encoding='utf-8', newline='')
    return tmp_path / 'sheet.csv', rows


def payload(item_id, **kw):
    return dict(item_id=item_id, grade='2', clause_states='partial', jurisdiction_match='not_applicable',
                evidence_span='span', unsure='no', note='') | kw


def test_audit_mode_shows_only_blind_fields_and_full_long_text(tmp_path, sheet):
    path, rows = sheet
    app = t.LabelApp(path, tmp_path / 'labels.csv')
    first = app.item(0)
    assert len(first['item']['text']) == LONG and first['item']['text'] == rows[0]['text']
    assert first['item']['common_rules'] == 'RULES'
    for i in range(len(rows)):
        got = app.item(i)
        assert not FORBIDDEN & set(got['item']) and 'judges' not in got
        assert 'LEAK' not in json.dumps(got)
    assert app.state() == dict(mode='audit', total=5, done=0, first_unlabelled=0, labels_file='labels.csv')


def test_save_autosaves_validates_and_resumes(tmp_path, sheet):
    path, rows = sheet
    labels = tmp_path / 'labels.csv'
    app = t.LabelApp(path, labels)
    with pytest.raises(ValueError):
        app.save(payload(rows[0]['item_id'], unsure='Yes'))
    with pytest.raises(ValueError):
        app.save(payload(rows[0]['item_id'], grade='5'))
    with pytest.raises(ValueError):
        app.save(payload('S8-unknown'))
    assert not labels.exists()
    app.save(payload(rows[0]['item_id'], unsure='yes', note='hard one'))
    app.save(payload(rows[2]['item_id'], grade='0', clause_states='unrelated'))
    with labels.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        assert tuple(reader.fieldnames) == t.TOOL_FIELDS
        saved = list(reader)
    assert [r['item_id'] for r in saved] == [rows[0]['item_id'], rows[2]['item_id']]  # sheet order
    assert saved[0]['unsure'] == 'yes' and saved[0]['timestamp']
    again = t.LabelApp(path, labels)  # resume
    assert again.state()['done'] == 2 and again.state()['first_unlabelled'] == 1
    assert again.item(0)['label']['note'] == 'hard one'
    again.save(payload(rows[0]['item_id'], grade='3'))  # relabel replaces, never duplicates
    assert len(read_labels([labels], 'x')) == 2 and read_labels([labels], 'x')[rows[0]['item_id']]['grade'] == '3'


def test_export_writes_validated_copy(tmp_path, sheet):
    path, rows = sheet
    app = t.LabelApp(path, tmp_path / 'labels.csv')
    for r in rows:
        app.save(payload(r['item_id']))
    assert app.export(tmp_path / 'export.csv') == dict(exported=5, complete=True)
    assert set(read_labels([tmp_path / 'export.csv'], 'x')) == {r['item_id'] for r in rows}
    with pytest.raises(ValueError):
        app.export(tmp_path / 'labels.csv')


def test_labels_file_from_another_sheet_is_refused(tmp_path, sheet):
    path, _ = sheet
    write_labels(tmp_path / 'labels.csv', [label('S8-other', 1)])
    with pytest.raises(ValueError, match='not in this sheet'):
        t.LabelApp(path, tmp_path / 'labels.csv')


def test_frozen_audit_labels_cannot_be_reopened(tmp_path, sheet):
    path, rows = sheet
    audit = tmp_path / 'audit'
    audit.mkdir()
    (audit / 'audit_sheet.csv').write_bytes(path.read_bytes())
    (audit / 'audit_ids.txt').write_text(''.join(r['item_id'] + '\n' for r in rows), encoding='utf-8')
    labels = write_labels(audit / 'labels_shyam_audit.csv', [label(r['item_id'], 1) for r in rows])
    freeze_audit(labels, audit)
    with pytest.raises(PermissionError, match='frozen'):
        t.LabelApp(audit / 'audit_sheet.csv', labels, freeze=audit / 'audit_freeze.json')


def write_disputes(tmp_path, rows):
    from retrieval_eval.s8_labels import DISPUTE_COLUMNS, csv_text
    d = []
    for r in rows:
        d.append({c: '' for c in DISPUTE_COLUMNS} | dict(item_id=r['item_id'], reason='threshold_split', qid=r['qid'],
                                                          a_grade='1', b_grade='2', a_note='A-note',
                                                          b_evidence_span='B-span', a_unsure='no', b_unsure='yes'))
    (tmp_path / 'disputes.csv').write_text(csv_text(d, DISPUTE_COLUMNS), encoding='utf-8', newline='')
    return tmp_path / 'disputes.csv'


def test_referee_mode_needs_freeze_and_shows_both_judges(tmp_path, sheet):
    path, rows = sheet
    disputes = write_disputes(tmp_path, rows)
    with pytest.raises(PermissionError, match='freeze'):
        t.LabelApp(path, tmp_path / 'ref.csv', 'referee', disputes, tmp_path / 'audit_freeze.json')
    with pytest.raises(PermissionError, match='freeze'):
        t.LabelApp(path, tmp_path / 'ref.csv', 'referee', disputes, None)
    (tmp_path / 'audit_freeze.json').write_text(json.dumps(dict(labels_file='labels_shyam_audit.csv')))
    app = t.LabelApp(path, tmp_path / 'ref.csv', 'referee', disputes, tmp_path / 'audit_freeze.json')
    got = app.item(0)
    assert got['reason'] == 'threshold_split' and got['mode'] == 'referee'
    assert got['judges']['a']['grade'] == '1' and got['judges']['b']['grade'] == '2'
    assert got['judges']['a']['note'] == 'A-note' and got['judges']['b']['evidence_span'] == 'B-span'
    assert set(got['judges']['a']) == {'query_asks', 'clause_states', 'jurisdiction_match', 'evidence_span',
                                       'grade', 'unsure', 'note'}
    write_disputes(tmp_path, rows[:-1])
    with pytest.raises(ValueError, match='different items'):
        t.LabelApp(path, tmp_path / 'ref2.csv', 'referee', tmp_path / 'disputes.csv', tmp_path / 'audit_freeze.json')


def test_page_is_self_contained():
    page = t.PAGE.lower()
    assert '://' not in page and '<link' not in page and 'src=' not in page and '@import' not in page
    for key in ("'0'", "'3'", "'u'", 'enter', 'arrowleft', 'arrowright', '/api/export', '/api/save'):
        assert key in page


def test_http_round_trip_on_localhost(tmp_path, sheet):
    path, rows = sheet
    app = t.LabelApp(path, tmp_path / 'labels.csv')
    server = t.ThreadingHTTPServer(('127.0.0.1', 0), t.make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        assert b'S8 labelling' in urllib.request.urlopen(base + '/').read()
        item = json.loads(urllib.request.urlopen(base + '/api/item?i=0').read())
        assert len(item['item']['text']) == LONG
        req = urllib.request.Request(base + '/api/save', data=json.dumps(payload(rows[0]['item_id'])).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
        assert json.loads(urllib.request.urlopen(req).read())['done'] == 1
        bad = urllib.request.Request(base + '/api/save', data=json.dumps(payload(rows[0]['item_id'], unsure='1')).encode(),
                                     method='POST')
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(bad)
        assert err.value.code == 400
        exported = urllib.request.urlopen(base + '/api/export').read().decode('utf-8-sig')
        assert rows[0]['item_id'] in exported
    finally:
        server.shutdown()
        server.server_close()
