"""A3 short-gap retest and exploratory filler follow-up.

Real commands are run by Hariom after code and addendum freezes. Console
output contains aggregate counts, dates, fixed paths and LF hashes only.
Exit codes: 0 success, 1 refused, 2 input/output error, 3 output exists.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import secrets
import statistics
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser

from . import diagnostics as DG
from . import draws as D
from . import label_tool as LT
from .addendum_freeze import build_manifest as addendum_manifest, checked_input_path
from .merge import cell_id
from .prereg_freeze import compute

ROOT = Path(__file__).resolve().parents[2]
MODULE_REL = 'eval/s10/retest_a3.py'
LOCK_REL = 'eval/s10/manifests/s10b2h_code_lock.json'
LOCK_FORMAT = 's10b2h-code-lock.v1'
A3_REL = 'eval/s10/manifests/addendum_a3.json'
SPEC_REL = 'specs/S10_PREREG_ADDENDUM_A3.md'
AUDIT_FREEZE_REL = 'eval/s10/manifests/auditA_labels_hariom_150.json'
DRAW_REL = 'data/gold/s10/draws/retest.json'
FILLER_DRAW_REL = 'data/gold/s10/draws/retest_fillers.json'
LABELS_REL = 'data/gold/s10/sheets/retest_labels.csv'
FILLER_LABELS_REL = 'data/gold/s10/sheets/retest_filler_labels.csv'
RECOGNITION_REL = 'data/gold/s10/sheets/retest_recognition.csv'
SHEET_REL = 'data/gold/s10/sheets/retest_sheet.md'
SESSION_REL = 'eval/s10/manifests/retest_session_v1.json'
COMPANIONS_REL = 'data/gold/s10/diagnostics/retest_a3_companions.json'
FOLLOWUP_LABELS_REL = 'data/gold/s10/sheets/filler_followup_labels.csv'
FOLLOWUP_FREEZE_REL = 'eval/s10/manifests/filler_followup_v1.json'
FOLLOWUP_COMPANIONS_REL = 'data/gold/s10/diagnostics/filler_followup.json'
RECOGNITION_FIELDS = ('order', 'j_id', 'item', 'answer', 'timestamp')
ANSWERS = ('yes', 'no', 'unsure')
FILLER_SEED = 'S10b-v1|retest-fillers'
SESSION_SEED = 'S10b-v1|retest-session'
FOLLOWUP_SEED = 'S10b-v1|filler-followup'
FOLLOWUP_LABEL = 'exploratory 7-day retest on fillers (A3)'
QUESTION = 'Before today, had you labelled this exact cell (this device and this test item)?'


class InputError(ValueError):
    """Only fixed check names may appear in this exception."""


class Refused(PermissionError):
    """A session or freeze precondition was not met."""


def _path(root, relative):
    root = Path(root).resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise InputError('path check failed')
    return path


def _exists(root, relative):
    path = _path(root, relative)
    return path.exists() or path.is_symlink()


def _raw(root, relative):
    return _path(root, relative).read_bytes()


def _json(root, relative):
    doc = json.loads(_raw(root, relative).decode('utf-8'))
    if not isinstance(doc, dict):
        raise InputError('JSON object check failed')
    return doc


def _sha(raw):
    return compute(raw)['sha256_lf']


def _dt(value):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.utcoffset() is None:
            raise ValueError
        return stamp.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise InputError('UTC timestamp check failed') from None


def _now(value=None):
    return _dt(value) if value is not None else datetime.now(timezone.utc)


def _text(doc):
    return json.dumps(doc, indent=2, ensure_ascii=False) + '\n'


def _expected(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise InputError('expected sha format check failed')
    return value


def _write_once(root, relative, doc, out):
    path = _path(root, relative)
    text = _text(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
    out(f'{relative} sha256_lf={_sha(text.encode("utf-8"))}')


def _check_a3(root):
    manifest = _json(root, A3_REL)
    path, relative = checked_input_path(root, SPEC_REL)
    if manifest.get('file') != relative or _sha(path.read_bytes()) != manifest.get('sha256_lf'):
        raise InputError('A3 sha check failed')
    return manifest['sha256_lf']


def _check_lock(root):
    manifest = _json(root, LOCK_REL)
    if (manifest.get('format') != LOCK_FORMAT or set(manifest.get('files', {})) != {MODULE_REL}
            or manifest['files'][MODULE_REL] != compute(_raw(root, MODULE_REL))):
        raise InputError('code lock check failed')
    return _sha(_raw(root, LOCK_REL))


def _provenance(root):
    audit = _json(root, AUDIT_FREEZE_REL)
    if _sha(_raw(root, D.HUMAN_REL)) != audit.get('sha256_lf'):
        raise InputError('audit-A sha check failed')
    return dict(addendum_a3_sha256_lf=_check_a3(root), code_lock_sha256_lf=_check_lock(root))


def cells_sha(cells):
    return hashlib.sha256('\n'.join(c['cell_id'] for c in cells).encode('utf-8')).hexdigest()


def _draw_payloads(root, now=None):
    provenance = _provenance(root)
    rows = D.load_audit_rows(root)
    entries = D.load_entries(root)
    human = D.load_label_csv(_path(root, D.HUMAN_REL))
    cells, locked_earliest = D.retest(rows, human)
    latest = max(_dt(human[cell_id(r['j_id'], r['item'])]['timestamp'])
                 for r in rows[:D.RETEST_POSITIONS])
    audit_ids = {cell_id(r['j_id'], r['item']) for r in rows}
    all_cells = [dict(cell_id=cell_id(e['j_id'], item), j_id=e['j_id'], item=item)
                 for e in entries for item in e['items']]
    by_id = {c['cell_id']: c for c in all_cells}
    if len(by_id) != len(all_cells) or len(audit_ids) != len(rows) or not audit_ids <= by_id.keys():
        raise InputError('population identity check failed')
    pool = sorted(by_id.keys() - audit_ids)
    if len(pool) < 30:
        raise InputError('filler pool size check failed')
    fillers = [by_id[c] for c in random.Random(FILLER_SEED).sample(pool, 30)]
    created = _now(now).isoformat()
    retest = dict(format=D.FORMAT, draw='retest', created_at=created, seed_string=D.seed('retest'),
                  positions='1-150', cells=cells, earliest_allowed_utc=(latest + timedelta(days=1)).isoformat(),
                  wait_days=1, locked_earliest_utc=locked_earliest.astimezone(timezone.utc).isoformat(),
                  addendum_a3_sha256_lf=provenance['addendum_a3_sha256_lf'], cells_sha256=cells_sha(cells))
    filler = dict(format=D.FORMAT, draw='retest-fillers', created_at=created, seed_string=FILLER_SEED,
                  pool_size=len(pool), cells=fillers, cells_sha256=cells_sha(fillers))
    return retest, filler, len(rows)


def draw(root, *, now=None, out=print, err=print):
    if any(_exists(root, rel) for rel in (DRAW_REL, FILLER_DRAW_REL)):
        raise FileExistsError
    retest, fillers, audit_rows = _draw_payloads(root, now)
    # Serialize and validate BOTH complete documents before creating either file.
    texts = {FILLER_DRAW_REL: _text(fillers), DRAW_REL: _text(retest)}
    for rel, doc in ((FILLER_DRAW_REL, fillers), (DRAW_REL, retest)):
        if json.loads(texts[rel]) != doc:
            raise InputError('draw serialization check failed')
    if any(_exists(root, rel) for rel in texts):
        raise FileExistsError
    created = []
    try:
        for rel, text in texts.items():
            path = _path(root, rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8', newline='\n') as stream:
                created.append(rel)
                stream.write(text)
    except OSError:
        if created:
            for rel in created:
                err(f'partial draw: {Path(rel).name} written; report to Hariom')
            return 2
        raise
    out(f'audit_list_rows={audit_rows} filler_pool_size={fillers["pool_size"]}')
    out(f'retest_cells={len(retest["cells"])} filler_cells={len(fillers["cells"])}')
    out(f'earliest_allowed_utc={retest["earliest_allowed_utc"]}')
    out(f'locked_earliest_utc={retest["locked_earliest_utc"]}')
    for rel, text in texts.items():
        out(f'{rel} sha256_lf={_sha(text.encode("utf-8"))}')
    out(f'addendum_a3_sha256_lf={retest["addendum_a3_sha256_lf"]}')
    return 0


def _checked_draws(root):
    actual = (_json(root, DRAW_REL), _json(root, FILLER_DRAW_REL))
    expected = _draw_payloads(root)[:2]
    for doc, want in zip(actual, expected):
        _dt(doc.get('created_at'))
        if {k: v for k, v in doc.items() if k != 'created_at'} != {
                k: v for k, v in want.items() if k != 'created_at'}:
            raise InputError('draw replay check failed')
    return actual


def session_cells(entries, cells, seed):
    wanted = {c['cell_id'] for c in cells}
    jids = sorted({c['j_id'] for c in cells})
    random.Random(seed).shuffle(jids)
    by_jid = {e['j_id']: e for e in entries}
    ordered = [dict(cell_id=cell_id(j, item), j_id=j, item=item)
               for j in jids for item in by_jid[j]['items'] if cell_id(j, item) in wanted]
    if len(ordered) != len(cells) or len(wanted) != len(cells):
        raise InputError('session identity check failed')
    return [dict(c, order=i) for i, c in enumerate(ordered, 1)]


def _csv_bytes(fields, rows):
    # label_tool._Labeller.csv_text: UTF-8 BOM, CRLF, explicit field order.
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\r\n')
    writer.writeheader()
    writer.writerows(rows)
    return b'\xef\xbb\xbf' + stream.getvalue().encode('utf-8')


def _read_rows(root, relative, fields, cells, *, recognition=False):
    if not _exists(root, relative):
        return {}
    records = list(csv.reader(io.StringIO(_raw(root, relative).decode('utf-8-sig'), newline=''), strict=True))
    if not records or tuple(records[0]) != fields:
        raise InputError('CSV header check failed')
    allowed = {c['cell_id']: c for c in cells}
    result, previous_order = {}, 0
    for record in records[1:]:
        if len(record) != len(fields):
            raise InputError('CSV column check failed')
        row = dict(zip(fields, record))
        cid = cell_id(row['j_id'], row['item'])
        if (cid not in allowed or cid in result or row['order'] != str(allowed[cid]['order'])
                or int(row['order']) <= previous_order):
            raise InputError('CSV cell/order check failed')
        previous_order = int(row['order'])
        _dt(row['timestamp'])
        if recognition:
            if row['answer'] not in ANSWERS:
                raise InputError('recognition answer check failed')
        else:
            LT.validate_label(row)
        result[cid] = row
    return result


def _read_session(root, retest, fillers):
    cells = session_cells(D.load_entries(root), retest['cells'] + fillers['cells'], SESSION_SEED)
    repeat_ids = {c['cell_id'] for c in retest['cells']}
    labels = _read_rows(root, LABELS_REL, LT.FIELDS, [c for c in cells if c['cell_id'] in repeat_ids])
    filler_labels = _read_rows(root, FILLER_LABELS_REL, LT.FIELDS,
                               [c for c in cells if c['cell_id'] not in repeat_ids])
    labels.update(filler_labels)
    answers = _read_rows(root, RECOGNITION_REL, RECOGNITION_FIELDS, cells, recognition=True)
    ids = [c['cell_id'] for c in cells]
    if set(labels) != set(ids[:len(labels)]) or set(answers) != set(ids[:len(answers)]):
        raise InputError('session prefix check failed')
    if not len(answers) <= len(labels) <= len(answers) + 1:
        raise Refused('session incomplete or out of order')
    if any(_dt(answers[c]['timestamp']) < _dt(labels[c]['timestamp']) for c in answers):
        raise InputError('recognition timestamp check failed')
    return cells, labels, answers


def _atomic_write(path, raw):
    # Replacement pattern from label_tool; retry schedule from friend_tool.
    # Failed temporary files are retained. There is deliberately no cleanup deletion.
    path.parent.mkdir(parents=True, exist_ok=True)
    for delay in (0.05, 0.1, 0.2, 0.4, 0.8):
        try:
            with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent,
                                             prefix=f'.{path.name}.', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
            os.replace(temporary, path)
            return
        except PermissionError:
            time.sleep(delay)
    raise PermissionError('not saved')


def _session_doc(root, retest, fillers, cells, labels, answers, *, now=None):
    if len(labels) != 60 or len(answers) != 60:
        raise Refused('session incomplete')
    earliest = _dt(retest['earliest_allowed_utc'])
    if any(_dt(labels[c['cell_id']]['timestamp']) < earliest for c in retest['cells']):
        raise Refused('retest timing check failed')
    return dict(format='s10-retest-session.v1', created_at=_now(now).isoformat(),
                files={rel: _csv_stats(root, rel, count) for rel, count in
                       ((LABELS_REL, 30), (FILLER_LABELS_REL, 30), (RECOGNITION_REL, 60))},
                draws={rel: _sha(_raw(root, rel)) for rel in (DRAW_REL, FILLER_DRAW_REL)},
                **_provenance(root))


def _csv_stats(root, relative, expected_rows):
    raw = _raw(root, relative)
    records = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline='')))
    stamps = [_dt(r['timestamp']) for r in records]
    if len(records) != expected_rows:
        raise Refused('CSV row count check failed')
    return dict(sha256_lf=_sha(raw), bytes=len(raw), rows=len(records),
                first_timestamp_utc=min(stamps).isoformat(), last_timestamp_utc=max(stamps).isoformat())


def _verify_session(root):
    manifest = _json(root, SESSION_REL)
    retest, fillers = _checked_draws(root)
    cells, labels, answers = _read_session(root, retest, fillers)
    expected = _session_doc(root, retest, fillers, cells, labels, answers, now=manifest.get('created_at'))
    if manifest != expected:
        raise Refused('session freeze check failed')
    return retest, fillers, cells, labels, answers


class RetestApp:
    """Browser state contains no sampling membership or first-pass answers."""

    def __init__(self, root=ROOT, *, followup=False, now=None):
        self.root, self.followup, self.now = Path(root), followup, now
        self.lock = threading.Lock()
        self._check_unfrozen()
        if followup:
            retest, fillers, _, original, _ = _verify_session(root)
            earliest = max(_dt(original[c['cell_id']]['timestamp']) for c in fillers['cells']) + timedelta(days=7)
            if _now(now) < earliest:
                raise Refused('follow-up timing check failed')
            self.cells = session_cells(D.load_entries(root), fillers['cells'], FOLLOWUP_SEED)
            self.labels = _read_rows(root, FOLLOWUP_LABELS_REL, LT.FIELDS, self.cells)
            ids = [c['cell_id'] for c in self.cells]
            if set(self.labels) != set(ids[:len(self.labels)]):
                raise InputError('follow-up prefix check failed')
            self.answers = {}
            self.repeat_ids = set()
        else:
            retest, fillers = _checked_draws(root)
            if _now(now) < _dt(retest['earliest_allowed_utc']):
                raise Refused('retest timing check failed')
            self.cells, self.labels, self.answers = _read_session(root, retest, fillers)
            self.repeat_ids = {c['cell_id'] for c in retest['cells']}
        judge_raw, list_raw = _raw(root, D.JUDGE_FILE_REL), _raw(root, D.LIST_REL)
        LT.check_shas(judge_raw, list_raw, _path(root, D.BUILD_MANIFEST_REL))
        codebook_raw = _raw(root, LT.CODEBOOK_REL)
        LT.check_codebook_v1(codebook_raw, _path(root, LT.CODEBOOK_V1_MANIFEST_REL))
        entries = D.load_entries(root)
        self.profiles = {e['j_id']: LT._scrub(dict(contact_category=e['contact_category'], duration=e['duration'],
                                                 profile=e['profile'])) for e in entries}
        self.headings = LT.codebook_headings(codebook_raw.decode('utf-8-sig'))
        self.codebook_name = Path(LT.CODEBOOK_REL).name
        self.token = secrets.token_urlsafe()

    def _check_unfrozen(self):
        blockers = (FOLLOWUP_FREEZE_REL,) if self.followup else (SHEET_REL, SESSION_REL)
        if any(_exists(self.root, rel) for rel in blockers):
            raise Refused('labelling session closed')

    def _complete(self, cid):
        return cid in self.labels and (self.followup or cid in self.answers)

    def state(self):
        first = next((i for i, c in enumerate(self.cells) if not self._complete(c['cell_id'])), None)
        return dict(total=len(self.cells), done=sum(self._complete(c['cell_id']) for c in self.cells),
                    first_incomplete=first)

    def _cell(self, index):
        if type(index) is not int or not 0 <= index < len(self.cells):
            raise InputError('cell check failed')
        first = self.state()['first_incomplete']
        if first is not None and index > first:
            raise Refused('complete the current cell first')
        return self.cells[index]

    def item(self, index):
        with self.lock:
            c = self._cell(index)
            cid = c['cell_id']
            label = self.labels.get(cid)
            return dict(index=index, cell=dict(position=c['order'], j_id=c['j_id'], item=c['item']),
                        profile=self.profiles[c['j_id']], headings=self.headings, codebook_name=self.codebook_name,
                        label={k: label[k] for k in ('label', 'confidence', 'reason', 'timestamp')} if label else None,
                        label_locked=label is not None,
                        needs_recognition=bool(label and not self.followup and cid not in self.answers),
                        recognition=self.answers.get(cid, {}).get('answer'), **self.state())

    def _payload_cell(self, payload):
        if not isinstance(payload, dict):
            raise InputError('payload check failed')
        c = self._cell(payload.get('index'))
        if payload.get('j_id') != c['j_id'] or payload.get('item') != c['item']:
            raise InputError('cell check failed')
        return c

    def save_label(self, payload):
        with self.lock:
            self._check_unfrozen()
            c = self._payload_cell(payload)
            cid = c['cell_id']
            value = LT.validate_label(payload)
            if cid in self.labels:
                if all(self.labels[cid][k] == v for k, v in value.items()):
                    return self.state()  # A lost HTTP response must not alter the saved timestamp.
                raise Refused('saved label is read-only')
            row = dict(order=str(c['order']), j_id=c['j_id'], item=c['item'], **value,
                       timestamp=_now(self.now).isoformat(timespec='seconds'))
            updated = dict(self.labels, **{cid: row})
            if self.followup:
                relative, keep = FOLLOWUP_LABELS_REL, lambda key: True
            elif cid in self.repeat_ids:
                relative, keep = LABELS_REL, lambda key: key in self.repeat_ids
            else:
                relative, keep = FILLER_LABELS_REL, lambda key: key not in self.repeat_ids
            rows = [updated[x['cell_id']] for x in self.cells if x['cell_id'] in updated and keep(x['cell_id'])]
            _atomic_write(_path(self.root, relative), _csv_bytes(LT.FIELDS, rows))
            self.labels = updated
            return self.state()

    def save_recognition(self, payload):
        with self.lock:
            self._check_unfrozen()
            if self.followup:
                raise Refused('recognition is unavailable')
            c = self._payload_cell(payload)
            cid, answer = c['cell_id'], payload.get('answer')
            if cid not in self.labels or answer not in ANSWERS:
                raise Refused('recognition requires a saved label and an answer')
            if cid in self.answers:
                if self.answers[cid]['answer'] == answer:
                    return self.state()
                raise Refused('saved recognition is read-only')
            row = dict(order=str(c['order']), j_id=c['j_id'], item=c['item'], answer=answer,
                       timestamp=_now(self.now).isoformat(timespec='seconds'))
            updated = dict(self.answers, **{cid: row})
            rows = [updated[x['cell_id']] for x in self.cells if x['cell_id'] in updated]
            _atomic_write(_path(self.root, RECOGNITION_REL), _csv_bytes(RECOGNITION_FIELDS, rows))
            self.answers = updated
            return self.state()


def freeze_labels(root, expected_draw_sha, *, now=None, out=print):
    if _exists(root, SESSION_REL):
        raise FileExistsError
    if _sha(_raw(root, DRAW_REL)) != _expected(expected_draw_sha):
        raise InputError('draw sha check failed')
    retest, fillers = _checked_draws(root)
    cells, labels, answers = _read_session(root, retest, fillers)
    doc = _session_doc(root, retest, fillers, cells, labels, answers, now=now)
    _write_once(root, SESSION_REL, doc, out)
    for rel, stats in doc['files'].items():
        out(f'{rel} rows={stats["rows"]} sha256_lf={stats["sha256_lf"]}')
    return 0


def clopper_pearson_upper(changes, n, alpha=0.05):
    """Exact one-sided upper confidence limit, by binomial-CDF inversion."""
    if type(changes) is not int or type(n) is not int or n < 1 or not 0 <= changes <= n or not 0 < alpha < 1:
        raise InputError('binomial arguments check failed')
    if changes == n:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        cdf = math.fsum(math.comb(n, i) * mid ** i * (1 - mid) ** (n - i)
                        for i in range(changes + 1))
        if cdf > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def fisher_greater(yes_a, n_a, yes_b, n_b):
    """Fixed-margin, one-sided Fisher probability for group A > group B."""
    if any(type(x) is not int for x in (yes_a, n_a, yes_b, n_b)) or not (
            0 <= yes_a <= n_a and 0 <= yes_b <= n_b and n_a + n_b > 0):
        raise InputError('Fisher arguments check failed')
    successes = yes_a + yes_b
    denominator = math.comb(n_a + n_b, n_a)
    return min(1.0, math.fsum(math.comb(successes, x) * math.comb(n_a + n_b - successes, n_a - x)
                              / denominator for x in range(yes_a, min(successes, n_a) + 1)
                              if 0 <= n_a - x <= n_a + n_b - successes))


def gap_stats(first, again, cells):
    hours = [(_dt(again[c['cell_id']]['timestamp']) - _dt(first[c['cell_id']]['timestamp'])).total_seconds() / 3600
             for c in cells]
    if not hours or min(hours) < 0:
        raise InputError('gap timestamp check failed')
    return dict(n=len(hours), min=min(hours), median=statistics.median(hours), max=max(hours))


def _agreement(first, again, cells):
    n = len(cells)
    same = sum((first[c['cell_id']]['label'] == 'mandatory') ==
               (again[c['cell_id']]['label'] == 'mandatory') for c in cells)
    return dict(agree=same, n=n, rate=same / n, changes=n - same,
                change_rate_upper_95=clopper_pearson_upper(n - same, n),
                bound_method='exact one-sided 95% Clopper-Pearson upper bound',
                labels='binary (mandatory vs not)')


def companions(root, *, now=None, out=print):
    if _exists(root, COMPANIONS_REL):
        raise FileExistsError
    retest, fillers, _, labels, answers = _verify_session(root)
    first = D.load_label_csv(_path(root, D.HUMAN_REL))
    cells = retest['cells']
    counts = {name: dict(Counter(answers[c['cell_id']]['answer'] for c in group))
              for name, group in (('repeats', cells), ('fillers', fillers['cells']))}
    counts = {name: {answer: counts[name].get(answer, 0) for answer in ANSWERS} for name in counts}
    result = dict(raw_agreement=_agreement(first, labels, cells), gap_hours=gap_stats(first, labels, cells),
                  recognition=counts, recognition_fisher_greater_p=fisher_greater(
                      counts['repeats']['yes'], 30, counts['fillers']['yes'], 30),
                  undefined_kappa_note=('First-pass binary labels are one class; kappa may be undefined.'
                                        if len({first[c['cell_id']]['label'] == 'mandatory' for c in cells}) == 1
                                        else 'none'))
    doc = dict(format='s10-retest-a3-companions.v1', created_at=_now(now).isoformat(),
               session_sha256_lf=_sha(_raw(root, SESSION_REL)), result=result)
    _write_once(root, COMPANIONS_REL, doc, out)
    out(_text(result).strip())
    return 0


def _followup_doc(root, *, now=None):
    _, fillers, _, first, _ = _verify_session(root)
    cells = session_cells(D.load_entries(root), fillers['cells'], FOLLOWUP_SEED)
    again = _read_rows(root, FOLLOWUP_LABELS_REL, LT.FIELDS, cells)
    if len(again) != 30:
        raise Refused('follow-up incomplete')
    earliest = max(_dt(first[c['cell_id']]['timestamp']) for c in cells) + timedelta(days=7)
    if any(_dt(r['timestamp']) < earliest for r in again.values()):
        raise Refused('follow-up timing check failed')
    doc = dict(format='s10-filler-followup.v1', created_at=_now(now).isoformat(),
               files={FOLLOWUP_LABELS_REL: _csv_stats(root, FOLLOWUP_LABELS_REL, 30)},
               session_sha256_lf=_sha(_raw(root, SESSION_REL)),
               draws={rel: _sha(_raw(root, rel)) for rel in (DRAW_REL, FILLER_DRAW_REL)}, **_provenance(root))
    return doc, cells, first, again


def followup_freeze(root, *, now=None, out=print):
    if _exists(root, FOLLOWUP_FREEZE_REL):
        raise FileExistsError
    doc, _, _, _ = _followup_doc(root, now=now)
    _write_once(root, FOLLOWUP_FREEZE_REL, doc, out)
    stats = doc['files'][FOLLOWUP_LABELS_REL]
    out(f'rows={stats["rows"]} sha256_lf={stats["sha256_lf"]}')
    return 0


def followup_companions(root, *, now=None, out=print):
    if _exists(root, FOLLOWUP_COMPANIONS_REL):
        raise FileExistsError
    manifest = _json(root, FOLLOWUP_FREEZE_REL)
    expected, cells, first, again = _followup_doc(root, now=manifest.get('created_at'))
    if expected != manifest:
        raise Refused('follow-up freeze check failed')
    kappa = DG.intra_rater({c: r['label'] for c, r in first.items()},
                           {c: r['label'] for c, r in again.items()}, cells)
    # The locked function's numerical result is retained; its audit-A title is replaced for this use.
    kappa = dict(kappa, statistic='Cohen kappa, binary (mandatory vs not), filler first pass vs follow-up')
    result = dict(label=FOLLOWUP_LABEL, kappa=kappa, raw_agreement=_agreement(first, again, cells),
                  gap_hours=gap_stats(first, again, cells))
    _write_once(root, FOLLOWUP_COMPANIONS_REL,
                dict(format='s10-filler-followup-diagnostic.v1', created_at=_now(now).isoformat(),
                     freeze_sha256_lf=_sha(_raw(root, FOLLOWUP_FREEZE_REL)), result=result), out)
    out(_text(result).strip())
    return 0


def freeze_a3(root, file_rel, expected_sha, *, now=None, out=print):
    if _exists(root, A3_REL):
        raise FileExistsError
    path, relative = checked_input_path(root, file_rel)
    if relative != SPEC_REL:
        raise InputError('A3 file check failed')
    raw = path.read_bytes()
    if _sha(raw) != _expected(expected_sha):
        raise InputError('A3 expected sha check failed')
    doc = addendum_manifest(relative, raw, now=_now(now).isoformat())
    _write_once(root, A3_REL, doc, out)
    out(f'bytes={doc["bytes"]} lines={doc["lines"]} sha256_lf={doc["sha256_lf"]}')
    return 0


def lock(root, expected_pair, *, now=None, out=print):
    if _exists(root, LOCK_REL):
        raise FileExistsError
    relative, sep, sha = expected_pair.partition('=')
    if relative != MODULE_REL or sep != '=':
        raise InputError('lock expected file check failed')
    stats = compute(_raw(root, MODULE_REL))
    if stats['sha256_lf'] != _expected(sha):
        raise Refused('code sha mismatch')
    doc = dict(format=LOCK_FORMAT, created_at=_now(now).isoformat(), python_version=platform.python_version(),
               files={MODULE_REL: stats})
    _write_once(root, LOCK_REL, doc, out)
    out(f'{MODULE_REL} sha256_lf={stats["sha256_lf"]} bytes_lf={stats["bytes_lf"]} lines={stats["lines"]}')
    return 0


def make_handler(app):
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
            try:
                url = urlsplit(self.path)
                if url.path == '/':
                    page = PAGE.replace('__SESSION_TOKEN__', json.dumps(app.token))
                    page = page.replace('__QUESTION__', '' if app.followup else QUESTION)
                    self._send(200, page, 'text/html; charset=utf-8')
                elif url.path == '/api/state':
                    self._send(200, json.dumps(app.state()))
                elif url.path == '/api/item':
                    index = int(parse_qs(url.query).get('i', ['0'])[0])
                    self._send(200, json.dumps(app.item(index)))
                else:
                    self._send(404, '{"error":"not found"}')
            except (ValueError, KeyError, TypeError, OverflowError, PermissionError):
                self._send(400, '{"error":"invalid request"}')

        def do_POST(self):
            if not self._save_allowed():
                return self._send(403, '{"error":"refused"}')
            try:
                endpoint = urlsplit(self.path).path
                if endpoint not in ('/api/save', '/api/recognition'):
                    return self._send(404, '{"error":"not found"}')
                lengths = self.headers.get_all('Content-Length', [])
                if len(lengths) != 1 or self.headers.get('Transfer-Encoding'):
                    raise InputError('request check failed')
                length = int(lengths[0])
                if not 0 < length <= 65536:
                    raise InputError('request check failed')
                payload = json.loads(self.rfile.read(length))
                fields = {'index', 'j_id', 'item'} | ({'label', 'confidence', 'reason'}
                                                      if endpoint == '/api/save' else {'answer'})
                if not isinstance(payload, dict) or set(payload) != fields:
                    raise InputError('request check failed')
                save = app.save_label if endpoint == '/api/save' else app.save_recognition
                self._send(200, json.dumps(save(payload)))
            except Refused:
                self._send(400, '{"error":"refused"}')
            except OSError:
                self._send(503, '{"error":"not saved"}')
            except (ValueError, KeyError, TypeError, OverflowError):
                self._send(400, '{"error":"not saved"}')
    return Handler


def serve_session(app, port=8768, open_browser=True, *, out=print):
    with HTTPServer(('127.0.0.1', port), make_handler(app)) as server:
        url = f'http://127.0.0.1:{server.server_address[1]}/'
        out(url)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main(argv=None, *, root=ROOT, now=None, out=print, err=None):
    err = err or (lambda message: print(message, file=sys.stderr))
    parser = argparse.ArgumentParser(prog='python -m eval.s10.retest_a3', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('freeze-a3', help='freeze the approved A3 text, write-once')
    command.add_argument('--file', required=True)
    command.add_argument('--expected-sha', required=True, metavar='SHA')
    command = commands.add_parser('lock', help='freeze this module, write-once')
    command.add_argument('--expected-sha', required=True, metavar='REL=SHA')
    commands.add_parser('draw', help='write the retest and filler draws after provenance checks')
    for name in ('serve', 'followup-serve'):
        command = commands.add_parser(name, help='resume a local labelling session')
        command.add_argument('--port', type=int, default=8768)
        command.add_argument('--no-browser', action='store_true')
    command = commands.add_parser('freeze-labels', help='freeze the complete session, write-once')
    command.add_argument('--expected-draw-sha', required=True, metavar='SHA')
    commands.add_parser('companions', help='write descriptive retest companions from frozen inputs')
    commands.add_parser('followup-freeze', help='freeze complete follow-up labels, write-once')
    commands.add_parser('followup-companions', help='write exploratory follow-up statistics')
    args = parser.parse_args(argv)
    try:
        if args.command == 'freeze-a3':
            return freeze_a3(root, args.file, args.expected_sha, now=now, out=out)
        if args.command == 'lock':
            return lock(root, args.expected_sha, now=now, out=out)
        if args.command == 'draw':
            return draw(root, now=now, out=out, err=err)
        if args.command in ('serve', 'followup-serve'):
            if not 1 <= args.port <= 65535:
                raise InputError('port check failed')
            app = RetestApp(root, followup=args.command == 'followup-serve', now=now)
            serve_session(app, args.port, not args.no_browser, out=out)
            return 0
        if args.command == 'freeze-labels':
            return freeze_labels(root, args.expected_draw_sha, now=now, out=out)
        functions = {'companions': companions, 'followup-freeze': followup_freeze,
                     'followup-companions': followup_companions}
        return functions[args.command](root, now=now, out=out)
    except FileExistsError:
        err('REFUSED: output exists (write-once); nothing written')
        return 3
    except Refused as exc:
        err(f'REFUSED: {exc}; nothing written')
        return 1
    except PermissionError:
        err('REFUSED: input permission or freeze check failed; nothing written')
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, OverflowError,
            csv.Error, D.yaml.YAMLError):
        err('ERROR: input or output check failed')
        return 2


PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>S10 Labelling</title><style>
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#66645e;--card:#fff;--line:#d9d7d0;--acc:#2f5d9e;--warn:#9e3b2f;--sel:#dfe8f5}
@media(prefers-color-scheme:dark){:root{--bg:#1b1b1a;--fg:#ecebe6;--muted:#a3a19a;--card:#252524;--line:#3b3a37;--acc:#8fb3e8;--warn:#e8988f;--sel:#2c3a4f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,Segoe UI,sans-serif}
header{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);padding:8px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;z-index:2}
button{font:inherit;padding:5px 10px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;cursor:pointer}
button:disabled{opacity:.6;cursor:default}main{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(300px,1fr);gap:16px;padding:16px}
@media(max-width:900px){main{grid-template-columns:1fr}}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:12px}
.k{color:var(--muted);font-size:12px}.big{font-size:20px;font-weight:600}ul.tree{margin:4px 0;padding-left:18px}.key{color:var(--muted)}
.opts{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 10px}.opts label{border:1px solid var(--line);border-radius:6px;padding:3px 9px;cursor:pointer}
.opts label:has(input:checked){background:var(--sel);border-color:var(--acc)}input[type=text]{width:100%;font:inherit;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px}
#msg{color:var(--warn);min-height:1.2em}#heads{margin:0;padding-left:16px;font-size:13px}[hidden]{display:none!important}
</style></head><body><header><b>S10 labelling</b><span id="prog"></span>
<button id="prev">Previous</button><button id="next">Next</button><button id="first">First incomplete</button></header>
<main><section><div class="card"><div class="k">Cell <span id="pos"></span></div>
<div class="big"><span id="jid"></span> &times; <span id="item"></span></div>
<div class="k">Contact category <span id="cat"></span> · duration <span id="dur"></span></div></div>
<div class="card"><div class="k">Device profile (input fields)</div><div id="profile"></div></div></section><section>
<div class="card"><div class="k">Label</div><div class="opts" id="label"></div>
<div class="k">Confidence</div><div class="opts" id="confidence"></div>
<div class="k">Reason (one line, required, max 300)</div><input type="text" id="reason" maxlength="300">
<p><button id="save">Save label</button></p><div id="recognition" hidden><p>__QUESTION__</p>
<button data-answer="yes">yes</button> <button data-answer="no">no</button> <button data-answer="unsure">unsure</button></div>
<p id="msg" role="status" aria-live="polite"></p></div>
<div class="card"><div class="k">Codebook sections (open <span id="cbname"></span> alongside)</div><ul id="heads"></ul></div>
</section></main><script>
const TOKEN=__SESSION_TOKEN__, $=id=>document.getElementById(id);
const OPTS={label:['not_applicable','recommended','mandatory'],confidence:['low','medium','high']};
let current=null,state={},queue=Promise.resolve(),busy=false;
for(const [f,values] of Object.entries(OPTS)){for(const v of values){const l=document.createElement('label'),i=document.createElement('input');
 i.type='radio';i.name=f;i.value=v;const s=document.createElement('span');s.textContent=v;l.append(i,s);$(f).append(l);}}
function setRadio(f,v){for(const i of document.getElementsByName(f))i.checked=(i.value===v);}
function getRadio(f){const i=[...document.getElementsByName(f)].find(x=>x.checked);return i?i.value:'';}
function tree(v){if(Array.isArray(v)){const u=document.createElement('ul');u.className='tree';v.forEach((x,i)=>{const li=document.createElement('li');
 const k=document.createElement('span');k.className='key';k.textContent='['+(i+1)+'] ';li.append(k,tree(x));u.append(li);});return u;}
 if(v&&typeof v==='object'){const u=document.createElement('ul');u.className='tree';for(const [k,x] of Object.entries(v)){const li=document.createElement('li');
 const s=document.createElement('span');s.className='key';s.textContent=k+': ';li.append(s,tree(x));u.append(li);}return u;}
 const s=document.createElement('span');s.textContent=String(v);return s;}
function controls(){const locked=busy||!current||current.label_locked;
 for(const i of document.querySelectorAll('#label input,#confidence input'))i.disabled=locked;
 $('reason').readOnly=locked;$('save').disabled=locked;
 for(const b of document.querySelectorAll('button')){if(b.id!=='save')b.disabled=busy;}
 $('next').disabled=busy||!current||current.needs_recognition||!current.label_locked;}
function run(action){queue=queue.then(async()=>{busy=true;controls();try{await action();}catch(e){$('msg').textContent='not saved; retry before continuing';}
 finally{busy=false;controls();}});}
function render(d){current=d;state=d;$('prog').textContent=`completed ${d.done} of ${d.total}`;$('pos').textContent=d.cell.position+' of '+d.total;
 $('jid').textContent=d.cell.j_id;$('item').textContent=d.cell.item;$('cat').textContent=d.profile.contact_category;$('dur').textContent=d.profile.duration;
 $('cbname').textContent=d.codebook_name;$('profile').textContent='';$('profile').append(tree(d.profile.profile));$('heads').textContent='';
 for(const t of d.headings){const li=document.createElement('li');li.textContent=t.replace(/^#+\s*/,'');$('heads').append(li);}
 const L=d.label||{};setRadio('label',L.label||'');setRadio('confidence',L.confidence||'');$('reason').value=L.reason||'';
 $('recognition').hidden=!d.needs_recognition;$('msg').textContent=d.needs_recognition?'Label saved. Answer the question to continue.':(d.label_locked?'Saved; read-only.':'');controls();}
async function go(i){if(i<0||(state.total!==undefined&&i>=state.total))return;
 const r=await fetch('/api/item?i='+i);if(!r.ok)throw Error();render(await r.json());}
async function post(endpoint,payload){const r=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':TOKEN},body:JSON.stringify(payload)});
 if(!r.ok)throw Error();return await r.json();}
function identity(){return {index:current.index,j_id:current.cell.j_id,item:current.cell.item};}
async function save(){if(!current||current.label_locked)return;const payload={...identity(),label:getRadio('label'),confidence:getRadio('confidence'),reason:$('reason').value.trim()};
 if(!payload.label||!payload.confidence||!payload.reason){$('msg').textContent='Label, confidence and reason are required.';return;}
 const d=await post('/api/save',payload);await go(current.index);if(!current.needs_recognition&&d.first_incomplete!==null)await go(d.first_incomplete);}
async function answer(value){const d=await post('/api/recognition',{...identity(),answer:value});await go(d.first_incomplete??current.index);}
$('save').onclick=()=>run(save);for(const b of document.querySelectorAll('[data-answer]'))b.onclick=()=>run(()=>answer(b.dataset.answer));
$('prev').onclick=()=>run(()=>go(current.index-1));$('next').onclick=()=>run(()=>go(current.index+1));
$('first').onclick=()=>run(async()=>{const r=await fetch('/api/state');if(!r.ok)throw Error();const d=await r.json();await go(d.first_incomplete??current?.index??0);});
run(async()=>{const r=await fetch('/api/state');if(!r.ok)throw Error();state=await r.json();await go(state.first_incomplete??0);});
</script></body></html>
'''


if __name__ == '__main__':
    raise SystemExit(main())
