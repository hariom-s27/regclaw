"""A3 tests use synthetic roots under arbitrary pytest tmp_path locations."""
from contextlib import contextmanager
import csv
from datetime import datetime, timedelta
import hashlib
from http.client import HTTPConnection
from http.server import HTTPServer
import io
import json
import math
from pathlib import Path
import random
import threading

import pytest
import yaml

from _s10b2b_fake import build_repo, entries, ITEMS
from eval.s10 import diagnostics as DG
from eval.s10 import draws as D
from eval.s10 import label_tool as LT
from eval.s10 import retest_a3 as A
from eval.s10.prereg_freeze import compute

REPO = Path(__file__).resolve().parents[1]
NOW = '2026-12-01T12:00:00+00:00'
LATER = '2026-12-08T12:00:00+00:00'
LEAK = 'PRIVATE-SYNTHETIC-CONTENT'
M, N, R = 'mandatory', 'not_applicable', 'recommended'


@pytest.fixture(autouse=True)
def fake_only(monkeypatch):
    """Block the real gold directory; never restrict pytest's temporary root."""
    monkeypatch.setenv('REGCLAW_STUB', '1')
    original = Path.open
    forbidden = (REPO / 'data/gold/s10').resolve()

    def guarded(path, *args, **kwargs):
        if path.resolve().is_relative_to(forbidden):
            raise AssertionError('real S10 access forbidden')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guarded)


def sha(raw):
    return hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()


def put(root, relative, raw):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw.encode('utf-8') if isinstance(raw, str) else raw)
    return path


def put_json(root, relative, doc):
    return put(root, relative, json.dumps(doc) + '\n')


def load(root, relative):
    return json.loads((root / relative).read_bytes())


def rows(root, relative):
    return list(csv.DictReader(io.StringIO((root / relative).read_text(encoding='utf-8-sig'), newline='')))


def go(root, *args, now=NOW):
    out, err = [], []
    code = A.main(list(args), root=root, now=now, out=out.append, err=err.append)
    console = '\n'.join(out + err)
    for forbidden in (LEAK, 'J-001', 'J-EXTRA', 'Quasar', 'Synthetic reason', 'ISO 10993', 'fda-knee'):
        assert forbidden not in console
    return code, out, err


@pytest.fixture
def root(tmp_path):
    root = build_repo(tmp_path / 'fake-root', human_upto=150, merge=False)
    # Extend only the synthetic population; preserve the original audit list and labels.
    population = entries()
    template = population[0]
    population += [dict(template, j_id=f'J-EXTRA-{i}', items=list(ITEMS)) for i in range(4)]
    judge = put(root, D.JUDGE_FILE_REL, yaml.safe_dump(dict(entries=population), sort_keys=False))
    for entry in population:
        entry['profile']['flags'] = [LEAK]
        entry['profile']['existing_evidence'] = dict(hidden=LEAK)
    judge.write_text(yaml.safe_dump(dict(entries=population), sort_keys=False), encoding='utf-8')
    build = load(root, D.BUILD_MANIFEST_REL)
    build['files'][D.JUDGE_FILE_REL] = dict(sha256_lf=sha(judge.read_bytes()))
    put_json(root, D.BUILD_MANIFEST_REL, build)
    put_json(root, A.AUDIT_FREEZE_REL, dict(sha256_lf=sha((root / D.HUMAN_REL).read_bytes())))
    put(root, LT.CODEBOOK_REL, '# Synthetic codebook\n## Section heading\n')
    put_json(root, LT.CODEBOOK_V1_MANIFEST_REL,
             dict(files={LT.CODEBOOK_REL: dict(sha256_lf=sha((root / LT.CODEBOOK_REL).read_bytes()))}))
    put(root, A.SPEC_REL, '# Synthetic A3\n')
    put(root, A.MODULE_REL, '# Synthetic module for a fake code lock\r\n')
    assert go(root, 'freeze-a3', '--file', A.SPEC_REL, '--expected-sha', sha((root / A.SPEC_REL).read_bytes()))[0] == 0
    assert go(root, 'lock', '--expected-sha', f'{A.MODULE_REL}={sha((root / A.MODULE_REL).read_bytes())}')[0] == 0
    return root


def ready(root):
    assert go(root, 'draw')[0] == 0
    return A.RetestApp(root, now=NOW)


def payload(app, index, label=R):
    c = app.cells[index]
    return dict(index=index, j_id=c['j_id'], item=c['item'], label=label,
                confidence='high', reason='Synthetic reason')


def recognition(app, index, answer='no'):
    p = payload(app, index)
    return {k: p[k] for k in ('index', 'j_id', 'item')} | dict(answer=answer)


def fill(app):
    for i in range(len(app.cells)):
        app.save_label(payload(app, i))
        if not app.followup:
            app.save_recognition(recognition(app, i))


def frozen(root):
    app = ready(root)
    fill(app)
    assert go(root, 'freeze-labels', '--expected-draw-sha', sha((root / A.DRAW_REL).read_bytes()))[0] == 0
    return app


def rewrite_rows(root, relative, records, fields=LT.FIELDS):
    put(root, relative, A._csv_bytes(fields, records))


def test_draw_matches_locked_retest_and_wait_dates(root):
    expected, date = D.retest(D.load_audit_rows(root), D.load_label_csv(root / D.HUMAN_REL))
    code, out, _ = go(root, 'draw')
    assert code == 0
    doc = load(root, A.DRAW_REL)
    assert doc['cells'] == expected
    assert doc['format'] == D.FORMAT and doc['draw'] == 'retest'
    assert doc['seed_string'] == 'S10b-v1|retest' and doc['positions'] == '1-150'
    assert doc['wait_days'] == 1
    assert datetime.fromisoformat(doc['locked_earliest_utc']) == date
    assert datetime.fromisoformat(doc['earliest_allowed_utc']) == date - timedelta(days=6)
    assert doc['cells_sha256'] == hashlib.sha256('\n'.join(c['cell_id'] for c in expected).encode()).hexdigest()
    assert doc['addendum_a3_sha256_lf'] == sha((root / A.SPEC_REL).read_bytes())
    assert f'audit_list_rows=350 filler_pool_size={load(root, A.FILLER_DRAW_REL)["pool_size"]}' in out
    assert any(line == f'{A.DRAW_REL} sha256_lf={sha((root / A.DRAW_REL).read_bytes())}' for line in out)
    assert D.load_draw(root, 'retest') == doc


@pytest.mark.parametrize('changed', ['audit', 'a3-spec', 'a3-manifest', 'lock-source', 'lock-manifest'])
def test_draw_requires_unchanged_frozen_inputs(root, changed):
    relative = {'audit': D.HUMAN_REL, 'a3-spec': A.SPEC_REL, 'a3-manifest': A.A3_REL,
                'lock-source': A.MODULE_REL, 'lock-manifest': A.LOCK_REL}[changed]
    path = root / relative
    path.write_bytes(path.read_bytes() + b'changed\n')
    assert go(root, 'draw')[0] == 2
    assert not (root / A.DRAW_REL).exists() and not (root / A.FILLER_DRAW_REL).exists()


@pytest.mark.parametrize('missing', [A.A3_REL, A.LOCK_REL])
def test_draw_requires_missing_freeze(root, missing, monkeypatch):
    original = A._raw

    def absent(at, relative):
        if relative == missing:
            raise FileNotFoundError
        return original(at, relative)

    monkeypatch.setattr(A, '_raw', absent)
    assert go(root, 'draw')[0] == 2
    assert not (root / A.DRAW_REL).exists() and not (root / A.FILLER_DRAW_REL).exists()


@pytest.mark.parametrize('existing', [A.DRAW_REL, A.FILLER_DRAW_REL])
def test_draw_existing_destination_writes_nothing(root, existing):
    put(root, existing, 'keep\n')
    assert go(root, 'draw')[0] == 3
    assert (root / existing).read_bytes() == b'keep\n'
    other = A.FILLER_DRAW_REL if existing == A.DRAW_REL else A.DRAW_REL
    assert not (root / other).exists()


def test_draw_second_write_failure_retains_fillers(root, monkeypatch):
    original = Path.open

    def failing(path, mode='r', *args, **kwargs):
        if path == root / A.DRAW_REL and mode == 'x':
            raise OSError('simulated second-write failure')
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', failing)
    code, out, err = go(root, 'draw')
    assert code == 2 and out == []
    assert err == ['partial draw: retest_fillers.json written; report to Hariom']
    assert len(load(root, A.FILLER_DRAW_REL)['cells']) == 30
    assert not (root / A.DRAW_REL).exists()
    assert go(root, 'draw')[0] == 3


def test_fillers_pool_and_determinism(root):
    ready(root)
    doc = load(root, A.FILLER_DRAW_REL)
    audit = {f'{r["j_id"]}|{r["item"]}' for r in D.load_audit_rows(root)}
    population = {f'{e["j_id"]}|{item}' for e in D.load_entries(root) for item in e['items']}
    pool = sorted(population - audit)
    assert doc['pool_size'] == len(pool)
    assert doc['seed_string'] == 'S10b-v1|retest-fillers'
    selected = [c['cell_id'] for c in doc['cells']]
    assert selected == random.Random('S10b-v1|retest-fillers').sample(pool, 30)
    assert not set(selected) & audit
    assert len(selected) == len(set(selected)) == 30
    assert doc['cells_sha256'] == hashlib.sha256('\n'.join(selected).encode()).hexdigest()


def test_session_order_and_privacy(root):
    app = ready(root)
    jids = sorted({c['j_id'] for c in app.cells})
    random.Random('S10b-v1|retest-session').shuffle(jids)
    assert list(dict.fromkeys(c['j_id'] for c in app.cells)) == jids
    by_id = {e['j_id']: e for e in D.load_entries(root)}
    for j in jids:
        selected = [c['item'] for c in app.cells if c['j_id'] == j]
        assert selected == [item for item in by_id[j]['items'] if item in selected]
    assert set(app.state()) == {'total', 'done', 'first_incomplete'}
    item = app.item(0)
    assert set(item) == {'index', 'cell', 'profile', 'headings', 'codebook_name', 'label', 'label_locked',
                         'needs_recognition', 'recognition', 'total', 'done', 'first_incomplete'}
    assert item['label'] is None and item['recognition'] is None
    assert set(item['cell']) == {'position', 'j_id', 'item'}
    assert LEAK not in json.dumps(item)
    for word in ('repeat', 'filler', 'audit', 'earlier label', 'first_pass', 'cells_sha256'):
        assert word not in A.PAGE.lower()
        assert word not in json.dumps(item).lower()
    assert A.QUESTION not in A.PAGE  # Injected by the handler, without sampling metadata.


def test_recognition_required_and_label_locked(root):
    app = ready(root)
    with pytest.raises(A.Refused):
        app.save_recognition(recognition(app, 0))
    app.save_label(payload(app, 0))
    assert app.state()['done'] == 0 and app.item(0)['needs_recognition']
    with pytest.raises(A.Refused):
        app.item(1)
    with pytest.raises(A.Refused):
        app.save_label(payload(app, 1))
    app.save_recognition(recognition(app, 0, 'unsure'))
    assert app.state()['done'] == 1
    before = dict(app.labels)
    with pytest.raises(A.Refused):
        app.save_label(payload(app, 0, M))
    with pytest.raises(A.Refused):
        app.save_recognition(recognition(app, 0, 'yes'))
    assert app.labels == before
    assert app.item(0)['label_locked'] and not app.item(0)['needs_recognition']


def test_resume_pending_recognition_first(root):
    app = ready(root)
    app.save_label(payload(app, 0))
    again = A.RetestApp(root, now=NOW)
    assert again.state() == dict(total=60, done=0, first_incomplete=0)
    item = again.item(0)
    assert item['needs_recognition'] and item['label_locked'] and item['label']['label'] == R
    with pytest.raises(A.Refused):
        again.save_label(payload(again, 0, M))
    expected = sha((root / A.DRAW_REL).read_bytes())
    assert go(root, 'freeze-labels', '--expected-draw-sha', expected)[0] == 1
    assert not (root / A.SESSION_REL).exists()
    again.save_recognition(recognition(again, 0))
    resumed = A.RetestApp(root, now=NOW)
    assert resumed.state()['first_incomplete'] == 1


def test_session_csv_contract(root):
    app = ready(root)
    fill(app)
    repeat = D.load_label_csv(root / A.LABELS_REL)
    assert set(repeat) == app.repeat_ids and len(repeat) == 30
    for relative in (A.LABELS_REL, A.FILLER_LABELS_REL, A.RECOGNITION_REL):
        raw = (root / relative).read_bytes()
        assert raw.startswith(b'\xef\xbb\xbf') and b'\r\n' in raw
        assert b'\n' not in raw.replace(b'\r\n', b'')
        expected_fields = A.RECOGNITION_FIELDS if relative == A.RECOGNITION_REL else LT.FIELDS
        records = rows(root, relative)
        assert tuple(records[0]) == expected_fields
        assert all(r['timestamp'] == NOW for r in records)
        assert [int(r['order']) for r in records] == sorted(int(r['order']) for r in records)
    assert len(rows(root, A.FILLER_LABELS_REL)) == 30
    assert len(rows(root, A.RECOGNITION_REL)) == 60
    assert A.RetestApp(root, now=NOW).state()['done'] == 60


@pytest.mark.parametrize('blocker', [A.SHEET_REL, A.SESSION_REL])
def test_serve_refusals_write_nothing(root, blocker):
    ready(root)
    put(root, blocker, 'keep\n')
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert go(root, 'serve', '--no-browser')[0] == 1
    assert {p: p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_serve_checks_judge_and_codebook(root):
    ready(root)
    path = root / LT.CODEBOOK_REL
    path.write_bytes(path.read_bytes() + b'changed\n')
    assert go(root, 'serve', '--no-browser')[0] == 1
    assert not (root / A.LABELS_REL).exists()


@contextmanager
def http_app(app):
    with HTTPServer(('127.0.0.1', 0), A.make_handler(app)) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            worker.join(timeout=5)


def request(port, method, endpoint, data=None, extra=None):
    connection = HTTPConnection('127.0.0.1', port, timeout=5)
    body = json.dumps(data).encode() if data is not None else None
    headers = dict(extra or {})
    if body is not None:
        headers['Content-Type'] = 'application/json'
    connection.request(method, endpoint, body=body, headers=headers)
    response = connection.getresponse()
    status, raw = response.status, response.read()
    connection.close()
    return status, raw


def test_http_security_and_page(root):
    app = ready(root)
    with http_app(app) as port:
        assert request(port, 'GET', '/', extra={'Host': 'evil.invalid'})[0] == 403
        status, page = request(port, 'GET', '/')
        assert status == 200 and A.QUESTION.encode() in page
        for word in (b'repeat', b'filler', b'audit', LEAK.lower().encode()):
            assert word not in page.lower()
        p = payload(app, 0)
        assert request(port, 'POST', '/api/save', p)[0] == 403
        good = {'X-Session-Token': app.token, 'Origin': f'http://127.0.0.1:{port}'}
        assert request(port, 'POST', '/api/save', p, good | {'Origin': 'http://evil.invalid'})[0] == 403
        assert request(port, 'POST', '/api/save', p, good | {'X-Session-Token': 'wrong'})[0] == 403
        assert request(port, 'POST', '/api/save', p | {'membership': 'hidden'}, good)[0] == 400
        assert request(port, 'POST', '/api/save', p, good)[0] == 200
        assert request(port, 'GET', '/api/item?i=1')[0] == 400
        status, raw = request(port, 'GET', '/api/item?i=0')
        assert status == 200 and json.loads(raw)['needs_recognition']
        assert request(port, 'POST', '/api/recognition', recognition(app, 0), good)[0] == 200
        assert request(port, 'POST', '/api/save', payload(app, 0, M), good)[0] == 400
        status, raw = request(port, 'GET', '/api/state')
        assert status == 200 and set(json.loads(raw)) == {'total', 'done', 'first_incomplete'}


def test_atomic_save_retry_and_failed_save_keeps_state(root, monkeypatch):
    app = ready(root)
    original = A.os.replace
    attempts = []
    monkeypatch.setattr(A.time, 'sleep', lambda delay: None)

    def fail(source, destination):
        attempts.append((source, destination))
        raise PermissionError

    monkeypatch.setattr(A.os, 'replace', fail)
    with pytest.raises(PermissionError):
        app.save_label(payload(app, 0))
    assert len(attempts) == 5 and app.labels == {}
    assert all(source.exists() for source, _ in attempts)
    assert all(source.parent == destination.parent for source, destination in attempts)
    monkeypatch.setattr(A.os, 'replace', original)
    app.save_label(payload(app, 0))
    saved = app.labels[app.cells[0]['cell_id']]['timestamp']
    app.now = '2026-12-01T13:00:00+00:00'
    app.save_label(payload(app, 0))
    assert app.labels[app.cells[0]['cell_id']]['timestamp'] == saved


def test_atomic_save_permission_retry_succeeds(root, monkeypatch):
    app = ready(root)
    original = A.os.replace
    count = []
    monkeypatch.setattr(A.time, 'sleep', lambda delay: None)

    def flaky(source, destination):
        count.append(1)
        if len(count) < 3:
            raise PermissionError
        return original(source, destination)

    monkeypatch.setattr(A.os, 'replace', flaky)
    app.save_label(payload(app, 0))
    assert len(count) == 3 and len(app.labels) == 1


def test_freeze_labels_validation_and_write_once(root):
    app = ready(root)
    expected = sha((root / A.DRAW_REL).read_bytes())
    assert go(root, 'freeze-labels', '--expected-draw-sha', expected)[0] == 1
    fill(app)
    assert go(root, 'freeze-labels', '--expected-draw-sha', '0' * 64)[0] == 2
    assert not (root / A.SESSION_REL).exists()
    assert go(root, 'freeze-labels', '--expected-draw-sha', expected)[0] == 0
    before = (root / A.SESSION_REL).read_bytes()
    doc = load(root, A.SESSION_REL)
    assert doc['draws'] == {relative: sha((root / relative).read_bytes())
                            for relative in (A.DRAW_REL, A.FILLER_DRAW_REL)}
    assert doc['addendum_a3_sha256_lf'] == sha((root / A.SPEC_REL).read_bytes())
    assert doc['code_lock_sha256_lf'] == sha((root / A.LOCK_REL).read_bytes())
    for relative, n in ((A.LABELS_REL, 30), (A.FILLER_LABELS_REL, 30), (A.RECOGNITION_REL, 60)):
        assert doc['files'][relative] == dict(sha256_lf=sha((root / relative).read_bytes()),
                                             bytes=len((root / relative).read_bytes()), rows=n,
                                             first_timestamp_utc=NOW, last_timestamp_utc=NOW)
    assert go(root, 'freeze-labels', '--expected-draw-sha', expected)[0] == 3
    assert (root / A.SESSION_REL).read_bytes() == before
    with pytest.raises(A.Refused):
        app.save_label(payload(app, 0))


def test_freeze_labels_refuses_early_timestamp(root):
    app = ready(root)
    fill(app)
    records = rows(root, A.LABELS_REL)
    early = datetime.fromisoformat(load(root, A.DRAW_REL)['earliest_allowed_utc']) - timedelta(seconds=1)
    records[0]['timestamp'] = early.isoformat()
    rewrite_rows(root, A.LABELS_REL, records)
    assert go(root, 'freeze-labels', '--expected-draw-sha', sha((root / A.DRAW_REL).read_bytes()))[0] == 1
    assert not (root / A.SESSION_REL).exists()


def test_freeze_labels_refuses_final_pending_recognition(root):
    app = ready(root)
    for i in range(60):
        app.save_label(payload(app, i))
        if i < 59:
            app.save_recognition(recognition(app, i))
    assert len(app.labels) == 60 and len(app.answers) == 59
    resumed = A.RetestApp(root, now=NOW)
    assert resumed.state()['first_incomplete'] == 59
    assert resumed.item(59)['needs_recognition'] and resumed.item(59)['label_locked']
    expected = sha((root / A.DRAW_REL).read_bytes())
    assert go(root, 'freeze-labels', '--expected-draw-sha', expected)[0] == 1
    assert not (root / A.SESSION_REL).exists()
    resumed.save_recognition(recognition(resumed, 59))
    assert go(root, 'freeze-labels', '--expected-draw-sha', expected)[0] == 0


@pytest.mark.parametrize('changes,n,expected', [(0, 30, '0.0950'), (1, 30, '0.1486'),
                                               (2, 30, '0.1953'), (0, 15, '0.1810')])
def test_exact_upper_bound(changes, n, expected):
    assert f'{A.clopper_pearson_upper(changes, n):.4f}' == expected
    if changes == 0:
        assert A.clopper_pearson_upper(changes, n) == pytest.approx(1 - 0.05 ** (1 / n))
    assert A.clopper_pearson_upper(n, n) == 1.0


def test_fisher_exact():
    assert A.fisher_greater(21, 30, 9, 30) == pytest.approx(0.00206, abs=0.000005)
    assert A.fisher_greater(0, 30, 0, 30) == 1.0
    assert A.fisher_greater(30, 30, 30, 30) == 1.0


def test_gap_stats_and_binary_agreement():
    cells = [dict(cell_id=str(i), j_id=f'P-{i}') for i in range(3)]
    first = {str(i): dict(label=N, timestamp=NOW) for i in range(3)}
    again = {str(i): dict(label=R if i != 1 else M,
                          timestamp=(datetime.fromisoformat(NOW) + timedelta(hours=h)).isoformat())
             for i, h in enumerate((24, 30, 48))}
    assert A.gap_stats(first, again, cells) == dict(n=3, min=24, median=30, max=48)
    assert A._agreement(first, again, cells)['agree'] == 2


def test_companions_aggregates_and_write_once(root):
    app = ready(root)
    # Recognition margins are synthetic and independent of labels.
    seen = {True: 0, False: 0}
    for i, c in enumerate(app.cells):
        repeated = c['cell_id'] in app.repeat_ids
        label = D.load_label_csv(root / D.HUMAN_REL)[c['cell_id']]['label'] if repeated else R
        app.save_label(payload(app, i, label))
        answer = 'yes' if seen[repeated] < (21 if repeated else 9) else 'unsure'
        app.save_recognition(recognition(app, i, answer))
        seen[repeated] += 1
    assert go(root, 'freeze-labels', '--expected-draw-sha', sha((root / A.DRAW_REL).read_bytes()))[0] == 0
    assert go(root, 'companions')[0] == 0
    doc = load(root, A.COMPANIONS_REL)['result']
    assert doc['raw_agreement']['agree'] == doc['raw_agreement']['n'] == 30
    assert f'{doc["raw_agreement"]["change_rate_upper_95"]:.4f}' == '0.0950'
    assert doc['recognition'] == dict(repeats=dict(yes=21, no=0, unsure=9), fillers=dict(yes=9, no=0, unsure=21))
    assert doc['recognition_fisher_greater_p'] == pytest.approx(0.00206, abs=0.000005)
    assert doc['gap_hours'] == A.gap_stats(D.load_label_csv(root / D.HUMAN_REL), app.labels,
                                          load(root, A.DRAW_REL)['cells'])
    assert doc['undefined_kappa_note'] == 'none' and 'kappa' not in doc
    assert go(root, 'companions')[0] == 3


def test_companions_undefined_kappa_note(root):
    records = rows(root, D.HUMAN_REL)
    for record in records:
        record['label'] = M
    rewrite_rows(root, D.HUMAN_REL, records)
    put_json(root, A.AUDIT_FREEZE_REL, dict(sha256_lf=sha((root / D.HUMAN_REL).read_bytes())))
    frozen(root)
    assert go(root, 'companions')[0] == 0
    assert 'one class' in load(root, A.COMPANIONS_REL)['result']['undefined_kappa_note']


@pytest.mark.parametrize('relative', [A.LABELS_REL, A.FILLER_LABELS_REL, A.RECOGNITION_REL,
                                     A.DRAW_REL, A.FILLER_DRAW_REL, A.SPEC_REL, A.MODULE_REL])
def test_companions_require_matching_freeze(root, relative):
    frozen(root)
    path = root / relative
    path.write_bytes(path.read_bytes() + b'\n')
    assert go(root, 'companions')[0] in (1, 2)
    assert not (root / A.COMPANIONS_REL).exists()


def test_locked_intra_rater_cli_accepts_wrapper_outputs(root, monkeypatch):
    app = ready(root)
    fill(app)
    monkeypatch.setattr(DG, 'ROOT', root)
    # diagnostics.main resolves ROOT at invocation; diagnostics.run receives it explicitly.
    assert DG.main(['intra-rater']) == 0
    assert (root / DG.DIAG_REL / 'intra_rater.json').is_file()


def test_locked_intra_rater_cli_refuses_early(root, monkeypatch):
    app = ready(root)
    fill(app)
    records = rows(root, A.LABELS_REL)
    records[0]['timestamp'] = (datetime.fromisoformat(load(root, A.DRAW_REL)['earliest_allowed_utc'])
                               - timedelta(seconds=1)).isoformat()
    rewrite_rows(root, A.LABELS_REL, records)
    monkeypatch.setattr(DG, 'ROOT', root)
    assert DG.main(['intra-rater']) == 1
    assert not (root / DG.DIAG_REL / 'intra_rater.json').exists()


def test_followup_session_and_freeze(root, monkeypatch):
    frozen(root)
    assert go(root, 'followup-serve', '--no-browser', now='2026-12-08T11:59:59+00:00')[0] == 1
    assert not (root / A.FOLLOWUP_LABELS_REL).exists()
    app = A.RetestApp(root, followup=True, now=LATER)
    expected_jids = sorted({c['j_id'] for c in load(root, A.FILLER_DRAW_REL)['cells']})
    random.Random('S10b-v1|filler-followup').shuffle(expected_jids)
    assert list(dict.fromkeys(c['j_id'] for c in app.cells)) == expected_jids
    assert len(app.cells) == 30 and not app.item(0)['needs_recognition']
    with http_app(app) as port:
        assert A.QUESTION.encode() not in request(port, 'GET', '/')[1]
    assert go(root, 'followup-freeze', now=LATER)[0] == 1
    fill(app)
    assert A.RetestApp(root, followup=True, now=LATER).state()['done'] == 30
    assert go(root, 'followup-freeze', now=LATER)[0] == 0
    assert go(root, 'followup-freeze', now=LATER)[0] == 3
    assert go(root, 'followup-serve', '--no-browser', now=LATER)[0] == 1
    called = []
    original = DG.intra_rater

    def recorded(first, again, cells, **kwargs):
        called.append(len(cells))
        return original(first, again, cells, **kwargs)

    monkeypatch.setattr(DG, 'intra_rater', recorded)
    assert go(root, 'followup-companions', now=LATER)[0] == 0
    assert called == [30]
    result = load(root, A.FOLLOWUP_COMPANIONS_REL)['result']
    assert result['label'] == 'exploratory 7-day retest on fillers (A3)'
    assert result['raw_agreement']['agree'] == 30
    assert result['gap_hours'] == dict(n=30, min=168, median=168, max=168)
    assert math.isnan(result['kappa']['kappa'])  # All synthetic fillers were R on both passes.
    assert result['kappa']['n_undefined'] == 2000
    assert go(root, 'followup-companions', now=LATER)[0] == 3


def test_followup_requires_matching_session_freeze(root):
    frozen(root)
    path = root / A.FILLER_LABELS_REL
    path.write_bytes(path.read_bytes() + b'\n')
    assert go(root, 'followup-serve', '--no-browser', now=LATER)[0] in (1, 2)
    assert not (root / A.FOLLOWUP_LABELS_REL).exists()


def test_followup_freeze_refuses_early(root):
    frozen(root)
    app = A.RetestApp(root, followup=True, now=LATER)
    fill(app)
    records = rows(root, A.FOLLOWUP_LABELS_REL)
    records[0]['timestamp'] = '2026-12-08T11:59:59+00:00'
    rewrite_rows(root, A.FOLLOWUP_LABELS_REL, records)
    assert go(root, 'followup-freeze', now=LATER)[0] == 1


def test_followup_companions_requires_matching_followup_freeze(root):
    frozen(root)
    app = A.RetestApp(root, followup=True, now=LATER)
    fill(app)
    assert go(root, 'followup-freeze', now=LATER)[0] == 0
    records = rows(root, A.FOLLOWUP_LABELS_REL)
    records[0]['reason'] = 'Changed synthetic reason'
    rewrite_rows(root, A.FOLLOWUP_LABELS_REL, records)
    assert go(root, 'followup-companions', now=LATER)[0] == 1
    assert not (root / A.FOLLOWUP_COMPANIONS_REL).exists()


def test_boundary_demo(capsys):
    cells = [dict(cell_id=f'cell-{i}', j_id=f'profile-{i % 20:02d}') for i in range(30)]
    first = {c['cell_id']: M if i % 2 else N for i, c in enumerate(cells)}
    perfect = DG.intra_rater(first, dict(first), cells)
    assert perfect['kappa'] == perfect['lo'] == perfect['hi'] == 1.0
    assert perfect['n_profiles'] == 20
    changed = dict(first)
    changed[cells[0]['cell_id']] = M
    one_change = DG.intra_rater(first, changed, cells)
    assert one_change['kappa'] == pytest.approx(0.9333333333333333)
    assert one_change['lo'] < 1.0 and one_change['hi'] == 1.0
    one_class = {c['cell_id']: M for c in cells}
    undefined = DG.intra_rater(one_class, one_class, cells)
    assert all(math.isnan(undefined[k]) for k in ('kappa', 'lo', 'hi'))
    assert undefined['n_undefined'] == 2000
    with capsys.disabled():
        for name, result in (('balanced_perfect', perfect), ('balanced_one_change', one_change),
                             ('one_class', undefined)):
            print('boundary_demo ' + name + ' ' + json.dumps(result, sort_keys=True))


def test_freeze_a3_and_lock_write_once(root):
    for command, relative, args in (
        ('freeze-a3', A.A3_REL, ['--file', A.SPEC_REL, '--expected-sha', sha((root / A.SPEC_REL).read_bytes())]),
        ('lock', A.LOCK_REL, ['--expected-sha', f'{A.MODULE_REL}={sha((root / A.MODULE_REL).read_bytes())}'])):
        before = (root / relative).read_bytes()
        assert go(root, command, *args)[0] == 3
        assert (root / relative).read_bytes() == before
    assert load(root, A.LOCK_REL)['files'] == {A.MODULE_REL: compute((root / A.MODULE_REL).read_bytes())}


def test_freeze_a3_and_lock_mismatch_writes_nothing(tmp_path):
    root = tmp_path / 'other-fake-root'
    put(root, A.SPEC_REL, '# Synthetic A3\n')
    put(root, A.MODULE_REL, '# Synthetic module\n')
    assert go(root, 'freeze-a3', '--file', A.SPEC_REL, '--expected-sha', '0' * 64)[0] == 2
    assert not (root / A.A3_REL).exists()
    assert go(root, 'lock', '--expected-sha', f'{A.MODULE_REL}={"0" * 64}')[0] == 1
    assert not (root / A.LOCK_REL).exists()


def test_committed_retest_a3_lock_matches():
    path = REPO / A.LOCK_REL
    if not path.is_file():
        pytest.skip('A3 code lock not written yet')
    manifest = json.loads(path.read_bytes())
    assert set(manifest) == {'format', 'created_at', 'python_version', 'files'}
    assert manifest['format'] == 's10b2h-code-lock.v1'
    assert manifest['files'] == {A.MODULE_REL: compute((REPO / A.MODULE_REL).read_bytes())}


def test_help_never_reads_data(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('help must not read inputs')

    monkeypatch.setattr(A, '_raw', forbidden)
    commands = (None, 'freeze-a3', 'lock', 'draw', 'serve', 'freeze-labels', 'companions',
                'followup-serve', 'followup-freeze', 'followup-companions')
    for command in commands:
        with pytest.raises(SystemExit) as exc:
            A.main(([command] if command else []) + ['--help'], root=tmp_path)
        assert exc.value.code == 0


def test_new_statistics_are_stdlib_only():
    source = (REPO / A.MODULE_REL).read_text(encoding='utf-8')
    import ast
    tree = ast.parse(source)
    imports = {alias.name.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
               for alias in node.names}
    imports |= {node.module.split('.')[0] for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and not node.level and node.module}
    assert not imports & {'scipy', 'numpy', 'sklearn'}
    assert not any(isinstance(node, ast.Attribute) and node.attr in ('unlink', 'remove', 'rmdir')
                   for node in ast.walk(tree))
