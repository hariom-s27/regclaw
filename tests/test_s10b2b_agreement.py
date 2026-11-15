"""S10b2b-04: agreement statistics on hand-computed tables (exact fractions).

Each value is checked twice: (1) against a fraction worked out by hand (derivation in the comments) and
(2) against a second computation written differently inside the test (closed binary formulas, count
form, or the raw pair list).
"""
from fractions import Fraction as F
import math

import pytest

import _netguard
from eval.s10 import agreement as G


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')


M, NM = G.MANDATORY, G.NOT_MANDATORY

# 2 x 2, rows = rater 1, cols = rater 2, order (mandatory, not): a=20 b=5 c=10 d=15, n=50.
T2 = [[20, 5], [10, 15]]
# 3 x 3 (NA, rec, mand), n=30.
T3 = [[10, 2, 0], [3, 8, 1], [0, 2, 4]]


def pairs_of(t, cats):
    return [(cats[i], cats[j]) for i in range(len(t)) for j in range(len(t)) for _ in range(t[i][j])]


# ---- binary hand table ---------------------------------------------------------------------------------
def test_binary_hand_values():
    # Po = 35/50; rows (1/2, 1/2); cols (3/5, 2/5); Pe = 1/2*3/5 + 1/2*2/5 = 1/2; kappa = (7/10-1/2)/(1/2)
    assert G.po(T2, exact=True) == F(7, 10)
    assert G.cohen_kappa(T2, exact=True) == F(2, 5)
    assert G.pabak(T2, exact=True) == F(2, 5)                        # 2*7/10 - 1
    # pi = (11/20, 9/20); Pe = 2 * 11/20 * 9/20 = 99/200; AC1 = (140-99)/(200-99)
    assert G.gwet_ac1(T2, exact=True) == F(41, 101)
    assert G.prevalence_index(T2, exact=True) == F(1, 10)            # |20-15|/50
    assert G.bias_index(T2, exact=True) == F(1, 10)                  # |5-10|/50
    assert G.bias_index(T2, exact=True, signed=True) == F(-1, 10)
    assert G.prevalence_index(T2, exact=True, signed=True) == F(1, 10)
    # A = 289/6250 + 1587/25000 = 2743/25000; B = 9/25 * 283/1000 = 2547/25000; C = 1/100
    # Var = (2743 + 2547 - 250)/25000 / (50 * 1/4) = 252/15625
    assert G.fce_variance(T2, exact=True) == F(252, 15625)


def test_binary_second_computation():
    a, b, c, d = 20, 5, 10, 15
    n = a + b + c + d
    # closed binary kappa: 2(ad - bc) / ((a+b)(b+d) + (a+c)(c+d))
    assert G.cohen_kappa(T2, exact=True) == F(2 * (a * d - b * c), (a + b) * (b + d) + (a + c) * (c + d))
    # Gwet binary: q = (row1 + col1) / 2n; e = 2 q (1 - q)
    q = F((a + b) + (a + c), 2 * n)
    e = 2 * q * (1 - q)
    assert G.gwet_ac1(T2, exact=True) == (F(a + d, n) - e) / (1 - e)
    # same table rebuilt from the raw pair list
    rebuilt = G.table(pairs_of(T2, G.BINARY), G.BINARY)
    assert rebuilt == T2
    # PABAK from the definition with Pe = 1/2
    assert G.pabak(T2, exact=True) == (F(a + d, n) - F(1, 2)) / (1 - F(1, 2))


def fce_count_form(t):
    """Fleiss-Cohen-Everitt variance written in counts (independent of the module's proportion form)."""
    k, n = len(t), sum(map(sum, t))
    r = [sum(t[i]) for i in range(k)]
    c = [sum(t[i][j] for i in range(k)) for j in range(k)]
    po = F(sum(t[i][i] for i in range(k)), n)
    pe = F(sum(r[i] * c[i] for i in range(k)), n * n)
    kap = (po - pe) / (1 - pe)
    a = sum(F(t[i][i], n) * (1 - F(r[i] + c[i], n) * (1 - kap)) ** 2 for i in range(k))
    b = sum(F(t[i][j], n) * F(c[i] + r[j], n) ** 2 for i in range(k) for j in range(k) if i != j)
    return (a + (1 - kap) ** 2 * b - (kap - pe * (1 - kap)) ** 2) / (n * (1 - pe) ** 2)


def test_fce_second_computation_and_interval():
    assert G.fce_variance(T2, exact=True) == fce_count_form(T2)
    assert G.fce_variance(T3, exact=True) == fce_count_form(T3)
    out = G.fce_interval(T2)
    se = math.sqrt(252 / 15625)
    assert out['label'] == 'assumes independent cells (too narrow here)'
    assert out['se'] == pytest.approx(se)
    assert out['lo'] == pytest.approx(0.4 - 1.959963984540054 * se)
    assert out['hi'] == pytest.approx(0.4 + 1.959963984540054 * se)


# ---- 3-class hand table --------------------------------------------------------------------------------
def test_three_class_hand_values():
    # Po = 22/30; rows (12,12,6)/30; cols (13,12,5)/30; Pe = (156+144+30)/900 = 11/30; kappa = (11/30)/(19/30)
    assert G.po(T3, exact=True) == F(11, 15)
    assert G.cohen_kappa(T3, exact=True) == F(11, 19)
    # pi = (25/60, 24/60, 11/60); sum pi(1-pi) = 2278/3600; Pe = 1139/3600; AC1 = (2640-1139)/(3600-1139)
    assert G.gwet_ac1(T3, exact=True) == F(1501, 2461)


def test_three_class_second_computation():
    n = 30
    r, c = [12, 12, 6], [13, 12, 5]
    s = sum(x * y for x, y in zip(r, c))
    assert G.cohen_kappa(T3, exact=True) == F(n * 22 - s, n * n - s)     # count form of kappa
    cats = ('not_applicable', 'recommended', 'mandatory')
    assert G.table(pairs_of(T3, cats), cats) == T3
    # binarised 3x3 -> 2x2 (mandatory first): a = 4, b = row mand off = 0+2, c = col mand off = 0+1, d = rest
    bin_t = G.table([(G.binarise(x), G.binarise(y)) for x, y in pairs_of(T3, cats)], G.BINARY)
    assert bin_t == [[4, 2], [1, 23]]


def test_float_output_matches_exact():
    assert G.cohen_kappa(T2) == pytest.approx(0.4)
    assert isinstance(G.cohen_kappa(T2), float)


# ---- edge cases ----------------------------------------------------------------------------------------
def test_perfect_agreement():
    t = [[3, 0], [0, 2]]
    assert G.cohen_kappa(t, exact=True) == 1
    assert G.pabak(t, exact=True) == 1
    assert G.gwet_ac1(t, exact=True) == 1
    assert G.fce_variance(t, exact=True) == 0
    assert G.fce_interval(t)['lo'] == pytest.approx(1.0)


def test_one_class_only_kappa_undefined_no_crash():
    t = [[5, 0], [0, 0]]
    assert math.isnan(G.cohen_kappa(t))
    assert math.isnan(G.fce_variance(t))
    assert math.isnan(G.fce_interval(t)['lo'])
    assert G.po(t, exact=True) == 1
    assert G.pabak(t, exact=True) == 1
    assert G.gwet_ac1(t, exact=True) == 1                            # pi = (1, 0): Pe = 0
    assert G.prevalence_index(t, exact=True) == 1


def test_empty_input():
    t = G.table([], G.BINARY)
    assert t == [[0, 0], [0, 0]]
    for fn in (G.po, G.cohen_kappa, G.pabak, G.gwet_ac1, G.prevalence_index, G.bias_index, G.fce_variance):
        assert math.isnan(fn(t))


def test_bad_input_raises():
    with pytest.raises(ValueError):
        G.table([(M, 'maybe')], G.BINARY)
    with pytest.raises(ValueError):
        G.pabak(T3)
    with pytest.raises(ValueError):
        G.cohen_kappa([[1, 2]])


def test_kappa_symmetric_under_transpose():
    tt = [list(r) for r in zip(*T2)]
    assert G.cohen_kappa(tt, exact=True) == G.cohen_kappa(T2, exact=True)
    assert G.bias_index(tt, exact=True, signed=True) == -G.bias_index(T2, exact=True, signed=True)
