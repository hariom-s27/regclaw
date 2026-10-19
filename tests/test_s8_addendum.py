"""S8 pre-registration addendum 1 exists and records items a-g before any eval run."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_addendum_exists_and_predates_runs():
    text = (ROOT / 'retrieval_eval/S8_PREREGISTRATION_ADDENDUM_1.md').read_text(encoding='utf-8')
    assert 'Dated 2026-10-20' in text and 'before any S8 eval query was run' in text
    for item in ('## a)', '## b)', '## c)', '## d)', '## e)', '## f)', '## g)'):
        assert item in text
    assert '320' in text and 'R@final' in text and 'Holm' in text and 'Urbano' in text
