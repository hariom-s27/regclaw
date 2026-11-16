"""S10 A2 label-quality statistics and a read-only aggregate report.

Later run: python -m eval.s10.label_quality report
Reference labels always come from merge.final. No per-cell output or files are
written. Exit codes: 0 reported, 2 invalid input, 6 P-21 STOP.
"""
import argparse
from collections import defaultdict
import csv
import io
import json
import math
from pathlib import Path
import sys
from typing import NamedTuple

from standards.ruleset import normalised_sha

from . import agreement as G
from . import draws as D
from . import label_freeze as LF
from . import second_rater as SR
from .merge import JUDGES, LABELS, cell_id

ROOT = Path(__file__).resolve().parents[2]
HUMAN_MANIFEST_REL = 'eval/s10/manifests/auditA_labels_hariom_150.json'
SCHEMES = ('binary', 'three_class')


class QualityError(ValueError):
    """Fixed check names only; never embed input values."""


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise QualityError('integer check failed')


def _finite(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise QualityError('finite number check failed')


def _label(value):
    if value not in LABELS:
        raise QualityError('label check failed')


def _binomial_cdf(x, n, p):
    if p == 0:
        return 1.0
    if p == 1:
        return float(x == n)
    logs = [math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
            + k * math.log(p) + (n - k) * math.log1p(-p) for k in range(x + 1)]
    largest = max(logs)
    return min(1.0, math.exp(largest) * math.fsum(math.exp(v - largest) for v in logs))


def exact_upper(x, n, alpha=0.05):
    """One-sided exact binomial upper bound; x counts errors, n is integer."""
    _integer(n, 1)
    _integer(x)
    _finite(alpha)
    if x > n or not 0 < alpha < 1:
        raise QualityError('binomial arguments check failed')
    if x == 0:
        return -math.expm1(math.log(alpha) / n)
    if x == n:
        return 1.0
    low, high = 0.0, 1.0
    for _ in range(200):
        middle = (low + high) / 2
        if middle == low or middle == high:
            break
        # P(X <= x) decreases as p increases.
        if _binomial_cdf(x, n, middle) > alpha:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def per_class_bounds(table):
    """Unweighted 3x3 integer table: rows final, columns friend, LABELS order.

    These binomial bounds are separate from the HT estimates; weighted totals
    are never treated as binomial observations.
    """
    if len(table) != len(LABELS) or any(len(row) != len(LABELS) for row in table):
        raise QualityError('class table shape check failed')
    for row in table:
        for count in row:
            _integer(count)
    result = {}
    for i, label in enumerate(LABELS):
        n = sum(table[i])
        x = n - table[i][i]
        result[label] = dict(n=n, x=x, upper=exact_upper(x, n) if n else None, undefined=not bool(n))
    return result


def profile_level_bound(n_profiles, x_profiles):
    """Upper bound for profiles with any binary disagreement, plus undefined flag."""
    _integer(n_profiles)
    _integer(x_profiles)
    if x_profiles > n_profiles:
        raise QualityError('profile counts check failed')
    return dict(n_profiles=n_profiles, x_profiles=x_profiles,
                upper=exact_upper(x_profiles, n_profiles) if n_profiles else None,
                undefined=not bool(n_profiles))


class DesignEffectRow(NamedTuple):
    rho: float
    deff: float
    n_eff: float
    bound: float | None

    @property
    def undefined(self):
        return self.bound is None

    @property
    def label(self):
        return 'sensitivity: zero-error formula at n_eff'


def design_effect_range(n, m, rhos=(0, 0.1, 0.3, 0.5, 1.0)):
    """m = assumed mean cluster size; fractional n_eff is sensitivity only."""
    _integer(n)
    _finite(m)
    if m < 1:
        raise QualityError('cluster size check failed')
    result = []
    for rho in rhos:
        _finite(rho)
        if not 0 <= rho <= 1:
            raise QualityError('rho check failed')
        deff = 1 + (m - 1) * rho
        n_eff = n / deff
        bound = -math.expm1(math.log(0.05) / n_eff) if n_eff else None
        result.append(DesignEffectRow(rho, deff, n_eff, bound))
    return result


def _disagrees(a, b, scheme):
    return (a == 'mandatory') != (b == 'mandatory') if scheme == 'binary' else a != b


def weighted_disagreement(rows, scheme):
    """HT disagreements / known population, with unweighted stratum counts.

    Each row has cell_id, final, friend_label, stratum, inclusion_probability,
    stratum_population. Each sampled cell must occur once; weights must recover
    its stratum population. No inferred replacement probabilities are used.
    """
    if scheme not in SCHEMES:
        raise QualityError('scheme check failed')
    groups = defaultdict(list)
    seen = set()
    for row in rows:
        _label(row['final'])
        _label(row['friend_label'])
        probability = row['inclusion_probability']
        population = row['stratum_population']
        _finite(probability)
        _integer(population, 1)
        if not 0 < probability <= 1 or row['cell_id'] in seen or not isinstance(row['stratum'], str):
            raise QualityError('weight or identifier check failed')
        seen.add(row['cell_id'])
        groups[row['stratum']].append(row)
    strata = {}
    for name, group in sorted(groups.items()):
        populations = {row['stratum_population'] for row in group}
        if len(populations) != 1:
            raise QualityError('stratum population check failed')
        population = populations.pop()
        weights = [1 / row['inclusion_probability'] for row in group]
        weight_total = math.fsum(weights)
        if not math.isclose(weight_total, population, rel_tol=1e-10, abs_tol=1e-10):
            raise QualityError('stratum weight total check failed')
        mismatch = [_disagrees(row['final'], row['friend_label'], scheme) for row in group]
        estimated = math.fsum(w for w, differs in zip(weights, mismatch) if differs)
        strata[name] = dict(n=len(group), agree=len(group) - sum(mismatch), disagree=sum(mismatch),
                            population=population, weight_total=weight_total,
                            estimated_disagreements=estimated)
    population = sum(value['population'] for value in strata.values())
    estimated = math.fsum(value['estimated_disagreements'] for value in strata.values())
    # Guard only roundoff at the endpoints after checking the population weights.
    rate = min(1.0, max(0.0, estimated / population)) if population else None
    return dict(scheme=scheme, population=population, estimated_disagreements=estimated,
                rate=rate, undefined=not bool(population), by_stratum=strata)


def _raw_agreement(pairs):
    n = len(pairs)
    agree = sum(a == b for a, b in pairs)
    return dict(n=n, agree=agree, disagree=n - agree, rate=agree / n if n else None, undefined=not bool(n))


def overlap_agreement(friend, hariom):
    """friend = {cell_id: {label, final}}; hariom = {cell_id: label}.

    Raw agreement and kappa compare the human labels directly. Per-class groups
    use merge.final, never a human label as the reference class.
    """
    for row in friend.values():
        _label(row['label'])
        _label(row['final'])
    for label in hariom.values():
        _label(label)
    overlap = sorted(set(friend) & set(hariom))
    pairs = [(friend[cid]['label'], hariom[cid]) for cid in overlap]
    per_class = {label: _raw_agreement([(friend[cid]['label'], hariom[cid]) for cid in overlap
                                       if friend[cid]['final'] == label]) for label in LABELS}
    kappa = G.cohen_kappa(G.table(pairs, LABELS))
    return dict(raw=_raw_agreement(pairs), per_class=per_class,
                kappa=None if math.isnan(kappa) else kappa, degenerate=math.isnan(kappa))


def p21_check(weighted_binary_rate, threshold=0.05):
    _finite(threshold)
    if not 0 <= threshold <= 1:
        raise QualityError('threshold check failed')
    if weighted_binary_rate is None:
        return 2, 'ERROR: P-21 weighted binary rate undefined'
    _finite(weighted_binary_rate)
    if not 0 <= weighted_binary_rate <= 1:
        raise QualityError('rate check failed')
    if weighted_binary_rate > threshold:
        return 6, f'STOP P-21: weighted_binary_rate={weighted_binary_rate:.12g} threshold={threshold:.12g}'
    return 0, f'P-21 PASS: weighted_binary_rate={weighted_binary_rate:.12g} threshold={threshold:.12g}'


class TipResult(NamedTuple):
    lower: float | None
    upper: float | None

    @property
    def lower_undefined(self):
        return self.lower is None

    @property
    def upper_undefined(self):
        return self.upper is None

    @property
    def undefined(self):
        return self.lower_undefined or self.upper_undefined


def tip(U, P, E):
    for value in (U, P, E):
        _integer(value)
    if U > P:
        raise QualityError('tipping counts check failed')
    # A2: flag the lower endpoint undefined whenever P-E <= 0.
    lower = max(U - E, 0) / (P - min(E, U)) if P - E > 0 else None
    upper = (U + E) / (P + E) if P + E else None
    return TipResult(lower, upper)


def e1(weighted_rate_on_gold_mandatory, P):
    _integer(P)
    _finite(weighted_rate_on_gold_mandatory)
    if not 0 <= weighted_rate_on_gold_mandatory <= 1:
        raise QualityError('rate check failed')
    return math.ceil(weighted_rate_on_gold_mandatory * P)


def e2(x, n, P):
    _integer(P)
    return math.ceil(exact_upper(x, n) * P)


def checked_merge_rows(rows):
    rows = list(rows)
    SR.require_final_labels(rows)
    seen = set()
    for row in rows:
        _label(row['final'])
        cid = row['cell_id']
        if (not isinstance(cid, str) or cid != cell_id(row['j_id'], row['item'])
                or cid in seen or any(c in cid for c in '\r\n')
                or set(row['labels']) != set(JUDGES) or set(row['confidences']) != set(JUDGES)):
            raise QualityError('merge structure check failed')
        for judge in JUDGES:
            _label(row['labels'][judge])
            if row['confidences'][judge] not in ('low', 'medium', 'high'):
                raise QualityError('confidence check failed')
        seen.add(cid)
    return rows


def two_way(rows):
    """Lower/higher judge labels for split cells only; never alters gold."""
    rank = {label: i for i, label in enumerate(LABELS)}
    result = []
    for row in checked_merge_rows(rows):
        labels = sorted(set(row['labels'].values()), key=rank.get)
        if len(labels) > 1:
            result.append((row['cell_id'], labels[0], labels[-1]))
    return result


def e2_pool(merged, friend, hariom):
    """Distinct final-mandatory cells seen by either human; any error counts once."""
    n = x = 0
    for cid in set(friend) | set(hariom):
        if merged[cid]['final'] != 'mandatory':
            continue
        n += 1
        labels = ([friend[cid]['label']] if cid in friend else []) + ([hariom[cid]] if cid in hariom else [])
        x += any(label != 'mandatory' for label in labels)
    return dict(x=x, n=n)


def e1_input(rows):
    """Mandatory-frame HT binary rate over the two approved mandatory strata."""
    mandatory = [row for row in rows if row['final'] == 'mandatory'
                 and row['stratum'] in ('audited_mandatory', 'unaudited_mandatory')]
    weighted = weighted_disagreement(mandatory, 'binary')
    return dict(weighted_binary_rate_on_final_mandatory=weighted['rate'],
                population=weighted['population'], undefined=weighted['undefined'])


def build_report(draw, friend, merged_rows, hariom):
    """Pure aggregate construction; friend and hariom are validated label maps."""
    merged = {row['cell_id']: row for row in checked_merge_rows(merged_rows)}
    wanted = {cell['cell_id'] for cell in draw['cells']}
    if set(friend) != wanted or not (wanted | set(hariom)) <= set(merged):
        raise QualityError('report membership check failed')
    rows = []
    for cell in draw['cells']:
        cid = cell['cell_id']
        if friend[cid]['final'] != merged[cid]['final']:
            raise QualityError('reference identity check failed')
        rows.append(dict(cell_id=cid, final=merged[cid]['final'], friend_label=friend[cid]['label'],
                         stratum=cell['stratum'], inclusion_probability=cell['inclusion_probability'],
                         stratum_population=draw['strata'][cell['stratum']]['population']))
    weighted = {scheme: weighted_disagreement(rows, scheme) for scheme in SCHEMES}
    table = G.table([(row['final'], row['friend_label']) for row in rows], LABELS)
    code, message = p21_check(weighted['binary']['rate'])
    return dict(weighted=weighted, e1_input=e1_input(rows), per_class=per_class_bounds(table),
                overlap=overlap_agreement(friend, hariom), e2_pool=e2_pool(merged, friend, hariom),
                p21=dict(exit_code=code, message=message))


def _match_sha(raw, expected):
    if not isinstance(expected, str) or normalised_sha(raw) != expected:
        raise QualityError('frozen sha check failed')


def load_report_inputs(root):
    """No manifest-selected arbitrary paths: only the fixed approved label files."""
    root = Path(root)
    merged = checked_merge_rows(D.load_merge(root))
    audit = D.load_audit_rows(root)
    draw_raw = (root / SR.DRAW_REL).read_bytes()
    draw = SR.checked_draw(draw_raw)
    if draw != SR.friend_draw(merged, audit):
        raise QualityError('draw does not match merge and audit list')
    friend_manifest = json.loads((root / SR.MANIFEST_REL).read_bytes())
    if (friend_manifest['format'] != 's10-friend-labels-manifest.v1'
            or friend_manifest['file'] != SR.LABELS_REL):
        raise QualityError('friend manifest check failed')
    _match_sha(draw_raw, friend_manifest['friend_set_sha256_lf'])
    _match_sha((root / SR.SHEET_REL).read_bytes(), friend_manifest['sheet_sha256_lf'])
    raw = (root / SR.LABELS_REL).read_bytes()
    _match_sha(raw, friend_manifest['sha256_lf'])
    labels = SR.checked_friend_rows(raw, draw['cells'])
    if friend_manifest['rows'] != len(labels) or friend_manifest['valid_rows'] != len(labels):
        raise QualityError('friend row count check failed')
    by_id = {row['cell_id']: row for row in merged}
    friend = {cell['cell_id']: dict(label=row['label'], final=by_id[cell['cell_id']]['final'])
              for cell, row in zip(draw['cells'], labels)}
    manifest = json.loads((root / HUMAN_MANIFEST_REL).read_bytes())
    if (manifest['format'] != LF.FORMAT or manifest['labeller'] != 'hariom'
            or manifest['file'] != D.HUMAN_REL or manifest['upto'] != LF.UPTO
            or manifest['data_rows'] != LF.UPTO):
        raise QualityError('human manifest check failed')
    raw = (root / D.HUMAN_REL).read_bytes()
    _match_sha(raw, manifest['sha256_lf'])
    if manifest['audit_list_sha256_lf'] != D.build_sha(root, D.LIST_REL):
        raise QualityError('human audit identity check failed')
    LF.checked_rows(raw, audit[:LF.UPTO])
    human_rows = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''))
    hariom = {cell_id(row['j_id'], row['item']): row['label'] for row in human_rows}
    return draw, friend, merged, hariom


def _number(value):
    return 'None' if value is None else f'{value:.12g}'


def report_lines(report, strata):
    """Only approved aggregate fields cross the console boundary."""
    lines = []
    for name in sorted(strata):
        for scheme in SCHEMES:
            counts = report['weighted'][scheme]['by_stratum'].get(name, dict(n=0, agree=0, disagree=0))
            lines.append(f'stratum={name} scheme={scheme} n={counts["n"]} '
                         f'agree={counts["agree"]} disagree={counts["disagree"]}')
    for scheme in SCHEMES:
        value = report['weighted'][scheme]
        lines.append(f'weighted scheme={scheme} rate={_number(value["rate"])} undefined={value["undefined"]}')
    value = report['e1_input']
    lines.append('E1_input weighted_binary_rate_on_final_mandatory='
                 f'{_number(value["weighted_binary_rate_on_final_mandatory"])} '
                 f'population={value["population"]} undefined={value["undefined"]}')
    for label in LABELS:
        value = report['per_class'][label]
        lines.append(f'friend_class={label} n={value["n"]} x={value["x"]} '
                     f'upper={_number(value["upper"])} undefined={value["undefined"]}')
    overlap = report['overlap']
    for name, value in [('raw', overlap['raw'])] + [(label, overlap['per_class'][label]) for label in LABELS]:
        lines.append(f'overlap_group={name} n={value["n"]} agree={value["agree"]} '
                     f'disagree={value["disagree"]} rate={_number(value["rate"])} undefined={value["undefined"]}')
    lines.append(f'overlap kappa={_number(overlap["kappa"])} degenerate={overlap["degenerate"]}')
    pool = report['e2_pool']
    lines.append(f'E2_pool x={pool["x"]} n={pool["n"]}')
    lines.append(report['p21']['message'])
    return lines


def main(argv=None, *, root=ROOT, out=print, err=None):
    err = err or (lambda message: print(message, file=sys.stderr))
    parser = argparse.ArgumentParser(prog='python -m eval.s10.label_quality', description=__doc__)
    parser.add_argument('command', choices=('report',))
    parser.parse_args(argv)
    try:
        draw, friend, merged, hariom = load_report_inputs(root)
        report = build_report(draw, friend, merged, hariom)
        lines = report_lines(report, draw['strata'])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, csv.Error, OverflowError, ZeroDivisionError) as exc:
        if isinstance(exc, SR.FriendError) and str(exc).startswith('REFUSED:'):
            err(str(exc))
        else:
            err('ERROR: label-quality input check failed; nothing written')
        return 2
    for line in lines:
        out(line)
    return report['p21']['exit_code']


if __name__ == '__main__':
    sys.exit(main())
