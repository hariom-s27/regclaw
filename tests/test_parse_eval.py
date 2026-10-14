import csv
import pytest

from ingest.parse_eval import FIELDS, boundary_matches, counts, metrics, score, wilson


def labelled_rows():
    gold, pred = {0, 5, 10, 15}, {0, 6, 10, 15, 18}
    return [dict(line_id=f'x:1:{i+1}', doc='x', page='1', text='aaaa',
                 gold_role='clause_start' if i in gold else 'continuation',
                 pred_role='clause_start' if i in pred else 'continuation',
                 gold_number=str(i), pred_number=str(i) if i != 10 else 'wrong',
                 gold_parent_number='root', pred_parent_number='root' if i != 15 else 'wrong')
            for i in range(20)]


def test_hand_computed_twenty_line_csv(tmp_path):
    path = tmp_path / 'labels.csv'
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, FIELDS); w.writeheader(); w.writerows(labelled_rows())
    result = score(path, tmp_path)['per_doc']['x']
    # Gold segments are 5+5+5+5; k=round_half_up(2.5)=3. 17 windows.
    # Moving 5->6 changes windows starting 2 and 5; extra 18 changes 15,16.
    assert result['boundary_f1']['value'] == pytest.approx(6/9)
    assert result['boundary_f1_pm1']['value'] == pytest.approx(8/9)
    assert result['Pk']['value'] == pytest.approx(4/17)
    assert result['WindowDiff']['value'] == pytest.approx(4/17)
    assert result['parent_accuracy']['value'] == pytest.approx(2/3)
    assert result['number_accuracy']['value'] == pytest.approx(2/3)
    # Hand-evaluated Wilson(2,3), z=1.95996398454.
    assert result['parent_accuracy']['ci95'] == pytest.approx([.2076596, .9385081], abs=1e-6)


def test_missing_labels_and_lost_lines():
    rows = labelled_rows()
    rows[8]['gold_role'] = ''
    rows[7]['pred_role'] = 'footnote'
    rows[12].update(gold_role='header_footer', pred_role='header_footer')
    m = metrics(counts(rows))
    assert m['unlabelled_rows'] == 1
    assert m['lost_lines'] == 1
    assert m['coverage']['value'] == pytest.approx(17/18)
    assert m['header_footer_precision']['value'] == 1
    assert m['header_footer_recall']['value'] == 1
    assert boundary_matches([1, 2], [2], 1) == [(1, 2)]
    assert wilson(0, 0) == [0, 1]


def test_no_windows_bridge_pages():
    rows = labelled_rows()
    for r in rows[10:]: r['page'] = '5'
    c = counts(rows)
    assert c['windows'] == 14  # two 10-line runs, k=3


def test_sample_is_pre_filter_and_gold_is_empty(tmp_path, monkeypatch):
    import ingest.parse_eval as pe
    lines = [dict(line_id=f'x:{p}:1', doc='x', page=p, text='footnote' if p == 2 else 'body',
                  pred_role='footnote' if p == 2 else 'continuation', pred_number='', pred_parent_number='')
             for p in range(1, 6)]
    monkeypatch.setattr(pe, 'run', lambda root: ({'x': {'lines': lines, 'stats': {'pages': 5}, 'clauses': []}}, []))
    pages, rows = pe.sample(tmp_path)
    assert pages['x']['hard_page'] == 2
    assert len(pages['x']['pages']) == 4
    assert any(r['pred_role'] == 'footnote' for r in rows)
    assert all(r['gold_role'] == r['gold_number'] == r['gold_parent_number'] == '' for r in rows)
