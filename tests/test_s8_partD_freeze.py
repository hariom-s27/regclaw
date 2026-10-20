"""S8 Part D freeze (Addendum 2): the committed guideline / addendum / instructions match the frozen shas."""
import hashlib
import json
from pathlib import Path

import pytest

from retrieval_eval import s8_judging as j

ROOT = Path(__file__).resolve().parents[1]
FREEZE = json.loads((ROOT / 'retrieval_eval/s8_partD_freeze.json').read_text(encoding='utf-8'))


def lf_sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()


@pytest.mark.parametrize('rel', sorted(FREEZE['docs_lf_normalised_sha256']))
def test_committed_docs_match_the_freeze(rel):
    assert lf_sha(ROOT / rel) == FREEZE['docs_lf_normalised_sha256'][rel]


def test_freeze_covers_guideline_addendum_instructions_and_kit_bytes():
    docs = FREEZE['docs_lf_normalised_sha256']
    assert set(docs) == {'retrieval_eval/S8_RELEVANCE_GUIDELINE_v1.0.md',
                         'retrieval_eval/S8_PREREGISTRATION_ADDENDUM_2.md', 'retrieval_eval/s8_judge_instructions.md'}
    assert hashlib.sha256(j.guideline_bytes()).hexdigest() == docs['retrieval_eval/S8_RELEVANCE_GUIDELINE_v1.0.md']
    assert hashlib.sha256(j.instructions_bytes()).hexdigest() == docs['retrieval_eval/s8_judge_instructions.md']
    audit = FREEZE['audit_raw_bytes_sha256']
    assert audit['data/runs/azure_openai/s8/c2/audit/audit_sheet.csv'].startswith('606d4bdb')
    assert audit['data/runs/azure_openai/s8/c2/audit/audit_ids.txt'].startswith('97069918')
    assert FREEZE['audit']['minimum_n'] == j.AUDIT_N_MIN and FREEZE['audit']['order_seed'] == j.ORDER_SEED
    assert sum(FREEZE['audit']['first_100_per_query'].values()) == 100


def test_addendum_records_the_fixed_order_and_median_rule():
    text = (ROOT / 'retrieval_eval/S8_PREREGISTRATION_ADDENDUM_2.md').read_text(encoding='utf-8')
    assert 'round-robin in the order of queries.yaml (R1, R2, R3, Q1 … Q13)' in text
    assert 'median gap between consecutive saves (all save timestamps sorted by time' in text
    assert 'Minimum N = 100' in text and 'seed 20261021' in text


@pytest.mark.parametrize('rel', sorted(FREEZE['audit_raw_bytes_sha256']))
def test_local_audit_files_match_the_freeze(rel):
    path = ROOT / rel
    if not path.exists():
        pytest.skip('local data/ only (not in CI)')
    assert hashlib.sha256(path.read_bytes()).hexdigest() == FREEZE['audit_raw_bytes_sha256'][rel]
