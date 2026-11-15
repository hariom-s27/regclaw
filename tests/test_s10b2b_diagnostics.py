"""S10b2b-09/-10/-11: C position diagnostic, stability self-agreement, intra-rater kappa (fakes only)."""
import csv
from datetime import timedelta
from fractions import Fraction as F
import io
import json
import math

import pytest

import _netguard
import _s10b2b_fake as FK
from eval.s10 import diagnostics as DG
from eval.s10 import draws as D
from eval.s10 import sheets as SH


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')


N, R, M = FK.N, FK.R, FK.M


def mrow(j, item, a, b, c):
    return dict(cell_id=f'{j}|{item}', j_id=j, item=item, labels=dict(A=a, B=b, C=c))


def test_c_position_hand_counts():
    assign = [dict(j_id='J-001', batch=1, position=1), dict(j_id='J-002', batch=1, position=2)]
    rows = [mrow('J-001', 'a', M, M, M), mrow('J-001', 'b', R, R, N), mrow('J-001', 'c', R, N, R),  # 2 agree, 1 equal
            mrow('J-002', 'a', N, N, N), mrow('J-002', 'b', N, N, None)]                          # 2 agree, 1 equal, 1 missing
    out = DG.c_position(rows, assign)
    assert out[0] == dict(position=1, ab_agree_cells=2, c_equal=1, c_missing=0, share_c_equal=0.5)
    assert out[1] == dict(position=2, ab_agree_cells=2, c_equal=1, c_missing=1, share_c_equal=0.5)
    assert [o['position'] for o in out] == [1, 2, 3, 4, 5] and math.isnan(out[2]['share_c_equal'])


def res(j_id, labels):
    return dict(device_id=j_id, cells=[dict(item=i, label=l) for i, l in labels.items()])


def test_stability_counts_only_scored_items():
    rows = [mrow('J-001', 'a', M, M, M), mrow('J-001', 'b', R, R, R), mrow('J-001', 'c', N, N, N)]
    devices = [dict(j_id='J-001', scored_items=['a', 'b'])]
    stab = {'A': {'J-001': res('J-001', dict(a=M, b=N, c=M))},      # a same, b differs; c is not scored
            'B': {'J-001': res('J-001', dict(a=M, b=R, c=M))},      # both same
            'C': {'J-001': None}}                                   # no re-label result
    out = DG.stability_self_agreement(rows, stab, devices)
    assert out['metric'] == 'self-agreement under reversed item order' and out['labels'] == '3-class'
    assert out['per_judge']['A'] == dict(scored_cells=2, both_labelled=2, identical=1, missing_main=0,
                                         missing_relabel=0, share_identical=0.5)
    assert out['per_judge']['B']['share_identical'] == 1.0
    assert out['per_judge']['C']['both_labelled'] == 0 and out['per_judge']['C']['missing_relabel'] == 2
    assert math.isnan(out['per_judge']['C']['share_identical'])
    assert set(out['per_judge']['A']) == {'scored_cells', 'both_labelled', 'identical', 'missing_main',
                                          'missing_relabel', 'share_identical'}         # no kappa


def test_intra_rater_binary_kappa_hand_value():
    # 10 cells over 5 profiles; binary table (mandatory first): a=3 b=1 c=1 d=5
    pairs = [(M, M), (M, M), (M, M), (M, R), (N, M), (N, N), (R, N), (R, R), (N, N), (R, N)]
    cells = [dict(cell_id=f'J-00{k // 2 + 1}|i{k}', j_id=f'J-00{k // 2 + 1}') for k in range(10)]
    first = {c['cell_id']: p[0] for c, p in zip(cells, pairs)}
    again = {c['cell_id']: p[1] for c, p in zip(cells, pairs)}
    out = DG.intra_rater(first, again, cells, n_boot=300)
    a, b, c, d = 3, 1, 1, 5
    assert out['kappa'] == pytest.approx(float(F(2 * (a * d - b * c), (a + b) * (b + d) + (a + c) * (c + d))))
    assert out['cells'] == 10 and out['n_profiles'] == 5 and out['ci_label'] == 'profile-cluster bootstrap (J-id)'
    assert out['lo'] <= out['kappa'] <= out['hi']
    assert 'po' not in out and 'Po' not in json.dumps(out)


# ---- CLIs on a fake repo -------------------------------------------------------------------------------
def test_cli_c_position_and_stability(tmp_path):
    root = FK.build_repo(tmp_path)
    for name in ('c-batches', 'stability'):
        assert D.run(root, name, out=lambda s: None) == 0
    lines = []
    assert DG.run(root, 'c-position', out=lines.append) == 0
    assert sum(l.startswith('position ') for l in lines) == 5
    lines = []
    assert DG.run(root, 'stability', out=lines.append) == 0                # no re-label results yet: counted missing
    doc = json.loads((root / DG.DIAG_REL / 'stability.json').read_text(encoding='utf-8'))
    assert doc['result']['per_judge']['A']['scored_cells'] == 60
    assert doc['result']['per_judge']['A']['missing_relabel'] == 60
    assert not any('J-0' in l or 'mandatory' in l for l in lines)
    assert DG.run(root, 'stability', out=lambda s: None) == 3


def retest_setup(tmp_path, *, days):
    root = FK.build_repo(tmp_path)
    assert D.run(root, 'retest', out=lambda s: None) == 0
    assert SH.run(root, 'retest-sheet', out=lambda s: None) == 0
    path = root / SH.SHEETS_REL / SH.FILES['retest'][1]
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
    stamp = (FK.T0 + timedelta(minutes=150, days=days)).isoformat()
    for r in rows:
        r['label'] = FK.human_label(f'{r["j_id"]}|{r["item"]}')
        r['timestamp'] = stamp
    return root, path, rows


def test_cli_intra_rater_ok_and_refusals(tmp_path):
    root, path, rows = retest_setup(tmp_path / 'early', days=6)
    FK.write_csv(path, SH.LABEL_FIELDS, rows, bom_crlf=True)
    lines = []
    assert DG.run(root, 'intra-rater', out=lines.append) == 1
    assert any('before the earliest allowed date' in l for l in lines)
    root, path, rows = retest_setup(tmp_path / 'missing', days=8)
    FK.write_csv(path, SH.LABEL_FIELDS, rows[:-1], bom_crlf=True)
    assert DG.run(root, 'intra-rater', out=lambda s: None) == 1
    root, path, rows = retest_setup(tmp_path / 'ok', days=8)
    FK.write_csv(path, SH.LABEL_FIELDS, rows, bom_crlf=True)
    lines = []
    assert DG.run(root, 'intra-rater', out=lines.append) == 0
    doc = json.loads((root / DG.DIAG_REL / 'intra_rater.json').read_text(encoding='utf-8'))
    assert doc['result']['cells'] == 30 and doc['result']['kappa'] == 1.0     # same labels both times
    assert not any('J-0' in l for l in lines)
