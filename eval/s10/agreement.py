"""S10b2b (S10b2b-04): two-rater agreement statistics in own code (stdlib only; no irrCAC/crowd-kit/sklearn).

Every function takes a k x k COUNT TABLE t (rows = rater 1, columns = rater 2, same category order) and
computes with fractions.Fraction; it returns a float, or the exact Fraction with exact=True. Undefined
values (empty table; chance agreement Pe = 1, i.e. both raters use one and the same class only) are
float('nan') and never raise.

  po                observed agreement  Po = sum t_ii / n
  cohen_kappa       (Po - Pe) / (1 - Pe), Pe = sum p_i+ p_+i (binary or 3-class, unweighted)
  pabak             2 Po - 1 (binary)
  gwet_ac1          (Po - Pe) / (1 - Pe), Pe = sum pi_k (1 - pi_k) / (q - 1), pi_k = (p_k+ + p_+k) / 2
  prevalence_index  |a - d| / n (binary; signed=True keeps the sign)       Byrt, Bishop & Carlin 1993
  bias_index        |b - c| / n (binary; signed=True keeps the sign)
  fce_variance      Fleiss, Cohen & Everitt (1969) large-sample variance of kappa (non-null)
  fce_interval      kappa +- z * SE, labelled FCE_LABEL: it assumes independent cells, too narrow here

Binary tables use BINARY order (mandatory first), so a = both mandatory, d = both not mandatory,
b = rater 1 mandatory and rater 2 not, c = the reverse.
"""
from fractions import Fraction
import math
from statistics import NormalDist

NAN = float('nan')
MANDATORY = 'mandatory'
NOT_MANDATORY = 'not_mandatory'
BINARY = (MANDATORY, NOT_MANDATORY)
FCE_LABEL = 'assumes independent cells (too narrow here)'


def binarise(label):
    """mandatory vs not (any other label, including recommended and not_applicable)."""
    return MANDATORY if label == MANDATORY else NOT_MANDATORY


def table(pairs, categories):
    """Count table from (rater-1 label, rater-2 label) pairs; ValueError on a label outside categories."""
    idx = {c: i for i, c in enumerate(categories)}
    t = [[0] * len(categories) for _ in categories]
    for a, b in pairs:
        if a not in idx or b not in idx:
            raise ValueError('label outside the category list')
        t[idx[a]][idx[b]] += 1
    return t


def _n(t):
    return sum(map(sum, t))


def _out(x, exact):
    return x if exact else float(x)


def _square(t):
    k = len(t)
    if k < 2 or any(len(row) != k for row in t):
        raise ValueError('need a square table with at least 2 categories')
    return k


def _binary(t):
    if _square(t) != 2:
        raise ValueError('binary statistic needs a 2 x 2 table')


def _po(t):
    return Fraction(sum(t[i][i] for i in range(len(t))), _n(t))


def _margins(t):
    n, k = _n(t), len(t)
    return ([Fraction(sum(t[i]), n) for i in range(k)], [Fraction(sum(t[i][j] for i in range(k)), n) for j in range(k)])


def po(t, exact=False):
    _square(t)
    return _out(_po(t), exact) if _n(t) else NAN


def _kappa_parts(t):
    """(kappa, Pe) as Fractions, or None when undefined."""
    if not _n(t):
        return None
    rows, cols = _margins(t)
    pe = sum(r * c for r, c in zip(rows, cols))
    if pe == 1:
        return None
    return (_po(t) - pe) / (1 - pe), pe


def cohen_kappa(t, exact=False):
    _square(t)
    parts = _kappa_parts(t)
    return NAN if parts is None else _out(parts[0], exact)


def pabak(t, exact=False):
    _binary(t)
    return _out(2 * _po(t) - 1, exact) if _n(t) else NAN


def gwet_ac1(t, exact=False):
    q = _square(t)
    if not _n(t):
        return NAN
    rows, cols = _margins(t)
    pi = [(r + c) / 2 for r, c in zip(rows, cols)]
    pe = sum(p * (1 - p) for p in pi) / (q - 1)
    if pe == 1:
        return NAN
    return _out((_po(t) - pe) / (1 - pe), exact)


def prevalence_index(t, exact=False, signed=False):
    _binary(t)
    n = _n(t)
    if not n:
        return NAN
    v = Fraction(t[0][0] - t[1][1], n)
    return _out(v if signed else abs(v), exact)


def bias_index(t, exact=False, signed=False):
    _binary(t)
    n = _n(t)
    if not n:
        return NAN
    v = Fraction(t[0][1] - t[1][0], n)
    return _out(v if signed else abs(v), exact)


def fce_variance(t, exact=False):
    """Var(kappa) = [A + B - C] / (n (1 - Pe)^2) with
    A = sum_i p_ii (1 - (p_i+ + p_+i)(1 - k))^2, B = (1 - k)^2 sum_{i != j} p_ij (p_+i + p_j+)^2,
    C = (k - Pe (1 - k))^2."""
    k_ = _square(t)
    parts = _kappa_parts(t)
    if parts is None:
        return NAN
    kappa, pe = parts
    n = _n(t)
    rows, cols = _margins(t)
    p = [[Fraction(t[i][j], n) for j in range(k_)] for i in range(k_)]
    a = sum(p[i][i] * (1 - (rows[i] + cols[i]) * (1 - kappa)) ** 2 for i in range(k_))
    b = (1 - kappa) ** 2 * sum(p[i][j] * (cols[i] + rows[j]) ** 2
                               for i in range(k_) for j in range(k_) if i != j)
    c = (kappa - pe * (1 - kappa)) ** 2
    return _out((a + b - c) / (n * (1 - pe) ** 2), exact)


def fce_interval(t, alpha=0.05):
    """Analytic interval kappa +- z * sqrt(Var_FCE); label FCE_LABEL (independent cells assumed)."""
    kappa, var = cohen_kappa(t), fce_variance(t)
    z = NormalDist().inv_cdf(1 - alpha / 2)
    if math.isnan(kappa) or math.isnan(var):
        return dict(point=kappa, se=NAN, lo=NAN, hi=NAN, alpha=alpha, label=FCE_LABEL)
    se = math.sqrt(max(var, 0.0))
    return dict(point=kappa, se=se, lo=kappa - z * se, hi=kappa + z * se, alpha=alpha, label=FCE_LABEL)
