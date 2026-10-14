"""The nine-field clause contract and warning-only parser diagnostics."""
from collections import Counter, defaultdict
import math
import re

FIELDS = {'id', 'doc', 'number', 'title', 'text', 'parent', 'source_pdf', 'page', 'informative'}


def validate(clauses, manifest):
    ids = set()
    for c in clauses:
        if set(c) != FIELDS:
            raise ValueError(f"{c.get('id')}: expected exactly nine fields")
        if c['doc'] not in manifest or c['id'] != c['doc'] + ':' + c['number']:
            raise ValueError(f"{c['id']}: invalid document or id")
        if c['id'] in ids:
            raise ValueError(f"{c['id']}: duplicate id")
        ids.add(c['id'])
        if type(c['page']) is not int or c['page'] < 1 or type(c['informative']) is not bool:
            raise ValueError(f"{c['id']}: invalid page or informative")
        if any(not isinstance(c[k], str) for k in ('id', 'doc', 'number', 'title', 'text', 'source_pdf')):
            raise ValueError(f"{c['id']}: invalid string field")
    for c in clauses:
        if c['parent'] is not None and c['parent'] not in ids:
            raise ValueError(f"{c['id']}: dangling parent")
    return True


def percentile(values, q):
    if not values:
        return 0
    values = sorted(values)
    x = (len(values) - 1) * q
    a = math.floor(x)
    return values[a] + (values[math.ceil(x)] - values[a]) * (x - a)


def quality_report(clauses, raw_text='', stripped_chars=0, pages=None):
    """Return Q1–Q8 for one document; never reject a clause."""
    pages = pages or max((c['page'] for c in clauses), default=0)
    grams = defaultdict(set)
    for c in clauses:
        words = c['text'].casefold().split()
        for i in range(len(words) - 4):
            grams[' '.join(words[i:i + 5])].add(c['page'])
    # One-page documents cannot establish repetition.
    leaks = sorted(g for g, ps in grams.items() if len(ps) >= max(2, math.ceil(pages * .3)))
    text = '\n'.join(c['text'] for c in clauses)
    raw_ids = len(re.findall(r'\d-\d', raw_text))
    kept_ids = len(re.findall(r'\d-\d', text))
    denominator = max(0, len(raw_text) - stripped_chars)
    pairs = [(c['parent'], c['number']) for c in clauses]
    sizes = [len(c['text']) for c in clauses]
    return {
        'Q1': {'count': len(leaks), 'examples': leaks[:3]},
        'Q2': len(re.findall(r'\.{3,}\s*\d+\s*$', text, re.M)),
        'Q3': len(pairs) - len(set(pairs)),
        'Q4': sum(ch == '\ufffd' or '\ufb00' <= ch <= '\ufb06' or
                  '\ue000' <= ch <= '\uf8ff' or 0xF0000 <= ord(ch) <= 0x10FFFD for ch in text),
        'Q5': {'raw': raw_ids, 'kept': kept_ids, 'warning': raw_ids > 0 and kept_ids < .8 * raw_ids},
        'Q6': len(re.findall(r'[^\W\d_]\d', text)),
        'Q7': {'ratio': len(text) / denominator if denominator else 1,
               'warning': bool(denominator and len(text) / denominator < .98)},
        'Q8': {'p50': percentile(sizes, .5), 'p95': percentile(sizes, .95), 'max': max(sizes, default=0)},
    }
