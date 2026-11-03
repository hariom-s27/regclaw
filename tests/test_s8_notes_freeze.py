"""S8 notes freeze (Addendum 2 Note 4 section 6): the committed notes match s8_notes_freeze.json (LF-normalised)."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from retrieval_eval import s8_labels

ROOT = Path(__file__).resolve().parents[1]
FREEZE_PATH = ROOT / 'retrieval_eval/s8_notes_freeze.json'
FREEZE = json.loads(FREEZE_PATH.read_text(encoding='utf-8'))
NOTE_4 = 'retrieval_eval/S8_PREREGISTRATION_ADDENDUM_2_NOTE_4.md'
NOTE_1 = 'retrieval_eval/S8_PREREGISTRATION_ADDENDUM_4_NOTE_1.md'


def lf_sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()


@pytest.mark.parametrize('rel', sorted(FREEZE['docs_lf_normalised_sha256']))
def test_committed_notes_match_the_freeze(rel):
    assert lf_sha(ROOT / rel) == FREEZE['docs_lf_normalised_sha256'][rel]


def test_freeze_covers_note_4_and_addendum_4_note_1_only():
    assert set(FREEZE['docs_lf_normalised_sha256']) == {NOTE_4, NOTE_1}
    assert set(FREEZE) == {'created', 'purpose', 'docs_lf_normalised_sha256'}


def note_4_bytes(root):
    """The COMMITTED Note 4 (git blob, independent of core.autocrlf), or, without git, the working copy with
    CRLF -> LF (a Windows autocrlf checkout holds CRLF). Returns (bytes, source)."""
    try:
        blob = subprocess.run(['git', 'show', f'HEAD:{NOTE_4}'], cwd=root, capture_output=True, check=True).stdout
        return blob, 'git_blob'
    except (OSError, subprocess.CalledProcessError):
        return (Path(root) / NOTE_4).read_bytes().replace(b'\r\n', b'\n'), 'working_copy_lf_normalised'


def assert_note_4_bytes(raw, source):
    assert not raw.startswith(b'\xef\xbb\xbf') and raw.endswith(b'\n')  # no BOM, final newline
    assert hashlib.sha256(raw).hexdigest() == FREEZE['docs_lf_normalised_sha256'][NOTE_4]  # the LF sha
    if source == 'git_blob':
        assert b'\r' not in raw  # committed with LF


def test_note_4_bytes_lf_no_bom_final_newline():
    assert_note_4_bytes(*note_4_bytes(ROOT))


def _git(repo, *args):
    subprocess.run(['git', '-c', 'core.autocrlf=false', '-c', 'user.name=t', '-c', 'user.email=t@t',
                    '-c', 'commit.gpgsign=false', *args], cwd=repo, capture_output=True, check=True)


def test_note_4_byte_check_on_an_autocrlf_checkout_reads_the_lf_blob(tmp_path):
    """Windows simulated: a tmp repo holds the committed LF Note 4; `git -c core.autocrlf=true checkout` writes a
    CRLF working copy (git converts on checkout on any OS), as on the laptop. The check reads the LF blob."""
    if shutil.which('git') is None:
        pytest.skip('git not available')
    lf, _ = note_4_bytes(ROOT)
    (tmp_path / NOTE_4).parent.mkdir(parents=True)
    (tmp_path / NOTE_4).write_bytes(lf)
    _git(tmp_path, 'init', '-q')
    _git(tmp_path, 'add', NOTE_4)
    _git(tmp_path, 'commit', '-q', '-m', 'note 4')
    (tmp_path / NOTE_4).unlink()
    subprocess.run(['git', '-c', 'core.autocrlf=true', 'checkout', '--', NOTE_4], cwd=tmp_path, check=True,
                   capture_output=True)
    working = (tmp_path / NOTE_4).read_bytes()
    assert b'\r\n' in working and working.replace(b'\r\n', b'\n') == lf  # the simulation is real: CRLF copy
    raw, source = note_4_bytes(tmp_path)
    assert source == 'git_blob' and raw == lf
    assert_note_4_bytes(raw, source)


def test_note_4_byte_check_without_git_falls_back_to_the_lf_normalised_copy(tmp_path, monkeypatch):
    lf, _ = note_4_bytes(ROOT)
    (tmp_path / NOTE_4).parent.mkdir(parents=True)
    (tmp_path / NOTE_4).write_bytes(lf.replace(b'\n', b'\r\n'))  # a Windows autocrlf working copy

    def no_git(*a, **kw):
        raise FileNotFoundError('git')
    monkeypatch.setattr(subprocess, 'run', no_git)
    raw, source = note_4_bytes(tmp_path)
    assert source == 'working_copy_lf_normalised' and raw == lf
    assert_note_4_bytes(raw, source)


def test_earlier_freeze_files_do_not_mention_note_4():
    for name in ('s8_partD_freeze.json', 's8_partE_freeze.json'):
        assert 'NOTE_4' not in (ROOT / 'retrieval_eval' / name).read_text(encoding='utf-8')


def test_reason_codes_equal_the_list_in_note_4_section_1():
    text = (ROOT / NOTE_4).read_text(encoding='utf-8')
    section = text.split('## 1.', 1)[1].split('## 2.', 1)[0]
    line = next(x for x in section.splitlines() if '`reason_code` ∈' in x)
    listed = re.findall(r'`([a-z_]+)`', line.split('∈', 1)[1])
    assert listed == list(s8_labels.REASON_CODES) == ['clear_reading', 'guideline_gap', 'unclear_text']
