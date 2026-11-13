"""S10-18: exact Clopper-Pearson interval (standard library only) and profiles with >= 1 under-call."""
import math

import pytest

from eval.s10 import metrics as M


def cell(pid, gold, final, item='ISO 10993-5', repeat=1):
    return dict(profile_id=pid, item=item, gold=gold, final=final, rule=final, llm=final, setting='s', repeat=repeat)


def test_known_values_to_four_decimals():
    lo, hi = M.clopper_pearson(0, 16)
    assert lo == 0.0 and round(hi, 4) == 0.2059
    lo, hi = M.clopper_pearson(1, 16)
    assert (round(lo, 4), round(hi, 4)) == (0.0016, 0.3023)
    lo, hi = M.clopper_pearson(0, 8)
    assert lo == 0.0 and round(hi, 4) == 0.3694


def test_edges_and_symmetry():
    assert M.clopper_pearson(16, 16)[1] == 1.0
    assert round(M.clopper_pearson(16, 16)[0], 4) == round(1 - 0.2059, 4)
    lo, hi = M.clopper_pearson(3, 10)
    lo2, hi2 = M.clopper_pearson(7, 10)
    assert (lo, hi) == pytest.approx((1 - hi2, 1 - lo2), abs=1e-9)
    assert 0 < lo < 0.3 < hi < 1
    for bad in ((1, 0), (-1, 5), (6, 5)):
        with pytest.raises(ValueError):
            M.clopper_pearson(*bad)


def test_profiles_with_under_call_counts_profiles_not_cells():
    cells = [cell('A', 'mandatory', 'recommended'), cell('A', 'mandatory', 'not_applicable', 'ISO 10993-4'),
             cell('A', 'mandatory', 'recommended', repeat=2),                 # same profile again: still one
             cell('B', 'mandatory', 'mandatory'), cell('B', 'recommended', 'not_applicable', 'ISO 10993-3'),
             cell('C', 'not_applicable', 'not_applicable')]
    out = M.profiles_with_under_call(cells)
    assert (out['count'], out['n']) == (1, 3) and out['rate'] == pytest.approx(1 / 3)
    assert (out['lo'], out['hi']) == pytest.approx(M.clopper_pearson(1, 3))
    assert out['method'] == 'exact Clopper-Pearson interval' and out['unit'] == 'profile'
    rules = M.profiles_with_under_call([dict(c, rule='not_applicable') for c in cells], pred='rule')
    assert rules['count'] == 2                                                # A and B have gold-mandatory cells


def test_no_profiles_gives_undefined_values():
    out = M.profiles_with_under_call([])
    assert (out['count'], out['n']) == (0, 0) and all(math.isnan(out[k]) for k in ('rate', 'lo', 'hi'))


def test_bootstrap_interval_is_labelled_approximate():
    cells = [cell('A', 'mandatory', 'mandatory'), cell('B', 'mandatory', 'recommended')]
    assert M.bootstrap_metrics(cells, n_boot=20, seed=1)['ci_label'] == 'approximate (few profiles)'
