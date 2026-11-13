"""S10b-02: compare Hariom's hand-copied FDA 2023 Attachment A grid with the frozen ruleset YAML.

Reads (read-only):
  data/gold/s10/e1_fda_grid_hariom.csv           columns nature, site, duration, 11 endpoint
                                                  columns, Repro, Degr, footnote_symbols_in_row
                                                  (X or O = marked, empty = not marked)
  standards/data/fda2023_attachment_a.v1.yaml    rows[*].cells[<col>].consider
Compares the 11 endpoint columns for all rows (24 x 11 = 264 cells). Repro/Degr marks and
footnote notes are recorded separately, as information only.

Writes eval/s10/manifests/e1_grid_check.json (write-once; refused if it exists), also on a
mismatch (verdict "mismatch"); nothing else is written. Prints counts, shas and the verdict.

Run from the repo root:  python -m eval.s10.e1_grid_check
Exit codes: 0 match · 1 mismatch · 2 input error · 3 manifest exists (write-once).
"""
import csv
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from standards.ruleset import normalised_sha

ROOT = Path(__file__).resolve().parents[2]
GRID_REL = 'data/gold/s10/e1_fda_grid_hariom.csv'
YAML_REL = 'standards/data/fda2023_attachment_a.v1.yaml'
MANIFEST_REL = 'eval/s10/manifests/e1_grid_check.json'
MANIFEST_FORMAT = 's10-e1-grid-check.v1'

KEY_COLS = ('nature', 'site', 'duration')
ENDPOINTS = ('Cyto', 'Sens', 'Irr', 'AST', 'MMP', 'SubA', 'Geno', 'Impl', 'Hemo', 'Chr', 'Carc')
INFO_COLS = ('Repro', 'Degr')
FOOTNOTE_COL = 'footnote_symbols_in_row'
HEADER = KEY_COLS + ENDPOINTS + INFO_COLS + (FOOTNOTE_COL,)
MARKS = {'X': True, 'O': True, '': False}


class GridInputError(ValueError):
    """The grid CSV or the YAML does not have the expected shape."""


def _key(row):
    return tuple(str(row[k]).strip() for k in KEY_COLS)


def load_grid(raw: bytes):
    """Parse the grid CSV bytes -> {key: row dict}. Raises GridInputError on bad shape."""
    text = raw.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text, newline=''))
    if tuple(h.strip() for h in (reader.fieldnames or ())) != HEADER:
        raise GridInputError(f'grid header differs from the expected {len(HEADER)} columns')
    grid = {}
    for n, row in enumerate(reader, start=2):
        row = {k.strip(): (v or '').strip() for k, v in row.items()}
        key = _key(row)
        if key in grid:
            raise GridInputError(f'duplicate grid row at line {n}: {"/".join(key)}')
        for col in ENDPOINTS + INFO_COLS:
            if row[col] not in MARKS:
                raise GridInputError(f'bad mark at line {n}, column {col} (allowed: X, O, empty)')
        grid[key] = row
    return grid


def load_ruleset(raw: bytes):
    """Parse the ruleset YAML bytes -> ({key: {col: consider}}, {key: {col: footnote}})."""
    doc = yaml.safe_load(raw.decode('utf-8'))
    rules, notes = {}, {}
    for row in doc.get('rows') or []:
        key = _key(row)
        if key in rules:
            raise GridInputError(f'duplicate YAML row: {"/".join(key)}')
        cells = row.get('cells') or {}
        missing = [c for c in ENDPOINTS if c not in cells]
        if missing:
            raise GridInputError(f'YAML row {"/".join(key)} lacks columns {missing}')
        rules[key] = {c: bool(cells[c]['consider']) for c in ENDPOINTS}
        notes[key] = {c: cells[c].get('footnote') for c in ENDPOINTS if cells[c].get('footnote')}
    return rules, notes


def compare(grid, rules, yaml_notes=None):
    """Compare the 11 endpoint columns. Row-set differences are input errors."""
    only_grid = sorted(set(grid) - set(rules))
    only_yaml = sorted(set(rules) - set(grid))
    if only_grid or only_yaml:
        raise GridInputError(f'row sets differ: {len(only_grid)} only in grid, {len(only_yaml)} only in YAML')
    mismatches, cells = [], 0
    for key in sorted(rules):
        for col in ENDPOINTS:
            cells += 1
            if MARKS[grid[key][col]] != rules[key][col]:
                mismatches.append(dict(zip(KEY_COLS, key), column=col))
    repro_degr = {}
    for col in INFO_COLS:
        marked = ['/'.join(k) for k in sorted(grid) if MARKS[grid[k][col]]]
        repro_degr[col] = dict(marked_count=len(marked), rows=marked)
    footnotes = []
    for key in sorted(grid):
        sym = grid[key][FOOTNOTE_COL]
        ynote = (yaml_notes or {}).get(key) or {}
        if sym or ynote:
            footnotes.append(dict(zip(KEY_COLS, key), grid_symbols=sym, yaml_cell_footnotes=ynote))
    return dict(rows=len(rules), cells_compared=cells, mismatches=mismatches,
                repro_degr=repro_degr, footnote_notes=footnotes,
                verdict='match' if not mismatches else 'mismatch')


def build_manifest(result, grid_sha, yaml_sha, now=None):
    return dict(format=MANIFEST_FORMAT, grid_file=GRID_REL, grid_sha256_lf=grid_sha,
                yaml_file=YAML_REL, yaml_sha256_lf=yaml_sha, rows=result['rows'],
                cells_compared=result['cells_compared'], endpoint_columns=list(ENDPOINTS),
                mismatches=result['mismatches'], repro_degr_info=result['repro_degr'],
                footnote_notes=result['footnote_notes'], verdict=result['verdict'],
                date=now or datetime.now(timezone.utc).isoformat())


def main(root=ROOT, now=None) -> int:
    root = Path(root)
    grid_path, yaml_path, manifest_path = root / GRID_REL, root / YAML_REL, root / MANIFEST_REL
    if manifest_path.exists():
        print(f'REFUSED: manifest already exists (write-once): {MANIFEST_REL}', file=sys.stderr)
        return 3
    try:
        grid_raw, yaml_raw = grid_path.read_bytes(), yaml_path.read_bytes()
        rules, notes = load_ruleset(yaml_raw)
        result = compare(load_grid(grid_raw), rules, notes)
    except (OSError, GridInputError, KeyError, TypeError) as exc:
        print(f'ERROR: {exc}; nothing written', file=sys.stderr)
        return 2
    manifest = build_manifest(result, normalised_sha(grid_raw), normalised_sha(yaml_raw), now)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(manifest, indent=2) + '\n')
    rd = result['repro_degr']
    print(f'rows={result["rows"]} cells_compared={result["cells_compared"]} '
          f'mismatches={len(result["mismatches"])} verdict={result["verdict"]}')
    for m in result['mismatches']:
        print(f'  mismatch: {m["nature"]}/{m["site"]}/{m["duration"]} column {m["column"]}')
    print(f'grid_sha256_lf={manifest["grid_sha256_lf"]}')
    print(f'yaml_sha256_lf={manifest["yaml_sha256_lf"]}')
    print(f'info: Repro marked={rd["Repro"]["marked_count"]} Degr marked={rd["Degr"]["marked_count"]} '
          f'footnote_note_rows={len(result["footnote_notes"])}')
    print(f'written {MANIFEST_REL}')
    return 0 if result['verdict'] == 'match' else 1


if __name__ == '__main__':
    sys.exit(main())
