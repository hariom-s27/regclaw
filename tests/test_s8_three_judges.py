"""S8 Addendum 2 notes 2-3 (three judges A/B/C, check-only audit, referee rules): fakes only, no network."""
import json
from pathlib import Path

import numpy as np
import pytest

from retrieval_eval import s8_judging as j
from retrieval_eval import s8_label_tool as t
from retrieval_eval import s8_labels as m
from tests.test_s8_labels import SEALED, label, make_sheet, write_labels

REF_FIELDS = [*m.LABEL_FIELDS, *m.REFEREE_FIELDS]
MISSING, INVALID = 'missing', 'invalid'


def write_judge3(folder, rows, grades):
    """One labels_s8_sheet_<qid>.csv per query; grade MISSING -> no row, INVALID -> a row with a bad grade."""
    by_q = {}
    for r in rows:
        g = grades[r['item_id']]
        by_q.setdefault(r['qid'], [])
        if g == MISSING:
            continue
        by_q[r['qid']].append(label(r['item_id'], 'BADVALUE' if g == INVALID else g))
    return [write_labels(Path(folder) / f'labels_s8_sheet_{q}.csv', ls) for q, ls in sorted(by_q.items())]


def ref(item_id, grade, reason_code='clear_reading', unsure='no', quote='', rule=''):
    return label(item_id, grade, unsure) | dict(reason_code=reason_code, override_quote=quote, override_rule=rule)


# ---------------------------------------------------------------- merge rule
def test_three_judge_merge_cases():
    rows = [dict(item_id=f'i{k}', qid='Q1') for k in range(6)]
    grades = {'i0': (3, 2, 3), 'i1': (2, 2, 3), 'i2': (0, 1, 1), 'i3': (1, 0, 0), 'i4': (1, 2, 1), 'i5': (3, 3, 1)}
    a, b, c = ({i: dict(grade=str(g[n])) for i, g in grades.items()} for n in range(3))
    accepted, disputes = m.merge(rows, a, b, c)
    assert accepted == {'i0': 3, 'i1': 2, 'i2': 1, 'i3': 0}  # medians, not the lowest
    assert disputes == ['i4', 'i5']  # any relevant-vs-not split
    two, _ = m.merge(rows, a, b)  # two judges: the lower of the two
    assert two['i0'] == 2 and two['i2'] == 0


def test_one_missing_judge_uses_the_two_judge_rule_and_two_missing_refuse():
    rows = [dict(item_id=f'i{k}', qid='Q1') for k in range(3)]
    a = {'i0': dict(grade='3'), 'i1': dict(grade='1'), 'i2': dict(grade='2')}
    b = {'i0': dict(grade='2'), 'i1': dict(grade='2'), 'i2': dict(grade='3')}
    c = {'i2': dict(grade='3')}  # C missing on i0, i1
    d = m.decide(rows, {'A': a, 'B': b, 'C': c})
    assert (d['i0']['rule'], d['i0']['tag'], d['i0']['consensus']) == ('two_judge', 'AB_min', 2)
    assert (d['i1']['rule'], d['i1']['consensus'], d['i1']['fallback_grade']) == ('two_judge', None, 1)
    assert (d['i2']['rule'], d['i2']['tag'], d['i2']['consensus']) == ('three_judge', 'ABC_median', 3)
    with pytest.raises(ValueError, match=r'1 item\(s\) have fewer than two valid judge rows'):
        m.decide(rows, {'A': a, 'B': {'i0': b['i0'], 'i2': b['i2']}, 'C': c})
    with pytest.raises(ValueError, match='fewer than two'):  # two-judge protocol: any missing row refuses
        m.decide(rows, {'A': a, 'B': {'i0': b['i0'], 'i2': b['i2']}})


# ---------------------------------------------------------------- Fleiss kappa
def test_fleiss_binary_known_example():
    # k relevant of 3 per item: 3,3,0,0,1,2 -> P = 1,1,1,1,1/3,1/3; P-bar = 7/9; p1 = 9/18; Pe = 1/2 -> 5/9
    ratings = [(3, 2, 3), (2, 2, 2), (0, 1, 0), (1, 1, 0), (2, 0, 1), (3, 3, 0)]
    assert m.fleiss_kappa_binary(ratings) == pytest.approx(5 / 9)
    assert m.fleiss_kappa_binary([(3, 3, 3), (0, 0, 1)]) == pytest.approx(1.0)
    assert np.isnan(m.fleiss_kappa_binary([(3, 3, 3), (2, 2, 3)]))  # one class only: undefined


def test_fleiss_matches_statsmodels_when_available():
    sm = pytest.importorskip('statsmodels.stats.inter_rater')
    rng = np.random.default_rng(5)
    ratings = [tuple(int(x) for x in rng.integers(0, 4, 3)) for _ in range(40)]
    table = np.array([[sum(not m.is_rel(g) for g in r), sum(m.is_rel(g) for g in r)] for r in ratings])
    assert m.fleiss_kappa_binary(ratings) == pytest.approx(sm.fleiss_kappa(table, method='fleiss'))


def test_fleiss_bootstrap_over_queries_is_seeded():
    per_qid = {q: sum((m.fleiss_item(k) for k in ks), np.zeros(3))
               for q, ks in (('Q1', [3, 3, 0]), ('Q2', [0, 1, 3]), ('Q3', [2, 0, 0, 3]))}
    a = m.bootstrap_ci(per_qid, m.fleiss_from_sums, n=400, seed=m.SEED)
    assert a == m.bootstrap_ci(per_qid, m.fleiss_from_sums, n=400, seed=m.SEED) and a['seed'] == 20261020
    assert a['lo'] <= a['hi']
    assert m.bootstrap_ci({}, m.fleiss_from_sums, n=10)['lo'] is None


# ---------------------------------------------------------------- pipeline fixture
QIDS = {'Q1': 20, 'Q2': 20, 'Q3': 20}
S1, S2, M1, M2 = 'S8-q10001', 'S8-q10003', 'S8-q10005', 'S8-q10007'
D1, D2, D3, D4 = 'S8-q10010', 'S8-q10012', 'S8-q10014', 'S8-q10016'


@pytest.fixture
def three(tmp_path, monkeypatch):
    real_open, real_bytes = Path.open, Path.read_bytes

    def guard(fn):
        def wrapped(self, *a, **kw):
            assert self.name not in SEALED, f'sealed file touched: {self.name}'
            return fn(self, *a, **kw)
        return wrapped
    for name in SEALED:
        (tmp_path / name).write_text('{"system": "SECRET"}\n', encoding='utf-8')
    monkeypatch.setattr(Path, 'open', guard(real_open))
    monkeypatch.setattr(Path, 'read_bytes', guard(real_bytes))
    rows = make_sheet(tmp_path / 'sheet.csv', QIDS)
    grades = {}
    for k, r in enumerate(rows):  # even: relevant (3, 2, 3) -> median 3; odd: (0, 1, 0) -> median 0
        grades[r['item_id']] = (3, 2, 3) if k % 2 == 0 else (0, 1, 0)
    grades[S1] = (1, 2, 1)  # split; median 1
    grades[S2] = (2, 3, 0)  # split; median 2
    grades[M1] = (1, 0, INVALID)  # C invalid -> A/B two-judge rule -> lower 0 (accepted)
    grades[M2] = (MISSING, 2, 1)  # A missing -> B/C split -> dispute; lower 1
    return tmp_path, rows, grades


def files(tmp, rows, grades, judges='ABC'):
    return {f'{x.lower()}_files': write_judge3(tmp / x, rows, {i: g[n] for i, g in grades.items()})
            for n, x in enumerate('ABC') if x in judges}


def run3(tmp, rows, grades, judges='ABC', **kw):
    kw.setdefault('_test_skip_judge_freeze', True)  # judge freezes: tests/test_s8_judge_check.py
    kw.setdefault('expected_items', len(rows))  # a fake sheet (the real one: m.SHEET_ITEMS = 1,482)
    return m.run_merge(sheet=tmp / 'sheet.csv', out=tmp / 'labels', bootstrap=300, **files(tmp, rows, grades, judges),
                       **kw)


def test_stage1_three_judges_agreement_and_counts(three):
    tmp, rows, grades = three
    s = run3(tmp, rows, grades)
    assert s['stage'] == 1 and s['mode'] == 'three_judge' and list(s['states']) == ['agreement_abc.json']
    agr = json.loads((tmp / 'labels' / 'agreement_abc.json').read_text(encoding='utf-8'))
    assert set(agr['pairs']) == {'A-B', 'A-C', 'B-C'}
    assert {p: v['n'] for p, v in agr['pairs'].items()} == {'A-B': 59, 'A-C': 58, 'B-C': 59}
    assert agr['pairs']['A-C']['confusion_4x4_rows_A_cols_C'] and agr['pairs']['B-C']['ci95_kappa_quadratic']['seed'] == m.SEED
    a = {i: dict(grade=str(g[0])) for i, g in grades.items() if g[0] not in (MISSING, INVALID)}
    b = {i: dict(grade=str(g[1])) for i, g in grades.items()}
    c = {i: dict(grade=str(g[2])) for i, g in grades.items() if g[2] not in (MISSING, INVALID)}
    direct = m.agreement(rows, a, c, bootstrap=300, names=('A', 'C'))
    assert agr['pairs']['A-C']['kappa_binary'] == direct['kappa_binary']
    assert agr['pairs']['A-C']['kappa_quadratic'] == direct['kappa_quadratic']
    ratings = [(int(a[i]['grade']), int(b[i]['grade']), int(c[i]['grade'])) for i in grades if i in a and i in c]
    assert agr['fleiss']['n'] == 58 and agr['fleiss']['kappa_binary'] == round(m.fleiss_kappa_binary(ratings), 4)
    assert agr['fleiss']['ci95_kappa_binary']['draws'] == 300
    assert agr['two_judge_rule_items_missing_row'] == 2  # M1 (C invalid) and M2 (A missing)
    assert agr['threshold_disputes'] == 3 and agr['accepted'] == 57
    assert agr['row_problems']['C'] == dict(missing=0, invalid=1, files={'labels_s8_sheet_Q1.csv': 1})
    assert agr['row_problems']['A'] == dict(missing=1, invalid=0, files={'labels_s8_sheet_Q1.csv': 1})
    assert agr['c_missing_or_invalid_rate'] == round(1 / 60, 4)
    text = json.dumps(agr)
    assert 'BADVALUE' not in text and 'SECRET' not in text  # counts only, no field values


def test_c_above_two_percent_is_refused(three):
    tmp, rows, grades = three
    grades[rows[20]['item_id']] = (3, 2, MISSING)  # 2 of 60 C rows = 3.3% > 2%
    with pytest.raises(ValueError, match=r'judge C: 2 of 60 rows missing or invalid \(3.3% > 2%\).*two-judge-fallback'):
        run3(tmp, rows, grades)
    assert not (tmp / 'labels').exists()
    grades[M2] = (1, 2, 1)  # the two-judge protocol needs A and B on every item
    s = run3(tmp, rows, grades, fallback_reason='C over 2% after one re-run')  # the fallback still runs
    assert s['mode'] == 'two_judge_fallback'


def test_c_at_two_percent_is_accepted(tmp_path):
    rows = make_sheet(tmp_path / 'sheet.csv', {'Q1': 50})
    grades = {r['item_id']: (3, 3, 3) for r in rows} | {rows[0]['item_id']: (0, 0, MISSING)}  # 1 of 50 = 2%
    assert run3(tmp_path, rows, grades)['mode'] == 'three_judge'


def test_file_level_problems_still_refuse(three):
    tmp, rows, grades = three
    fs = files(tmp, rows, grades)
    q2 = tmp / 'C' / 'labels_s8_sheet_Q2.csv'
    write_labels(q2, [label(rows[20]['item_id'], 3), label(rows[20]['item_id'], 2)])
    with pytest.raises(ValueError, match='more than once'):
        m.run_merge(sheet=tmp / 'sheet.csv', out=tmp / 'labels', bootstrap=50, _test_skip_judge_freeze=True,
                    expected_items=len(rows), **fs)
    write_labels(tmp / 'C' / 'labels_s8_sheet_Q1.csv', [label(r['item_id'], 3) for r in rows[1:20]])
    write_labels(q2, [label(rows[0]['item_id'], 3)])  # a Q1 item in the Q2 file
    with pytest.raises(ValueError, match='another query'):
        m.run_merge(sheet=tmp / 'sheet.csv', out=tmp / 'labels', bootstrap=50, _test_skip_judge_freeze=True,
                    expected_items=len(rows), **fs)
    write_labels(q2, [label('S8-zzz', 3)])
    with pytest.raises(ValueError, match='unknown item id'):
        m.run_merge(sheet=tmp / 'sheet.csv', out=tmp / 'labels', bootstrap=50, _test_skip_judge_freeze=True,
                    expected_items=len(rows), **fs)
    write_labels(q2, [label(rows[20]['item_id'], 3) | {'score': '1'}], [*m.LABEL_FIELDS, 'score'])
    with pytest.raises(ValueError, match='unknown columns'):
        m.run_merge(sheet=tmp / 'sheet.csv', out=tmp / 'labels', bootstrap=50, _test_skip_judge_freeze=True,
                    expected_items=len(rows), **fs)


def test_two_judge_mode_needs_c_or_a_fallback_reason_for_stage2(three):
    tmp, rows, grades = three
    grades = {i: (g[0] if g[0] not in (MISSING, INVALID) else 1, g[1], g[2]) for i, g in grades.items()}
    audit = write_labels(tmp / 'audit.csv', [label(rows[0]['item_id'], 3)])
    with pytest.raises(PermissionError, match='--c.*two-judge-fallback'):
        run3(tmp, rows, grades, 'AB', audit_ids=[rows[0]['item_id']], audit_file=audit, _test_skip_freeze=True)
    with pytest.raises(ValueError, match='needs a reason'):
        run3(tmp, rows, grades, 'AB', fallback_reason='  ')
    s = run3(tmp, rows, grades, 'AB')  # stage 1 alone is allowed
    assert s['mode'] == 'two_judge' and list(s['states']) == ['agreement_ab.json']


def test_fallback_with_c_merges_a_b_only_and_reports_c_as_secondary(three):
    tmp, rows, grades = three
    grades[M2] = (1, 2, 1)  # the two-judge protocol needs A and B on every item
    s = run3(tmp, rows, grades, fallback_reason='C quota exhausted on day 3')
    agr = json.loads((tmp / 'labels' / 'agreement_ab.json').read_text(encoding='utf-8'))
    assert s['mode'] == agr['mode'] == 'two_judge_fallback' and agr['judges_in_force'] == ['A', 'B']
    assert agr['fallback_reason'] == 'C quota exhausted on day 3'
    assert set(agr['secondary_c']) == {'A-C', 'B-C'} and agr['secondary_c']['A-C']['n'] == 59
    assert agr['n'] == 60 and agr['two_judge_rule_items_missing_row'] == 0
    accepted, disputes = m.merge(rows, *({i: dict(grade=str(g[n])) for i, g in grades.items()} for n in (0, 1)))
    assert agr['accepted'] == len(accepted) and agr['threshold_disputes'] == len(disputes)
    assert S2 not in disputes  # (2, 3) same side for A/B; C's 0 is ignored in the fallback


# ---------------------------------------------------------------- stage 2 + 3 (three judges)
def audit_rows(rows, grades):
    """Author labels on the first 40 items: agree with the median consensus except D1-D4 (relevant -> 0)."""
    out = []
    for r in rows[:40]:
        g = grades[r['item_id']]
        valid = [x for x in g if x not in (MISSING, INVALID)]
        side = sorted(valid)[len(valid) // 2] if len(valid) == 3 else min(valid)
        out.append(label(r['item_id'], 0 if r['item_id'] in (D1, D2, D3, D4) else side,
                         'yes' if r['item_id'] == D1 else 'no'))
    return out


def stage2(tmp, rows, grades, **kw):
    audit = write_labels(tmp / 'audit.csv', audit_rows(rows, grades))
    return run3(tmp, rows, grades, audit_ids=[r['item_id'] for r in rows[:40]], audit_file=audit,
                _test_skip_freeze=True, **kw)


def test_stage2_primary_and_secondary_kappa_vs_median_consensus(three):
    tmp, rows, grades = three
    s = stage2(tmp, rows, grades)
    check = json.loads((tmp / 'labels' / 'audit_check.json').read_text(encoding='utf-8'))
    assert check['mode'] == 'three_judge' and check['audit_mode'] == 'check_only (note 2)'
    # note 3 section 4: M1 (A/B two-judge rule, C invalid) is left out of the primary kappa and counted
    assert check['on_accepted'] == 36 and check['on_disputes'] == 3 and check['on_two_judge_rule'] == 1
    assert check['disagreements'] == 4 and check['flag'] == 'PASS'
    # primary: 36 items; consensus relevant = the 20 even items; the author says not relevant on D1-D4
    b = np.array(check['confusion_2x2_rows_shyam_cols_consensus'])
    assert b.sum() == 36 and b[0, 1] == 4 and b[1, 0] == 0
    assert check['kappa_binary'] == round(m.kappa_from_confusion(b), 4)
    sec = check['secondary']
    assert sec['n'] == 35 and np.array(sec['confusion_2x2_rows_shyam_cols_consensus'])[0, 1] == 3  # D1 unsure
    assert sec['kappa_binary'] > check['kappa_binary'] and check['ci95_kappa_binary']['seed'] == m.SEED
    assert check['report_sentence'].startswith('The author, a non-specialist, audited 40 items blind;')
    assert s['referee'] == {'threshold_split': 3, 'audit_disagrees': 4, 'total': 7}
    disputes = {d['item_id']: d for d in m.read_disputes(tmp / 'labels' / 'disputes.csv')}
    assert disputes[S1]['c_grade'] == '1' and disputes[S2]['c_grade'] == '0' and disputes[D1]['reason'] == 'audit_disagrees'
    assert disputes[M2]['a_grade'] == '' and disputes[M2]['b_grade'] == '2'  # A's missing row stays blank
    assert not {'system', 'rank', 'score'} & set(m.DISPUTE_COLUMNS)
    assert [f'c_{f}' for f in m.JUDGE_FIELDS] == m.DISPUTE_COLUMNS[-len(m.JUDGE_FIELDS):]


def test_stage2_stop_is_on_the_primary_kappa_only(three):
    tmp, rows, grades = three
    flipped = [label(r['item_id'], 0 if l['grade'] in '23' else 3, 'yes' if k else 'no')
               for k, (r, l) in enumerate(zip(rows[:40], audit_rows(rows, grades)))]
    audit = write_labels(tmp / 'audit.csv', flipped)  # all disagree; the only "sure" item cannot rescue it
    s = run3(tmp, rows, grades, audit_ids=[r['item_id'] for r in rows[:40]], audit_file=audit, _test_skip_freeze=True)
    assert s['audit']['flag'] == 'STOP' and 'stopped' in s and not (tmp / 'labels' / 'disputes.csv').exists()


def good_referee(rows):
    text = {r['item_id']: r['text'] for r in rows}
    return [ref(S1, 2),
            ref(S2, 3, 'unclear_text', unsure='yes'),
            ref(M2, 3, 'guideline_gap', unsure='yes'),
            ref(D1, 0),
            ref(D2, 0, unsure='yes', quote=text[D2], rule='13'),  # unsure -> consensus stands (C4 a)
            ref(D3, 1, quote='  ' + text[D3].replace(' ', '\n   ') + ' ', rule='13'),  # whitespace-normalised match
            ref(D4, 0, quote=text[D4], rule='')]  # quote without a rule -> consensus stands


def test_stage3_categories_reason_codes_and_judge_right(three):
    from retrieval_eval import qrels as qrels_io
    tmp, rows, grades = three
    stage2(tmp, rows, grades)
    referee = write_labels(tmp / 'ref.csv', good_referee(rows), REF_FIELDS)
    s = stage2(tmp, rows, grades, referee_file=referee)
    assert s['stage'] == 3
    meta = json.loads((tmp / 'labels' / 'qrels_meta.json').read_text(encoding='utf-8'))
    assert meta['mode'] == 'three_judge' and meta['judges_in_force'] == ['A', 'B', 'C']
    assert meta['categories'] == dict(accepted_median=52, accepted_lower=1, dispute_referee=1, dispute_unsure_median=1,
                                      dispute_unsure_lower=1, auditor_disagrees_kept=3, auditor_override_evidence=1)
    assert meta['reason_codes'] == dict(clear_reading=5, guideline_gap=1, unclear_text=1)
    # note 4 section 3: judge_right only where the referee sets the grade: S1 (dispute_referee, final 2 vs
    # 1, 2, 1) and D3 (auditor_override_evidence, final 1 vs 3, 2, 3); kept / unsure items are per category only
    assert meta['judge_right'] == {'A': dict(right=0, judged=2), 'B': dict(right=1, judged=2),
                                   'C': dict(right=0, judged=2)}
    assert meta['judge_right_by_reason'] == {
        'threshold_split': {'A': dict(right=0, judged=1), 'B': dict(right=1, judged=1), 'C': dict(right=0, judged=1)},
        'audit_disagrees': {'A': dict(right=0, judged=1), 'B': dict(right=0, judged=1), 'C': dict(right=0, judged=1)}}
    assert meta['judge_right_by_category'] == {
        'dispute_referee': {'A': dict(right=0, judged=1), 'B': dict(right=1, judged=1), 'C': dict(right=0, judged=1)},
        'dispute_unsure_median': {'A': dict(right=1, judged=1), 'B': dict(right=1, judged=1),
                                  'C': dict(right=0, judged=1)},  # S2: (2, 3, 0) -> 2
        'dispute_unsure_lower': {'A': dict(right=0, judged=0), 'B': dict(right=0, judged=1),
                                 'C': dict(right=1, judged=1)},  # M2: (-, 2, 1) -> 1
        'auditor_disagrees_kept': {j: dict(right=3, judged=3) for j in 'ABC'},  # D1, D2, D4: right by construction
        'auditor_override_evidence': {j: dict(right=0, judged=1) for j in 'ABC'}}
    # S1: majority side of (1, 2, 1) is "not relevant"; the referee chose 2 -> against the majority
    assert (meta['referee_with_majority'], meta['referee_against_majority']) == (0, 1)
    assert meta['audit_disagrees_two_judge_rule'] == 0
    assert meta['override_flipped_side'] == 1 and meta['two_judge_rule_items_missing_row'] == 2
    got = {r['clause_id']: (r['grade'], r['judge']) for r in qrels_io.read(tmp / 'labels' / 'qrels.tsv')}
    cid = {r['item_id']: r['clause_id'] for r in rows}
    assert got[cid[S1]] == (2, 'referee')
    assert got[cid[S2]] == (2, 'ABC_median')  # unsure -> median of (2, 3, 0)
    assert got[cid[M2]] == (1, 'BC_min')  # unsure on a two-judge item -> lower of (2, 1)
    assert got[cid[M1]] == (0, 'AB_min')
    assert got[cid[D1]] == got[cid[D2]] == got[cid[D4]] == (3, 'ABC_median')
    assert got[cid[D3]] == (1, 'referee')
    assert got[cid[rows[0]['item_id']]] == (3, 'ABC_median') and got[cid[rows[9]['item_id']]] == (0, 'ABC_median')
    assert meta['source'] == {'ABC_median': 56, 'AB_min': 1, 'BC_min': 1, 'referee': 2}  # 52 + S2 + D1, D2, D4


def test_split_flip_on_a_two_judge_rule_item_uses_the_lower_of_the_two(three):
    tmp, rows, grades = three
    stage2(tmp, rows, grades)
    labels = [ref(M2, 2) if r['item_id'] == M2 else r for r in good_referee(rows)]  # M2 (-, 2, 1): lower 1
    stage2(tmp, rows, grades, referee_file=write_labels(tmp / 'ref.csv', labels, REF_FIELDS))
    meta = json.loads((tmp / 'labels' / 'qrels_meta.json').read_text(encoding='utf-8'))
    assert meta['categories']['dispute_referee'] == 2 and meta['categories']['dispute_unsure_lower'] == 0
    assert (meta['referee_with_majority'], meta['referee_against_majority']) == (0, 2)  # S1 and M2: both against
    assert meta['judge_right']['A'] == dict(right=0, judged=2)  # A has no valid row on M2


def test_audit_unsure_with_quote_and_rule_keeps_the_consensus():
    """Note 4 section 2: an override needs a found quote, a rule 1-16 AND unsure = no."""
    d = dict(rule='three_judge', tag='ABC_median', grades=[3, 2, 3], fallback_grade=3, consensus=3)
    base = dict(grade='0', override_quote='q', override_rule='5', reason_code='clear_reading')
    assert m.final_grade(d, 'audit_disagrees', base | {'unsure': 'yes'}) == (3, 'ABC_median', 'auditor_disagrees_kept')
    assert m.final_grade(d, 'audit_disagrees', base | {'unsure': 'no'}) == (0, 'referee', 'auditor_override_evidence')


def test_stage1_refuses_a_duplicate_qid_clause_pair_naming_it(three):
    tmp, rows, grades = three
    rows[5]['clause_id'] = rows[2]['clause_id']
    (tmp / 'sheet.csv').write_text(m.sheet_csv(rows), encoding='utf-8', newline='')
    with pytest.raises(ValueError, match=rf"1 duplicate \(qid, clause_id\) pair\(s\): \(Q1, {rows[2]['clause_id']}\): "
                                         rf"items {rows[2]['item_id']} and {rows[5]['item_id']}"):
        run3(tmp, rows, grades)
    assert not (tmp / 'labels').exists()


def test_stage1_checks_the_s8_sheet_record(three):
    tmp, rows, grades = three
    (tmp / m.SHEET_META).write_text(json.dumps(dict(items=1482)), encoding='utf-8')
    with pytest.raises(ValueError, match='holds 60 items; sheet_meta.json records 1482'):
        run3(tmp, rows, grades)
    (tmp / m.SHEET_META).unlink()
    with pytest.raises(PermissionError, match='sheet_meta.json .* missing'):
        run3(tmp, rows, grades)
    assert not (tmp / 'labels').exists()
    (tmp / m.SHEET_META).write_text(json.dumps(dict(items=60)), encoding='utf-8')
    run3(tmp, rows, grades)
    agr = json.loads((tmp / 'labels' / 'agreement_abc.json').read_text(encoding='utf-8'))
    assert 'sheet_meta_sha256' not in agr['inputs']  # stage-1 bytes unchanged; recorded in qrels_meta (stage 3)


def _change_one_clause_text(tmp, rows):
    """Same items and count, one byte of clause text different: the sheet sha changes."""
    changed = [dict(r) for r in rows]
    changed[7]['text'] += '.'
    (tmp / 'sheet.csv').write_text(m.sheet_csv(changed), encoding='utf-8', newline='')


@pytest.mark.parametrize('stage', [1, 2, 3])
def test_every_stage_refuses_an_item_count_other_than_1482(three, stage):
    tmp, rows, grades = three
    if stage == 2:
        run3(tmp, rows, grades)  # stage 1 done; stage 2 is refused
    if stage == 3:
        stage2(tmp, rows, grades)  # stages 1-2 done; stage 3 is refused
    kw = dict(referee_file=write_labels(tmp / 'ref.csv', good_referee(rows), REF_FIELDS)) if stage == 3 else {}
    call = (lambda: run3(tmp, rows, grades, expected_items=None)) if stage == 1 else \
        (lambda: stage2(tmp, rows, grades, expected_items=None, **kw))
    assert m.SHEET_ITEMS == 1482
    with pytest.raises(ValueError, match='sheet.csv holds 60 items; the S8 sheet has 1482'):
        call()
    assert not (tmp / 'labels' / ('agreement_abc.json' if stage == 1 else
                                  'audit_check.json' if stage == 2 else 'qrels.tsv')).exists()


def test_stage2_refuses_a_sheet_that_differs_from_the_stage1_record(three):
    tmp, rows, grades = three
    run3(tmp, rows, grades)  # stage 1 records inputs.sheet_sha256
    _change_one_clause_text(tmp, rows)
    with pytest.raises(PermissionError, match=r'differs from the one stage 1 recorded in agreement_abc.json'):
        stage2(tmp, rows, grades)
    assert not (tmp / 'labels' / 'audit_check.json').exists()


def test_stage3_refuses_a_sheet_that_differs_from_the_stage1_record(three):
    tmp, rows, grades = three
    stage2(tmp, rows, grades)
    referee = write_labels(tmp / 'ref.csv', good_referee(rows), REF_FIELDS)
    _change_one_clause_text(tmp, rows)
    with pytest.raises(PermissionError, match=r'inputs.sheet_sha256 mismatch\): stages 2 and 3 refused'):
        stage2(tmp, rows, grades, referee_file=referee)
    assert not (tmp / 'labels' / 'qrels.tsv').exists() and not (tmp / 'labels' / 'qrels_meta.json').exists()


def test_stage3_records_the_checked_sheet_sha_in_qrels_meta(three):
    tmp, rows, grades = three
    run3(tmp, rows, grades)
    stage2(tmp, rows, grades)
    stage2(tmp, rows, grades, referee_file=write_labels(tmp / 'ref.csv', good_referee(rows), REF_FIELDS))
    meta = json.loads((tmp / 'labels' / 'qrels_meta.json').read_text(encoding='utf-8'))
    agr = json.loads((tmp / 'labels' / 'agreement_abc.json').read_text(encoding='utf-8'))
    assert meta['sheet_check']['sheet_sha256'] == agr['inputs']['sheet_sha256'] == m.bytes_sha(tmp / 'sheet.csv')
    assert meta['sheet_check']['items'] == 60 and meta['sheet_check']['stage1_record'] == 'matches agreement_abc.json'
    assert meta['sheet_check']['sheet_meta_sha256'] == m.bytes_sha(tmp / m.SHEET_META)


@pytest.mark.parametrize('item,change,match', [
    (D3, dict(override_quote='text Q1 99'), 'override_quote not found'),
    (D3, dict(override_quote='TEXT Q1 14'), 'override_quote not found'),  # case-sensitive
    (S1, dict(override_quote='not in this clause'), 'override_quote not found'),  # checked on any item
    (D3, dict(override_rule='17'), 'override_rule'),
    (D3, dict(override_rule='0'), 'override_rule'),
    (D3, dict(override_rule='rule 14'), 'override_rule'),
    (S1, dict(reason_code=''), 'reason_code'),
    (D1, dict(reason_code='A_right'), 'reason_code'),
])
def test_stage3_refuses_bad_referee_fields(three, item, change, match):
    tmp, rows, grades = three
    stage2(tmp, rows, grades)
    bad = [r | change if r['item_id'] == item else r for r in good_referee(rows)]
    with pytest.raises(ValueError, match=match):
        stage2(tmp, rows, grades, referee_file=write_labels(tmp / 'ref.csv', bad, REF_FIELDS))
    assert not (tmp / 'labels' / 'qrels.tsv').exists()


def test_stage3_refuses_a_referee_file_without_reason_code_column(three):
    tmp, rows, grades = three
    stage2(tmp, rows, grades)
    plain = write_labels(tmp / 'ref.csv', good_referee(rows), [*m.LABEL_FIELDS, 'override_quote', 'override_rule'])
    with pytest.raises(ValueError, match="reason_code ''"):
        stage2(tmp, rows, grades, referee_file=plain)


def test_fallback_stage3_unsure_dispute_takes_the_lower(three):
    tmp, rows, grades = three
    grades[M2] = (1, 2, 1)
    kw = dict(fallback_reason='TEST fallback')
    stage2(tmp, rows, grades, **kw)
    ids = (tmp / 'labels' / 'referee_ids.txt').read_text(encoding='utf-8').split()
    text = {r['item_id']: r['text'] for r in rows}
    labels = [ref(i, 3, 'unclear_text', unsure='yes') if i in (S1, M2) else
              ref(i, 1, quote=text[i], rule='14') if i == D1 else ref(i, 0) for i in ids]
    s = stage2(tmp, rows, grades, referee_file=write_labels(tmp / 'ref.csv', labels, REF_FIELDS), **kw)
    meta = json.loads((tmp / 'labels' / 'qrels_meta.json').read_text(encoding='utf-8'))
    assert meta['mode'] == 'two_judge_fallback' and meta['judges_in_force'] == ['A', 'B']
    assert meta['fallback_reason'] == 'TEST fallback' and set(meta['judge_right']) == {'A', 'B'}
    assert meta['categories']['dispute_unsure_lower'] == 2 and meta['categories']['dispute_unsure_median'] == 0
    assert meta['categories']['accepted_median'] == 0 and meta['categories']['auditor_override_evidence'] == 1
    assert S2 not in ids  # (2, 3): same side for A/B
    assert s['qrels']['source']['AB_min'] == 60 - meta['categories']['dispute_referee'] - 1


# ---------------------------------------------------------------- CLI
def test_cli_merge_has_c_and_fallback_and_refuses_stage2_without_them(three, monkeypatch):
    tmp, rows, grades = three
    fs = files(tmp, rows, grades)
    audit = write_labels(tmp / 'audit.csv', [label(rows[0]['item_id'], 3)])
    with pytest.raises(PermissionError, match='--c'):
        m.main(['s8-merge-labels', '--a', str(tmp / 'A'), '--b', str(tmp / 'B'), '--sheet', str(tmp / 'sheet.csv'),
                '--out', str(tmp / 'labels'), '--audit', str(audit), '--audit-dir', str(tmp)])
    with pytest.raises(ValueError, match='needs a reason'):
        m.main(['s8-merge-labels', '--a', str(tmp / 'A'), '--b', str(tmp / 'B'), '--c', str(tmp / 'C'),
                '--two-judge-fallback', '', '--sheet', str(tmp / 'sheet.csv'), '--out', str(tmp / 'labels')])
    assert fs and not (tmp / 'labels').exists()


# ---------------------------------------------------------------- referee tool
@pytest.fixture
def referee_app(tmp_path):
    rows = make_sheet(tmp_path / 'ref_sheet.csv', {'Q1': 3})
    d = []
    for k, r in enumerate(rows):
        row = {c: '' for c in m.DISPUTE_COLUMNS} | dict(item_id=r['item_id'], reason='threshold_split', qid='Q1',
                                                       a_grade='1', b_grade='2', a_unsure='no', b_unsure='no')
        if k < 2:
            row |= dict(c_grade='3', c_unsure='yes', c_note='C-note')
        d.append(row)
    (tmp_path / 'disputes.csv').write_text(m.csv_text(d, m.DISPUTE_COLUMNS), encoding='utf-8', newline='')
    (tmp_path / 'audit_freeze.json').write_text(json.dumps(dict(labels_file='labels_shyam_audit.csv')))
    app = t.LabelApp(tmp_path / 'ref_sheet.csv', tmp_path / 'labels_referee.csv', 'referee', tmp_path / 'disputes.csv',
                     tmp_path / 'audit_freeze.json')
    return app, rows, tmp_path


def payload(item_id, **kw):
    return dict(item_id=item_id, grade='2', clause_states='partial', jurisdiction_match='not_applicable',
                evidence_span='', unsure='no', note='', reason_code='clear_reading', override_quote='',
                override_rule='') | kw


def test_referee_tool_shows_judge_c_when_present(referee_app):
    app, rows, _ = referee_app
    first, last = app.item(0), app.item(2)
    c_column = str(app.judge_order(first['item']['item_id']).index('c') + 1)  # blinded: Judge 1-3
    assert set(first['judges']) == {'1', '2', '3'} and first['judges'][c_column]['note'] == 'C-note'
    assert set(last['judges']) == {'1', '2'}  # two-judge item / fallback: no empty C column
    assert 'c' not in app.judge_order(last['item']['item_id'])
    assert "Object.keys(d.judges)" in t.PAGE and 'reason_code' in t.PAGE and 'override_quote' in t.PAGE


def test_referee_tool_requires_reason_code_and_checks_rule_and_quote(referee_app):
    app, rows, tmp = referee_app
    i = rows[0]['item_id']
    for bad, match in ((dict(reason_code=''), 'reason_code'), (dict(reason_code='B_right'), 'reason_code'),
                       (dict(override_rule='17'), 'override_rule'), (dict(override_quote='absent words'), 'not found')):
        with pytest.raises(ValueError, match=match):
            app.save(payload(i, **bad))
    assert not (tmp / 'labels_referee.csv').exists()
    app.save(payload(i, reason_code='guideline_gap', override_quote=rows[0]['text'].replace(' ', '  '),
                     override_rule='16'))
    saved = m.read_labels([tmp / 'labels_referee.csv'], 'referee', m.REFEREE_FIELDS)
    assert saved[i]['reason_code'] == 'guideline_gap' and saved[i]['override_rule'] == '16'
    header = (tmp / 'labels_referee.csv').read_text(encoding='utf-8-sig').splitlines()[0].split(',')
    assert tuple(header) == t.REFEREE_TOOL_FIELDS
    again = t.LabelApp(tmp / 'ref_sheet.csv', tmp / 'labels_referee.csv', 'referee', tmp / 'disputes.csv',
                       tmp / 'audit_freeze.json')  # resume keeps the referee fields
    assert again.item(0)['label']['reason_code'] == 'guideline_gap'


def test_audit_tool_columns_are_unchanged(tmp_path):
    rows = make_sheet(tmp_path / 'sheet.csv', {'Q1': 2})
    app = t.LabelApp(tmp_path / 'sheet.csv', tmp_path / 'labels.csv')
    assert app.fields == t.TOOL_FIELDS == ('item_id', 'clause_states', 'jurisdiction_match', 'evidence_span', 'grade',
                                           'unsure', 'note', 'timestamp')
    app.save(payload(rows[0]['item_id']))  # referee-only keys in the payload are ignored in audit mode
    header = (tmp_path / 'labels.csv').read_text(encoding='utf-8-sig').splitlines()[0].split(',')
    assert tuple(header) == t.TOOL_FIELDS


# ---------------------------------------------------------------- kits: 2 or 3 roots
@pytest.fixture
def src(tmp_path):
    d = tmp_path / 'repo_data'
    d.mkdir()
    make_sheet(d / 'practice_sheet.csv', {'P1': 5})
    (d / 'per_query').mkdir()
    make_sheet(d / 'per_query' / 's8_sheet_Q1.csv', {'Q1': 3})
    return d


def test_three_kits_are_made_and_checked(tmp_path, src):
    roots = [tmp_path / f's8_judge{x}' for x in 'ABC']
    trees = j.make_kits(roots, src / 'practice_sheet.csv', repo=tmp_path / 'repo')
    assert len(trees) == 3 and trees[str(roots[0])] == trees[str(roots[2])]
    ok = j.check_kits(roots, src / 'practice_sheet.csv', src / 'per_query', repo=tmp_path / 'repo')
    assert ok == {str(r): [] for r in roots}
    (roots[2] / 'GUIDELINE.md').write_text('guideline v1.0 ...', encoding='utf-8')  # only kit C differs
    problems = j.check_kits(roots, src / 'practice_sheet.csv', src / 'per_query', repo=tmp_path / 'repo')
    assert all('GUIDELINE.md differs between kits' in p for p in problems.values())
    assert 'GUIDELINE.md differs from the guideline in force v1.0.1' in problems[str(roots[2])]
    (roots[1] / 'notes.txt').write_text('x', encoding='utf-8')
    problems = j.check_kits(roots, src / 'practice_sheet.csv', src / 'per_query', repo=tmp_path / 'repo')
    assert 'unexpected file notes.txt' in problems[str(roots[1])]


@pytest.mark.parametrize('n', [1, 4])
def test_kit_root_count_must_be_two_or_three(tmp_path, src, n):
    roots = [tmp_path / f'kit{k}' for k in range(n)]
    with pytest.raises(ValueError, match='2 or 3 kit roots'):
        j.make_kits(roots, src / 'practice_sheet.csv', repo=tmp_path / 'repo')
    with pytest.raises(ValueError, match='2 or 3 kit roots'):
        j.check_kits(roots, src / 'practice_sheet.csv', src / 'per_query', repo=tmp_path / 'repo')
    with pytest.raises(SystemExit):
        j.main(['s8-check-kits', '--roots', *map(str, roots)])


def test_kit_cli_defaults_to_three_roots():
    import inspect
    assert "nargs='+', default=[str(r) for r in KIT_ROOTS]" in inspect.getsource(j.main)
    assert len(j.KIT_ROOTS) == 3 and j.KIT_ROOTS[2] == Path('D:/s8_judgeC')
