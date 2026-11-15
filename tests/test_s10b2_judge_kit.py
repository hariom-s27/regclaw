"""S10b2 judge kit builder, on a FAKE repo root only (no real files)."""
import json
import random
from pathlib import Path

import pytest
import yaml

from eval.s10 import judge_kit as JK
from eval.s10.audit_list import FAMILY_ITEMS
from standards.ruleset import normalised_sha

PARTS = [i for f in ('F2', 'F1', 'F3', 'F4') for i in FAMILY_ITEMS[f]]
NOW = '2026-11-15T00:00:00+00:00'
REAL_SCHEMA = Path(__file__).resolve().parents[1] / JK.SCHEMA_REL
LEAK = 'LEAKWORD'


def fake_entry(i, evidence=False):
    comp = dict(name='FAKE part', contact_nature='surface', contact_site='intact_skin',
                exposure=dict(pattern='single', hours_per_use=2),
                materials=[dict(name='FAKE polymer', material_class='polymer')], sterilization='non_sterile')
    if evidence:
        comp['existing_evidence'] = dict(equivalent_marketed_device=True)
    return dict(id=f'PR-{i}', split='practice', contact_category='surface/intact_skin', duration='A',
                flags=['attachment_g_material'], profile=dict(device_name=f'Quasar widget {i}', components=[comp]))


def put(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode('utf-8'))
    return normalised_sha(text.encode('utf-8'))


def setup(root, instructions=True):
    cb = put(root, JK.CODEBOOK_V01_REL, '# Fake codebook\r\n## 1. Rule\r\nbody\r\n')
    if instructions:
        put(root, JK.INSTRUCTIONS_V01_REL, '# Fake instructions\r\nOutput JSON only.\r\n')
    put(root, JK.SCHEMA_REL, REAL_SCHEMA.read_text(encoding='utf-8'))
    put(root, 'standards/data/endpoint_to_part.v1.yaml', yaml.safe_dump(dict(version=1, parts=[dict(part=p) for p in PARTS])))
    pr = put(root, JK.PRACTICE_REL, yaml.safe_dump(dict(format='s10-practice-profiles.v1', split='practice',
                                                        profiles=[fake_entry(i, evidence=(i == 2)) for i in range(1, 5)])))
    probes = put(root, JK.PROBES_REL, yaml.safe_dump(dict(format='s10-practice-profiles.v1', split='practice',
                                                          profiles=[fake_entry(i) for i in range(5, 11)])))
    put(root, JK.BUILD_MANIFEST_REL, json.dumps(dict(files={JK.CODEBOOK_V01_REL: dict(sha256_lf=cb),
                                                            JK.PRACTICE_REL: dict(sha256_lf=pr)})))
    return probes


def kit_files(root, rnd='practice'):
    d = Path(root) / JK.KITS_REL / rnd
    return sorted(p.relative_to(d).as_posix() for p in d.rglob('*') if p.is_file()) if d.exists() else []


def test_practice_kit(tmp_path):
    probes = setup(tmp_path)
    lines = []
    assert JK.run(tmp_path, 'practice', probes_sha=probes, now=NOW, out=lines.append) == 0
    files = kit_files(tmp_path)
    assert sum(f.startswith('A/') for f in files) == 10 and sum(f.startswith('B/') for f in files) == 10
    assert [f for f in files if f.startswith('C/')] == ['C/batch_01.prompt.md', 'C/batch_02.prompt.md']
    assert 'kit_manifest.json' in files and len(files) == 23
    assert 'round=practice devices=10 files: A=10 B=10 C batches=2' in lines
    assert not any('Quasar' in l for l in lines)
    m = json.loads((tmp_path / JK.KITS_REL / 'practice' / 'kit_manifest.json').read_text(encoding='utf-8'))
    assert m['template_version'] == JK.TEMPLATE_VERSION and m['date'] == NOW and m['round'] == 'practice'
    assert sorted(m['inputs']) == ['codebook', 'instructions', 'practice_probes', 'practice_profiles', 'schema']
    assert m['inputs']['practice_probes']['sha256_lf'] == probes
    batches = [f for f in m['files'] if f['judge'] == 'C']
    assert [b['device_ids'] for b in batches] == [[f'PR-{i}' for i in range(1, 6)], [f'PR-{i}' for i in range(6, 11)]]
    for f in m['files']:
        raw = (tmp_path / f['path']).read_bytes()
        assert b'\r' not in raw and normalised_sha(raw) == f['sha256_lf']
        assert f['seed_strings'] == [f'S10b-v1|order|{f["judge"]}|{d}' for d in f['device_ids']]
        for d, order in f['item_order'].items():
            expect = list(PARTS)
            random.Random(f'S10b-v1|order|{f["judge"]}|{d}').shuffle(expect)
            assert order == expect


def test_prompt_blocks_and_sheet(tmp_path):
    JK.run(tmp_path, 'practice', probes_sha=setup(tmp_path), now=NOW, out=lambda s: None)
    text = (tmp_path / JK.KITS_REL / 'practice' / 'A' / 'PR-2.prompt.md').read_text(encoding='utf-8')
    heads = [JK.H_INSTR, JK.H_CODEBOOK, JK.H_SHEET, JK.H_TASK]
    pos = [text.index(h) for h in heads]
    assert pos == sorted(pos) and pos[0] == 0
    assert all(text.count(h) == 1 for h in heads)
    sheet = yaml.safe_load(text.split(JK.H_SHEET)[1].split(JK.H_TASK)[0])
    assert list(sheet) == ['id', 'contact_category', 'duration', 'profile', 'items']
    assert sheet['id'] == 'PR-2' and sorted(sheet['items']) == sorted(PARTS)
    for word in ('flags', 'existing_evidence', 'derived', 'split:', 'attachment_g_material'):
        assert word not in text
    task = text.split(JK.H_TASK)[1]
    assert 'Return only the JSON object for device PR-2, labelling exactly these 20 items in this order: ' in task
    assert '; '.join(sheet['items']) in task


def test_judges_get_different_orders(tmp_path):
    JK.run(tmp_path, 'practice', probes_sha=setup(tmp_path), now=NOW, out=lambda s: None)
    m = json.loads((tmp_path / JK.KITS_REL / 'practice' / 'kit_manifest.json').read_text(encoding='utf-8'))
    order = {(f['judge'], d): o for f in m['files'] for d, o in f['item_order'].items()}
    assert all(order[('A', f'PR-{i}')] != order[('B', f'PR-{i}')] for i in range(1, 11))
    assert order[('A', 'PR-1')] != order[('A', 'PR-2')]


def test_batch_file(tmp_path):
    JK.run(tmp_path, 'practice', probes_sha=setup(tmp_path), now=NOW, out=lambda s: None)
    text = (tmp_path / JK.KITS_REL / 'practice' / 'C' / 'batch_02.prompt.md').read_text(encoding='utf-8')
    assert text.count(JK.H_INSTR) == 1 and text.count(JK.H_CODEBOOK) == 1 and JK.H_SHEET not in text
    assert [text.index(f'=== DEVICE SHEET {k} ===') for k in range(1, 6)] == \
        sorted(text.index(f'=== DEVICE SHEET {k} ===') for k in range(1, 6))
    assert '=== DEVICE SHEET 6 ===' not in text
    assert 'Return one JSON array with 5 objects, one per device, in this order: PR-6, PR-7, PR-8, PR-9, PR-10.' in text


def test_deterministic(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    for root in (a, b):
        JK.run(root, 'practice', probes_sha=setup(root), now=NOW, out=lambda s: None)
    for f in kit_files(a):
        assert (a / JK.KITS_REL / 'practice' / f).read_bytes() == (b / JK.KITS_REL / 'practice' / f).read_bytes()


def test_write_once(tmp_path):
    probes = setup(tmp_path)
    assert JK.run(tmp_path, 'practice', probes_sha=probes, now=NOW, out=lambda s: None) == 0
    m = tmp_path / JK.KITS_REL / 'practice' / 'kit_manifest.json'
    before = m.read_bytes()
    assert JK.run(tmp_path, 'practice', probes_sha=probes, now='later', out=lambda s: None) == 3
    assert m.read_bytes() == before


@pytest.mark.parametrize('what', ['codebook', 'practice', 'probes'])
def test_sha_mismatch_refused(tmp_path, what):
    probes = setup(tmp_path)
    if what == 'codebook':
        put(tmp_path, JK.CODEBOOK_V01_REL, '# changed codebook\n')
    elif what == 'practice':
        p = tmp_path / JK.PRACTICE_REL
        p.write_text(p.read_text(encoding='utf-8') + '# changed\n', encoding='utf-8')
    else:
        probes = '0' * 64
    assert JK.run(tmp_path, 'practice', probes_sha=probes, now=NOW, out=lambda s: None) == 1
    assert kit_files(tmp_path) == []


def test_missing_instructions_is_input_error(tmp_path):
    probes = setup(tmp_path, instructions=False)
    lines = []
    assert JK.run(tmp_path, 'practice', probes_sha=probes, now=NOW, out=lines.append) == 2
    assert kit_files(tmp_path) == [] and any('JUDGE_INSTRUCTIONS' in l for l in lines)


def test_eval_round_refused(tmp_path):
    setup(tmp_path)
    lines = []
    assert JK.run(tmp_path, 'eval', now=NOW, out=lines.append) == 1
    assert any('does not exist' in l for l in lines)
    files = {JK.SCHEMA_REL: dict(sha256_lf=normalised_sha((tmp_path / JK.SCHEMA_REL).read_bytes()))}
    put(tmp_path, JK.CODEBOOK_V1_MANIFEST_REL, json.dumps(dict(template_version=JK.TEMPLATE_VERSION, files=files)))
    lines.clear()
    assert JK.run(tmp_path, 'eval', now=NOW, out=lines.append) == 1
    assert any('no eval kit is built' in l for l in lines)
    files[JK.SCHEMA_REL]['sha256_lf'] = '0' * 64
    put(tmp_path, JK.CODEBOOK_V1_MANIFEST_REL, json.dumps(dict(template_version=JK.TEMPLATE_VERSION, files=files)))
    lines.clear()
    assert JK.run(tmp_path, 'eval', now=NOW, out=lines.append) == 1
    assert any('sha differs' in l for l in lines)
    assert kit_files(tmp_path, 'eval') == []


def test_smoke_round(tmp_path):
    setup(tmp_path)
    assert JK.run(tmp_path, 'smoke', now=NOW, out=lambda s: None) == 0
    assert kit_files(tmp_path, 'smoke') == ['A/SMOKE-1.prompt.md', 'B/SMOKE-1.prompt.md', 'C/batch_01.prompt.md',
                                            'kit_manifest.json']
    m = json.loads((tmp_path / JK.KITS_REL / 'smoke' / 'kit_manifest.json').read_text(encoding='utf-8'))
    assert 'practice_probes' not in m['inputs']


def test_device_key_natural_order():
    assert sorted(['PR-10', 'PR-2', 'PR-1'], key=JK.device_key) == ['PR-1', 'PR-2', 'PR-10']
