"""Over-claim lint (agreed item 3) and guard hardening: wording only, labels never change."""
import pytest

import _netguard
from standards.classifier import FakeLLM, LLMResponse
from standards.data_loader import load_data
from standards.demo import FakeRetriever, example_profiles
from standards.engine import evaluate
from standards.guard import guard
from standards.overclaim import PATTERNS, find_overclaims, lint_output
from standards import ruleset as RS
from standards.tool import get_applicable_standards

DATA = load_data()
BANNED = DATA['labels']['banned_phrases']


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')


# Decision table (also in the step log): text -> pattern expected, or None = allowed.
FLAG = [
    ('The device is approved.', 'approved'),
    ('This material is NOT approved for implants.', 'approved'),          # negation still flagged
    ('Compare with the FDA-approved predicate.', 'approved'),              # status claim about a predicate
    ('An unapproved coating.', 'approved'),
    ('The device is compliant with ISO 10993-1.', 'compliant'),
    ('The device is non-compliant.', 'compliant'),
    ('This ensures compliance.', 'compliant'),
    ('The device complies with Annex I.', 'compliant'),
    ('This guarantees safety.', 'guarantee'),
    ('There is no guarantee of safety.', 'guarantee'),                      # negation still flagged
    ('Biocompatibility is guaranteed.', 'guarantee'),
    ('Cytotoxicity test required.', 'test_required'),
    ('Testing is required for this endpoint.', 'test_required'),
    ('Two tests are required.', 'test_required'),
    ('The part must be tested.', 'must_be_tested'),
    ('FDA requires this evaluation.', 'fda_requires'),
    ('This is FDA-required.', 'fda_requires'),
    ('FDA mandates a test.', 'fda_requires'),
    ('ＦＤＡ　ｒｅｑｕｉｒｅｓ it', 'fda_requires'),                           # NFKC full-width
    ('fda\nrequires it', 'fda_requires'),                                  # whitespace collapsed
]
ALLOW = [
    'Must be addressed in the biological evaluation, not necessarily tested.',
    'May be met by existing data, a rationale, or a test.',
    'A test may address this endpoint.',
    'FDA recommends considering this endpoint.',
    'FDA does not require a specific test here.',                           # negated requirement: allowed
    'The premarket approval (PMA) pathway is out of scope.',               # pathway noun: allowed
    'Requirements of MDR Annex I are a general safety and performance requirement.',
    'Testing may be considered.',
    'Conformance to recognised standards is voluntary.',
]


@pytest.mark.parametrize('text,pattern', FLAG)
def test_flagged_phrases(text, pattern):
    assert pattern in find_overclaims(text)


@pytest.mark.parametrize('text', ALLOW)
def test_allowed_neutral_or_negated_phrases(text):
    assert find_overclaims(text) == []


def test_one_place_and_yaml_phrases_still_covered():
    assert set(PATTERNS) == {'approved', 'compliant', 'guarantee', 'test_required', 'must_be_tested', 'fda_requires'}
    for phrase in BANNED:      # labels.v1.yaml list keeps working through the same function
        assert find_overclaims('x ' + phrase.upper() + ' y', BANNED)


def test_fixed_texts_are_clean():
    texts = [DATA['labels']['caveat'], *DATA['labels']['labels'].values(), RS.FDA_DISCLAIMER, RS.ISO2025_DISCLAIMER,
             RS.ISO2025_MIXED_DISCLAIMER, RS.FDA_RULESET, RS.ISO2025_RULESET, RS.ISO2025_MIXED_RULESET, RS.ISO2018_RULESET]
    assert [t for t in texts if find_overclaims(t, BANNED)] == []


@pytest.mark.parametrize('key', sorted(example_profiles()))
def test_engine_notes_and_outputs_have_no_overclaims(key):
    profile = example_profiles()[key]
    profile['target_markets'] = ['EU', 'US']                       # R15 notes
    profile['labeled_non_pyrogenic'] = True                        # R07 notes
    profile['components'][0]['materials'][0]['novel'] = True       # R12 notes
    rules = evaluate(profile, DATA)
    assert [n for n in rules['notes'] + rules['assumptions'] if find_overclaims(n, BANNED)] == []
    result = get_applicable_standards(profile, FakeRetriever(), llm=FakeLLM(), data=DATA)
    assert result['provenance']['overclaim_hits'] == [] and lint_output(result, BANNED) == []


def _guarded(items, notes):
    rules = evaluate(example_profiles()['E2'], DATA)
    return rules, guard(rules, LLMResponse(dict(standards=items, notes=notes)), DATA,
                        [dict(clause_id='fda-iso10993-1:III.A')])


def _item(part='ISO 10993-1', label='mandatory', reason='Reason.'):
    return dict(part=part, label=label, reason=reason, clause_ids=[], unsupported_by_corpus=True,
                superseded_edition=False)


@pytest.mark.parametrize('reason', ['This guarantees biocompatibility.', 'FDA requires it.', 'Not approved yet.'])
def test_guard_replaces_new_pattern_reasons_label_unchanged(reason):
    rules, out = _guarded([_item(reason=reason)], [])
    row = next(p for p in out['standards'] if p['part'] == 'ISO 10993-1')
    assert row['reason'] == DATA['labels']['labels']['mandatory'] and row['label'] == 'mandatory'
    assert any(e['type'] == 'banned_wording' for e in out['disagreements'])


def test_guard_drops_overclaiming_llm_note_and_logs_pattern_not_text():
    secret = 'FDA requires this and it guarantees approval'
    rules, out = _guarded([], [dict(text=secret, clause_ids=['fda-iso10993-1:III.A']),
                               dict(text='Consider device-specific standards.', clause_ids=[])])
    texts = [n['text'] for n in out['notes']]
    assert secret not in texts and 'Consider device-specific standards.' in texts
    event = next(e for e in out['disagreements'] if e['type'] == 'banned_wording_note')
    assert event == dict(type='banned_wording_note', patterns=['guarantee', 'fda_requires'])
    assert secret not in repr(out['disagreements'])


def test_guard_hardening_never_changes_labels():
    items = [_item(p['part'], p['label'], 'FDA requires this; guaranteed.') for p in evaluate(example_profiles()['E2'], DATA)['parts']]
    clean = [_item(p['part'], p['label'], 'Plain reason.') for p in evaluate(example_profiles()['E2'], DATA)['parts']]
    _, dirty_out = _guarded(items, [dict(text='Device is compliant.', clause_ids=[])])
    _, clean_out = _guarded(clean, [])
    assert [(p['part'], p['label']) for p in dirty_out['standards']] == [(p['part'], p['label']) for p in clean_out['standards']]
