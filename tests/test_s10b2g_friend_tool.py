"""Friend browser checks use synthetic roots and in-memory HTTP connections."""
import builtins
import csv
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.s10 import friend_tool as FT
from eval.s10 import second_rater as SR


REPO = Path(__file__).resolve().parents[1]
PORT = 8767
NA, R, M = 'not_applicable', 'recommended', 'mandatory'
PRIVATE = 'SYNTHETIC-HIDDEN-SENTINEL'
JID = 'J-SYNTHETIC-PRIVATE'
ITEMS = ('ISO-FAKE-ALPHA', 'ISO-FAKE-BETA', 'ISO-FAKE-GAMMA')
HEADER = b'sheet_id,label,unsure_guideline_gap,note\n'


@pytest.fixture(autouse=True)
def fake_only(monkeypatch):
    """Do not depend on tmp_path being inside the repository."""
    monkeypatch.setenv('REGCLAW_STUB', '1')
    real_gold = (REPO / 'data/gold/s10').resolve()
    path_open, builtin_open, os_open = Path.open, builtins.open, os.open

    def check(path):
        if isinstance(path, int):
            return
        candidate = Path(os.fsdecode(path)).resolve()
        if (candidate.is_relative_to(real_gold)
                or candidate.name in ('holdout_profiles.yaml', 'profiles_draft.yaml', 'jid_map.json')
                or 'jid_map' in candidate.parts):
            raise AssertionError('forbidden data access')

    def guarded_path(path, *args, **kwargs):
        check(path)
        return path_open(path, *args, **kwargs)

    def guarded_builtin(path, *args, **kwargs):
        check(path)
        return builtin_open(path, *args, **kwargs)

    def guarded_os(path, *args, **kwargs):
        check(path)
        return os_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guarded_path)
    monkeypatch.setattr(builtins, 'open', guarded_builtin)
    monkeypatch.setattr(os, 'open', guarded_os)


def put(root, relative, raw):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw.encode('utf-8') if isinstance(raw, str) else raw)
    return path


def sha(raw):
    return hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()


def csv_raw(rows, header=('sheet_id', 'label', 'unsure_guideline_gap', 'note')):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(header)
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


@pytest.fixture
def kit(tmp_path, monkeypatch):
    root = tmp_path / 'synthetic-root'
    assert root.resolve() != REPO.resolve()
    # JSON is valid YAML, so the fixture needs no additional serializer.
    hidden = {key: PRIVATE for key in SR.HIDDEN}
    profile = dict(device_name='Synthetic device <not markup>', components=[dict(
        name='Synthetic part', contact_nature='surface', contact_site='intact_skin',
        exposure=dict(pattern='single', hours_per_use=1),
        materials=[dict(name='Synthetic polymer', material_class='polymer')],
        nested=dict(hidden), **hidden)], **hidden)
    entry = dict(j_id=JID, profile=profile, contact_category='surface/intact_skin',
                 duration='A', items=list(ITEMS))
    judge_raw = json.dumps(dict(entries=[entry])).encode('utf-8')
    put(root, SR.D.JUDGE_FILE_REL, judge_raw)
    put(root, SR.D.BUILD_MANIFEST_REL, json.dumps(dict(files={
        SR.D.JUDGE_FILE_REL: dict(sha256_lf=sha(judge_raw))})))
    codebook = b'# Synthetic frozen codebook\n\n<plain text, never HTML>\n'
    put(root, SR.CODEBOOK_REL, codebook)
    put(root, SR.CODEBOOK_MANIFEST_REL, json.dumps(dict(files={
        SR.CODEBOOK_REL: dict(sha256_lf=sha(codebook))})))
    cells = [dict(sheet_id=f'S-{i:03d}', cell_id=f'{JID}|{item}',
                  stratum='unaudited_recommended', inclusion_probability=1.0)
             for i, item in enumerate(ITEMS, 1)]
    strata = {name: dict(population=0, n=0, forced_count=0) for name in SR.TARGETS}
    strata['unaudited_recommended'] = dict(population=3, n=3, forced_count=0)
    seeds = {name: f'S10b-A2|friend|{name}' for name in SR.TARGETS}
    seeds['sheet_order'] = 'S10b-A2|friend-order'
    draw = dict(format='s10-friend-set.v1', seed_strings=seeds, strata=strata, cells=cells)
    put(root, SR.DRAW_REL, json.dumps(draw))
    put(root, SR.SHEET_REL, SR.render_sheet(cells, {JID: entry}))
    put(root, SR.LABELS_REL, HEADER + b'S-001,,,\nS-002,,,\nS-003,,,\n')
    cold_cells = [dict(sheet_id=f'C-{i:03d}', cell_id=f'PR-{i}|{item}')
                  for i, item in enumerate(ITEMS, 1)]
    practice = {f'PR-{i}': dict(entry) for i in range(1, 4)}
    cold = dict(cells=cold_cells, population=3,
                reference={cell['cell_id']: PRIVATE for cell in cold_cells})
    put(root, SR.COLD_SHEET_REL, SR.render_sheet(cold_cells, practice))
    put(root, SR.COLD_LABELS_REL, HEADER + b'C-001,,,\nC-002,,,\nC-003,,,\n')
    calls = []

    def coldread(passed_root):
        assert Path(passed_root) == root
        calls.append(passed_root)
        return cold, practice

    monkeypatch.setattr(SR, '_coldread', coldread)
    return SimpleNamespace(root=root, cells=cells, cold_cells=cold_cells,
                           codebook=codebook, cold_calls=calls)


def app_for(kit, sheet='main'):
    output = []
    app = FT.FriendApp(kit.root, sheet, out=output.append)
    return app, output


class MemorySocket:
    def __init__(self, request):
        self.reader = io.BytesIO(request)
        self.response = io.BytesIO()

    def makefile(self, *args, **kwargs):
        return self.reader

    def sendall(self, data):
        self.response.write(data)


def request(app, method='GET', path='/', *, headers=None, payload=None, raw_body=None, port=PORT):
    """Dispatch the real handler, without a socket, listener or browser."""
    body = raw_body if raw_body is not None else (
        json.dumps(payload).encode('utf-8') if payload is not None else b'')
    headers = list(headers) if headers is not None else [('Host', f'127.0.0.1:{port}')]
    if body and not any(key.lower() == 'content-length' for key, _ in headers):
        headers.append(('Content-Length', str(len(body))))
    head = f'{method} {path} HTTP/1.0\r\n'
    head += ''.join(f'{key}: {value}\r\n' for key, value in headers)
    connection = MemorySocket(head.encode('iso-8859-1') + b'\r\n' + body)
    server = SimpleNamespace(server_address=('127.0.0.1', port))
    FT.make_handler(app)(connection, ('127.0.0.1', 12345), server)
    response_head, response_body = connection.response.getvalue().split(b'\r\n\r\n', 1)
    lines = response_head.decode('iso-8859-1').split('\r\n')
    response_headers = dict(line.split(': ', 1) for line in lines[1:])
    assert int(response_headers['Content-Length']) == len(response_body)
    return int(lines[0].split()[1]), response_headers, response_body


def answer(app, **changes):
    payload = dict(sheet_id=app.cells[0]['sheet_id'], label=R, unsure=False, note='')
    payload.update(changes)
    return payload


def save_headers(app, host=None, origin=None):
    result = [('Host', host or f'127.0.0.1:{PORT}'), ('X-Session-Token', app.token)]
    if origin is not None:
        result.append(('Origin', origin))
    return result


@pytest.mark.parametrize('sheet,prefix', [('main', 'S'), ('coldread', 'C')])
def test_startup_resume_blind_response_and_no_startup_write(kit, sheet, prefix):
    relative = SR.LABELS_REL if sheet == 'main' else SR.COLD_LABELS_REL
    # A reordered, non-prefix partial CSV must resume in draw order.
    put(kit.root, relative, csv_raw([
        (f'{prefix}-003', '', '', ''),
        (f'{prefix}-002', M, 'yes', 'Friend-only note'),
        (f'{prefix}-001', '', '', '')]))
    before = snapshot(kit.root)
    app, output = app_for(kit, sheet)
    assert snapshot(kit.root) == before
    assert output == []
    assert app.state() == dict(total=3, answered=1, first_unanswered=0)
    assert [row['sheet_id'] for row in app.rows] == [f'{prefix}-001', f'{prefix}-002', f'{prefix}-003']
    status, headers, body = request(app, path='/api/cell?index=1')
    assert status == 200 and headers['Cache-Control'] == 'no-store'
    data = json.loads(body)
    assert set(data) == {'index', 'content', 'sheet_id', 'label', 'unsure_guideline_gap',
                         'note', 'total', 'answered', 'first_unanswered'}
    assert data['sheet_id'] == f'{prefix}-002'
    assert data['label'] == M and data['note'] == 'Friend-only note'
    assert data['unsure_guideline_gap'] == 'yes'
    assert ITEMS[1] in data['content']
    assert 'contact_category: surface/intact_skin' in data['content']
    assert 'duration: A' in data['content']
    assert 'device_name: Synthetic device <not markup>' in data['content']
    for secret in (PRIVATE, JID, 'PR-', 'cell_id', 'j_id', 'stratum', 'inclusion_probability',
                   'confidence', 'reason', 'existing_evidence', 'derived', 'final', NA, R):
        assert secret not in body.decode('utf-8')
    assert kit.cold_calls == ([kit.root] if sheet == 'coldread' else [])
    # Unanswered cells never inherit an answer, note or uncertainty flag.
    for index in (0, 2):
        blank = json.loads(request(app, path=f'/api/cell?index={index}')[2])
        assert (blank['label'], blank['unsure_guideline_gap'], blank['note']) == ('', '', '')
    assert json.loads(request(app, path='/api/state')[2]) == dict(total=3, answered=1, first_unanswered=0)


@pytest.mark.parametrize('sheet,prefix', [('main', 'S'), ('coldread', 'C')])
def test_save_exact_binary_format_order_and_complete_validator(kit, sheet, prefix):
    app, output = app_for(kit, sheet)
    app.save(f'{prefix}-003', M, unsure=True, note='Friend, "own" café')
    app.save(f'{prefix}-001', NA)
    expected = (HEADER + f'{prefix}-001,not_applicable,,\n{prefix}-002,,,\n'
                f'{prefix}-003,mandatory,yes,"Friend, ""own"" café"\n'.encode('utf-8'))
    # Keep the byte oracle independent of the production CSV serializer.
    assert app.labels_path.read_bytes() == expected
    app.save(f'{prefix}-002', R)
    raw = app.labels_path.read_bytes()
    assert b'\r' not in raw and not raw.startswith(b'\xef\xbb\xbf')
    assert raw.endswith(b'\n')
    rows = SR.checked_friend_rows(raw, kit.cells if sheet == 'main' else kit.cold_cells)
    assert [row['label'] for row in rows] == [NA, R, M]
    assert app.state() == dict(total=3, answered=3, first_unanswered=None)
    assert output == ['answered 1 of 3', 'answered 2 of 3', 'answered 3 of 3']
    resumed, _ = app_for(kit, sheet)
    assert resumed.rows == rows
    assert not list(app.labels_path.parent.glob('*.tmp'))
    # Relabelling does not increase the number answered.
    app.save(f'{prefix}-001', R)
    assert output[-1] == 'answered 3 of 3'


@pytest.mark.parametrize('note,expected', [
    ('a\r\nb\nc\rd', 'abcd'),
    ('x' * 201, 'x' * 200),
    ('a\r\n' * 201, 'a' * 200),
    ('', ''),
])
def test_note_sanitising_and_uncertainty(kit, note, expected):
    app, _ = app_for(kit)
    result = app.save('S-001', R, unsure=True, note=note)
    assert result['note'] == expected and result['unsure_guideline_gap'] == 'yes'
    app.save('S-001', R, unsure=False, note=note)
    saved = list(csv.DictReader(io.StringIO(app.labels_path.read_bytes().decode('utf-8'))))
    assert saved[0]['note'] == expected and saved[0]['unsure_guideline_gap'] == ''
    assert b'\r' not in app.labels_path.read_bytes()


def test_resume_bom_crlf_and_legacy_no_are_canonical_on_save(kit):
    raw = csv_raw([('S-003', '', '', ''), ('S-002', M, 'no', 'a\r\nb'), ('S-001', '', '', '')])
    put(kit.root, SR.LABELS_REL, b'\xef\xbb\xbf' + raw.replace(b'\n', b'\r\n'))
    app, _ = app_for(kit)
    assert app.cell(1)['note'] == 'ab'
    assert app.cell(1)['unsure_guideline_gap'] == ''
    app.save('S-001', R)
    assert app.labels_path.read_bytes() == HEADER + b'S-001,recommended,,\nS-002,mandatory,,ab\nS-003,,,\n'


@pytest.mark.parametrize('field,value', [
    ('label', ''), ('label', PRIVATE), ('label', []),
    ('sheet_id', 'S-999'), ('sheet_id', JID),
    ('unsure', 'yes'), ('unsure', 1), ('note', None), ('note', 42),
])
def test_invalid_answer_never_changes_memory_or_files(kit, field, value):
    app, output = app_for(kit)
    before = snapshot(kit.root)
    original = [dict(row) for row in app.rows]
    with pytest.raises((ValueError, TypeError)):
        app.save(**answer(app, **{field: value}))
    assert snapshot(kit.root) == before and app.rows == original and output == []


def refuse(kit, sheet, monkeypatch):
    before = snapshot(kit.root)
    out, err = [], []

    def no_server(*args, **kwargs):
        raise AssertionError('refusal must precede server startup')

    monkeypatch.setattr(FT, 'serve', no_server)
    code = FT.main(['serve', '--sheet', sheet, '--no-browser'], root=kit.root,
                   out=out.append, err=err.append)
    assert code == 2 and out == [] and err == [FT.REFUSAL]
    assert PRIVATE not in err[0] and JID not in err[0]
    assert snapshot(kit.root) == before


@pytest.mark.parametrize('sheet', ['main', 'coldread'])
@pytest.mark.parametrize('bad', ['missing', 'header', 'duplicate', 'missing-id', 'extra-id',
                                'wrong-id', 'columns', 'invalid-label', 'invalid-gap', 'malformed'])
def test_target_refusals(kit, sheet, bad, monkeypatch):
    relative = SR.LABELS_REL if sheet == 'main' else SR.COLD_LABELS_REL
    prefix = 'S' if sheet == 'main' else 'C'
    rows = [[f'{prefix}-001', '', '', ''], [f'{prefix}-002', '', '', ''], [f'{prefix}-003', '', '', '']]
    header = ('sheet_id', 'label', 'unsure_guideline_gap', 'note')
    if bad == 'missing':
        # Point the imported path constant at an absent synthetic file.
        monkeypatch.setattr(SR, 'LABELS_REL' if sheet == 'main' else 'COLD_LABELS_REL',
                            'missing-fake-target.csv')
    else:
        if bad == 'header':
            header = tuple(reversed(header))
        elif bad == 'duplicate':
            rows[1][0] = rows[0][0]
        elif bad == 'missing-id':
            rows.pop()
        elif bad == 'extra-id':
            rows.append([f'{prefix}-004', '', '', ''])
        elif bad == 'wrong-id':
            rows[0][0] = 'SYNTHETIC-WRONG-ID'
        elif bad == 'columns':
            rows[0].append(PRIVATE)
        elif bad == 'invalid-label':
            rows[0][1] = PRIVATE
        elif bad == 'invalid-gap':
            rows[0][2] = PRIVATE
        raw = csv_raw(rows, header)
        if bad == 'malformed':
            raw += b'"unterminated'
        put(kit.root, relative, raw)
    refuse(kit, sheet, monkeypatch)


@pytest.mark.parametrize('sheet', ['main', 'coldread'])
@pytest.mark.parametrize('bad', ['sheet-missing', 'sheet-changed', 'book-missing', 'book-changed',
                                'book-manifest-missing', 'book-manifest-bad', 'book-entry-missing'])
def test_sheet_and_codebook_refusals(kit, sheet, bad, monkeypatch):
    sheet_constant = 'SHEET_REL' if sheet == 'main' else 'COLD_SHEET_REL'
    if bad == 'sheet-missing':
        monkeypatch.setattr(SR, sheet_constant, 'absent-fake-sheet.md')
    elif bad == 'sheet-changed':
        put(kit.root, getattr(SR, sheet_constant), PRIVATE)
    elif bad == 'book-missing':
        monkeypatch.setattr(SR, 'CODEBOOK_REL', 'absent-fake-book.md')
    elif bad == 'book-changed':
        put(kit.root, SR.CODEBOOK_REL, PRIVATE)
    elif bad == 'book-manifest-missing':
        monkeypatch.setattr(SR, 'CODEBOOK_MANIFEST_REL', 'absent-fake-manifest.json')
    elif bad == 'book-manifest-bad':
        put(kit.root, SR.CODEBOOK_MANIFEST_REL, PRIVATE)
    else:
        put(kit.root, SR.CODEBOOK_MANIFEST_REL, '{"files":{}}')
    refuse(kit, sheet, monkeypatch)


@pytest.mark.parametrize('bad', ['missing', 'json', 'seed', 'identifier', 'probability',
                                'profile-sha', 'swapped-cells', 'changed-profile', 'malformed-yaml'])
def test_main_source_refusals(kit, bad, monkeypatch):
    if bad == 'missing':
        monkeypatch.setattr(SR, 'DRAW_REL', 'absent-fake-draw.json')
    elif bad == 'profile-sha':
        put(kit.root, SR.D.JUDGE_FILE_REL, PRIVATE)
    elif bad in ('changed-profile', 'malformed-yaml'):
        raw = (kit.root / SR.D.JUDGE_FILE_REL).read_bytes()
        raw = (b'entries: [' if bad == 'malformed-yaml'
               else raw.replace(b'Synthetic device', b'Changed synthetic device'))
        put(kit.root, SR.D.JUDGE_FILE_REL, raw)
        put(kit.root, SR.D.BUILD_MANIFEST_REL, json.dumps(dict(files={
            SR.D.JUDGE_FILE_REL: dict(sha256_lf=sha(raw))})))
    else:
        raw = json.loads((kit.root / SR.DRAW_REL).read_bytes())
        if bad == 'seed':
            raw['seed_strings']['sheet_order'] = PRIVATE
        elif bad == 'identifier':
            raw['cells'][0]['sheet_id'] = PRIVATE
        elif bad == 'probability':
            raw['cells'][0]['inclusion_probability'] = 0
        elif bad == 'swapped-cells':
            first, second = raw['cells'][:2]
            first['cell_id'], second['cell_id'] = second['cell_id'], first['cell_id']
        put(kit.root, SR.DRAW_REL, PRIVATE if bad == 'json' else json.dumps(raw))
    refuse(kit, 'main', monkeypatch)


def test_coldread_helper_failure_is_content_free(kit, monkeypatch):
    def fail(root):
        raise PermissionError(PRIVATE)

    monkeypatch.setattr(SR, '_coldread', fail)
    refuse(kit, 'coldread', monkeypatch)


def test_frozen_main_refuses_before_reading_sources(kit, monkeypatch):
    # Only existence is needed: never parse a manifest to select file inputs.
    put(kit.root, SR.MANIFEST_REL, PRIVATE)

    def forbidden(*args, **kwargs):
        raise AssertionError('frozen startup must not read data sources')

    # Take the snapshot outside the read guard used for this assertion.
    before = snapshot(kit.root)
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, 'read_bytes', forbidden)
        out, err = [], []
        code = FT.main(['serve', '--sheet', 'main'], root=kit.root,
                       out=out.append, err=err.append)
        assert code == 2 and out == [] and err == [FT.REFUSAL]
    assert snapshot(kit.root) == before
    # The main freeze does not prohibit the separate training sheet.
    app, _ = app_for(kit, 'coldread')
    assert app.state()['total'] == 3


def test_freeze_appearing_during_session_blocks_save(kit):
    app, output = app_for(kit)
    put(kit.root, SR.MANIFEST_REL, PRIVATE)
    before = snapshot(kit.root)
    original = [dict(row) for row in app.rows]
    with pytest.raises(ValueError):
        app.save('S-001', R)
    assert snapshot(kit.root) == before and app.rows == original and output == []


@pytest.mark.parametrize('sheet', ['main', 'coldread'])
def test_lf_sheet_and_codebook_comparison_accepts_crlf(kit, sheet):
    relative = SR.SHEET_REL if sheet == 'main' else SR.COLD_SHEET_REL
    path = kit.root / relative
    path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
    path = kit.root / SR.CODEBOOK_REL
    path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
    app, _ = app_for(kit, sheet)
    assert app.state()['total'] == 3
    status, headers, body = request(app, path='/codebook')
    assert status == 200 and headers['Content-Type'] == 'text/plain; charset=utf-8'
    assert headers['X-Content-Type-Options'] == 'nosniff'
    assert body == kit.codebook.replace(b'\n', b'\r\n')


def test_atomic_write_flushes_closes_and_replaces_in_same_directory(kit, monkeypatch):
    app, _ = app_for(kit)
    target = app.labels_path
    old = target.read_bytes()
    events = []
    original_temp = FT.tempfile.NamedTemporaryFile
    original_replace = FT.os.replace

    class Tracked:
        def __init__(self, stream):
            self.stream = stream
            self.name = stream.name

        def __enter__(self):
            return self

        def write(self, raw):
            events.append('write')
            return self.stream.write(raw)

        def flush(self):
            events.append('flush')
            self.stream.flush()

        def __exit__(self, *args):
            self.stream.close()
            events.append('close')

    def temporary(*args, **kwargs):
        assert Path(kwargs['dir']).resolve() == target.parent.resolve()
        assert kwargs['delete'] is False
        return Tracked(original_temp(*args, **kwargs))

    def replace(source, destination):
        assert Path(source).parent.resolve() == target.parent.resolve()
        assert Path(destination) == target
        assert target.read_bytes() == old
        assert Path(source).read_bytes() == HEADER + b'S-001,recommended,,\nS-002,,,\nS-003,,,\n'
        assert events == ['write', 'flush', 'close']
        # Memory must still reflect the old file at replacement time.
        assert app.rows[0]['label'] == ''
        events.append('replace')
        original_replace(source, destination)

    monkeypatch.setattr(FT.tempfile, 'NamedTemporaryFile', temporary)
    monkeypatch.setattr(FT.os, 'replace', replace)
    app.save('S-001', R)
    assert events == ['write', 'flush', 'close', 'replace']
    assert app.rows[0]['label'] == R


@pytest.mark.parametrize('stage', ['temp-open', 'temp-write', 'replace'])
@pytest.mark.parametrize('succeed', [True, False])
def test_permission_retries_third_attempt_or_all_five(kit, stage, succeed, monkeypatch):
    app, output = app_for(kit)
    old_file = app.labels_path.read_bytes()
    old_rows = [dict(row) for row in app.rows]
    original_replace = FT.os.replace
    original_temp = FT.tempfile.NamedTemporaryFile
    attempts, delays = [], []

    def attempt():
        attempts.append('attempt')
        assert app.labels_path.read_bytes() == old_file
        assert app.rows == old_rows and output == []
        if not succeed or len(attempts) < 3:
            raise PermissionError(PRIVATE)

    def replace(source, destination):
        attempt()
        return original_replace(source, destination)

    class WriteFailure:
        def __init__(self, stream):
            self.stream = stream
            self.name = stream.name

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, raw):
            attempt()
            return self.stream.write(raw)

        def flush(self):
            self.stream.flush()

    def temporary(*args, **kwargs):
        if stage == 'temp-open':
            attempt()
        stream = original_temp(*args, **kwargs)
        return WriteFailure(stream) if stage == 'temp-write' else stream

    monkeypatch.setattr(FT.time, 'sleep', delays.append)
    if stage == 'replace':
        monkeypatch.setattr(FT.os, 'replace', replace)
    else:
        monkeypatch.setattr(FT.tempfile, 'NamedTemporaryFile', temporary)
    status, _, body = request(app, 'POST', '/api/save', headers=save_headers(app), payload=answer(app))
    if succeed:
        assert status == 200 and json.loads(body)['label'] == R
        assert len(attempts) == 3 and delays == [0.05, 0.1]
        assert app.rows[0]['label'] == R and output == ['answered 1 of 3']
    else:
        assert status == 503 and body == b'{"error":"not saved"}'
        assert len(attempts) == 5 and delays == [0.05, 0.1, 0.2, 0.4, 0.8]
        assert app.labels_path.read_bytes() == old_file and app.rows == old_rows and output == []
        assert json.loads(request(app, path='/api/cell?index=0')[2])['label'] == ''
    assert PRIVATE.encode() not in body
    assert not list(app.labels_path.parent.glob('*.tmp'))


@pytest.mark.parametrize('method', ['GET', 'POST', 'HEAD', 'PUT', 'OPTIONS'])
@pytest.mark.parametrize('hosts', [[], ['evil.test:8767'], ['127.0.0.1'], ['localhost'],
                                 ['127.0.0.1:9999'], ['LOCALHOST:8767'], ['127.0.0.1:8767.evil'],
                                 ['user@127.0.0.1:8767'], ['127.0.0.1:8767', 'localhost:8767']])
def test_host_rejected_for_every_method(kit, method, hosts):
    app, output = app_for(kit)
    before = snapshot(kit.root)
    headers = [('Host', host) for host in hosts]
    headers.append(('X-Session-Token', app.token))
    status, _, body = request(app, method, '/api/save', headers=headers, payload=answer(app))
    assert status == 403 and body == b'{"error":"refused"}'
    assert snapshot(kit.root) == before and output == []


@pytest.mark.parametrize('host', ['127.0.0.1:8767', 'localhost:8767'])
@pytest.mark.parametrize('origin', [None, 'http://127.0.0.1:8767', 'http://localhost:8767'])
def test_allowed_hosts_and_origins(kit, host, origin):
    app, _ = app_for(kit)
    assert request(app, headers=[('Host', host)])[0] == 200
    status, _, body = request(app, 'POST', '/api/save', headers=save_headers(app, host, origin),
                              payload=answer(app, unsure=True, note='own note'))
    assert status == 200
    assert json.loads(body)['unsure_guideline_gap'] == 'yes'


def test_security_checks_use_the_actual_server_port(kit):
    app, _ = app_for(kit)
    assert request(app, port=9123)[0] == 200
    assert request(app, port=9123, headers=[('Host', '127.0.0.1:8767')])[0] == 403
    headers = [('Host', 'localhost:9123'), ('Origin', 'http://localhost:9123'),
               ('X-Session-Token', app.token)]
    assert request(app, 'POST', '/api/save', port=9123, headers=headers, payload=answer(app))[0] == 200
    headers[1] = ('Origin', 'http://localhost:8767')
    assert request(app, 'POST', '/api/save', port=9123, headers=headers, payload=answer(app))[0] == 403


@pytest.mark.parametrize('tokens', [[], ['wrong'], [''], ['café'], ['valid', 'valid']])
def test_missing_wrong_non_ascii_and_duplicate_tokens_rejected(kit, tokens):
    app, output = app_for(kit)
    before = snapshot(kit.root)
    headers = [('Host', f'127.0.0.1:{PORT}')]
    headers.extend(('X-Session-Token', app.token if token == 'valid' else token) for token in tokens)
    status, _, body = request(app, 'POST', '/api/save', headers=headers, payload=answer(app))
    assert status == 403 and body == b'{"error":"refused"}'
    assert snapshot(kit.root) == before and output == []


@pytest.mark.parametrize('origins', [[''], ['null'], ['https://127.0.0.1:8767'],
                                   ['http://127.0.0.1:9999'], ['http://localhost:8767/'],
                                   ['http://evil.test'], ['http://localhost:8767.evil'],
                                   ['http://127.0.0.1:8767', 'http://localhost:8767']])
def test_invalid_and_duplicate_origins_rejected(kit, origins):
    app, output = app_for(kit)
    before = snapshot(kit.root)
    headers = save_headers(app) + [('Origin', origin) for origin in origins]
    status, _, body = request(app, 'POST', '/api/save', headers=headers, payload=answer(app))
    assert status == 403 and body == b'{"error":"refused"}'
    assert snapshot(kit.root) == before and output == []


def test_token_is_random_per_session_and_embedded_only_in_page(kit, monkeypatch):
    generated, calls = iter(['synthetic-session-one', 'synthetic-session-two']), []

    def token_urlsafe():
        calls.append('called')
        return next(generated)

    monkeypatch.setattr(FT.secrets, 'token_urlsafe', token_urlsafe)
    first, _ = app_for(kit)
    second, _ = app_for(kit)
    assert calls == ['called', 'called'] and first.token != second.token
    page = request(first)[2].decode('utf-8')
    assert first.token in page and second.token not in page and '__SESSION_TOKEN__' not in page
    for path in ('/api/state', '/api/cell?index=0', '/codebook'):
        assert first.token.encode() not in request(first, path=path)[2]
    status, _, _ = request(second, 'POST', '/api/save', headers=save_headers(first), payload=answer(second))
    assert status == 403


@pytest.mark.parametrize('payload', [None, [], {}, {'sheet_id': JID}, {'unexpected': PRIVATE}])
def test_bad_save_payload_content_free_and_unchanged(kit, payload):
    app, output = app_for(kit)
    before = snapshot(kit.root)
    status, _, body = request(app, 'POST', '/api/save', headers=save_headers(app),
                              raw_body=json.dumps(payload).encode('utf-8'))
    assert status == 400 and body == b'{"error":"not saved"}'
    assert snapshot(kit.root) == before and output == []


@pytest.mark.parametrize('length', ['-1', '0', '65537', 'bad'])
def test_invalid_body_length_is_rejected(kit, length):
    app, _ = app_for(kit)
    headers = save_headers(app) + [('Content-Length', length)]
    status, _, body = request(app, 'POST', '/api/save', headers=headers)
    assert status == 400 and body == b'{"error":"not saved"}'


@pytest.mark.parametrize('extra_headers,body', [
    ([], b'{broken json'),
    ([('Content-Length', '1'), ('Content-Length', '1')], b'x'),
    ([('Transfer-Encoding', 'chunked')], b'0\r\n\r\n'),
])
def test_malformed_body_and_ambiguous_framing_do_not_save(kit, extra_headers, body):
    app, output = app_for(kit)
    before = snapshot(kit.root)
    status, _, response = request(app, 'POST', '/api/save', headers=save_headers(app) + extra_headers,
                                  raw_body=body)
    assert status == 400 and response == b'{"error":"not saved"}'
    assert snapshot(kit.root) == before and output == []


@pytest.mark.parametrize('path', ['/api/cell?index=-1', '/api/cell?index=3',
                                 '/api/cell?index=not-a-number'])
def test_bad_cell_requests_are_content_free(kit, path):
    app, _ = app_for(kit)
    status, _, body = request(app, path=path)
    assert status == 400 and body == b'{"error":"invalid request"}'


def test_unknown_routes_do_not_serve_files(kit):
    app, _ = app_for(kit)
    for path in ('/anything', '/api/review', '/data/gold/s10/judge_file_v1.yaml',
                 '/codebook/../second_rater.py'):
        status, _, body = request(app, path=path)
        assert status == 404 and body == b'{"error":"not found"}'
    assert request(app, 'POST', '/anything', headers=save_headers(app), payload=answer(app))[0] == 404


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_page_self_contained_controls_keyboard_and_empty_selection(kit):
    app, _ = app_for(kit)
    status, headers, raw = request(app)
    assert status == 200 and headers['Content-Type'] == 'text/html; charset=utf-8'
    page = raw.decode('utf-8')
    parser = PageParser()
    parser.feed(page)
    buttons = [attrs for tag, attrs in parser.elements if tag == 'button' and 'data-label' in attrs]
    assert [button['data-label'] for button in buttons] == [NA, R, M]
    assert all(button['aria-pressed'] == 'false' and 'selected' not in button.get('class', '')
               for button in buttons)
    note = next(attrs for tag, attrs in parser.elements if tag == 'input' and attrs.get('id') == 'note')
    unsure = next(attrs for tag, attrs in parser.elements if tag == 'input' and attrs.get('id') == 'unsure')
    assert note['type'] == 'text' and note['maxlength'] == '200'
    assert unsure['type'] == 'checkbox' and 'checked' not in unsure
    links = [attrs for tag, attrs in parser.elements if tag == 'a']
    assert links == [dict(href='/codebook', target='_blank', rel='noopener')]
    assert not any(tag in ('iframe', 'img', 'link') or (tag == 'script' and 'src' in attrs)
                   for tag, attrs in parser.elements)
    for forbidden in ('localStorage', 'sessionStorage', 'indexedDB', 'document.cookie',
                      PRIVATE, JID, 'j_id', 'cell_id', 'confidence', 'reason', 'stratum'):
        assert forbidden not in page
    for key in ("'1':'not_applicable'", "'2':'recommended'", "'3':'mandatory'",
                "e.key.toLowerCase()==='u'", "e.key==='ArrowLeft'", "e.key==='ArrowRight'"):
        assert key in page
    assert "e.target===$('note')" in page
    assert "selected=d.label||''" in page
    assert "$('unsure').checked=d.unsure_guideline_gap==='yes'" in page
    assert "$('note').value=d.note||''" in page
    assert "if(!await persist())return" in page
    assert 'overflow:auto' in page and 'height:100dvh' in page
    assert "$('content').textContent=d.content" in page
    assert "'X-Session-Token':TOKEN" in page


@pytest.mark.parametrize('open_browser', [True, False])
def test_server_binds_only_loopback_console_and_browser_option(kit, open_browser, monkeypatch):
    app, _ = app_for(kit)
    addresses, browsers, output, lifecycle = [], [], [], []

    class Server:
        def __init__(self, address, handler):
            addresses.append(address)
            self.server_address = ('127.0.0.1', PORT)
            assert issubclass(handler, FT.BaseHTTPRequestHandler)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            lifecycle.append('closed')

        def serve_forever(self):
            lifecycle.append('served')
            raise KeyboardInterrupt

    monkeypatch.setattr(FT, 'HTTPServer', Server)
    monkeypatch.setattr(FT.webbrowser, 'open', browsers.append)
    FT.serve(app, open_browser=open_browser, out=output.append)
    assert addresses == [('127.0.0.1', 8767)]
    assert browsers == (['http://127.0.0.1:8767/'] if open_browser else [])
    assert output == ['http://127.0.0.1:8767/', 'answered 0 of 3']
    assert lifecycle == ['served', 'closed']


@pytest.mark.parametrize('options,port,browser', [([], 8767, True), (['--no-browser'], 8767, False),
                                               (['--port', '9123'], 9123, True)])
def test_cli_dispatch(kit, options, port, browser, monkeypatch):
    calls, output, errors = [], [], []

    def serve(app, passed_port, open_browser, *, out):
        assert app.root == kit.root and app.sheet == 'coldread'
        calls.append((passed_port, open_browser))
        out('http://127.0.0.1:8767/')
        out('answered 0 of 3')

    monkeypatch.setattr(FT, 'serve', serve)
    assert FT.main(['serve', '--sheet', 'coldread', *options], root=kit.root,
                   out=output.append, err=errors.append) == 0
    assert calls == [(port, browser)] and errors == []
    assert output == ['http://127.0.0.1:8767/', 'answered 0 of 3']


@pytest.mark.parametrize('port', ['0', '-1', '65536'])
def test_invalid_port_refuses_without_writes(kit, port):
    before = snapshot(kit.root)
    out, err = [], []
    assert FT.main(['serve', '--sheet', 'main', '--port', port], root=kit.root,
                   out=out.append, err=err.append) == 2
    assert out == [] and err == [FT.REFUSAL] and snapshot(kit.root) == before


def test_empty_coldread_selection(kit, monkeypatch):
    monkeypatch.setattr(SR, '_coldread', lambda root: (dict(cells=[], reference={}, population=0), {}))
    put(kit.root, SR.COLD_SHEET_REL, SR.render_sheet([], {}))
    put(kit.root, SR.COLD_LABELS_REL, HEADER)
    app, _ = app_for(kit, 'coldread')
    assert app.state() == dict(total=0, answered=0, first_unanswered=None)
    assert request(app, path='/api/state')[0] == 200
    assert request(app, path='/api/cell?index=0')[0] == 400
