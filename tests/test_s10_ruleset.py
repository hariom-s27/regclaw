"""Ruleset identity on every S10 output + content lock on the rule YAMLs (agreed items 1-2)."""
import shutil

import pytest

import _netguard
from standards.classifier import FakeLLM
from standards.data_loader import load_data
from standards.demo import FakeRetriever, example_profiles
from standards import ruleset as RS
from standards.tool import get_applicable_standards

DATA = load_data()
FDA_DISCLAIMER = ("Based on FDA's 2023 biocompatibility guidance; ISO 10993-1:2025 is partially recognised by "
                  "FDA (May 2026) and its tables differ. Not reviewed against ISO 10993-1:2025.")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')


def test_stored_shas_match_current_rule_yamls():
    assert RS.check_ruleset() == RS.RULESET_SHA256
    assert set(RS.RULESET_FILES_SHA256) == {'fda2023_attachment_a.v1.yaml', 'iso2025_anchor_rows.v1.yaml',
                                            'endpoint_to_part.v1.yaml', 'labels.v1.yaml'}
    assert RS.RULESET_VERSION == 'S10-ruleset.v1'


@pytest.mark.parametrize('name', sorted(RS.RULESET_FILES_SHA256))
def test_one_changed_byte_fails_without_version_bump(tmp_path, name):
    folder = tmp_path / 'data'
    shutil.copytree(RS.Path(RS.__file__).with_name('data'), folder)
    raw = bytearray((folder / name).read_bytes())
    i = raw.index(b'version: 1') + len('version: ')
    raw[i:i + 1] = b'2'                                   # one byte changes
    (folder / name).write_bytes(bytes(raw))
    with pytest.raises(RS.RulesetChanged, match=name.replace('.', r'\.')):
        RS.check_ruleset(folder)


def test_matrix_value_byte_flip_fails(tmp_path):
    folder = tmp_path / 'data'
    shutil.copytree(RS.Path(RS.__file__).with_name('data'), folder)
    path = folder / RS.MATRIX_FILE
    text = path.read_bytes()
    flipped = text.replace(b'consider: true', b'consider: fals', 1)   # same length, one cell
    assert flipped != text
    path.write_bytes(flipped)
    with pytest.raises(RS.RulesetChanged):
        RS.check_ruleset(folder)


def to_crlf(raw: bytes) -> bytes:
    """CRLF copy built from LF-normalised bytes: correct whether the input is already CRLF (Windows) or LF."""
    return raw.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')


def simulate_checkout(folder, names, checkout):
    """checkout='crlf': the tmp copy looks like a Windows core.autocrlf=true working copy before the test acts."""
    if checkout == 'crlf':
        for name in names:
            p = folder / name
            p.write_bytes(to_crlf(p.read_bytes()))


def write_crlf_copy(folder, names):
    for name in names:
        p = folder / name
        crlf = to_crlf(p.read_bytes())
        assert b'\r\n' in crlf and b'\r\r\n' not in crlf
        p.write_bytes(crlf)


@pytest.mark.parametrize('checkout', ['lf', 'crlf'])
def test_crlf_checkout_still_matches(tmp_path, checkout):
    folder = tmp_path / 'data'
    shutil.copytree(RS.Path(RS.__file__).with_name('data'), folder)
    simulate_checkout(folder, RS.RULESET_FILES_SHA256, checkout)
    write_crlf_copy(folder, RS.RULESET_FILES_SHA256)
    assert RS.check_ruleset(folder) == RS.RULESET_SHA256


@pytest.mark.parametrize('key', sorted(example_profiles()))
def test_every_fda_output_carries_ruleset_and_exact_disclaimer(key):
    result = get_applicable_standards(example_profiles()[key], FakeRetriever(), llm=FakeLLM(), data=DATA)
    assert result['ruleset'] == 'FDA-2023-AttA (ISO 10993-1:2018 basis)'
    assert result['disclaimer'] == FDA_DISCLAIMER
    assert result['ruleset_version'] == RS.RULESET_VERSION and result['ruleset_sha256'] == RS.RULESET_SHA256


def test_fallback_output_also_carries_ruleset(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'dummy-not-a-key')       # a key in the OS env must not go live
    built = []
    monkeypatch.setattr('standards.tool.make_client', lambda role: built.append(role))
    result = get_applicable_standards(example_profiles()['E1'], FakeRetriever(), llm=None, data=DATA)
    assert built == []
    assert result['provenance']['llm_status'] == 'fallback'
    assert result['ruleset'].startswith('FDA-2023-AttA') and result['disclaimer'] == FDA_DISCLAIMER


def test_iso2025_output_never_prints_fda_ruleset():
    profile = dict(example_profiles()['E2'], framework='ISO_10993_1_2025')
    profile['components'][0]['exposure'] = dict(pattern='single', hours_per_use=24, contact_days=1)
    result = get_applicable_standards(profile, FakeRetriever(), llm=FakeLLM(), data=DATA)
    assert not result['assumptions'] or not any('FDA row used' in a for a in result['assumptions'])
    assert result['ruleset'] == RS.ISO2025_RULESET and result['disclaimer'] == RS.ISO2025_DISCLAIMER
    assert not result['ruleset'].startswith('FDA') and 'not FDA Attachment A' in result['ruleset']
    assert "Not FDA's 2023 Attachment A matrix" in result['disclaimer']


def test_iso2025_with_fda_fallback_is_labelled_mixed():
    profile = dict(example_profiles()['E3'], framework='ISO_10993_1_2025')   # indirect/blood rows unverified
    profile['components'][0]['contact_site'] = 'blood_path_indirect'
    profile['components'][0]['exposure'] = dict(pattern='single', contact_days=2)
    result = get_applicable_standards(profile, FakeRetriever(), llm=FakeLLM(), data=DATA)
    assert result['standards'][0]['framework_row_unverified']
    assert result['ruleset'] == RS.ISO2025_MIXED_RULESET and result['disclaimer'] == RS.ISO2025_MIXED_DISCLAIMER


def test_iso2018_uses_fda_matrix_and_says_so():
    profile = dict(example_profiles()['E1'], framework='ISO_10993_1_2018')
    result = get_applicable_standards(profile, FakeRetriever(), llm=FakeLLM(), data=DATA)
    assert result['ruleset'] == RS.ISO2018_RULESET and result['disclaimer'] == FDA_DISCLAIMER


def test_unknown_framework_raises():
    with pytest.raises(ValueError, match='Unknown framework'):
        RS.ruleset_for('ISO_10993_1_2031')


# ---- code lock: engine / guard / over-claim lint tied to ENGINE_VERSION
from standards import ENGINE_VERSION  # noqa: E402

CODE_DIR = RS.Path(RS.__file__).parent


def _code_copy(tmp_path):
    folder = tmp_path / 'code'
    folder.mkdir()
    for name in RS.ENGINE_FILES_SHA256:
        shutil.copy(CODE_DIR / name, folder / name)
    return folder


def test_engine_shas_match_current_code_and_version():
    assert set(RS.ENGINE_FILES_SHA256) == {'engine.py', 'guard.py', 'overclaim.py'}
    assert RS.ENGINE_FILES_VERSION == ENGINE_VERSION == 'S10.v1'
    assert RS.check_engine() == RS.ENGINE_SHA256


@pytest.mark.parametrize('name', sorted(RS.ENGINE_FILES_SHA256))
def test_one_changed_code_byte_fails_without_engine_version_bump(tmp_path, name):
    folder = _code_copy(tmp_path)
    raw = bytearray((folder / name).read_bytes())
    i = raw.index(b'"""') + 3                          # first docstring character
    raw[i:i + 1] = b'X' if raw[i:i + 1] != b'X' else b'Y'
    (folder / name).write_bytes(bytes(raw))
    with pytest.raises(RS.EngineChanged, match=name.replace('.', r'\.')):
        RS.check_engine(folder)


def test_engine_version_bump_without_new_shas_fails():
    with pytest.raises(RS.EngineChanged, match='ENGINE_FILES_VERSION'):
        RS.check_engine(version='S10.v2')


@pytest.mark.parametrize('checkout', ['lf', 'crlf'])
def test_crlf_code_copy_still_matches(tmp_path, checkout):
    folder = _code_copy(tmp_path)
    simulate_checkout(folder, RS.ENGINE_FILES_SHA256, checkout)
    write_crlf_copy(folder, RS.ENGINE_FILES_SHA256)
    assert RS.check_engine(folder) == RS.ENGINE_SHA256


def test_output_provenance_carries_engine_version_and_sha():
    result = get_applicable_standards(example_profiles()['E1'], FakeRetriever(), llm=FakeLLM(), data=DATA)
    assert result['provenance']['engine_version'] == ENGINE_VERSION
    assert result['provenance']['engine_sha256'] == RS.ENGINE_SHA256
