"""S10b-10 label tool, on FAKE judge file / audit list / codebook / manifest only (local 127.0.0.1 server)."""
import csv
import io
import json
import threading
import urllib.error
import urllib.request

import pytest
import yaml

from eval.s10 import label_tool as LT
from standards.ruleset import normalised_sha

ITEMS = [f'ISO 10993-{n}' for n in (1, 3, 4, 5, 6, 10, 11, 12, 17, 18, 23, 7)]
LEAK = 'P-LEAK-007'
FORBIDDEN = {'split', 'stratum', 'comment', 'flags', 'existing_evidence', 'family', 'derived', 'id'}


def fake_files(root, n_rows=350):
    entries = []
    for i in range(1, 31):
        comp = dict(name='FAKE part', contact_nature='surface', contact_site='intact_skin',
                    exposure=dict(pattern='single', hours_per_use=2), sterilization='non_sterile',
                    materials=[dict(name='FAKE polymer', material_class='polymer')],
                    existing_evidence=dict(equivalent_marketed_device=True))
        entries.append(dict(j_id=f'J-{i:03d}', contact_category='surface/intact_skin', duration='A',
                            split='holdout', stratum='S3', comment=LEAK, flags=['x'], id=LEAK,
                            profile=dict(device_name=f'Fake gizmo {i}', components=[comp]), items=ITEMS))
    judge = yaml.safe_dump(dict(format='s10-judge-file.v1', entries=entries), sort_keys=False)
    cells = [(e['j_id'], it) for e in entries for it in ITEMS][:n_rows]
    buf = io.StringIO(newline='')
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(['order', 'j_id', 'item', 'family'])
    for k, (j, it) in enumerate(cells, start=1):
        w.writerow([k, j, it, 'F1'])
    paths = dict(judge=root / 'judge.yaml', audit_list=root / 'list.csv', codebook=root / 'cb.md',
                 labels=root / 'out' / 'labels.csv', manifest=root / 'build.json')
    paths['judge'].write_text(judge, encoding='utf-8')
    paths['audit_list'].write_text(buf.getvalue(), encoding='utf-8')
    paths['codebook'].write_text('# Fake codebook\nintro\n## 1. First\nbody\n### sub\n## 2. Second\n', encoding='utf-8')
    paths['manifest'].write_text(json.dumps(dict(files={
        LT.JUDGE_REL: dict(sha256_lf=normalised_sha(judge.encode())),
        LT.LIST_REL: dict(sha256_lf=normalised_sha(buf.getvalue().encode()))})), encoding='utf-8')
    paths['codebook_manifest'] = root / 'codebook_v1.json'          # S10b2: audit needs the frozen codebook
    paths['codebook_manifest'].write_text(json.dumps(dict(template_version='t', files={
        LT.CODEBOOK_REL: dict(sha256_lf=normalised_sha(paths['codebook'].read_bytes()))})), encoding='utf-8')
    return paths


def app_for(paths, upto=150):
    return LT.LabelApp(paths['judge'], paths['audit_list'], paths['codebook'], paths['labels'], upto, paths['manifest'],
                       codebook_manifest=paths['codebook_manifest'])


def ok(app, i, **kw):
    j, it = app.keys[i]
    return dict(index=i, j_id=j, item=it, label='recommended', confidence='medium', reason='fits the grid') | kw


def keys_deep(obj):
    if isinstance(obj, dict):
        return set(obj) | set().union(*(keys_deep(v) for v in obj.values()))
    if isinstance(obj, list):
        return set().union(*(keys_deep(v) for v in obj)) if obj else set()
    return set()


@pytest.fixture
def paths(tmp_path):
    return fake_files(tmp_path)


def test_item_shows_only_blind_fields(paths):
    app = app_for(paths)
    got = app.item(0)
    text = json.dumps(got)
    assert not FORBIDDEN & keys_deep(got) and LEAK not in text
    assert sorted(got['profile']) == ['contact_category', 'duration', 'profile']
    assert got['cell'] == dict(position=1, j_id='J-001', item=ITEMS[0])
    assert got['headings'] == ['# Fake codebook', '## 1. First', '## 2. Second']
    assert 'F1' not in text
    assert app.state() == dict(mode='audit', total=150, upto=150, done=0, first_unlabelled=0, labels_file='labels.csv')


@pytest.mark.parametrize('upto', [150, 250, 350])
def test_upto_limits_total(paths, upto):
    assert app_for(paths, upto).state()['total'] == upto


@pytest.mark.parametrize('upto', [0, 100, 200, 351])
def test_upto_other_values_refused(paths, upto):
    with pytest.raises(ValueError):
        app_for(paths, upto)


@pytest.mark.parametrize('bad', [dict(label='maybe'), dict(confidence='very'), dict(reason=''), dict(reason='   '),
                                 dict(reason='two\nlines'), dict(reason='x' * 301), dict(item='ISO 99999'),
                                 dict(index=999)])
def test_save_validates(paths, bad):
    app = app_for(paths)
    with pytest.raises(ValueError):
        app.save(ok(app, 0, **bad))
    assert not paths['labels'].exists()


def test_save_writes_bom_crlf_and_resumes(paths):
    app = app_for(paths)
    app.save(ok(app, 0, label='mandatory', confidence='high', reason='always addressed'))
    app.save(ok(app, 1, reason='x' * 300))
    raw = paths['labels'].read_bytes()
    assert raw.startswith('﻿'.encode('utf-8')) and b'\r\n' in raw
    rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline='')))
    assert [r['order'] for r in rows] == ['1', '2'] and list(rows[0]) == list(LT.FIELDS)
    assert rows[0]['label'] == 'mandatory' and rows[0]['timestamp']
    again = app_for(paths)
    assert again.state()['done'] == 2 and again.state()['first_unlabelled'] == 2
    assert again.item(0)['label']['reason'] == 'always addressed'
    again.save(ok(again, 0, label='not_applicable'))            # re-label an earlier cell
    assert again.state()['done'] == 2


def test_gap_free_order(paths):
    app = app_for(paths)
    with pytest.raises(ValueError, match='gap-free'):
        app.item(1)
    with pytest.raises(ValueError, match='gap-free'):
        app.save(ok(app, 1))
    app.save(ok(app, 0))
    assert app.item(1)['index'] == 1


def test_labels_file_with_gap_or_beyond_upto_refused(paths):
    app = app_for(paths, 250)
    for i in range(160):
        app.save(ok(app, i))
    assert app_for(paths, 250).state()['done'] == 160
    assert app_for(paths, 350).state()['done'] == 160            # 150/250 labels open under a larger --upto
    with pytest.raises(ValueError, match='outside'):
        app_for(paths, 150)
    text = paths['labels'].read_text(encoding='utf-8-sig').splitlines()
    del text[5]                                                   # remove one row -> gap
    paths['labels'].write_text('\n'.join(text) + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='gap-free'):
        app_for(paths, 250)


def test_invalid_saved_label_refused(paths):
    app = app_for(paths)
    app.save(ok(app, 0))
    paths['labels'].write_text(paths['labels'].read_text(encoding='utf-8-sig').replace('recommended', 'maybe'),
                               encoding='utf-8')
    with pytest.raises(ValueError):
        app_for(paths)


@pytest.mark.parametrize('which', ['judge', 'audit_list'])
def test_sha_mismatch_refuses_start(paths, which):
    p = paths[which]
    p.write_text(p.read_text(encoding='utf-8') + '\n# changed\n' if which == 'judge'
                 else p.read_text(encoding='utf-8'), encoding='utf-8')
    if which == 'audit_list':
        m = json.loads(paths['manifest'].read_text(encoding='utf-8'))
        m['files'][LT.LIST_REL]['sha256_lf'] = '0' * 64
        paths['manifest'].write_text(json.dumps(m), encoding='utf-8')
    with pytest.raises(PermissionError):
        app_for(paths)


def interleave(paths):
    """Rewrite the fake list so J-ids interleave (A,B,A,B...) inside every look; update the manifest sha."""
    jids = [f'J-{i:03d}' for i in range(1, 31)]
    mixed = []
    for a, b in zip(jids[0::2], jids[1::2]):
        for it in ITEMS:
            mixed += [dict(j_id=a, item=it), dict(j_id=b, item=it)]
    mixed = mixed[:350]
    buf = io.StringIO(newline='')
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(['order', 'j_id', 'item', 'family'])
    for k, r in enumerate(mixed, start=1):
        w.writerow([k, r['j_id'], r['item'], 'F1'])
    paths['audit_list'].write_text(buf.getvalue(), encoding='utf-8')
    m = json.loads(paths['manifest'].read_text(encoding='utf-8'))
    m['files'][LT.LIST_REL]['sha256_lf'] = normalised_sha(buf.getvalue().encode())
    paths['manifest'].write_text(json.dumps(m), encoding='utf-8')
    return [(r['j_id'], r['item']) for r in mixed]


def test_audit_grouped_by_jid_inside_each_look(paths):
    listed = interleave(paths)
    app = app_for(paths, 350)
    looks = [listed[0:150], listed[150:250], listed[250:350]]
    expect = []
    for look in looks:
        for j in dict.fromkeys(k[0] for k in look):
            expect += [k for k in look if k[0] == j]
    assert app.keys == expect
    assert set(app.keys[:150]) == set(looks[0]) and set(app.keys[150:250]) == set(looks[1])
    assert app.keys[0][0] == app.keys[1][0] and listed[0][0] != listed[1][0]
    assert app_for(paths, 150).keys == app.keys[:150] and app_for(paths, 250).keys == app.keys[:250]


def test_audit_csv_rows_stay_in_list_order(paths):
    listed = interleave(paths)
    app = app_for(paths)
    for i in range(13):                                    # J-001's 12 cells, then J-002's first
        app.save(ok(app, i))
    rows = list(csv.DictReader(io.StringIO(paths['labels'].read_text(encoding='utf-8-sig'), newline='')))
    orders = [int(r['order']) for r in rows]
    assert orders == sorted(listed.index(k) + 1 for k in app.keys[:13])
    assert orders != [listed.index(k) + 1 for k in app.keys[:13]]  # display order differs, CSV is in list order
    assert [(r['j_id'], r['item']) for r in rows] == [listed[o - 1] for o in orders]
    assert app_for(paths).state()['done'] == 13


def test_audit_refused_without_codebook_v1(paths):
    paths['codebook_manifest'].unlink()
    with pytest.raises(PermissionError, match='does not exist'):
        app_for(paths)


def test_audit_refused_on_codebook_sha(paths):
    paths['codebook'].write_text('# changed\n', encoding='utf-8')
    with pytest.raises(PermissionError, match='codebook sha differs'):
        app_for(paths)


def test_audit_defaults():
    assert LT.CODEBOOK_REL == 'eval/s10/codebook/CODEBOOK_v1.0.md'
    assert LT.CODEBOOK_V1_MANIFEST_REL == 'eval/s10/manifests/codebook_v1.json'
    with pytest.raises(SystemExit):
        LT.main(['--mode', 'audit'])                               # --upto required in audit mode
    with pytest.raises(SystemExit):
        LT.main(['--mode', 'practice', '--upto', '150'])           # --upto is audit-only


def test_http_roundtrip(paths):
    app = app_for(paths)
    server = LT.ThreadingHTTPServer(('127.0.0.1', 0), LT.make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        assert b'S10 labelling' in urllib.request.urlopen(base + '/').read()
        state = json.loads(urllib.request.urlopen(base + '/api/state').read())
        assert state['total'] == 150 and state['done'] == 0
        item = json.loads(urllib.request.urlopen(base + '/api/item?i=0').read())
        assert LEAK not in json.dumps(item)
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + '/api/item?i=3')
        req = urllib.request.Request(base + '/api/save', data=json.dumps(ok(app, 0)).encode(),
                                     headers={'Content-Type': 'application/json'})
        assert json.loads(urllib.request.urlopen(req).read())['done'] == 1
        bad = urllib.request.Request(base + '/api/save', data=json.dumps(ok(app, 1, label='x')).encode(),
                                     headers={'Content-Type': 'application/json'})
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(bad)
    finally:
        server.shutdown()
        server.server_close()
