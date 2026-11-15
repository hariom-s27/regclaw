"""S10b2b (S10b2b-02/-08/-09/-10/-11): the seeded S10b draws. Real draws are run later by Hariom.

Every draw uses random.Random("S10b-v1|<purpose>") on a SORTED list of ids:
  audit-b              unanimous jury cells (3 valid, equal labels; not routed) with >= 1 "low" confidence,
                       minus every audit-A cell; > 50 -> sample 50, else all (sorted)
  retest               30 cells sampled from the cell ids at audit-A positions 1-150 (all must be labelled);
                       earliest allowed date = latest label timestamp among positions 1-150 + 7 days
  stab-devices         6 J-ids sampled from the sorted J-ids
  stab-items|<J-id>    10 items sampled from that device's sorted items
  c-batches            sorted J-ids shuffled, cut into consecutive batches of 5 (batch id, position 1-5)
Each CLI subcommand writes data/gold/s10/draws/<name>.json (LF, write-once) and prints counts and its LF
sha256; the frozen file is the record. Profile content, labels and cell ids are never printed.

Run from the repo root:  python -m eval.s10.draws {c-batches|stability|retest|audit-b}
Exit codes: 0 written · 2 input error · 3 output exists.
"""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import random
import sys

import yaml

from standards.ruleset import normalised_sha

from .merge import LABELS, cell_id

ROOT = Path(__file__).resolve().parents[2]
SEED = 'S10b-v1'
FORMAT = 's10-draw.v1'
AUDIT_B_MAX = 50
RETEST_N = 30
RETEST_POSITIONS = 150
RETEST_WAIT_DAYS = 7
STAB_DEVICES = 6
STAB_ITEMS = 10
C_BATCH = 5

JUDGE_FILE_REL = 'data/gold/s10/judge_file_v1.yaml'
LIST_REL = 'data/gold/s10/audit_a_list.csv'
HUMAN_REL = 'data/gold/s10/labels_hariom_auditA.csv'
MERGE_REL = 'data/gold/s10/merge_v1.json'
BUILD_MANIFEST_REL = 'eval/s10/manifests/s10b1_build.json'
DRAWS_REL = 'data/gold/s10/draws'
OUT = {'c-batches': 'c_batches.json', 'stability': 'stability_set.json', 'retest': 'retest.json',
       'audit-b': 'audit_b.json'}


class DrawError(ValueError):
    """Bad input (message carries no labels or profile content)."""


def seed(purpose):
    return f'{SEED}|{purpose}'


# ---- pure draws ----------------------------------------------------------------------------------------
def c_batches(jids):
    """[{j_id, batch, position}] in batch/position order."""
    ids = sorted(jids)
    random.Random(seed('c-batches')).shuffle(ids)
    return [dict(j_id=j, batch=k // C_BATCH + 1, position=k % C_BATCH + 1) for k, j in enumerate(ids)]


def batches_of(assignment):
    """c_batches output -> [[j_id, ...] per batch], positions in order."""
    out = {}
    for r in sorted(assignment, key=lambda r: (r['batch'], r['position'])):
        out.setdefault(r['batch'], []).append(r['j_id'])
    return [out[b] for b in sorted(out)]


def stability(entries):
    """entries: [{j_id, items}] -> [{j_id, scored_items}] in draw order."""
    items = {e['j_id']: list(e['items']) for e in entries}
    devices = random.Random(seed('stab-devices')).sample(sorted(items), STAB_DEVICES)
    return [dict(j_id=j, scored_items=random.Random(seed(f'stab-items|{j}')).sample(sorted(items[j]), STAB_ITEMS))
            for j in devices]


def retest(audit_rows, human):
    """audit_rows in list order ({order, j_id, item}); human: {cell_id: {'label', 'timestamp'}}.
    Returns (cells [{cell_id, j_id, item}] in draw order, earliest allowed datetime)."""
    prefix = audit_rows[:RETEST_POSITIONS]
    if len(prefix) < RETEST_POSITIONS:
        raise DrawError(f'audit list has fewer than {RETEST_POSITIONS} rows')
    ids = [cell_id(r['j_id'], r['item']) for r in prefix]
    if any(c not in human for c in ids):
        raise DrawError(f'audit-A positions 1-{RETEST_POSITIONS} are not all labelled')
    try:
        latest = max(datetime.fromisoformat(human[c]['timestamp']) for c in ids)
    except (TypeError, ValueError) as exc:
        raise DrawError('a label timestamp in positions 1-150 is missing or not ISO 8601') from exc
    by_id = {cell_id(r['j_id'], r['item']): r for r in prefix}
    picked = random.Random(seed('retest')).sample(sorted(ids), RETEST_N)
    return [dict(cell_id=c, j_id=by_id[c]['j_id'], item=by_id[c]['item']) for c in picked], \
        latest + timedelta(days=RETEST_WAIT_DAYS)


def audit_b_candidates(merged_rows, audit_a_ids):
    """Unanimous, not routed, any 'low' confidence, not in audit A. Returns (sorted ids, counts)."""
    audit_a = set(audit_a_ids)
    unanimous_low = routed_excluded = in_audit_a = 0
    out = []
    for row in merged_rows:
        labels = [row['labels'].get(j) for j in ('A', 'B', 'C')]
        if not (all(x in LABELS for x in labels) and len(set(labels)) == 1):
            continue
        if 'low' not in [row['confidences'].get(j) for j in ('A', 'B', 'C')]:
            continue
        unanimous_low += 1
        if row['routed']:
            routed_excluded += 1
        elif row['cell_id'] in audit_a:
            in_audit_a += 1
        else:
            out.append(row['cell_id'])
    return sorted(out), dict(unanimous_low=unanimous_low, routed_excluded=routed_excluded,
                             audit_a_excluded=in_audit_a, candidates=len(out))


def audit_b(merged_rows, audit_a_ids):
    cands, counts = audit_b_candidates(merged_rows, audit_a_ids)
    picked = random.Random(seed('audit-b')).sample(cands, AUDIT_B_MAX) if len(cands) > AUDIT_B_MAX else cands
    by_id = {r['cell_id']: r for r in merged_rows}
    return [dict(cell_id=c, j_id=by_id[c]['j_id'], item=by_id[c]['item']) for c in picked], \
        dict(counts, selected=len(picked))


# ---- sha-checked loaders (shared with sheets / diagnostics) --------------------------------------------
def build_sha(root, rel):
    try:
        return json.loads((Path(root) / BUILD_MANIFEST_REL).read_text(encoding='utf-8'))['files'][rel]['sha256_lf']
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise DrawError(f'{BUILD_MANIFEST_REL} has no sha for {rel}') from exc


def load_entries(root):
    raw = (Path(root) / JUDGE_FILE_REL).read_bytes()
    if normalised_sha(raw) != build_sha(root, JUDGE_FILE_REL):
        raise DrawError('judge file sha differs from s10b1_build.json')
    return yaml.safe_load(raw.decode('utf-8'))['entries']


def load_audit_rows(root):
    raw = (Path(root) / LIST_REL).read_bytes()
    if normalised_sha(raw) != build_sha(root, LIST_REL):
        raise DrawError('audit list sha differs from s10b1_build.json')
    rows = sorted(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))), key=lambda r: int(r['order']))
    if [int(r['order']) for r in rows] != list(range(1, len(rows) + 1)):
        raise DrawError('audit list order is not 1..N')
    return rows


def load_label_csv(path):
    """Label CSV (utf-8-sig; label_tool FIELDS) -> {cell_id: {'label', 'timestamp'}}; blank labels skipped."""
    out = {}
    p = Path(path)
    if not p.is_file():
        return out
    for r in csv.DictReader(io.StringIO(p.read_text(encoding='utf-8-sig'))):
        if not (r.get('label') or '').strip():
            continue
        if r['label'] not in LABELS:
            raise DrawError(f'{p.name} holds an invalid label')
        out[cell_id(r['j_id'], r['item'])] = dict(label=r['label'], timestamp=r.get('timestamp', ''))
    return out


def load_merge(root):
    doc = json.loads((Path(root) / MERGE_REL).read_text(encoding='utf-8'))
    if doc.get('format') != 's10-merge.v1' or doc.get('status') != 'OK':
        raise DrawError('merge file has the wrong format or is not status OK')
    if doc.get('judge_file_sha256_lf') != build_sha(root, JUDGE_FILE_REL):
        raise DrawError('merge file was built from a different judge file')
    return doc['rows']


def load_draw(root, name):
    p = Path(root) / DRAWS_REL / OUT[name]
    if not p.is_file():
        raise DrawError(f'draw file missing: {DRAWS_REL}/{OUT[name]}')
    return json.loads(p.read_text(encoding='utf-8'))


# ---- CLI -----------------------------------------------------------------------------------------------
def _draw(root, name):
    """Returns (payload dict, count lines)."""
    if name == 'c-batches':
        a = c_batches(e['j_id'] for e in load_entries(root))
        return dict(seed_string=seed('c-batches'), batch_size=C_BATCH, assignment=a), \
            [f'devices={len(a)} batches={len(batches_of(a))}']
    if name == 'stability':
        s = stability(load_entries(root))
        return dict(seed_strings=[seed('stab-devices'), seed('stab-items|<J-id>')], devices=s), \
            [f'devices={len(s)} scored_items_per_device={STAB_ITEMS}']
    if name == 'retest':
        rows = load_audit_rows(root)
        cells, earliest = retest(rows, load_label_csv(Path(root) / HUMAN_REL))
        return dict(seed_string=seed('retest'), positions=f'1-{RETEST_POSITIONS}', cells=cells,
                    earliest_allowed_utc=earliest.astimezone(timezone.utc).isoformat()), \
            [f'cells={len(cells)}', f'earliest allowed retest date (UTC): {earliest.astimezone(timezone.utc).date()}']
    rows = load_audit_rows(root)
    cells, counts = audit_b(load_merge(root), [cell_id(r['j_id'], r['item']) for r in rows])
    return dict(seed_string=seed('audit-b'), max_cells=AUDIT_B_MAX, counts=counts, cells=cells), \
        [' '.join(f'{k}={v}' for k, v in counts.items())]


def run(root=ROOT, name='c-batches', *, now=None, out=print) -> int:
    root = Path(root)
    dest = root / DRAWS_REL / OUT[name]
    if dest.exists():
        out(f'REFUSED (write-once): already exists: {DRAWS_REL}/{OUT[name]}')
        return 3
    try:
        payload, lines = _draw(root, name)
    except (DrawError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        out(f'ERROR: {exc if isinstance(exc, DrawError) else type(exc).__name__}; nothing written')
        return 2
    doc = dict(format=FORMAT, draw=name, created_at=now or datetime.now(timezone.utc).isoformat(), **payload)
    text = json.dumps(doc, indent=2, ensure_ascii=False) + '\n'
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    for line in lines:
        out(line)
    out(f'{DRAWS_REL}/{OUT[name]} sha256_lf={normalised_sha(text.encode("utf-8"))}')
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='python -m eval.s10.draws', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('draw', choices=tuple(OUT))
    return run(ROOT, ap.parse_args(argv).draw)


if __name__ == '__main__':
    sys.exit(main())
