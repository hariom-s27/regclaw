"""Prompt edits require a versioned freeze amendment and a new S8 run."""
import json
from pathlib import Path

import pytest

from retrieval.hybrid_retriever import SCHEMA_FALLBACK, prompt_hashes


@pytest.mark.parametrize('record_field,code_field', [
    ('template_sha', 'prompt_template_sha'),
    ('fewshot_sha', 'fewshot_sha'),
])
def test_current_prompt_matches_v1_freeze(record_field, code_field):
    path = Path(__file__).resolve().parents[1] / 'retrieval_eval/s8_prompt_freeze.json'
    frozen = json.loads(path.read_text(encoding='utf-8'))
    assert frozen['version'] == '1.0' and frozen['status'] == 'FROZEN'
    assert prompt_hashes(SCHEMA_FALLBACK)[code_field] == frozen[record_field], (
        'Prompt change requires version 1.1 + rerun all 16 queries + log entry')
