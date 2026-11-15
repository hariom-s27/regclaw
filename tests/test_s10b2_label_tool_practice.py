"""S10b2 label tool: practice and review-practice modes, on a FAKE repo root only."""
import csv
import io
import json
import random
import threading
import urllib.error
import urllib.request

import pytest
import yaml

from eval.s10 import judge_kit as JK
from eval.s10 import label_tool as LT
from standards.ruleset import normalised_sha
from tests.test_s10b2_judge_kit import PARTS, fake_entry, put, setup as kit_setup

YN = ['eo_sterilised', 'sold_sterile', 'implant', 'blood_lymph_or_csf_contact', 'designed_to_degrade',
      'may_degrade_or_corrode', 'novel_material', 'nanomaterial', 'reproductive_concern_stated',
      'special_population_stated', 'knee_prosthesis']
FORBIDDEN = ('flags', 'existing_evidence', 'derived', 'split', 'attachment_g_material', 'equivalent_marketed_device')


def make_root(tmp_path, kit=True):
    root = tmp_path / 'root'
    probes = kit_setup(root)
    if kit:
        assert JK.run(root, 'practice', probes_sha=probes, now='t', out=lambda s: None) == 0
    return root


def label_all(root, n=80, label_for=lambda d, it: 'recommended', reason='H-REASON'):
    app = LT.PracticeApp(root)
    for i in range(n):
        d, it = app.keys[i]
        app.save(dict(index=i, device_id=d, item=it, label=label_for(d, it), confidence='low', reason=reason))
    return app


def result_for(judge, device_id, label_for=lambda j, d, it: 'recommended', checklist=None):
    ck = dict(components=[dict(name='p', contact_category='c', duration='A', table_row='r')],
              **{k: 'no' for k in YN}, material_classes=['polymer'])
    ck.update(checklist or {})
    return dict(device_id=device_id, checklist=ck,
                cells=[dict(item=it, reason=f'{judge}-REASON-{it}', label=label_for(judge, device_id, it),
                            confidence='medium', section='§4.1', needs_sme='') for it in PARTS])


def write_results(root, devices, judges=('A', 'B', 'C'), **kw):
    for j in judges:
        for d in devices:
            p = root / LT.JUDGES_PRACTICE_REL / j / f'{d}.result.json'
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(result_for(j, d, **{k: v for k, v in kw.items() if k != 'checklists'},
                                               checklist=(kw.get('checklists') or {}).get((j, d)))),
                         encoding='utf-8')


def test_practice_cells_and_order(tmp_path):
    app = LT.PracticeApp(make_root(tmp_path))
    assert len(app.keys) == 80 and app.state()['mode'] == 'practice' and app.state()['upto'] is None
    assert [k[0] for k in app.keys] == [d for d in LT.PRACTICE_IDS for _ in range(20)]
    for d in LT.PRACTICE_IDS:
        expect = list(PARTS)
        random.Random(f'S10b-v1|hariom-practice|{d}').shuffle(expect)
        assert [it for dd, it in app.keys if dd == d] == expect
    assert [c['order'] for c in app.cells] == list(range(1, 81))
    assert app.codebook_name == 'CODEBOOK_v0.1.md'


def test_practice_item_is_blind(tmp_path):
    app = label_all(make_root(tmp_path), n=20)           # PR-2 carries existing_evidence + flags in the fake file
    got = app.item(20)
    text = json.dumps(got)
    assert got['cell']['device_id'] == 'PR-2' and got['id_field'] == 'device_id'
    assert sorted(got['profile']) == ['contact_category', 'duration', 'profile']
    for word in FORBIDDEN:
        assert word not in text
    assert 'j_id' not in got['cell']


@pytest.mark.parametrize('what', ['practice', 'codebook'])
def test_practice_sha_refused(tmp_path, what):
    root = make_root(tmp_path, kit=False)
    rel = JK.PRACTICE_REL if what == 'practice' else JK.CODEBOOK_V01_REL
    p = root / rel
    p.write_text(p.read_text(encoding='utf-8') + '\n# changed\n', encoding='utf-8')
    with pytest.raises(PermissionError, match='s10b1_build.json'):
        LT.PracticeApp(root)


def test_practice_wrong_ids_refused(tmp_path):
    root = make_root(tmp_path, kit=False)
    sha = put(root, JK.PRACTICE_REL, yaml.safe_dump(dict(format='s10-practice-profiles.v1', split='practice',
                                                         profiles=[fake_entry(i) for i in (1, 2, 3, 5)])))
    m = json.loads((root / JK.BUILD_MANIFEST_REL).read_text(encoding='utf-8'))
    m['files'][JK.PRACTICE_REL]['sha256_lf'] = sha
    (root / JK.BUILD_MANIFEST_REL).write_text(json.dumps(m), encoding='utf-8')
    with pytest.raises(LT.LabelToolError, match='PR-1..PR-4'):
        LT.PracticeApp(root)


def test_practice_csv_resume_gap_free(tmp_path):
    root = make_root(tmp_path)
    app = label_all(root, n=3)
    raw = (root / LT.PRACTICE_LABELS_REL).read_bytes()
    assert raw.startswith('﻿'.encode('utf-8')) and b'\r\n' in raw
    rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline='')))
    assert list(rows[0]) == list(LT.PRACTICE_FIELDS) and [r['order'] for r in rows] == ['1', '2', '3']
    assert rows[0]['device_id'] == 'PR-1' and rows[0]['item'] == app.keys[0][1]
    again = LT.PracticeApp(root)
    assert again.state()['done'] == 3 and again.state()['first_unlabelled'] == 3
    with pytest.raises(ValueError, match='gap-free'):
        again.item(5)
    with pytest.raises(ValueError, match='unknown cell'):
        again.save(dict(index=3, j_id='PR-1', item=again.keys[3][1], label='recommended', confidence='low',
                        reason='x'))


def test_review_refused_with_counts(tmp_path):
    root = make_root(tmp_path)
    with pytest.raises(PermissionError, match=r'labels 0/80; valid results A 0/4 B 0/4 C 0/4'):
        LT.ReviewApp(root)
    label_all(root, n=80)
    write_results(root, LT.PRACTICE_IDS, judges=('A', 'B'))
    with pytest.raises(PermissionError, match=r'labels 80/80; valid results A 4/4 B 4/4 C 0/4'):
        LT.ReviewApp(root)
    write_results(root, LT.PRACTICE_IDS, judges=('C',))
    bad = result_for('C', 'PR-3')
    bad['cells'].pop()                                       # invalid result does not count
    (root / LT.JUDGES_PRACTICE_REL / 'C' / 'PR-3.result.json').write_text(json.dumps(bad), encoding='utf-8')
    with pytest.raises(PermissionError, match=r'C 3/4'):
        LT.ReviewApp(root)


def test_review_content(tmp_path):
    root = make_root(tmp_path)
    label_all(root, n=80, label_for=lambda d, it: 'mandatory')
    write_results(root, LT.PRACTICE_IDS)
    app = LT.ReviewApp(root)
    got = app.review(0)
    d, it = got['cell']['device_id'], got['cell']['item']
    assert (d, it) == app.practice.keys[0] and got['hariom']['label'] == 'mandatory'
    assert sorted(got['judges']) == ['A', 'B', 'C']
    assert got['judges']['B'] == dict(label='recommended', reason=f'B-REASON-{it}', section='§4.1')
    assert app.state() == dict(mode='review-practice', total=80, labels_file='labels_hariom_practice.csv')
    for word in FORBIDDEN:
        assert word not in json.dumps(got)


def serve(app):
    server = LT.ThreadingHTTPServer(('127.0.0.1', 0), LT.make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_address[1]}'


def test_review_http_read_only(tmp_path):
    root = make_root(tmp_path)
    label_all(root, n=80)
    write_results(root, LT.PRACTICE_IDS)
    server, base = serve(LT.ReviewApp(root))
    try:
        assert b'S10 Practice Review' in urllib.request.urlopen(base + '/').read()
        got = json.loads(urllib.request.urlopen(base + '/api/review?i=5').read())
        assert got['index'] == 5
        for path in ('/api/item?i=0',):
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(base + path)
        req = urllib.request.Request(base + '/api/save', data=b'{}', headers={'Content-Type': 'application/json'})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_practice_http_roundtrip(tmp_path):
    root = make_root(tmp_path)
    app = LT.PracticeApp(root)
    server, base = serve(app)
    try:
        page = urllib.request.urlopen(base + '/').read()
        assert b'S10 labelling' in page and b'cbname' in page
        item = json.loads(urllib.request.urlopen(base + '/api/item?i=0').read())
        assert item['codebook_name'] == 'CODEBOOK_v0.1.md' and item['cell']['device_id'] == 'PR-1'
        body = dict(index=0, device_id='PR-1', item=item['cell']['item'], label='recommended', confidence='high',
                    reason='fits')
        req = urllib.request.Request(base + '/api/save', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        assert json.loads(urllib.request.urlopen(req).read())['done'] == 1
    finally:
        server.shutdown()
        server.server_close()
