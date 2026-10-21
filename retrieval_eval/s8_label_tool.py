"""S8 Part D: local, offline labelling tool for the human auditor / referee (stdlib only, 127.0.0.1).

  python tasks.py s8-label --mode audit   --sheet <sheet.csv> --labels <labels.csv>
  python tasks.py s8-label --mode audit   --sheet audit/audit_sheet.csv --labels audit/labels_shyam_audit.csv
                           --order audit/audit_order.txt
  python tasks.py s8-label --mode referee --sheet labels/referee_sheet.csv --disputes labels/disputes.csv
                           --labels labels/labels_referee.csv
  python tasks.py s8-label --sheet <sheet.csv> --labels <labels.csv> --export <out.csv>

AUDIT mode shows only blind sheet fields (never grades, ranks, systems or any LLM field).
With --order, items follow the fixed audit order and labelling is gap-free: nothing past the first
unlabelled item can be opened or saved (earlier items may be re-labelled until the freeze).
REFEREE mode also shows the judges' fields (A, B and C when present) from disputes.csv, blinded as Judge 1-3 in
an order shuffled per item (random.Random(f'{REFEREE_SEED}:{item_id}'); the A/B/C mapping stays server-side), and refuses to
start unless the audit is frozen (audit_freeze.json). It also records reason_code (required) and, for
audit disagreements, override_quote (must appear in the clause text) + override_rule (1-16)
(Addendum 2 notes 2-3). Every save rewrites the labels CSV atomically (resumable).
A frozen audit labels file is never opened for editing.
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

from retrieval_eval.s8_judging import AUDIT_N_MIN, frozen_labels_path, read_order
from retrieval_eval.s8_labels import (JUDGE_FIELDS, LABEL_FIELDS, REFEREE_FIELDS, read_disputes, read_labels,
                                      read_sheet, validate_label, validate_referee)
from retrieval_eval.s8_runs import output_dir

SHOWN_FIELDS = ('item_id', 'qid', 'query', 'jurisdiction', 'narrative_grade3', 'narrative_grade2',
                'narrative_grade1_traps', 'source_doc', 'clause_id', 'title', 'text')
TOOL_FIELDS = tuple(f for f in LABEL_FIELDS if f != 'query_asks') + ('timestamp',)
REFEREE_TOOL_FIELDS = TOOL_FIELDS[:-1] + REFEREE_FIELDS + ('timestamp',)
REFEREE_SEED = 20261021  # judge columns shuffled per item, stable across restarts (model identity hidden)


class LabelApp:
    def __init__(self, sheet, labels, mode='audit', disputes=None, freeze=None, order=None):
        if mode not in ('audit', 'referee'):
            raise ValueError('mode must be audit or referee')
        if order is not None and mode != 'audit':
            raise ValueError('--order is for AUDIT mode only')
        self.labels_path, self.mode, self.lock = Path(labels), mode, threading.Lock()
        self.fields = REFEREE_TOOL_FIELDS if mode == 'referee' else TOOL_FIELDS
        extra = REFEREE_FIELDS if mode == 'referee' else ()
        rows = read_sheet(sheet)
        if freeze is not None and frozen_labels_path(freeze) == self.labels_path.resolve():
            raise PermissionError('this labels file is the frozen audit; it cannot be edited')
        self.judges = {}
        if mode == 'referee':
            if freeze is None or not Path(freeze).exists():
                raise PermissionError('REFEREE mode needs the audit freeze (run s8-freeze-audit first)')
            if disputes is None:
                raise ValueError('REFEREE mode needs --disputes')
            self.judges = {d['item_id']: d for d in read_disputes(disputes)}
            if set(self.judges) != {r['item_id'] for r in rows}:
                raise ValueError('disputes.csv and the referee sheet hold different items')
        rules = next((r['common_rules'] for r in rows if r['common_rules']), '')
        self.ordered = order is not None
        if self.ordered:  # the fixed interleaved audit order; labelling must stay a gap-free prefix
            order_ids = read_order(order)
            if sorted(order_ids) != sorted(r['item_id'] for r in rows):
                raise ValueError('the order file is not a permutation of this sheet')
            by_id = {r['item_id']: r for r in rows}
            rows = [by_id[i] for i in order_ids]
        self.items = [{**{f: r[f] for f in SHOWN_FIELDS}, 'common_rules': rules} for r in rows]
        self.ids = [r['item_id'] for r in rows]
        self.texts = {r['item_id']: r['text'] for r in rows}
        self.labels = {}
        if self.labels_path.exists():
            self.labels = {k: {f: v[f] for f in self.fields} for k, v in
                           read_labels([self.labels_path], 'labels', extra).items()}
            unknown = set(self.labels) - set(self.ids)
            if unknown:
                raise ValueError(f'labels file holds {len(unknown)} item(s) not in this sheet')
            if self.ordered and self._first_unlabelled() is not None and \
                    len(self.labels) > self._first_unlabelled():
                raise ValueError('labels file is not a gap-free prefix of the order')

    def _first_unlabelled(self):
        return next((i for i, x in enumerate(self.ids) if x not in self.labels), None)

    def _check_reachable(self, index):
        """Ordered audit: nothing past the first unlabelled item can be opened or saved."""
        first = self._first_unlabelled()
        if self.ordered and first is not None and index > first:
            raise ValueError(f'gap-free order: label item {first + 1} first')

    def state(self):
        done = len(self.labels)
        result = dict(mode=self.mode, total=len(self.ids), done=done, first_unlabelled=self._first_unlabelled(),
                      labels_file=self.labels_path.name)
        if self.ordered:
            result.update(ordered=True, minimum=AUDIT_N_MIN)
        return result

    def judge_order(self, item_id):
        """Server-side only: the judge letters behind Judge 1, 2, 3 for this item (never sent to the browser).
        C is left out when it has no row on the item."""
        d = self.judges[item_id]
        letters = [j for j in ('a', 'b', 'c') if j != 'c' or any(d[f'c_{f}'] for f in JUDGE_FIELDS)]
        random.Random(f'{REFEREE_SEED}:{item_id}').shuffle(letters)
        return letters

    def item(self, index):
        index = max(0, min(int(index), len(self.ids) - 1))
        self._check_reachable(index)
        item_id = self.ids[index]
        result = dict(index=index, item=self.items[index], label=self.labels.get(item_id), **self.state())
        if self.mode == 'referee':
            d = self.judges[item_id]
            result['reason'] = d['reason']
            result['judges'] = {str(n): {f: d[f'{j}_{f}'] for f in JUDGE_FIELDS}
                                for n, j in enumerate(self.judge_order(item_id), start=1)}
        return result

    def save(self, payload):
        item_id = payload.get('item_id', '')
        if item_id not in self.ids:
            raise ValueError('unknown item_id')
        self._check_reachable(self.ids.index(item_id))
        extra = REFEREE_FIELDS if self.mode == 'referee' else ()
        label = validate_label({f: str(payload.get(f, '')) for f in (*LABEL_FIELDS, *extra)}, item_id, extra)
        if self.mode == 'referee':
            validate_referee(label, item_id, self.texts[item_id])
        label = {f: label[f] for f in self.fields if f != 'timestamp'}
        label['timestamp'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with self.lock:
            self.labels[item_id] = label
            self._write(self.labels_path)
        return self.state()

    def csv_text(self):
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=self.fields, lineterminator='\r\n')
        writer.writeheader()
        writer.writerows(self.labels[i] for i in self.ids if i in self.labels)
        return '﻿' + stream.getvalue()

    def _write(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text(self.csv_text(), encoding='utf-8', newline='')
        os.replace(tmp, path)

    def export(self, path):
        if Path(path).resolve() == self.labels_path.resolve():
            raise ValueError('export path must differ from the labels file')
        self._write(path)
        return dict(exported=len(self.labels), complete=len(self.labels) == len(self.ids))


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body, ctype='application/json; charset=utf-8', extra=None):
            data = body.encode('utf-8') if isinstance(body, str) else body
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            for k, v in (extra or {}).items():
                self.send_header(k, v)
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
                elif url.path == '/api/export':
                    self._send(200, app.csv_text(), 'text/csv; charset=utf-8',
                               {'Content-Disposition': f'attachment; filename="{app.labels_path.name}"'})
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
            except (ValueError, KeyError) as e:
                self._send(400, json.dumps({'error': str(e)}))
    return Handler


def serve(app, port=8765, open_browser=True):
    server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(app))
    url = f'http://127.0.0.1:{server.server_address[1]}/'
    print(f'labelling tool ({app.mode}) at {url}  labels -> {app.labels_path}  (Ctrl+C to stop)')
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
    out = output_dir()
    parser = argparse.ArgumentParser(prog='tasks.py s8-label', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['s8-label'])
    parser.add_argument('--mode', choices=['audit', 'referee'], default='audit')
    parser.add_argument('--sheet', required=True)
    parser.add_argument('--labels', required=True)
    parser.add_argument('--disputes')
    parser.add_argument('--order', help='audit_order.txt: fixed interleaved order, gap-free labelling (audit mode)')
    parser.add_argument('--freeze', default=str(out / 'audit' / 'audit_freeze.json'))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--export', help='write a validated copy of the labels and exit')
    args = parser.parse_args(argv)
    app = LabelApp(args.sheet, args.labels, args.mode, args.disputes, args.freeze, args.order)
    if args.export:
        print(json.dumps(app.export(args.export)))
        return 0
    serve(app, args.port, not args.no_browser)
    return 0


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>S8 Labelling</title>
<style>
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#66645e;--card:#fff;--line:#d9d7d0;--acc:#2f5d9e;--warn:#9e3b2f;--sel:#dfe8f5}
@media (prefers-color-scheme:dark){:root{--bg:#1b1b1a;--fg:#ecebe6;--muted:#a3a19a;--card:#252524;--line:#3b3a37;--acc:#8fb3e8;--warn:#e8988f;--sel:#2c3a4f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,Segoe UI,sans-serif}
header{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);padding:8px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;z-index:2}
header b{font-size:16px}#prog{color:var(--muted)}button{font:inherit;padding:4px 10px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;cursor:pointer}
button.primary{background:var(--acc);color:var(--bg);border-color:var(--acc)}main{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(300px,1fr);gap:16px;padding:16px}
@media (max-width:900px){main{grid-template-columns:1fr}}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:12px}
.k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}.q{font-size:18px;font-weight:600}
#text{white-space:pre-wrap;max-height:60vh;overflow:auto;border:1px solid var(--line);border-radius:6px;padding:10px;font-family:Georgia,serif;font-size:15px}
details summary{cursor:pointer;color:var(--muted)}.opts{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 10px}
.opts label{border:1px solid var(--line);border-radius:6px;padding:3px 9px;cursor:pointer}.opts input{display:none}.opts input:checked+span{font-weight:700}
.opts label:has(input:checked){background:var(--sel);border-color:var(--acc)}textarea{width:100%;font:inherit;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px}
#msg{color:var(--warn);min-height:1.2em}.saved{color:var(--acc)!important}kbd{border:1px solid var(--line);border-radius:3px;padding:0 4px;font-size:12px}
table.j{width:100%;border-collapse:collapse;font-size:13px}table.j td{border-top:1px solid var(--line);padding:4px;vertical-align:top}table.j td:first-child{color:var(--muted);width:30%}
</style></head><body>
<header><b>S8 labelling</b><span id="mode"></span><span id="prog"></span>
<button onclick="go(cur-1)">&larr; Prev</button><button onclick="go(cur+1)">Next &rarr;</button>
<button onclick="go(state.first_unlabelled??cur)">First unlabelled</button>
<a href="/api/export"><button>Export CSV</button></a></header>
<main><section>
<div class="card"><div class="k">Query <span id="qid"></span> &middot; jurisdiction: <span id="jur"></span></div><div class="q" id="query"></div>
<div class="k" style="margin-top:8px">Grade 3</div><div id="n3"></div><div class="k">Grade 2</div><div id="n2"></div>
<div class="k">Grade 1 / traps</div><div id="n1"></div>
<details><summary>Common rules</summary><div id="rules" style="white-space:pre-wrap"></div></details></div>
<div class="card"><div class="k">Source doc <span id="doc"></span> &middot; clause <span id="cid"></span></div>
<div style="font-weight:600;margin:4px 0" id="title"></div><div id="text"></div><div class="k" id="len"></div></div>
</section><section>
<div class="card" id="judges" hidden><div class="k">Referee: judges' labels (order shuffled per item) &middot; reason <span id="reason"></span></div><table class="j" id="jt"></table></div>
<div class="card">
<div class="k">Grade <kbd>0</kbd>-<kbd>3</kbd></div><div class="opts" id="grade"></div>
<div class="k">Clause states <kbd>a</kbd> <kbd>p</kbd> <kbd>m</kbd> <kbd>x</kbd></div><div class="opts" id="clause_states"></div>
<div class="k">Jurisdiction match <kbd>y</kbd> <kbd>n</kbd> <kbd>-</kbd></div><div class="opts" id="jurisdiction_match"></div>
<div class="k">Unsure <kbd>u</kbd></div><div class="opts" id="unsure"></div>
<div class="k">Evidence span (optional)</div><textarea id="evidence_span" rows="3"></textarea>
<div class="k">Note</div><textarea id="note" rows="2"></textarea>
<div id="refbox" hidden><div class="k">Reason code (required)</div><div class="opts" id="reason_code"></div>
<div class="k">Override quote (audit disagreement only: copied from the clause text)</div><textarea id="override_quote" rows="2"></textarea>
<div class="k">Override rule (guideline rule 1-16 the quote shows was broken)</div><input id="override_rule" size="4" inputmode="numeric">
<div class="k">The consensus stands unless both the quote and the rule are given (and unsure = no).</div></div>
<p><button class="primary" onclick="save()">Save &amp; next <kbd>Enter</kbd></button> <span id="msg"></span></p>
<div class="k">Keys work outside text boxes; <kbd>Esc</kbd> leaves a text box; <kbd>&larr;</kbd>/<kbd>&rarr;</kbd> navigate.</div>
</div></section></main>
<script>
const OPTS={grade:['0','1','2','3'],clause_states:['answers','partial','mentions_only','unrelated'],
 jurisdiction_match:['yes','no','not_applicable'],unsure:['no','yes']};
const REF={reason_code:['clear_reading','guideline_gap','unclear_text']};
for(const [f,vals] of Object.entries(REF)){const box=document.getElementById(f);
 for(const v of vals){const l=document.createElement('label');const i=document.createElement('input');i.type='radio';i.name=f;i.value=v;
  const s=document.createElement('span');s.textContent=v;l.append(i,s);box.append(l);}}
const KEYS={'0':['grade','0'],'1':['grade','1'],'2':['grade','2'],'3':['grade','3'],a:['clause_states','answers'],
 p:['clause_states','partial'],m:['clause_states','mentions_only'],x:['clause_states','unrelated'],
 y:['jurisdiction_match','yes'],n:['jurisdiction_match','no'],'-':['jurisdiction_match','not_applicable']};
let cur=0,state={},item=null;
for(const [f,vals] of Object.entries(OPTS)){const box=document.getElementById(f);
 for(const v of vals){const l=document.createElement('label');const i=document.createElement('input');i.type='radio';i.name=f;i.value=v;
  const s=document.createElement('span');s.textContent=v;l.append(i,s);box.append(l);}}
const $=id=>document.getElementById(id);
function setRadio(f,v){for(const i of document.getElementsByName(f))i.checked=(i.value===v);}
function getRadio(f){const i=[...document.getElementsByName(f)].find(x=>x.checked);return i?i.value:'';}
function msg(t,ok){$('msg').textContent=t;$('msg').className=ok?'saved':'';}
async function go(i){if(i<0||(state.total!==undefined&&i>=state.total))return;
 if(state.ordered&&state.first_unlabelled!=null&&i>state.first_unlabelled){msg('gap-free order: label item '+(state.first_unlabelled+1)+' first');return;}
 const r=await fetch('/api/item?i='+i);const d=await r.json();if(!r.ok){msg(d.error);return;}
 cur=d.index;state=d;item=d.item;render(d);}
function render(d){const it=d.item;$('mode').textContent=d.mode.toUpperCase();
 $('prog').textContent=d.ordered?`item ${d.index+1} / ${d.total} · labelled ${d.done} / ${d.total} (minimum ${d.minimum})`
  :`item ${d.index+1} / ${d.total} · labelled ${d.done}`;
 $('qid').textContent=it.qid;$('jur').textContent=it.jurisdiction||'(none named)';$('query').textContent=it.query;
 $('n3').textContent=it.narrative_grade3;$('n2').textContent=it.narrative_grade2;$('n1').textContent=it.narrative_grade1_traps;
 $('rules').textContent=it.common_rules;$('doc').textContent=it.source_doc;$('cid').textContent=it.clause_id;
 $('title').textContent=it.title;$('text').textContent=it.text;$('text').scrollTop=0;$('len').textContent=it.text.length+' characters';
 const L=d.label||{};for(const f of Object.keys(OPTS))setRadio(f,L[f]||'');
 if(!d.label){setRadio('unsure','no');if(!it.jurisdiction)setRadio('jurisdiction_match','not_applicable');}
 $('evidence_span').value=L.evidence_span||'';$('note').value=L.note||'';
 const ref=d.mode==='referee';$('refbox').hidden=!ref;
 if(ref){setRadio('reason_code',L.reason_code||'');$('override_quote').value=L.override_quote||'';$('override_rule').value=L.override_rule||'';}
 msg(d.label?'saved earlier '+L.timestamp:'',!!d.label);
 const J=$('judges');if(d.judges){J.hidden=false;$('reason').textContent=d.reason;const t=$('jt');t.textContent='';
  const js=Object.keys(d.judges);const h=t.insertRow();h.insertCell().textContent='';
  for(const j of js)h.insertCell().textContent='Judge '+j;
  for(const f of ['grade','clause_states','jurisdiction_match','unsure','query_asks','evidence_span','note']){
   const r=t.insertRow();r.insertCell().textContent=f;for(const j of js)r.insertCell().textContent=d.judges[j][f];}}
 else J.hidden=true;}
async function save(){const p={item_id:item.item_id,evidence_span:$('evidence_span').value,note:$('note').value};
 for(const f of Object.keys(OPTS))p[f]=getRadio(f);
 if(state.mode==='referee'){p.reason_code=getRadio('reason_code');p.override_quote=$('override_quote').value;p.override_rule=$('override_rule').value.trim();
  if(!p.reason_code){msg('missing: reason_code');return;}}
 const miss=['grade','clause_states','jurisdiction_match'].filter(f=>!p[f]);if(miss.length){msg('missing: '+miss.join(', '));return;}
 const r=await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
 const d=await r.json();if(!r.ok){msg(d.error);return;}
 Object.assign(state,d);
 if(d.done===d.total&&cur===d.total-1){await go(cur);msg('all items labelled — saved',true);return;}
 await go(cur+1<d.total?cur+1:(d.first_unlabelled??cur));}
document.addEventListener('keydown',e=>{const t=e.target.tagName;
 if(t==='TEXTAREA'||t==='INPUT'){if(e.key==='Escape')e.target.blur();return;}
 if(e.ctrlKey||e.metaKey||e.altKey)return;
 if(e.key==='Enter'){e.preventDefault();save();return;}
 if(e.key==='ArrowRight'){go(cur+1);return;}if(e.key==='ArrowLeft'){go(cur-1);return;}
 if(e.key==='u'){setRadio('unsure',getRadio('unsure')==='yes'?'no':'yes');return;}
 const k=KEYS[e.key];if(k)setRadio(k[0],k[1]);});
fetch('/api/state').then(r=>r.json()).then(s=>{state=s;go(s.first_unlabelled??0);});
</script></body></html>
"""


if __name__ == '__main__':
    raise SystemExit(main())
