"""S8 pre-registration addendum 3 (completion-token budget) exists before any c2 run."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_addendum3_records_budget_reason_and_failure_rule():
    raw = (ROOT / 'retrieval_eval/S8_PREREGISTRATION_ADDENDUM_3.md').read_text(encoding='utf-8')
    text = ' '.join(raw.split())
    assert 'Dated 2026-10-20' in text and 'before any relevance label' in text
    for needle in ('4,000', '8,000', '`c2`', '19 of 39', 'v1 Q4/a', 'v1 Q12/b', '≤ 900',
                   'counts as a system failure', 'never excluded, regenerated or given a fallback',
                   'The stop guard is unchanged', 'Addendum 2 is reserved'):
        assert needle in text, needle
