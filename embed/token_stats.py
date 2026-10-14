"""Offline cl100k_base counts for embed input v1 and R14 option (b)."""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from ingest.validate import percentile

ROOT = Path(__file__).resolve().parents[1]
TOKEN_LIMIT = 8191


def offline_encoding():
    """Fail before tiktoken can download its vocabulary when no local cache exists."""
    import tiktoken
    cache = Path(os.environ.get('TIKTOKEN_CACHE_DIR', os.environ.get('DATA_GYM_CACHE_DIR',
                                                                  str(Path(tempfile.gettempdir()) / 'data-gym-cache'))))
    vocabulary = cache / '9b5ad71b2ce5302211f9c61530b329a4922fc6a4'
    expected_sha = '223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7'
    if not vocabulary.is_file() or hashlib.sha256(vocabulary.read_bytes()).hexdigest() != expected_sha:
        raise RuntimeError('Offline cl100k_base vocabulary unavailable. Set TIKTOKEN_CACHE_DIR to a verified local tiktoken cache; downloads are disabled.')
    return tiktoken.get_encoding('cl100k_base')


def embed_input(clause):
    return clause['title'] + '\n' + clause['text']


def token_count(encoding, text):
    # Regulatory text is ordinary text, even if it contains tiktoken special strings.
    return len(encoding.encode(text, disallowed_special=()))


def split_inputs(clause, encoding, limit=TOKEN_LIMIT):
    """Repeat the title on each vector; count the entire input against the ceiling."""
    text, prefix = clause['text'], clause['title'] + '\n'
    if token_count(encoding, embed_input(clause)) <= limit:
        return [embed_input(clause)]
    if token_count(encoding, prefix) >= limit:
        raise ValueError(f"{clause['id']}: title alone exhausts the token limit")
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks, current = [], ''
    for sentence in sentences:
        if not sentence:
            continue
        candidate = current + (' ' if current else '') + sentence
        if token_count(encoding, prefix + candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(prefix + current)
            current = ''
        remainder = sentence
        while token_count(encoding, prefix + remainder) > limit:
            # A single overlong sentence needs a fallback: split at a word boundary,
            # then a character boundary if it has no whitespace. Never truncate text.
            low, high = 1, len(remainder)
            while low < high:
                mid = (low + high + 1) // 2
                if token_count(encoding, prefix + remainder[:mid]) <= limit:
                    low = mid
                else:
                    high = mid - 1
            if token_count(encoding, prefix + remainder[:low]) > limit:
                raise ValueError('A single character plus the title exceeds the token limit')
            cut = remainder.rfind(' ', 0, low + 1)
            if cut <= 0:
                cut = low
            piece = remainder[:cut].rstrip()
            chunks.append(prefix + piece)
            remainder = remainder[cut:].lstrip()
        current = remainder
    if current:
        chunks.append(prefix + current)
    if not all(token_count(encoding, c) <= limit for c in chunks):
        raise AssertionError('Splitting exceeded token limit')
    return chunks


def summarise(rows):
    values = [r['tokens'] for r in rows]
    return {'count': len(values), 'p50': percentile(values, .5), 'p95': percentile(values, .95),
            'max': max(values, default=0), 'gt_8191': sum(n > 8191 for n in values),
            'gt_2000': sum(n > 2000 for n in values), 'gt_512': sum(n > 512 for n in values),
            'lt_8': sum(n < 8 for n in values)}


def analyse(clauses, encoding):
    by_doc = defaultdict(list)
    rows, vectors = [], 0
    for c in clauses:
        row = {'id': c['id'], 'tokens': token_count(encoding, embed_input(c))}
        rows.append(row); by_doc[c['doc']].append(row)
        vectors += len(split_inputs(c, encoding))
    return {'encoding': 'cl100k_base', 'embed_input_version': 1, 'input_template': 'title + "\\n" + text',
            'parity': 'The counts and split ceiling include title and newline, including the repeated title in each split vector.',
            'per_doc': {d: summarise(rs) for d, rs in sorted(by_doc.items())}, 'overall': summarise(rows),
            'largest': sorted(rows, key=lambda r: (-r['tokens'], r['id']))[:10],
            'option_b': {'clauses': len(rows), 'vectors': vectors, 'extra_vectors': vectors-len(rows),
                         'limit': TOKEN_LIMIT, 'overlong_sentence_fallback': 'word boundary, then character boundary'}}


def run(root=ROOT, encoding=None):
    root = Path(root)
    clauses = json.loads((root / 'data/clauses.json').read_text(encoding='utf-8'))
    result = analyse(clauses, encoding or offline_encoding())
    print('PARITY: embed input v1 = title + newline + text; cl100k_base; repeated title counted in split vectors.')
    print('doc | count | p50 | p95 | max | >8191 | >2000 | >512 | <8')
    for doc, s in list(result['per_doc'].items()) + [('overall', result['overall'])]:
        print(' | '.join([doc] + [str(round(s[k], 2)) for k in ['count', 'p50', 'p95', 'max', 'gt_8191', 'gt_2000', 'gt_512', 'lt_8']]))
    for r in result['largest']:
        print(r['id'], r['tokens'])
    b = result['option_b']
    print(f"clauses: {b['clauses']}, vectors under option (b): {b['vectors']}, extra vectors: {b['extra_vectors']}")
    (root / 'data/token_stats.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    run()
