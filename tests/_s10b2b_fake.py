"""FAKE S10b repo for the S10b2b Part B tests (30 fake J-ids, 12 items each + fda-knee on J-003; no real data).

build_repo(root) writes, under a pytest temp root only: the judge file, s10b1_build.json (judge file + audit list
shas), fake judge results for A/B/C (eval round), the 350-row audit-A list, merge_v1.json (via merge.run) and
Hariom's audit-A labels (BOM + CRLF, label_tool FIELDS).
"""
import csv
import io
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from eval.s10 import draws as D
from eval.s10 import merge as MG
from standards.ruleset import normalised_sha

N, R, M = 'not_applicable', 'recommended', 'mandatory'
LABELS = (N, R, M)
ITEMS = ['ISO 10993-1', 'ISO 10993-3', 'ISO 10993-4', 'ISO 10993-5', 'ISO 10993-6', 'ISO 10993-7',
         'ISO 10993-10', 'ISO 10993-11', 'ISO 10993-12', 'ISO 10993-17', 'ISO 10993-18', 'ISO 10993-23']
JIDS = [f'J-{i:03d}' for i in range(1, 31)]
T0 = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


def entries():
    out = []
    for i, j in enumerate(JIDS, start=1):
        knee = j == 'J-003'
        profile = dict(device_name=f'Quasar gadget {i}', target_markets=['US'],
                       components=[dict(name='Shell', contact_nature='surface', contact_site='intact_skin',
                                        materials=[dict(name='Polymer X', material_class='polymer')])],
                       device_specific_standards=['fda-knee'] if knee else [])
        out.append(dict(j_id=j, contact_category='surface/intact_skin', duration='A', profile=profile,
                        items=ITEMS + (['fda-knee'] if knee else [])))
    return out


def all_cells():
    return [(e['j_id'], it) for e in entries() for it in e['items']]


def base_label(k):
    return LABELS[k % 3]


def judge_results():
    """A = B = base label; C differs on every 7th cell; B confidence low on every 5th cell."""
    res = {j: {} for j in MG.JUDGES}
    k = 0
    for e in entries():
        cells = {j: [] for j in MG.JUDGES}
        for it in e['items']:
            base = base_label(k)
            for j in MG.JUDGES:
                lab = base
                if j == 'C' and k % 7 == 0:
                    lab = {M: R, R: N, N: M}[base]
                conf = 'low' if (j == 'B' and k % 5 == 0) else 'high'
                cells[j].append(dict(item=it, label=lab, confidence=conf, section='§4.2', needs_sme='',
                                     reason=f'Fits {lab}: per section 4.{k % 9} -> {lab.upper()} ({j.lower()}{k})'))
            k += 1
        for j in MG.JUDGES:
            res[j][e['j_id']] = dict(device_id=e['j_id'], checklist={}, cells=cells[j])
    return res


def human_label(cid):
    j, it = cid.split('|')
    return base_label(all_cells().index((j, it)))


def write_csv(path, fields, rows, *, bom_crlf=False):
    buf = io.StringIO(newline='')
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator='\r\n' if bom_crlf else '\n')
    w.writeheader()
    w.writerows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b'\xef\xbb\xbf' if bom_crlf else b'') + buf.getvalue().encode('utf-8'))


def audit_rows():
    cells = all_cells()
    random.Random('fake-audit').shuffle(cells)
    return [dict(order=n, j_id=j, item=it, family='F1') for n, (j, it) in enumerate(cells[:350], start=1)]


def write_human(root, upto=350, label_of=None, t0=T0):
    rows = []
    for r in audit_rows()[:upto]:
        cid = MG.cell_id(r['j_id'], r['item'])
        rows.append(dict(order=r['order'], j_id=r['j_id'], item=r['item'],
                         label=(label_of or human_label)(cid), confidence='high', reason='fake',
                         timestamp=(t0 + timedelta(minutes=r['order'])).isoformat(timespec='seconds')))
    write_csv(Path(root) / D.HUMAN_REL, ('order', 'j_id', 'item', 'label', 'confidence', 'reason', 'timestamp'),
              rows, bom_crlf=True)


def build_repo(root, *, human_upto=350, merge=True):
    root = Path(root)
    jf = root / D.JUDGE_FILE_REL
    jf.parent.mkdir(parents=True, exist_ok=True)
    jf_raw = yaml.safe_dump(dict(format='s10-judge-file.v1', seed='S10b-v1', entries=entries()),
                            sort_keys=False, allow_unicode=True).encode('utf-8')
    jf.write_bytes(jf_raw)
    write_csv(root / D.LIST_REL, ('order', 'j_id', 'item', 'family'), audit_rows())
    man = root / D.BUILD_MANIFEST_REL
    man.parent.mkdir(parents=True, exist_ok=True)
    man.write_text(json.dumps(dict(files={D.JUDGE_FILE_REL: dict(sha256_lf=normalised_sha(jf_raw)),
                                          D.LIST_REL: dict(sha256_lf=normalised_sha((root / D.LIST_REL).read_bytes()))})),
                   encoding='utf-8')
    for j, by_id in judge_results().items():
        for jid, res in by_id.items():
            p = root / MG.RESULTS_REL / j / f'{jid}.result.json'
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(res), encoding='utf-8')
    if merge:
        assert MG.run(root, now='2026-11-16T00:00:00+00:00', out=lambda s: None) == 0
    if human_upto:
        write_human(root, human_upto)
    return root


def merged_rows(root):
    return json.loads((Path(root) / MG.OUT_REL).read_text(encoding='utf-8'))['rows']
