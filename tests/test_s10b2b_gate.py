"""S10b2b-03/-07: audit-A gate on fake profiles (30 fake J-ids, 350-row fake list; no real data)."""
from fractions import Fraction as F
import csv
import io
import json
import math
import random

import pytest

import _netguard
from eval.s10 import agreement as G
from eval.s10 import gate as GT
from eval.s10.metrics import _quantile
from standards.ruleset import normalised_sha


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')


N, R, M = 'not_applicable', 'recommended', 'mandatory'


def fake(bad_upto=0, bad_every=1, n=350):
    """Position p -> J-((p-1)%30+1), item I-((p-1)//30). Human: M if p%3==0, R if p%3==1, else N.
    Final = human, except positions p <= bad_upto with p % bad_every == 0 (M -> N, otherwise -> M)."""
    rows, human, merged = [], {}, {}
    for p in range(1, n + 1):
        j, item = f'J-{(p - 1) % 30 + 1:03d}', f'I-{(p - 1) // 30}'
        cid = f'{j}|{item}'
        h = M if p % 3 == 0 else (R if p % 3 == 1 else N)
        f = (N if h == M else M) if (p <= bad_upto and p % bad_every == 0) else h
        rows.append(dict(order=p, j_id=j, item=item, family='F1'))
        human[cid] = h
        merged[cid] = dict(cell_id=cid, j_id=j, item=item, routed=False, route_reasons=[], final=f,
                           labels=dict(A=f, B=f, C=f), confidences=dict(A='high', B='high', C='high'))
    return rows, human, merged


# ---- sequential looks ----------------------------------------------------------------------------------
def test_pass_at_look_1():
    res = GT.run_gate(*fake())
    assert res['status'] == 'PASS' and res['look'] == 150 and len(res['looks']) == 1
    g = res['looks'][0]['gate']
    assert g['kappa'] == 1.0 and g['lower'] == 1.0 and g['n_boot'] == 2000 and g['seed'] == 0 and g['q'] == 0.0083


def test_pass_at_look_2():
    res = GT.run_gate(*fake(150, 8))
    assert res['status'] == 'PASS' and res['look'] == 250
    first, second = res['looks']
    assert first['gate']['passed'] is False and first['gate']['lower'] < 0.60
    assert second['gate']['passed'] is True and second['gate']['lower'] >= 0.60


def test_pass_at_look_3():
    res = GT.run_gate(*fake(150, 4))
    assert res['status'] == 'PASS' and res['look'] == 350 and len(res['looks']) == 3


def test_fail_at_350_is_stop():
    res = GT.run_gate(*fake(350, 3))
    assert res['status'] == 'STOP' and res['look'] == 350
    assert [x['look'] for x in res['looks']] == [150, 250, 350]
    assert not any(x['gate']['passed'] for x in res['looks'])


def test_need_labels_and_continue():
    rows, human, merged = fake()
    del human[f'{rows[99]["j_id"]}|{rows[99]["item"]}']
    res = GT.run_gate(rows, human, merged)
    assert res['status'] == 'NEED_LABELS' and res['next_look'] == 150 and res['labelled'] == 99 and res['looks'] == []
    rows, human, merged = fake(150, 8)
    for r in rows[200:]:
        del human[f'{r["j_id"]}|{r["item"]}']
    res = GT.run_gate(rows, human, merged)
    assert res['status'] == 'CONTINUE' and res['next_look'] == 250 and len(res['looks']) == 1


def test_deterministic_seed_0():
    a = GT.run_gate(*fake(150, 8))
    b = GT.run_gate(*fake(150, 8))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---- routed cells --------------------------------------------------------------------------------------
def test_routed_cells_excluded():
    rows, human, merged = fake()
    base = GT.gate_look(rows[:150], human, merged, n_boot=300)
    for p, r in enumerate(rows[:150], start=1):
        if p % 5 == 0:
            row = merged[f'{r["j_id"]}|{r["item"]}']
            flip = N if human[row['cell_id']] == M else M
            row.update(routed=True, route_reasons=['straddle'], final=None, labels=dict(A=M, B=N, C=flip))
    res = GT.gate_look(rows[:150], human, merged, n_boot=300)
    assert res['gate_cells'] == 120 and res['routed_in_prefix'] == 30 and res['routed_share'] == pytest.approx(0.2)
    assert res['gate']['kappa'] == base['gate']['kappa'] == 1.0 and res['gate']['lower'] == 1.0
    assert sum(map(sum, res['secondary']['gate_cells']['table_3x3']['counts'])) == 120


# ---- undefined resamples + exact lower quantile --------------------------------------------------------
def undefined_fake():
    spec = {'J-001': [(N, N), (N, N)], 'J-002': [(R, R), (R, N)], 'J-003': [(M, M), (N, N), (M, N)]}
    rows, human, merged = [], {}, {}
    for j, cells in spec.items():
        for k, (h, f) in enumerate(cells):
            cid = f'{j}|I-{k}'
            rows.append(dict(order=len(rows) + 1, j_id=j, item=f'I-{k}'))
            human[cid] = h
            merged[cid] = dict(cell_id=cid, routed=False, final=f, labels=dict(A=f, B=f, C=f))
    return rows, human, merged, spec


def test_undefined_resamples_counted_and_lower_is_quantile_0_0083():
    rows, human, merged, spec = undefined_fake()
    n_boot = 400
    res = GT.gate_look(rows, human, merged, n_boot=n_boot)
    # independent replay of the profile resampling (sorted J-ids, Random(0), one choice per profile)
    ids = sorted(spec)
    rng = random.Random(0)
    values, undefined = [], 0
    for _ in range(n_boot):
        drawn = [rng.choice(ids) for _ in ids]
        a = b = c = d = 0                                   # 2x2 counts, mandatory first
        for j in drawn:
            for h, f in spec[j]:
                hm, fm = h == M, f == M
                a += hm and fm
                b += hm and not fm
                c += fm and not hm
                d += not hm and not fm
        den = (a + b) * (b + d) + (a + c) * (c + d)
        if den == 0:                                        # one class only: kappa undefined
            undefined += 1
        else:
            values.append(float(F(2 * (a * d - b * c), den)))
    assert undefined > 0
    assert res['gate']['n_undefined'] == undefined
    values.sort()
    pos = (len(values) - 1) * 0.0083
    lo, hi = math.floor(pos), math.ceil(pos)
    manual = values[lo] + (values[hi] - values[lo]) * (pos - lo)
    assert res['gate']['lower'] == pytest.approx(manual, abs=1e-12)
    assert res['gate']['lower'] == pytest.approx(_quantile(values, 0.0083), abs=1e-12)


def test_nan_lower_is_not_passed():
    rows, human, merged = fake(n=150)
    for cid in human:                                       # one class only everywhere: every kappa undefined
        human[cid] = N
        merged[cid].update(final=N, labels=dict(A=N, B=N, C=N))
    res = GT.run_gate(rows, human, merged, looks=(150,))
    assert res['status'] == 'STOP'
    g = res['looks'][0]['gate']
    assert math.isnan(g['kappa']) and math.isnan(g['lower']) and g['n_undefined'] == 2000 and g['passed'] is False


# ---- secondary rows (hand counts) ----------------------------------------------------------------------
def test_secondary_rows_hand_counts():
    # positions 1..150, bad every 4th: human M = 50 cells; M -> N at p % 12 == 0 (12 cells);
    # not-M -> M at p % 4 == 0 and p % 3 != 0 (37 - 12 = 25 cells). a=38 b=12 c=25 d=75.
    rows, human, merged = fake(150, 4)
    res = GT.gate_look(rows[:150], human, merged, n_boot=200)
    s = res['secondary']['gate_cells']
    assert s['binary_table']['counts'] == [[38, 12], [25, 75]]
    assert res['gate']['kappa'] == pytest.approx(float(F(2 * (38 * 75 - 12 * 25), 50 * 87 + 63 * 100)))
    assert s['po'] == pytest.approx(113 / 150)
    assert s['pabak'] == pytest.approx(2 * 113 / 150 - 1)
    assert s['prevalence_index'] == pytest.approx(abs(38 - 75) / 150)
    assert s['prevalence_index_signed'] == pytest.approx((38 - 75) / 150)
    assert s['bias_index'] == pytest.approx(abs(12 - 25) / 150)
    assert s['bias_index_signed'] == pytest.approx((12 - 25) / 150)
    assert s['gold_mandatory_recall_vs_human']['point'] == pytest.approx(38 / 50)
    assert s['gold_mandatory_precision_vs_human']['point'] == pytest.approx(38 / 63)
    assert s['gold_mandatory_recall_vs_human']['label'] == 'profile-cluster bootstrap (J-id)'
    assert s['fce_interval']['label'] == 'assumes independent cells (too narrow here)'
    assert sum(map(sum, s['table_3x3']['counts'])) == 150
    assert s['table_3x3']['counts'][2] == [12, 0, 38]                 # human M row: 12 -> NA, 38 -> M
    allp = res['secondary']['all_prefix_vs_median']
    assert allp['kappa_binary'] == pytest.approx(res['gate']['kappa']) and allp['cells_skipped_not_3_labels'] == 0
    assert set(s) == {'binary_table', 'po', 'pabak', 'ac1', 'prevalence_index', 'bias_index',
                      'prevalence_index_signed', 'bias_index_signed', 'table_3x3',
                      'gold_mandatory_recall_vs_human', 'gold_mandatory_precision_vs_human', 'fce_interval'}


def test_all_prefix_median_skips_cells_without_3_labels():
    rows, human, merged = fake()
    cid = f'{rows[0]["j_id"]}|{rows[0]["item"]}'
    merged[cid].update(routed=True, route_reasons=['label_missing_or_invalid'], final=None,
                       labels=dict(A=M, B=M, C=None))
    res = GT.gate_look(rows[:150], human, merged, n_boot=50)
    assert res['secondary']['all_prefix_vs_median']['cells_used'] == 149
    assert res['secondary']['all_prefix_vs_median']['cells_skipped_not_3_labels'] == 1


# ---- CLI on a fake repo --------------------------------------------------------------------------------
def write_repo(root, rows, merged, *, list_sha_ok=True):
    lst = io.StringIO(newline='')
    w = csv.DictWriter(lst, fieldnames=('order', 'j_id', 'item', 'family'), lineterminator='\n')
    w.writeheader()
    w.writerows(rows)
    list_raw = lst.getvalue().encode('utf-8')
    (root / 'data/gold/s10').mkdir(parents=True, exist_ok=True)
    (root / GT.LIST_REL).write_bytes(list_raw)
    judge_sha = normalised_sha(b'fake judge file')
    (root / 'eval/s10/manifests').mkdir(parents=True, exist_ok=True)
    (root / GT.BUILD_MANIFEST_REL).write_text(json.dumps(dict(files={
        GT.LIST_REL: dict(sha256_lf=normalised_sha(list_raw) if list_sha_ok else '0' * 64),
        GT.JUDGE_FILE_REL: dict(sha256_lf=judge_sha)})), encoding='utf-8')
    (root / GT.MERGE_REL).write_text(json.dumps(dict(format='s10-merge.v1', status='OK', judge_file_sha256_lf=judge_sha,
                                                     rows=list(merged.values()))), encoding='utf-8')


def write_human(root, rows, human, upto):
    buf = io.StringIO(newline='')
    w = csv.DictWriter(buf, fieldnames=('order', 'j_id', 'item', 'label', 'confidence', 'reason', 'timestamp'),
                       lineterminator='\r\n')
    w.writeheader()
    for r in rows[:upto]:
        w.writerow(dict(order=r['order'], j_id=r['j_id'], item=r['item'], label=human[f'{r["j_id"]}|{r["item"]}'],
                        confidence='high', reason='fake', timestamp='2026-11-16T00:00:00+00:00'))
    (root / GT.HUMAN_REL).write_bytes(b'\xef\xbb\xbf' + buf.getvalue().encode('utf-8'))


def _no_cell_level_text(lines):
    text = '\n'.join(lines)
    for word in (N, R, M, 'J-0', 'fake'):
        assert word not in text


def test_cli_pass_at_150_then_reuse(tmp_path):
    rows, human, merged = fake()
    write_repo(tmp_path, rows, merged)
    write_human(tmp_path, rows, human, 150)
    lines = []
    assert GT.run(tmp_path, out=lines.append) == 0
    assert (tmp_path / GT.GATE_DIR_REL / 'gate_look150.json').is_file()
    assert lines[-1] == 'GATE PASSED at look 150'
    _no_cell_level_text(lines)
    lines2 = []
    assert GT.run(tmp_path, out=lines2.append) == 0
    assert 'look 150: already evaluated (file reused)' in lines2
    assert sorted(p.name for p in (tmp_path / GT.GATE_DIR_REL).iterdir()) == ['gate_look150.json']


def test_cli_continue_then_pass_then_changed_labels_refused(tmp_path):
    rows, human, merged = fake(150, 8)
    write_repo(tmp_path, rows, merged)
    write_human(tmp_path, rows, human, 150)
    lines = []
    assert GT.run(tmp_path, out=lines.append) == 5
    assert lines[-1] == 'not passed yet: look 250 needs 100 more labels'
    write_human(tmp_path, rows, human, 250)
    lines = []
    assert GT.run(tmp_path, out=lines.append) == 0
    assert 'look 150: already evaluated (file reused)' in lines and lines[-1] == 'GATE PASSED at look 250'
    _no_cell_level_text(lines)
    cid = f'{rows[0]["j_id"]}|{rows[0]["item"]}'
    human[cid] = M if human[cid] != M else N
    write_human(tmp_path, rows, human, 250)
    lines = []
    assert GT.run(tmp_path, out=lines.append) == 2
    assert lines[-1].startswith('ERROR: human labels in look 150 changed')


def test_cli_stop_at_350(tmp_path):
    rows, human, merged = fake(350, 3)
    write_repo(tmp_path, rows, merged)
    write_human(tmp_path, rows, human, 350)
    lines = []
    assert GT.run(tmp_path, out=lines.append) == 4
    assert lines[-1] == 'STOP: gate failed at look 350; Hariom decides'
    assert len(list((tmp_path / GT.GATE_DIR_REL).iterdir())) == 3


def test_cli_refuses_list_sha_mismatch(tmp_path):
    rows, human, merged = fake()
    write_repo(tmp_path, rows, merged, list_sha_ok=False)
    write_human(tmp_path, rows, human, 150)
    lines = []
    assert GT.run(tmp_path, out=lines.append) == 2
    assert not (tmp_path / GT.GATE_DIR_REL).exists()
