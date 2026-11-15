"""S10b2b (S10b2b-09/-10/-11): descriptive diagnostics, never gating. Pre-registered numbers only.

  c-position    judge C batch-position diagnostic: per position 1-5, among cells where A and B give the same
                valid label, the count and share of cells where C gives that label (C missing counted apart,
                in the denominator)
  stability     "self-agreement under reversed item order": per judge, over the 6 x 10 scored stability cells,
                the share of identical 3-class labels (main pass vs re-label), with counts
  intra-rater   Hariom's audit-A label vs his re-label on the 30 retest cells: binary kappa (mandatory vs not)
                with a profile-cluster bootstrap CI (metrics.bootstrap_ci, seed 0, 2000, alpha 0.05); refused if
                a re-label is missing or time-stamped before the earliest allowed date
Inputs: data/gold/s10/merge_v1.json, draws/{c_batches,stability_set,retest}.json, judges/stability/<J>/
<J-id>.result.json, labels_hariom_auditA.csv, sheets/retest_labels.csv. Writes data/gold/s10/diagnostics/
<name>.json (LF, write-once). Prints aggregate numbers only (no label, reason or J-id-level result).

Run from the repo root:  python -m eval.s10.diagnostics {c-position|stability|intra-rater}
Exit codes: 0 written · 1 refused (retest incomplete / too early) · 2 input error · 3 output exists.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from standards.ruleset import normalised_sha

from . import draws as D
from .gate import CLUSTER_CI_LABEL, binary_kappa
from .merge import JUDGES, LABELS, cell_id
from .metrics import bootstrap_ci

ROOT = Path(__file__).resolve().parents[2]
FORMAT = 's10-diagnostic.v1'
DIAG_REL = 'data/gold/s10/diagnostics'
STAB_RESULTS_REL = 'data/gold/s10/judges/stability'
RETEST_LABELS_REL = 'data/gold/s10/sheets/retest_labels.csv'
STABILITY_METRIC = 'self-agreement under reversed item order'
NAMES = ('c-position', 'stability', 'intra-rater')


class Refused(PermissionError):
    pass


def c_position(merged_rows, assignment):
    pos = {a['j_id']: a['position'] for a in assignment}
    n, equal, missing = Counter(), Counter(), Counter()
    for r in merged_rows:
        a, b, c = (r['labels'].get(j) for j in JUDGES)
        if a in LABELS and a == b:
            p = pos[r['j_id']]
            n[p] += 1
            equal[p] += c == a
            missing[p] += c not in LABELS
    return [dict(position=p, ab_agree_cells=n[p], c_equal=equal[p], c_missing=missing[p],
                 share_c_equal=equal[p] / n[p] if n[p] else float('nan'))
            for p in range(1, D.C_BATCH + 1)]


def _label(result, item):
    for c in (result or {}).get('cells') or []:
        if isinstance(c, dict) and c.get('item') == item:
            return c.get('label')
    return None


def stability_self_agreement(merged_rows, stab_results, devices):
    """stab_results: {judge: {j_id: result or None}}; devices: stability draw [{j_id, scored_items}]."""
    main = {r['cell_id']: r['labels'] for r in merged_rows}
    out = {}
    for j in JUDGES:
        scored = both = same = miss_main = miss_re = 0
        for d in devices:
            for item in d['scored_items']:
                scored += 1
                m = main[cell_id(d['j_id'], item)].get(j)
                s = _label((stab_results.get(j) or {}).get(d['j_id']), item)
                miss_main += m not in LABELS
                miss_re += s not in LABELS
                if m in LABELS and s in LABELS:
                    both += 1
                    same += m == s
        out[j] = dict(scored_cells=scored, both_labelled=both, identical=same, missing_main=miss_main,
                      missing_relabel=miss_re, share_identical=same / both if both else float('nan'))
    return dict(metric=STABILITY_METRIC, labels='3-class', per_judge=out)


def intra_rater(first, again, cells, *, n_boot=2000, seed=0):
    """first / again: {cell_id: label}; cells: retest draw [{cell_id, j_id}]."""
    pairs = [dict(profile_id=c['j_id'], human=first[c['cell_id']], retest=again[c['cell_id']]) for c in cells]
    out = bootstrap_ci(pairs, lambda cs: binary_kappa(cs, 'human', 'retest'), n_boot=n_boot, seed=seed, alpha=0.05)
    return dict(statistic='Cohen kappa, binary (mandatory vs not), audit-A label vs re-label', cells=len(pairs),
                kappa=out['point'], lo=out['lo'], hi=out['hi'], alpha=0.05, n_boot=n_boot, seed=seed,
                n_profiles=out['n_profiles'], n_undefined=out['n_undefined'], ci_label=CLUSTER_CI_LABEL)


# ---- CLI -----------------------------------------------------------------------------------------------
def _stab_results(root, devices):
    res = {}
    for j in JUDGES:
        res[j] = {}
        for d in devices:
            p = Path(root) / STAB_RESULTS_REL / j / f'{d["j_id"]}.result.json'
            res[j][d['j_id']] = json.loads(p.read_text(encoding='utf-8')) if p.is_file() else None
    return res


def _compute(root, name, out):
    if name == 'c-position':
        rows = c_position(D.load_merge(root), D.load_draw(root, 'c-batches')['assignment'])
        for r in rows:
            out(f'position {r["position"]}: ab_agree={r["ab_agree_cells"]} c_equal={r["c_equal"]} '
                f'c_missing={r["c_missing"]} share={r["share_c_equal"]:.3f}')
        return dict(label='descriptive, never gating', positions=rows)
    if name == 'stability':
        devices = D.load_draw(root, 'stability')['devices']
        res = stability_self_agreement(D.load_merge(root), _stab_results(root, devices), devices)
        for j, v in res['per_judge'].items():
            out(f'{j}: {STABILITY_METRIC}: identical={v["identical"]} of both_labelled={v["both_labelled"]} '
                f'(scored={v["scored_cells"]}) share={v["share_identical"]:.3f}')
        return res
    draw = D.load_draw(root, 'retest')
    first = {k: v['label'] for k, v in D.load_label_csv(Path(root) / D.HUMAN_REL).items()}
    again_raw = D.load_label_csv(Path(root) / RETEST_LABELS_REL)
    ids = [c['cell_id'] for c in draw['cells']]
    if any(c not in again_raw or c not in first for c in ids):
        raise Refused('a retest cell has no audit-A label or no re-label')
    earliest = datetime.fromisoformat(draw['earliest_allowed_utc'])
    try:
        early = sum(datetime.fromisoformat(again_raw[c]['timestamp']) < earliest for c in ids)
    except (TypeError, ValueError) as exc:
        raise Refused('a re-label timestamp is missing or not ISO 8601') from exc
    if early:
        raise Refused(f'{early} re-labels are time-stamped before the earliest allowed date')
    res = intra_rater(first, {c: again_raw[c]['label'] for c in ids}, draw['cells'])
    out(f'intra-rater binary kappa={res["kappa"]:.4f} CI [{res["lo"]:.4f}, {res["hi"]:.4f}] cells={res["cells"]} '
        f'n_undefined={res["n_undefined"]}')
    return res


def run(root=ROOT, name='c-position', *, now=None, out=print) -> int:
    root = Path(root)
    dest = root / DIAG_REL / f'{name.replace("-", "_")}.json'
    if dest.exists():
        out(f'REFUSED (write-once): already exists: {DIAG_REL}/{dest.name}')
        return 3
    try:
        res = _compute(root, name, out)
    except Refused as exc:
        out(f'REFUSED: {exc}; nothing written')
        return 1
    except (D.DrawError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        out(f'ERROR: {exc if isinstance(exc, D.DrawError) else type(exc).__name__}; nothing written')
        return 2
    text = json.dumps(dict(format=FORMAT, diagnostic=name, created_at=now or datetime.now(timezone.utc).isoformat(),
                           result=res), indent=2, ensure_ascii=False) + '\n'
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    out(f'{DIAG_REL}/{dest.name} sha256_lf={normalised_sha(text.encode("utf-8"))}')
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='python -m eval.s10.diagnostics', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('diagnostic', choices=NAMES)
    return run(ROOT, ap.parse_args(argv).diagnostic)


if __name__ == '__main__':
    sys.exit(main())
