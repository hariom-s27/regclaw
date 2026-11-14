"""S10b-07/08: draw the Audit-A cell list (350 distinct cells) from judge_file_v1.yaml (J-ids only; no map).

Algorithm (seed "S10b-v1"; see logs/S10b1_report.md, Part C plan):
  1. cells = (j_id, item, family) for every item of every entry; family map fixed below; unknown item = error;
  2. family totals T_f = largest-remainder rounding of TOTAL * C_f / C (floor, then +1 to the largest
     remainders; ties in family order F1..F5); T_f <= C_f checked;
  3. per-J-id quotas: base = TOTAL // J, extra = TOTAL % J; Random("S10b-v1|rows").sample(sorted J-ids, extra)
     get base + 1, the rest get base (30 J-ids: 20 x 12 + 10 x 11 = 350);
  4. selection, attempt a = 0, 1, ...: Random(f"S10b-v1|cells|{a}") shuffles the cells sorted by
     (j_id, item position); greedy accept while the J-id and family counts are below quota; the first
     attempt that reaches TOTAL wins;
  5. order: the selected cells sorted by (j_id, item position), shuffled by Random("S10b-v1|order"),
     numbered 1..TOTAL.
Writes data/gold/s10/audit_a_list.csv (order,j_id,item,family; LF; write-once). Prints counts only.

Run from the repo root:  python -m eval.s10.audit_list
Exit codes: 0 written · 2 input error · 3 output exists.
"""
import csv
import io
import random
import sys
from collections import Counter
from pathlib import Path

import yaml

from standards.ruleset import normalised_sha

ROOT = Path(__file__).resolve().parents[2]
SEED = 'S10b-v1'
TOTAL = 350
MAX_ATTEMPTS = 1000
JUDGE_REL = 'data/gold/s10/judge_file_v1.yaml'
OUT_REL = 'data/gold/s10/audit_a_list.csv'
COLUMNS = ('order', 'j_id', 'item', 'family')
FAMILY_ITEMS = {
    'F1': ['ISO 10993-3', 'ISO 10993-4', 'ISO 10993-5', 'ISO 10993-6', 'ISO 10993-10', 'ISO 10993-11', 'ISO 10993-23'],
    'F2': ['ISO 10993-1', 'ISO 10993-12', 'ISO 10993-17', 'ISO 10993-18'],
    'F3': ['ISO 10993-7', 'ISO 10993-9', 'ISO 10993-13', 'ISO 10993-14', 'ISO 10993-15', 'ISO 10993-16', 'ISO 11737-3'],
    'F4': ['ISO/TS 10993-19', 'ISO/TS 10993-20'],
    'F5': ['fda-knee'],
}
FAMILIES = list(FAMILY_ITEMS)
FAMILY_OF = {item: fam for fam, items in FAMILY_ITEMS.items() for item in items}


class AuditListError(ValueError):
    """The judge file or the allocation broke the audit-list rules."""


def load_cells(doc):
    """[(j_id, item, family, position)] from a judge-file document."""
    cells, seen = [], set()
    for entry in doc.get('entries') or []:
        j = entry['j_id']
        if j in seen:
            raise AuditListError(f'duplicate J-id {j}')
        seen.add(j)
        for pos, item in enumerate(entry['items']):
            if item not in FAMILY_OF:
                raise AuditListError(f'{j}: item {item!r} has no family')
            cells.append((j, item, FAMILY_OF[item], pos))
    if len({(c[0], c[1]) for c in cells}) != len(cells):
        raise AuditListError('a J-id lists the same item twice')
    return cells


def family_totals(counts, total=TOTAL):
    """Largest-remainder rounding of total * C_f / C; ties broken in family order."""
    c = sum(counts.get(f, 0) for f in FAMILIES)
    if c < total:
        raise AuditListError(f'only {c} cells, need {total}')
    quota = {f: total * counts.get(f, 0) / c for f in FAMILIES}
    out = {f: int(q) for f, q in quota.items()}
    rest = total - sum(out.values())
    for f in sorted(FAMILIES, key=lambda f: (-(quota[f] - out[f]), FAMILIES.index(f)))[:rest]:
        out[f] += 1
    for f in FAMILIES:
        if out[f] > counts.get(f, 0):
            raise AuditListError(f'family {f}: total {out[f]} exceeds its {counts.get(f, 0)} cells')
    return out


def row_quotas(jids, total=TOTAL, seed=SEED):
    jids = sorted(jids)
    base, extra = divmod(total, len(jids))
    plus = set(random.Random(f'{seed}|rows').sample(jids, extra))
    return {j: base + (j in plus) for j in jids}


def select(cells, rows, fams, seed=SEED, max_attempts=MAX_ATTEMPTS):
    """Returns (selected cells, attempt index)."""
    total = sum(rows.values())
    if total != sum(fams.values()):
        raise AuditListError('row quotas and family totals disagree')
    base = sorted(cells, key=lambda c: (c[0], c[3]))
    for attempt in range(max_attempts):
        pool = list(base)
        random.Random(f'{seed}|cells|{attempt}').shuffle(pool)
        rc, fc, chosen = Counter(), Counter(), []
        for cell in pool:
            j, _, fam, _ = cell
            if rc[j] < rows[j] and fc[fam] < fams[fam]:
                rc[j] += 1
                fc[fam] += 1
                chosen.append(cell)
        if len(chosen) == total:
            return chosen, attempt
    raise AuditListError(f'no allocation found in {max_attempts} attempts')


def ordered_rows(chosen, seed=SEED):
    rows = sorted(chosen, key=lambda c: (c[0], c[3]))
    random.Random(f'{seed}|order').shuffle(rows)
    return [dict(order=i, j_id=c[0], item=c[1], family=c[2]) for i, c in enumerate(rows, start=1)]


def build(doc, total=TOTAL, seed=SEED):
    cells = load_cells(doc)
    counts = Counter(c[2] for c in cells)
    fams = family_totals(counts, total)
    rows = row_quotas({c[0] for c in cells}, total, seed)
    per_j = Counter(c[0] for c in cells)
    if any(rows[j] > per_j[j] for j in rows):
        raise AuditListError('a J-id has fewer cells than its quota')
    chosen, attempt = select(cells, rows, fams, seed)
    out = ordered_rows(chosen, seed)
    sel_f = Counter(r['family'] for r in out)
    sel_j = Counter(r['j_id'] for r in out)
    stats = dict(cells_total=len(cells), family_cells={f: counts.get(f, 0) for f in FAMILIES},
                 family_totals=fams, family_selected={f: sel_f.get(f, 0) for f in FAMILIES},
                 per_jid_min=min(sel_j.values()), per_jid_max=max(sel_j.values()),
                 jids=len(sel_j), rows=len(out), attempt=attempt)
    return out, stats


def to_csv(rows) -> str:
    buf = io.StringIO(newline='')
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator='\n')
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def run(root=ROOT, out=print) -> int:
    root = Path(root)
    judge, dest = root / JUDGE_REL, root / OUT_REL
    if dest.exists():
        out(f'REFUSED (write-once): already exists: {OUT_REL}')
        return 3
    try:
        doc = yaml.safe_load(judge.read_text(encoding='utf-8'))
        rows, stats = build(doc)
    except (AuditListError, OSError, KeyError, TypeError, AttributeError) as exc:
        out(f'ERROR: {exc if isinstance(exc, AuditListError) else type(exc).__name__}; nothing written')
        return 2
    text = to_csv(rows)
    with dest.open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    out(f'cells_total={stats["cells_total"]} rows={stats["rows"]} jids={stats["jids"]} '
        f'per_jid min={stats["per_jid_min"]} max={stats["per_jid_max"]} attempt={stats["attempt"]}')
    out('family  cells  total  selected')
    for f in FAMILIES:
        out(f'{f:<6}  {stats["family_cells"][f]:>5}  {stats["family_totals"][f]:>5}  {stats["family_selected"][f]:>8}')
    out(f'{OUT_REL} sha256_lf={normalised_sha(text.encode("utf-8"))}')
    return 0


def main() -> int:
    return run()


if __name__ == '__main__':
    sys.exit(main())
