"""S10b-10 / S10b2 (S7): local, offline labelling tool for Hariom (stdlib + yaml, 127.0.0.1 only).

Modelled on retrieval_eval/s8_label_tool.py (unchanged).

  python -m eval.s10.label_tool --mode practice              (PR-1..PR-4, 80 cells, codebook v0.1)
  python -m eval.s10.label_tool --mode review-practice       (read-only; Hariom's label beside judges A/B/C)
  python -m eval.s10.label_tool --mode audit --upto 150      (then 250, then 350; needs codebook_v1.json)

AUDIT mode reads judge_file_v1.yaml + audit_a_list.csv and shows the first --upto cells of the list, one at
a time. Inside each look (list rows 1-150, 151-250, 251-350) cells are grouped by J-id (J-ids in order of
first appearance), items in list order within a J-id; the look sets themselves do not change. It shows the
J-id's profile fields (contact category, duration, DeviceProfile input fields), the item, and the codebook
section headings. It never shows the family, P-ids, the J-id map, judge labels or engine output; it never
opens jid_map.json or any judge/engine file. It refuses to start unless the judge file and the audit list
LF sha256 equal the ones in eval/s10/manifests/s10b1_build.json AND eval/s10/manifests/codebook_v1.json
exists and records the LF sha of the codebook file in use (default CODEBOOK_v1.0.md).

PRACTICE mode shows the 80 cells of PR-1..PR-4 (practice_profiles.yaml), grouped by device in id order,
items in a seeded order ("S10b-v1|hariom-practice|<id>"), with the same profile view the judges get (no
flags, no derived fields, no existing evidence). Codebook v0.1; the practice file and codebook LF sha must
equal s10b1_build.json. Labels go to data/gold/s10/labels_hariom_practice.csv.

REVIEW-PRACTICE mode (read-only) starts only when all 80 practice labels exist AND judges A, B and C each
have a valid result for PR-1..PR-4; per cell it shows Hariom's label beside each judge's label, reason and
section. Practice data only.

Inputs: label (not_applicable / recommended / mandatory), confidence (low / medium / high), reason
(required, one line, <= 300 chars). Labelling is a gap-free prefix of the display order (earlier cells may
be re-labelled). Every save rewrites the labels CSV atomically (UTF-8 BOM + CRLF, rows in list order, as
S8), so the tool resumes where it stopped.
"""
import argparse
import csv
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import random
import threading
from urllib.parse import parse_qs, urlparse
import webbrowser

import yaml

from standards.ruleset import normalised_sha

from .judge_file import load_parts
from .judge_kit import device_key, sheet_profile
from .judge_output import validate

ROOT = Path(__file__).resolve().parents[2]
JUDGE_REL = 'data/gold/s10/judge_file_v1.yaml'
LIST_REL = 'data/gold/s10/audit_a_list.csv'
CODEBOOK_REL = 'eval/s10/codebook/CODEBOOK_v1.0.md'           # audit default (S7)
CODEBOOK_V01_REL = 'eval/s10/codebook/CODEBOOK_v0.1.md'        # practice
CODEBOOK_V1_MANIFEST_REL = 'eval/s10/manifests/codebook_v1.json'
LABELS_REL = 'data/gold/s10/labels_hariom_auditA.csv'
PRACTICE_REL = 'data/gold/s10/practice_profiles.yaml'
PRACTICE_LABELS_REL = 'data/gold/s10/labels_hariom_practice.csv'
SCHEMA_REL = 'eval/s10/codebook/judge_output.schema.json'
JUDGES_PRACTICE_REL = 'data/gold/s10/judges/practice'
BUILD_MANIFEST_REL = 'eval/s10/manifests/s10b1_build.json'
UPTO_CHOICES = (150, 250, 350)
LOOKS = ((1, 150), (151, 250), (251, 350))
PRACTICE_IDS = ('PR-1', 'PR-2', 'PR-3', 'PR-4')
PRACTICE_SEED = 'S10b-v1|hariom-practice|{}'
JUDGES = ('A', 'B', 'C')
LABELS = ('not_applicable', 'recommended', 'mandatory')
CONFIDENCE = ('low', 'medium', 'high')
REASON_MAX = 300
FIELDS = ('order', 'j_id', 'item', 'label', 'confidence', 'reason', 'timestamp')
PRACTICE_FIELDS = ('order', 'device_id', 'item', 'label', 'confidence', 'reason', 'timestamp')
HIDDEN_KEYS = frozenset({'id', 'split', 'stratum', 'comment', 'flags', 'derived', 'existing_evidence',
                         'family', 'p_id'})


class LabelToolError(ValueError):
    """An input, a saved label or the labels file broke the tool's rules."""


def _scrub(obj):
    """Drop hidden keys at every depth (defensive; the judge file is already blinded)."""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items() if k not in HIDDEN_KEYS}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def codebook_headings(text):
    return [line.strip() for line in text.splitlines() if line.startswith('# ') or line.startswith('## ')]


def check_shas(judge_raw, list_raw, manifest_path):
    try:
        files = json.loads(Path(manifest_path).read_text(encoding='utf-8'))['files']
        want = (files[JUDGE_REL]['sha256_lf'], files[LIST_REL]['sha256_lf'])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PermissionError(f'build manifest unreadable or incomplete: {manifest_path}') from exc
    if normalised_sha(judge_raw) != want[0]:
        raise PermissionError('judge file sha differs from s10b1_build.json; refusing to start')
    if normalised_sha(list_raw) != want[1]:
        raise PermissionError('audit list sha differs from s10b1_build.json; refusing to start')


def check_codebook_v1(codebook_raw, codebook_manifest_path):
    """Audit mode: codebook_v1.json must exist and record the LF sha of the codebook file in use."""
    path = Path(codebook_manifest_path)
    if not path.is_file():
        raise PermissionError(f'audit refused: {CODEBOOK_V1_MANIFEST_REL} does not exist (freeze the codebook first)')
    try:
        want = json.loads(path.read_text(encoding='utf-8'))['files'][CODEBOOK_REL]['sha256_lf']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PermissionError(f'audit refused: {CODEBOOK_V1_MANIFEST_REL} has no sha for {CODEBOOK_REL}') from exc
    if normalised_sha(codebook_raw) != want:
        raise PermissionError('audit refused: codebook sha differs from codebook_v1.json')


def build_manifest_sha(manifest_path, rel):
    try:
        return json.loads(Path(manifest_path).read_text(encoding='utf-8'))['files'][rel]['sha256_lf']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PermissionError(f'build manifest has no sha for {rel}') from exc


def look_order(rows):
    """Display order: inside each look, group by J-id (first appearance), list order within a J-id."""
    out = []
    for lo, hi in LOOKS:
        look = [r for r in rows if lo <= r['order'] <= hi]
        for j in dict.fromkeys(r['j_id'] for r in look):
            out += [r for r in look if r['j_id'] == j]
    return out


def practice_item_order(parts, device_id):
    out = list(parts)
    random.Random(PRACTICE_SEED.format(device_id)).shuffle(out)
    return out


def validate_label(payload):
    label, conf = str(payload.get('label', '')), str(payload.get('confidence', ''))
    reason = str(payload.get('reason', '')).strip()
    if label not in LABELS:
        raise LabelToolError(f'label must be one of {", ".join(LABELS)}')
    if conf not in CONFIDENCE:
        raise LabelToolError(f'confidence must be one of {", ".join(CONFIDENCE)}')
    if not reason:
        raise LabelToolError('reason is required')
    if '\n' in reason or '\r' in reason:
        raise LabelToolError('reason must be one line')
    if len(reason) > REASON_MAX:
        raise LabelToolError(f'reason is longer than {REASON_MAX} characters')
    return dict(label=label, confidence=conf, reason=reason)


class _Labeller:
    """Shared save / resume / gap-free / CSV logic. Subclasses set cells (display order), profiles, headings."""
    id_field = 'j_id'
    fields = FIELDS
    outside_msg = 'labels file holds a cell outside the first --upto cells of the list'
    order_msg = 'labels file order does not match the audit list'

    def _init_labels(self, labels):
        self.lock = threading.Lock()
        self.labels_path = Path(labels)
        self.keys = [(c[self.id_field], c['item']) for c in self.cells]
        self.labels = {}
        if self.labels_path.exists():
            self._load_existing()

    def _load_existing(self):
        text = self.labels_path.read_text(encoding='utf-8-sig')
        rows = list(csv.DictReader(io.StringIO(text, newline='')))
        index = {k: i for i, k in enumerate(self.keys)}
        for r in rows:
            key = (r.get(self.id_field, ''), r.get('item', ''))
            if key not in index:
                raise LabelToolError(self.outside_msg)
            if str(self.cells[index[key]]['order']) != r.get('order'):
                raise LabelToolError(self.order_msg)
            if key in self.labels:
                raise LabelToolError('labels file holds a cell twice')
            self.labels[key] = dict(validate_label(r), timestamp=r.get('timestamp', ''))
        first = self._first_unlabelled()
        if first is not None and len(self.labels) > first:
            raise LabelToolError('labels file is not a gap-free prefix of the display order')

    def _first_unlabelled(self):
        return next((i for i, k in enumerate(self.keys) if k not in self.labels), None)

    def _check_reachable(self, index):
        first = self._first_unlabelled()
        if first is not None and index > first:
            raise LabelToolError(f'gap-free order: label cell {first + 1} first')

    def state(self):
        return dict(mode=self.mode, total=len(self.keys), upto=self.upto, done=len(self.labels),
                    first_unlabelled=self._first_unlabelled(), labels_file=self.labels_path.name)

    def item(self, index):
        index = max(0, min(int(index), len(self.keys) - 1))
        self._check_reachable(index)
        cell = self.cells[index]
        key = self.keys[index]
        return dict(index=index, cell={'position': index + 1, self.id_field: key[0], 'item': key[1]},
                    profile=self.profiles[key[0]], headings=self.headings, id_field=self.id_field,
                    codebook_name=self.codebook_name, label=self.labels.get(key), **self.state())

    def save(self, payload):
        try:
            index = int(payload.get('index'))
        except (TypeError, ValueError):
            raise LabelToolError('unknown cell') from None
        if not 0 <= index < len(self.keys) or (payload.get(self.id_field), payload.get('item')) != self.keys[index]:
            raise LabelToolError('unknown cell')
        self._check_reachable(index)
        label = validate_label(payload)
        label['timestamp'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with self.lock:
            self.labels[self.keys[index]] = label
            self._write()
        return self.state()

    def csv_text(self):
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=self.fields, lineterminator='\r\n')
        writer.writeheader()
        for cell, key in sorted(zip(self.cells, self.keys), key=lambda ck: ck[0]['order']):
            if key in self.labels:
                writer.writerow({'order': cell['order'], self.id_field: key[0], 'item': key[1], **self.labels[key]})
        return '﻿' + stream.getvalue()

    def _write(self):
        self.labels_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.labels_path.with_name(self.labels_path.name + '.tmp')
        tmp.write_text(self.csv_text(), encoding='utf-8', newline='')
        os.replace(tmp, self.labels_path)


class LabelApp(_Labeller):
    """Audit mode (Audit-A labels)."""

    def __init__(self, judge, audit_list, codebook, labels, upto, manifest, mode='audit', codebook_manifest=None):
        if mode != 'audit':
            raise ValueError('mode must be audit')
        if int(upto) not in UPTO_CHOICES:
            raise ValueError(f'--upto must be one of {UPTO_CHOICES}')
        self.mode, self.upto = mode, int(upto)
        judge_raw, list_raw = Path(judge).read_bytes(), Path(audit_list).read_bytes()
        check_shas(judge_raw, list_raw, manifest)
        codebook_raw = Path(codebook).read_bytes()
        check_codebook_v1(codebook_raw, codebook_manifest or ROOT / CODEBOOK_V1_MANIFEST_REL)
        entries = {e['j_id']: e for e in yaml.safe_load(judge_raw.decode('utf-8'))['entries']}
        rows = list(csv.DictReader(io.StringIO(list_raw.decode('utf-8-sig'), newline='')))
        if [int(r['order']) for r in rows] != list(range(1, len(rows) + 1)):
            raise LabelToolError('audit list order is not 1..N')
        if self.upto > len(rows):
            raise LabelToolError(f'--upto {self.upto} exceeds the {len(rows)} listed cells')
        for r in rows:
            if r['j_id'] not in entries or r['item'] not in entries[r['j_id']]['items']:
                raise LabelToolError(f'list row {r["order"]} is not a judge-file cell')
        self.cells = look_order([dict(order=int(r['order']), j_id=r['j_id'], item=r['item'])
                                 for r in rows[:self.upto]])
        self.profiles = {j: _scrub(dict(contact_category=e['contact_category'], duration=e['duration'],
                                        profile=e['profile'])) for j, e in entries.items()}
        self.headings = codebook_headings(codebook_raw.decode('utf-8-sig'))
        self.codebook_name = Path(codebook).name
        self._init_labels(labels)


class PracticeApp(_Labeller):
    """Practice mode: PR-1..PR-4 x 20 parts = 80 cells, codebook v0.1."""
    id_field = 'device_id'
    fields = PRACTICE_FIELDS
    outside_msg = 'labels file holds a cell outside the practice cells'
    order_msg = 'labels file order does not match the practice order'

    def __init__(self, root=ROOT, labels=None):
        root = Path(root)
        self.mode, self.upto, self.root = 'practice', None, root
        manifest = root / BUILD_MANIFEST_REL
        practice_raw = (root / PRACTICE_REL).read_bytes()
        if normalised_sha(practice_raw) != build_manifest_sha(manifest, PRACTICE_REL):
            raise PermissionError('practice_profiles.yaml sha differs from s10b1_build.json; refusing to start')
        codebook_raw = (root / CODEBOOK_V01_REL).read_bytes()
        if normalised_sha(codebook_raw) != build_manifest_sha(manifest, CODEBOOK_V01_REL):
            raise PermissionError('codebook v0.1 sha differs from s10b1_build.json; refusing to start')
        entries = {e['id']: e for e in yaml.safe_load(practice_raw.decode('utf-8-sig'))['profiles']}
        if sorted(entries, key=device_key) != list(PRACTICE_IDS):
            raise LabelToolError('practice file must hold exactly PR-1..PR-4')
        self.parts = load_parts(root)
        self.cells, order = [], 0
        for d in PRACTICE_IDS:
            for it in practice_item_order(self.parts, d):
                order += 1
                self.cells.append(dict(order=order, device_id=d, item=it))
        self.profiles = {d: _scrub(dict(contact_category=e['contact_category'], duration=e['duration'],
                                        profile=sheet_profile(e['profile']))) for d, e in entries.items()}
        self.headings = codebook_headings(codebook_raw.decode('utf-8-sig'))
        self.codebook_name = Path(CODEBOOK_V01_REL).name
        self._init_labels(labels or root / PRACTICE_LABELS_REL)


def judge_results(root, device_ids, parts):
    """{judge: {device_id: result}} for results that exist AND pass the validator (practice only)."""
    root = Path(root)
    schema = json.loads((root / SCHEMA_REL).read_text(encoding='utf-8'))
    out = {j: {} for j in JUDGES}
    for j in JUDGES:
        for d in device_ids:
            p = root / JUDGES_PRACTICE_REL / j / f'{d}.result.json'
            if not p.is_file():
                continue
            try:
                res = json.loads(p.read_text(encoding='utf-8'))
            except ValueError:
                continue
            if validate(res, dict(id=d, items=list(parts)), schema)[0]:
                out[j][d] = res
    return out


class ReviewApp:
    """Review-practice mode (read-only): Hariom's practice label beside judges A/B/C (label, reason, section)."""

    def __init__(self, root=ROOT, labels=None):
        self.mode = 'review-practice'
        self.practice = PracticeApp(root, labels)
        results = judge_results(root, PRACTICE_IDS, self.practice.parts)
        done = len(self.practice.labels)
        valid = {j: len(results[j]) for j in JUDGES}
        if done != len(self.practice.keys) or any(v != len(PRACTICE_IDS) for v in valid.values()):
            raise PermissionError(f'review refused: labels {done}/{len(self.practice.keys)}; valid results '
                                  + ' '.join(f'{j} {valid[j]}/{len(PRACTICE_IDS)}' for j in JUDGES))
        self.cells = {j: {d: {c['item']: c for c in results[j][d]['cells']} for d in PRACTICE_IDS} for j in JUDGES}
        self.labels_path = self.practice.labels_path

    def state(self):
        return dict(mode=self.mode, total=len(self.practice.keys), labels_file=self.labels_path.name)

    def review(self, index):
        index = max(0, min(int(index), len(self.practice.keys) - 1))
        d, it = self.practice.keys[index]
        judges = {j: {k: self.cells[j][d][it][k] for k in ('label', 'reason', 'section')} for j in JUDGES}
        return dict(index=index, cell=dict(position=index + 1, device_id=d, item=it),
                    profile=self.practice.profiles[d], hariom=self.practice.labels[(d, it)], judges=judges,
                    **self.state())


def make_handler(app):
    review = isinstance(app, ReviewApp)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body, ctype='application/json; charset=utf-8'):
            data = body.encode('utf-8') if isinstance(body, str) else body
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            i = parse_qs(url.query).get('i', ['0'])[0]
            try:
                if url.path == '/':
                    self._send(200, REVIEW_PAGE if review else PAGE, 'text/html; charset=utf-8')
                elif url.path == '/api/state':
                    self._send(200, json.dumps(app.state()))
                elif url.path == '/api/item' and not review:
                    self._send(200, json.dumps(app.item(i)))
                elif url.path == '/api/review' and review:
                    self._send(200, json.dumps(app.review(i)))
                else:
                    self._send(404, json.dumps({'error': 'not found'}))
            except (ValueError, KeyError) as e:
                self._send(400, json.dumps({'error': str(e)}))

        def do_POST(self):
            if review or urlparse(self.path).path != '/api/save':
                return self._send(404, json.dumps({'error': 'not found'}))
            try:
                payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
                self._send(200, json.dumps(app.save(payload)))
            except (ValueError, KeyError, AttributeError) as e:
                self._send(400, json.dumps({'error': str(e)}))
    return Handler


def serve(app, port=8766, open_browser=True):
    server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(app))
    url = f'http://127.0.0.1:{server.server_address[1]}/'
    upto = getattr(app, 'upto', None)
    print(f'S10 labelling tool ({app.mode}{f", upto {upto}" if upto else ""}) at {url}  '
          f'labels -> {app.labels_path}  (Ctrl+C to stop)')
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    print(json.dumps(app.state()))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m eval.s10.label_tool', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', choices=['audit', 'practice', 'review-practice'], default='audit')
    parser.add_argument('--upto', type=int, choices=UPTO_CHOICES, help='audit mode only (required there)')
    parser.add_argument('--judge', default=str(ROOT / JUDGE_REL))
    parser.add_argument('--list', default=str(ROOT / LIST_REL))
    parser.add_argument('--codebook', default=str(ROOT / CODEBOOK_REL), help='audit mode only')
    parser.add_argument('--codebook-manifest', default=str(ROOT / CODEBOOK_V1_MANIFEST_REL), help='audit mode only')
    parser.add_argument('--labels', help='default: labels_hariom_auditA.csv (audit) / labels_hariom_practice.csv')
    parser.add_argument('--manifest', default=str(ROOT / BUILD_MANIFEST_REL))
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    if args.mode == 'audit':
        if args.upto is None:
            parser.error('--upto is required in audit mode')
        app = LabelApp(args.judge, args.list, args.codebook, args.labels or str(ROOT / LABELS_REL), args.upto,
                       args.manifest, args.mode, codebook_manifest=args.codebook_manifest)
    elif args.upto is not None:
        parser.error('--upto is for audit mode only')
    elif args.mode == 'practice':
        app = PracticeApp(ROOT, args.labels)
    else:
        app = ReviewApp(ROOT, args.labels)
    serve(app, args.port, not args.no_browser)
    return 0


REVIEW_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>S10 Practice Review</title>
<style>
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#66645e;--card:#fff;--line:#d9d7d0;--acc:#2f5d9e;--warn:#9e3b2f}
@media (prefers-color-scheme:dark){:root{--bg:#1b1b1a;--fg:#ecebe6;--muted:#a3a19a;--card:#252524;--line:#3b3a37;--acc:#8fb3e8;--warn:#e8988f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,Segoe UI,sans-serif}
header{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);padding:8px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
button{font:inherit;padding:4px 10px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;cursor:pointer}
main{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.4fr);gap:16px;padding:16px}@media (max-width:900px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:12px}
.k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}.big{font-size:20px;font-weight:600}
.lab{font-weight:700}.diff{color:var(--warn)}ul.tree{margin:4px 0;padding-left:18px}.key{color:var(--muted)}
</style></head><body>
<header><b>S10 practice review (read-only, practice data only)</b><span id="prog"></span>
<button onclick="go(cur-1)">&larr; Prev</button><button onclick="go(cur+1)">Next &rarr;</button></header>
<main><section>
<div class="card"><div class="k">Cell <span id="pos"></span></div><div class="big"><span id="dev"></span> &times; <span id="item"></span></div>
<div class="k" style="margin-top:6px">Contact category <span id="cat"></span> &middot; duration <span id="dur"></span></div></div>
<div class="card"><div class="k">Device profile (input fields)</div><div id="profile"></div></div>
</section><section><div id="labels"></div></section></main>
<script>
let cur=0,total=0;const $=id=>document.getElementById(id);
function tree(v){if(Array.isArray(v)){const u=document.createElement('ul');u.className='tree';
  v.forEach((x,i)=>{const li=document.createElement('li');const k=document.createElement('span');k.className='key';k.textContent='['+(i+1)+'] ';li.append(k,tree(x));u.append(li);});return u;}
 if(v&&typeof v==='object'){const u=document.createElement('ul');u.className='tree';
  for(const [k,x] of Object.entries(v)){const li=document.createElement('li');const s=document.createElement('span');s.className='key';s.textContent=k+': ';li.append(s,tree(x));u.append(li);}return u;}
 const s=document.createElement('span');s.textContent=String(v);return s;}
function card(title,label,lines,diff){const c=document.createElement('div');c.className='card';
 const k=document.createElement('div');k.className='k';k.textContent=title;const l=document.createElement('div');l.className='lab'+(diff?' diff':'');l.textContent=label;
 c.append(k,l);for(const t of lines){const p=document.createElement('div');p.textContent=t;c.append(p);}return c;}
async function go(i){if(i<0||(total&&i>=total))return;const r=await fetch('/api/review?i='+i);const d=await r.json();if(!r.ok)return;
 cur=d.index;total=d.total;$('prog').textContent=`cell ${d.index+1} / ${d.total}`;$('pos').textContent=d.cell.position+' of '+d.total;
 $('dev').textContent=d.cell.device_id;$('item').textContent=d.cell.item;$('cat').textContent=d.profile.contact_category;$('dur').textContent=d.profile.duration;
 const p=$('profile');p.textContent='';p.append(tree(d.profile.profile));const L=$('labels');L.textContent='';
 L.append(card('Hariom',d.hariom.label+' ('+d.hariom.confidence+')',['reason: '+d.hariom.reason],false));
 for(const [j,v] of Object.entries(d.judges))L.append(card('Judge '+j,v.label,['section: '+v.section,'reason: '+v.reason],v.label!==d.hariom.label));}
document.addEventListener('keydown',e=>{if(e.key==='ArrowRight')go(cur+1);if(e.key==='ArrowLeft')go(cur-1);});
go(0);
</script></body></html>
"""


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>S10 Labelling</title>
<style>
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#66645e;--card:#fff;--line:#d9d7d0;--acc:#2f5d9e;--warn:#9e3b2f;--sel:#dfe8f5}
@media (prefers-color-scheme:dark){:root{--bg:#1b1b1a;--fg:#ecebe6;--muted:#a3a19a;--card:#252524;--line:#3b3a37;--acc:#8fb3e8;--warn:#e8988f;--sel:#2c3a4f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,Segoe UI,sans-serif}
header{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);padding:8px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;z-index:2}
header b{font-size:16px}#prog{color:var(--muted)}button{font:inherit;padding:4px 10px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;cursor:pointer}
button.primary{background:var(--acc);color:var(--bg);border-color:var(--acc)}main{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(300px,1fr);gap:16px;padding:16px}
@media (max-width:900px){main{grid-template-columns:1fr}}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:12px}
.k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}.big{font-size:20px;font-weight:600}
ul.tree{margin:4px 0;padding-left:18px}ul.tree li{margin:1px 0}.key{color:var(--muted)}
.opts{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 10px}.opts label{border:1px solid var(--line);border-radius:6px;padding:3px 9px;cursor:pointer}
.opts input{display:none}.opts label:has(input:checked){background:var(--sel);border-color:var(--acc);font-weight:700}
input[type=text]{width:100%;font:inherit;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px}
#msg{color:var(--warn);min-height:1.2em}.saved{color:var(--acc)!important}kbd{border:1px solid var(--line);border-radius:3px;padding:0 4px;font-size:12px}
#heads{margin:0;padding-left:16px;font-size:13px}
</style></head><body>
<header><b>S10 labelling</b><span id="mode"></span><span id="prog"></span>
<button onclick="go(cur-1)">&larr; Prev</button><button onclick="go(cur+1)">Next &rarr;</button>
<button onclick="go(state.first_unlabelled??cur)">First unlabelled</button></header>
<main><section>
<div class="card"><div class="k">Cell <span id="pos"></span></div>
<div class="big"><span id="jid"></span> &times; <span id="item"></span></div>
<div class="k" style="margin-top:6px">Contact category <span id="cat"></span> &middot; duration <span id="dur"></span></div></div>
<div class="card"><div class="k">Device profile (input fields)</div><div id="profile"></div></div>
</section><section>
<div class="card">
<div class="k">Label <kbd>1</kbd> <kbd>2</kbd> <kbd>3</kbd></div><div class="opts" id="label"></div>
<div class="k">Confidence <kbd>l</kbd> <kbd>m</kbd> <kbd>h</kbd></div><div class="opts" id="confidence"></div>
<div class="k">Reason (one line, required, max 300)</div><input type="text" id="reason" maxlength="300">
<p><button class="primary" onclick="save()">Save &amp; next <kbd>Enter</kbd></button> <span id="msg"></span></p>
<div class="k">Keys work outside the text box; <kbd>Esc</kbd> leaves it; <kbd>&larr;</kbd>/<kbd>&rarr;</kbd> navigate.</div></div>
<div class="card"><div class="k">Codebook sections (open <span id="cbname"></span> alongside)</div><ul id="heads"></ul></div>
</section></main>
<script>
const OPTS={label:['not_applicable','recommended','mandatory'],confidence:['low','medium','high']};
const KEYS={'1':['label','not_applicable'],'2':['label','recommended'],'3':['label','mandatory'],
 l:['confidence','low'],m:['confidence','medium'],h:['confidence','high']};
let cur=0,state={},cell=null,idf='j_id';
const $=id=>document.getElementById(id);
for(const [f,vals] of Object.entries(OPTS)){const box=$(f);
 for(const v of vals){const l=document.createElement('label');const i=document.createElement('input');i.type='radio';i.name=f;i.value=v;
  const s=document.createElement('span');s.textContent=v;l.append(i,s);box.append(l);}}
function setRadio(f,v){for(const i of document.getElementsByName(f))i.checked=(i.value===v);}
function getRadio(f){const i=[...document.getElementsByName(f)].find(x=>x.checked);return i?i.value:'';}
function msg(t,ok){$('msg').textContent=t;$('msg').className=ok?'saved':'';}
function tree(v){if(Array.isArray(v)){const u=document.createElement('ul');u.className='tree';
  v.forEach((x,i)=>{const li=document.createElement('li');const k=document.createElement('span');k.className='key';k.textContent='['+(i+1)+'] ';
   li.append(k);const c=tree(x);li.append(c);u.append(li);});if(!v.length){const li=document.createElement('li');li.textContent='(none)';u.append(li);}return u;}
 if(v&&typeof v==='object'){const u=document.createElement('ul');u.className='tree';
  for(const [k,x] of Object.entries(v)){const li=document.createElement('li');const s=document.createElement('span');s.className='key';s.textContent=k+': ';
   li.append(s,tree(x));u.append(li);}return u;}
 const s=document.createElement('span');s.textContent=String(v);return s;}
async function go(i){if(i<0||(state.total!==undefined&&i>=state.total))return;
 if(state.first_unlabelled!=null&&i>state.first_unlabelled){msg('gap-free order: label cell '+(state.first_unlabelled+1)+' first');return;}
 const r=await fetch('/api/item?i='+i);const d=await r.json();if(!r.ok){msg(d.error);return;}
 cur=d.index;state=d;cell=d.cell;idf=d.id_field;render(d);}
function render(d){$('mode').textContent=d.mode.toUpperCase();
 $('prog').textContent=`cell ${d.index+1} / ${d.total} · labelled ${d.done} / ${d.total}`;
 $('pos').textContent=d.cell.position+' of '+d.total;$('jid').textContent=d.cell[d.id_field];$('cbname').textContent=d.codebook_name;$('item').textContent=d.cell.item;
 $('cat').textContent=d.profile.contact_category;$('dur').textContent=d.profile.duration;
 const p=$('profile');p.textContent='';p.append(tree(d.profile.profile));
 const h=$('heads');h.textContent='';for(const t of d.headings){const li=document.createElement('li');li.textContent=t.replace(/^#+\s*/,'');h.append(li);}
 const L=d.label||{};setRadio('label',L.label||'');setRadio('confidence',L.confidence||'');$('reason').value=L.reason||'';
 msg(d.label?'saved earlier '+L.timestamp:'',!!d.label);}
async function save(){const p={index:cur,[idf]:cell[idf],item:cell.item,label:getRadio('label'),confidence:getRadio('confidence'),reason:$('reason').value.trim()};
 const miss=['label','confidence','reason'].filter(f=>!p[f]);if(miss.length){msg('missing: '+miss.join(', '));return;}
 const r=await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
 const d=await r.json();if(!r.ok){msg(d.error);return;}
 Object.assign(state,d);
 if(d.done===d.total&&cur===d.total-1){await go(cur);msg('all '+d.total+' cells labelled — saved',true);return;}
 await go(cur+1<d.total?cur+1:(d.first_unlabelled??cur));}
document.addEventListener('keydown',e=>{const t=e.target.tagName;
 if(t==='INPUT'){if(e.key==='Escape')e.target.blur();else if(e.key==='Enter'){e.preventDefault();save();}return;}
 if(e.ctrlKey||e.metaKey||e.altKey)return;
 if(e.key==='Enter'){e.preventDefault();save();return;}
 if(e.key==='ArrowRight'){go(cur+1);return;}if(e.key==='ArrowLeft'){go(cur-1);return;}
 const k=KEYS[e.key];if(k)setRadio(k[0],k[1]);});
fetch('/api/state').then(r=>r.json()).then(s=>{state=s;go(s.first_unlabelled??0);});
</script></body></html>
"""


if __name__ == '__main__':
    raise SystemExit(main())
