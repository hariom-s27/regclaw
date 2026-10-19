"""S8 Part D1 audit sample + audit freeze (s8_judging): fakes only, no services, no network."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from retrieval_eval import s8_judging as j
from retrieval_eval.s8_labels import read_sheet
from tests.test_s8_labels import label, make_sheet, write_labels

# Per-query item counts of the c2 blind sheet (counts only).
SHEET_SIZES = {'Q6': 131, 'Q2': 129, 'Q7': 128, 'Q10': 123, 'Q11': 121, 'Q3': 99, 'R1': 95, 'Q1': 83, 'R2': 81,
               'Q12': 79, 'Q4': 78, 'R3': 76, 'Q9': 72, 'Q13': 69, 'Q5': 61, 'Q8': 57}


def test_allocation_is_proportional_with_minimum_eight():
    alloc = j.audit_allocation(SHEET_SIZES)
    assert set(alloc) == set(SHEET_SIZES) and sum(SHEET_SIZES.values()) == 1482
    assert min(alloc.values()) == 8 and alloc['Q8'] == 8
    assert 198 <= sum(alloc.values()) <= 204
    assert alloc['Q6'] == round(200 * 131 / 1482)
    assert j.audit_allocation({'A': 3, 'B': 997}, target=200)['A'] == 3  # never more than the query has


def test_draw_sees_only_item_id_and_qid(tmp_path):
    a = make_sheet(tmp_path / 'a.csv', {'Q1': 400, 'Q2': 250, 'Q3': 120})
    b = [{**r, 'text': 'DIFFERENT ' + r['text'][::-1], 'title': 'x', 'clause_id': 'other', 'source_doc': 'y'}
         for r in a]
    ra, _ = j.build_audit(a)
    rb, _ = j.build_audit(b)
    assert [r['item_id'] for r in ra] == [r['item_id'] for r in rb]
    chosen, alloc = j.draw_audit([(r['item_id'], r['qid']) for r in a])
    assert chosen == {r['item_id'] for r in ra}
    assert j.draw_audit([(r['item_id'], r['qid']) for r in a], seed=1)[0] != chosen


def test_audit_rows_verbatim_in_sheet_order_with_rules_on_first_row_per_query(tmp_path):
    sheet = make_sheet(tmp_path / 's.csv', {'Q1': 500, 'Q2': 300})
    rows, alloc = j.build_audit(sheet, seed=5)
    order = {r['item_id']: n for n, r in enumerate(sheet)}
    assert [order[r['item_id']] for r in rows] == sorted(order[r['item_id']] for r in rows)
    by_id = {r['item_id']: r for r in sheet}
    seen = set()
    for r in rows:
        assert {k: v for k, v in r.items() if k != 'common_rules'} == \
               {k: v for k, v in by_id[r['item_id']].items() if k != 'common_rules'}
        assert r['common_rules'] == ('RULES' if r['qid'] not in seen else '')
        seen.add(r['qid'])
    assert {q: sum(r['qid'] == q for r in rows) for q in alloc} == alloc == {'Q1': 125, 'Q2': 75}


def test_run_audit_sample_write_once(tmp_path):
    make_sheet(tmp_path / 's8_sheet.csv', {'Q1': 600, 'Q2': 400})
    out = tmp_path / 'audit'
    res = j.run_audit_sample(tmp_path / 's8_sheet.csv', out)
    assert res['states'] == {'audit_sheet.csv': 'written', 'audit_ids.txt': 'written'}
    rows = read_sheet(out / 'audit_sheet.csv')
    assert (out / 'audit_ids.txt').read_text(encoding='utf-8').split() == [r['item_id'] for r in rows]
    assert j.run_audit_sample(tmp_path / 's8_sheet.csv', out)['states']['audit_sheet.csv'] == 'unchanged'
    with pytest.raises(FileExistsError):
        j.run_audit_sample(tmp_path / 's8_sheet.csv', out, seed=99)


def test_freeze_audit_requires_complete_labels_and_is_write_once(tmp_path):
    make_sheet(tmp_path / 's8_sheet.csv', {'Q1': 30, 'Q2': 20})
    out = tmp_path / 'audit'
    j.run_audit_sample(tmp_path / 's8_sheet.csv', out)
    ids = (out / 'audit_ids.txt').read_text(encoding='utf-8').split()
    labels = write_labels(out / j.AUDIT_LABELS, [label(i, 1) for i in ids[:-1]])
    with pytest.raises(ValueError, match='unlabelled'):
        j.freeze_audit(labels, out)
    labels = write_labels(out / j.AUDIT_LABELS, [label(i, 1) for i in ids])
    rec = j.freeze_audit(labels, out, now=datetime(2026, 10, 21, tzinfo=timezone.utc))
    saved = json.loads((out / 'audit_freeze.json').read_text(encoding='utf-8'))
    assert saved == rec and rec['items'] == len(ids) and rec['frozen_at'] == '2026-10-21T00:00:00+00:00'
    assert rec['labels_file'] == j.AUDIT_LABELS and len(rec['labels_sha256']) == 64
    assert j.frozen_labels_path(out / 'audit_freeze.json') == labels.resolve()
    with pytest.raises(FileExistsError):
        j.freeze_audit(labels, out)
