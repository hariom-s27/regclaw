"""v1.1 is frozen: any edit requires a new version (and new queries; see S8_PREREGISTRATION.md)."""
import json
from pathlib import Path

import pytest

from retrieval.hybrid_retriever import PROMPT_VERSIONS, SCHEMA_FALLBACK, prompt_hashes

ROOT = Path(__file__).resolve().parents[1]


def frozen(name):
    return json.loads((ROOT / 'retrieval_eval' / name).read_text(encoding='utf-8'))


@pytest.mark.parametrize('record_field,code_field', [
    ('template_sha', 'prompt_template_sha'),
    ('fewshot_sha', 'fewshot_sha'),
])
def test_current_prompt_matches_v1_1_freeze(record_field, code_field):
    record = frozen('s8_prompt_freeze_v1_1.json')
    assert record['version'] == '1.1' and record['parent'] == '1.0' and record['status'] == 'FROZEN'
    assert prompt_hashes(SCHEMA_FALLBACK, '1.1')[code_field] == record[record_field], (
        'v1.1 is frozen; a prompt change needs a new version evaluated on new queries')


def test_v1_1_freeze_shares_v1_provenance_fields():
    v1, v11 = frozen('s8_prompt_freeze.json'), frozen('s8_prompt_freeze_v1_1.json')
    for field in ('schema_serialisation_version', 'live_schema_sha_at_freeze', 'role',
                  'provisional_model', 'effort', 'anthropic_model', 'output_mechanism'):
        assert v11[field] == v1[field], field
    assert v11['arm'] == 'secondary (pre-registered)' and len(v11['changes']) == 2
    assert set(PROMPT_VERSIONS) == {'1.0', '1.1'}
