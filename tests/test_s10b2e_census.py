"""Counts-only census checks on hand-computed, synthetic merge rows."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from eval.s10 import census as C
from eval.s10 import draws as D
from tests import test_s10b2e_second_rater as FK

NA, R, M = 'not_applicable', 'recommended', 'mandatory'
REPO = Path(__file__).resolve().parents[1]
PRIVATE = 'J-FAKE-CENSUS-PRIVATE'


@pytest.fixture(autouse=True)
def fake_only(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path.resolve().is_relative_to((REPO / 'data/gold/s10').resolve()):
            raise AssertionError('real S10 access forbidden')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guarded)


@pytest.fixture
def root(tmp_path):
    assert tmp_path.resolve().is_relative_to(REPO.resolve())
    return tmp_path / 'fake-census-root'


def row(name, final, labels):
    item = f'PRIVATE-ITEM-{name}'
    return dict(cell_id=f'{PRIVATE}|{item}', j_id=PRIVATE, item=item, final=final,
                labels=dict(zip('ABC', labels)), confidences=dict.fromkeys('ABC', 'high'),
                routed=False, route_reasons=[])


def population():
    rows = [row('a', NA, (NA, NA, NA)), row('b', R, (NA, R, R)),
            row('c', M, (M, NA, M)), row('d', M, (R, NA, M)),
            row('e', NA, (NA, NA, R)), row('f', R, (R, R, R))]
    rows[2]['confidences']['C'] = 'medium'
    audit = [dict(order=i, j_id=rows[k]['j_id'], item=rows[k]['item'], family='FAKE')
             for i, k in enumerate((4, 2), 1)]
    return rows, audit


def setup(root):
    rows, audit = population()
    audit_sha = FK.put(root, D.LIST_REL, FK.csv_text(('order', 'j_id', 'item', 'family'), audit))
    # The loader compares the recorded judge sha; it does not open a judge profile file.
    judge_sha = 'a' * 64
    FK.put(root, D.BUILD_MANIFEST_REL, json.dumps(dict(files={
        D.JUDGE_FILE_REL: dict(sha256_lf=judge_sha), D.LIST_REL: dict(sha256_lf=audit_sha)})))
    FK.put(root, D.MERGE_REL, json.dumps(dict(format='s10-merge.v1', status='OK',
                                           judge_file_sha256_lf=judge_sha, rows=rows)))
    return rows, audit


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def cli(root):
    out, err = [], []
    code = C.main([], root=root, out=out.append, err=err.append)
    console = '\n'.join(out + err)
    for forbidden in (PRIVATE, 'PRIVATE-ITEM-', 'cell_id', 'route_reasons', 'medium'):
        assert forbidden not in console
    return code, out, err


def test_hand_census_every_requested_count_and_no_mutation():
    rows, audit = population()
    before = deepcopy((rows, audit))
    got = C.census_counts(rows, audit)
    # Six cells: NA=a,e; R=b,f; M=c,d. Audited e,c => NA=1,R=0,M=1.
    # Splits b,c,d,e => K=4; both audited cells split. Odd A=b,B=c,C=e.
    # b,d,e all-high => 3; only d has all three distinct labels.
    assert got == dict(cells=6, merged_classes={NA: 2, R: 2, M: 2}, audited_cells=2,
                       audited_classes={NA: 1, R: 0, M: 1}, split_cells=4, audited_split_cells=2,
                       split_patterns={(1, 2, 0): 1, (1, 0, 2): 1, (1, 1, 1): 1, (2, 1, 0): 1},
                       odd_judge_out={'A': 1, 'B': 1, 'C': 1}, all_high_split_cells=3, three_way_splits=1)
    assert (rows, audit) == before


def test_only_first_150_positions_are_audited():
    # 150 mandatory cells followed by one NA cell; position 151 must be outside the prefix.
    rows = [row(str(i), M, (M, M, M)) for i in range(150)] + [row('last', NA, (NA, NA, NA))]
    audit = [dict(order=i, j_id=r['j_id'], item=r['item']) for i, r in enumerate(rows, 1)]
    got = C.census_counts(rows, list(reversed(audit)))
    assert got['cells'] == 151 and got['audited_cells'] == 150
    assert got['merged_classes'] == {NA: 1, R: 0, M: 150}
    assert got['audited_classes'] == {NA: 0, R: 0, M: 150}


def test_cli_counts_only_and_no_writes(root):
    setup(root)
    before = snapshot(root)
    code, out, err = cli(root)
    assert code == 0 and err == [] and snapshot(root) == before
    assert out == [
        'cells=6 audited_cells=2',
        'class=not_applicable overall=2 audited=1',
        'class=recommended overall=2 audited=0',
        'class=mandatory overall=2 audited=1',
        'K_split_cells=4 audited_split_cells=2',
        'split_pattern_order=not_applicable,recommended,mandatory',
        'split_pattern=(1, 0, 2) count=1',
        'split_pattern=(1, 1, 1) count=1',
        'split_pattern=(1, 2, 0) count=1',
        'split_pattern=(2, 1, 0) count=1',
        'odd_judge_out=A count=1', 'odd_judge_out=B count=1', 'odd_judge_out=C count=1',
        'all_high_split_cells=3 three_way_splits=1',
    ]


def test_null_final_refuses_before_audit_load(root, monkeypatch):
    setup(root)
    path = root / D.MERGE_REL
    doc = json.loads(path.read_bytes())
    doc['rows'][0].update(final=None, routed=True)
    path.write_bytes(json.dumps(doc).encode('utf-8'))

    def forbidden(*args):
        raise AssertionError('null-final refusal must precede audit read')

    monkeypatch.setattr(D, 'load_audit_rows', forbidden)
    before = snapshot(root)
    code, out, err = cli(root)
    assert code == 2 and out == [] and snapshot(root) == before
    assert err == ["REFUSED: 1 routed cells without a final label; the friend draw needs every cell's final label"]


@pytest.mark.parametrize('bad', ['duplicate-cell', 'invalid-label', 'invalid-confidence', 'missing-judge', 'wrong-status'])
def test_cli_bad_merge_never_echoes_contents(root, bad):
    setup(root)
    path = root / D.MERGE_REL
    doc = json.loads(path.read_bytes())
    if bad == 'duplicate-cell':
        doc['rows'].append(dict(doc['rows'][0]))
    elif bad == 'invalid-label':
        doc['rows'][0]['labels']['A'] = PRIVATE
    elif bad == 'invalid-confidence':
        doc['rows'][0]['confidences']['A'] = PRIVATE
    elif bad == 'missing-judge':
        doc['rows'][0]['labels'].pop('C')
    else:
        doc['status'] = PRIVATE
    path.write_bytes(json.dumps(doc).encode('utf-8'))
    before = snapshot(root)
    code, out, err = cli(root)
    assert code == 2 and out == [] and err and snapshot(root) == before


@pytest.mark.parametrize('bad', ['changed-sha', 'position-gap', 'duplicate-cell', 'missing-cell'])
def test_cli_bad_audit_list(root, bad):
    _, audit = setup(root)
    if bad == 'position-gap':
        audit[1]['order'] = 3
    elif bad == 'duplicate-cell':
        audit[1].update(j_id=audit[0]['j_id'], item=audit[0]['item'])
    elif bad == 'missing-cell':
        audit[1]['item'] = 'PRIVATE-ITEM-absent'
    path = root / D.LIST_REL
    path.write_bytes(FK.csv_text(('order', 'j_id', 'item', 'family'), audit).encode('utf-8'))
    if bad == 'changed-sha':
        path.write_bytes(path.read_bytes() + b'\n')
    else:
        manifest_path = root / D.BUILD_MANIFEST_REL
        manifest = json.loads(manifest_path.read_bytes())
        manifest['files'][D.LIST_REL]['sha256_lf'] = FK.sha(path.read_bytes())
        manifest_path.write_bytes(json.dumps(manifest).encode('utf-8'))
    before = snapshot(root)
    code, out, err = cli(root)
    assert code == 2 and out == [] and err and snapshot(root) == before
