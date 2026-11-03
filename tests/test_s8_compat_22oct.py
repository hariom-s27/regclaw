"""S8 merge compatibility with the 22 Oct stage-1/2 outputs (fakes only, offline).

The real run folder already holds agreement_abc.json, audit_check.json, disputes.csv, referee_sheet.csv and
referee_ids.txt, written write-once by the code of commit e42d61d. For the SAME inputs, the current code must
reproduce those five files byte for byte (write_once -> 'unchanged'), so stage 3 can run on that folder; new
fields go only into stage-3 files (qrels.tsv, qrels_meta.json). Two checks:
  a) golden sha256s of the five files as written by e42d61d's s8_labels.py on this fake world (always runs);
  b) e42d61d's s8_labels.py itself (git show) writes stages 1+2 into a folder, then the current code runs
     stages 1+2+3 on that folder (skipped only when the git object is not available, e.g. a depth-1 checkout).
"""
import builtins
from datetime import datetime, timezone
import importlib.util
import io
import subprocess

import pytest

from retrieval_eval import s8_labels as m
from retrieval_eval.s8_judging import freeze_audit
from retrieval_eval.s8_runs import ROOT, bytes_sha
from tests.test_s8_labels import make_sheet, write_labels
from tests.test_s8_three_judges import (INVALID, M1, M2, MISSING, QIDS, REF_FIELDS, S1, S2, audit_rows, good_referee,
                                        ref, write_judge3)

OLD_COMMIT = 'e42d61d0ef032730f53625fd2e59a95bc9913c1f'  # main on 22 Oct: the code that wrote the real stage 1-2 files
FROZEN_AT = datetime(2026, 10, 22, 9, 0, 0, tzinfo=timezone.utc)
GOLDEN = {  # sha256 of the files e42d61d's run_merge writes for world(mode) below (stages 1 + 2)
    'three_judge': {
        'agreement_abc.json': 'c925e62d4e470c9c87a0ff2fc96598e5516f61b5952508cbce0b08a09f823ab4',
        'audit_check.json': '398625bafd86d062383b596084d7582a9e5a7671d7d1bbbc8cfea5f8d586e988',
        'disputes.csv': 'b04fe48154898d77389c28de3e460cb74c1f04c9e8137cb55e2453e0f4d024fa',
        'referee_sheet.csv': 'd4e288329ae179d6345aff11a5c8e37cc0207792098267d3c6120a8cf3b6066b',
        'referee_ids.txt': '0851b1c4461bc49df33f92d1689658883e8ea44ce72023a4421810cae1162767'},
    'two_judge_fallback': {
        'agreement_ab.json': 'ca32606d3ab1c0925c3b1adbfed2addbb6d99716b0a9694d4b313b27c88cc9b3',
        'audit_check.json': '5590fc544ee6ebeae2d66a1c2238984c35187d5afb8b8e5e9741f0387455e521',
        'disputes.csv': '5a25ae163e6903c70cc286fef9da13c84a3bc5d893098a2485ecfe5eafef4c9d',
        'referee_sheet.csv': '74ed58827b7a98e66f1ad342459138b92b8935ab6699180a5787593f87356049',
        'referee_ids.txt': 'b6ef01c056c77d44c0e55eab40906df2029f9b2e46080fd219837ba68b0d98b3'}}


def windows_text_open(real):
    """Windows text mode on any OS: a text-mode write with newline=None turns '\n' into '\r\n' (os.linesep on
    Windows). Reads, binary writes and newline='' writes are unchanged, as on Windows."""
    def opened(file, mode='r', buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
        if 'b' not in mode and any(c in mode for c in 'wax+') and newline is None:
            newline = '\r\n'
        return real(file, mode, buffering, encoding, errors, newline, closefd, opener)
    return opened


@pytest.fixture(autouse=True, params=['native', 'windows_text_mode'])
def line_endings(request, monkeypatch):
    """Every test here runs twice: with this OS's text mode, and with Windows text mode simulated (the laptop:
    core.autocrlf=true, CRLF text writes). Path.write_text / Path.open reach io.open; open() is builtins.open."""
    if request.param == 'windows_text_mode':
        monkeypatch.setattr(io, 'open', windows_text_open(io.open))
        monkeypatch.setattr(builtins, 'open', windows_text_open(builtins.open))
    return request.param


def stage12_names(mode):
    return tuple(GOLDEN[mode])


def world(base, mode):
    """A deterministic fake run folder: sheet/ (+ sheet_meta.json), three judge folders, a frozen audit of 40 items.
    Returns run_merge keyword arguments (stages 1 + 2), a function that writes the referee file for stage 3 (from
    referee_ids.txt, after stage 2) and the item count."""
    (base / 'sheet').mkdir(parents=True)
    rows = make_sheet(base / 'sheet' / 's8_sheet.csv', QIDS)
    grades = {r['item_id']: ((3, 2, 3) if k % 2 == 0 else (0, 1, 0)) for k, r in enumerate(rows)}
    grades.update({S1: (1, 2, 1), S2: (2, 3, 0), M1: (1, 0, INVALID),
                   M2: (MISSING, 2, 1) if mode == 'three_judge' else (1, 2, 1)})
    files = {f'{x.lower()}_files': write_judge3(base / x, rows, {i: g[n] for i, g in grades.items()})
             for n, x in enumerate('ABC')}
    audit = base / 'audit'
    audit.mkdir()
    ids = [r['item_id'] for r in rows[:40]]
    for name in ('audit_ids.txt', 'audit_order.txt'):  # hashed: explicit LF bytes on every OS (the golden shas)
        (audit / name).write_bytes(''.join(i + '\n' for i in ids).encode('utf-8'))
    (audit / 'audit_sheet.csv').write_bytes(b'x')
    labels = write_labels(audit / 'labels_audit_fake.csv', audit_rows(rows, grades))
    freeze_audit(labels, audit, audit / 'audit_order.txt', now=FROZEN_AT, minimum=40)
    kw = dict(sheet=base / 'sheet' / 's8_sheet.csv', out=base / 'labels', bootstrap=200, audit_file=labels,
              freeze_path=audit / 'audit_freeze.json', _test_skip_judge_freeze=True, **files)
    if mode == 'two_judge_fallback':
        kw['fallback_reason'] = 'fake: C over 2% after one re-run'
    planned = {r['item_id']: r for r in good_referee(rows)}

    def referee():
        ids = (kw['out'] / 'referee_ids.txt').read_text(encoding='utf-8').split()
        return write_labels(base / 'labels_referee.csv', [planned.get(i) or ref(i, 2) for i in ids], REF_FIELDS)
    return kw, referee, len(rows)


def old_module(tmp_path):
    """e42d61d's s8_labels.py, imported from git (None when the object is not in this checkout)."""
    try:
        source = subprocess.run(['git', 'show', f'{OLD_COMMIT}:retrieval_eval/s8_labels.py'], cwd=ROOT,
                                capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    path = tmp_path / 's8_labels_e42d61d.py'
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location('s8_labels_e42d61d', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('mode', sorted(GOLDEN))
def test_stage12_bytes_equal_the_22_oct_code_golden_shas(tmp_path, mode):
    kw, _, n = world(tmp_path / 'X', mode)
    s = m.run_merge(**kw, expected_items=n)
    assert s['stage'] == 2 and s['audit']['flag'] == 'PASS'
    assert {name: bytes_sha(kw['out'] / name) for name in stage12_names(mode)} == GOLDEN[mode]


@pytest.mark.parametrize('mode', sorted(GOLDEN))
def test_stage3_runs_on_a_folder_written_by_the_22_oct_code(tmp_path, mode):
    old = old_module(tmp_path)
    if old is None:
        pytest.skip(f'git object {OLD_COMMIT[:7]} not available (shallow checkout); the golden test covers this')
    kw, referee, n = world(tmp_path / 'X', mode)
    before = old.run_merge(**kw)  # the 22 Oct code: stages 1 + 2
    assert set(before['states']) == set(stage12_names(mode)) and set(before['states'].values()) == {'written'}
    assert {name: bytes_sha(kw['out'] / name) for name in stage12_names(mode)} == GOLDEN[mode]
    after = m.run_merge(**kw, referee_file=referee(), expected_items=n)  # the current code: stages 1 + 2 + 3
    assert after['stage'] == 3
    assert {k: v for k, v in after['states'].items() if k in stage12_names(mode)} == \
        {name: 'unchanged' for name in stage12_names(mode)}
    assert {k: v for k, v in after['states'].items() if k not in stage12_names(mode)} == \
        {'qrels.tsv': 'written', 'qrels_meta.json': 'written'}
