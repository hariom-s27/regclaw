"""Report cross-document twins and word-five-gram similarity; no graph changes."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re

from goldset.sample import ROOT, jaccard, normalised


def word_grams(text):
    words = re.findall(r'\w+', normalised(text))
    return {tuple(words[i:i+5]) for i in range(max(0, len(words)-4))}


def find_pairs(clauses):
    rows = sorted(clauses, key=lambda c: c['id'])
    hashes = [hashlib.sha256(normalised(c['text']).encode()).hexdigest() for c in rows]
    grams = [word_grams(c['text']) for c in rows]
    pairs = []
    for i, a in enumerate(rows):
        if not a['text'].strip():
            continue
        for j in range(i+1, len(rows)):
            b = rows[j]
            if a['doc'] == b['doc'] or not b['text'].strip():
                continue
            exact = hashes[i] == hashes[j]
            x, y = grams[i], grams[j]
            if not exact and (not x or not y or min(len(x), len(y)) < .7 * max(len(x), len(y))):
                continue
            # Identical short strings have no word-five-grams; the exact hash
            # establishes identity rather than treating two empty sets as evidence.
            similarity = 1.0 if exact else jaccard(x, y)
            if exact or similarity >= .7:
                pairs.append(dict(id_a=a['id'], id_b=b['id'], exact=exact, jaccard=round(similarity, 8),
                                  category='exact' if exact else 'same' if similarity >= .9 else 'edited'))
    return sorted(pairs, key=lambda r: (not r['exact'], -r['jaccard'], r['id_a'], r['id_b']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default=ROOT / 'data/clauses.json')
    parser.add_argument('--output', default=ROOT / 'data/near_dups.csv')
    args = parser.parse_args(argv)
    pairs = find_pairs(json.loads(Path(args.input).read_text(encoding='utf-8')))
    with Path(args.output).open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['id_a', 'id_b', 'exact', 'jaccard', 'category'])
        w.writeheader()
        w.writerows(pairs)
    print('Cross-document pairs:', dict(Counter(r['category'] for r in pairs)))
    print('Jaccard >=0.9:', sum(r['jaccard'] >= .9 for r in pairs), '; 0.7-0.9:', sum(.7 <= r['jaccard'] < .9 for r in pairs))
    for row in pairs[:10]:
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
