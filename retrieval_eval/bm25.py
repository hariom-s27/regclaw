"""Pure-Python Okapi BM25 evaluation baseline (k1=1.2, b=0.75)."""
from collections import Counter
import math
import re


def tokenize(text):
    return re.findall(r'\w+', text.casefold(), re.UNICODE)


class BM25:
    name = 'bm25'

    def __init__(self, clauses, k1=1.2, b=.75):
        if k1 <= 0 or not 0 <= b <= 1:
            raise ValueError('Invalid BM25 parameters')
        self.clauses = {c['id']: c for c in clauses}
        if len(self.clauses) != len(clauses):
            raise ValueError('Duplicate corpus ids')
        self.k1, self.b = k1, b
        self.tf = {c['id']: Counter(tokenize(c['title'] + '\n' + c['text'])) for c in clauses}
        self.lengths = {cid: sum(tf.values()) for cid, tf in self.tf.items()}
        self.average_length = sum(self.lengths.values()) / len(clauses) if clauses else 0
        self.df = Counter(term for tf in self.tf.values() for term in tf)

    def rank(self, query, k=20):
        if k < 0:
            raise ValueError('k must be nonnegative')
        terms = Counter(tokenize(query))
        n = len(self.clauses)
        scores = []
        for cid, tf in self.tf.items():
            score = 0.0
            for term, qf in terms.items():
                freq = tf[term]
                if not freq:
                    continue
                idf = math.log(1 + (n - self.df[term] + .5) / (self.df[term] + .5))
                normalizer = self.k1 * (1 - self.b + self.b * self.lengths[cid] / self.average_length)
                score += qf * idf * freq * (self.k1 + 1) / (freq + normalizer)
            # Keep zero-score items for a deterministic top-k on sparse/hard-negative topics.
            scores.append((cid, score))
        return sorted(scores, key=lambda item: (-item[1], item[0]))[:k]
