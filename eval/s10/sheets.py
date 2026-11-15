"""S10b2b (S10b2b-08/-09/-12 + Q9): blind labelling sheets for Hariom (Markdown to read + CSV to fill).

Profile view = the judges' view: contact_category, duration and judge_kit.sheet_profile(profile). Sheets never
show why a cell was chosen, judge labels, confidences or judge names. Rows are grouped by J-id (J-ids in order
of first appearance), items in list order within a J-id; the CSV order column follows the same order.

  audit-b-sheet   from draws/audit_b.json -> sheets/audit_b_sheet.md + audit_b_labels.csv
  retest-sheet    from draws/retest.json  -> sheets/retest_sheet.md + retest_labels.csv
  referee-pass1   referee set = routed merge cells + audit-B cells whose blind label differs from the unanimous
                  jury label. A cell that already has a blind label from audit A (else audit B) reuses it as its
                  pass-1 label (source audit_a / audit_b) and skips the pass-1 sheet; the rest (source sheet) go
                  to sheets/referee_pass1_sheet.md + referee_pass1_labels.csv. Writes sheets/referee_set.json.
                  Needs the audit-B draw and all audit-B labels.
  referee-pass2   needs every pass-1 sheet label; first writes sheets/referee_pass1_lock.json (pass 1 locked:
                  write-once sha of every pass-1 label and its source), then sheets/referee_pass2_sheet.md +
                  referee_pass2_labels.csv: per cell the 3 judge reasons cleaned by clean_reason (label words ->
                  [label], judge/model/vendor names -> [judge], S10P-24 anonymised) and shuffled with
                  Random("S10b-v1|referee-order|<cell_id>"); no tally, no confidence, no judge names; a missing or
                  empty reason shows NO_REASON. verify_pass1_lock() re-checks pass 1 against the lock later.
Every referee CSV has the column unsure_guideline_gap ("yes" when unsure -> guideline gap; label still required).
All outputs are LF and write-once. Prints counts and shas only.

Run from the repo root:  python -m eval.s10.sheets {audit-b-sheet|retest-sheet|referee-pass1|referee-pass2}
Exit codes: 0 written · 1 refused (pass 1 incomplete or changed) · 2 input error · 3 output exists.
"""
import argparse
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
from .judge_kit import device_key, sheet_profile
from .merge import JUDGES, LABELS, _load_inputs as load_judge_results, cell_id, collect_cells

ROOT = Path(__file__).resolve().parents[2]
SEED = 'S10b-v1'
FORMAT_SET = 's10-referee-set.v1'
FORMAT_LOCK = 's10-referee-pass1-lock.v1'
SHEETS_REL = 'data/gold/s10/sheets'
NO_REASON = '(no reason given)'
LABEL_FIELDS = ('order', 'j_id', 'item', 'label', 'confidence', 'reason', 'timestamp')       # = label_tool FIELDS
REFEREE_FIELDS = ('order', 'cell_id', 'j_id', 'item', 'label', 'unsure_guideline_gap', 'note', 'timestamp')
FILES = dict(
    audit_b=('audit_b_sheet.md', 'audit_b_labels.csv'),
    retest=('retest_sheet.md', 'retest_labels.csv'),
    pass1=('referee_pass1_sheet.md', 'referee_pass1_labels.csv'),
    pass2=('referee_pass2_sheet.md', 'referee_pass2_labels.csv'))
SET_NAME, LOCK_NAME = 'referee_set.json', 'referee_pass1_lock.json'

# ---- reason cleaning (S10b2b-12) -----------------------------------------------------------------------
_LABEL_RE = re.compile(r'(?i)\b(?:mandatory|recommended|not_applicable|not[ -]applicable|n/a)\b')
_NA_RE = re.compile(r'\bNA\b')                                  # upper-case only: "na" is left alone
JUDGE_NAMES = ('claude', 'opus', 'sonnet', 'haiku', 'anthropic', 'gpt', 'openai', 'codex', 'gemini', 'google',
               'antigravity', 'chatgpt', 'astra')
MODEL_WORDS = ('astra', 'flash', 'pro', 'mini')     # masked only when attached to a masked name + version
# S10P-24 anonymised: a name plus an optional directly following version (separator space / - / . / none, starting
# with a digit: 6, 4, 4o, 5.5, 3.8) plus an optional model word becomes ONE [judge]; or "judge a/b/c".
_NAME_RE = re.compile(r'(?i)\b(?:judge\s+[abc]|(?:' + '|'.join(JUDGE_NAMES) + r')'
                      r'(?:[ .\-]?\d+(?:\.\d+)*[a-z]*(?:[ \-](?:' + '|'.join(MODEL_WORDS) + r'))?)?)\b')
_ARROW_RE = re.compile(r'\s*(?:->|=>|→)\s*')
_SECTION_RE = re.compile(r'(?i)(?:\b(?:section|sec\.?)\s*§?|§)\s*(\d+(?:\.\d+)*)')
_QUOTES = str.maketrans({'“': '"', '”': '"', '„': '"', '″': '"',
                         '‘': "'", '’': "'", '‚': "'", '′': "'"})


def clean_reason(text):
    """Mask judge / model / vendor names (JUDGE_NAMES with an attached version and model word, and "judge a/b/c";
    whole word, any case) with one [judge] each, and
    label words with [label] (mandatory, recommended, not_applicable and its variants not applicable,
    not-applicable, N/A, NA), straighten quotes, arrows -> " → ", section refs -> §N.N, collapse whitespace."""
    s = str(text).translate(_QUOTES)
    s = _NAME_RE.sub('[judge]', s)
    s = _LABEL_RE.sub('[label]', s)
    s = _NA_RE.sub('[label]', s)
    s = _SECTION_RE.sub(lambda m: f'§{m.group(1)}', s)
    s = _ARROW_RE.sub(' → ', s)
    return re.sub(r'\s+', ' ', s).strip()


def shuffled_reasons(cid, reasons):
    """reasons in judge order A, B, C (None / blank = missing) -> cleaned and shuffled for one cell."""
    out = [clean_reason(r) if isinstance(r, str) and r.strip() else NO_REASON for r in reasons]
    random.Random(f'{SEED}|referee-order|{cid}').shuffle(out)
    return out


# ---- rendering -----------------------------------------------------------------------------------------
def grouped(cells):
    """cells [{cell_id, j_id, item, ...}] -> same cells grouped by J-id (first appearance), list order inside."""
    jids = list(dict.fromkeys(c['j_id'] for c in cells))
    return [c for j in jids for c in cells if c['j_id'] == j]


def profile_view(entry):
    return yaml.safe_dump(dict(contact_category=entry['contact_category'], duration=entry['duration'],
                               profile=sheet_profile(entry['profile'])),
                          sort_keys=False, allow_unicode=True, default_flow_style=False).strip('\n')


def render_md(title, intro, cells, entries, reasons=None):
    lines = [f'# {title}', '', intro, '']
    last = None
    for n, c in enumerate(cells, start=1):
        if c['j_id'] != last:
            last = c['j_id']
            lines += [f'## {last}', '', '```yaml', profile_view(entries[last]), '```', '']
        lines.append(f'{n}. {c["item"]}')
        if reasons is not None:
            lines += [f'   - Reason {k}: {r}' for k, r in enumerate(reasons[c['cell_id']], start=1)]
    return '\n'.join(lines) + '\n'


def render_csv(cells, fields):
    buf = io.StringIO(newline='')
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator='\n')
    w.writeheader()
    for n, c in enumerate(cells, start=1):
        row = {k: '' for k in fields}
        row.update(order=n, j_id=c['j_id'], item=c['item'])
        if 'cell_id' in fields:
            row['cell_id'] = c['cell_id']
        w.writerow(row)
    return buf.getvalue()


INTRO_BLIND = ('Label each item for its device from the profile shown (not_applicable / recommended / mandatory), '
               'with confidence and a one-line reason, in the CSV next to this file.')
INTRO_PASS1 = ('Referee pass 1: label each item for its device from the profile shown, in the CSV next to this '
               'file. Write yes in unsure_guideline_gap when you are unsure because the guideline has a gap.')
INTRO_PASS2 = ('Referee pass 2: three reasons are shown per item, in random order. Label each item in the CSV next '
               'to this file. Write yes in unsure_guideline_gap when you are unsure because the guideline has a gap.')


# ---- referee set (Q9) ----------------------------------------------------------------------------------
def referee_set(merged_rows, audit_a, audit_b_cells, audit_b, item_rank):
    """merged_rows: merge rows; audit_a / audit_b: {cell_id: label}; audit_b_cells: drawn audit-B cell ids;
    item_rank: {cell_id: (J-id key, item position)}. Returns [{cell_id, j_id, item, why, pass1_source,
    pass1_label}] in J-id / item order, and counts."""
    rows = {r['cell_id']: r for r in merged_rows}
    why = {r['cell_id']: 'routed' for r in merged_rows if r['routed']}
    for cid in audit_b_cells:
        row = rows[cid]
        if not row['routed'] and audit_b[cid] != row['final']:
            why[cid] = 'audit_b_disagreement'
    out = []
    for cid in sorted(why, key=lambda c: item_rank[c]):
        if cid in audit_a:
            src, lab = 'audit_a', audit_a[cid]
        elif cid in audit_b:
            src, lab = 'audit_b', audit_b[cid]
        else:
            src, lab = 'sheet', None
        out.append(dict(cell_id=cid, j_id=rows[cid]['j_id'], item=rows[cid]['item'], why=why[cid],
                        pass1_source=src, pass1_label=lab))
    counts = dict(cells=len(out), routed=sum(c['why'] == 'routed' for c in out),
                  audit_b_disagreement=sum(c['why'] == 'audit_b_disagreement' for c in out),
                  **{f'source_{s}': sum(c['pass1_source'] == s for c in out) for s in ('audit_a', 'audit_b', 'sheet')})
    return out, counts


def pass1_table(ref_cells, sheet_labels):
    """Every referee cell's pass-1 label: reused blind label or the pass-1 sheet label. Raises if incomplete."""
    table = []
    for c in ref_cells:
        lab = c['pass1_label'] if c['pass1_source'] != 'sheet' else sheet_labels.get(c['cell_id'])
        if lab not in LABELS:
            raise PermissionError('pass 1 is incomplete: every pass-1 sheet cell needs a valid label')
        table.append((c['cell_id'], c['pass1_source'], lab))
    return table


def pass1_sha(table):
    return normalised_sha(''.join(f'{c},{s},{l}\n' for c, s, l in table).encode('utf-8'))


def read_referee_csv(path):
    """{cell_id: label} for rows with a label (utf-8-sig)."""
    p = Path(path)
    if not p.is_file():
        return {}
    out = {}
    for r in csv.DictReader(io.StringIO(p.read_text(encoding='utf-8-sig'))):
        if (r.get('label') or '').strip():
            if r['label'] not in LABELS:
                raise D.DrawError(f'{p.name} holds an invalid label')
            out[r['cell_id']] = r['label']
    return out


def verify_pass1_lock(root):
    """Raise PermissionError unless the current pass-1 labels equal the locked ones. Returns the pass-1 table."""
    d = Path(root) / SHEETS_REL
    lock = json.loads((d / LOCK_NAME).read_text(encoding='utf-8'))
    ref = json.loads((d / SET_NAME).read_text(encoding='utf-8'))
    table = pass1_table(ref['cells'], read_referee_csv(d / FILES['pass1'][1]))
    if pass1_sha(table) != lock['pass1_table_sha256'] or normalised_sha((d / SET_NAME).read_bytes()) != lock['referee_set_sha256_lf']:
        raise PermissionError('pass-1 labels changed after the lock')
    return table


# ---- CLI -----------------------------------------------------------------------------------------------
def _entries(root):
    return {e['j_id']: e for e in D.load_entries(root)}


def _labels(path):
    return {cid: v['label'] for cid, v in D.load_label_csv(path).items()}


def _write(root, rel_name, text, written):
    dest = Path(root) / SHEETS_REL / rel_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    written.append((rel_name, normalised_sha(text.encode('utf-8'))))


OUTPUTS = {'audit-b-sheet': FILES['audit_b'], 'retest-sheet': FILES['retest'],
           'referee-pass1': FILES['pass1'] + (SET_NAME,), 'referee-pass2': FILES['pass2'] + (LOCK_NAME,)}


def _build(root, name, now, written, out):
    entries = _entries(root)
    if name in ('audit-b-sheet', 'retest-sheet'):
        key = 'audit-b' if name == 'audit-b-sheet' else 'retest'
        cells = grouped(D.load_draw(root, key)['cells'])
        md, csv_name = FILES['audit_b' if key == 'audit-b' else 'retest']
        title = 'Audit B labelling sheet' if key == 'audit-b' else 'Re-label sheet'
        _write(root, md, render_md(title, INTRO_BLIND, cells, entries), written)
        _write(root, csv_name, render_csv(cells, LABEL_FIELDS), written)
        out(f'cells={len(cells)} devices={len({c["j_id"] for c in cells})}')
        return 0
    d = Path(root) / SHEETS_REL
    if name == 'referee-pass1':
        merged = D.load_merge(root)
        audit_b_cells = [c['cell_id'] for c in D.load_draw(root, 'audit-b')['cells']]
        audit_b = _labels(d / FILES['audit_b'][1])
        if any(c not in audit_b for c in audit_b_cells):
            out('REFUSED: audit-B labels are incomplete; nothing written')
            return 1
        rank = {cell_id(j, it): (device_key(j), k) for j, e in entries.items() for k, it in enumerate(e['items'])}
        ref, counts = referee_set(merged, _labels(Path(root) / D.HUMAN_REL), audit_b_cells, audit_b, rank)
        doc = dict(format=FORMAT_SET, created_at=now or datetime.now(timezone.utc).isoformat(),
                   inputs={rel: normalised_sha((Path(root) / rel).read_bytes()) for rel in
                           (D.MERGE_REL, D.HUMAN_REL, f'{SHEETS_REL}/{FILES["audit_b"][1]}')},
                   counts=counts, cells=ref)
        sheet_cells = [c for c in ref if c['pass1_source'] == 'sheet']
        _write(root, SET_NAME, json.dumps(doc, indent=2, ensure_ascii=False) + '\n', written)
        _write(root, FILES['pass1'][0], render_md('Referee pass 1', INTRO_PASS1, sheet_cells, entries), written)
        _write(root, FILES['pass1'][1], render_csv(sheet_cells, REFEREE_FIELDS), written)
        out(' '.join(f'{k}={v}' for k, v in counts.items()))
        return 0
    ref_raw = (d / SET_NAME).read_bytes()
    ref = json.loads(ref_raw.decode('utf-8'))['cells']
    try:
        table = pass1_table(ref, read_referee_csv(d / FILES['pass1'][1]))
    except PermissionError as exc:
        out(f'REFUSED: {exc}; nothing written')
        return 1
    lock = dict(format=FORMAT_LOCK, created_at=now or datetime.now(timezone.utc).isoformat(),
                referee_set_sha256_lf=normalised_sha(ref_raw),
                pass1_labels_csv_sha256_lf=normalised_sha((d / FILES['pass1'][1]).read_bytes())
                if (d / FILES['pass1'][1]).is_file() else None,
                pass1_table_sha256=pass1_sha(table), cells=len(table),
                by_source={s: sum(t[1] == s for t in table) for s in ('audit_a', 'audit_b', 'sheet')})
    j_entries, results = load_judge_results(Path(root))
    judge_cells = {c['cell_id']: c for c in collect_cells(j_entries, results)}
    reasons = {}
    for c in ref:
        jc = judge_cells[c['cell_id']]['judges']
        reasons[c['cell_id']] = shuffled_reasons(c['cell_id'], [(jc[j] or {}).get('reason') for j in JUDGES])
    _write(root, LOCK_NAME, json.dumps(lock, indent=2) + '\n', written)
    _write(root, FILES['pass2'][0], render_md('Referee pass 2', INTRO_PASS2, ref, entries, reasons), written)
    _write(root, FILES['pass2'][1], render_csv(ref, REFEREE_FIELDS), written)
    out(f'pass 1 locked: cells={len(table)}; pass-2 cells={len(ref)}')
    return 0


def run(root=ROOT, name='audit-b-sheet', *, now=None, out=print) -> int:
    root = Path(root)
    existing = [n for n in OUTPUTS[name] if (root / SHEETS_REL / n).exists()]
    if existing:
        out(f'REFUSED (write-once): already exists: {", ".join(existing)}')
        return 3
    written = []
    try:
        code = _build(root, name, now, written, out)
    except (D.DrawError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        out(f'ERROR: {exc if isinstance(exc, D.DrawError) else type(exc).__name__}'
            + (f'; files already written: {len(written)}' if written else '; nothing written'))
        return 2
    for rel, sha in written:
        out(f'{SHEETS_REL}/{rel} sha256_lf={sha}')
    return code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='python -m eval.s10.sheets', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('sheet', choices=('audit-b-sheet', 'retest-sheet', 'referee-pass1', 'referee-pass2'))
    return run(ROOT, ap.parse_args(argv).sheet)


if __name__ == '__main__':
    sys.exit(main())
