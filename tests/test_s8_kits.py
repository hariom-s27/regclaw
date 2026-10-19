"""S8 Part D1 judge kits (s8_judging): built and checked in tmp folders only, never the real kits."""
from pathlib import Path

import pytest

from retrieval_eval import s8_judging as j
from tests.test_s8_labels import make_sheet

INSTRUCTIONS_TEXT = """You are an independent relevance judge in a research study on medical-device regulatory documents.
This folder contains: GUIDELINE.md (your rules), practice/ or eval/ (sheets to judge, CSV), output/ (where you write).
1. Read GUIDELINE.md fully before judging. Follow it exactly.
2. Judge every row of each sheet file yourself, one row at a time, by reading the query, narrative and the full clause text.
3. You MAY use code only to read rows from the CSV and to write your answers to output/. You MUST NOT use code, keyword matching, heuristics, embeddings, web search or any model/API call to decide a grade. Every grade is your own reading.
4. Do not open any file or folder outside this folder.
5. Write output/labels_<sheetname>.csv with columns: item_id, query_asks, clause_states, jurisdiction_match, evidence_span, grade, unsure, note. One row per item. Save after every 10 items.
6. Work file by file. After each file, report: items done, grade counts. Do not report anything else.
7. Keep the same standard from first to last item. Do not revise earlier grades unless you find you broke a rule; if so, note it.
"""


@pytest.fixture
def kits(tmp_path):
    src = tmp_path / 'repo_data'
    src.mkdir()
    make_sheet(src / 'practice_sheet.csv', {'P1': 5})
    per_query = src / 'per_query'
    per_query.mkdir()
    for q in ('Q1', 'Q2'):
        make_sheet(per_query / f's8_sheet_{q}.csv', {q: 3})
    roots = [tmp_path / 's8_judgeA', tmp_path / 's8_judgeB']
    trees = j.make_kits(roots, src / 'practice_sheet.csv', repo=tmp_path / 'repo')
    return roots, src / 'practice_sheet.csv', per_query, trees


def test_instructions_text_is_exact(tmp_path):
    assert j.instructions_bytes() == INSTRUCTIONS_TEXT.encode('utf-8')
    crlf = tmp_path / 'crlf.md'
    crlf.write_bytes(INSTRUCTIONS_TEXT.replace('\n', '\r\n').encode('utf-8'))
    assert j.instructions_bytes(crlf) == INSTRUCTIONS_TEXT.encode('utf-8')  # autocrlf checkout


def test_kit_contains_only_allowed_entries(kits):
    roots, practice, per_query, trees = kits
    for root in roots:
        assert [e['path'] for e in trees[str(root)]] == [
            'GUIDELINE.md', 'JUDGE_INSTRUCTIONS.md', 'eval/', 'output/', 'practice/', 'practice/practice_sheet.csv']
        assert (root / 'GUIDELINE.md').read_text(encoding='utf-8') == j.GUIDELINE_PLACEHOLDER
        assert (root / 'practice' / 'practice_sheet.csv').read_bytes() == practice.read_bytes()
        assert not any((root / 'eval').iterdir()) and not any((root / 'output').iterdir())
    assert j.check_kits(roots, practice, per_query, repo=roots[0].parent / 'repo') == {str(r): [] for r in roots}
    assert j.make_kits(roots, practice, repo=roots[0].parent / 'repo') == trees  # idempotent


def test_checker_flags_foreign_files(kits):
    roots, practice, per_query, _ = kits
    a = roots[0]
    for rel in ('item_map.jsonl', 'output/pool_key.jsonl', 'eval/notes.txt', 'output/sub/labels_x.csv', 'CLAUDE.md'):
        (a / rel).parent.mkdir(parents=True, exist_ok=True)
        (a / rel).write_text('x', encoding='utf-8')
    problems = j.check_kits(roots, practice, per_query, repo=a.parent / 'repo')
    assert problems[str(roots[1])] == []
    text = ' '.join(problems[str(a)])
    for rel in ('item_map.jsonl', 'output/pool_key.jsonl', 'eval/notes.txt', 'output/sub', 'CLAUDE.md'):
        assert rel in text


def test_checker_eval_and_output_rules(kits):
    roots, practice, per_query, _ = kits
    a = roots[0]
    (a / 'eval' / 's8_sheet_Q1.csv').write_bytes((per_query / 's8_sheet_Q1.csv').read_bytes())
    (a / 'output' / 'labels_practice_sheet.csv').write_text('item_id\n', encoding='utf-8')
    assert j.check_kit(a, j.bytes_sha(practice), per_query) == []
    (a / 'eval' / 's8_sheet_Q2.csv').write_text('tampered', encoding='utf-8')
    (a / 'practice' / 'practice_sheet.csv').write_text('tampered', encoding='utf-8')
    problems = ' '.join(j.check_kit(a, j.bytes_sha(practice), per_query))
    assert 'eval/s8_sheet_Q2.csv' in problems and 'practice_sheet.csv sha' in problems


def test_guideline_must_match_between_kits(kits):
    roots, practice, per_query, _ = kits
    (roots[0] / 'GUIDELINE.md').write_text('guideline v1.0 ...', encoding='utf-8')
    problems = j.check_kits(roots, practice, per_query, repo=roots[0].parent / 'repo')
    assert all('GUIDELINE.md differs between kits' in p for p in problems.values())
    (roots[1] / 'GUIDELINE.md').write_text('guideline v1.0 ...', encoding='utf-8')
    assert not any(j.check_kits(roots, practice, per_query, repo=roots[0].parent / 'repo').values())


def test_roots_must_not_overlap_repo_or_each_other(tmp_path):
    repo = tmp_path / 'repo'
    with pytest.raises(ValueError, match='repo'):
        j.check_roots([repo / 'kitA', tmp_path / 'kitB'], repo)
    with pytest.raises(ValueError, match='overlap'):
        j.check_roots([tmp_path / 'kitA', tmp_path / 'kitA' / 'kitB'], repo)
    j.check_roots([Path('D:/s8_judgeA'), Path('D:/s8_judgeB')], j.ROOT)  # default roots are outside the repo


def test_make_kits_refuses_a_dirty_root(tmp_path):
    make_sheet(tmp_path / 'p.csv', {'P1': 2})
    dirty = tmp_path / 'kitA'
    dirty.mkdir()
    (dirty / 'secret.txt').write_text('x', encoding='utf-8')
    with pytest.raises(FileExistsError):
        j.make_kits([dirty, tmp_path / 'kitB'], tmp_path / 'p.csv', repo=tmp_path / 'repo')
