"""Pure S10 metrics (R23). No I/O, no randomness except the seeded bootstrap.

A CELL is one scored (profile, ISO part) decision of one run:
    {'profile_id', 'item', 'gold', 'final', 'rule', 'llm', 'setting', 'repeat'}
gold/final/rule are labels; llm is a label, 'omitted' (LLM skipped a rule-flagged
part) or None (LLM output unavailable / fell back). Cells from all repeats are pooled.
`pred` selects the prediction: 'final' (hybrid tool) or 'rule' (rules-only baseline).
Undefined values (0/0) are float('nan').
"""
import math
import random
from collections import defaultdict

LABELS = ('not_applicable', 'recommended', 'mandatory')
RANK = {label: i for i, label in enumerate(LABELS)}
NAN = float('nan')


def _ratio(num, den):
    return num / den if den else NAN


def confusion(cells, pred='final'):
    """3x3 counts, rows = gold, columns = prediction, order LABELS."""
    cm = [[0] * 3 for _ in range(3)]
    for c in cells:
        cm[RANK[c['gold']]][RANK[c[pred]]] += 1
    return cm


# Every scored metric is a function of the confusion matrix only, so the bootstrap can sum
# per-profile matrices instead of re-scanning cells (see bootstrap_metrics).
def under_call_cm(cm):
    """PRIMARY SAFETY METRIC: share of gold-mandatory cells predicted below mandatory."""
    return _ratio(cm[2][0] + cm[2][1], sum(cm[2]))


def over_call_cm(cm):
    """Share of gold non-mandatory cells predicted ABOVE gold."""
    return _ratio(cm[0][1] + cm[0][2] + cm[1][2], sum(cm[0]) + sum(cm[1]))


def accuracy_cm(cm):
    return _ratio(sum(cm[i][i] for i in range(3)), sum(map(sum, cm)))


def macro_f1_cm(cm):
    """Mean F1 over the labels present in gold or prediction (absent labels are skipped)."""
    scores = []
    for i in range(3):
        gold_n, pred_n, tp = sum(cm[i]), sum(cm[r][i] for r in range(3)), cm[i][i]
        if not gold_n and not pred_n:
            continue
        p, r = (tp / pred_n if pred_n else 0.0), (tp / gold_n if gold_n else 0.0)
        scores.append(2 * p * r / (p + r) if p + r else 0.0)
    return sum(scores) / len(scores) if scores else NAN


def _kappa_cm(cm, weight):
    k, n = len(cm), sum(map(sum, cm))
    if not n:
        return NAN
    row = [sum(cm[i]) / n for i in range(k)]
    col = [sum(cm[i][j] for i in range(k)) / n for j in range(k)]
    num = sum(weight(i, j) * cm[i][j] / n for i in range(k) for j in range(k))
    den = sum(weight(i, j) * row[i] * col[j] for i in range(k) for j in range(k))
    return 1 - num / den if den else NAN


def qw_kappa_cm(cm):
    """Quadratic-weighted kappa, labels ordered not_applicable < recommended < mandatory."""
    return _kappa_cm(cm, lambda i, j: (i - j) ** 2 / 4)


def binary_kappa_cm(cm):
    """Cohen's kappa on mandatory vs not-mandatory."""
    b = [[cm[0][0] + cm[0][1] + cm[1][0] + cm[1][1], cm[0][2] + cm[1][2]], [cm[2][0] + cm[2][1], cm[2][2]]]
    return _kappa_cm(b, lambda i, j: float(i != j))


CM_METRICS = {'under_call_rate': under_call_cm, 'over_call_rate': over_call_cm, 'accuracy': accuracy_cm,
              'macro_f1': macro_f1_cm, 'qw_kappa': qw_kappa_cm, 'binary_kappa': binary_kappa_cm}


def under_call_rate(cells, pred='final'):
    return under_call_cm(confusion(cells, pred))


def over_call_rate(cells, pred='final'):
    return over_call_cm(confusion(cells, pred))


def accuracy(cells, pred='final'):
    return accuracy_cm(confusion(cells, pred))


def macro_f1(cells, pred='final'):
    return macro_f1_cm(confusion(cells, pred))


def qw_kappa(cells, pred='final'):
    return qw_kappa_cm(confusion(cells, pred))


def binary_kappa(cells, pred='final'):
    return binary_kappa_cm(confusion(cells, pred))


def upgrade_pr(cells):
    """Upgrade = final above the rule floor. Warranted = gold above the rule floor.
    precision = warranted upgrades / upgrades; recall = warranted upgrades / warranted cells."""
    up = [c for c in cells if RANK[c['final']] > RANK[c['rule']]]
    warranted = [c for c in cells if RANK[c['gold']] > RANK[c['rule']]]
    hit = sum(RANK[c['gold']] > RANK[c['rule']] for c in up)
    return dict(upgrades=len(up), warranted=len(warranted), warranted_upgrades=hit,
                precision=_ratio(hit, len(up)), recall=_ratio(hit, len(warranted)))


def blocked_downgrades(cells):
    """LLM below the rule floor (blocked by the guard). Gold not needed; pass all output cells."""
    blocked = sum(c['llm'] in RANK and RANK[c['llm']] < RANK[c['rule']] for c in cells)
    omitted = sum(c['llm'] == 'omitted' for c in cells)
    return dict(blocked_downgrades=blocked, omitted_rule_parts=omitted,
                llm_unavailable=sum(c['llm'] is None for c in cells))


def disagreement_table(cells):
    """Cells where the LLM label differs from the rule label, split by who matches gold."""
    table = dict(rules_right=0, llm_right=0, both_wrong=0, n=0)
    rows = []
    for c in cells:
        if c['llm'] not in RANK or c['llm'] == c['rule']:
            continue
        kind = ('rules_right' if c['rule'] == c['gold'] else
                'llm_right' if c['llm'] == c['gold'] else 'both_wrong')
        table[kind] += 1
        table['n'] += 1
        rows.append(dict(profile_id=c['profile_id'], item=c['item'], gold=c['gold'], rule=c['rule'],
                         llm=c['llm'], final=c['final'], kind=kind, repeat=c.get('repeat')))
    return dict(table=table, rows=rows)


def stability(cells, pred='final'):
    """Over k repeats of ONE setting: share of profiles whose whole label vector is identical
    in every repeat, and per-cell flip rate (share of (profile, item) cells with >1 label)."""
    by_profile = defaultdict(lambda: defaultdict(dict))
    for c in cells:
        by_profile[c['profile_id']][c['repeat']][c['item']] = c[pred]
    repeats = {len(runs) for runs in by_profile.values()}
    if not by_profile or min(repeats) < 2:
        return dict(identical_profile_share=NAN, cell_flip_rate=NAN, flipping_cells=[], repeats=sorted(repeats))
    identical, flipping, n_cells = 0, [], 0
    for pid, runs in sorted(by_profile.items()):
        vectors = [tuple(sorted(v.items())) for v in runs.values()]
        identical += len(set(vectors)) == 1
        items = sorted({i for v in runs.values() for i in v})
        for item in items:
            n_cells += 1
            seen = sorted({v.get(item) for v in runs.values()}, key=str)
            if len(seen) > 1:
                flipping.append(dict(profile_id=pid, item=item, labels=seen))
    return dict(identical_profile_share=identical / len(by_profile), cell_flip_rate=_ratio(len(flipping), n_cells),
                flipping_cells=flipping, repeats=sorted(repeats))


def _quantile(sorted_values, q):
    """Linear interpolation between order statistics (numpy's default 'linear')."""
    pos = (len(sorted_values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def bootstrap_ci(cells, fn, *, n_boot=2000, seed=0, alpha=0.05):
    """Percentile CI resampling PROFILES with replacement (all cells of a drawn profile,
    across all repeats, move together). fn(cells) -> float. Seeded, deterministic."""
    groups = defaultdict(list)
    for c in cells:
        groups[c['profile_id']].append(c)
    ids = sorted(groups)
    point = fn(cells)
    if not ids or n_boot < 1:
        return dict(point=point, lo=NAN, hi=NAN, n_boot=n_boot, seed=seed, n_profiles=len(ids), n_undefined=0)
    rng = random.Random(seed)
    values, undefined = [], 0
    for _ in range(n_boot):
        sample = [c for pid in (rng.choice(ids) for _ in ids) for c in groups[pid]]
        v = fn(sample)
        if isinstance(v, float) and math.isnan(v):
            undefined += 1
        else:
            values.append(v)
    values.sort()
    lo, hi = (_quantile(values, alpha / 2), _quantile(values, 1 - alpha / 2)) if values else (NAN, NAN)
    return dict(point=point, lo=lo, hi=hi, n_boot=n_boot, seed=seed, n_profiles=len(ids), n_undefined=undefined)


def lift(cells, metric, *, n_boot=2000, seed=0):
    """LLM lift = metric(hybrid) - metric(rules-only), paired by profile in the bootstrap."""
    def diff(cs):
        return metric(cs, 'final') - metric(cs, 'rule')
    out = bootstrap_ci(cells, diff, n_boot=n_boot, seed=seed)
    return dict(hybrid=metric(cells, 'final'), rules=metric(cells, 'rule'), lift=out['point'],
                lo=out['lo'], hi=out['hi'], n_boot=n_boot, seed=seed, n_profiles=out['n_profiles'])


SCORED = {'under_call_rate': under_call_rate, 'over_call_rate': over_call_rate, 'accuracy': accuracy,
          'macro_f1': macro_f1, 'qw_kappa': qw_kappa, 'binary_kappa': binary_kappa}


def _isnan(v):
    return isinstance(v, float) and math.isnan(v)


def bootstrap_metrics(cells, *, n_boot=2000, seed=0, alpha=0.05):
    """All CM_METRICS for hybrid ('final'), rules-only ('rule') and paired lift (final - rule)
    in ONE profile-resampling bootstrap. Draws are identical to bootstrap_ci with the same
    seed (tested), but each resample sums per-profile confusion matrices."""
    groups = defaultdict(list)
    for c in cells:
        groups[c['profile_id']].append(c)
    ids = sorted(groups)
    per = {pid: (confusion(groups[pid], 'final'), confusion(groups[pid], 'rule')) for pid in ids}

    def add(mats):
        return [[sum(m[i][j] for m in mats) for j in range(3)] for i in range(3)]

    def evaluate_all(cm_final, cm_rule):
        out = {}
        for name, fn in CM_METRICS.items():
            h, r = fn(cm_final), fn(cm_rule)
            out[name] = dict(hybrid=h, rules_only=r, llm_lift=h - r)
        return out

    point = evaluate_all(confusion(cells, 'final'), confusion(cells, 'rule'))
    draws = {name: {k: [] for k in ('hybrid', 'rules_only', 'llm_lift')} for name in CM_METRICS}
    rng = random.Random(seed)
    for _ in range(n_boot if ids else 0):
        sample = [rng.choice(ids) for _ in ids]
        res = evaluate_all(add([per[p][0] for p in sample]), add([per[p][1] for p in sample]))
        for name, vals in res.items():
            for k, v in vals.items():
                draws[name][k].append(v)
    out = {}
    for name in CM_METRICS:
        out[name] = {}
        for k in ('hybrid', 'rules_only', 'llm_lift'):
            values = sorted(v for v in draws[name][k] if not _isnan(v))
            lo, hi = (_quantile(values, alpha / 2), _quantile(values, 1 - alpha / 2)) if values else (NAN, NAN)
            out[name][k] = dict(point=point[name][k], lo=lo, hi=hi,
                                n_undefined=len(draws[name][k]) - len(values))
    return dict(metrics=out, n_boot=n_boot, seed=seed, alpha=alpha, n_profiles=len(ids),
                resampling_unit='profile')
