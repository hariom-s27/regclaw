"""S10b2b (Q1/Q2): judge_kit eval and stability rounds, and the runner seeing them, on a FAKE repo only."""
import json
from pathlib import Path

import pytest
import yaml

import _netguard
import _s10b2b_fake as FK
from eval.s10 import draws as D
from eval.s10 import judge_kit as JK
from eval.s10 import judge_runner as JR
from standards.ruleset import normalised_sha

NOW = '2026-11-16T00:00:00+00:00'
REAL_SCHEMA = Path(__file__).resolve().parents[1] / JK.SCHEMA_REL


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')


def put(root, rel, raw):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw)
    return normalised_sha(raw)


def setup(root, draws=('c-batches', 'stability')):
    FK.build_repo(root, human_upto=0, merge=False)
    files = {JK.CODEBOOK_V1_REL: put(root, JK.CODEBOOK_V1_REL, b'# Fake codebook v1.0\r\n## 1. Rule\r\nbody\r\n'),
             JK.INSTRUCTIONS_V1_REL: put(root, JK.INSTRUCTIONS_V1_REL, b'# Fake instructions v1.0\r\nJSON only.\r\n'),
             JK.SCHEMA_REL: put(root, JK.SCHEMA_REL, REAL_SCHEMA.read_bytes())}
    put(root, JK.CODEBOOK_V1_MANIFEST_REL, json.dumps(dict(
        template_version=JK.TEMPLATE_VERSION, files={k: dict(sha256_lf=v) for k, v in files.items()})).encode())
    for name in draws:
        assert D.run(root, name, now=NOW, out=lambda s: None) == 0
    return root


def manifest(root, rnd):
    return json.loads((Path(root) / JK.KITS_REL / rnd / 'kit_manifest.json').read_text(encoding='utf-8'))


def orders(m):
    return {(f['judge'], d): o for f in m['files'] for d, o in f['item_order'].items()}


def test_eval_kit(tmp_path):
    root = setup(tmp_path)
    lines = []
    assert JK.run(root, 'eval', now=NOW, out=lines.append) == 0
    assert 'round=eval devices=30 files: A=30 B=30 C batches=6' in lines
    assert not any('Quasar' in l for l in lines)
    m = manifest(root, 'eval')
    assert m['items_source'] == JK.JUDGE_FILE_REL
    assert sorted(m['inputs']) == ['c_batches', 'codebook', 'instructions', 'judge_file', 'schema']
    assign = json.loads((root / D.DRAWS_REL / 'c_batches.json').read_text(encoding='utf-8'))['assignment']
    assert [f['device_ids'] for f in m['files'] if f['judge'] == 'C'] == D.batches_of(assign)
    items = {e['j_id']: e['items'] for e in FK.entries()}
    for (judge, d), order in orders(m).items():
        assert order == JK.item_order(items[d], judge, d)
    assert 'fda-knee' in orders(m)[('A', 'J-003')] and 'fda-knee' not in orders(m)[('A', 'J-001')]
    text = (root / JK.KITS_REL / 'eval' / 'A' / 'J-003.prompt.md').read_text(encoding='utf-8')
    sheet = yaml.safe_load(text.split(JK.H_SHEET)[1].split(JK.H_TASK)[0])
    assert sheet['id'] == 'J-003' and len(sheet['items']) == 13
    assert 'Fake codebook v1.0' in text and 'Fake instructions v1.0' in text


def test_eval_kit_refuses_changed_c_batches(tmp_path):
    root = setup(tmp_path)
    p = root / D.DRAWS_REL / 'c_batches.json'
    doc = json.loads(p.read_text(encoding='utf-8'))
    doc['assignment'][0], doc['assignment'][1] = doc['assignment'][1], doc['assignment'][0]
    p.write_text(json.dumps(doc), encoding='utf-8')
    lines = []
    assert JK.run(root, 'eval', now=NOW, out=lines.append) == 1
    assert any('c_batches.json differs' in l for l in lines)
    assert not (root / JK.KITS_REL / 'eval').exists()


def test_eval_kit_needs_draw_and_judge_file_sha(tmp_path):
    root = setup(tmp_path, draws=())
    lines = []
    assert JK.run(root, 'eval', now=NOW, out=lines.append) == 2
    assert any('missing input' in l for l in lines)
    root2 = setup(tmp_path / 'b')
    p = root2 / JK.JUDGE_FILE_REL
    p.write_bytes(p.read_bytes() + b'# changed\n')
    lines = []
    assert JK.run(root2, 'eval', now=NOW, out=lines.append) == 1
    assert any('judge file sha differs' in l for l in lines)


def test_stability_kit_reverses_each_judges_main_order(tmp_path):
    root = setup(tmp_path)
    assert JK.run(root, 'eval', now=NOW, out=lambda s: None) == 0
    lines = []
    assert JK.run(root, 'stability', now=NOW, out=lines.append) == 0
    assert 'round=stability devices=6 files: A=6 B=6 C batches=2' in lines
    main, stab = orders(manifest(root, 'eval')), orders(manifest(root, 'stability'))
    drawn = [d['j_id'] for d in json.loads((root / D.DRAWS_REL / 'stability_set.json').read_text(encoding='utf-8'))['devices']]
    assert {d for (_, d) in stab} == set(drawn)
    for (judge, d), order in stab.items():
        assert order == list(reversed(main[(judge, d)]))           # full list, reversed, per judge
    m = manifest(root, 'stability')
    assert [f['device_ids'] for f in m['files'] if f['judge'] == 'C'] == [drawn[:3], drawn[3:]]
    assert [f['device_ids'][0] for f in m['files'] if f['judge'] == 'A'] == drawn
    assert 'reverse' in m['item_order_rule']
    text = (root / JK.KITS_REL / 'stability' / 'B' / f'{drawn[0]}.prompt.md').read_text(encoding='utf-8')
    assert 'scored' not in text


def test_stability_kit_refuses_changed_draw(tmp_path):
    root = setup(tmp_path)
    p = root / D.DRAWS_REL / 'stability_set.json'
    doc = json.loads(p.read_text(encoding='utf-8'))
    doc['devices'][0]['scored_items'].reverse()
    p.write_text(json.dumps(doc), encoding='utf-8')
    assert JK.run(root, 'stability', now=NOW, out=lambda s: None) == 1


def test_runner_status_sees_eval_and_stability_kits(tmp_path):
    root = setup(tmp_path)
    assert JK.run(root, 'eval', now=NOW, out=lambda s: None) == 0
    assert JK.run(root, 'stability', now=NOW, out=lambda s: None) == 0
    assert JR.status(root, 'eval', out=lambda s: None)['C'] == dict(valid=30, invalid=0, missing=0)  # fake results exist
    assert JR.status(root, 'stability', out=lambda s: None)['A'] == dict(valid=0, invalid=0, missing=6)
