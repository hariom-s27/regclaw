"""S10b2 practice comparison, on a FAKE repo root only."""
import pytest

from eval.s10 import label_tool as LT
from eval.s10 import practice_compare as PC
from tests.test_s10b2_label_tool_practice import label_all, make_root, write_results

ALL = [f'PR-{i}' for i in range(1, 11)]
F4 = set(PC.FAMILY_ITEMS['F4'])
LABEL_WORDS = ('not_applicable', 'recommended', 'mandatory', 'REASON')


def run(root):
    lines = []
    return PC.run(root, now='2026-11-15T00:00:00+00:00', out=lines.append), lines


def ready(tmp_path, **kw):
    root = make_root(tmp_path)
    label_all(root, n=80, label_for=kw.pop('hariom', lambda d, it: 'recommended'))
    write_results(root, ALL, **kw)
    return root


def tables(root):
    d = root / PC.OUT_DIR_REL
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def test_straddle_rule():
    assert PC.straddle(['mandatory', 'recommended'])
    assert PC.straddle(['not_applicable', 'mandatory', 'mandatory'])
    assert not PC.straddle(['mandatory', 'mandatory', 'mandatory'])
    assert not PC.straddle(['recommended', 'not_applicable'])


def test_refused_before_lock(tmp_path):
    root = make_root(tmp_path)
    code, lines = run(root)
    assert code == 1 and lines == ['REFUSED (lock S10b2a-07 not open): Hariom labels 0/80; valid results '
                                   'A 0/10 B 0/10 C 0/10']
    label_all(root, n=80)
    write_results(root, ALL[:9])                             # PR-10 missing for every judge
    code, lines = run(root)
    assert code == 1 and 'Hariom labels 80/80; valid results A 9/10 B 9/10 C 9/10' in lines[0]
    assert tables(root) == []


def test_counts_and_numbered_files(tmp_path):
    root = ready(tmp_path,
                 label_for=lambda j, d, it: 'mandatory' if (j == 'C' and it in F4) else 'recommended',
                 hariom=lambda d, it: 'mandatory' if (d, it) == ('PR-1', 'ISO 10993-3') else 'recommended')
    code, lines = run(root)
    assert code == 0
    assert lines[0] == 'cells=200 hariom_cells=80'
    assert lines[1] == 'straddles: F1 1/70 F2 0/40 F3 0/70 F4 20/20'
    assert lines[2] == 'unanimous A=B=C: 180/200 (0.900)'
    assert lines[3] == 'all-four agreement (A, B, C, Hariom): 71/80 (0.887)'
    assert lines[4].startswith('wrote data/gold/s10/practice/practice_table_01.md sha256_lf=')
    for line in lines:
        assert not any(w in line for w in LABEL_WORDS)
    first = (root / PC.OUT_DIR_REL / 'practice_table_01.md').read_bytes()
    code, lines = run(root)
    assert code == 0 and 'practice_table_02.md' in lines[-1]
    assert tables(root) == ['practice_table_01.md', 'practice_table_02.md']
    assert (root / PC.OUT_DIR_REL / 'practice_table_01.md').read_bytes() == first


def test_table_content(tmp_path):
    root = ready(tmp_path,
                 label_for=lambda j, d, it: 'mandatory' if (j == 'C' and it == 'ISO/TS 10993-19') else 'recommended',
                 checklists={('C', 'PR-7'): dict(implant='yes', material_classes=['metal_alloy']),
                             ('A', 'PR-7'): dict(material_classes=['polymer'])})
    assert run(root)[0] == 0
    text = (root / PC.OUT_DIR_REL / 'practice_table_01.md').read_text(encoding='utf-8')
    assert 'Practice data only; never an eval number.' in text
    assert text.count('\n## PR-') == 10 and '\n## PR-10\n' in text
    assert '| ISO/TS 10993-19 | F4 | recommended (§4.1) | recommended (§4.1) | mandatory (§4.1) | recommended | STRADDLE |' in text
    assert '| ISO/TS 10993-19 | F4 | recommended (§4.1) | recommended (§4.1) | mandatory (§4.1) | – | STRADDLE |' in text
    assert '- **ISO/TS 10993-19** (STRADDLE) (not unanimous)' in text
    assert '  - C: C-REASON-ISO/TS 10993-19' in text and '  - Hariom: H-REASON' in text
    pr7 = text.partition('\n## PR-7\n')[2].partition('\n## PR-8\n')[0]
    assert '- **implant**' in pr7 and '  - C: yes' in pr7 and '  - A: no' in pr7
    assert '- **material_classes**' in pr7 and '  - C: metal_alloy' in pr7
    pr1 = text.partition('\n## PR-1\n')[2].partition('\n## PR-2\n')[0]
    assert pr1.count('(none)') == 1                          # checklist: no differences on PR-1
    rows = [l for l in pr1.splitlines() if l.startswith('| ISO')]
    assert [r.split(' | ')[0][2:] for r in rows] == [i for f in PC.FAMILIES for i in PC.FAMILY_ITEMS[f]]


def test_invalid_result_keeps_lock_closed(tmp_path):
    root = ready(tmp_path)
    p = root / LT.JUDGES_PRACTICE_REL / 'B' / 'PR-8.result.json'
    p.write_text('{"device_id": "PR-8"}', encoding='utf-8')
    code, lines = run(root)
    assert code == 1 and 'B 9/10' in lines[0]


def test_missing_kit_is_input_error(tmp_path):
    root = make_root(tmp_path, kit=False)
    code, lines = run(root)
    assert code == 2 and 'kit manifest missing' in lines[0]
