"""S8 D5r: the whole Part D -> E chain end to end on FAKE data (offline, 0 API calls).

validate -> freeze judges -> audit sample/order/labels/freeze -> merge stages 1-2 -> referee (tool save, no GUI)
-> merge stage 3 -> qrels manifest -> analysis gate. Covers judge B's real file quirks (BOM, CRLF, quoted
newlines, "none" spans, resumed files out of order, " 2" grades), judge exclusions (Addendum 2 note 3 section 3),
the primary audit kappa without two-judge-rule items (note 3 section 4) and write-once behaviour.

Every test runs under a poison guard: opening any sealed path (pool_key / item maps / audit labels by name, the
real data/runs/azure_openai/s8 tree, the judge kits or the calibration archive) fails the test. All text is
invented ("Clause about widget labelling ...").
"""
import builtins
from contextlib import contextmanager
import csv
from datetime import datetime, timedelta, timezone
import io
import itertools
import json
from pathlib import Path
import random
import shutil
from types import SimpleNamespace

import pytest

from retrieval_eval import qrels as qrels_io
from retrieval_eval import s8_analysis as an
from retrieval_eval import s8_judge_check as jc
from retrieval_eval import s8_judging as jg
from retrieval_eval import s8_label_tool as lt
from retrieval_eval import s8_labels as lb
from retrieval_eval.s8_runs import ROOT, SHEET_COLUMNS, bytes_sha, jsonl_text, sheet_csv
from tests.test_s8_analysis import world as analysis_world

NOW = datetime(2026, 10, 23, 9, 0, 0, tzinfo=timezone.utc)
REAL_OPEN = io.open  # saved before the guard: plants poison files and reads the fake item map in assertions
SEALED_NAMES = {'pool_key.jsonl', 'item_map.jsonl', 'practice_item_map.jsonl', 'labels_shyam_audit.csv'}
SEALED_PREFIXES = [str(ROOT / 'data' / 'runs' / 'azure_openai' / 's8').lower(), 'd:\\s8_judge', 'd:\\s8_calib_archive']
AUDIT_LABELS = 'labels_auditor_fake.csv'  # never the real file name (that one is sealed)
LEAK_MARKERS = ('RATIONALE-', 'ASKS-', 'shall print', 'Partial only')
RULES = ('grades: fake rule text about widgets\njudge_text_not_graph: fake rule text\n'
         'jurisdiction: fake rule text\nswap: fake rule text')
STATES = {0: 'unrelated', 1: 'mentions_only', 2: 'partial', 3: 'answers'}


def _sealed(file):
    if isinstance(file, int):
        return False
    path = Path(file)
    text = str(path.resolve()).lower()
    return path.name in SEALED_NAMES or any(text.startswith(p) for p in SEALED_PREFIXES)


@contextmanager
def sealed_guard():
    real_builtin, real_io = builtins.open, io.open

    def guard(real):
        def wrapped(file, *a, **kw):
            assert not _sealed(file), f'sealed path opened: {file}'
            return real(file, *a, **kw)
        return wrapped
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(builtins, 'open', guard(real_builtin))
        mp.setattr(io, 'open', guard(real_io))
        yield


@pytest.fixture(autouse=True)
def poison():
    with sealed_guard():
        yield


@contextmanager
def unique_stamps():
    """Validate reports are named by the UTC second; a fast test would reuse a second."""
    counter = itertools.count()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(jc, 'utc_stamp', lambda now=None: f'20261023T09{next(counter):04d}Z')
        yield


# ---------------------------------------------------------------- fake world
def item_id(qid, k):
    return f'S8-e2e-{qid.lower()}-{k:02d}'


def clause_text(qid, k):
    return (f'Clause about widget labelling {qid}-{k}. The maker shall print the batch code on box {qid}-{k}. '
            'NOTE: gadget trays are out of scope.')


def span_of(qid, k):
    return f'The maker shall print the batch code on box {qid}-{k}'


def consensus(grades):
    """Same side for every judge -> median (three) / lower (two); a split -> None."""
    return (sorted(grades)[(len(grades) - 1) // 2] if len(grades) == 3 else min(grades)) \
        if len({g >= 2 for g in grades}) == 1 else None


def judge_row(judge, iid, grade, span, **kw):
    return dict(item_id=iid, query_asks=kw.get('asks', f'ASKS-{judge}-{iid}'), clause_states=STATES[int(grade)],
                jurisdiction_match='not_applicable', evidence_span=span, grade=str(grade), unsure='no',
                note=kw.get('note', f'RATIONALE-{judge}-{iid}'))


def csv_bytes(rows, *, bom=False, crlf=False, fields=lb.LABEL_FIELDS):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(fields), lineterminator='\r\n' if crlf else '\n')
    writer.writeheader()
    writer.writerows(rows)
    return (('\ufeff' if bom else '') + stream.getvalue()).encode('utf-8')


def write_plain(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with REAL_OPEN(path, 'wb') as f:
        f.write(data)


def build_world(base, spec, *, codex_b=True, b_tweak=None, b_drop=(), b_bad=(), texts=None, judge_kw=None,
                queries=None, cids=None, default_kw=None):
    """A run folder shaped like data/runs/.../s8/c2 plus three kits, all under `base`.
    spec = {qid: [(gA, gB, gC), ...]}; texts = {item_id: (title, text, span)}; judge_kw = {(judge, item_id): kw};
    cids = {item_id: clause_id}; default_kw(judge, item_id) -> kw for the other judge rows."""
    base = Path(base)
    run = base / 'run'
    w = SimpleNamespace(base=base, run=run, sheet=run / 'sheet' / 's8_sheet.csv', per_query=run / 'sheet' / 'per_query',
                        labels=run / 'labels', audit=run / 'audit', clauses=base / 'clauses.json',
                        kits={j: base / 'kits' / j for j in 'ABC'}, rows=[], grades={}, span={}, qids=list(spec))
    w.out = {j: k / 'output' for j, k in w.kits.items()}
    texts, judge_kw = texts or {}, judge_kw or {}
    for qid, triples in spec.items():
        for k, g in enumerate(triples):
            iid = item_id(qid, k)
            title, text, span = texts.get(iid, (f'Widget labelling clause {qid}-{k}', clause_text(qid, k),
                                                span_of(qid, k)))
            w.rows.append({c: '' for c in SHEET_COLUMNS} | dict(
                item_id=iid, qid=qid, query=(queries or {}).get(qid, f'fake query about widget boxes ({qid})'),
                narrative_grade3='states what a widget box label must show',
                narrative_grade2='labelling of widgets in general', narrative_grade1_traps='gadget trays',
                common_rules=RULES if k == 0 else '', source_doc='fake-doc',
                clause_id=(cids or {}).get(iid, f'fake-doc:{qid}.{k}'),
                title=title, text=text))
            w.grades[iid], w.span[iid] = tuple(g), span
    w.by_id = {r['item_id']: r for r in w.rows}
    write_plain(w.sheet, sheet_csv(w.rows).encode('utf-8'))
    for qid in spec:
        write_plain(w.per_query / f's8_sheet_{qid}.csv', sheet_csv([r for r in w.rows if r['qid'] == qid]).encode('utf-8'))
    write_plain(run / 'sheet' / 'item_map.jsonl', jsonl_text(
        [dict(item_id=r['item_id'], qid=r['qid'], clause_id=r['clause_id']) for r in w.rows]).encode('utf-8'))
    write_plain(run / 'pool_key.jsonl', b'{"system": "SECRET"}\n')  # present, must never be read
    write_plain(w.clauses, json.dumps([dict(id=r['clause_id'], doc='fake-doc', title=r['title'], text=r['text'])
                                       for r in w.rows]).encode('utf-8'))
    w.labels.mkdir(parents=True, exist_ok=True)
    w.audit.mkdir(parents=True, exist_ok=True)
    for n, judge in enumerate('ABC'):
        (w.kits[judge] / 'eval').mkdir(parents=True, exist_ok=True)
        for qid in spec:
            shutil.copyfile(w.per_query / f's8_sheet_{qid}.csv', w.kits[judge] / 'eval' / f's8_sheet_{qid}.csv')
            rows = []
            for r in (r for r in w.rows if r['qid'] == qid):
                iid = r['item_id']
                if judge == 'B' and iid in b_drop:
                    continue
                kw = judge_kw.get((judge, iid)) or (default_kw(judge, iid) if default_kw else {})
                row = judge_row(judge, iid, w.grades[iid][n], w.span[iid], **kw)
                if judge == 'B' and iid in b_bad:
                    row['grade'] = 'high'
                rows.append(row)
            codex = judge == 'B' and codex_b
            if codex and b_tweak:
                rows = b_tweak(qid, rows)
            write_plain(w.out[judge] / f'labels_s8_sheet_{qid}.csv', csv_bytes(rows, bom=codex, crlf=codex))
    return w


def validate_and_freeze(w, now=NOW, judges='ABC', **kw):
    """Function path (W2 / side worlds); W1 uses the CLIs."""
    records = {}
    for n, judge in enumerate(judges):
        t = now + timedelta(minutes=n)
        records[judge] = jc.freeze_judge(judge, f'fake-model-{judge}', w.kits[judge], w.per_query, w.labels, now=t,
                                         **(kw if judge == 'B' else {}))[0]
    return records


def audit_setup(w, n, pick, *, unsure=(), cli_freeze=False):
    """Blind audit: sample (all items here), fixed order, the first n labelled with the tool, then frozen.
    pick(item_id, consensus) -> the auditor's grade."""
    jg.run_audit_sample(w.sheet, w.audit)
    jg.run_audit_order(w.audit, qid_order=w.qids)
    order = jg.read_order(w.audit / jg.AUDIT_ORDER)
    app = lt.LabelApp(w.audit / 'audit_sheet.csv', w.audit / AUDIT_LABELS, 'audit', order=w.audit / jg.AUDIT_ORDER)
    for iid in order[:n]:
        g = pick(iid, consensus(w.grades[iid]))
        app.save(dict(item_id=iid, grade=str(g), clause_states=STATES[g], jurisdiction_match='not_applicable',
                      unsure='yes' if iid in unsure else 'no', evidence_span='', note=''))
    if cli_freeze:
        assert jg.main(['s8-freeze-audit', '--labels', str(w.audit / AUDIT_LABELS), '--folder', str(w.audit),
                        '--order', str(w.audit / jg.AUDIT_ORDER)]) == 0
    else:
        jg.freeze_audit(w.audit / AUDIT_LABELS, w.audit, w.audit / jg.AUDIT_ORDER, now=NOW, minimum=n)
    return order[:n]


def merge(w, out=None, **kw):
    kw.setdefault('bootstrap', 200)
    return lb.run_merge(sheet=w.sheet, a_files=lb.judge_files(w.out['A']), b_files=lb.judge_files(w.out['B']),
                        c_files=lb.judge_files(w.out['C']), out=out or w.labels, **kw)


def audit_kw(w, referee=None):
    return dict(audit_file=w.audit / AUDIT_LABELS, freeze_path=w.audit / 'audit_freeze.json', referee_file=referee)


def referee_app(w):
    return lt.LabelApp(w.labels / 'referee_sheet.csv', w.labels / 'labels_referee.csv', 'referee',
                       disputes=w.labels / 'disputes.csv', freeze=w.audit / 'audit_freeze.json')


def ref_payload(iid, grade, reason_code, unsure='no', quote='', rule=''):
    return dict(item_id=iid, grade=str(grade), clause_states=STATES[grade], jurisdiction_match='not_applicable',
                unsure=unsure, evidence_span='', note='', reason_code=reason_code, override_quote=quote,
                override_rule=rule)


def kappa(pairs):
    """Independent binary Cohen kappa from (auditor_relevant, consensus_relevant) pairs."""
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return (po - pe) / (1 - pe)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def assert_no_leak(text):
    for marker in LEAK_MARKERS:
        assert marker not in text, f'label text printed: {marker}'


# ---------------------------------------------------------------- W1: 3 queries, 25 items, the full chain via CLIs
W1_SPEC = {
    'Q1': [(2, 3, 3), (0, 1, 1), (1, 0, 0), (3, 2, 2), (1, 2, 2), (3, 3, 3), (0, 0, 0), (2, 2, 3)],
    'Q2': [(3, 3, 2), (0, 0, 1), (2, 1, 1), (1, 1, 1), (3, 3, 3), (0, 1, 0), (2, 2, 2), (1, 0, 1)],
    'Q10': [(3, 2, 3), (0, 0, 0), (0, 3, 3), (2, 3, 2), (1, 1, 0), (3, 3, 3), (0, 1, 1), (2, 2, 3), (0, 0, 1)],
}
SPLIT_REF, SPLIT_GAP, SPLIT_UNSURE = item_id('Q1', 4), item_id('Q2', 2), item_id('Q10', 2)
B_NOTE = 'Partial only, not "direct";\r\nsee rule 7, line 2'
B_SPAN_MULTILINE = 'The maker shall\r\nprint the batch code'
SPACE_GRADE = item_id('Q10', 7)  # B wrote " 2"
NONE_SPANS = {item_id('Q1', 1): 'none', item_id('Q1', 2): 'none', item_id('Q1', 3): 'None',
              item_id('Q10', 0): 'none', item_id('Q10', 1): 'none'}
W1_PREFIX = 20
CASE1_ITEMS = {item_id('Q1', k) for k in range(4)} | {SPACE_GRADE}


def w1_b_tweak(qid, rows):
    """Judge B as written by Codex on Windows (BOM + CRLF added by build_world)."""
    for r in rows:
        r['evidence_span'] = NONE_SPANS.get(r['item_id'], r['evidence_span'])
        if r['item_id'] == SPLIT_REF:
            r['note'] = B_NOTE  # quoted field with a comma, quotes and a CRLF inside
        if r['item_id'] == item_id('Q1', 0):
            r['evidence_span'] = B_SPAN_MULTILINE
        if r['item_id'] == SPACE_GRADE:
            r['grade'] = ' 2'
    return rows[4:] + rows[:4] if qid == 'Q10' else rows  # the resumed Q10 chat appended out of sheet order


def run_cli(capsys, main, argv):
    code = main(argv)
    out = capsys.readouterr().out
    assert_no_leak(out)
    return code, out


@pytest.fixture(scope='module')
def w1(tmp_path_factory):
    capsys_lines = []

    class Cap:  # module scope cannot use capsys: capture stdout by hand
        def readouterr(self):
            value = buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
            capsys_lines.append(value)
            return SimpleNamespace(out=value)
    buffer = io.StringIO()
    cap = Cap()
    with sealed_guard(), unique_stamps(), pytest.MonkeyPatch.context() as mp:
        mp.setattr('sys.stdout', buffer)
        w = build_world(tmp_path_factory.mktemp('w1'), W1_SPEC, b_tweak=w1_b_tweak)
        args = lambda j: ['--judge', j, '--root', str(w.kits[j]), '--per-query', str(w.per_query),
                          '--labels-dir', str(w.labels)]
        w.validate_out = {j: run_cli(cap, jc.main, ['s8-validate-judge', *args(j), '--require-all']) for j in 'ABC'}
        w.validate_reports = sorted(w.labels.glob('validate_*.json'))
        w.freeze_out = {j: run_cli(cap, jc.main, ['s8-freeze-judge', *args(j), '--model', f'fake-model-{j}'])
                        for j in 'ABC'}
        # audit: the auditor agrees with the consensus except D_keep (says 0) and D_over (says 3)
        jg.run_audit_sample(w.sheet, w.audit)
        jg.run_audit_order(w.audit, qid_order=w.qids)
        prefix = jg.read_order(w.audit / jg.AUDIT_ORDER)[:W1_PREFIX]
        with_cons = [i for i in prefix if consensus(w.grades[i]) is not None]
        free = [i for i in with_cons if i not in CASE1_ITEMS]  # case 1 items keep their median
        w.d_keep = next(i for i in free if consensus(w.grades[i]) >= 2)
        w.d_over = next(i for i in free if consensus(w.grades[i]) <= 1)
        w.unsure_item = next(i for i in reversed(free) if i not in (w.d_keep, w.d_over))
        w.prefix, w.with_cons = prefix, with_cons

        def pick(iid, cons):
            if iid == w.d_keep:
                return 0
            if iid == w.d_over:
                return 3
            return 1 if cons is None else cons
        w.shyam = {i: pick(i, consensus(w.grades[i])) for i in prefix}
        app = lt.LabelApp(w.audit / 'audit_sheet.csv', w.audit / AUDIT_LABELS, 'audit', order=w.audit / jg.AUDIT_ORDER)
        for iid in prefix:
            g = w.shyam[iid]
            app.save(dict(item_id=iid, grade=str(g), clause_states=STATES[g], jurisdiction_match='not_applicable',
                          unsure='yes' if iid == w.unsure_item else 'no', evidence_span='', note=''))
        jg.freeze_audit(w.audit / AUDIT_LABELS, w.audit, w.audit / jg.AUDIT_ORDER, now=NOW, minimum=W1_PREFIX)
        merge_args = ['s8-merge-labels', '--a', str(w.out['A']), '--b', str(w.out['B']), '--c', str(w.out['C']),
                      '--sheet', str(w.sheet), '--audit-dir', str(w.audit), '--out', str(w.labels)]
        w.stage1 = run_cli(cap, lb.main, merge_args)
        w.stage2 = run_cli(cap, lb.main, [*merge_args, '--audit', str(w.audit / AUDIT_LABELS)])
        # referee via the tool's save function (no GUI)
        ref = referee_app(w)
        w.ref_ids = [ref.ids[k] for k in range(len(ref.ids))]
        w.ref_view = {i: ref.item(k) for k, i in enumerate(ref.ids)}
        w.ref_order = {i: ref.judge_order(i) for i in ref.ids}  # server-side mapping, never on screen
        text = w.by_id[w.d_over]['text']
        w.bad_saves = []
        for bad in (dict(quote='The maker shall paint the batch code', rule='5'), dict(quote=span_of('Q1', 0), rule='5'),
                    dict(quote='The maker shall print', rule='17'), dict(quote='The maker shall print', rule='rule 5')):
            with pytest.raises(ValueError) as e:
                ref.save(ref_payload(w.d_over, 3, 'guideline_gap', **bad))
            w.bad_saves.append(str(e.value))
        with pytest.raises(ValueError) as e:
            ref.save(ref_payload(SPLIT_REF, 2, ''))
        w.bad_saves.append(str(e.value))
        w.decisions = {SPLIT_REF: ref_payload(SPLIT_REF, 2, 'clear_reading'),
                       SPLIT_GAP: ref_payload(SPLIT_GAP, 2, 'guideline_gap'),
                       SPLIT_UNSURE: ref_payload(SPLIT_UNSURE, 0, 'unclear_text', unsure='yes'),
                       w.d_keep: ref_payload(w.d_keep, 0, 'clear_reading'),
                       w.d_over: ref_payload(w.d_over, 3, 'guideline_gap',
                                             quote='  The maker shall print\n   the batch code ', rule='5')}
        assert 'The maker shall print the batch code' in text
        for payload in w.decisions.values():
            ref.save(payload)
        w.stage3 = run_cli(cap, lb.main, [*merge_args, '--audit', str(w.audit / AUDIT_LABELS),
                                          '--referee', str(w.labels / 'labels_referee.csv')])
    w.stdout = ''.join(capsys_lines)
    return w


def test_w1_cli_outputs_print_counts_only(w1):
    assert w1.stage1[0] == w1.stage2[0] == w1.stage3[0] == 0
    assert all(code == 0 for code, _ in w1.validate_out.values())
    assert_no_leak(w1.stdout)
    for name in ('agreement_abc.json', 'audit_check.json', 'qrels_meta.json'):
        assert_no_leak((w1.labels / name).read_text(encoding='utf-8'))


def test_case1_same_side_gives_the_median(w1):
    got = {r['clause_id']: (r['grade'], r['judge']) for r in qrels_io.read(w1.labels / 'qrels.tsv')}
    cid = lambda q, k: w1.by_id[item_id(q, k)]['clause_id']
    assert got[cid('Q1', 0)] == (3, 'ABC_median')  # 2,3,3
    assert got[cid('Q1', 1)] == (1, 'ABC_median')  # 0,1,1
    assert got[cid('Q1', 2)] == (0, 'ABC_median')  # 1,0,0
    assert got[cid('Q1', 3)] == (2, 'ABC_median')  # 3,2,2
    assert got[cid('Q10', 7)] == (2, 'ABC_median')  # 2," 2",3: B's " 2" read as 2


def test_case2_splits_go_to_the_referee_with_all_three_rationales(w1):
    agr = read_json(w1.labels / 'agreement_abc.json')
    assert agr['mode'] == 'three_judge' and agr['threshold_disputes'] == 3 and agr['accepted'] == 22
    assert set(agr['pairs']) == {'A-B', 'A-C', 'B-C'} and agr['fleiss']['n'] == 25
    disputes = {d['item_id']: d for d in lb.read_disputes(w1.labels / 'disputes.csv')}
    splits = [i for i, d in disputes.items() if d['reason'] == 'threshold_split']
    assert splits == [SPLIT_REF, SPLIT_GAP, SPLIT_UNSURE]
    for iid in splits:
        d = disputes[iid]
        for j in 'abc':
            assert d[f'{j}_grade'] == str(w1.grades[iid]['abc'.index(j)])
            assert d[f'{j}_query_asks'] == f'ASKS-{j.upper()}-{iid}'
            assert d[f'{j}_note'] == (B_NOTE if (iid, j) == (SPLIT_REF, 'b') else f'RATIONALE-{j.upper()}-{iid}')
    view = w1.ref_view[SPLIT_REF]  # what the referee screen receives (blinded: Judge 1-3)
    shown = {letter: view['judges'][str(n)] for n, letter in enumerate(w1.ref_order[SPLIT_REF], 1)}
    assert view['reason'] == 'threshold_split' and set(view['judges']) == {'1', '2', '3'}
    assert shown['b']['note'] == B_NOTE and shown['c']['evidence_span'] == w1.span[SPLIT_REF]
    assert (w1.labels / 'referee_ids.txt').read_text(encoding='utf-8').split() == w1.ref_ids


def test_case3_4_5_judge_b_codex_file_validates(w1):
    raw = (w1.out['B'] / 'labels_s8_sheet_Q1.csv').read_bytes()
    assert raw.startswith(b'\xef\xbb\xbf') and b'\r\n' in raw and b'"Partial only, not ""direct"";\r\nsee rule 7' in raw
    report = read_json(next(p for p in w1.validate_reports if p.name.startswith('validate_B_')))
    assert report['overall'] == 'PASS' and report['counts']['PASS'] == 3
    q1, q10 = report['files']['Q1'], report['files']['Q10']
    assert q1['status'] == q10['status'] == 'PASS' and q1['quote_not_found'] == 0  # multi-line span found
    assert (q1['none_spans'], q10['none_spans']) == (3, 2)  # case 4: warning only
    assert q10['warnings']['order_differs'] is True and q1['warnings']['order_differs'] is False  # case 5
    assert q10['grade_counts']['2'] == 2  # item 0 and the " 2" of item 7
    code, out = w1.validate_out['B']
    assert code == 0 and 'OVERALL judge B: PASS' in out and 'order_differs' in out
    frozen = read_json(w1.labels / 'judge_B_freeze.json')
    assert frozen['counts']['labels_s8_sheet_Q1.csv']['none_spans'] == 3 and 'excluded' not in frozen
    assert frozen['files']['labels_s8_sheet_Q1.csv'] == bytes_sha(w1.out['B'] / 'labels_s8_sheet_Q1.csv')


def test_case6_audit_pass_kappa_matches_hand_count(w1):
    check = read_json(w1.labels / 'audit_check.json')
    pairs = [(w1.shyam[i] >= 2, consensus(w1.grades[i]) >= 2) for i in w1.with_cons]
    assert check['flag'] == 'PASS' and check['audit_items'] == W1_PREFIX
    assert check['on_accepted'] == len(w1.with_cons) and check['on_two_judge_rule'] == 0
    assert check['on_disputes'] == W1_PREFIX - len(w1.with_cons) and check['disagreements'] == 2
    assert check['kappa_binary'] == round(kappa(pairs), 4) >= 0.60
    sure = [(w1.shyam[i] >= 2, consensus(w1.grades[i]) >= 2) for i in w1.with_cons if i != w1.unsure_item]
    assert check['secondary']['n'] == len(sure) and check['secondary']['kappa_binary'] == round(kappa(sure), 4)


def test_case6_audit_stop_writes_no_referee_list_and_no_qrels(tmp_path):
    w = build_world(tmp_path, W1_SPEC, b_tweak=w1_b_tweak)
    with unique_stamps():
        validate_and_freeze(w)
    audit_setup(w, W1_PREFIX, lambda iid, cons: 1 if cons is None else (0 if cons >= 2 else 3))  # all flipped
    s = merge(w, **audit_kw(w))
    assert s['audit']['flag'] == 'STOP' and s['stopped'] == 'audit STOP: no referee list, no qrels'
    assert s['audit']['kappa_binary'] < 0.60
    for name in ('disputes.csv', 'referee_sheet.csv', 'referee_ids.txt', 'qrels.tsv', 'qrels_meta.json'):
        assert not (w.labels / name).exists()
    with pytest.raises(PermissionError, match='qrels.tsv missing'):
        an.write_qrels_manifest(w.run, tmp_path / 'manifest.json', repo=ROOT, now=NOW)
    (w.audit / AUDIT_LABELS).write_bytes((w.audit / AUDIT_LABELS).read_bytes() + b'\r\n')
    with pytest.raises(PermissionError, match='audit labels file differs'):  # labels changed after the freeze
        merge(w, out=tmp_path / 'other', freeze_dir=w.labels, **audit_kw(w))


def test_case7_audit_disagreements_kept_or_overturned(w1):
    disputes = {d['item_id']: d['reason'] for d in lb.read_disputes(w1.labels / 'disputes.csv')}
    assert disputes[w1.d_keep] == disputes[w1.d_over] == 'audit_disagrees'
    meta = read_json(w1.labels / 'qrels_meta.json')
    assert meta['categories']['auditor_disagrees_kept'] == 1 and meta['categories']['auditor_override_evidence'] == 1
    assert meta['override_flipped_side'] == 1
    got = {r['clause_id']: (r['grade'], r['judge']) for r in qrels_io.read(w1.labels / 'qrels.tsv')}
    assert got[w1.by_id[w1.d_keep]['clause_id']] == (consensus(w1.grades[w1.d_keep]), 'ABC_median')  # no quote/rule
    assert got[w1.by_id[w1.d_over]['clause_id']] == (3, 'referee')  # quote found + rule 5
    # the tool refused: a quote not in the clause, a quote from another clause, rule 17, "rule 5", no reason_code
    assert [m.split(': ', 1)[1].split(' (')[0] for m in w1.bad_saves] == [
        'override_quote not found in the clause text', 'override_quote not found in the clause text',
        "override_rule '17' not a guideline rule 1..16", "override_rule 'rule 5' not a guideline rule 1..16",
        "reason_code '' not one of clear_reading|guideline_gap|unclear_text"]


def test_case8_judge_file_changed_after_freeze_is_refused_by_name(tmp_path, w1):
    b_copy = tmp_path / 'B_output'
    shutil.copytree(w1.out['B'], b_copy)
    target = b_copy / 'labels_s8_sheet_Q2.csv'
    target.write_bytes(target.read_bytes().replace(b'RATIONALE-B', b'RATIONALE-b', 1))  # one byte, as an editor would
    with pytest.raises(PermissionError, match=r'judge B: labels_s8_sheet_Q2.csv differs from judge_B_freeze.json'):
        lb.run_merge(sheet=w1.sheet, a_files=lb.judge_files(w1.out['A']), b_files=lb.judge_files(b_copy),
                     c_files=lb.judge_files(w1.out['C']), out=tmp_path / 'labels', freeze_dir=w1.labels, bootstrap=50)
    assert not (tmp_path / 'labels').exists()


def test_case9_two_judge_fallback_records_its_mode(tmp_path, w1):
    s = merge(w1, out=tmp_path / 'fallback', fallback_reason='fake: C quota exhausted on day 3', freeze_dir=w1.labels)
    agr = read_json(tmp_path / 'fallback' / 'agreement_ab.json')
    assert s['mode'] == agr['mode'] == 'two_judge_fallback' and agr['judges_in_force'] == ['A', 'B']
    assert agr['fallback_reason'] == 'fake: C quota exhausted on day 3' and agr['judges_read'] == ['A', 'B', 'C']
    assert set(agr['secondary_c']) == {'A-C', 'B-C'} and agr['n'] == 25
    ab_splits = [i for i, g in w1.grades.items() if consensus(g[:2]) is None]
    assert agr['threshold_disputes'] == len(ab_splits) and agr['accepted'] == 25 - len(ab_splits)
    with pytest.raises(ValueError, match='needs a reason'):
        merge(w1, out=tmp_path / 'fallback2', fallback_reason='  ', freeze_dir=w1.labels)


def expected_w1(w1):
    final = {}
    for iid, g in w1.grades.items():
        d = w1.decisions.get(iid)
        if consensus(g) is None:
            final[iid] = sorted(g)[1] if d['unsure'] == 'yes' else int(d['grade'])
        elif iid == w1.d_over:
            final[iid] = int(d['grade'])
        else:
            final[iid] = consensus(g)
    return final


def test_case10_referee_then_qrels(w1):
    rows = qrels_io.read(w1.labels / 'qrels.tsv')
    with REAL_OPEN(w1.run / 'sheet' / 'item_map.jsonl', encoding='utf-8') as f:
        item_map = [json.loads(line) for line in f]
    assert len(rows) == len(item_map) == 25
    assert {(r['qid'], r['clause_id']) for r in rows} == {(m['qid'], m['clause_id']) for m in item_map}
    assert all(r['grade'] in (0, 1, 2, 3) and r['qid'] and r['clause_id'] for r in rows)
    by_cid = {r['clause_id']: r['grade'] for r in rows}
    assert {i: by_cid[w1.by_id[i]['clause_id']] for i in w1.grades} == expected_w1(w1)
    meta = read_json(w1.labels / 'qrels_meta.json')
    assert meta['reason_codes'] == dict(clear_reading=2, guideline_gap=2, unclear_text=1)
    assert meta['categories'] == dict(accepted_median=20, accepted_lower=0, dispute_referee=2, dispute_unsure_median=1,
                                      dispute_unsure_lower=0, auditor_disagrees_kept=1, auditor_override_evidence=1)
    assert meta['excluded_by_judge'] == {'A': 0, 'B': 0, 'C': 0} and meta['referee_items'] == 5
    final = expected_w1(w1)
    for j, n in zip('ABC', range(3)):  # per-judge correctness, derived on the 5 referee items
        right = sum((w1.grades[i][n] >= 2) == (final[i] >= 2) for i in w1.decisions)
        assert meta['judge_right'][j] == dict(right=right, judged=5)
    labels = (w1.labels / 'labels_referee.csv').read_bytes()
    assert labels.startswith(b'\xef\xbb\xbf') and labels.count(b'\r\n') == 6  # header + 5 rows, resumable file


def test_case12_write_once_freeze_merge_rerun(tmp_path, w1):
    before = {p.name: bytes_sha(p) for p in w1.labels.iterdir()}
    with pytest.raises(FileExistsError, match='judge_B_freeze.json exists'):
        jc.main(['s8-freeze-judge', '--judge', 'B', '--root', str(w1.kits['B']), '--per-query', str(w1.per_query),
                 '--labels-dir', str(w1.labels), '--model', 'fake-model-B'])
    s = merge(w1, bootstrap=lb.BOOTSTRAP, **audit_kw(w1, w1.labels / 'labels_referee.csv'))
    assert s['stage'] == 3 and set(s['states'].values()) == {'unchanged'}  # same inputs: byte-identical no-op
    changed = tmp_path / 'labels_referee_changed.csv'
    changed.write_bytes((w1.labels / 'labels_referee.csv').read_bytes())
    app = lt.LabelApp(w1.labels / 'referee_sheet.csv', changed, 'referee', disputes=w1.labels / 'disputes.csv',
                      freeze=w1.audit / 'audit_freeze.json')
    app.save(ref_payload(SPLIT_REF, 0, 'clear_reading'))
    with pytest.raises(FileExistsError, match='qrels.tsv exists with different content'):
        merge(w1, bootstrap=lb.BOOTSTRAP, **audit_kw(w1, changed))
    assert {p.name: bytes_sha(p) for p in w1.labels.iterdir() if p.name in before} == before


# ---------------------------------------------------------------- case 11 (W2: world() of test_s8_analysis)
def test_case11_manifest_analysis_gate_and_lock(tmp_path, monkeypatch):
    run_src, queries, grades, _ = analysis_world(tmp_path / 'w2src')
    spec, texts, split_items = {}, {}, []
    for qid in grades:
        triples = []
        for k, (cid, g) in enumerate(sorted(grades[qid].items())):
            triples.append((g, g, g))
        spec[qid] = triples
    # two splits; the referee restores the world's grade so the qrels equal world()'s grades
    for qid, cond, make in (('R1', lambda g: g >= 2, lambda g: (1, g, g)), ('Q1', lambda g: g <= 1, lambda g: (g, g, 2))):
        k = next(k for k, t in enumerate(spec[qid]) if cond(t[0]))
        split_items.append((item_id(qid, k), spec[qid][k][0]))
        spec[qid][k] = make(spec[qid][k][0])
    cids = {item_id(q, k): cid for q in grades for k, cid in enumerate(sorted(grades[q]))}  # the world's clause ids
    w = build_world(tmp_path / 'w2', spec, cids=cids)
    for name in ('runs.jsonl', 'generations.jsonl', 'noob.jsonl'):
        shutil.copyfile(run_src / name, w.run / name)
    with unique_stamps():
        validate_and_freeze(w)
    audit_setup(w, 50, lambda iid, cons: 1 if cons is None else cons, cli_freeze=True)  # CLI: N >= 50
    s = merge(w, **audit_kw(w))
    assert s['audit']['flag'] == 'PASS' and s['referee']['threshold_split'] == 2
    ref = referee_app(w)
    for (iid, g), code in zip(split_items, ('clear_reading', 'unclear_text')):
        ref.save(ref_payload(iid, g, code))
    s = merge(w, **audit_kw(w, w.labels / 'labels_referee.csv'))
    assert s['stage'] == 3 and s['qrels']['pairs'] == 200
    got = qrels_io.grades_by_query(qrels_io.read(w.labels / 'qrels.tsv'))
    assert got == grades
    # s8-qrels-manifest via its CLI, pointed at the fake run folder and a tmp manifest
    manifest = tmp_path / 's8_qrels_manifest.json'
    monkeypatch.setattr(an, 'output_dir', lambda: w.run)
    monkeypatch.setattr(an, 'MANIFEST', manifest)
    assert an.main(['s8-qrels-manifest']) == 0
    record = read_json(manifest)
    assert record['merge_mode'] == 'three_judge' and set(record['files']) >= {
        'qrels', 'qrels_meta', 'audit_check', 'agreement', 'audit_freeze', 'judge_A_freeze', 'judge_B_freeze',
        'judge_C_freeze', 'guideline', 'partD_freeze'}
    folder, shas = an.run_analysis(w.run, queries=queries, clauses_path=tmp_path / 'w2src' / 'clauses.json',
                                   manifest_path=manifest, now=NOW)
    assert folder == w.run / 'analysis' and read_json(folder / 'inputs.json')['gate'] == 'verified'
    assert read_json(folder / 'agreement.json')['agreement']['excluded_by_judge'] == {'A': 0, 'B': 0, 'C': 0}
    qrels = w.labels / 'qrels.tsv'
    original = qrels.read_bytes()
    qrels.write_bytes(original[:-2] + (b'0' if original[-2:-1] != b'0' else b'1') + original[-1:])
    with pytest.raises(PermissionError) as e:
        an.run_analysis(w.run, queries=queries, clauses_path=tmp_path / 'w2src' / 'clauses.json',
                        manifest_path=manifest, now=NOW + timedelta(hours=1))
    assert str(e.value).startswith('Part E is locked until the qrels freeze.') and 'qrels.tsv differs' in str(e.value)
    # case 12: the manifest is write-once; a re-run of the analysis writes a new folder, never over the old one
    with pytest.raises(FileExistsError, match='already frozen'):
        an.main(['s8-qrels-manifest'])
    qrels.write_bytes(original)
    again, shas2 = an.run_analysis(w.run, queries=queries, clauses_path=tmp_path / 'w2src' / 'clauses.json',
                                   manifest_path=manifest, now=NOW + timedelta(hours=2))
    assert again == w.run / 'analysis' / 'run_20261023T110000Z'
    assert {n: bytes_sha(folder / n) for n in shas} == shas


# ---------------------------------------------------------------- cases 13 + 14 (W3: exclusions, 120 items)
def w3_spec():
    return {q: [((k * 3) % 4,) * 3 for k in range(40)] for q in ('Q3', 'Q4', 'Q5')}


def test_case13_14_exclusions_and_primary_kappa(tmp_path, capsys):
    base = tmp_path / 'w3'
    # the audit order needs only ids + qids: compute it first, then plant B's two bad rows
    probe = build_world(tmp_path / 'probe', w3_spec(), codex_b=False)
    jg.run_audit_sample(probe.sheet, probe.audit)
    jg.run_audit_order(probe.audit, qid_order=probe.qids)
    order = jg.read_order(probe.audit / jg.AUDIT_ORDER)
    x_missing, x_invalid = order[0], order[-1]  # x_missing is in the audit prefix (case 14)
    w = build_world(base, w3_spec(), b_drop=(x_missing,), b_bad=(x_invalid,), b_tweak=lambda q, rows: rows)
    excl = tmp_path / 'exclude.txt'
    t = itertools.count(1)
    when = lambda: NOW + timedelta(minutes=next(t))
    freeze_b = lambda **kw: jc.freeze_judge('B', 'fake-model-B', w.kits['B'], w.per_query, w.labels, now=when(), **kw)
    with pytest.raises(PermissionError, match='validation FAIL'):
        freeze_b()  # no exclusion is possible without the flag
    excl.write_text(f'{x_missing}\n{x_invalid}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='go together'):
        freeze_b(exclude_items=excl)
    with pytest.raises(ValueError, match='go together'):
        freeze_b(exclude_items=excl, reason='   ')
    with pytest.raises(SystemExit):  # the CLI refuses the flag without --reason
        jc.main(['s8-freeze-judge', '--judge', 'B', '--root', str(w.kits['B']), '--per-query', str(w.per_query),
                 '--labels-dir', str(w.labels), '--model', 'm', '--exclude-items', str(excl)])
    excl.write_text(f'{x_missing}\nS8-e2e-zz-99\n', encoding='utf-8')
    with pytest.raises(ValueError, match=r'1 unknown item_id\(s\)'):
        freeze_b(exclude_items=excl, reason='fake: re-run failed')
    passing = item_id('Q4', 5)
    excl.write_text(f'{x_missing}\n{passing}\n', encoding='utf-8')
    with pytest.raises(PermissionError, match=rf"1 item\(s\) PASS validation in labels_s8_sheet_Q4.csv.*{passing}"):
        freeze_b(exclude_items=excl, reason='fake: re-run failed')
    excl.write_text(f'{x_missing}\n{x_invalid}\n{passing}\n', encoding='utf-8')
    with pytest.raises(PermissionError, match=r'3 items > 2% of the 120 sheet items \(max 2\)'):
        freeze_b(exclude_items=excl, reason='fake: re-run failed')
    excl.write_text(f'{x_missing}\n{x_missing}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='twice'):
        freeze_b(exclude_items=excl, reason='fake: re-run failed')
    excl.write_text(f'{x_missing}\n', encoding='utf-8')
    with pytest.raises(PermissionError, match='still FAIL once the excluded items are set aside'):
        freeze_b(exclude_items=excl, reason='fake: re-run failed')  # x_invalid still fails
    assert not (w.labels / 'judge_B_freeze.json').exists()
    excl.write_text(f'\n{x_missing}\r\n{x_invalid}\n\n', encoding='utf-8')
    record, _ = freeze_b(exclude_items=excl, reason='fake: two rows still invalid after the one re-run')
    assert record['excluded'] == dict(
        item_ids=sorted([x_missing, x_invalid]), count=2, reason='fake: two rows still invalid after the one re-run',
        by_file={f"labels_s8_sheet_{w.by_id[x_missing]['qid']}.csv": 1,
                 f"labels_s8_sheet_{w.by_id[x_invalid]['qid']}.csv": 1} if w.by_id[x_missing]['qid'] !=
        w.by_id[x_invalid]['qid'] else {f"labels_s8_sheet_{w.by_id[x_missing]['qid']}.csv": 2},
        max_allowed=2, sheet_items=120,
        rule='decided by the other two judges (two-judge rule; Addendum 2 note 3 section 3)')
    assert read_json(w.labels / record['validate_report'])['overall'] == 'FAIL'  # the report it was frozen on
    others = validate_and_freeze(w, now=NOW + timedelta(hours=1), judges='AC')
    assert 'excluded' not in others['A'] and 'excluded' not in others['C']
    assert int(jc.EXCLUDE_MAX * 1482) == 29
    # audit: the auditor disagrees only on x_missing (A+C two-judge rule) -> left out of the primary kappa
    audit_setup(w, 12, lambda iid, cons: (0 if cons >= 2 else 3) if iid == x_missing else cons)
    s = merge(w, **audit_kw(w))
    check = read_json(w.labels / 'audit_check.json')
    assert check['on_two_judge_rule'] == 1 and check['on_accepted'] == 11 and check['kappa_binary'] == 1.0
    assert check['disagreements'] == check['disagreements_on_two_judge_rule'] == 1 and check['flag'] == 'PASS'
    assert check['confusion_2x2_rows_shyam_cols_consensus'][0][1] + \
        check['confusion_2x2_rows_shyam_cols_consensus'][1][0] == 0
    assert s['referee'] == {'audit_disagrees': 1, 'total': 1}
    agr = read_json(w.labels / 'agreement_abc.json')
    assert agr['excluded_by_judge'] == {'A': 0, 'B': 2, 'C': 0} and agr['two_judge_rule_items_missing_row'] == 2
    assert {p: v['n'] for p, v in agr['pairs'].items()} == {'A-B': 118, 'A-C': 120, 'B-C': 118}
    assert agr['fleiss']['n'] == 118 and agr['row_problems']['B'] == dict(missing=0, invalid=0, files={})
    ref = referee_app(w)
    g = w.grades[x_missing][0]
    ref.save(ref_payload(x_missing, 0 if g >= 2 else 3, 'clear_reading'))  # no quote/rule -> consensus stands
    merge(w, **audit_kw(w, w.labels / 'labels_referee.csv'))
    meta = read_json(w.labels / 'qrels_meta.json')
    assert meta['excluded_by_judge'] == {'A': 0, 'B': 2, 'C': 0} and meta['two_judge_rule_items_missing_row'] == 2
    assert meta['judge_right'] == {'A': dict(right=1, judged=1), 'B': dict(right=0, judged=0),
                                   'C': dict(right=1, judged=1)}  # B skipped on its excluded item
    got = {r['clause_id']: (r['grade'], r['judge']) for r in qrels_io.read(w.labels / 'qrels.tsv')}
    for iid in (x_missing, x_invalid):
        assert got[w.by_id[iid]['clause_id']] == (w.grades[iid][0], 'AC_min')
    assert meta['categories']['accepted_lower'] == 1 and meta['categories']['auditor_disagrees_kept'] == 1
    assert_no_leak(capsys.readouterr().out)


def test_case14_old_rule_would_have_counted_the_two_judge_item():
    """Unit view of note 3 section 4: same data, the two-judge item changes nothing in the primary kappa."""
    accepted = {'i1': 3, 'i2': 0, 'i3': 2, 'i4': 0}
    audit = {i: dict(grade=str(g), unsure='no') for i, g in accepted.items()} | {'i4': dict(grade='3', unsure='no')}
    check, disagree = lb.audit_check(audit, accepted, [], two_rule=['i4'], bootstrap=10)
    assert check['on_accepted'] == 3 and check['on_two_judge_rule'] == 1 and check['kappa_binary'] == 1.0
    assert disagree == ['i4'] and check['secondary']['n'] == 3
    check, _ = lb.audit_check(audit, accepted, [], two_rule=[], bootstrap=10)
    assert check['on_accepted'] == 4 and check['kappa_binary'] < 1.0


# ---------------------------------------------------------------- case 15 (resumed Q10 files that must FAIL)
def resume_variant(rows, kind):
    head = csv_bytes(rows[:4], bom=True, crlf=True)
    tail = csv_bytes(rows[4:], crlf=True).split(b'\r\n', 1)[1]  # rows only
    if kind == 'repeated_header':
        return head + csv_bytes(rows[4:], crlf=True)  # the second chat wrote its own header
    if kind == 'duplicated_row':
        return head + csv_bytes(rows[3:], crlf=True).split(b'\r\n', 1)[1]  # row 4 written again
    return head + '\ufeff'.encode('utf-8') + tail  # the second chat's file began with its own BOM


HINT = 'hint Q10 labels_s8_sheet_Q10.csv: file line 6 '  # header = line 1, rows 1-4 = lines 2-5


@pytest.mark.parametrize('kind,expect', [
    ('repeated_header', ['FAIL Q10 unknown: ["item_id"]', 'FAIL Q10 invalid_values: ',
                         HINT + 'repeats the header line (item_id,query_asks,...): delete that line']),
    ('duplicated_row', ['FAIL Q10 duplicates: ["S8-e2e-q10-03"]']),
    ('mid_file_bom', ['FAIL Q10 missing: ["S8-e2e-q10-04"]', 'FAIL Q10 unknown: ["\\ufeffS8-e2e-q10-04"]',
                      HINT + 'holds an invisible BOM character (U+FEFF), left where a second file was pasted in: '
                      'delete that character (retype the start of the line)']),
])
def test_case15_resumed_file_problems_fail_with_their_item_ids(tmp_path, kind, expect):
    w = build_world(tmp_path, W1_SPEC, b_tweak=w1_b_tweak)
    rows = [judge_row('B', item_id('Q10', k), g[1], span_of('Q10', k)) for k, g in enumerate(W1_SPEC['Q10'])]
    (w.out['B'] / 'labels_s8_sheet_Q10.csv').write_bytes(resume_variant(rows, kind))
    report = jc.validate_judge('B', w.kits['B'], w.per_query, require_all=True, now=NOW)
    assert report['overall'] == 'FAIL' and report['files']['Q10']['status'] == 'FAIL'
    assert report['files']['Q1']['status'] == report['files']['Q2']['status'] == 'PASS'
    lines = jc.warning_lines(report)
    for text in expect:
        assert any(line.startswith(text) for line in lines), (text, lines)
    hints = [line for line in lines if line.startswith('hint ')]
    assert len(hints) == (0 if kind == 'duplicated_row' else 1)  # hints only: the status stays FAIL
    warn = report['files']['Q10']['warnings']
    assert (warn['repeated_header_lines'], warn['bom_inside_file']) == (
        {'repeated_header': (1, 0), 'duplicated_row': (0, 0), 'mid_file_bom': (0, 1)}[kind])
    assert_no_leak('\n'.join(jc.table_lines(report) + lines))
    with pytest.raises(PermissionError, match='validation FAIL'):
        jc.freeze_judge('B', 'fake-model-B', w.kits['B'], w.per_query, w.labels, now=NOW + timedelta(minutes=1))


def test_validate_report_in_the_same_second_gets_a_suffix_never_overwrites(tmp_path):
    w = build_world(tmp_path, W1_SPEC, b_tweak=w1_b_tweak)
    report = jc.validate_judge('B', w.kits['B'], w.per_query, now=NOW)
    paths = [jc.write_report(report, w.labels, NOW) for _ in range(3)]
    assert [p.name for p in paths] == ['validate_B_20261023T090000Z.json', 'validate_B_20261023T090000Z_2.json',
                                       'validate_B_20261023T090000Z_3.json']
    first = paths[0].read_bytes()
    jc.freeze_judge('B', 'fake-model-B', w.kits['B'], w.per_query, w.labels, now=NOW)  # same second as validate
    assert read_json(w.labels / 'judge_B_freeze.json')['validate_report'] == 'validate_B_20261023T090000Z_4.json'
    assert paths[0].read_bytes() == first


def test_fewer_than_two_valid_rows_refuses_before_writing(tmp_path):
    """B excludes 2 items; the two-judge fallback drops C -> those items have only A: refused, nothing written."""
    x1, x2 = item_id('Q3', 7), item_id('Q5', 30)
    w = build_world(tmp_path, w3_spec(), b_drop=(x1,), b_bad=(x2,), b_tweak=lambda q, rows: rows)
    (tmp_path / 'exclude.txt').write_text(f'{x1}\n{x2}\n', encoding='utf-8')
    validate_and_freeze(w, exclude_items=tmp_path / 'exclude.txt', reason='fake: rows still bad after the re-run')
    out = tmp_path / 'fallback'
    with pytest.raises(ValueError) as e:
        merge(w, out=out, fallback_reason='fake: C over 2% after one re-run', freeze_dir=w.labels)
    assert str(e.value) == (f'2 item(s) have fewer than two valid judge rows: {x1} (no valid row from B); '
                            f'{x2} (no valid row from B) — re-run a judge on these items')
    assert not out.exists()
    assert merge(w, out=tmp_path / 'three', freeze_dir=w.labels)['mode'] == 'three_judge'  # with C: A + C decide


# ---------------------------------------------------------------- referee practice demo (Part 3)
DEMO_QUERIES = {'D1': 'fake query: what must the label on a widget box show?',
                'D2': 'fake query: when must gadget trays be cleaned before they are used again?'}
DEMO_SPEC = {
    'D1': [(3, 3, 3), (1, 2, 2), (0, 0, 0), (2, 2, 3), (2, 1, 1), (0, 1, 0), (3, 3, 2), (1, 1, 1)],
    'D2': [(0, 2, 3), (0, 0, 0), (3, 3, 3), (3, 1, 1), (1, 0, 1), (2, 2, 2), (1, 1, 2), (0, 0, 1)],
}
DEMO_TEXTS = {  # invented clauses for the five split items: (title, text, a quotable span)
    item_id('D1', 1): ('Box markings', 'Each widget box should carry a mark that lets the maker trace the batch. '
                       'The form of the mark is left to the maker.', 'carry a mark that lets the maker trace the batch'),
    item_id('D1', 4): ('Label language', 'Labels on widget boxes shall be written in the language of the country of '
                       'sale. Pictograms may replace words where a recognised symbol exists.',
                       'shall be written in the language of the country of sale'),
    item_id('D2', 0): ('Tray reprocessing', 'Gadget trays that are reused shall be cleaned and dried before the next '
                       'use. The cleaning method shall be recorded.', 'shall be cleaned and dried before the next use'),
    item_id('D2', 3): ('Storage of trays', 'Clean gadget trays shall be stored in a closed cabinet. Trays stored for '
                       'more than 30 days are cleaned again before use.', 'are cleaned again before use'),
    item_id('D2', 6): ('Single-use trays', 'Trays marked single use are not cleaned and are discarded after use. This '
                       'section does not cover reusable trays.', 'are not cleaned and are discarded after use'),
}
DEMO_NOTES = {  # invented rationales: (query_asks, note) per judge
    item_id('D1', 1): {'A': ('what the box label shows', 'Only a traceability mark; no label content is listed.'),
                       'B': ('label content', 'A batch mark is label content, so partial.'),
                       'C': ('label content', 'Gives one element of the label (trace mark).')},
    item_id('D1', 4): {'A': ('label content', 'Language rule is about how, not what, the label shows: partial.'),
                       'B': ('label content', 'Language only; the query asks for content. Mentions only.'),
                       'C': ('label content', 'Not content; a formatting rule.')},
    item_id('D2', 0): {'A': ('when trays are cleaned', 'Names cleaning but the query asks when; "before the next use" '
                             'may be read as when. I stay at 0 because no trigger is given.'),
                       'B': ('when trays are cleaned', 'States before next use: partial answer.'),
                       'C': ('when trays are cleaned', '"before the next use" answers when.')},
    item_id('D2', 3): {'A': ('cleaning timing', 'Re-cleaning after 30 days of storage is a when rule.'),
                       'B': ('cleaning timing', 'About storage, the 30-day line is a side remark.'),
                       'C': ('cleaning timing', 'Storage clause; mentions cleaning only.')},
    item_id('D2', 6): {'A': ('cleaning timing', 'Single-use trays are out of the query.'),
                       'B': ('cleaning timing', 'Says trays are not cleaned; unrelated to when.'),
                       'C': ('cleaning timing', 'Tells when cleaning does not apply, which partly answers.')},
}
DEMO_PREFIX = 16


def build_referee_demo(folder, now=NOW):
    """A fake run folder whose referee list holds 5 threshold splits + 1 audit disagreement (invented text).
    Runs the real chain up to merge stage 2. Returns the paths for the s8-label command."""
    judge_kw = {(j, iid): dict(asks=notes[j][0], note=notes[j][1]) for iid, notes in DEMO_NOTES.items() for j in 'ABC'}
    generic = {'A': 'States a batch-code duty for widget boxes; graded on the clause text only.',
               'B': 'Batch code on the box is label content; same reading as the narrative.',
               'C': 'Clause names what the box must carry.'}
    w = build_world(folder, DEMO_SPEC, texts=DEMO_TEXTS, judge_kw=judge_kw, queries=DEMO_QUERIES,
                    b_tweak=lambda q, rows: rows,
                    default_kw=lambda j, iid: dict(asks='what the box label shows', note=generic[j]))
    with unique_stamps():
        validate_and_freeze(w, now=now)
    jg.run_audit_sample(w.sheet, w.audit)
    jg.run_audit_order(w.audit, qid_order=w.qids)
    prefix = jg.read_order(w.audit / jg.AUDIT_ORDER)[:DEMO_PREFIX]
    with_cons = [i for i in prefix if consensus(w.grades[i]) is not None]
    disagree = next(i for i in with_cons if consensus(w.grades[i]) <= 1)
    pick = lambda iid, cons: 1 if cons is None else (3 if iid == disagree else cons)
    app = lt.LabelApp(w.audit / 'audit_sheet.csv', w.audit / AUDIT_LABELS, 'audit', order=w.audit / jg.AUDIT_ORDER)
    for iid in prefix:
        g = pick(iid, consensus(w.grades[iid]))
        app.save(dict(item_id=iid, grade=str(g), clause_states=STATES[g], jurisdiction_match='not_applicable',
                      unsure='no', evidence_span='', note=''))
    jg.freeze_audit(w.audit / AUDIT_LABELS, w.audit, w.audit / jg.AUDIT_ORDER, now=now, minimum=DEMO_PREFIX)
    summary = merge(w, **audit_kw(w))
    if summary['audit']['flag'] != 'PASS' or summary['referee'] != {'threshold_split': 5, 'audit_disagrees': 1,
                                                                     'total': 6}:
        raise RuntimeError(f'demo world is not as designed: {summary}')
    return dict(sheet=w.labels / 'referee_sheet.csv', disputes=w.labels / 'disputes.csv',
                labels=w.labels / 'labels_referee.csv', freeze=w.audit / 'audit_freeze.json', disagree=disagree,
                world=w)


def test_referee_demo_builds_six_items_and_opens_in_the_tool(tmp_path):
    paths = build_referee_demo(tmp_path / 'demo')
    app = lt.LabelApp(paths['sheet'], paths['labels'], 'referee', disputes=paths['disputes'], freeze=paths['freeze'])
    reasons = [app.item(k)['reason'] for k in range(len(app.ids))]
    assert len(app.ids) == 6 and reasons.count('threshold_split') == 5 and reasons.count('audit_disagrees') == 1
    view = app.item(app.ids.index(item_id('D2', 0)))
    c_column = str(app.judge_order(item_id('D2', 0)).index('c') + 1)
    assert view['judges'][c_column]['note'] == DEMO_NOTES[item_id('D2', 0)]['C'][1]
    assert view['item']['text'] == DEMO_TEXTS[item_id('D2', 0)][1] and not paths['labels'].exists()


# ---------------------------------------------------------------- blinded judge names on the referee screen
MODEL_NAMES = ('Claude', 'Opus', 'GPT', 'Astra', 'Gemini', 'Flash', 'fake-model')


def demo_app(paths):
    return lt.LabelApp(paths['sheet'], paths['labels'], 'referee', disputes=paths['disputes'], freeze=paths['freeze'])


def test_referee_screen_is_blinded_over_http(tmp_path):
    import threading
    import urllib.request
    app = demo_app(build_referee_demo(tmp_path / 'demo'))
    server = lt.ThreadingHTTPServer(('127.0.0.1', 0), lt.make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        page = urllib.request.urlopen(base + '/').read().decode('utf-8')
        items = [urllib.request.urlopen(f'{base}/api/item?i={k}').read().decode('utf-8') for k in range(6)]
        state = urllib.request.urlopen(base + '/api/state').read().decode('utf-8')
    finally:
        server.shutdown()
        server.server_close()
    assert "Referee: judges' labels (order shuffled per item)" in page and "'Judge '+j;" in page
    for text in (page, state, *items):
        for banned in ('Judge A', 'Judge B', 'Judge C', 'a_grade', 'b_note', 'c_note', *MODEL_NAMES):
            assert banned not in text, banned
    for text in items:
        judges = json.loads(text)['judges']
        assert set(judges) == {'1', '2', '3'} and all(set(f) == set(lb.JUDGE_FIELDS) for f in judges.values())


def test_referee_judge_order_is_stable_per_item_and_shuffled_and_merge_unchanged(tmp_path):
    paths = build_referee_demo(tmp_path / 'demo')
    first, again = demo_app(paths), demo_app(paths)  # a restart
    orders = {i: first.judge_order(i) for i in first.ids}
    assert orders == {i: again.judge_order(i) for i in again.ids}
    assert len({tuple(o) for o in orders.values()}) >= 2  # the order differs across items
    for iid, order in orders.items():  # random.Random(f'20261021:{item_id}') shuffle of a, b, c
        expected = ['a', 'b', 'c']
        random.Random(f'20261021:{iid}').shuffle(expected)
        assert order == expected and lt.REFEREE_SEED == 20261021
    disputes = {d['item_id']: d for d in lb.read_disputes(paths['disputes'])}
    for k, iid in enumerate(first.ids):  # every column shows the right judge's fields
        view = first.item(k)
        for n, letter in enumerate(orders[iid], 1):
            assert view['judges'][str(n)] == {f: disputes[iid][f'{letter}_{f}'] for f in lb.JUDGE_FIELDS}
    # (c) the referee file holds no judge columns, and the merge still uses A/B/C as before
    w = paths['world']
    decisions = {}
    for iid in first.ids:
        g = w.grades[iid]
        if iid == paths['disagree']:
            decisions[iid] = ref_payload(iid, 3, 'guideline_gap')  # no quote/rule -> consensus stands
        elif iid == item_id('D2', 0):
            decisions[iid] = ref_payload(iid, 0, 'unclear_text', unsure='yes')  # -> median of (0, 2, 3) = 2
        else:
            decisions[iid] = ref_payload(iid, 3 - sorted(g)[1], 'clear_reading')
        first.save(decisions[iid])
    columns, _ = lb.read_csv(paths['labels'])
    assert columns == list(lt.REFEREE_TOOL_FIELDS)
    merge(w, **audit_kw(w, paths['labels']))
    got = {r['clause_id']: r['grade'] for r in qrels_io.read(w.labels / 'qrels.tsv')}
    for iid, g in w.grades.items():
        d = decisions.get(iid)
        want = (sorted(g)[1] if d is None or iid == paths['disagree'] or d['unsure'] == 'yes' else int(d['grade']))
        assert got[w.by_id[iid]['clause_id']] == want, iid
