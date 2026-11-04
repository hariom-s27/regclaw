"""Over-claim lint: the ONE place for wording an S10 output must never use.

"mandatory" means "must be addressed (test, existing data or documented rationale)".
The tool never states approval, compliance, a guarantee, a test obligation or an FDA
requirement. Matching is case-insensitive with word boundaries on NFKC-normalised,
whitespace-collapsed text.

Consistency rule (tested in tests/test_s10_overclaim.py):
- approval / compliance / guarantee VOCABULARY is flagged in any form, negated or not
  ("not approved", "unapproved", "FDA-approved predicate", "non-compliant", "no guarantee").
  The tool has no basis to state either status, so a negation is still an over-claim.
- REQUIREMENT statements are flagged only in the affirmative form ("test required",
  "testing is required", "must be tested", "FDA requires", "FDA-required").
  Neutral or negated forms are allowed: "not necessarily tested", "a test may address",
  "FDA recommends", "FDA does not require".
- Neutral pathway nouns are allowed: "premarket approval (PMA)" is NOT flagged
  ("approval" noun), because naming a pathway claims no status.
"""
import re
import unicodedata

PATTERNS = {
    'approved': r'\b(?:un|pre)?approved\b|\bapproves?\b',
    'compliant': r'\b(?:non-?)?complian(?:t|ce)\b|\bcompl(?:y|ies)\s+with\b',
    'guarantee': r'\bguarantee(?:s|d)?\b',
    'test_required': r'\b(?:tests?|testing)\s+(?:is\s+|are\s+)?required\b',
    'must_be_tested': r'\bmust\s+be\s+tested\b',
    'fda_requires': r'\bfda\W{0,3}(?:requires?|required|mandates?|mandated)\b',
}
_COMPILED = {name: re.compile(rx, re.IGNORECASE) for name, rx in PATTERNS.items()}


def normalise(text):
    return ' '.join(unicodedata.normalize('NFKC', str(text)).casefold().split())


def find_overclaims(text, extra=()):
    """Names of the patterns that hit `text`. `extra` = literal phrases (e.g. the
    labels YAML banned_phrases), matched with the same word-boundary rule as before."""
    cleaned = normalise(text)
    hits = [name for name, rx in _COMPILED.items() if rx.search(cleaned)]
    for phrase in extra:
        if re.search(r'(?<!\w)' + re.escape(normalise(phrase)) + r'(?!\w)', cleaned):
            hits.append('yaml:' + phrase)
    return hits


def _walk(value, path):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f'{path}.{key}' if path else str(key))
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            yield from _walk(item, f'{path}[{i}]')


# Free-text fields of an assembled S10 output (identifiers/provenance are not prose).
TEXT_FIELDS = ('standards', 'notes', 'assumptions', 'caveat', 'disclaimer', 'ruleset')
SKIP_KEYS = {'part', 'edition', 'clause_ids', 'rule_trace', 'label', 'endpoints', 'suggested_route'}


def lint_output(result, extra=()):
    """Every over-claim in the prose of an assembled output: [{'field', 'pattern'}]."""
    hits = []
    for field in TEXT_FIELDS:
        for path, text in _walk(result.get(field), field):
            if SKIP_KEYS & set(re.split(r'[.\[\]]', path)):
                continue
            hits += [dict(field=path, pattern=name) for name in find_overclaims(text, extra)]
    return hits
