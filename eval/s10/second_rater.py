"""S10b A2 friend kit: seeded draw, blind sheets, training check and label freeze.

Run later from the repository root with ``python -m eval.s10.second_rater``:
  draw
  sheet
  coldread-sheet
  coldread-compare
  freeze --expected-draw-sha <sha printed by draw>

Generated files are UTF-8, LF and write-once. The rater fills the generated CSV
manually. Console output contains aggregates and LF shas, never cell content.
Exit codes: 0 success, 2 input error, 3 existing output.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import random
import re
import sys

import yaml

from standards.ruleset import normalised_sha

from . import draws as D
from .judge_kit import sheet_profile
from .label_tool import ReviewApp, check_codebook_v1
from .merge import JUDGES, LABELS, cell_id

ROOT = Path(__file__).resolve().parents[2]
SEED = 'S10b-A2'
DRAW_FORMAT = 's10-friend-set.v1'
DRAW_REL = 'data/gold/s10/draws/friend_set.json'
SHEET_REL = 'data/gold/s10/sheets/friend_sheet.md'
LABELS_REL = 'data/gold/s10/sheets/friend_labels.csv'
COLD_SHEET_REL = 'data/gold/s10/sheets/friend_coldread_sheet.md'
COLD_LABELS_REL = 'data/gold/s10/sheets/friend_coldread_labels.csv'
MANIFEST_REL = 'eval/s10/manifests/friend_labels_v1.json'
CODEBOOK_REL = 'eval/s10/codebook/CODEBOOK_v1.0.md'
CODEBOOK_MANIFEST_REL = 'eval/s10/manifests/codebook_v1.json'
FIELDS = ('sheet_id', 'label', 'unsure_guideline_gap', 'note')
AUDITED_PREFIX = 150
COLD_N = 20
TARGETS = {
    'audited_not_applicable': 20,
    'audited_recommended': 5,
    'audited_mandatory': 25,
    'unaudited_not_applicable': 40,
    'unaudited_recommended': None,
    'unaudited_mandatory': 50,
    'unaudited_split': None,
}
HIDDEN = frozenset({
    'id', 'j_id', 'p_id', 'cell_id', 'sheet_id', 'split', 'stratum', 'family',
    'comment', 'flags', 'derived', 'existing_evidence', 'label', 'labels',
    'reason', 'reasons', 'confidence', 'confidences', 'final', 'routed',
    'route_reasons', 'inclusion_probability', 'sampling_counts',
})


class FriendError(ValueError):
    """Only fixed check names and aggregate counts belong in these messages."""


def require_final_labels(rows):
    """Shared refusal for the draw and the later quality report (A2-02b)."""
    count = sum(row['final'] is None for row in rows)
    if count:
        raise FriendError(
            f'REFUSED: {count} routed cells without a final label; '
            "the friend draw needs every cell's final label")


def friend_draw(merged_rows, audit_rows):
    """Pure draw from merge rows and the ordered Audit-A list, not human labels."""
    rows = list(merged_rows)
    require_final_labels(rows)
    audit = sorted(audit_rows, key=lambda r: int(r['order']))
    if len(audit) < AUDITED_PREFIX or [int(r['order']) for r in audit] != list(range(1, len(audit) + 1)):
        raise FriendError('audit-list positions check failed')
    audited = [cell_id(r['j_id'], r['item']) for r in audit[:AUDITED_PREFIX]]
    if len(set(audited)) != AUDITED_PREFIX:
        raise FriendError('duplicate audited cell check failed')
    by_id = {}
    buckets = {name: [] for name in TARGETS}
    for row in rows:
        cid = row['cell_id']
        if (not isinstance(cid, str) or cid != cell_id(row['j_id'], row['item'])
                or cid in by_id or any(c in cid for c in '\r\n')
                or row['final'] not in LABELS
                or set(row['labels']) != set(JUDGES)
                or any(row['labels'][j] not in LABELS for j in JUDGES)):
            raise FriendError('merge-row check failed')
        by_id[cid] = row
        if cid in audited:
            name = f'audited_{row["final"]}'
        elif len(set(row['labels'].values())) > 1:
            name = 'unaudited_split'
        else:
            name = f'unaudited_{row["final"]}'
        buckets[name].append(cid)
    if not set(audited) <= set(by_id):
        raise FriendError('audit-list membership check failed')
    strata, selected = {}, {}
    seeds = {name: f'{SEED}|friend|{name}' for name in TARGETS}
    seeds['sheet_order'] = f'{SEED}|friend-order'
    for name, ids in buckets.items():
        population = len(ids)
        target = TARGETS[name]
        n = population if target is None else min(target, population)
        strata[name] = dict(population=population, n=n,
                            forced_count=population if name == 'unaudited_split' else 0)
        for cid in random.Random(seeds[name]).sample(sorted(ids), n):
            selected[cid] = dict(cell_id=cid, stratum=name, inclusion_probability=n / population)
    order = sorted(selected)
    random.Random(seeds['sheet_order']).shuffle(order)
    cells = [dict(sheet_id=f'S-{i:03d}', **selected[cid]) for i, cid in enumerate(order, 1)]
    return dict(format=DRAW_FORMAT, seed_strings=seeds, strata=strata, cells=cells)


def checked_draw(raw):
    """Validate the persisted draw without opening a label source."""
    doc = json.loads(raw.decode('utf-8'))
    seeds = {name: f'{SEED}|friend|{name}' for name in TARGETS}
    seeds['sheet_order'] = f'{SEED}|friend-order'
    if doc['format'] != DRAW_FORMAT or doc['seed_strings'] != seeds or set(doc['strata']) != set(TARGETS):
        raise FriendError('draw format check failed')
    cells = doc['cells']
    ids = [c['cell_id'] for c in cells]
    if len(ids) != len(set(ids)) or [c['sheet_id'] for c in cells] != [f'S-{i:03d}' for i in range(1, len(cells) + 1)]:
        raise FriendError('draw identifiers check failed')
    counts = Counter(c['stratum'] for c in cells)
    if set(counts) - set(TARGETS):
        raise FriendError('draw stratum check failed')
    for name, info in doc['strata'].items():
        population, n, forced = (info[k] for k in ('population', 'n', 'forced_count'))
        if any(type(v) is not int or v < 0 for v in (population, n, forced)):
            raise FriendError('draw counts check failed')
        target = TARGETS[name]
        want = population if target is None else min(target, population)
        if n != want or counts[name] != n or forced != (population if name == 'unaudited_split' else 0):
            raise FriendError('draw counts check failed')
    for c in cells:
        cid = c['cell_id']
        info = doc['strata'][c['stratum']]
        probability = c['inclusion_probability']
        if (not isinstance(cid, str) or '|' not in cid or any(x in cid for x in '\r\n')
                or not all(cid.split('|', 1)) or type(probability) not in (float, int)
                or not 0 < probability <= 1 or probability != info['n'] / info['population']):
            raise FriendError('draw probability or cell check failed')
    return doc


def _scrub(value):
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items() if key not in HIDDEN}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def render_sheet(cells, entries):
    """Profile and item only under anonymous headings; preserve draw order."""
    lines = ['# Friend labelling sheet', '',
             'Use only [frozen codebook v1.0](../../../../eval/s10/codebook/CODEBOOK_v1.0.md).',
             'Fill the matching sheet_id in the CSV. Mark unsure_guideline_gap yes when unsure; '
             'otherwise leave it blank or write no. Notes are optional.', '']
    for c in cells:
        jid, item = c['cell_id'].split('|', 1)
        entry = entries[jid]
        if 'items' in entry and item not in entry['items']:
            raise FriendError('sheet item membership check failed')
        profile = _scrub(dict(contact_category=entry['contact_category'], duration=entry['duration'],
                              profile=sheet_profile(entry['profile'])))
        text = yaml.safe_dump(profile, sort_keys=False, allow_unicode=True).rstrip('\n')
        fence = '`' * max(3, max((len(m.group()) + 1 for m in re.finditer(r'`+', text)), default=0))
        lines += [f'## {c["sheet_id"]}', '', fence + 'yaml', text, fence, '', item, '']
    return '\n'.join(lines) + '\n'


def render_labels(cells):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator='\n')
    writer.writeheader()
    for c in cells:
        writer.writerow(dict(sheet_id=c['sheet_id'], label='', unsure_guideline_gap='', note=''))
    return stream.getvalue()


def checked_friend_rows(raw, cells):
    """Require exactly one complete valid response for each anonymous sheet ID."""
    reader = csv.reader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
    if tuple(next(reader, [])) != FIELDS:
        raise FriendError('labels header check failed')
    records = list(reader)
    if len(records) != len(cells) or any(len(r) != len(FIELDS) for r in records):
        raise FriendError('labels row count or structure check failed')
    rows = [dict(zip(FIELDS, r)) for r in records]
    ids = [r['sheet_id'] for r in rows]
    if len(ids) != len(set(ids)) or set(ids) != {c['sheet_id'] for c in cells}:
        raise FriendError('labels identifiers check failed')
    for row in rows:
        if row['label'] not in LABELS or row['unsure_guideline_gap'] not in ('', 'yes', 'no'):
            raise FriendError('labels validation check failed')
    by_id = {r['sheet_id']: r for r in rows}
    return [by_id[c['sheet_id']] for c in cells]


def coldread_draw(practice_entries, items, hariom, judges):
    """Pure training selection from the four practice profiles; never probes.

    hariom: {(device_id, item): label}; judges: {judge: {(device_id, item): label}}.
    """
    if set(practice_entries) != {'PR-1', 'PR-2', 'PR-3', 'PR-4'} or len(items) != len(set(items)):
        raise FriendError('practice population check failed')
    reference = {}
    for device in sorted(practice_entries):
        for item in items:
            key = device, item
            labels = [hariom[key]] + [judges[j][key] for j in JUDGES]
            if any(label not in LABELS for label in labels):
                raise FriendError('practice labels check failed')
            if len(set(labels)) == 1:
                reference[cell_id(device, item)] = labels[0]
    picked = random.Random(f'{SEED}|coldread').sample(sorted(reference), min(COLD_N, len(reference)))
    cells = [dict(sheet_id=f'C-{i:03d}', cell_id=cid) for i, cid in enumerate(picked, 1)]
    return dict(cells=cells, population=len(reference), reference={cid: reference[cid] for cid in picked})


def _coldread(root):
    app = ReviewApp(root)
    human = {key: row['label'] for key, row in app.practice.labels.items()}
    judges = {j: {(d, item): c['label'] for d, entries in app.cells[j].items()
                  for item, c in entries.items()} for j in JUDGES}
    return coldread_draw(app.practice.profiles, app.practice.parts, human, judges), app.practice.profiles


def _codebook(root):
    check_codebook_v1((root / CODEBOOK_REL).read_bytes(), root / CODEBOOK_MANIFEST_REL)


def _friend_sheet(root, draw):
    entries = D.load_entries(root)
    by_id = {e['j_id']: e for e in entries}
    if len(by_id) != len(entries):
        raise FriendError('duplicate profile check failed')
    return render_sheet(draw['cells'], by_id)


def _json_text(doc):
    return json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + '\n'


def freeze_friend(root, *, expected_draw_sha, now=None):
    """Validate all sources and build the content-free manifest in memory."""
    root = Path(root)
    raw = (root / DRAW_REL).read_bytes()
    if normalised_sha(raw) != expected_draw_sha:
        raise FriendError('draw sha mismatch; nothing written')
    draw = checked_draw(raw)
    _codebook(root)
    sheet_raw = (root / SHEET_REL).read_bytes()
    if normalised_sha(sheet_raw) != normalised_sha(_friend_sheet(root, draw).encode('utf-8')):
        raise FriendError('sheet does not match draw and profiles; nothing written')
    label_raw = (root / LABELS_REL).read_bytes()
    rows = checked_friend_rows(label_raw, draw['cells'])
    return dict(format='s10-friend-labels-manifest.v1', file=LABELS_REL,
                sha256_lf=normalised_sha(label_raw), rows=len(rows), valid_rows=len(rows),
                friend_set_sha256_lf=normalised_sha(raw), sheet_sha256_lf=normalised_sha(sheet_raw),
                created_at=now or datetime.now(timezone.utc).isoformat())


OUTPUTS = {'draw': (DRAW_REL,), 'sheet': (SHEET_REL, LABELS_REL),
           'coldread-sheet': (COLD_SHEET_REL, COLD_LABELS_REL),
           'coldread-compare': (), 'freeze': (MANIFEST_REL,)}


def _exists(path):
    return path.exists() or path.is_symlink()


def main(argv=None, *, root=ROOT, now=None, out=print, err=None):
    err = err or (lambda message: print(message, file=sys.stderr))
    parser = argparse.ArgumentParser(prog='python -m eval.s10.second_rater', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in OUTPUTS:
        command = commands.add_parser(name)
        if name == 'freeze':
            command.add_argument('--expected-draw-sha', required=True)
    args = parser.parse_args(argv)
    root = Path(root)
    written = 0
    try:
        if any(_exists(root / rel) for rel in OUTPUTS[args.command]):
            err('REFUSED: output exists (write-once)')
            return 3
        payloads, lines = [], []
        if args.command == 'draw':
            merged = D.load_merge(root)
            require_final_labels(merged)
            doc = friend_draw(merged, D.load_audit_rows(root))
            payloads = [(DRAW_REL, _json_text(doc))]
            for name, info in doc['strata'].items():
                lines.append(f'{name} population={info["population"]} n={info["n"]} '
                             f'forced_count={info["forced_count"]} take_all={int(info["n"] == info["population"])}')
            lines.append(f'cells={len(doc["cells"])}')
        elif args.command == 'sheet':
            _codebook(root)
            doc = checked_draw((root / DRAW_REL).read_bytes())
            payloads = [(SHEET_REL, _friend_sheet(root, doc)), (LABELS_REL, render_labels(doc['cells']))]
            lines = [f'cells={len(doc["cells"])}']
        elif args.command in ('coldread-sheet', 'coldread-compare'):
            _codebook(root)
            doc, profiles = _coldread(root)
            sheet = render_sheet(doc['cells'], profiles)
            if args.command == 'coldread-sheet':
                payloads = [(COLD_SHEET_REL, sheet), (COLD_LABELS_REL, render_labels(doc['cells']))]
                lines = [f'eligible={doc["population"]} selected={len(doc["cells"])} '
                         f'take_all={int(doc["population"] < COLD_N)}']
            else:
                if normalised_sha((root / COLD_SHEET_REL).read_bytes()) != normalised_sha(sheet.encode('utf-8')):
                    raise FriendError('cold-read sheet check failed')
                rows = checked_friend_rows((root / COLD_LABELS_REL).read_bytes(), doc['cells'])
                counts = {label: dict(agree=0, disagree=0) for label in LABELS}
                for row, c in zip(rows, doc['cells']):
                    label = doc['reference'][c['cell_id']]
                    counts[label]['agree' if row['label'] == label else 'disagree'] += 1
                lines = [f'{label} agree={counts[label]["agree"]} disagree={counts[label]["disagree"]}'
                         for label in LABELS]
        else:
            if not re.fullmatch(r'[0-9a-f]{64}', args.expected_draw_sha):
                raise FriendError('expected draw sha format check failed')
            doc = freeze_friend(root, expected_draw_sha=args.expected_draw_sha, now=now)
            payloads = [(MANIFEST_REL, _json_text(doc))]
            lines = [f'rows={doc["rows"]} valid_rows={doc["valid_rows"]}']
        # Finish validation and rendering before creating any output of a pair.
        if any(_exists(root / rel) for rel, _ in payloads):
            err('REFUSED: output exists (write-once)')
            return 3
        for rel, text in payloads:
            text = text.replace('\r\n', '\n').replace('\r', '\n')
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(text)
            written += 1
            lines.append(f'{rel} sha256_lf={normalised_sha(text.encode("utf-8"))}')
        for line in lines:
            out(line)
        return 0
    except FileExistsError:
        err(f'REFUSED: output exists (write-once); files_written={written}')
        return 3
    except (OSError, ValueError, KeyError, TypeError, AttributeError, csv.Error, ZeroDivisionError) as exc:
        if isinstance(exc, FriendError) and str(exc).startswith('REFUSED:'):
            err(str(exc))
        else:
            err(f'ERROR: input or output check failed; files_written={written}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
