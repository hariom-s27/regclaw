"""S8 Part D merge + agreement (s8_labels): fakes only, no services, no network, no real labels."""
import csv
import json
from pathlib import Path

import numpy as np
import pytest

from retrieval_eval import s8_labels as m
from retrieval_eval.s8_runs import SHEET_COLUMNS, sheet_csv

SEALED = {'pool_key.jsonl', 'runs.jsonl', 'item_map.jsonl', 'practice_item_map.jsonl'}


def make_sheet(path, per_qid):
    rows = []
    for qid, n in per_qid.items():
        for i in range(n):
            rows.append({c: '' for c in SHEET_COLUMNS} | dict(
                item_id=f'S8-{qid.lower()}{i:04d}', qid=qid, query=f'query {qid}', source_doc='doc',
                clause_id=f'doc:{qid}.{i}', title=f't{i}', text=f'text {qid} {i}',
                common_rules='RULES' if i == 0 else ''))
    Path(path).write_text(sheet_csv(rows), encoding='utf-8', newline='')
    return rows


def label(item_id, grade, unsure='no', **kw):
    return dict(item_id=item_id, query_asks='asks', clause_states=kw.get('clause_states', 'partial'),
                jurisdiction_match='not_applicable', evidence_span=kw.get('evidence_span', 'span'),
                grade=str(grade), unsure=unsure, note=kw.get('note', ''))


def write_labels(path, rows, fields=m.LABEL_FIELDS):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(fields), extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    return path


def write_judge(folder, rows, grades):
    """One labels_s8_sheet_<qid>.csv per query, like the judges will produce."""
    by_q = {}
    for r in rows:
        by_q.setdefault(r['qid'], []).append(label(r['item_id'], grades[r['item_id']]))
    return [write_labels(Path(folder) / f'labels_s8_sheet_{q}.csv', ls) for q, ls in sorted(by_q.items())]


# ---------------------------------------------------------------- kappa
def test_kappa_binary_textbook_value():
    assert m.kappa_from_confusion([[20, 5], [10, 15]]) == pytest.approx(0.4)
    assert m.kappa_from_confusion([[10, 0], [0, 10]]) == pytest.approx(1.0)
    assert np.isnan(m.kappa_from_confusion([[10, 0], [0, 0]]))  # one class only: undefined


def test_quadratic_kappa_matches_disagreement_form():
    rng = np.random.default_rng(1)
    c = rng.integers(0, 20, size=(4, 4))
    i, j = np.indices((4, 4))
    v = (i - j) ** 2
    expected = 1 - (v * c).sum() / (v * np.outer(c.sum(1), c.sum(0)) / c.sum()).sum()
    assert m.kappa_from_confusion(c, 'quadratic') == pytest.approx(expected)


def test_binary_collapse_uses_grade_two_threshold():
    c = np.arange(16).reshape(4, 4)
    b = m.binary_confusion(c)
    assert b.tolist() == [[c[:2, :2].sum(), c[:2, 2:].sum()], [c[2:, :2].sum(), c[2:, 2:].sum()]]
    assert b.sum() == c.sum()


def test_bootstrap_resamples_queries_and_is_seeded():
    per_qid = {'Q1': np.diag([5, 0, 0, 5]), 'Q2': np.array([[0, 0, 5, 0], [0, 0, 0, 0], [5, 0, 0, 0], [0, 0, 0, 0]]),
               'Q3': np.diag([3, 3, 3, 3])}
    stat = lambda mm: m.kappa_from_confusion(m.binary_confusion(mm))
    a = m.bootstrap_ci(per_qid, stat, n=500, seed=7)
    assert a == m.bootstrap_ci(per_qid, stat, n=500, seed=7)
    assert a['draws'] == 500 and a['lo'] <= a['hi']
    one = m.bootstrap_ci({'Q1': np.diag([5, 0, 0, 5])}, stat, n=50)
    assert one['lo'] == one['hi'] == 1.0  # one query: every resample is identical


# ---------------------------------------------------------------- merge rule + validation
def test_merge_same_side_takes_lower_grade_else_dispute():
    rows = [dict(item_id=f'i{k}', qid='Q1') for k in range(5)]
    a = {f'i{k}': dict(grade=g) for k, g in enumerate('32101')}
    b = {f'i{k}': dict(grade=g) for k, g in enumerate('23002')}
    accepted, disputes = m.merge(rows, a, b)
    assert accepted == {'i0': 2, 'i1': 2, 'i2': 0, 'i3': 0}
    assert disputes == ['i4']  # 1 vs 2 straddles the threshold


@pytest.mark.parametrize('field,value', [('grade', '4'), ('grade', ''), ('unsure', 'Yes'), ('unsure', 'true'),
                                         ('unsure', '1'), ('clause_states', 'relevant'),
                                         ('jurisdiction_match', 'n/a')])
def test_invalid_fields_rejected(tmp_path, field, value):
    bad = label('S8-x', 2) | {field: value}
    with pytest.raises(ValueError):
        m.read_labels([write_labels(tmp_path / 'l.csv', [bad])], 'judge A')


def test_unsure_yes_no_accepted_and_query_asks_optional(tmp_path):
    fields = [f for f in m.LABEL_FIELDS if f != 'query_asks'] + ['timestamp']
    path = write_labels(tmp_path / 'l.csv', [label('S8-a', 3, 'yes'), label('S8-b', 0, 'no')], fields)
    got = m.read_labels([path], 'audit')
    assert {k: v['unsure'] for k, v in got.items()} == {'S8-a': 'yes', 'S8-b': 'no'}


def test_unknown_or_missing_columns_rejected(tmp_path):
    with pytest.raises(ValueError, match='unknown columns'):
        m.read_labels([write_labels(tmp_path / 'a.csv', [label('S8-a', 1) | {'score': '1'}],
                                    [*m.LABEL_FIELDS, 'score'])], 'A')
    with pytest.raises(ValueError, match='missing columns'):
        m.read_labels([write_labels(tmp_path / 'b.csv', [label('S8-a', 1)],
                                    [f for f in m.LABEL_FIELDS if f != 'grade'])], 'A')


def test_every_item_exactly_once(tmp_path):
    rows = make_sheet(tmp_path / 'sheet.csv', {'Q1': 3})
    ids = [r['item_id'] for r in rows]
    dup = [write_labels(tmp_path / 'x.csv', [label(ids[0], 1)]), write_labels(tmp_path / 'y.csv', [label(ids[0], 2)])]
    with pytest.raises(ValueError, match='more than once'):
        m.read_labels(dup, 'A')
    partial = m.read_labels([write_labels(tmp_path / 'p.csv', [label(i, 1) for i in ids[:2]])], 'A')
    with pytest.raises(ValueError, match=r'1 item\(s\) unlabelled'):
        m.check_coverage(partial, ids, 'A')
    extra = m.read_labels([write_labels(tmp_path / 'e.csv', [label(i, 1) for i in ids + ['S8-zzz']])], 'A')
    with pytest.raises(ValueError, match='unknown item'):
        m.check_coverage(extra, ids, 'A')


def test_judge_file_must_hold_its_own_query(tmp_path):
    rows = make_sheet(tmp_path / 'sheet.csv', {'Q1': 2, 'Q2': 2})
    a = tmp_path / 'A'
    write_labels(a / 'labels_s8_sheet_Q1.csv', [label(r['item_id'], 1) for r in rows])  # Q2 items in Q1 file
    labels = m.read_labels(m.judge_files(a), 'A')
    with pytest.raises(ValueError, match='another query'):
        m.check_files_match_qid(labels, {r['item_id']: r for r in rows}, 'A')


# ---------------------------------------------------------------- pipeline on fakes
@pytest.fixture
def fake(tmp_path, monkeypatch):
    real_open, real_bytes = Path.open, Path.read_bytes

    def guard(fn):
        def wrapped(self, *a, **kw):
            assert self.name not in SEALED, f'sealed file touched: {self.name}'
            return fn(self, *a, **kw)
        return wrapped
    for name in SEALED:  # poison: present, but must never be read
        (tmp_path / name).write_text('{"system": "SECRET"}\n', encoding='utf-8')
    monkeypatch.setattr(Path, 'open', guard(real_open))
    monkeypatch.setattr(Path, 'read_bytes', guard(real_bytes))
    rows = make_sheet(tmp_path / 'sheet.csv', {'Q1': 12, 'Q2': 10, 'Q3': 8})
    rng = np.random.default_rng(3)
    ga = {r['item_id']: int(rng.integers(0, 4)) for r in rows}
    gb = {k: (g if rng.random() < 0.8 else int(rng.integers(0, 4))) for k, g in ga.items()}
    return tmp_path, rows, ga, gb


def run(tmp, rows, ga, gb, **kw):
    return m.run_merge(sheet=tmp / 'sheet.csv', a_files=write_judge(tmp / 'A', rows, ga),
                       b_files=write_judge(tmp / 'B', rows, gb), out=tmp / 'labels', bootstrap=300, **kw)


def test_stage1_agreement_counts_only(fake):
    tmp, rows, ga, gb = fake
    s = run(tmp, rows, ga, gb)
    assert s['stage'] == 1 and s['items'] == 30
    ab = json.loads((tmp / 'labels' / 'agreement_ab.json').read_text(encoding='utf-8'))
    conf = np.array(ab['confusion_4x4_rows_A_cols_B'])
    assert conf.sum() == 30 and conf.shape == (4, 4)
    assert ab['kappa_binary'] == pytest.approx(round(m.kappa_from_confusion(m.binary_confusion(conf)), 4))
    assert ab['raw_exact'] == pytest.approx(round(np.trace(conf) / 30, 4))
    assert ab['queries'] == 3 and ab['ci95_kappa_binary']['seed'] == 20261020
    assert ab['accepted'] + ab['threshold_disputes'] == 30
    text = json.dumps(ab)
    assert 'SECRET' not in text and 'system' not in text
    assert not (tmp / 'labels' / 'disputes.csv').exists()  # no referee list before the audit freeze
    assert run(tmp, rows, ga, gb)['states']['agreement_ab.json'] == 'unchanged'  # write-once, reproducible


def test_changed_labels_cannot_overwrite_outputs(fake):
    tmp, rows, ga, gb = fake
    run(tmp, rows, ga, gb)
    ga[rows[0]['item_id']] = (ga[rows[0]['item_id']] + 2) % 4
    with pytest.raises(FileExistsError):
        run(tmp, rows, ga, gb)


def test_audit_requires_freeze(fake):
    tmp, rows, ga, gb = fake
    ids = [r['item_id'] for r in rows[::3]]
    audit = write_labels(tmp / 'audit.csv', [label(i, ga[i]) for i in ids])
    with pytest.raises(PermissionError, match='s8-freeze-audit'):
        run(tmp, rows, ga, gb, audit_ids=ids, audit_file=audit, freeze_path=tmp / 'audit_freeze.json')
    (tmp / 'audit_freeze.json').write_text(json.dumps(dict(labels_sha256='0' * 64, frozen_at='2026-10-21T00:00:00')))
    with pytest.raises(PermissionError, match='sha mismatch'):
        run(tmp, rows, ga, gb, audit_ids=ids, audit_file=audit, freeze_path=tmp / 'audit_freeze.json')
    assert not (tmp / 'labels' / 'audit_check.json').exists()


def test_audit_pass_builds_referee_list_then_qrels(fake):
    from retrieval_eval import qrels as qrels_io
    from retrieval_eval.s8_judging import freeze_audit
    tmp, rows, ga, gb = fake
    accepted, splits = m.merge(rows, {k: dict(grade=str(v)) for k, v in ga.items()},
                               {k: dict(grade=str(v)) for k, v in gb.items()})
    acc_ids = [r['item_id'] for r in rows if r['item_id'] in accepted]
    ids = acc_ids[:12] + splits[:1]
    flip = acc_ids[0]  # Shyam disagrees with the consensus on one item
    audit_rows = [label(i, accepted.get(i, 0)) for i in ids]
    audit_rows[0]['grade'] = '0' if accepted[flip] >= 2 else '3'
    audit_dir = tmp / 'audit'
    audit_dir.mkdir()
    (audit_dir / 'audit_ids.txt').write_text(''.join(i + '\n' for i in ids), encoding='utf-8')
    (audit_dir / 'audit_sheet.csv').write_text('x', encoding='utf-8')
    audit = write_labels(audit_dir / 'labels_shyam_audit.csv', audit_rows)
    freeze_audit(audit, audit_dir)
    frozen = dict(audit_ids=ids, audit_file=audit, freeze_path=audit_dir / 'audit_freeze.json')
    s = run(tmp, rows, ga, gb, **frozen)
    assert s['stage'] == 2 and s['audit']['flag'] == 'PASS' and s['audit']['disagreements'] == 1
    assert s['audit']['on_accepted'] == 12 and s['audit']['on_disputes'] == 1
    referee_ids = (tmp / 'labels' / 'referee_ids.txt').read_text(encoding='utf-8').split()
    assert set(referee_ids) == set(splits) | {flip}
    disputes = m.read_disputes(tmp / 'labels' / 'disputes.csv')
    assert {d['item_id']: d['reason'] for d in disputes}[flip] == 'audit_disagrees'
    assert all(d['a_grade'] and d['b_evidence_span'] == 'span' for d in disputes)
    assert not {'system', 'rank', 'score'} & set(m.DISPUTE_COLUMNS)
    referee_sheet = m.read_sheet(tmp / 'labels' / 'referee_sheet.csv')
    assert [r['item_id'] for r in referee_sheet] == referee_ids
    assert not (tmp / 'labels' / 'qrels.tsv').exists()

    bad = write_labels(tmp / 'ref_bad.csv', [label(i, 3) for i in referee_ids[1:]])
    with pytest.raises(ValueError, match='unlabelled'):
        run(tmp, rows, ga, gb, referee_file=bad, **frozen)
    ref = write_labels(tmp / 'ref.csv', [label(i, 3) for i in referee_ids])
    s = run(tmp, rows, ga, gb, referee_file=ref, **frozen)
    assert s['stage'] == 3 and s['qrels']['pairs'] == 30
    q = qrels_io.read(tmp / 'labels' / 'qrels.tsv')
    by = {(r['qid'], r['clause_id']): r for r in q}
    for r in rows:
        got = by[(r['qid'], r['clause_id'])]
        if r['item_id'] in referee_ids:
            assert (got['grade'], got['judge']) == (3, 'referee')
        else:
            assert (got['grade'], got['judge']) == (min(ga[r['item_id']], gb[r['item_id']]), 'AB_min')
    assert s['qrels']['source'] == {'referee': len(referee_ids), 'AB_min': 30 - len(referee_ids)}


def test_audit_stop_blocks_referee_and_qrels(fake):
    tmp, rows, ga, gb = fake
    accepted, _ = m.merge(rows, {k: dict(grade=str(v)) for k, v in ga.items()},
                          {k: dict(grade=str(v)) for k, v in gb.items()})
    ids = [i for i in (r['item_id'] for r in rows) if i in accepted][:12]
    audit = write_labels(tmp / 'audit.csv', [label(i, 3 if accepted[i] < 2 else 0) for i in ids])  # all flipped
    s = run(tmp, rows, ga, gb, audit_ids=ids, audit_file=audit, _test_skip_freeze=True)
    assert s['audit']['flag'] == 'STOP' and 'stopped' in s
    assert not (tmp / 'labels' / 'disputes.csv').exists() and not (tmp / 'labels' / 'qrels.tsv').exists()


def test_cli_has_no_test_only_flag():
    import inspect
    assert '_test_skip_freeze' not in inspect.getsource(m.main)
