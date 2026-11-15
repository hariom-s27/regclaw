"""S10b2b-02/-08/-09/-10/-11: seeded draws on fakes (independent seed replays; no real data)."""
from datetime import datetime, timedelta, timezone
import json
import random

import pytest

import _netguard
import _s10b2b_fake as FK
from eval.s10 import draws as D
from standards.ruleset import normalised_sha


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')


def shuffled(xs, tag='x'):
    xs = list(xs)
    random.Random(tag).shuffle(xs)
    return xs


# ---- c-batches -----------------------------------------------------------------------------------------
def test_c_batches_replay_and_shape():
    got = D.c_batches(shuffled(FK.JIDS))
    ids = sorted(FK.JIDS)
    random.Random('S10b-v1|c-batches').shuffle(ids)
    assert [r['j_id'] for r in got] == ids
    assert [(r['batch'], r['position']) for r in got] == [(b, p) for b in range(1, 7) for p in range(1, 6)]
    assert sorted(r['j_id'] for r in got) == sorted(FK.JIDS)
    assert D.batches_of(got) == [ids[k:k + 5] for k in range(0, 30, 5)]
    assert D.c_batches(FK.JIDS) == got                              # input order does not matter


# ---- stability -----------------------------------------------------------------------------------------
def test_stability_replay_and_shape():
    ents = FK.entries()
    got = D.stability(shuffled(ents))
    devs = random.Random('S10b-v1|stab-devices').sample(sorted(FK.JIDS), 6)
    assert [d['j_id'] for d in got] == devs and len(set(devs)) == 6
    items = {e['j_id']: e['items'] for e in ents}
    for d in got:
        assert d['scored_items'] == random.Random(f'S10b-v1|stab-items|{d["j_id"]}').sample(sorted(items[d['j_id']]), 10)
        assert len(set(d['scored_items'])) == 10 and set(d['scored_items']) <= set(items[d['j_id']])
    assert D.stability(ents) == got


# ---- retest --------------------------------------------------------------------------------------------
def human_map(upto=150):
    out = {}
    for r in FK.audit_rows()[:upto]:
        out[f'{r["j_id"]}|{r["item"]}'] = dict(label='mandatory',
                                               timestamp=(FK.T0 + timedelta(minutes=r['order'])).isoformat())
    return out


def test_retest_replay_positions_and_date():
    rows = FK.audit_rows()
    cells, earliest = D.retest(rows, human_map(150))
    first150 = sorted(f'{r["j_id"]}|{r["item"]}' for r in rows[:150])
    assert [c['cell_id'] for c in cells] == random.Random('S10b-v1|retest').sample(first150, 30)
    assert len({c['cell_id'] for c in cells}) == 30 and all(c['cell_id'] in first150 for c in cells)
    assert earliest == FK.T0 + timedelta(minutes=150) + timedelta(days=7)      # latest stamp = position 150
    cells2, _ = D.retest(rows, dict(reversed(list(human_map(150).items()))))
    assert cells2 == cells


def test_retest_refuses_unlabelled_prefix():
    h = human_map(150)
    h.pop(next(iter(h)))
    with pytest.raises(D.DrawError):
        D.retest(FK.audit_rows(), h)


# ---- audit B -------------------------------------------------------------------------------------------
def row(cid, labels, confs, routed=False):
    j, it = cid.split('|')
    return dict(cell_id=cid, j_id=j, item=it, routed=routed, labels=dict(zip('ABC', labels)),
                confidences=dict(zip('ABC', confs)))


def test_audit_b_candidates_rules():
    M, R = 'mandatory', 'recommended'
    rows = [row('J-001|a', (M, M, M), ('high', 'low', 'high')),          # candidate
            row('J-001|b', (M, M, M), ('high', 'high', 'high')),         # no low
            row('J-001|c', (M, M, R), ('low', 'low', 'low'), routed=True),  # not unanimous
            row('J-002|a', (R, R, R), ('low', 'high', 'high'), routed=True),  # unanimous low but routed
            row('J-002|b', (R, R, R), ('high', 'high', 'low')),          # in audit A
            row('J-002|c', (R, R, None), ('low', 'low', None), routed=True)]
    cells, counts = D.audit_b(rows, ['J-002|b'])
    assert [c['cell_id'] for c in cells] == ['J-001|a']
    assert counts == dict(unanimous_low=3, routed_excluded=1, audit_a_excluded=1, candidates=1, selected=1)


def test_audit_b_samples_50_when_more_and_never_audit_a():
    rows = [row(f'J-{i:03d}|x{k}', ('recommended',) * 3, ('low', 'high', 'high')) for i in range(1, 31) for k in range(3)]
    audit_a = [r['cell_id'] for r in rows[::4]]
    cells, counts = D.audit_b(shuffled(rows), audit_a)
    cands = sorted(set(r['cell_id'] for r in rows) - set(audit_a))
    assert counts['candidates'] == len(cands) > 50 and counts['selected'] == 50
    assert [c['cell_id'] for c in cells] == random.Random('S10b-v1|audit-b').sample(cands, 50)
    assert not set(c['cell_id'] for c in cells) & set(audit_a)
    few, _ = D.audit_b(rows[:12], [])
    assert [c['cell_id'] for c in few] == sorted(r['cell_id'] for r in rows[:12])


# ---- CLI on a fake repo --------------------------------------------------------------------------------
@pytest.mark.parametrize('name', ['c-batches', 'stability', 'retest', 'audit-b'])
def test_cli_writes_once_prints_sha_and_counts_only(tmp_path, name):
    root = FK.build_repo(tmp_path)
    lines = []
    assert D.run(root, name, now='2026-11-16T00:00:00+00:00', out=lines.append) == 0
    path = root / D.DRAWS_REL / D.OUT[name]
    assert lines[-1] == f'{D.DRAWS_REL}/{D.OUT[name]} sha256_lf={normalised_sha(path.read_bytes())}'
    assert b'\r' not in path.read_bytes()
    text = '\n'.join(lines)
    for word in ('J-0', 'ISO 10993', 'mandatory', 'recommended', 'not_applicable', 'Quasar'):
        assert word not in text
    doc = json.loads(path.read_text(encoding='utf-8'))
    assert doc['draw'] == name and doc['format'] == 's10-draw.v1'
    assert D.run(root, name, out=lines.append) == 3


def test_cli_retest_date_and_audit_b_counts(tmp_path):
    root = FK.build_repo(tmp_path)
    lines = []
    assert D.run(root, 'retest', out=lines.append) == 0
    assert f'earliest allowed retest date (UTC): {(FK.T0 + timedelta(minutes=150, days=7)).date()}' in lines
    lines = []
    assert D.run(root, 'audit-b', out=lines.append) == 0
    doc = json.loads((root / D.DRAWS_REL / 'audit_b.json').read_text(encoding='utf-8'))
    audit_a = {f'{r["j_id"]}|{r["item"]}' for r in FK.audit_rows()}
    assert doc['counts']['selected'] == len(doc['cells']) <= 50
    assert not {c['cell_id'] for c in doc['cells']} & audit_a


def test_cli_refuses_changed_judge_file(tmp_path):
    root = FK.build_repo(tmp_path)
    p = root / D.JUDGE_FILE_REL
    p.write_bytes(p.read_bytes() + b'# changed\n')
    lines = []
    assert D.run(root, 'c-batches', out=lines.append) == 2
    assert not (root / D.DRAWS_REL).exists()
