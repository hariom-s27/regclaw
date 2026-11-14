"""S10b-10: local, offline labelling tool for Hariom's Audit-A labels (stdlib + yaml, 127.0.0.1 only).

Modelled on retrieval_eval/s8_label_tool.py (unchanged).

  python -m eval.s10.label_tool --mode audit --upto 150      (then 250, then 350)

AUDIT mode reads judge_file_v1.yaml + audit_a_list.csv and shows the first --upto cells of the list,
one at a time, in list order: the J-id's profile fields (contact category, duration, DeviceProfile
input fields), the item, and the codebook section headings. It never shows the family, P-ids, the
J-id map, judge labels or engine output; it never opens jid_map.json or any judge/engine file.
It refuses to start unless the judge file and the audit list LF sha256 equal the ones in
eval/s10/manifests/s10b1_build.json.
Inputs: label (not_applicable / recommended / mandatory), confidence (low / medium / high), reason
(required, one line, <= 300 chars). Labelling is a gap-free prefix of the list order (earlier cells may
be re-labelled). Every save rewrites data/gold/s10/labels_hariom_auditA.csv atomically (UTF-8 BOM + CRLF,
as S8), so the tool resumes where it stopped.
"""
import argparse
import csv
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlparse
import webbrowser

import yaml

from standards.ruleset import normalised_sha

ROOT = Path(__file__).resolve().parents[2]
JUDGE_REL = 'data/gold/s10/judge_file_v1.yaml'
LIST_REL = 'data/gold/s10/audit_a_list.csv'
CODEBOOK_REL = 'eval/s10/codebook/CODEBOOK_v0.1.md'
LABELS_REL = 'data/gold/s10/labels_hariom_auditA.csv'
BUILD_MANIFEST_REL = 'eval/s10/manifests/s10b1_build.json'
UPTO_CHOICES = (150, 250, 350)
LABELS = ('not_applicable', 'recommended', 'mandatory')
CONFIDENCE = ('low', 'medium', 'high')
REASON_MAX = 300
FIELDS = ('order', 'j_id', 'item', 'label', 'confidence', 'reason', 'timestamp')
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


class LabelApp:
    def __init__(self, judge, audit_list, codebook, labels, upto, manifest, mode='audit'):
        if mode != 'audit':
            raise ValueError('mode must be audit')
        if int(upto) not in UPTO_CHOICES:
            raise ValueError(f'--upto must be one of {UPTO_CHOICES}')
        self.mode, self.upto, self.lock = mode, int(upto), threading.Lock()
        self.labels_path = Path(labels)
        judge_raw, list_raw = Path(judge).read_bytes(), Path(audit_list).read_bytes()
        check_shas(judge_raw, list_raw, manifest)
        entries = {e['j_id']: e for e in yaml.safe_load(judge_raw.decode('utf-8'))['entries']}
        rows = list(csv.DictReader(io.StringIO(list_raw.decode('utf-8-sig'), newline='')))
        if [int(r['order']) for r in rows] != list(range(1, len(rows) + 1)):
            raise LabelToolError('audit list order is not 1..N')
        if self.upto > len(rows):
            raise LabelToolError(f'--upto {self.upto} exceeds the {len(rows)} listed cells')
        for r in rows:
            if r['j_id'] not in entries or r['item'] not in entries[r['j_id']]['items']:
                raise LabelToolError(f'list row {r["order"]} is not a judge-file cell')
        self.cells = [dict(order=int(r['order']), j_id=r['j_id'], item=r['item']) for r in rows[:self.upto]]
        self.keys = [(c['j_id'], c['item']) for c in self.cells]
        self.profiles = {j: _scrub(dict(contact_category=e['contact_category'], duration=e['duration'],
                                        profile=e['profile'])) for j, e in entries.items()}
        self.headings = codebook_headings(Path(codebook).read_text(encoding='utf-8'))
        self.labels = {}
        if self.labels_path.exists():
            self._load_existing()

    def _load_existing(self):
        text = self.labels_path.read_text(encoding='utf-8-sig')
        rows = list(csv.DictReader(io.StringIO(text, newline='')))
        index = {k: i for i, k in enumerate(self.keys)}
        for r in rows:
            key = (r.get('j_id', ''), r.get('item', ''))
            if key not in index:
                raise LabelToolError('labels file holds a cell outside the first --upto cells of the list')
            if str(self.cells[index[key]]['order']) != r.get('order'):
                raise LabelToolError('labels file order does not match the audit list')
            if key in self.labels:
                raise LabelToolError('labels file holds a cell twice')
            self.labels[key] = dict(validate_label(r), timestamp=r.get('timestamp', ''))
        first = self._first_unlabelled()
        if first is not None and len(self.labels) > first:
            raise LabelToolError('labels file is not a gap-free prefix of the list order')

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
        return dict(index=index, cell=dict(position=index + 1, j_id=cell['j_id'], item=cell['item']),
                    profile=self.profiles[cell['j_id']], headings=self.headings,
                    label=self.labels.get(key), **self.state())

    def save(self, payload):
        try:
            index = int(payload.get('index'))
        except (TypeError, ValueError):
            raise LabelToolError('unknown cell') from None
        if not 0 <= index < len(self.keys) or (payload.get('j_id'), payload.get('item')) != self.keys[index]:
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
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator='\r\n')
        writer.writeheader()
        for cell, key in zip(self.cells, self.keys):
            if key in self.labels:
                writer.writerow(dict(order=cell['order'], j_id=key[0], item=key[1], **self.labels[key]))
        return '﻿' + stream.getvalue()

    def _write(self):
        self.labels_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.labels_path.with_name(self.labels_path.name + '.tmp')
        tmp.write_text(self.csv_text(), encoding='utf-8', newline='')
        os.replace(tmp, self.labels_path)


def make_handler(app):
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
            try:
                if url.path == '/':
                    self._send(200, PAGE, 'text/html; charset=utf-8')
                elif url.path == '/api/state':
                    self._send(200, json.dumps(app.state()))
                elif url.path == '/api/item':
                    self._send(200, json.dumps(app.item(parse_qs(url.query).get('i', ['0'])[0])))
                else:
                    self._send(404, json.dumps({'error': 'not found'}))
            except (ValueError, KeyError) as e:
                self._send(400, json.dumps({'error': str(e)}))

        def do_POST(self):
            if urlparse(self.path).path != '/api/save':
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
    print(f'S10 labelling tool ({app.mode}, upto {app.upto}) at {url}  labels -> {app.labels_path}  (Ctrl+C to stop)')
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
    parser.add_argument('--mode', choices=['audit'], default='audit')
    parser.add_argument('--upto', type=int, choices=UPTO_CHOICES, required=True)
    parser.add_argument('--judge', default=str(ROOT / JUDGE_REL))
    parser.add_argument('--list', default=str(ROOT / LIST_REL))
    parser.add_argument('--codebook', default=str(ROOT / CODEBOOK_REL))
    parser.add_argument('--labels', default=str(ROOT / LABELS_REL))
    parser.add_argument('--manifest', default=str(ROOT / BUILD_MANIFEST_REL))
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    app = LabelApp(args.judge, args.list, args.codebook, args.labels, args.upto, args.manifest, args.mode)
    serve(app, args.port, not args.no_browser)
    return 0


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
<div class="card"><div class="k">Codebook sections (open CODEBOOK_v0.1.md alongside)</div><ul id="heads"></ul></div>
</section></main>
<script>
const OPTS={label:['not_applicable','recommended','mandatory'],confidence:['low','medium','high']};
const KEYS={'1':['label','not_applicable'],'2':['label','recommended'],'3':['label','mandatory'],
 l:['confidence','low'],m:['confidence','medium'],h:['confidence','high']};
let cur=0,state={},cell=null;
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
 cur=d.index;state=d;cell=d.cell;render(d);}
function render(d){$('mode').textContent=d.mode.toUpperCase();
 $('prog').textContent=`cell ${d.index+1} / ${d.total} · labelled ${d.done} / ${d.upto}`;
 $('pos').textContent=d.cell.position+' of '+d.total;$('jid').textContent=d.cell.j_id;$('item').textContent=d.cell.item;
 $('cat').textContent=d.profile.contact_category;$('dur').textContent=d.profile.duration;
 const p=$('profile');p.textContent='';p.append(tree(d.profile.profile));
 const h=$('heads');h.textContent='';for(const t of d.headings){const li=document.createElement('li');li.textContent=t.replace(/^#+\s*/,'');h.append(li);}
 const L=d.label||{};setRadio('label',L.label||'');setRadio('confidence',L.confidence||'');$('reason').value=L.reason||'';
 msg(d.label?'saved earlier '+L.timestamp:'',!!d.label);}
async function save(){const p={index:cur,j_id:cell.j_id,item:cell.item,label:getRadio('label'),confidence:getRadio('confidence'),reason:$('reason').value.trim()};
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
