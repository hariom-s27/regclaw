"""S8 D4v: judge-output validator, per-judge freeze, merge freeze check, run comparison. Fakes only."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from retrieval_eval import s8_judge_check as jc
from retrieval_eval import s8_labels as m
from retrieval_eval.s8_runs import SHEET_COLUMNS, sheet_csv
from tests.test_s8_labels import label, write_labels

QIDS = ('Q1', 'R1')
NOW = datetime(2026, 10, 21, 9, 0, 0, tzinfo=timezone.utc)
TEXT = 'The sponsor shall\n  submit   the final\treport to the authority. NOTE: see Annex I.'
SPAN = 'sponsor shall submit the final report'  # found only after whitespace normalisation


def sheet_rows(qid, n=4):
    return [{c: '' for c in SHEET_COLUMNS} | dict(item_id=f'S8-{qid.lower()}{i:04d}', qid=qid, query=f'query {qid}',
                                                  source_doc='doc', clause_id=f'doc:{qid}.{i}', title=f't{i}',
                                                  text=TEXT, common_rules='RULES' if i == 0 else '')
            for i in range(n)]


def good(item_id, grade=2, **kw):
    return label(item_id, grade, evidence_span=kw.pop('evidence_span', SPAN), **kw)


@pytest.fixture
def env(tmp_path):
    """per_query/ (source of truth), a combined sheet, one kit root with eval/ copies and full output/."""
    per_query, root = tmp_path / 'per_query', tmp_path / 'kitA'
    per_query.mkdir()
    (root / 'eval').mkdir(parents=True)
    rows = {q: sheet_rows(q) for q in QIDS}
    for q, rs in rows.items():
        data = sheet_csv(rs).encode('utf-8')
        (per_query / f's8_sheet_{q}.csv').write_bytes(data)
        (root / 'eval' / f's8_sheet_{q}.csv').write_bytes(data)
        write_labels(root / 'output' / f'labels_s8_sheet_{q}.csv', [good(r['item_id']) for r in rs])
    (tmp_path / 'sheet.csv').write_bytes(sheet_csv([r for rs in rows.values() for r in rs]).encode('utf-8'))
    return tmp_path, per_query, root, rows


def out_file(root, qid):
    return root / 'output' / f'labels_s8_sheet_{qid}.csv'


# ---------------------------------------------------------------- A. validator
def test_clean_kit_passes_and_the_report_has_no_label_text(env):
    tmp, per_query, root, rows = env
    r = jc.validate_judge('A', root, per_query, require_all=True, now=NOW)
    assert r['overall'] == 'PASS' and r['counts'] == dict(PASS=2, FAIL=0, pending=0, files=2)
    f = r['files']['Q1']
    assert (f['rows'], f['missing'], f['duplicates'], f['unknown'], f['invalid_values'], f['quote_not_found'],
            f['none_spans']) == (4, 0, 0, 0, 0, 0, 0)
    assert f['grade_counts'] == {'0': 0, '1': 0, '2': 4, '3': 0}
    path = jc.write_report(r, tmp / 'labels', NOW)
    assert path.name == 'validate_A_20261021T090000Z.json'
    assert SPAN not in path.read_text(encoding='utf-8')
    with pytest.raises(FileExistsError):  # a NEW file each run
        jc.write_report(r, tmp / 'labels', NOW)


def test_missing_duplicate_unknown_ids(env):
    tmp, per_query, root, rows = env
    ids = [r['item_id'] for r in rows['Q1']]
    write_labels(out_file(root, 'Q1'), [good(ids[0]), good(ids[1]), good(ids[1]), good('S8-zzz9999')])
    f = jc.validate_judge('A', root, per_query)['files']['Q1']
    assert (f['missing'], f['duplicates'], f['unknown'], f['status']) == (2, 1, 1, 'FAIL')
    assert f['failing_item_ids']['missing'] == ids[2:]
    assert f['failing_item_ids']['duplicates'] == [ids[1]] and f['failing_item_ids']['unknown'] == ['S8-zzz9999']
    assert set(f['fail_reasons']) == {'missing', 'duplicates', 'unknown'}


@pytest.mark.parametrize('column,value', [('clause_states', 'answer'), ('jurisdiction_match', 'n/a'),
                                          ('grade', '2.0'), ('grade', '4'), ('unsure', 'maybe')])
def test_invalid_values_counted_per_column(env, column, value):
    tmp, per_query, root, rows = env
    ids = [r['item_id'] for r in rows['Q1']]
    write_labels(out_file(root, 'Q1'), [good(i) for i in ids[:3]] + [good(ids[3]) | {column: value}])
    f = jc.validate_judge('A', root, per_query)['files']['Q1']
    assert f['invalid_values'] == 1 and f['failing_item_ids']['invalid_values'] == {column: [ids[3]]}
    assert f['status'] == 'FAIL' and f['fail_reasons'] == ['invalid_values']
    assert json.dumps(value) not in json.dumps(f)  # item ids only, never the value


def test_wrong_header_fails(env):
    tmp, per_query, root, rows = env
    fields = [c for c in m.LABEL_FIELDS if c != 'note']
    write_labels(out_file(root, 'Q1'), [good(r['item_id']) for r in rows['Q1']], fields)
    f = jc.validate_judge('A', root, per_query)['files']['Q1']
    assert f['header_ok'] is False and f['status'] == 'FAIL'
    swapped = ['item_id', 'grade', *[c for c in m.LABEL_FIELDS if c not in ('item_id', 'grade')]]
    write_labels(out_file(root, 'Q1'), [good(r['item_id']) for r in rows['Q1']], swapped)  # order matters too
    assert jc.validate_judge('A', root, per_query)['files']['Q1']['status'] == 'FAIL'


def test_order_differs_is_a_warning_only(env):
    tmp, per_query, root, rows = env
    write_labels(out_file(root, 'Q1'), [good(r['item_id']) for r in reversed(rows['Q1'])])
    f = jc.validate_judge('A', root, per_query)['files']['Q1']
    assert f['warnings']['order_differs'] is True and f['status'] == 'PASS'


def test_quote_found_after_whitespace_normalisation_but_case_sensitive(env):
    tmp, per_query, root, rows = env
    ids = [r['item_id'] for r in rows['Q1']]
    write_labels(out_file(root, 'Q1'), [
        good(ids[0], evidence_span='  sponsor shall\nsubmit\t\tthe   final report '),  # found
        good(ids[1], evidence_span='Sponsor shall submit'),  # case differs -> not found
        good(ids[2], evidence_span='sponsor must submit'),  # not in the clause
        good(ids[3])])
    f = jc.validate_judge('A', root, per_query)['files']['Q1']
    assert f['quote_not_found'] == 2 and f['failing_item_ids']['quote_not_found'] == ids[1:3]
    assert f['status'] == 'FAIL' and f['fail_reasons'] == ['quote_not_found']


def test_none_spans_counted_as_warning_and_empty_span_fails(env):
    tmp, per_query, root, rows = env
    ids = [r['item_id'] for r in rows['Q1']]
    write_labels(out_file(root, 'Q1'), [good(ids[0], 0, evidence_span='none'), good(ids[1], 0, evidence_span='None'),
                                        good(ids[2]), good(ids[3])])
    f = jc.validate_judge('A', root, per_query)['files']['Q1']
    assert (f['none_spans'], f['quote_not_found'], f['status']) == (2, 0, 'PASS')
    write_labels(out_file(root, 'Q1'), [good(ids[0], evidence_span='  '), *[good(i) for i in ids[1:]]])
    f = jc.validate_judge('A', root, per_query)['files']['Q1']
    assert f['empty_span'] == 1 and f['failing_item_ids']['empty_span'] == [ids[0]] and f['status'] == 'FAIL'


def test_word_limits_are_warnings(env):
    tmp, per_query, root, rows = env
    ids = [r['item_id'] for r in rows['Q1']]
    long_text = ' '.join(['word'] * 35)
    rows['Q1'][0]['text'] = long_text
    (per_query / 's8_sheet_Q1.csv').write_bytes(sheet_csv(rows['Q1']).encode('utf-8'))
    write_labels(out_file(root, 'Q1'), [
        good(ids[0], evidence_span=long_text) | dict(query_asks=' '.join(['a'] * 21), note=' '.join(['n'] * 26)),
        *[good(i) for i in ids[1:]]])
    f = jc.validate_judge('A', root, per_query)['files']['Q1']
    assert f['warnings']['evidence_span_over_30_words'] == 1
    assert f['warnings']['query_asks_over_20_words'] == 1 and f['warnings']['note_over_25_words'] == 1
    assert f['status'] == 'PASS'


def test_missing_file_is_pending_unless_require_all(env):
    tmp, per_query, root, rows = env
    out_file(root, 'R1').unlink()
    r = jc.validate_judge('A', root, per_query)
    assert r['files']['R1']['status'] == 'pending' and r['overall'] == 'PENDING'
    r = jc.validate_judge('A', root, per_query, require_all=True)
    assert r['files']['R1']['status'] == 'FAIL' and r['overall'] == 'FAIL'
    assert any('R1' in line and 'FAIL' in line for line in jc.table_lines(r))


def test_kit_eval_sha_mismatch_fails(env):
    tmp, per_query, root, rows = env
    path = root / 'eval' / 's8_sheet_R1.csv'
    path.write_bytes(path.read_bytes() + b'\r\n')
    r = jc.validate_judge('A', root, per_query, require_all=True)
    assert r['overall'] == 'FAIL' and r['kit_eval_problems'] == ['eval/s8_sheet_R1.csv sha256 differs from the '
                                                                'source-of-truth sheet']
    path.unlink()
    assert jc.validate_judge('A', root, per_query)['kit_eval_problems'] == ['eval/s8_sheet_R1.csv missing']


def test_cli_validate_writes_a_report_and_exit_code(env, capsys):
    tmp, per_query, root, rows = env
    args = ['s8-validate-judge', '--judge', 'A', '--root', str(root), '--per-query', str(per_query),
            '--labels-dir', str(tmp / 'labels')]
    assert jc.main(args) == 0
    assert 'OVERALL judge A: PASS' in capsys.readouterr().out
    assert len(list((tmp / 'labels').glob('validate_A_*.json'))) == 1
    write_labels(out_file(root, 'R1'), [good(r['item_id'], evidence_span='absent') for r in rows['R1']])
    assert jc.main([*args[:-1], str(tmp / 'labels2')]) == 1  # FAIL -> exit 1, report still written
    out = capsys.readouterr().out
    assert 'OVERALL judge A: FAIL' in out and 'FAIL R1 quote_not_found' in out and 'absent' not in out
    assert len(list((tmp / 'labels2').glob('validate_A_*.json'))) == 1


# ---------------------------------------------------------------- B. freeze
def test_freeze_refuses_on_fail_and_on_pending(env):
    tmp, per_query, root, rows = env
    write_labels(out_file(root, 'Q1'), [good(r['item_id'], evidence_span='not in clause') for r in rows['Q1']])
    with pytest.raises(PermissionError, match='validation FAIL'):
        jc.freeze_judge('A', 'Claude Opus 5.5, High', root, per_query, tmp / 'labels', NOW)
    assert not (tmp / 'labels' / 'judge_A_freeze.json').exists()
    write_labels(out_file(root, 'Q1'), [good(r['item_id']) for r in rows['Q1']])
    out_file(root, 'R1').unlink()
    with pytest.raises(PermissionError, match='validation FAIL'):  # --require-all: a missing file fails
        jc.freeze_judge('A', 'model', root, per_query, tmp / 'labels', NOW + timedelta(seconds=1))
    with pytest.raises(ValueError, match='--model'):
        jc.freeze_judge('A', '  ', root, per_query, tmp / 'labels', NOW)


def test_freeze_writes_shas_and_is_write_once(env):
    tmp, per_query, root, rows = env
    record, path = jc.freeze_judge('A', 'Claude Opus 5.5, High', root, per_query, tmp / 'labels', NOW)
    assert path.name == 'judge_A_freeze.json' and record['model'] == 'Claude Opus 5.5, High'
    assert record['validate_report'] == 'validate_A_20261021T090000Z.json'
    assert (tmp / 'labels' / record['validate_report']).exists()
    assert record['files'] == {f'labels_s8_sheet_{q}.csv': m.bytes_sha(out_file(root, q)) for q in QIDS}
    assert record['counts']['labels_s8_sheet_Q1.csv']['rows'] == 4
    with pytest.raises(FileExistsError, match='already frozen'):
        jc.freeze_judge('A', 'Claude Opus 5.5, High', root, per_query, tmp / 'labels', NOW + timedelta(seconds=5))


# ---------------------------------------------------------------- C. merge freeze check
@pytest.fixture
def three_frozen(env):
    """Kits A, B, C with identical PASSing outputs, each frozen into labels/."""
    tmp, per_query, root, rows = env
    roots = {'A': root}
    for x in 'BC':
        roots[x] = tmp / f'kit{x}'
        for sub in ('eval', 'output'):
            (roots[x] / sub).mkdir(parents=True)
            for p in (root / sub).iterdir():
                (roots[x] / sub / p.name).write_bytes(p.read_bytes())
    for n, x in enumerate('ABC'):
        jc.freeze_judge(x, f'model {x}', roots[x], per_query, tmp / 'labels', NOW + timedelta(seconds=n))
    files = {f'{x.lower()}_files': m.judge_files(roots[x] / 'output') for x in 'ABC'}
    return tmp, roots, files


def test_merge_runs_when_every_judge_matches_its_freeze(three_frozen):
    tmp, roots, files = three_frozen
    s = m.run_merge(sheet=tmp / 'sheet.csv', out=tmp / 'labels', bootstrap=50, **files)
    assert s['stage'] == 1 and s['mode'] == 'three_judge'


def test_merge_refuses_a_changed_sha_and_names_the_file(three_frozen):
    tmp, roots, files = three_frozen
    path = roots['B'] / 'output' / 'labels_s8_sheet_R1.csv'
    path.write_bytes(path.read_bytes() + b'\r\n')
    with pytest.raises(PermissionError, match=r'judge B: labels_s8_sheet_R1.csv differs from judge_B_freeze.json'):
        m.run_merge(sheet=tmp / 'sheet.csv', out=tmp / 'labels', bootstrap=50, **files)
    assert not (tmp / 'labels' / 'agreement_abc.json').exists()


def test_merge_refuses_a_missing_freeze_or_a_different_file_set(three_frozen):
    tmp, roots, files = three_frozen
    with pytest.raises(PermissionError, match='judge_A_freeze.json missing'):
        m.run_merge(sheet=tmp / 'sheet.csv', out=tmp / 'other', freeze_dir=tmp / 'nowhere', bootstrap=50, **files)
    with pytest.raises(PermissionError, match=r"judge A: files differ .*not given: \['labels_s8_sheet_R1.csv'\]"):
        m.run_merge(sheet=tmp / 'sheet.csv', out=tmp / 'labels', bootstrap=50,
                    **files | {'a_files': files['a_files'][:1]})
    # the test-only bypass is a keyword, not a CLI option
    s = m.run_merge(sheet=tmp / 'sheet.csv', out=tmp / 'x', freeze_dir=tmp / 'nowhere', bootstrap=50,
                    _test_skip_judge_freeze=True, **files)
    assert s['stage'] == 1
    with pytest.raises(SystemExit):
        m.main(['s8-merge-labels', '--a', 'x', '--b', 'y', '--test-skip-judge-freeze'])


# ---------------------------------------------------------------- D. compare runs
def write_run(folder, grades):
    write_labels(Path(folder) / 'labels_s8_sheet_Q1.csv', [good(i, g) for i, g in grades.items()])


def test_compare_runs_known_example(tmp_path):
    write_run(tmp_path / 'r1', {'i1': 3, 'i2': 2, 'i3': 0, 'i4': 1, 'i5': 2, 'i7': 'x'})
    write_run(tmp_path / 'r2', {'i1': 3, 'i2': 1, 'i3': 0, 'i4': 1, 'i6': 2, 'i7': 2})
    r = jc.compare_runs(tmp_path / 'r1', tmp_path / 'r2', 'run1_vs_run2', NOW)
    assert (r['n'], r['only_a'], r['only_b'], r['excluded_invalid_grade']) == (4, 1, 1, 1)
    assert r['exact_agreement'] == 0.75 and r['binary_agreement'] == 0.75
    assert r['kappa_binary'] == pytest.approx(0.5)  # [[2,0],[1,1]]: po .75, pe .5
    assert r['kappa_quadratic'] == pytest.approx(0.9)  # po 35/36, pe 26/36 (hand-computed)
    assert r['confusion_4x4_rows_a_cols_b'] == [[1, 0, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1]]
    assert r['confusion_2x2_rows_a_cols_b'] == [[2, 0], [1, 1]]
    path = jc.write_compare(r, tmp_path / 'labels', NOW)
    assert path.name == 'compare_run1_vs_run2_20261021T090000Z.json'
    assert 'i1' not in path.read_text(encoding='utf-8')  # aggregate only
    with pytest.raises(FileExistsError):
        jc.write_compare(r, tmp_path / 'labels', NOW)


def test_compare_runs_refuses_duplicates_and_bad_names(tmp_path):
    write_run(tmp_path / 'r1', {'i1': 3})
    write_labels(tmp_path / 'r2' / 'labels_s8_sheet_Q1.csv', [good('i1', 3), good('i1', 2)])
    with pytest.raises(ValueError, match='more than once'):
        jc.compare_runs(tmp_path / 'r1', tmp_path / 'r2', 'x')
    with pytest.raises(ValueError, match='--name'):
        jc.compare_runs(tmp_path / 'r1', tmp_path / 'r1', '../x')
    with pytest.raises(ValueError, match='no labels_s8_sheet'):
        jc.compare_runs(tmp_path / 'r1', tmp_path / 'empty', 'x')


def test_tasks_registers_the_three_commands():
    import tasks
    for name in ('s8-validate-judge', 's8-freeze-judge', 's8-compare-runs'):
        assert tasks.S8_D_COMMANDS[name] == 'retrieval_eval.s8_judge_check'
