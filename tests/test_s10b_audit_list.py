"""S10b-07/08 Audit-A list, on a FAKE judge file only (no real files)."""
import csv
import io
from collections import Counter

import pytest
import yaml

from eval.s10 import audit_list as AL

PARTS = [i for f in ('F2', 'F1', 'F3', 'F4') for i in AL.FAMILY_ITEMS[f]]


def fake_judge(knee_ids=(3, 9, 17, 25)):
    return dict(format='s10-judge-file.v1', seed='S10b-v1', entries=[
        dict(j_id=f'J-{i:03d}', contact_category='surface/intact_skin', duration='A', profile={},
             items=PARTS + (['fda-knee'] if i in knee_ids else [])) for i in range(1, 31)])


def write_judge(root, doc):
    p = root / AL.JUDGE_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding='utf-8')


def read_rows(root):
    return list(csv.DictReader(io.StringIO((root / AL.OUT_REL).read_text(encoding='utf-8'), newline='')))


@pytest.mark.parametrize('k,expected', [
    (0, dict(F1=123, F2=70, F3=122, F4=35, F5=0)),
    (2, dict(F1=122, F2=70, F3=122, F4=35, F5=1)),
    (4, dict(F1=122, F2=69, F3=122, F4=35, F5=2)),
    (6, dict(F1=121, F2=69, F3=121, F4=35, F5=4)),
    (10, dict(F1=121, F2=69, F3=120, F4=34, F5=6)),
])
def test_largest_remainder_table(k, expected):
    counts = dict(F1=210, F2=120, F3=210, F4=60, F5=k)
    assert AL.family_totals(counts) == expected


def test_row_quotas():
    q = AL.row_quotas([f'J-{i:03d}' for i in range(1, 31)])
    assert sum(q.values()) == 350 and Counter(q.values()) == {12: 20, 11: 10}


def test_build_and_write(tmp_path):
    write_judge(tmp_path, fake_judge())
    lines = []
    assert AL.run(tmp_path, out=lines.append) == 0
    rows = read_rows(tmp_path)
    assert list(rows[0]) == list(AL.COLUMNS)
    assert len(rows) == 350 and [int(r['order']) for r in rows] == list(range(1, 351))
    assert len({(r['j_id'], r['item']) for r in rows}) == 350
    per_j = Counter(r['j_id'] for r in rows)
    assert len(per_j) == 30 and set(per_j.values()) <= {11, 12}
    assert Counter(per_j.values()) == {12: 20, 11: 10}
    fam = Counter(r['family'] for r in rows)
    assert dict(fam) == {f: n for f, n in AL.family_totals(dict(F1=210, F2=120, F3=210, F4=60, F5=4)).items() if n}
    assert all(AL.FAMILY_OF[r['item']] == r['family'] for r in rows)
    assert b'\r\n' not in (tmp_path / AL.OUT_REL).read_bytes()
    assert any(l.startswith('cells_total=604 rows=350') for l in lines)


def test_deterministic(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    for root in (a, b):
        write_judge(root, fake_judge())
        AL.run(root, out=lambda s: None)
    assert (a / AL.OUT_REL).read_bytes() == (b / AL.OUT_REL).read_bytes()


def test_order_is_shuffled(tmp_path):
    write_judge(tmp_path, fake_judge())
    AL.run(tmp_path, out=lambda s: None)
    jids = [r['j_id'] for r in read_rows(tmp_path)]
    assert jids != sorted(jids)


def test_write_once(tmp_path):
    write_judge(tmp_path, fake_judge())
    assert AL.run(tmp_path, out=lambda s: None) == 0
    before = (tmp_path / AL.OUT_REL).read_bytes()
    assert AL.run(tmp_path, out=lambda s: None) == 3
    assert (tmp_path / AL.OUT_REL).read_bytes() == before


def test_unknown_item_is_error(tmp_path):
    doc = fake_judge()
    doc['entries'][0]['items'].append('ISO 99999-1')
    write_judge(tmp_path, doc)
    assert AL.run(tmp_path, out=lambda s: None) == 2
    assert not (tmp_path / AL.OUT_REL).exists()


def test_duplicate_jid_is_error():
    doc = fake_judge()
    doc['entries'][1]['j_id'] = 'J-001'
    with pytest.raises(AL.AuditListError):
        AL.build(doc)
