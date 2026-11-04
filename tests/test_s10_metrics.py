"""S10 eval metrics on hand-made fakes (values computed by hand; see the step log)."""
import math

import pytest

import _netguard
from eval.s10 import metrics as M


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')


N, R, MAND = 'not_applicable', 'recommended', 'mandatory'


def cell(pid, item, gold, final, rule, llm, repeat=1):
    return dict(profile_id=pid, item=item, gold=gold, final=final, rule=rule, llm=llm, repeat=repeat, setting='s')


# Worked example: 2 profiles, 5 cells.
CELLS = [
    cell('P1', 'a', MAND, MAND, MAND, MAND),       # correct
    cell('P1', 'b', MAND, R, R, N),                # under-call; LLM downgrade blocked
    cell('P1', 'c', N, R, N, R),                   # over-call; unwarranted upgrade
    cell('P2', 'd', R, R, N, R),                   # warranted upgrade
    cell('P2', 'e', N, N, N, 'omitted'),
]


def test_confusion_matrix():
    assert M.confusion(CELLS) == [[1, 1, 0], [0, 1, 0], [0, 1, 1]]
    assert M.confusion(CELLS, 'rule') == [[2, 0, 0], [1, 0, 0], [0, 1, 1]]


def test_worked_example_hybrid():
    assert M.under_call_rate(CELLS) == pytest.approx(1 / 2)
    assert M.over_call_rate(CELLS) == pytest.approx(1 / 3)
    assert M.accuracy(CELLS) == pytest.approx(3 / 5)
    assert M.macro_f1(CELLS) == pytest.approx(11 / 18)      # (2/3 + 1/2 + 2/3) / 3
    assert M.qw_kappa(CELLS) == pytest.approx(2 / 3)        # 1 - 0.1/0.3
    assert M.binary_kappa(CELLS) == pytest.approx(6 / 11)   # (0.8-0.56)/(1-0.56)


def test_worked_example_rules_only_and_lift():
    assert M.under_call_rate(CELLS, 'rule') == pytest.approx(1 / 2)
    assert M.over_call_rate(CELLS, 'rule') == 0
    assert M.accuracy(CELLS, 'rule') == pytest.approx(3 / 5)
    out = M.lift(CELLS, M.over_call_rate, n_boot=50, seed=1)
    assert out['lift'] == pytest.approx(1 / 3) and out['hybrid'] == pytest.approx(1 / 3) and out['rules'] == 0


def test_upgrade_precision_recall():
    assert M.upgrade_pr(CELLS) == dict(upgrades=2, warranted=2, warranted_upgrades=1, precision=0.5, recall=0.5)


def test_blocked_downgrades_and_disagreements():
    assert M.blocked_downgrades(CELLS) == dict(blocked_downgrades=1, omitted_rule_parts=1, llm_unavailable=0)
    table = M.disagreement_table(CELLS)
    assert table['table'] == dict(rules_right=1, llm_right=1, both_wrong=1, n=3)
    assert {r['item']: r['kind'] for r in table['rows']} == {'b': 'both_wrong', 'c': 'rules_right', 'd': 'llm_right'}


def test_llm_unavailable_cells_never_count_as_disagreement():
    cells = [cell('P', 'x', MAND, MAND, MAND, None)]
    assert M.disagreement_table(cells)['table']['n'] == 0
    assert M.blocked_downgrades(cells)['llm_unavailable'] == 1


def test_perfect_and_undefined_kappa():
    perfect = [cell('P', i, lab, lab, lab, lab) for i, lab in enumerate([N, R, MAND])]
    assert M.qw_kappa(perfect) == pytest.approx(1) and M.binary_kappa(perfect) == pytest.approx(1)
    assert M.macro_f1(perfect) == pytest.approx(1) and M.under_call_rate(perfect) == 0
    constant = [cell('P', i, MAND, MAND, MAND, MAND) for i in range(3)]
    assert math.isnan(M.qw_kappa(constant)) and math.isnan(M.binary_kappa(constant))
    assert math.isnan(M.over_call_rate(constant))      # no gold non-mandatory cell
    assert math.isnan(M.accuracy([])) and math.isnan(M.macro_f1([]))


def test_under_call_counts_recommended_and_na():
    cells = [cell('P', 1, MAND, R, R, R), cell('P', 2, MAND, N, N, N), cell('P', 3, MAND, MAND, MAND, MAND),
             cell('P', 4, R, N, N, N)]                  # gold R -> N is not an under-call of a mandatory
    assert M.under_call_rate(cells) == pytest.approx(2 / 3)


def test_stability_identical_share_and_flip_rate():
    cells = [cell('P1', 'a', MAND, MAND, MAND, MAND, r) for r in (1, 2)]
    cells += [cell('P1', 'b', N, N, N, N, r) for r in (1, 2)]
    cells += [cell('P2', 'a', MAND, MAND, MAND, MAND, r) for r in (1, 2)]
    cells += [cell('P2', 'b', N, N, N, N, 1), cell('P2', 'b', N, R, N, R, 2)]
    out = M.stability(cells)
    assert out['identical_profile_share'] == 0.5 and out['cell_flip_rate'] == 0.25
    assert out['flipping_cells'] == [dict(profile_id='P2', item='b', labels=[N, R])]
    assert math.isnan(M.stability(cells[:1])['cell_flip_rate'])     # k=1: undefined


def test_bootstrap_resamples_profiles_not_cells():
    seen = []

    def record(cells):
        seen.append(sorted({c['profile_id'] for c in cells}))
        groups = {}
        for c in cells:
            groups.setdefault(c['profile_id'], []).append(c['item'])
        # Every drawn profile brings ALL its cells, as whole blocks (P1 = a,b,c; P2 = d,e).
        for pid, items in groups.items():
            block = ['a', 'b', 'c'] if pid == 'P1' else ['d', 'e']
            assert len(items) % len(block) == 0 and sorted(set(items)) == block
        assert len(cells) in {4, 5, 6}          # 2 draws of 3- or 2-cell profiles
        return float(len(cells))

    out = M.bootstrap_ci(CELLS, record, n_boot=300, seed=7)
    assert len(seen) == 301 and out['n_profiles'] == 2 and 4 <= out['lo'] <= out['hi'] <= 6
    assert ['P1'] in seen and ['P2'] in seen          # with replacement: one profile drawn twice


def test_bootstrap_deterministic_and_seed_sensitive():
    many = [cell(f'P{i}', 'x', MAND, MAND if i % 3 else R, MAND, MAND) for i in range(30)]
    a = M.bootstrap_ci(many, M.under_call_rate, n_boot=200, seed=3)
    assert a == M.bootstrap_ci(many, M.under_call_rate, n_boot=200, seed=3)
    assert a != M.bootstrap_ci(many, M.under_call_rate, n_boot=200, seed=4)
    assert a['lo'] <= a['point'] <= a['hi'] and a['n_boot'] == 200


def test_fast_bootstrap_matches_generic_bootstrap():
    many = [cell(f'P{i}', it, gold, final, rule, None)
            for i in range(12) for it, gold, final, rule in
            [('x', MAND, MAND if i % 4 else R, R), ('y', N, R if i % 5 == 0 else N, N), ('z', R, R, N if i % 2 else R)]]
    fast = M.bootstrap_metrics(many, n_boot=150, seed=11)
    assert fast['resampling_unit'] == 'profile' and fast['n_profiles'] == 12
    for name, fn in M.SCORED.items():
        slow = M.bootstrap_ci(many, lambda c, f=fn: f(c, 'final'), n_boot=150, seed=11)
        got = fast['metrics'][name]['hybrid']
        for key in ('point', 'lo', 'hi'):
            assert got[key] == pytest.approx(slow[key], nan_ok=True), (name, key)
        paired = M.lift(many, fn, n_boot=150, seed=11)
        assert fast['metrics'][name]['llm_lift']['lo'] == pytest.approx(paired['lo'], nan_ok=True)
        assert fast['metrics'][name]['llm_lift']['point'] == pytest.approx(paired['lift'], nan_ok=True)
