"""Read-only checks of the committed S10 pre-registration manifest (writes no files)."""
import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from standards.ruleset import normalised_sha

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'eval' / 's10' / 'manifests' / 'prereg_v1.json'
SPEC = ROOT / 'specs' / 'S10_PREREGISTRATION.md'
FIELDS = {'format', 'file', 'sha256_lf', 'bytes_lf', 'lines', 'version',
          'approved', 'decisions', 'created_at'}


@pytest.fixture(scope='module')
def manifest():
    assert MANIFEST.is_file(), f'missing committed manifest: {MANIFEST}'
    return json.loads(MANIFEST.read_text(encoding='utf-8'))


def test_manifest_fields_and_format(manifest):
    assert set(manifest) == FIELDS
    assert manifest['format'] == 's10-prereg-manifest.v1'
    assert manifest['file'] == 'specs/S10_PREREGISTRATION.md'
    assert re.fullmatch(r'[0-9a-f]{64}', manifest['sha256_lf'])
    assert isinstance(manifest['bytes_lf'], int) and manifest['bytes_lf'] > 0
    assert isinstance(manifest['lines'], int) and manifest['lines'] > 0
    assert manifest['version'] == 'v1.0'
    assert manifest['approved'] == '2026-11-14'
    assert manifest['decisions'] == 'S10P-01..S10P-29 (D20)'
    created = datetime.fromisoformat(manifest['created_at'])
    assert created.utcoffset() is not None and created.utcoffset().total_seconds() == 0


def test_local_spec_matches_manifest(manifest):
    if not SPEC.is_file():
        pytest.skip('specs/S10_PREREGISTRATION.md is local-only (git-ignored); recompute check skipped')
    assert normalised_sha(SPEC.read_bytes()) == manifest['sha256_lf']
