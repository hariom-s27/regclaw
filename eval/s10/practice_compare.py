"""S10b2 (S8): practice comparison table. Practice data only, never an eval number.

  python -m eval.s10.practice_compare

Refuses until the lock opens (S10b2a-07, as Hariom defined it): Hariom's 80 practice labels (PR-1..PR-4) are
complete AND judges A, B and C each have a valid result for all 10 practice devices (PR-1..PR-10).
Writes data/gold/s10/practice/practice_table_NN.md with the next free number (never overwrites):
  - per device x item: A/B/C label + section, Hariom's label (PR-1..PR-4), STRADDLE where the labels in the
    row sit on both sides of the mandatory line (at least one mandatory and one not);
  - for every cell that is not unanimous across A/B/C or is a STRADDLE: each judge's reason (and Hariom's);
  - per device: every checklist field where A, B and C give different values, with each judge's value.
Console output: counts only (cells, straddles per family F1-F4, A=B=C share over all cells, all-four
agreement over Hariom's cells) and the file name. Exit codes: 0 written, 1 refused (lock), 2 input error.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

from standards.ruleset import normalised_sha

from .audit_list import FAMILY_ITEMS
from .judge_kit import KITS_REL, device_key
from .label_tool import JUDGES, PRACTICE_IDS, PracticeApp, judge_results

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR_REL = 'data/gold/s10/practice'
OUT_RE = re.compile(r'^practice_table_(\d{2,})\.md$')
FAMILIES = ('F1', 'F2', 'F3', 'F4')


class CompareError(ValueError):
    pass


def next_table_path(out_dir: Path) -> Path:
    nums = [int(m.group(1)) for p in out_dir.glob('practice_table_*.md') if (m := OUT_RE.match(p.name))] \
        if out_dir.exists() else []
    return out_dir / f'practice_table_{max(nums, default=0) + 1:02d}.md'


def straddle(labels):
    return 'mandatory' in labels and any(l != 'mandatory' for l in labels)


def _cell(text):
    return str(text).replace('|', '\\|').replace('\n', ' ')


def _components(value):
    return '; '.join(f'{c.get("name")}: {c.get("contact_category")} / {c.get("duration")} / {c.get("table_row")}'
                     for c in value) or '(none)'


def _norm(field, value):
    if field == 'material_classes':
        return sorted(value)
    return value


def _show(field, value):
    if field == 'components':
        return _components(value)
    if field == 'material_classes':
        return ', '.join(value) or '(none)'
    return str(value)


def load(root):
    """Returns (devices, items in family order, results {judge: {device: result}}, practice app, kit sha)."""
    root = Path(root)
    kit_path = root / KITS_REL / 'practice' / 'kit_manifest.json'
    if not kit_path.is_file():
        raise CompareError(f'practice kit manifest missing: {KITS_REL}/practice/kit_manifest.json')
    kit_raw = kit_path.read_bytes()
    kit = json.loads(kit_raw.decode('utf-8'))
    devices = sorted({d for f in kit['files'] for d in f['device_ids']}, key=device_key)
    practice = PracticeApp(root)
    items = [i for f in FAMILIES for i in FAMILY_ITEMS[f]]
    if sorted(items) != sorted(practice.parts):
        raise CompareError('family items F1-F4 differ from the 20 parts')
    if not set(PRACTICE_IDS) <= set(devices):
        raise CompareError('practice kit does not hold PR-1..PR-4')
    return devices, items, judge_results(root, devices, practice.parts), practice, normalised_sha(kit_raw)


def lock_counts(devices, results, practice):
    done, total = len(practice.labels), len(practice.keys)
    valid = {j: len(results[j]) for j in JUDGES}
    is_open = done == total and all(v == len(devices) for v in valid.values())
    return is_open, done, total, valid


def build_table(devices, items, results, practice, kit_sha, now):
    family_of = {i: f for f in FAMILIES for i in FAMILY_ITEMS[f]}
    counts = dict(cells=0, hariom_cells=0, unanimous=0, all_four=0,
                  straddle={f: 0 for f in FAMILIES}, family_cells={f: 0 for f in FAMILIES})
    lines = ['# S10b2a practice comparison', '',
             '**Practice data only; never an eval number.**', '',
             f'- date: {now}', f'- practice kit_manifest.json sha256_lf: {kit_sha}',
             f'- devices: {len(devices)}; items per device: {len(items)}; Hariom labels: PR-1..PR-4', '']
    for d in devices:
        cells = {j: {c['item']: c for c in results[j][d]['cells']} for j in JUDGES}
        lines += [f'## {d}', '', '| item | family | A | B | C | Hariom | STRADDLE |', '|---|---|---|---|---|---|---|']
        notes = []
        for it in items:
            jl = [cells[j][it]['label'] for j in JUDGES]
            h = practice.labels.get((d, it))
            row_labels = jl + ([h['label']] if h else [])
            s = straddle(row_labels)
            unanimous = len(set(jl)) == 1
            counts['cells'] += 1
            counts['family_cells'][family_of[it]] += 1
            counts['unanimous'] += unanimous
            counts['straddle'][family_of[it]] += s
            if h:
                counts['hariom_cells'] += 1
                counts['all_four'] += len(set(row_labels)) == 1
            lines.append('| ' + ' | '.join([_cell(it), family_of[it]]
                                           + [_cell(f'{cells[j][it]["label"]} ({cells[j][it]["section"]})') for j in JUDGES]
                                           + [_cell(h['label']) if h else '–', 'STRADDLE' if s else '']) + ' |')
            if s or not unanimous:
                notes.append(f'- **{_cell(it)}**' + (' (STRADDLE)' if s else '') + (' (not unanimous)' if not unanimous else ''))
                notes += [f'  - {j}: {_cell(cells[j][it]["reason"])}' for j in JUDGES]
                if h:
                    notes.append(f'  - Hariom: {_cell(h["reason"])}')
        lines.append('')
        lines += [f'### {d}: reasons where A/B/C are not unanimous or a STRADDLE is marked', '']
        lines += notes or ['(none)']
        lines.append('')
        checks = {j: results[j][d]['checklist'] for j in JUDGES}
        diff = [k for k in checks['A'] if len({json.dumps(_norm(k, checks[j].get(k)), sort_keys=True)
                                                for j in JUDGES}) > 1]
        lines += [f'### {d}: checklist fields where A, B and C differ', '']
        if diff:
            for k in diff:
                lines.append(f'- **{k}**')
                lines += [f'  - {j}: {_cell(_show(k, checks[j].get(k)))}' for j in JUDGES]
        else:
            lines.append('(none)')
        lines.append('')
    return '\n'.join(lines) + '\n', counts


def run(root=ROOT, now=None, out=print) -> int:
    root = Path(root)
    try:
        devices, items, results, practice, kit_sha = load(root)
    except (CompareError, PermissionError, OSError, ValueError, KeyError) as exc:
        out(f'INPUT ERROR: {exc}' if isinstance(exc, (CompareError, PermissionError)) else
            f'INPUT ERROR: {type(exc).__name__}')
        return 2
    is_open, done, total, valid = lock_counts(devices, results, practice)
    if not is_open:
        out(f'REFUSED (lock S10b2a-07 not open): Hariom labels {done}/{total}; valid results '
            + ' '.join(f'{j} {valid[j]}/{len(devices)}' for j in JUDGES))
        return 1
    now = now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    text, c = build_table(devices, items, results, practice, kit_sha, now)
    out_dir = root / OUT_DIR_REL
    out_dir.mkdir(parents=True, exist_ok=True)
    path = next_table_path(out_dir)
    with path.open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    out(f'cells={c["cells"]} hariom_cells={c["hariom_cells"]}')
    out('straddles: ' + ' '.join(f'{f} {c["straddle"][f]}/{c["family_cells"][f]}' for f in FAMILIES))
    out(f'unanimous A=B=C: {c["unanimous"]}/{c["cells"]} ({c["unanimous"] / c["cells"]:.3f})')
    out(f'all-four agreement (A, B, C, Hariom): {c["all_four"]}/{c["hariom_cells"]} '
        f'({c["all_four"] / c["hariom_cells"]:.3f})')
    out(f'wrote {path.relative_to(root).as_posix()} sha256_lf={normalised_sha(text.encode("utf-8"))}')
    return 0


def main(argv=None) -> int:
    argparse.ArgumentParser(prog='python -m eval.s10.practice_compare', description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    return run()


if __name__ == '__main__':
    sys.exit(main())
