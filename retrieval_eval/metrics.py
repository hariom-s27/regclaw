"""R15 metrics, per-query Wilson intervals and descriptive summaries; no p-values.

Undefined ratios (empty relevant/added sets) score zero. Wilson intervals with
zero trials are None. Precision@k always divides by k, even for shorter rankings.
Rankings must contain unique ids so rank and set metrics agree.
"""
import argparse
import json
import math
from pathlib import Path
import statistics

from retrieval_eval.qrels import grades_by_query, read, relevant

ROOT = Path(__file__).resolve().parents[1]


def ratio(numerator, denominator):
    return numerator / denominator if denominator else 0.0


def top(ranked, k):
    if k <= 0:
        raise ValueError('k must be positive')
    if len(set(ranked)) != len(ranked):
        raise ValueError('Rankings must have unique ids')
    return ranked[:k]


def candidate_recall(candidates, gold):
    return ratio(len(set(candidates) & set(gold)), len(set(gold)))


def reduction_ratio(candidates, corpus_size):
    if corpus_size <= 0 or len(set(candidates)) > corpus_size:
        raise ValueError('Corpus size must be positive and cover the candidates')
    return 1 - len(set(candidates)) / corpus_size


def recall_at_k(ranked, gold, k=10):
    return ratio(len(set(top(ranked, k)) & set(gold)), len(set(gold)))


def conditional_recall_at_k(ranked, candidates, gold, k=10):
    return ratio(len(set(top(ranked, k)) & set(gold)), len(set(candidates) & set(gold)))


def precision_at_k(ranked, gold, k=10):
    return len(set(top(ranked, k)) & set(gold)) / k


def reciprocal_rank(ranked, gold):
    top(ranked, max(1, len(ranked)))
    return next((1 / i for i, cid in enumerate(ranked, 1) if cid in gold), 0.0)


def mrr(rankings, gold_sets):
    if len(rankings) != len(gold_sets):
        raise ValueError('One relevant set is required for each ranking')
    return statistics.mean(reciprocal_rank(r, g) for r, g in zip(rankings, gold_sets)) if rankings else 0.0


def ndcg_at_k(ranked, grades, k=10):
    ranking = top(ranked, k)
    dcg = sum((2**grades.get(cid, 0)-1) / math.log2(i+1) for i, cid in enumerate(ranking, 1))
    ideal = sum((2**grade-1) / math.log2(i+1) for i, grade in enumerate(sorted(grades.values(), reverse=True)[:k], 1))
    return ratio(dcg, ideal)


def expansion_delta(ranked, final, gold, k=10):
    return candidate_recall(final, gold) - recall_at_k(ranked, gold, k)


def expansion_precision(ranked, final, gold, k=10):
    added = set(final) - set(top(ranked, k))
    return ratio(len(added & set(gold)), len(added))


def wilson(successes, trials, z=1.959963984540054):
    if not 0 <= successes <= trials:
        raise ValueError('Wilson requires 0 <= successes <= trials')
    if trials == 0:
        return None
    p = successes / trials
    denominator = 1 + z*z/trials
    center = (p + z*z/(2*trials)) / denominator
    half = z * math.sqrt(p*(1-p)/trials + z*z/(4*trials*trials)) / denominator
    return [max(0.0, center-half), min(1.0, center+half)]


def evaluate(ranked, candidates, final, grades, corpus_size, k=10):
    gold = relevant(grades)
    ranked_top = set(top(ranked, k))
    candidates, final = set(candidates), set(final)
    # Conditional recall is interpretable only for a ranking inside stage 1.
    if not set(ranked) <= candidates:
        raise ValueError('Ranking contains ids outside the candidate set')
    if not ranked_top <= final:
        raise ValueError('Expansion final set must include the top-k ranking')
    added = final - ranked_top
    values = {'candidate_recall': candidate_recall(candidates, gold),
              'reduction_ratio': reduction_ratio(candidates, corpus_size),
              f'recall@{k}': recall_at_k(ranked, gold, k),
              f'conditional_recall@{k}': conditional_recall_at_k(ranked, candidates, gold, k),
              f'precision@{k}': precision_at_k(ranked, gold, k),
              'reciprocal_rank': reciprocal_rank(ranked, gold), 'ndcg@10': ndcg_at_k(ranked, grades, 10),
              'expansion_delta_recall': expansion_delta(ranked, final, gold, k),
              'expansion_precision': expansion_precision(ranked, final, gold, k)}
    proportions = {'candidate_recall': (len(candidates & gold), len(gold)),
                   'reduction_ratio': (corpus_size-len(candidates), corpus_size),
                   f'recall@{k}': (len(ranked_top & gold), len(gold)),
                   f'conditional_recall@{k}': (len(ranked_top & gold), len(candidates & gold)),
                   f'precision@{k}': (len(ranked_top & gold), k),
                   'expansion_precision': (len(added & gold), len(added))}
    return {'metrics': values, 'wilson_ci95': {key: wilson(n, d) for key, (n, d) in proportions.items()}}


def summary(rows):
    keys = sorted({key for row in rows for key in row})
    return {key: {'median': statistics.median(values), 'min': min(values), 'max': max(values), 'n': len(values)}
            for key in keys if (values := [r[key] for r in rows if key in r])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qrels', required=True)
    parser.add_argument('--rankings', required=True, help='JSON: qid -> {ranked: [id], candidates: [id], final: [id]}')
    parser.add_argument('--corpus-size', type=int, required=True)
    parser.add_argument('--k', type=int, default=10)
    parser.add_argument('--output', default=ROOT / 'data/retrieval_eval/metrics.json')
    args = parser.parse_args(argv)
    grades = grades_by_query(read(args.qrels))
    rankings = json.loads(Path(args.rankings).read_text(encoding='utf-8'))
    per_query = {}
    for qid, row in rankings.items():
        if qid not in grades:
            raise ValueError(f'No qrels for {qid}; do not treat unjudged topics as empty gold')
        per_query[qid] = evaluate(row['ranked'], row['candidates'], row['final'], grades[qid], args.corpus_size, args.k)
    result = {'per_query': per_query, 'summary': summary([r['metrics'] for r in per_query.values()]),
              'mrr': statistics.mean(r['metrics']['reciprocal_rank'] for r in per_query.values()) if per_query else 0.0,
              'unjudged_policy': 'unjudged documents score 0; topics without qrels are rejected'}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
