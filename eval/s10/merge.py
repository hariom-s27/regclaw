"""S10b2b (S10P-04 v3 / S10P-08): merge the three judges' labels into the jury label per cell.

A cell is (J-id, item); cell_id = "<J-id>|<item>". Input per cell: the label, confidence and reason of
judges A, B and C (any may be missing). A cell is ROUTED to the referee if any of these fires (all reasons
are kept, in ROUTE_REASONS order):
  label_missing_or_invalid  a judge has no valid result for the device, no cell for the item, or a label
                            outside not_applicable / recommended / mandatory
  straddle                  >= 1 mandatory AND >= 1 lower label among the 3 (includes 1-1-1)
  always_rule_part_na       ISO 10993-1, -12, -17 or -18 labelled not_applicable by any judge on a
                            contacting device (contacting = any component contact_nature != non_contact)
  non_contact_above_na      a non-contacting device (every component non_contact) labelled above
                            not_applicable by any judge. GUARD: may never fire (A1-6: a fully
                            non-contacting device cannot be expressed in the profile format)
  knee_on_non_knee          item fda-knee on a profile whose device_specific_standards lacks fda-knee.
                            GUARD: the judge file adds fda-knee only to knee profiles and the validator
                            rejects extra items, so it may never fire
  empty_reason              a present judge cell whose reason is empty or whitespace
Property checks only flag; they never change a label. Otherwise final = median of the 3 labels (middle
label after sorting not_applicable < recommended < mandatory). Routed count > STOP_ROUTED -> status STOP
(counts only; Hariom decides).

CLI (run later by Hariom on the eval round; tests use fakes in tmp dirs):
  python -m eval.s10.merge
reads data/gold/s10/judge_file_v1.yaml (LF sha must equal eval/s10/manifests/s10b1_build.json) and
data/gold/s10/judges/eval/{A,B,C}/<J-id>.result.json; writes data/gold/s10/merge_v1.json (LF, write-once).
Reasons are not copied into the output. Prints counts and the output sha only.
Exit codes: 0 written · 2 input error · 3 output exists · 4 STOP (routed > 200; nothing written).
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import yaml

from standards.ruleset import normalised_sha

ROOT = Path(__file__).resolve().parents[2]
FORMAT = 's10-merge.v1'
LABELS = ('not_applicable', 'recommended', 'mandatory')
RANK = {label: i for i, label in enumerate(LABELS)}
JUDGES = ('A', 'B', 'C')
ALWAYS_RULE_PARTS = ('ISO 10993-1', 'ISO 10993-12', 'ISO 10993-17', 'ISO 10993-18')
KNEE = 'fda-knee'
STOP_ROUTED = 200
ROUTE_REASONS = ('label_missing_or_invalid', 'straddle', 'always_rule_part_na', 'non_contact_above_na',
                 'knee_on_non_knee', 'empty_reason')
GUARD_CHECKS = ('non_contact_above_na', 'knee_on_non_knee')

JUDGE_FILE_REL = 'data/gold/s10/judge_file_v1.yaml'
RESULTS_REL = 'data/gold/s10/judges/eval'
BUILD_MANIFEST_REL = 'eval/s10/manifests/s10b1_build.json'
OUT_REL = 'data/gold/s10/merge_v1.json'


class MergeError(ValueError):
    """Bad input (message carries no labels or reasons)."""


def cell_id(j_id, item):
    return f'{j_id}|{item}'


def is_contacting(profile):
    """Engine rule (standards/engine.py): contacting = any component whose contact_nature != non_contact."""
    return any((c or {}).get('contact_nature') != 'non_contact' for c in (profile or {}).get('components') or [])


def is_knee(profile):
    return KNEE in ((profile or {}).get('device_specific_standards') or [])


def _judge_cell(result, item):
    if not isinstance(result, dict) or not isinstance(result.get('cells'), list):
        return None
    for c in result['cells']:
        if isinstance(c, dict) and c.get('item') == item:
            return dict(label=c.get('label'), confidence=c.get('confidence'), reason=c.get('reason'))
    return None


def collect_cells(entries, results):
    """entries: judge-file entries; results: {judge: {j_id: result dict or None}} -> cells in file order."""
    cells = []
    for e in entries:
        for item in e['items']:
            cells.append(dict(cell_id=cell_id(e['j_id'], item), j_id=e['j_id'], item=item,
                              judges={j: _judge_cell((results.get(j) or {}).get(e['j_id']), item) for j in JUDGES}))
    return cells


def valid_label(jc):
    return isinstance(jc, dict) and jc.get('label') in RANK


def median_label(labels):
    return sorted(labels, key=RANK.get)[1]


def property_flags(cell, entry):
    """Route reasons other than straddle (S10P-08 property checks)."""
    judges = [cell['judges'].get(j) for j in JUDGES]
    labels = [jc['label'] for jc in judges if valid_label(jc)]
    profile = entry.get('profile') or {}
    contacting = is_contacting(profile)
    flags = set()
    if len(labels) < len(JUDGES):
        flags.add('label_missing_or_invalid')
    if cell['item'] in ALWAYS_RULE_PARTS and contacting and 'not_applicable' in labels:
        flags.add('always_rule_part_na')
    if not contacting and any(RANK[x] > RANK['not_applicable'] for x in labels):
        flags.add('non_contact_above_na')
    if cell['item'] == KNEE and not is_knee(profile):
        flags.add('knee_on_non_knee')
    if any(isinstance(jc, dict) and not (isinstance(jc.get('reason'), str) and jc['reason'].strip()) for jc in judges):
        flags.add('empty_reason')
    return flags


def merge_cell(cell, entry):
    reasons = property_flags(cell, entry)
    labels = [cell['judges'][j]['label'] for j in JUDGES if valid_label(cell['judges'].get(j))]
    if len(labels) == len(JUDGES) and 'mandatory' in labels and any(x != 'mandatory' for x in labels):
        reasons.add('straddle')
    reasons = [r for r in ROUTE_REASONS if r in reasons]
    routed = bool(reasons)
    jc = cell['judges']
    return dict(cell_id=cell['cell_id'], j_id=cell['j_id'], item=cell['item'],
                labels={j: (jc[j]['label'] if valid_label(jc[j]) else None) for j in JUDGES},
                confidences={j: (jc[j].get('confidence') if isinstance(jc[j], dict) else None) for j in JUDGES},
                routed=routed, route_reasons=reasons, final=None if routed else median_label(labels))


def merge(entries, results, stop_routed=STOP_ROUTED):
    by_j = {e['j_id']: e for e in entries}
    rows = [merge_cell(c, by_j[c['j_id']]) for c in collect_cells(entries, results)]
    reasons = Counter(r for row in rows for r in row['route_reasons'])
    routed = sum(row['routed'] for row in rows)
    counts = dict(cells=len(rows), routed=routed, jury=len(rows) - routed,
                  by_reason={r: reasons.get(r, 0) for r in ROUTE_REASONS},
                  final_by_label={x: sum(row['final'] == x for row in rows) for x in LABELS})
    status = 'STOP' if routed > stop_routed else 'OK'
    return dict(status=status, stop_routed=stop_routed, counts=counts, rows=rows)


# ---- CLI -----------------------------------------------------------------------------------------------
def _load_inputs(root):
    raw = (root / JUDGE_FILE_REL).read_bytes()
    try:
        want = json.loads((root / BUILD_MANIFEST_REL).read_text(encoding='utf-8'))['files'][JUDGE_FILE_REL]['sha256_lf']
    except (ValueError, KeyError, TypeError) as exc:
        raise MergeError(f'{BUILD_MANIFEST_REL} has no sha for the judge file') from exc
    if normalised_sha(raw) != want:
        raise MergeError('judge file sha differs from s10b1_build.json')
    entries = yaml.safe_load(raw.decode('utf-8'))['entries']
    results = {}
    for j in JUDGES:
        results[j] = {}
        for e in entries:
            p = root / RESULTS_REL / j / f'{e["j_id"]}.result.json'
            results[j][e['j_id']] = json.loads(p.read_text(encoding='utf-8')) if p.is_file() else None
    return entries, results


def run(root=ROOT, *, now=None, out=print) -> int:
    root = Path(root)
    dest = root / OUT_REL
    if dest.exists():
        out(f'REFUSED (write-once): already exists: {OUT_REL}')
        return 3
    try:
        entries, results = _load_inputs(root)
        res = merge(entries, results)
    except (MergeError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        out(f'ERROR: {exc if isinstance(exc, MergeError) else type(exc).__name__}; nothing written')
        return 2
    c = res['counts']
    missing = {j: sum(v is None for v in results[j].values()) for j in JUDGES}
    out(f'devices={len(entries)} cells={c["cells"]} routed={c["routed"]} jury={c["jury"]} '
        f'devices_without_result: ' + ' '.join(f'{j}={missing[j]}' for j in JUDGES))
    out('route reasons: ' + ' '.join(f'{r}={n}' for r, n in c['by_reason'].items()))
    if res['status'] == 'STOP':
        out(f'STOP: routed {c["routed"]} > {STOP_ROUTED}; nothing written; Hariom decides')
        return 4
    doc = dict(format=FORMAT, created_at=now or datetime.now(timezone.utc).isoformat(),
               judge_file_sha256_lf=normalised_sha((root / JUDGE_FILE_REL).read_bytes()),
               status=res['status'], stop_routed=STOP_ROUTED, guard_checks=list(GUARD_CHECKS),
               counts=c, rows=res['rows'])
    text = json.dumps(doc, indent=2, ensure_ascii=False) + '\n'
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    out(f'{OUT_REL} sha256_lf={normalised_sha(text.encode("utf-8"))}')
    return 0


def main(argv=None) -> int:
    argparse.ArgumentParser(prog='python -m eval.s10.merge', description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    return run(ROOT)


if __name__ == '__main__':
    sys.exit(main())
