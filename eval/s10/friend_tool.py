"""Blind friend labelling over a loopback-only, offline HTTP interface.

Run from the repository root with ``python -m eval.s10.friend_tool serve
--sheet coldread`` (or ``--sheet main``). Existing sheets and CSV templates
must already have been created by second_rater. No manifests are written.
"""
import argparse
import csv
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser

from . import second_rater as SR


ROOT = SR.ROOT
FIELDS = SR.FIELDS
LABELS = SR.LABELS
REFUSAL = 'REFUSED: input check failed; nothing written'


def _sheet_sections(rendered: str, cells: list[dict]) -> list[str]:
    """Use the authoritative renderer's anonymous sections verbatim."""
    markers = [f'\n## {cell["sheet_id"]}\n\n' for cell in cells]
    positions = []
    for marker in markers:
        if rendered.count(marker) != 1:
            raise SR.FriendError('sheet sections check failed')
        positions.append(rendered.index(marker))
    if positions != sorted(positions):
        raise SR.FriendError('sheet sections check failed')
    return [rendered[start + len(marker):end].rstrip('\n')
            for start, end, marker in zip(
                positions, positions[1:] + [len(rendered)], markers)]


def _clean_note(value: str) -> str:
    if not isinstance(value, str):
        raise SR.FriendError('note check failed')
    return value.replace('\r', '').replace('\n', '')[:200]


def _csv_bytes(rows: list[dict]) -> bytes:
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def _load_answers(raw: bytes, cells: list[dict]) -> list[dict]:
    """Delegate CSV validation, allowing blanks only in the label column.

    A validation-only copy substitutes a valid label for blank answers. The
    substituted labels never become saved answers or browser state.
    """
    records = list(csv.reader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True))
    if not records or tuple(records[0]) != FIELDS:
        raise SR.FriendError('labels header check failed')
    blank_ids = set()
    validation = io.StringIO(newline='')
    writer = csv.writer(validation, lineterminator='\n')
    writer.writerow(records[0])
    for record in records[1:]:
        checked = list(record)
        if len(checked) == len(FIELDS) and checked[1] == '':
            blank_ids.add(checked[0])
            checked[1] = LABELS[0]
        writer.writerow(checked)
    rows = SR.checked_friend_rows(validation.getvalue().encode('utf-8'), cells)
    for row in rows:
        if row['sheet_id'] in blank_ids:
            row['label'] = ''
        row['unsure_guideline_gap'] = 'yes' if row['unsure_guideline_gap'] == 'yes' else ''
        row['note'] = _clean_note(row['note'])
    return rows


def _atomic_write(path: Path, raw: bytes) -> None:
    """Five attempts, each with a private temporary file beside the target.

    Permission failures back off before retrying or returning the final
    failure. Replacement is the only operation that changes the target.
    """
    for delay in (0.05, 0.1, 0.2, 0.4, 0.8):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent,
                                             prefix=f'.{path.name}.', suffix='.tmp',
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
            os.replace(temporary, path)
            return
        except PermissionError:
            time.sleep(delay)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    # A locked temporary file must not mask the save result.
                    pass
    raise PermissionError('not saved')


class FriendApp:
    def __init__(self, root: Path, sheet: str, *, out=print):
        self.root = Path(root)
        self.sheet = sheet
        self.out = out
        if sheet not in ('coldread', 'main'):
            raise SR.FriendError('sheet check failed')
        self._check_unfrozen()
        labels_rel = SR.LABELS_REL if sheet == 'main' else SR.COLD_LABELS_REL
        sheet_rel = SR.SHEET_REL if sheet == 'main' else SR.COLD_SHEET_REL
        self.labels_path = self.root / labels_rel
        # A missing target must never be replaced by a newly created template.
        label_raw = self.labels_path.read_bytes()
        self.codebook = (self.root / SR.CODEBOOK_REL).read_bytes()
        SR.check_codebook_v1(self.codebook, self.root / SR.CODEBOOK_MANIFEST_REL)
        if sheet == 'main':
            draw = SR.checked_draw((self.root / SR.DRAW_REL).read_bytes())
            rendered = SR._friend_sheet(self.root, draw)
        else:
            draw, entries = SR._coldread(self.root)
            rendered = SR.render_sheet(draw['cells'], entries)
        if SR.normalised_sha((self.root / sheet_rel).read_bytes()) != SR.normalised_sha(rendered.encode('utf-8')):
            raise SR.FriendError('sheet check failed')
        self.sections = _sheet_sections(rendered, draw['cells'])
        # Retain no internal identifiers, reference answers or sampling metadata.
        self.cells = [dict(sheet_id=cell['sheet_id']) for cell in draw['cells']]
        self.rows = _load_answers(label_raw, self.cells)
        self.token = secrets.token_urlsafe()

    def _check_unfrozen(self):
        if self.sheet == 'main' and SR._exists(self.root / SR.MANIFEST_REL):
            raise SR.FriendError('frozen labels check failed')

    def state(self) -> dict:
        return dict(total=len(self.rows), answered=sum(bool(row['label']) for row in self.rows),
                    first_unanswered=next((i for i, row in enumerate(self.rows) if not row['label']), None))

    def cell(self, index: int) -> dict:
        if type(index) is not int or not 0 <= index < len(self.rows):
            raise SR.FriendError('cell check failed')
        return dict(index=index, content=self.sections[index], **self.rows[index], **self.state())

    def save(self, sheet_id: str, label: str, *, unsure: bool = False, note: str = '') -> dict:
        self._check_unfrozen()
        if label not in LABELS or type(unsure) is not bool:
            raise SR.FriendError('answer check failed')
        index = next((i for i, row in enumerate(self.rows) if row['sheet_id'] == sheet_id), None)
        if index is None:
            raise SR.FriendError('cell check failed')
        updated = [dict(row) for row in self.rows]
        updated[index] = dict(sheet_id=sheet_id, label=label,
                              unsure_guideline_gap='yes' if unsure else '', note=_clean_note(note))
        raw = _csv_bytes(updated)
        if all(row['label'] for row in updated):
            SR.checked_friend_rows(raw, self.cells)
        _atomic_write(self.labels_path, raw)
        self.rows = updated
        state = self.state()
        self.out(f'answered {state["answered"]} of {state["total"]}')
        return self.cell(index)


def make_handler(app: FriendApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def parse_request(self):
            if not super().parse_request():
                return False
            if not self._host_ok():
                self._send(403, '{"error":"refused"}')
                return False
            return True

        def send_error(self, code, message=None, explain=None):
            self._send(code, '{"error":"invalid request"}')

        def _send(self, status, body, content_type='application/json; charset=utf-8'):
            raw = body.encode('utf-8') if isinstance(body, str) else body
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(raw)

        def _host_ok(self):
            hosts = self.headers.get_all('Host', [])
            port = self.server.server_address[1]
            return len(hosts) == 1 and hosts[0] in (f'127.0.0.1:{port}', f'localhost:{port}')

        def _save_allowed(self):
            origins = self.headers.get_all('Origin', [])
            tokens = self.headers.get_all('X-Session-Token', [])
            port = self.server.server_address[1]
            return (len(origins) <= 1
                    and (not origins or origins[0] in (f'http://127.0.0.1:{port}', f'http://localhost:{port}'))
                    and len(tokens) == 1
                    and secrets.compare_digest(tokens[0].encode('utf-8'), app.token.encode('utf-8')))

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, '{"error":"refused"}')
            try:
                url = urlsplit(self.path)
                if url.path == '/':
                    page = PAGE.replace('__SESSION_TOKEN__', json.dumps(app.token))
                    self._send(200, page, 'text/html; charset=utf-8')
                elif url.path == '/codebook':
                    self._send(200, app.codebook, 'text/plain; charset=utf-8')
                elif url.path == '/api/state':
                    self._send(200, json.dumps(app.state()))
                elif url.path == '/api/cell':
                    index = int(parse_qs(url.query).get('index', ['0'])[0])
                    self._send(200, json.dumps(app.cell(index), ensure_ascii=False))
                else:
                    self._send(404, '{"error":"not found"}')
            except (ValueError, KeyError, TypeError, OverflowError):
                self._send(400, '{"error":"invalid request"}')

        def do_POST(self):
            if not self._host_ok() or not self._save_allowed():
                return self._send(403, '{"error":"refused"}')
            try:
                if urlsplit(self.path).path != '/api/save':
                    return self._send(404, '{"error":"not found"}')
                lengths = self.headers.get_all('Content-Length', [])
                if len(lengths) != 1 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('request check failed')
                length = int(lengths[0])
                if not 0 < length <= 65536:
                    raise ValueError('request check failed')
                payload = json.loads(self.rfile.read(length))
                if (not isinstance(payload, dict)
                        or set(payload) != {'sheet_id', 'label', 'unsure', 'note'}):
                    raise ValueError('request check failed')
                result = app.save(payload['sheet_id'], payload['label'],
                                  unsure=payload['unsure'], note=payload['note'])
                self._send(200, json.dumps(result, ensure_ascii=False))
            except OSError:
                self._send(503, '{"error":"not saved"}')
            except (ValueError, KeyError, TypeError, OverflowError):
                self._send(400, '{"error":"not saved"}')

    return Handler


def serve(app: FriendApp, port=8767, open_browser=True, *, out=print) -> None:
    with HTTPServer(('127.0.0.1', port), make_handler(app)) as server:
        app.out = out
        url = f'http://127.0.0.1:{server.server_address[1]}/'
        out(url)
        state = app.state()
        out(f'answered {state["answered"]} of {state["total"]}')
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main(argv=None, *, root=ROOT, out=print, err=None) -> int:
    err = err or (lambda message: print(message, file=sys.stderr))
    parser = argparse.ArgumentParser(prog='python -m eval.s10.friend_tool', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('serve')
    command.add_argument('--sheet', choices=('coldread', 'main'), required=True)
    command.add_argument('--port', type=int, default=8767)
    command.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.port <= 65535:
            raise ValueError('port check failed')
        app = FriendApp(Path(root), args.sheet, out=out)
        serve(app, args.port, not args.no_browser, out=out)
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, csv.Error,
            ZeroDivisionError, RuntimeError, SR.yaml.YAMLError):
        err(REFUSAL)
        return 2


PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Friend labelling</title>
<style>
*{box-sizing:border-box}body{margin:0;height:100dvh;display:flex;flex-direction:column;background:#f6f5f1;color:#202724;font:16px/1.45 system-ui,sans-serif}
header{padding:12px 20px;border-bottom:1px solid #ccd3ca;display:flex;gap:16px;align-items:center;flex-wrap:wrap;background:#fff}
header a{color:#22543d}main{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1.5fr) minmax(280px,1fr);gap:20px;padding:20px}
section{min-width:0;min-height:0;border:1px solid #ccd3ca;border-radius:12px;background:#fff;padding:20px;display:flex;flex-direction:column}
h1{margin:0 0 10px;font-size:24px}pre{margin:0;overflow:auto;min-height:0;flex:1;white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.6 Consolas,monospace}
button,input{font:inherit}button{border:1px solid #8b9b8e;border-radius:8px;background:#fff;padding:10px 12px;color:inherit;cursor:pointer}
button.selected{background:#22543d;color:white;border-color:#22543d}button:focus-visible,input:focus-visible{outline:3px solid #c88c28;outline-offset:2px}
.choices{display:grid;gap:10px;margin-bottom:18px}.uncertain{display:flex;gap:8px;align-items:center;margin-bottom:18px}
input[type=text]{width:100%;border:1px solid #8b9b8e;border-radius:6px;padding:9px;margin:6px 0 12px}nav{display:flex;gap:8px;flex-wrap:wrap;margin-top:auto}
#message{min-height:24px;color:#8b3025}small{color:#536156;display:block;margin-bottom:12px}
@media(max-width:700px){header{padding:8px 12px;gap:8px}main{grid-template-columns:1fr;grid-template-rows:minmax(100px,1fr) auto;padding:10px;gap:10px}section{padding:12px}.choices{grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;margin-bottom:8px}.choices button{font-size:12px;padding:8px 2px}.uncertain{margin-bottom:8px}small{display:none}nav button{padding:6px 8px}}
</style></head><body>
<header><strong>Friend labelling</strong><span id="position"></span><span id="progress"></span>
<a href="/codebook" target="_blank" rel="noopener">Frozen codebook</a></header>
<main><section><h1 id="sheet"></h1><pre id="content"></pre></section>
<section><div class="choices">
<button type="button" data-label="not_applicable" aria-pressed="false">not_applicable</button>
<button type="button" data-label="recommended" aria-pressed="false">recommended</button>
<button type="button" data-label="mandatory" aria-pressed="false">mandatory</button></div>
<label class="uncertain"><input id="unsure" type="checkbox">unsure: guideline gap</label>
<label for="note">Note (optional)</label><input id="note" type="text" maxlength="200" autocomplete="off">
<small>1 / 2 / 3: answer · U: unsure · ← / →: navigate. Shortcuts pause while typing a note.</small>
<div id="message" role="status" aria-live="polite"></div>
<nav><button id="previous" type="button">Previous</button><button id="next" type="button">Next</button>
<button id="first" type="button">first unanswered</button></nav></section></main>
<script>
const TOKEN=__SESSION_TOKEN__;
const $=id=>document.getElementById(id), choices=[...document.querySelectorAll('[data-label]')];
let current=null, selected='', dirty=false, queue=Promise.resolve();
function mark(){for(const b of choices){const pressed=b.dataset.label===selected;b.classList.toggle('selected',pressed);b.setAttribute('aria-pressed',String(pressed));}}
function progress(d){$('progress').textContent=`answered ${d.answered} of ${d.total}`;}
function run(action){queue=queue.then(async()=>{for(const e of [...choices,$('note'),$('unsure')])e.disabled=true;
 try{await action();}catch(e){$('message').textContent='not saved';}
 finally{for(const e of [...choices,$('note'),$('unsure')])e.disabled=false;}});}
async function persist(){if(!dirty||!selected||!current)return true;
 const payload={sheet_id:current.sheet_id,label:selected,unsure:$('unsure').checked,note:$('note').value};
 const r=await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':TOKEN},body:JSON.stringify(payload)});
 if(!r.ok){$('message').textContent='not saved';return false;}
 const d=await r.json();current=d;dirty=false;$('note').value=d.note;progress(d);$('message').textContent='saved';return true;}
function render(d){current=d;selected=d.label||'';dirty=false;mark();$('unsure').checked=d.unsure_guideline_gap==='yes';$('note').value=d.note||'';
 $('sheet').textContent=d.sheet_id;$('content').textContent=d.content;$('position').textContent=`cell ${d.index+1} of ${d.total}`;
 progress(d);$('message').textContent='';$('content').scrollTop=0;}
async function navigate(target){if(!await persist())return;
 const s=await fetch('/api/state');if(!s.ok)throw Error();const state=await s.json();progress(state);
 if(!state.total){current=null;selected='';dirty=false;mark();$('unsure').checked=false;$('note').value='';$('sheet').textContent='';$('content').textContent='';$('position').textContent='cell 0 of 0';return;}
 let index=target==='first'?(state.first_unanswered??current?.index??0):(current?.index??0)+target;
 index=Math.max(0,Math.min(index,state.total-1));const r=await fetch('/api/cell?index='+index);if(!r.ok)throw Error();render(await r.json());}
function choose(label){run(async()=>{if(!current)return;selected=label;dirty=true;mark();await persist();});}
for(const b of choices)b.addEventListener('click',()=>choose(b.dataset.label));
$('note').addEventListener('input',()=>{dirty=true;});$('note').addEventListener('change',()=>run(persist));
$('unsure').addEventListener('change',()=>{dirty=true;run(persist);});
$('previous').addEventListener('click',()=>run(()=>navigate(-1)));$('next').addEventListener('click',()=>run(()=>navigate(1)));
$('first').addEventListener('click',()=>run(()=>navigate('first')));
document.addEventListener('keydown',e=>{if(e.target===$('note')||e.ctrlKey||e.metaKey||e.altKey||e.repeat)return;
 const keys={'1':'not_applicable','2':'recommended','3':'mandatory'};
 if(keys[e.key]){e.preventDefault();choose(keys[e.key]);}
 else if(e.key.toLowerCase()==='u'){e.preventDefault();run(async()=>{$('unsure').checked=!$('unsure').checked;dirty=true;await persist();});}
 else if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();run(()=>navigate(e.key==='ArrowLeft'?-1:1));}});
run(()=>navigate('first'));
</script></body></html>
'''


if __name__ == '__main__':
    sys.exit(main())
