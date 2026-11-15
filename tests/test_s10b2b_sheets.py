"""S10b2b-08/-09/-12 + Q9: blind sheets, reason cleaning, referee pass 1 / pass 2 (fakes only)."""
import csv
import io
import json
import random
import re

import pytest

import _netguard
import _s10b2b_fake as FK
from eval.s10 import draws as D
from eval.s10 import sheets as SH
from standards.ruleset import normalised_sha


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')


N, R, M = FK.N, FK.R, FK.M
LABEL_WORD = re.compile(r'(?i)\b(mandatory|recommended|not_applicable|not[ -]applicable|n/a)\b|\bNA\b')


# ---- clean_reason --------------------------------------------------------------------------------------
@pytest.mark.parametrize('raw,clean', [
    ('This is MANDATORY.', 'This is [label].'),
    ('mandatory/recommended split', '[label]/[label] split'),
    ('Not_Applicable here; not applicable there; not-applicable too', '[label] here; [label] there; [label] too'),
    ('Rated N/A or n/a; NA by rule', 'Rated [label] or [label]; [label] by rule'),
    ('na is not a label word; NAMSA and mandatorily stay', 'na is not a label word; NAMSA and mandatorily stay'),
    ('contact -> long  =>   test → done', 'contact → long → test → done'),
    ('“quoted” and ‘single’', '"quoted" and \'single\''),
    ('per Section 4.2, sec. 5.1.3, sec 7 and § 6.1', 'per §4.2, §5.1.3, §7 and §6.1'),
    ('  many\t\tspaces\n here ', 'many spaces here'),
])
def test_clean_reason_cases(raw, clean):
    assert SH.clean_reason(raw) == clean


def test_no_label_word_survives_cleaning_fuzz():
    words = ['mandatory', 'Recommended', 'NOT_APPLICABLE', 'not applicable', 'Not-Applicable', 'N/A', 'n/a', 'NA']
    rng = random.Random('fuzz')
    for _ in range(500):
        parts = [rng.choice(words + ['part', '->', '(', ')', '.', ',', '"', 'Section 4.1', ':'])
                 for _ in range(rng.randint(1, 12))]
        out = SH.clean_reason(rng.choice([' ', '', '  ']).join(parts))
        assert not LABEL_WORD.search(out), out


NAME_WORD = re.compile(r'(?i)\b(?:judge\s+[abc]|' + '|'.join(SH.JUDGE_NAMES) + r')\b')


@pytest.mark.parametrize('name', list(SH.JUDGE_NAMES) + ['judge a', 'judge b', 'judge c'])
def test_every_name_masked_any_case_and_punctuation(name):
    for variant in (name, name.upper(), name.title(), name[:1].upper() + name[1:]):
        for raw in (variant, f'({variant})', f'{variant}, then', f'"{variant}".', f'per {variant}: ok', f'{variant}-6'):
            out = SH.clean_reason(raw)
            assert '[judge]' in out and not NAME_WORD.search(out), (raw, out)


@pytest.mark.parametrize('raw,clean', [
    ('Judge  A said so; JUDGE b too', '[judge] said so; [judge] too'),
    ('As Claude Opus 5.5 and GPT-6 noted (Gemini via Antigravity)', 'As [judge] [judge] and [judge] noted ([judge] via [judge])'),
    ('OpenAI, Anthropic, Google; ChatGPT; codex; haiku; sonnet.', '[judge], [judge], [judge]; [judge]; [judge]; [judge]; [judge].'),
    ('opuses googled codexes haikus sonnets claudes gpts judged judge d', 'opuses googled codexes haikus sonnets claudes gpts judged judge d'),
    ('Magnum opusculum; geminid; astral; gpts stay', 'Magnum opusculum; geminid; astral; gpts stay'),
    ('flash sterilisation, pro forma, mini spike', 'flash sterilisation, pro forma, mini spike'),
    ('Per Opus 5.5. Then gpt4: done', 'Per [judge]. Then [judge]: done'),
])
def test_name_masking_cases(raw, clean):
    assert SH.clean_reason(raw) == clean


@pytest.mark.parametrize('raw', ['GPT-6', 'gpt4', 'gpt-5o', 'GPT-6 Astra', 'Gemini 3.8 Flash', 'Opus 5.5', 'astra',
                                 'ASTRA', 'gemini 3.8 pro', 'gpt-5 mini', 'Sonnet5.5', 'codex.2'])
def test_name_with_version_and_model_word_is_one_mask(raw):
    assert SH.clean_reason(raw) == '[judge]'


def test_claude_opus_version():
    assert SH.clean_reason('Claude Opus 5.5') == '[judge] [judge]'
    assert SH.clean_reason('Gemini 3.8 Flash (High) said so') == '[judge] (High) said so'


def test_shuffled_reasons_replay_and_missing():
    got = SH.shuffled_reasons('J-001|ISO 10993-5', ['A is mandatory', None, '   '])
    want = ['A is [label]', SH.NO_REASON, SH.NO_REASON]
    random.Random('S10b-v1|referee-order|J-001|ISO 10993-5').shuffle(want)
    assert got == want
    assert SH.shuffled_reasons('J-001|ISO 10993-5', ['A is mandatory', None, '   ']) == got


# ---- referee set (Q9), unit level ----------------------------------------------------------------------
def mrow(cid, routed, final, labels=(M, M, M)):
    j, it = cid.split('|')
    return dict(cell_id=cid, j_id=j, item=it, routed=routed, final=final, labels=dict(zip('ABC', labels)))


def test_referee_set_sources_and_audit_b_disagreement():
    rows = [mrow('J-001|a', True, None), mrow('J-001|b', True, None), mrow('J-002|a', False, M),
            mrow('J-002|b', False, R), mrow('J-003|a', False, N)]
    rank = {r['cell_id']: (r['j_id'], r['item']) for r in rows}
    audit_a = {'J-001|a': N, 'J-003|a': N}
    audit_b = {'J-002|a': R, 'J-002|b': R}                  # J-002|a disagrees with jury M, J-002|b agrees
    ref, counts = SH.referee_set(rows, audit_a, ['J-002|a', 'J-002|b'], audit_b, rank)
    by = {c['cell_id']: c for c in ref}
    assert list(by) == ['J-001|a', 'J-001|b', 'J-002|a']
    assert by['J-001|a']['pass1_source'] == 'audit_a' and by['J-001|a']['pass1_label'] == N
    assert by['J-001|b']['pass1_source'] == 'sheet' and by['J-001|b']['pass1_label'] is None
    assert by['J-002|a'] == dict(cell_id='J-002|a', j_id='J-002', item='a', why='audit_b_disagreement',
                                 pass1_source='audit_b', pass1_label=R)
    assert counts == dict(cells=3, routed=2, audit_b_disagreement=1, source_audit_a=1, source_audit_b=1,
                          source_sheet=1)
    with pytest.raises(PermissionError):
        SH.pass1_table(ref, {})
    assert SH.pass1_table(ref, {'J-001|b': M}) == [('J-001|a', 'audit_a', N), ('J-001|b', 'sheet', M),
                                                    ('J-002|a', 'audit_b', R)]


# ---- CLI flow on a fake repo ---------------------------------------------------------------------------
def read_csv(path):
    return list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))


def fill_csv(path, fields, label_of, key='cell_id'):
    rows = read_csv(path)
    for r in rows:
        cid = r['cell_id'] if key == 'cell_id' else f'{r["j_id"]}|{r["item"]}'
        r['label'] = label_of(cid)
        r['timestamp'] = '2026-12-01T00:00:00+00:00'
    FK.write_csv(path, fields, rows, bom_crlf=True)


def sheets_dir(root):
    return root / SH.SHEETS_REL


def blind_sheet_ok(md, csv_text):
    for word in ('low', 'unanimous', 'jury', 'judge', 'Judge', 'position', 'routed', 'reason for selection'):
        assert word not in md.replace('one-line reason', ''), word
    assert 'Quasar gadget' in md


@pytest.mark.parametrize('draw,sheet,files', [('audit-b', 'audit-b-sheet', SH.FILES['audit_b']),
                                              ('retest', 'retest-sheet', SH.FILES['retest'])])
def test_blind_sheets(tmp_path, draw, sheet, files):
    root = FK.build_repo(tmp_path)
    assert D.run(root, draw, out=lambda s: None) == 0
    lines = []
    assert SH.run(root, sheet, out=lines.append) == 0
    md = (sheets_dir(root) / files[0]).read_text(encoding='utf-8')
    rows = read_csv(sheets_dir(root) / files[1])
    cells = json.loads((root / D.DRAWS_REL / D.OUT[draw]).read_text(encoding='utf-8'))['cells']
    assert sorted(f'{r["j_id"]}|{r["item"]}' for r in rows) == sorted(c['cell_id'] for c in cells)
    assert list(rows[0]) == list(SH.LABEL_FIELDS) and all(r['label'] == '' for r in rows)
    blind_sheet_ok(md, None)
    assert [l for l in lines if 'sha256_lf=' in l][0].endswith(normalised_sha((sheets_dir(root) / files[0]).read_bytes()))
    assert SH.run(root, sheet, out=lambda s: None) == 3


def flow(tmp_path):
    """Fake repo + audit-B draw/sheet filled (every 2nd audit-B cell disagrees with the jury)."""
    root = FK.build_repo(tmp_path)
    assert D.run(root, 'audit-b', out=lambda s: None) == 0
    assert SH.run(root, 'audit-b-sheet', out=lambda s: None) == 0
    rows = {r['cell_id']: r for r in FK.merged_rows(root)}
    cells = [c['cell_id'] for c in json.loads((root / D.DRAWS_REL / 'audit_b.json').read_text(encoding='utf-8'))['cells']]
    flip = set(cells[::2])

    def ab_label(cid):
        f = rows[cid]['final']
        return ({M: R, R: N, N: M}[f]) if cid in flip else f
    fill_csv(sheets_dir(root) / SH.FILES['audit_b'][1], SH.LABEL_FIELDS, ab_label, key='j_item')
    return root, rows, flip


def test_referee_pass1_reuses_blind_labels_and_sheet_is_blind(tmp_path):
    root, rows, flip = flow(tmp_path)
    assert flip, 'fake needs audit-B cells'
    lines = []
    assert SH.run(root, 'referee-pass1', out=lines.append) == 0
    ref = json.loads((sheets_dir(root) / SH.SET_NAME).read_text(encoding='utf-8'))
    by = {c['cell_id']: c for c in ref['cells']}
    audit_a = {f'{r["j_id"]}|{r["item"]}' for r in FK.audit_rows()}
    routed = {cid for cid, r in rows.items() if r['routed']}
    assert {c for c, v in by.items() if v['why'] == 'routed'} == routed
    assert {c for c, v in by.items() if v['why'] == 'audit_b_disagreement'} == flip
    for cid, c in by.items():
        want = 'audit_a' if cid in audit_a else ('audit_b' if cid in flip else 'sheet')
        assert c['pass1_source'] == want
    assert ref['counts']['source_audit_a'] > 0 and ref['counts']['source_audit_b'] == len(flip) > 0
    sheet_rows = read_csv(sheets_dir(root) / SH.FILES['pass1'][1])
    assert [r['cell_id'] for r in sheet_rows] == [c for c, v in by.items() if v['pass1_source'] == 'sheet']
    assert list(sheet_rows[0]) == list(SH.REFEREE_FIELDS)
    md = (sheets_dir(root) / SH.FILES['pass1'][0]).read_text(encoding='utf-8')
    assert not LABEL_WORD.search(md) and 'Judge' not in md and 'says' not in md and 'confidence' not in md
    assert 'unsure_guideline_gap' in md
    text = '\n'.join(lines)
    assert 'J-0' not in text and 'mandatory' not in text
    assert SH.run(root, 'referee-pass1', out=lambda s: None) == 3                   # write-once


def test_referee_pass1_needs_all_audit_b_labels(tmp_path):
    root = FK.build_repo(tmp_path)
    assert D.run(root, 'audit-b', out=lambda s: None) == 0
    assert SH.run(root, 'audit-b-sheet', out=lambda s: None) == 0
    assert SH.run(root, 'referee-pass1', out=lambda s: None) == 1
    assert not (sheets_dir(root) / SH.SET_NAME).exists()


def test_referee_pass2_lock_reasons_and_tamper(tmp_path):
    root, rows, flip = flow(tmp_path)
    assert SH.run(root, 'referee-pass1', out=lambda s: None) == 0
    lines = []
    assert SH.run(root, 'referee-pass2', out=lines.append) == 1                     # pass 1 not filled
    assert not (sheets_dir(root) / SH.LOCK_NAME).exists()
    fill_csv(sheets_dir(root) / SH.FILES['pass1'][1], SH.REFEREE_FIELDS, lambda cid: M)
    assert SH.run(root, 'referee-pass2', out=lines.append) == 0
    lock = json.loads((sheets_dir(root) / SH.LOCK_NAME).read_text(encoding='utf-8'))
    ref = json.loads((sheets_dir(root) / SH.SET_NAME).read_text(encoding='utf-8'))['cells']
    assert lock['cells'] == len(ref) and sum(lock['by_source'].values()) == len(ref)
    assert SH.verify_pass1_lock(root)
    md = (sheets_dir(root) / SH.FILES['pass2'][0]).read_text(encoding='utf-8')
    assert not LABEL_WORD.search(md)
    assert 'Judge A' not in md and 'Judge B' not in md and 'Judge C' not in md   # names only inside reasons
    assert 'confidence' not in md and '->' not in md and 'section 4.' not in md
    res = FK.judge_results()
    one = ref[0]
    want = SH.shuffled_reasons(one['cell_id'], [
        next(c['reason'] for c in res[j][one['j_id']]['cells'] if c['item'] == one['item']) for j in 'ABC'])
    block = md.split(f'. {one["item"]}\n', 1)[1].split('\n')[:3]
    assert block == [f'   - Reason {k}: {r}' for k, r in enumerate(want, start=1)]
    assert len(read_csv(sheets_dir(root) / SH.FILES['pass2'][1])) == len(ref)
    fill_csv(sheets_dir(root) / SH.FILES['pass1'][1], SH.REFEREE_FIELDS, lambda cid: N)   # change pass 1
    with pytest.raises(PermissionError):
        SH.verify_pass1_lock(root)
    assert SH.run(root, 'referee-pass2', out=lambda s: None) == 3
