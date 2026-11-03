"""S8 notes freeze (Addendum 2 Note 4 section 6): the committed notes match s8_notes_freeze.json (LF-normalised)."""
import hashlib
import json
from pathlib import Path
import re

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


def test_note_4_bytes_lf_no_bom_final_newline():
    raw = (ROOT / NOTE_4).read_bytes()
    assert not raw.startswith(b'\xef\xbb\xbf') and b'\r' not in raw and raw.endswith(b'\n')


def test_earlier_freeze_files_do_not_mention_note_4():
    for name in ('s8_partD_freeze.json', 's8_partE_freeze.json'):
        assert 'NOTE_4' not in (ROOT / 'retrieval_eval' / name).read_text(encoding='utf-8')


def test_reason_codes_equal_the_list_in_note_4_section_1():
    text = (ROOT / NOTE_4).read_text(encoding='utf-8')
    section = text.split('## 1.', 1)[1].split('## 2.', 1)[0]
    line = next(x for x in section.splitlines() if '`reason_code` ∈' in x)
    listed = re.findall(r'`([a-z_]+)`', line.split('∈', 1)[1])
    assert listed == list(s8_labels.REASON_CODES) == ['clear_reading', 'guideline_gap', 'unclear_text']
