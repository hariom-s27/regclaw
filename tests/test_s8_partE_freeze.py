"""S8 Part E freeze (Addendum 4): the committed addendum matches the frozen sha (LF-normalised)."""
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FREEZE = json.loads((ROOT / 'retrieval_eval/s8_partE_freeze.json').read_text(encoding='utf-8'))
ADDENDUM_4 = 'retrieval_eval/S8_PREREGISTRATION_ADDENDUM_4.md'


def lf_sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()


@pytest.mark.parametrize('rel', sorted(FREEZE['docs_lf_normalised_sha256']))
def test_committed_docs_match_the_freeze(rel):
    assert lf_sha(ROOT / rel) == FREEZE['docs_lf_normalised_sha256'][rel]


def test_freeze_covers_addendum_4_only():
    assert set(FREEZE['docs_lf_normalised_sha256']) == {ADDENDUM_4}
    assert set(FREEZE) == {'created', 'purpose', 'docs_lf_normalised_sha256', 'descriptive_items', 'bootstrap',
                           'label'}


def test_addendum_4_states_the_fixed_details():
    text = (ROOT / ADDENDUM_4).read_text(encoding='utf-8')
    assert 'kept** in all analyses and flagged "swap-eligible (no replacement possible)"' in text
    assert '**MRR is MRR@10** for every system' in text
    assert '**No R@10 spread**' in text
    assert 'These are added here and are **descriptive only**' in text
    assert 'provisional (GPT): stage 1 = Azure gpt-5-mini' in text


def test_freeze_constants():
    assert FREEZE['bootstrap'] == {'resamples': 10000, 'seed': 20261019, 'interval': '95% percentile'}
    assert FREEZE['label'] == 'provisional (GPT): stage 1 = Azure gpt-5-mini'
    assert len(FREEZE['descriptive_items']) == 5


def test_part_d_freeze_does_not_mention_addendum_4():
    text = (ROOT / 'retrieval_eval/s8_partD_freeze.json').read_text(encoding='utf-8')
    assert 'ADDENDUM_4' not in text


NOTE_1_LF_SHA = '9247fdba5f1abf6a7edff5e7b0bdb360b276eb1aad2fc5d244d9cf32149965f3'


def test_addendum_4_note_1_matches_its_sha_and_records_the_decisions():
    path = ROOT / 'retrieval_eval/S8_PREREGISTRATION_ADDENDUM_4_NOTE_1.md'
    assert path.is_file()
    assert lf_sha(path) == NOTE_1_LF_SHA
    raw = path.read_bytes()
    assert not raw.startswith(b'\xef\xbb\xbf')  # no BOM
    text = raw.decode('utf-8')
    for phrase in ('Conditional R@10', 'Expansion precision', 'n/a', 'ΔR'):
        assert phrase in text
