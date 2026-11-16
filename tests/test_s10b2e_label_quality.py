"""A2 bounds, weighting and report checks; all data are synthetic test fixtures."""
from copy import deepcopy
from fractions import Fraction
import json
import math
from pathlib import Path

import pytest

from eval.s10 import draws as D
from eval.s10 import label_freeze as LF
from eval.s10 import label_quality as Q
from eval.s10 import second_rater as SR
from eval.s10.metrics import clopper_pearson
from tests import test_s10b2e_second_rater as FK

NA, R, M = 'not_applicable', 'recommended', 'mandatory'
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline_fake_only(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path.resolve().is_relative_to((REPO / 'data/gold/s10').resolve()):
            raise AssertionError('real S10 access forbidden')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guarded)


@pytest.fixture
def root(tmp_path):
    assert tmp_path.resolve().is_relative_to(REPO.resolve())
    return tmp_path / 'fake-quality-root'


@pytest.mark.parametrize('n,want', [(76, 0.0386507667), (30, 0.0950338529), (7, 0.3481636551)])
def test_zero_error_bounds(n, want):
    # P(no errors) = (1-p)^n = .05, hence p = 1 - .05^(1/n).
    assert Q.exact_upper(0, n) == pytest.approx(want, abs=1e-10)
    assert Q.exact_upper(0, n) == pytest.approx(1 - 0.05 ** (1 / n), abs=1e-14)


@pytest.mark.parametrize('x,n', [(1, 5), (2, 10), (7, 12)])
def test_nonzero_bounds_independent_cdf_and_existing_two_sided(x, n):
    p = Q.exact_upper(x, n)
    # Independent direct binomial polynomial, not the production log-space CDF.
    cdf = sum(math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(x + 1))
    assert cdf == pytest.approx(0.05, abs=1e-12)
    # A two-sided alpha=.10 interval puts .05 in its upper tail.
    assert p == pytest.approx(clopper_pearson(x, n, alpha=0.10)[1], abs=1e-12)


def test_binomial_endpoints_alpha_and_monotonicity():
    assert Q.exact_upper(5, 5) == 1
    assert Q.exact_upper(0, 1) == pytest.approx(0.95)  # 1-.05 = .95.
    assert Q.exact_upper(0, 1, alpha=0.2) == pytest.approx(0.8)  # 1-.2 = .8.
    bounds = [Q.exact_upper(x, 10) for x in range(11)]
    assert bounds == sorted(bounds)


@pytest.mark.parametrize('x,n,alpha', [(-1, 5, .05), (6, 5, .05), (0, 0, .05),
                                          (0, 1.5, .05), (True, 5, .05), (0, 5, 0),
                                          (0, 5, 1), (0, 5, float('nan'))])
def test_bad_binomial_arguments(x, n, alpha):
    with pytest.raises(ValueError):
        Q.exact_upper(x, n, alpha)


def test_per_class_orientation_and_zero_class():
    # Rows final, columns friend: NA n=4 x=1; R n=0; M n=5 x=2.
    table = [[3, 1, 0], [0, 0, 0], [1, 1, 3]]
    result = Q.per_class_bounds(table)
    assert result[NA]['n'] == 4 and result[NA]['x'] == 1
    assert result[M]['n'] == 5 and result[M]['x'] == 2
    assert result[R] == dict(n=0, x=0, upper=None, undefined=True)
    for label, n, x in ((NA, 4, 1), (M, 5, 2)):
        upper = result[label]['upper']
        independent = sum(math.comb(n, k) * upper ** k * (1-upper) ** (n-k) for k in range(x+1))
        assert independent == pytest.approx(.05)
        assert result[label]['undefined'] is False
    with pytest.raises(ValueError):
        Q.per_class_bounds([[1, 2]])
    with pytest.raises(ValueError):
        Q.per_class_bounds([[0.5, 0, 0], [0, 0, 0], [0, 0, 0]])


def test_profile_bound_and_sensitivity():
    # One error-bearing profile out of five: solve (1-p)^5 + 5p(1-p)^4 = .05.
    result = Q.profile_level_bound(5, 1)
    p = result['upper']
    assert (1-p)**5 + 5*p*(1-p)**4 == pytest.approx(.05)
    assert result['undefined'] is False
    assert Q.profile_level_bound(0, 0) == dict(n_profiles=0, x_profiles=0, upper=None, undefined=True)
    with pytest.raises(ValueError):
        Q.profile_level_bound(2, 3)
    rows = Q.design_effect_range(30, 3)
    # deff=1+2*rho => [1,1.2,1.6,2,3]; n_eff=30/deff.
    for row, rho, deff in zip(rows, (0, .1, .3, .5, 1), (1, 1.2, 1.6, 2, 3)):
        assert row.rho == rho and row.deff == pytest.approx(deff)
        assert row.n_eff == pytest.approx(30 / deff)
        assert row.bound == pytest.approx(1 - .05 ** (deff / 30))
        assert row.undefined is False and 'sensitivity' in row.label
    assert Q.design_effect_range(0, 1)[0].undefined is True
    assert Q.design_effect_range(0, 1)[0].bound is None
    with pytest.raises(ValueError):
        Q.design_effect_range(5, 0)
    with pytest.raises(ValueError):
        Q.design_effect_range(5, 1, rhos=(1.1,))


def ht_rows():
    return [
        dict(cell_id='fake-a', final=M, friend_label=R, stratum='sampled', inclusion_probability=.25, stratum_population=8),
        dict(cell_id='fake-b', final=M, friend_label=M, stratum='sampled', inclusion_probability=.25, stratum_population=8),
        dict(cell_id='fake-c', final=R, friend_label=NA, stratum='census', inclusion_probability=1, stratum_population=2),
        dict(cell_id='fake-d', final=NA, friend_label=NA, stratum='census', inclusion_probability=1, stratum_population=2),
    ]


def test_ht_by_stratum_binary_and_three_class():
    rows = ht_rows()
    before = deepcopy(rows)
    binary = Q.weighted_disagreement(rows, 'binary')
    three = Q.weighted_disagreement(rows, 'three_class')
    # Population: 4+4+1+1=10. Binary errors weigh 4; 3-class errors weigh 4+1=5.
    assert binary['population'] == three['population'] == 10
    assert binary['rate'] == pytest.approx(Fraction(4, 10))
    assert three['rate'] == pytest.approx(Fraction(5, 10))
    assert binary['estimated_disagreements'] == 4
    assert three['estimated_disagreements'] == 5
    assert binary['by_stratum']['sampled']['agree'] == 1
    assert binary['by_stratum']['sampled']['disagree'] == 1
    assert binary['by_stratum']['census']['agree'] == 2
    assert three['by_stratum']['census']['disagree'] == 1
    for report in (binary, three):
        for value in report['by_stratum'].values():
            assert value['weight_total'] == value['population']
    assert rows == before


@pytest.mark.parametrize('bad', ['zero', 'over-one', 'nan', 'population', 'duplicate', 'label'])
def test_bad_ht_rows(bad):
    rows = ht_rows()
    if bad in ('zero', 'over-one', 'nan'):
        rows[0]['inclusion_probability'] = {'zero': 0, 'over-one': 1.1, 'nan': float('nan')}[bad]
    elif bad == 'population':
        rows[0]['stratum_population'] = 7
    elif bad == 'duplicate':
        rows[1]['cell_id'] = rows[0]['cell_id']
    else:
        rows[0]['final'] = 'invalid'
    with pytest.raises(ValueError):
        Q.weighted_disagreement(rows, 'binary')


def test_empty_weights_and_p21_boundary():
    result = Q.weighted_disagreement([], 'binary')
    assert result['rate'] is None and result['undefined'] is True
    assert Q.p21_check(None)[0] == 2
    assert Q.p21_check(.05)[0] == 0
    assert Q.p21_check(.051)[0] == 6
    assert 'STOP P-21' in Q.p21_check(.051)[1]
    with pytest.raises(ValueError):
        Q.p21_check(float('nan'))
    with pytest.raises(ValueError):
        Q.weighted_disagreement([], 'invalid')


def test_overlap_hand_kappa_and_final_class_groups():
    # Human-pair matrix: [[1,1,0],[0,1,0],[0,0,1]], n=4.
    # Po=3/4; Pe=(2*1+1*2+1*1)/16=5/16; kappa=(12-5)/(16-5)=7/11.
    friend = {'a': dict(label=NA, final=M), 'b': dict(label=NA, final=M),
              'c': dict(label=R, final=NA), 'd': dict(label=M, final=R),
              'friend-only': dict(label=M, final=M)}
    hariom = {'a': NA, 'b': R, 'c': R, 'd': M, 'human-only': M}
    result = Q.overlap_agreement(friend, hariom)
    assert result['raw'] == dict(n=4, agree=3, disagree=1, rate=.75, undefined=False)
    assert result['kappa'] == pytest.approx(Fraction(7, 11)) and result['degenerate'] is False
    assert result['per_class'][M] == dict(n=2, agree=1, disagree=1, rate=.5, undefined=False)
    assert result['per_class'][NA]['n'] == result['per_class'][R]['n'] == 1
    # The final-M group contains two human NA labels: grouping must not use a human label.
    assert result['per_class'][M]['n'] == 2


def test_overlap_empty_and_degenerate():
    empty = Q.overlap_agreement({}, {})
    assert empty['raw']['rate'] is None and empty['raw']['undefined'] is True
    assert empty['kappa'] is None and empty['degenerate'] is True
    constant = Q.overlap_agreement({'a': dict(label=M, final=NA)}, {'a': M})
    assert constant['raw']['rate'] == 1
    assert constant['kappa'] is None and constant['degenerate'] is True


def test_tipping_and_error_count_helpers():
    # tip(6,56,3): (6-3)/(56-3)=3/53; (6+3)/(56+3)=9/59.
    result = Q.tip(6, 56, 3)
    assert result == pytest.approx((3 / 53, 9 / 59)) and result.undefined is False
    assert Q.tip(2, 10, 3) == pytest.approx((0, 5 / 13))  # numerator clipped to zero.
    edge = Q.tip(6, 56, 56)
    assert edge.lower is None and edge.lower_undefined and edge.undefined
    assert edge.upper == pytest.approx(62 / 112) and not edge.upper_undefined
    assert Q.tip(6, 56, 57).lower is None  # P-E=-1 also flags the lower endpoint.
    assert Q.tip(0, 0, 0) == (None, None)
    assert Q.tip(0, 0, 0).upper_undefined
    assert Q.e1(.1, 56) == 6  # ceil(5.6)=6.
    assert Q.e2(0, 30, 56) == 6  # ceil((1-.05^(1/30))*56) = ceil(5.3219...) = 6.
    with pytest.raises(ValueError):
        Q.tip(6, 5, 1)


def merge_row(name, final, labels=None):
    return dict(cell_id=f'FAKE-PRIVATE|{name}', j_id='FAKE-PRIVATE', item=name, final=final,
                labels=dict(zip('ABC', labels or (final,) * 3)), confidences=dict.fromkeys('ABC', 'high'),
                routed=False, route_reasons=[])


def test_two_way_extremes_including_three_way_without_mutation():
    rows = [merge_row('a', M), merge_row('b', R, (R, NA, R)), merge_row('c', M, (M, NA, R))]
    before = deepcopy(rows)
    assert Q.two_way(rows) == [('FAKE-PRIVATE|b', NA, R), ('FAKE-PRIVATE|c', NA, M)]
    assert rows == before


@pytest.mark.parametrize('friend_a,human_a', [(M, NA), (R, M), (R, NA)])
def test_e2_pool_distinct_union_any_human_and_mandatory_filter(friend_a, human_a):
    rows = [merge_row(name, M if name != 'e' else NA) for name in 'abcde']
    merged = {row['cell_id']: row for row in rows}
    friend = {'FAKE-PRIVATE|a': dict(label=friend_a, final=M), 'FAKE-PRIVATE|b': dict(label=R, final=M),
              'FAKE-PRIVATE|e': dict(label=M, final=NA)}
    human = {'FAKE-PRIVATE|a': human_a, 'FAKE-PRIVATE|c': M, 'FAKE-PRIVATE|e': M}
    # Union mandatory cells a,b,c => n=3; errors a,b => x=2. a is counted once;
    # unseen mandatory d and human-labelled nonmandatory e are excluded.
    assert Q.e2_pool(merged, friend, human) == dict(x=2, n=3)


def test_report_reference_is_final_even_when_judges_unanimously_differ():
    row = merge_row('reference', M, (R, R, R))
    cid = row['cell_id']
    draw = dict(cells=[dict(cell_id=cid, stratum='audited_mandatory', inclusion_probability=1)],
                strata={'audited_mandatory': dict(population=1)})
    result = Q.build_report(draw, {cid: dict(label=M, final=M)}, [row], {cid: M})
    # One final-M cell, both humans agree: zero errors, regardless of the judge labels.
    assert result['weighted']['binary']['rate'] == 0
    assert result['per_class'][M]['n'] == 1 and result['per_class'][M]['x'] == 0
    assert result['per_class'][R]['n'] == 0
    assert result['e2_pool'] == dict(x=0, n=1)


def prepare_report(root, *, errors=False, all_wrong=False):
    merged, audit = FK.setup_friend(root)
    assert SR.main(['draw'], root=root, out=lambda _: None) == 0
    assert SR.main(['sheet'], root=root, out=lambda _: None) == 0
    draw = json.loads((root / SR.DRAW_REL).read_bytes())
    by_id = {row['cell_id']: row for row in merged}
    drawn = {cell['cell_id'] for cell in draw['cells']}
    human_overrides = {}
    friend_overrides = {}
    if errors:
        overlap = next(cell['cell_id'] for cell in draw['cells'] if cell['stratum'] == 'audited_mandatory')
        friend_only = next(cell['cell_id'] for cell in draw['cells'] if cell['stratum'] == 'unaudited_mandatory')
        human_only = next(row['cell_id'] for row in merged[:60] if row['cell_id'] not in drawn)
        friend_overrides = {overlap: R, friend_only: NA}
        human_overrides = {overlap: NA, human_only: R}
    friend_rows = FK.read_csv(root / SR.LABELS_REL)
    for cell, row in zip(draw['cells'], friend_rows):
        label = by_id[cell['cell_id']]['final']
        row['label'] = (NA if label == M else M) if all_wrong else friend_overrides.get(cell['cell_id'], label)
    FK.put(root, SR.LABELS_REL, FK.csv_text(FK.FIELDS, friend_rows))
    draw_sha = FK.sha((root / SR.DRAW_REL).read_bytes())
    assert SR.main(['freeze', '--expected-draw-sha', draw_sha], root=root, now=FK.NOW, out=lambda _: None) == 0
    human_rows = [dict(order=row['order'], j_id=row['j_id'], item=row['item'],
                       label=human_overrides.get(f'{row["j_id"]}|{row["item"]}', by_id[f'{row["j_id"]}|{row["item"]}']['final']),
                       confidence='high', reason='Synthetic human reason', timestamp=FK.NOW) for row in audit]
    FK.put(root, D.HUMAN_REL, FK.csv_text(LF.FIELDS, human_rows))
    FK.put(root, LF.CODE_LOCK_REL, '{"format":"synthetic-lock"}\n')
    human_sha = FK.sha((root / D.HUMAN_REL).read_bytes())
    assert LF.main(['--labeller', 'hariom', '--expected-sha', human_sha], root=root, now=FK.NOW,
                   out=lambda _: None, err=lambda _: None) == 0
    return draw


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def report_cli(root):
    out, err = [], []
    code = Q.main(['report'], root=root, out=out.append, err=err.append)
    for hidden in (FK.JID, FK.LEAK, 'FAKE-ITEM-', 'Synthetic device', 'S-001', 'cell_id'):
        assert hidden not in '\n'.join(out + err)
    return code, out, err


def test_report_e2_pool_overlap_and_no_writes(root):
    prepare_report(root, errors=True)
    before = snapshot(root)
    code, out, err = report_cli(root)
    assert code == 0 and err == [] and snapshot(root) == before
    # Mandatory pool = all 60 audited M + 50 distinct unaudited friend M = 110.
    # One overlapping error + one human-only error + one friend-only error = 3.
    assert 'E2_pool x=3 n=110' in out
    assert any(line.startswith('overlap_group=raw n=50 ') for line in out)  # 25+20+5 audited selections.
    assert any(line.startswith('friend_class=mandatory n=75 x=2 ') for line in out)  # 25+50 M, two friend errors.
    # Friend binary HT errors: (60/25 + 60/50)/270 = 3.6/270 = 1/75.
    rate_line = next(line for line in out if line.startswith('weighted scheme=binary '))
    rate = float(rate_line.split('rate=')[1].split()[0])
    assert rate == pytest.approx(Fraction(1, 75))
    assert out[-1].startswith('P-21 PASS:')
    assert all(line.startswith(('stratum=', 'weighted ', 'friend_class=', 'overlap_group=',
                                'overlap kappa=', 'E2_pool ', 'P-21 ')) for line in out)


def test_report_p21_stop_keeps_aggregates_and_writes_nothing(root):
    prepare_report(root, all_wrong=True)
    before = snapshot(root)
    code, out, err = report_cli(root)
    assert code == 6 and err == [] and snapshot(root) == before
    assert 'weighted scheme=binary rate=1 undefined=False' in out
    assert out[-1].startswith('STOP P-21:')


def test_report_null_final_exact_refusal_before_other_inputs(root):
    merged, _ = FK.setup_friend(root)
    merged[0].update(final=None, routed=True)
    path = root / D.MERGE_REL
    doc = json.loads(path.read_bytes())
    doc['rows'] = merged
    path.write_bytes(json.dumps(doc).encode('utf-8'))
    before = snapshot(root)
    code, out, err = report_cli(root)
    assert code == 2 and out == [] and snapshot(root) == before
    assert err == ["REFUSED: 1 routed cells without a final label; the friend draw needs every cell's final label"]


@pytest.mark.parametrize('rel', [SR.LABELS_REL, SR.DRAW_REL, SR.SHEET_REL, D.HUMAN_REL, D.LIST_REL])
def test_report_sha_mismatch_refuses_no_partial_aggregates(root, rel):
    prepare_report(root)
    path = root / rel
    path.write_bytes(path.read_bytes() + b'\n')
    before = snapshot(root)
    code, out, err = report_cli(root)
    assert code == 2 and out == [] and err and snapshot(root) == before


@pytest.mark.parametrize('kind', ['friend-path', 'human-path', 'friend-count', 'human-count', 'incomplete-labels'])
def test_report_validates_frozen_contracts(root, kind):
    prepare_report(root)
    rel = Q.HUMAN_MANIFEST_REL if kind.startswith('human') else SR.MANIFEST_REL
    path = root / rel
    doc = json.loads(path.read_bytes())
    if kind.endswith('path'):
        doc['file'] = '../forbidden.csv'  # Must be rejected before following any manifest-selected path.
    elif kind == 'friend-count':
        doc['valid_rows'] = 149  # Draw has 150 rows, so this frozen count is inconsistent.
    elif kind == 'human-count':
        doc['data_rows'] = 149  # Frozen prefix contract requires 150.
    else:
        labels_path = root / SR.LABELS_REL
        rows = FK.read_csv(labels_path)
        rows[0]['label'] = ''
        labels_path.write_bytes(FK.csv_text(FK.FIELDS, rows).encode('utf-8'))
        doc['sha256_lf'] = FK.sha(labels_path.read_bytes())
    path.write_bytes(json.dumps(doc).encode('utf-8'))
    before = snapshot(root)
    code, out, err = report_cli(root)
    assert code == 2 and out == [] and err and snapshot(root) == before
