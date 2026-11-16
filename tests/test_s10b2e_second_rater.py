"""Friend kit checks use synthetic inputs beneath the repository basetemp only."""
from collections import Counter
import csv
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import random

import pytest
import yaml

from eval.s10 import draws as D
from eval.s10 import label_tool as LT
from eval.s10 import second_rater as SR

REPO = Path(__file__).resolve().parents[1]
NOW = '2026-11-16T12:00:00+00:00'
NA, R, M = 'not_applicable', 'recommended', 'mandatory'
JID = 'J-FAKE-PRIVATE'
LEAK = 'PRIVATE-CONTENT-MARKER'
FIELDS = ('sheet_id', 'label', 'unsure_guideline_gap', 'note')


@pytest.fixture(autouse=True)
def fake_only(monkeypatch):
    """Refuse real gold reads even if a future loader accidentally drops root."""
    monkeypatch.setenv('REGCLAW_STUB', '1')
    original = Path.open
    forbidden = REPO / 'data/gold/s10'

    def guarded(path, *args, **kwargs):
        if path.resolve().is_relative_to(forbidden.resolve()):
            raise AssertionError('real S10 access forbidden')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guarded)


@pytest.fixture
def root(tmp_path):
    # The documented --basetemp stays inside the repository; never write elsewhere.
    assert tmp_path.resolve().is_relative_to(REPO.resolve())
    return tmp_path / 'fake-root'


def sha(raw):
    # Independent LF computation; do not use the production sha helper as the oracle.
    return hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()


def put(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = text.encode('utf-8')
    path.write_bytes(raw)
    return sha(raw)


def csv_text(fields, rows, ending='\n'):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator=ending)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def read_csv(path):
    return list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'), newline='')))


def profile():
    return dict(device_name='Synthetic device', j_id=LEAK, flags=[LEAK],
                labels={'A': LEAK}, derived={'value': LEAK}, components=[dict(
                    name='Synthetic component', contact_nature='surface', contact_site='intact_skin',
                    exposure=dict(pattern='single', hours_per_use=1),
                    materials=[dict(name='Synthetic polymer', material_class='polymer')],
                    existing_evidence={'value': LEAK}, confidence=LEAK)])


def population():
    rows = []
    # Audited: 60 M + 60 NA + 30 R = 150. Unaudited: 60 M + 50 NA + 7 R + 3 split = 120.
    for label, count in ((M, 60), (NA, 60), (R, 30), (M, 60), (NA, 50), (R, 7), (R, 3)):
        for _ in range(count):
            item = f'FAKE-ITEM-{len(rows):03d}'
            rows.append(dict(cell_id=f'{JID}|{item}', j_id=JID, item=item,
                             labels=dict.fromkeys('ABC', label), confidences=dict.fromkeys('ABC', 'high'),
                             final=label, routed=False, route_reasons=[]))
    for row in rows[-3:]:
        row['labels'] = dict(A=NA, B=R, C=M)
    audit = [dict(order=i, j_id=r['j_id'], item=r['item'], family='FAKE')
             for i, r in enumerate(rows[:150], 1)]
    return rows, audit


def setup_friend(root):
    rows, audit = population()
    entry = dict(j_id=JID, contact_category='surface/intact_skin', duration='A',
                 profile=profile(), items=[r['item'] for r in rows])
    judge_sha = put(root, D.JUDGE_FILE_REL, yaml.safe_dump(dict(entries=[entry])))
    audit_sha = put(root, D.LIST_REL, csv_text(('order', 'j_id', 'item', 'family'), audit))
    put(root, D.BUILD_MANIFEST_REL, json.dumps(dict(files={
        D.JUDGE_FILE_REL: dict(sha256_lf=judge_sha), D.LIST_REL: dict(sha256_lf=audit_sha)})))
    put(root, D.MERGE_REL, json.dumps(dict(format='s10-merge.v1', status='OK',
                                        judge_file_sha256_lf=judge_sha, rows=rows)))
    setup_codebook(root)
    return rows, audit


def setup_codebook(root):
    book_sha = put(root, SR.CODEBOOK_REL, '# Frozen synthetic codebook\n')
    put(root, SR.CODEBOOK_MANIFEST_REL, json.dumps(dict(files={SR.CODEBOOK_REL: dict(sha256_lf=book_sha)})))


def go(root, command, *args):
    out, err = [], []
    code = SR.main([command, *args], root=root, now=NOW, out=out.append, err=err.append)
    console = '\n'.join(out + err)
    for forbidden in (JID, LEAK, 'FAKE-ITEM-', 'Synthetic device', 'PR-1', 'ISO-FAKE-'):
        assert forbidden not in console
    return code, out, err


def ready_friend(root):
    setup_friend(root)
    assert go(root, 'draw')[0] == 0
    assert go(root, 'sheet')[0] == 0
    fill_labels(root / SR.LABELS_REL)
    return sha((root / SR.DRAW_REL).read_bytes())


def fill_labels(path, labels=None):
    rows = read_csv(path)
    for i, row in enumerate(rows):
        row.update(label=labels[i] if labels else R, unsure_guideline_gap='', note='')
    path.write_bytes(csv_text(FIELDS, rows).encode('utf-8'))
    return rows


def test_strata_counts_forced_inclusion_and_population_weights():
    rows, audit = population()
    doc = SR.friend_draw(rows, audit)
    expected = {
        'audited_mandatory': (60, 25, 0), 'audited_not_applicable': (60, 20, 0),
        'audited_recommended': (30, 5, 0), 'unaudited_mandatory': (60, 50, 0),
        'unaudited_not_applicable': (50, 40, 0), 'unaudited_recommended': (7, 7, 0),
        'unaudited_split': (3, 3, 3),
    }
    # Selected = 25 + 20 + 5 + 50 + 40 + 7 + 3 = 150; population = 150 + 120 = 270.
    assert len(doc['cells']) == 150
    assert sum(s['population'] for s in doc['strata'].values()) == 270
    for name, (pop, n, forced) in expected.items():
        assert doc['strata'][name] == dict(population=pop, n=n, forced_count=forced)
        selected = [c for c in doc['cells'] if c['stratum'] == name]
        assert len(selected) == n
        assert all(0 < c['inclusion_probability'] <= 1 for c in selected)
        # n observations of weight population/n sum to population in each stratum.
        assert sum(1 / c['inclusion_probability'] for c in selected) == pytest.approx(pop)
    forced = {c['cell_id']: c for c in doc['cells'] if c['stratum'] == 'unaudited_split'}
    assert set(forced) == {r['cell_id'] for r in rows[-3:]}
    assert all(c['inclusion_probability'] == 1 for c in forced.values())


def test_seed_replay_input_order_and_sheet_ids():
    rows, audit = population()
    doc = SR.friend_draw(rows, audit)
    # Build candidate sets independently from the fixture's consecutive population blocks.
    ranges = {'audited_mandatory': (0, 60, 25), 'audited_not_applicable': (60, 120, 20),
              'audited_recommended': (120, 150, 5), 'unaudited_mandatory': (150, 210, 50),
              'unaudited_not_applicable': (210, 260, 40), 'unaudited_recommended': (260, 267, 7),
              'unaudited_split': (267, 270, 3)}
    chosen = []
    for name, (lo, hi, n) in ranges.items():
        candidates = sorted(r['cell_id'] for r in rows[lo:hi])
        chosen.extend(random.Random(f'S10b-A2|friend|{name}').sample(candidates, n))
    chosen.sort()
    random.Random('S10b-A2|friend-order').shuffle(chosen)
    assert [c['cell_id'] for c in doc['cells']] == chosen
    assert [c['sheet_id'] for c in doc['cells']] == [f'S-{i:03d}' for i in range(1, 151)]
    assert SR.friend_draw(list(reversed(rows)), list(reversed(audit))) == doc


def test_small_stratum_take_all_and_empty_strata():
    rows, audit = population()
    # Keep audited prefix plus two unaudited M cells: 150 + 2 cells in the population.
    doc = SR.friend_draw(rows[:152], audit)
    assert doc['strata']['unaudited_mandatory'] == dict(population=2, n=2, forced_count=0)
    assert doc['strata']['unaudited_split'] == dict(population=0, n=0, forced_count=0)
    assert all(c['inclusion_probability'] == 1 for c in doc['cells'] if c['stratum'] == 'unaudited_mandatory')


def test_audited_split_stays_in_audited_class():
    rows, audit = population()
    rows[0]['labels'] = dict(A=NA, B=R, C=M)
    doc = SR.friend_draw(rows, audit)
    # Only the three unaudited split cells are forced; audited M population remains 60.
    assert doc['strata']['unaudited_split']['population'] == 3
    assert doc['strata']['audited_mandatory']['population'] == 60


def test_null_final_refuses_before_audit_read(root, monkeypatch):
    setup_friend(root)
    path = root / D.MERGE_REL
    doc = json.loads(path.read_bytes())
    doc['rows'][0].update(final=None, routed=True)
    path.write_bytes(json.dumps(doc).encode('utf-8'))

    def forbidden(*args):
        raise AssertionError('audit list must not be read after null-final refusal')

    monkeypatch.setattr(D, 'load_audit_rows', forbidden)
    code, out, err = go(root, 'draw')
    assert code == 2 and out == []
    assert err == ["REFUSED: 1 routed cells without a final label; the friend draw needs every cell's final label"]
    assert not (root / SR.DRAW_REL).exists()
    assert not (root / SR.DRAW_REL).parent.exists()


@pytest.mark.parametrize('bad', ['duplicate', 'invalid-final', 'invalid-judge', 'missing-audit'])
def test_bad_merge_is_input_error(root, bad):
    setup_friend(root)
    path = root / D.MERGE_REL
    doc = json.loads(path.read_bytes())
    if bad == 'duplicate':
        doc['rows'].append(dict(doc['rows'][0]))
    elif bad == 'invalid-final':
        doc['rows'][0]['final'] = LEAK
    elif bad == 'invalid-judge':
        doc['rows'][0]['labels']['A'] = LEAK
    else:
        doc['rows'].pop(0)
    path.write_bytes(json.dumps(doc).encode('utf-8'))
    assert go(root, 'draw')[0] == 2
    assert not (root / SR.DRAW_REL).exists()


def test_draw_prints_stratum_counts_total_and_independent_sha(root):
    setup_friend(root)
    code, out, err = go(root, 'draw')
    assert code == 0 and err == []
    # Seven strata, then total count, then output sha = nine lines.
    assert len(out) == 9
    assert 'cells=150' in out
    assert 'unaudited_recommended population=7 n=7 forced_count=0 take_all=1' in out
    assert 'unaudited_split population=3 n=3 forced_count=3 take_all=1' in out
    raw = (root / SR.DRAW_REL).read_bytes()
    assert b'\r' not in raw
    assert out[-1] == f'{SR.DRAW_REL} sha256_lf={sha(raw)}'
    assert SR.checked_draw(raw) == json.loads(raw)


def test_draw_prints_small_stratum_take_all(root):
    setup_friend(root)
    path = root / D.MERGE_REL
    doc = json.loads(path.read_bytes())
    doc['rows'] = doc['rows'][:152]
    path.write_bytes(json.dumps(doc).encode('utf-8'))
    code, out, err = go(root, 'draw')
    assert code == 0 and err == []
    assert 'unaudited_mandatory population=2 n=2 forced_count=0 take_all=1' in out
    assert 'cells=52' in out  # Audited 25 + 20 + 5, plus both unaudited M cells = 52.


def test_sheet_blind_fields_order_lf_and_shas(root):
    setup_friend(root)
    go(root, 'draw')
    code, out, err = go(root, 'sheet')
    assert code == 0 and err == []
    draw = json.loads((root / SR.DRAW_REL).read_bytes())
    md = (root / SR.SHEET_REL).read_text(encoding='utf-8')
    rows = read_csv(root / SR.LABELS_REL)
    assert tuple(rows[0]) == FIELDS
    assert [r['sheet_id'] for r in rows] == [c['sheet_id'] for c in draw['cells']]
    assert [line[3:] for line in md.splitlines() if line.startswith('## ')] == [r['sheet_id'] for r in rows]
    assert [line for line in md.splitlines() if line.startswith('FAKE-ITEM-')] == [c['cell_id'].split('|')[1] for c in draw['cells']]
    for word in (JID, LEAK, 'stratum', 'confidence', 'routed', 'reason', 'existing_evidence', 'derived', NA, R, M):
        assert word not in md
    assert 'CODEBOOK_v1.0.md' in md and 'v0.1' not in md
    assert 'yes when' in md and 'yeswhen' not in md
    assert md.count('Synthetic device') == len(rows)
    assert all(r == dict(sheet_id=r['sheet_id'], label='', unsure_guideline_gap='', note='') for r in rows)
    for rel in (SR.SHEET_REL, SR.LABELS_REL):
        raw = (root / rel).read_bytes()
        assert b'\r' not in raw and not raw.startswith(b'\xef\xbb\xbf')
        assert f'{rel} sha256_lf={sha(raw)}' in out


@pytest.mark.parametrize('first', ['markdown', 'csv'])
def test_sheet_pair_preflight_writes_nothing_when_either_exists(root, first):
    setup_friend(root)
    go(root, 'draw')
    rel = SR.SHEET_REL if first == 'markdown' else SR.LABELS_REL
    other = SR.LABELS_REL if first == 'markdown' else SR.SHEET_REL
    put(root, rel, 'keep\n')
    assert go(root, 'sheet')[0] == 3
    assert (root / rel).read_bytes() == b'keep\n'
    assert not (root / other).exists()


@pytest.mark.parametrize('changed', ['judge', 'audit', 'codebook'])
def test_changed_frozen_input_refuses_without_outputs(root, changed):
    setup_friend(root)
    go(root, 'draw')
    rel = {'judge': D.JUDGE_FILE_REL, 'audit': D.LIST_REL, 'codebook': SR.CODEBOOK_REL}[changed]
    path = root / rel
    path.write_bytes(path.read_bytes() + b'\n')
    command = 'draw' if changed == 'audit' else 'sheet'
    if command == 'draw':
        # Use a separate fake root instead of deleting the already-created draw.
        root = root / 'other-fake-root'
        setup_friend(root)
        path = root / rel
        path.write_bytes(path.read_bytes() + b'\n')
    assert go(root, command)[0] == 2
    assert not (root / (SR.DRAW_REL if command == 'draw' else SR.SHEET_REL)).exists()


@pytest.mark.parametrize('change', ['zero-probability', 'duplicate-id', 'wrong-count', 'wrong-seed'])
def test_draw_validation_before_sheet_write(root, change):
    setup_friend(root)
    go(root, 'draw')
    path = root / SR.DRAW_REL
    doc = json.loads(path.read_bytes())
    if change == 'zero-probability':
        doc['cells'][0]['inclusion_probability'] = 0
    elif change == 'duplicate-id':
        doc['cells'][1]['cell_id'] = doc['cells'][0]['cell_id']
    elif change == 'wrong-count':
        doc['strata']['audited_mandatory']['n'] = 24
    else:
        doc['seed_strings']['sheet_order'] = LEAK
    path.write_bytes(json.dumps(doc).encode('utf-8'))
    assert go(root, 'sheet')[0] == 2
    assert not (root / SR.SHEET_REL).exists()
    assert not (root / SR.LABELS_REL).exists()


def test_freeze_manifest_content_lf_normalisation_and_write_once(root):
    expected = ready_friend(root)
    path = root / SR.LABELS_REL
    # Human spreadsheet saves may be BOM + CRLF; do not rewrite the source CSV.
    path.write_bytes(b'\xef\xbb\xbf' + path.read_bytes().replace(b'\n', b'\r\n'))
    before = path.read_bytes()
    code, out, err = go(root, 'freeze', '--expected-draw-sha', expected)
    assert code == 0 and err == []
    manifest_raw = (root / SR.MANIFEST_REL).read_bytes()
    doc = json.loads(manifest_raw)
    assert doc == dict(format='s10-friend-labels-manifest.v1', file=SR.LABELS_REL,
                       sha256_lf=sha(before), rows=150, valid_rows=150,
                       friend_set_sha256_lf=expected, sheet_sha256_lf=sha((root / SR.SHEET_REL).read_bytes()),
                       created_at=NOW)
    assert datetime.fromisoformat(doc['created_at']).utcoffset().total_seconds() == 0
    assert b'\r' not in manifest_raw
    assert all(word not in manifest_raw.decode('utf-8') for word in (JID, LEAK, NA, R, M, 'FAKE-ITEM-'))
    assert 'rows=150 valid_rows=150' in out
    assert out[-1] == f'{SR.MANIFEST_REL} sha256_lf={sha(manifest_raw)}'
    assert go(root, 'freeze', '--expected-draw-sha', expected)[0] == 3
    assert (root / SR.MANIFEST_REL).read_bytes() == manifest_raw
    assert path.read_bytes() == before


@pytest.mark.parametrize('expected', ['0' * 64, 'not-a-sha'])
def test_freeze_draw_sha_mismatch_writes_nothing(root, expected):
    ready_friend(root)
    assert go(root, 'freeze', '--expected-draw-sha', expected)[0] == 2
    assert not (root / SR.MANIFEST_REL).exists()


def test_draw_changed_since_sheet_refuses_even_with_new_expected_sha(root):
    ready_friend(root)
    path = root / SR.DRAW_REL
    doc = json.loads(path.read_bytes())
    # Swap cell identities while retaining anonymous positions: the old sheet is now inconsistent.
    doc['cells'][0], doc['cells'][1] = doc['cells'][1], doc['cells'][0]
    doc['cells'][0]['sheet_id'], doc['cells'][1]['sheet_id'] = 'S-001', 'S-002'
    path.write_bytes((json.dumps(doc) + '\n').encode('utf-8'))
    assert go(root, 'freeze', '--expected-draw-sha', sha(path.read_bytes()))[0] == 2
    assert not (root / SR.MANIFEST_REL).exists()


@pytest.mark.parametrize('bad', ['blank', 'invalid', 'gap', 'missing', 'extra', 'duplicate', 'header', 'columns'])
def test_freeze_rejects_invalid_labels_without_echoing(root, bad):
    expected = ready_friend(root)
    path = root / SR.LABELS_REL
    rows = read_csv(path)
    fields = FIELDS
    if bad == 'blank':
        rows[0]['label'] = ''
    elif bad == 'invalid':
        rows[0]['label'] = LEAK
    elif bad == 'gap':
        rows[0]['unsure_guideline_gap'] = LEAK
    elif bad == 'missing':
        rows.pop()
    elif bad == 'extra':
        rows.append(dict(rows[0]))
    elif bad == 'duplicate':
        rows[1]['sheet_id'] = rows[0]['sheet_id']
    elif bad == 'header':
        fields = FIELDS[::-1]
    text = csv_text(fields, rows)
    if bad == 'columns':
        text = text.replace('S-001,', 'S-001,extra,', 1)
    path.write_bytes(text.encode('utf-8'))
    before = path.read_bytes()
    assert go(root, 'freeze', '--expected-draw-sha', expected)[0] == 2
    assert not (root / SR.MANIFEST_REL).exists()
    assert path.read_bytes() == before


def test_freeze_accepts_reordered_complete_rows_and_gap_flags(root):
    expected = ready_friend(root)
    path = root / SR.LABELS_REL
    rows = read_csv(path)[::-1]
    rows[0].update(unsure_guideline_gap='yes', note='Synthetic uncertainty\nsecond line')
    rows[1]['unsure_guideline_gap'] = 'no'
    path.write_bytes(csv_text(FIELDS, rows).encode('utf-8'))
    assert go(root, 'freeze', '--expected-draw-sha', expected)[0] == 0


def setup_practice(root, eligible_count=24):
    setup_codebook(root)
    ids = ('PR-1', 'PR-2', 'PR-3', 'PR-4')
    parts = [f'ISO-FAKE-{i:02d}' for i in range(20)]
    entries = [dict(id=d, contact_category='surface/intact_skin', duration='A', profile=profile()) for d in ids]
    profile_sha = put(root, LT.PRACTICE_REL, yaml.safe_dump(dict(profiles=entries)))
    old_book_sha = put(root, LT.CODEBOOK_V01_REL, '# Synthetic practice codebook\n')
    put(root, LT.BUILD_MANIFEST_REL, json.dumps(dict(files={
        LT.PRACTICE_REL: dict(sha256_lf=profile_sha), LT.CODEBOOK_V01_REL: dict(sha256_lf=old_book_sha)})))
    put(root, 'standards/data/endpoint_to_part.v1.yaml', yaml.safe_dump(dict(parts=[dict(part=p) for p in parts])))
    # Synthetic permissive schema; validator still checks device, cells, sections, reasons and components.
    put(root, LT.SCHEMA_REL, '{}\n')
    keys = sorted((d, item) for d in ids for item in parts)
    eligible = set(keys[:eligible_count])
    label_of = {key: (NA, R, M)[i % 3] for i, key in enumerate(keys)}
    human_rows = []
    for d in ids:
        order = list(parts)
        random.Random(f'S10b-v1|hariom-practice|{d}').shuffle(order)
        for item in order:
            human_rows.append(dict(order=len(human_rows) + 1, device_id=d, item=item, label=label_of[d, item],
                                   confidence='high', reason='Synthetic practice reason', timestamp=NOW))
    put(root, LT.PRACTICE_LABELS_REL, csv_text(
        ('order', 'device_id', 'item', 'label', 'confidence', 'reason', 'timestamp'), human_rows))
    for j in 'ABC':
        for d in ids:
            cells = []
            for item in parts:
                label = label_of[d, item]
                if j == 'C' and (d, item) not in eligible:
                    label = M if label != M else NA
                cells.append(dict(item=item, label=label, confidence='high', reason='Synthetic reason',
                                  section='§1', needs_sme=''))
            result = dict(device_id=d, checklist=dict(components=[dict(name='Synthetic component')]), cells=cells)
            put(root, f'{LT.JUDGES_PRACTICE_REL}/{j}/{d}.result.json', json.dumps(result))
    return keys, eligible, label_of


def test_coldread_eligibility_seed_and_training_counts(root):
    keys, eligible, label_of = setup_practice(root)
    code, out, err = go(root, 'coldread-sheet')
    assert code == 0 and err == []
    assert out[0] == 'eligible=24 selected=20 take_all=0'
    selected = random.Random('S10b-A2|coldread').sample(sorted(f'{d}|{it}' for d, it in eligible), 20)
    md = (root / SR.COLD_SHEET_REL).read_text(encoding='utf-8')
    assert [line for line in md.splitlines() if line.startswith('ISO-FAKE-')] == [cid.split('|')[1] for cid in selected]
    for hidden in ('PR-', 'stratum', NA, R, M, LEAK, 'confidence', 'reason', 'v0.1'):
        assert hidden not in md
    assert 'CODEBOOK_v1.0.md' in md
    assert 'yes when' in md and 'yeswhen' not in md
    expected_ids = [f'C-{i:03d}' for i in range(1, 21)]
    assert [row['sheet_id'] for row in read_csv(root / SR.COLD_LABELS_REL)] == expected_ids
    assert [line[3:] for line in md.splitlines() if line.startswith('## ')] == expected_ids
    assert 'S-' not in md
    for rel in (SR.COLD_SHEET_REL, SR.COLD_LABELS_REL):
        raw = (root / rel).read_bytes()
        assert b'\r' not in raw and f'{rel} sha256_lf={sha(raw)}' in out
    refs = [label_of[tuple(cid.split('|', 1))] for cid in selected]
    labels = refs[:]
    labels[0] = M if refs[0] != M else NA
    fill_labels(root / SR.COLD_LABELS_REL, labels)
    code, out, err = go(root, 'coldread-compare')
    assert code == 0 and err == []
    # Independent confusion counts over paired synthetic reference/response values.
    table = Counter(zip(refs, labels))
    expected = []
    for label in (NA, R, M):
        agree = table[label, label]
        disagree = sum(n for (ref, answer), n in table.items() if ref == label and answer != label)
        expected.append(f'{label} agree={agree} disagree={disagree}')
    assert out == expected
    assert sum(table.values()) == 20  # 19 agree + one deliberately changed response.
    assert sum(n for (a, b), n in table.items() if a != b) == 1
    assert go(root, 'coldread-sheet')[0] == 3


def test_coldread_take_all_per_class_counts(root):
    setup_practice(root, eligible_count=3)
    code, out, _ = go(root, 'coldread-sheet')
    assert code == 0 and out[0] == 'eligible=3 selected=3 take_all=1'
    # The first three eligible references are NA, R, M: one cell of each class.
    chosen = random.Random('S10b-A2|coldread').sample(
        [f'PR-1|ISO-FAKE-{i:02d}' for i in range(3)], 3)
    reference = {f'PR-1|ISO-FAKE-{i:02d}': label for i, label in enumerate((NA, R, M))}
    fill_labels(root / SR.COLD_LABELS_REL, [reference[cid] for cid in chosen])
    assert go(root, 'coldread-compare') == (0, [f'{label} agree=1 disagree=0' for label in (NA, R, M)], [])


def test_coldread_empty_eligibility(root):
    setup_practice(root, eligible_count=0)
    code, out, _ = go(root, 'coldread-sheet')
    assert code == 0 and out[0] == 'eligible=0 selected=0 take_all=1'
    assert go(root, 'coldread-compare') == (0, [f'{label} agree=0 disagree=0' for label in (NA, R, M)], [])


def test_coldread_no_probe_access_and_incomplete_sources_refuse(root, monkeypatch):
    setup_practice(root)
    original = Path.open

    def guarded(path, *args, **kwargs):
        if 'probes' in path.name or path.name.startswith('PR-5'):
            raise AssertionError('probes forbidden')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guarded)
    path = root / LT.JUDGES_PRACTICE_REL / 'C/PR-1.result.json'
    path.write_bytes(b'{}\n')
    assert go(root, 'coldread-sheet')[0] == 2
    assert not (root / SR.COLD_SHEET_REL).exists()
    assert not (root / SR.COLD_LABELS_REL).exists()


def test_coldread_human_disagreement_not_eligible(root):
    setup_practice(root, eligible_count=3)
    path = root / LT.PRACTICE_LABELS_REL
    rows = read_csv(path)
    for row in rows:
        if row['device_id'] == 'PR-1' and row['item'] == 'ISO-FAKE-00':
            row['label'] = M  # All judges say NA; human differs, so only two eligible cells remain.
    path.write_bytes(csv_text(tuple(rows[0]), rows).encode('utf-8'))
    assert go(root, 'coldread-sheet')[1][0] == 'eligible=2 selected=2 take_all=1'


def test_coldread_compare_incomplete_labels_prints_no_counts(root):
    setup_practice(root)
    go(root, 'coldread-sheet')
    code, out, err = go(root, 'coldread-compare')
    assert code == 2 and out == [] and err


def test_exclusive_create_race_refuses_without_overwrite(root, monkeypatch):
    setup_friend(root)
    original = Path.open

    def race(path, mode='r', *args, **kwargs):
        if path == root / SR.DRAW_REL and mode == 'x':
            with original(path, 'xb') as stream:
                stream.write(b'other writer\n')
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', race)
    code, out, err = go(root, 'draw')
    assert code == 3 and out == [] and 'write-once' in err[0]
    assert (root / SR.DRAW_REL).read_bytes() == b'other writer\n'


def test_draw_write_once_precedes_source_read(root, monkeypatch):
    put(root, SR.DRAW_REL, 'keep\n')

    def forbidden(*args):
        raise AssertionError('existing output must be checked first')

    monkeypatch.setattr(D, 'load_merge', forbidden)
    assert go(root, 'draw')[0] == 3
    assert (root / SR.DRAW_REL).read_bytes() == b'keep\n'


def test_cli_requires_expected_sha_and_does_not_offer_lock(root):
    for args in (['freeze'], ['label'], ['lock']):
        with pytest.raises(SystemExit) as exc:
            SR.main(args, root=root)
        assert exc.value.code == 2
    assert not root.exists()
