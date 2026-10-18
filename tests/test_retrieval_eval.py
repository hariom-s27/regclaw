import csv
import hashlib
import json
import math
from pathlib import Path

import pytest
import yaml

from retrieval_eval.bm25 import BM25
from retrieval_eval.metrics import (candidate_recall, conditional_recall_at_k, evaluate, expansion_delta,
                                    expansion_precision, mrr, ndcg_at_k, precision_at_k, recall_at_k,
                                    reciprocal_rank, reduction_ratio, summary, wilson, main as metrics_main)
from retrieval_eval.pool import corpus_fit, main as pool_main, make_pool, read_queries
from retrieval_eval.qrels import grades_by_query, read, relevant, write


def test_hand_computed_metrics_and_summary():
    ranked = ['b', 'x', 'a', 'c']
    grades = {'a': 3, 'b': 2, 'c': 1, 'd': 2}
    gold, candidates, final = {'a', 'b', 'd'}, {'a', 'b', 'x', 'c'}, {'a', 'b', 'x', 'd'}
    assert candidate_recall(candidates, gold) == pytest.approx(2/3)
    assert reduction_ratio(candidates, 10) == .6
    assert recall_at_k(ranked, gold, 3) == pytest.approx(2/3)
    assert conditional_recall_at_k(ranked, candidates, gold, 3) == 1
    assert precision_at_k(ranked, gold, 3) == pytest.approx(2/3)
    assert precision_at_k(['a'], gold, 10) == .1
    assert reciprocal_rank(ranked, gold) == 1
    assert mrr([ranked, ['x', 'd']], [gold, gold]) == .75
    dcg = 3 + 7/2 + 1/math.log2(5)
    ideal = 7 + 3/math.log2(3) + 3/2 + 1/math.log2(5)
    assert ndcg_at_k(ranked, grades) == pytest.approx(dcg/ideal)
    assert expansion_delta(ranked, final, gold, 3) == pytest.approx(1/3)
    assert expansion_precision(ranked, final, gold, 3) == 1
    assert wilson(5, 10) == pytest.approx([.2365930905, .7634069095])
    assert wilson(0, 0) is None
    report = evaluate(ranked, candidates, final, grades, 10, 3)
    assert report['metrics']['conditional_recall@3'] == 1
    assert report['wilson_ci95']['recall@3'] == wilson(2, 3)
    assert summary([{'a': 0}, {'a': 1}, {'a': .5}]) == {'a': dict(median=.5, min=0, max=1, n=3)}


def test_empty_gold_and_invalid_rankings():
    assert recall_at_k([], set()) == 0
    assert ndcg_at_k([], {}) == 0
    assert expansion_precision([], [], set()) == 0
    assert mrr([], []) == 0
    assert reciprocal_rank(['a'], set()) == 0
    for function in (recall_at_k, precision_at_k):
        with pytest.raises(ValueError):
            function(['a', 'a'], {'a'})
    with pytest.raises(ValueError):
        reduction_ratio({'a'}, 0)
    with pytest.raises(ValueError):
        evaluate(['outside'], ['a'], ['outside'], {}, 10)


def toy_corpus():
    return [dict(id=cid, doc=doc, title='', text=text) for cid, doc, text in [
        ('a', 'fda-knee', 'knee knee'), ('b', 'eu-mdr', 'implant'),
        ('c', 'epa-63o', 'ethylene oxide sterilization'), ('d', 'in-mdr', 'knee'), ('e', 'imdrf-n47', 'water water')]]


def test_bm25_five_doc_ranking_and_hand_score():
    system = BM25(toy_corpus())
    ranking = system.rank('knee', 5)
    assert [cid for cid, _ in ranking[:2]] == ['a', 'd']
    assert ranking[0][1] == pytest.approx(math.log(2.4)*2*2.2/(2+1.2*(.25+.75*2/1.8)))
    assert system.rank('ethylene oxide', 1)[0][0] == 'c'
    assert system.rank('not present', 5) == [(cid, 0) for cid in 'abcde']
    assert BM25([]).rank('knee') == []


def test_qrels_roundtrip_grades_and_rejection(tmp_path):
    path = tmp_path / 'qrels.tsv'
    rows = [dict(qid='Q1', clause_id=cid, grade=grade, judge='human', date='2026-10-15') for cid, grade in [('a', 3), ('b', 1)]]
    write(path, rows)
    assert read(path) == rows
    assert relevant(grades_by_query(rows)['Q1']) == {'a'}
    for bad in ([{**rows[0], 'grade': 4}], [rows[0], rows[0]], [{**rows[0], 'judge': ''}]):
        with pytest.raises(ValueError):
            write(path, bad)


def test_plugin_pool_union_best_rank_and_source_fit(capsys):
    class Fixed:
        def __init__(self, ids): self.ids = ids
        def rank(self, query, k): return [(cid, 1) for cid in self.ids[:k]]
    q = dict(qid='Q1', query='knee', expected_sources=['fda-*'])
    pooled = make_pool(q, toy_corpus(), {'bm25': Fixed(['a', 'd']), 'future': Fixed(['d', 'b'])})
    assert len(pooled) == 3
    assert next(r for r in pooled if r['clause_id'] == 'd')['systems'] == 'bm25;future'
    assert next(r for r in pooled if r['clause_id'] == 'd')['best_rank'] == 1
    assert all(r['grade'] == '' and len(r['text']) <= 300 for r in pooled)
    result = corpus_fit([q, {**q, 'qid': 'Q12', 'expected_sources': []}], toy_corpus(), BM25(toy_corpus()))
    assert result[0]['flag'] == '' and 'likely unsupported' in result[1]['flag']


def test_frozen_topics_review_before_results_and_cli(tmp_path):
    document = yaml.safe_load((Path(__file__).resolve().parents[1] / 'retrieval_eval/queries.yaml').read_bytes())
    topics = read_queries(Path(__file__).resolve().parents[1] / 'retrieval_eval/queries.yaml')
    assert len(topics) == 16
    assert all(set(q['narrative']) == {'grade3', 'grade2', 'grade1_traps'} for q in topics)
    topic_path = tmp_path / 'topics.yaml'
    topic_path.write_text(yaml.safe_dump({**document, 'status': 'DRAFT'}))
    with pytest.raises(ValueError, match='FROZEN'):
        read_queries(topic_path)
    topic_path.write_text(yaml.safe_dump({**document, 'queries': [topics[0]]}))
    corpus = tmp_path / 'clauses.json'
    corpus.write_text(json.dumps(toy_corpus()))
    output = tmp_path / 'output'
    assert pool_main(['--systems', 'bm25', '--input', str(corpus), '--queries', str(topic_path),
                      '--output-dir', str(output), '--corpus-fit']) == 0
    with (output / 'pool_R1.csv').open() as f:
        assert len(list(csv.DictReader(f))) == 5
    qrels = tmp_path / 'qrels.tsv'
    write(qrels, [dict(qid='R1', clause_id='a', grade=3, judge='test', date='2026-10-15')])
    rankings = tmp_path / 'rankings.json'
    rankings.write_text(json.dumps({'R1': dict(ranked=['a', 'b'], candidates=['a', 'b'], final=['a', 'b'])}))
    assert metrics_main(['--qrels', str(qrels), '--rankings', str(rankings), '--corpus-size', '5',
                         '--output', str(output / 'metrics.json')]) == 0
    assert json.loads((output / 'metrics.json').read_text())['mrr'] == 1


def test_frozen_topics_integrity():
    raw = (Path(__file__).resolve().parents[1] / 'retrieval_eval/queries.yaml').read_bytes()
    document = yaml.safe_load(raw)
    assert document['status'] == 'FROZEN'
    assert len(document['queries']) == len({q['qid'] for q in document['queries']}) == 16
    assert hashlib.sha256(raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n')).hexdigest() == (
        '83d7813873d6d9e47a732db8778f68c18cfb4654834acfa16ca6847457affc7c')
