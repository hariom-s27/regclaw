"""S10b2b (S10P-07 v3 + A1-3): the audit-A gate with looks at 150 / 250 / 350.

Per look L (audit-A list positions 1..L, list order):
  gate cells   = prefix cells whose final label came from the jury (merge row not routed); routed cells are
                 never scored, so referee and audit-B labels are never an input
  statistic    = Cohen kappa, binary (mandatory vs not), Hariom's blind label (rows) vs the final label
  lower bound  = metrics.bootstrap_ci(gate cells, kappa, n_boot=2000, seed=0, alpha=2*Q)['lo'], i.e. the
                 linear Q-quantile (Q = 0.0083, one-sided 0.83% per look) of the defined resampled kappas;
                 profiles (cluster = J-id, carried as profile_id) are resampled; undefined kappas (one class
                 only) are skipped and counted in n_undefined
  pass         = point and lower defined and lower >= 0.60; stop at the first pass; fail at 350 -> STOP
Secondary rows (reported, never gating; pre-registered S10P-07 list only):
  all prefix cells: binary kappa of Hariom vs the jury median (median only when all 3 judge labels exist,
  else skipped and counted); gate cells: Po, PABAK, AC1 (binary), prevalence and bias index (absolute;
  signed kept), 3x3 table (rows Hariom, columns final, order NA/rec/mand), gold-mandatory recall and
  precision vs Hariom (Hariom = reference) with profile-cluster CIs (bootstrap_ci, seed 0, 2000, alpha
  0.05), FCE analytic interval labelled "assumes independent cells (too narrow here)".

CLI (run later by Hariom; tests use fakes in tmp dirs):  python -m eval.s10.gate
Reads data/gold/s10/audit_a_list.csv (LF sha must equal eval/s10/manifests/s10b1_build.json),
data/gold/s10/labels_hariom_auditA.csv (utf-8-sig, CRLF) and data/gold/s10/merge_v1.json (status OK; its
judge-file sha must equal s10b1_build.json). Evaluates every look whose prefix Hariom has fully labelled, in
order, until the first pass. Each evaluated look is written once to data/gold/s10/gate/gate_look<L>.json with
the sha of the human-label prefix; an existing look file is reused only if that sha still matches.
Prints counts and gate numbers only (no label, reason or J-id-level result).
Exit codes: 0 passed (now or earlier) · 2 input error · 4 STOP (failed at 350) · 5 not passed yet: label the
next look.
"""
import argparse
import csv
from datetime import datetime, timezone
import io
import json
import math
from pathlib import Path
import sys

from standards.ruleset import normalised_sha

from . import agreement as G
from .merge import LABELS, cell_id, median_label
from .metrics import bootstrap_ci

ROOT = Path(__file__).resolve().parents[2]
FORMAT = 's10-gate-look.v1'
LOOKS = (150, 250, 350)
Q = 0.0083
THRESHOLD = 0.60
N_BOOT = 2000
SEED = 0
CI_ALPHA = 0.05
CLUSTER_CI_LABEL = 'profile-cluster bootstrap (J-id)'

LIST_REL = 'data/gold/s10/audit_a_list.csv'
HUMAN_REL = 'data/gold/s10/labels_hariom_auditA.csv'
MERGE_REL = 'data/gold/s10/merge_v1.json'
BUILD_MANIFEST_REL = 'eval/s10/manifests/s10b1_build.json'
JUDGE_FILE_REL = 'data/gold/s10/judge_file_v1.yaml'
GATE_DIR_REL = 'data/gold/s10/gate'


class GateError(ValueError):
    """Bad input (message carries no labels)."""


def _nan(v):
    return isinstance(v, float) and math.isnan(v)


def binary_kappa(cells, a='human', b='final'):
    return G.cohen_kappa(G.table([(G.binarise(c[a]), G.binarise(c[b])) for c in cells], G.BINARY))


def _ratio(num, den):
    return num / den if den else float('nan')


def recall_vs_human(cells):
    """Of Hariom's mandatory cells, the share the final label also calls mandatory."""
    hm = [c for c in cells if c['human'] == 'mandatory']
    return _ratio(sum(c['final'] == 'mandatory' for c in hm), len(hm))


def precision_vs_human(cells):
    """Of the final-mandatory cells, the share Hariom also calls mandatory."""
    fm = [c for c in cells if c['final'] == 'mandatory']
    return _ratio(sum(c['human'] == 'mandatory' for c in fm), len(fm))


def _ci(cells, fn, n_boot, seed):
    out = bootstrap_ci(cells, fn, n_boot=n_boot, seed=seed, alpha=CI_ALPHA)
    return dict(point=out['point'], lo=out['lo'], hi=out['hi'], n_undefined=out['n_undefined'],
                alpha=CI_ALPHA, label=CLUSTER_CI_LABEL)


def gate_look(prefix, human, merged, *, n_boot=N_BOOT, seed=SEED, q=Q, threshold=THRESHOLD):
    """prefix: audit rows (dicts with j_id, item); human: {cell_id: label}; merged: {cell_id: merge row}."""
    gate, all_cells, skipped = [], [], 0
    for r in prefix:
        cid = cell_id(r['j_id'], r['item'])
        row = merged[cid]
        if not row['routed']:
            gate.append(dict(profile_id=r['j_id'], human=human[cid], final=row['final']))
        labels = [row['labels'].get(j) for j in ('A', 'B', 'C')]
        if all(x in LABELS for x in labels):
            all_cells.append(dict(profile_id=r['j_id'], human=human[cid], median=median_label(labels)))
        else:
            skipped += 1
    boot = bootstrap_ci(gate, binary_kappa, n_boot=n_boot, seed=seed, alpha=2 * q)
    point, lower = boot['point'], boot['lo']
    passed = not _nan(point) and not _nan(lower) and lower >= threshold
    bt = G.table([(G.binarise(c['human']), G.binarise(c['final'])) for c in gate], G.BINARY)
    routed = len(prefix) - len(gate)
    secondary = dict(
        all_prefix_vs_median=dict(kappa_binary=binary_kappa(all_cells, 'human', 'median'),
                                  cells_used=len(all_cells), cells_skipped_not_3_labels=skipped),
        gate_cells=dict(
            binary_table=dict(order=list(G.BINARY), rows='human', cols='final', counts=bt),
            po=G.po(bt), pabak=G.pabak(bt), ac1=G.gwet_ac1(bt),
            prevalence_index=G.prevalence_index(bt), bias_index=G.bias_index(bt),
            prevalence_index_signed=G.prevalence_index(bt, signed=True),
            bias_index_signed=G.bias_index(bt, signed=True),
            table_3x3=dict(order=list(LABELS), rows='human', cols='final',
                           counts=G.table([(c['human'], c['final']) for c in gate], LABELS)),
            gold_mandatory_recall_vs_human=_ci(gate, recall_vs_human, n_boot, seed),
            gold_mandatory_precision_vs_human=_ci(gate, precision_vs_human, n_boot, seed),
            fce_interval=G.fce_interval(bt)))
    return dict(look=len(prefix), prefix_cells=len(prefix), gate_cells=len(gate), routed_in_prefix=routed,
                routed_share=_ratio(routed, len(prefix)),
                gate=dict(statistic='Cohen kappa, binary (mandatory vs not), human vs final', kappa=point,
                          lower=lower, q=q, threshold=threshold, n_boot=n_boot, seed=seed,
                          n_profiles=boot['n_profiles'], n_undefined=boot['n_undefined'], passed=passed),
                secondary=secondary)


def labelled_prefix(audit_rows, human):
    n = 0
    for r in audit_rows:
        if cell_id(r['j_id'], r['item']) not in human:
            break
        n += 1
    return n


def run_gate(audit_rows, human, merged, *, looks=LOOKS, **kw):
    """Sequential looks. status: PASS (at look) · CONTINUE (failed; next look needs labels) · STOP (failed at
    the last look) · NEED_LABELS (first look not fully labelled)."""
    done = labelled_prefix(audit_rows, human)
    results = []
    for L in looks:
        if L > len(audit_rows):
            raise GateError(f'audit list has {len(audit_rows)} rows, look needs {L}')
        if done < L:
            return dict(status='CONTINUE' if results else 'NEED_LABELS', next_look=L, labelled=done, looks=results)
        res = gate_look(audit_rows[:L], human, merged, **kw)
        results.append(res)
        if res['gate']['passed']:
            return dict(status='PASS', look=L, labelled=done, looks=results)
    return dict(status='STOP', look=looks[-1], labelled=done, looks=results)


# ---- CLI -----------------------------------------------------------------------------------------------
def _build_sha(root, rel):
    try:
        return json.loads((root / BUILD_MANIFEST_REL).read_text(encoding='utf-8'))['files'][rel]['sha256_lf']
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise GateError(f'{BUILD_MANIFEST_REL} has no sha for {rel}') from exc


def load_inputs(root):
    raw = (root / LIST_REL).read_bytes()
    if normalised_sha(raw) != _build_sha(root, LIST_REL):
        raise GateError('audit list sha differs from s10b1_build.json')
    audit = sorted(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))), key=lambda r: int(r['order']))
    if [int(r['order']) for r in audit] != list(range(1, len(audit) + 1)):
        raise GateError('audit list order is not 1..N')
    merge_doc = json.loads((root / MERGE_REL).read_text(encoding='utf-8'))
    if merge_doc.get('format') != 's10-merge.v1' or merge_doc.get('status') != 'OK':
        raise GateError('merge file has the wrong format or is not status OK')
    if merge_doc.get('judge_file_sha256_lf') != _build_sha(root, JUDGE_FILE_REL):
        raise GateError('merge file was built from a different judge file')
    merged = {row['cell_id']: row for row in merge_doc['rows']}
    if any(cell_id(r['j_id'], r['item']) not in merged for r in audit):
        raise GateError('an audit cell is missing from the merge file')
    human = {}
    path = root / HUMAN_REL
    if path.is_file():
        for r in csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))):
            if r.get('label') not in LABELS:
                raise GateError('human labels file holds an invalid label')
            human[cell_id(r['j_id'], r['item'])] = r['label']
    return audit, human, merged


def prefix_sha(audit_rows, human, L):
    text = ''.join(f'{r["order"]},{r["j_id"]},{r["item"]},{human[cell_id(r["j_id"], r["item"])]}\n'
                   for r in audit_rows[:L])
    return normalised_sha(text.encode('utf-8'))


def _say(out, res):
    g = res['gate']
    out(f'look {res["look"]}: prefix={res["prefix_cells"]} gate_cells={res["gate_cells"]} '
        f'routed={res["routed_in_prefix"]} routed_share={res["routed_share"]:.3f} kappa={g["kappa"]:.4f} '
        f'lower={g["lower"]:.4f} n_undefined={g["n_undefined"]} passed={g["passed"]}')


def run(root=ROOT, *, now=None, out=print) -> int:
    root = Path(root)
    try:
        audit, human, merged = load_inputs(root)
    except (GateError, OSError, ValueError, KeyError, TypeError) as exc:
        out(f'ERROR: {exc if isinstance(exc, GateError) else type(exc).__name__}; nothing written')
        return 2
    done = labelled_prefix(audit, human)
    out(f'audit rows={len(audit)} labelled prefix={done}')
    for i, L in enumerate(LOOKS):
        if done < L:
            out(f'not passed yet: look {L} needs {L - done} more labels')
            return 5
        dest = root / GATE_DIR_REL / f'gate_look{L}.json'
        sha = prefix_sha(audit, human, L)
        if dest.exists():
            stored = json.loads(dest.read_text(encoding='utf-8'))
            if stored.get('human_prefix_sha256_lf') != sha:
                out(f'ERROR: human labels in look {L} changed since gate_look{L}.json was written; nothing written')
                return 2
            res = stored['result']
            out(f'look {L}: already evaluated (file reused)')
        else:
            res = gate_look(audit[:L], human, merged)
            doc = dict(format=FORMAT, created_at=now or datetime.now(timezone.utc).isoformat(), look=L,
                       human_prefix_sha256_lf=sha, merge_file_sha256_lf=normalised_sha((root / MERGE_REL).read_bytes()),
                       result=res)
            text = json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=True) + '\n'
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open('x', encoding='utf-8', newline='\n') as fh:
                fh.write(text)
            _say(out, res)
            out(f'{GATE_DIR_REL}/gate_look{L}.json sha256_lf={normalised_sha(text.encode("utf-8"))}')
        if res['gate']['passed']:
            out(f'GATE PASSED at look {L}')
            return 0
        if i == len(LOOKS) - 1:
            out(f'STOP: gate failed at look {L}; Hariom decides')
            return 4
    return 5


def main(argv=None) -> int:
    argparse.ArgumentParser(prog='python -m eval.s10.gate', description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    return run(ROOT)


if __name__ == '__main__':
    sys.exit(main())
