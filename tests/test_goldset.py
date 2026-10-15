import csv
import json

import numpy as np
import pytest

from goldset.sample import allocate, char_grams, jaccard, run
from goldset.score import agreement_values, paired_comparison, read_gold, report, retest_report, score, print_tables


def test_allocation_floor_cap_and_redistribution():
    assert allocate({'FDA': 70, 'EU': 20, 'INDIA': 10}) == {'FDA': 34, 'EU': 10, 'INDIA': 6}
    assert allocate({'FDA': 100, 'EU': 2, 'INDIA': 10}) == {'FDA': 42, 'EU': 2, 'INDIA': 6}
    assert allocate({'FDA': 20, 'EU': 14}) == {'FDA': 20, 'EU': 14}
    assert jaccard(char_grams('ABC  def'), char_grams('abc def')) == 1


def test_sampler_deterministic_blind_and_no_review_train(tmp_path):
    (tmp_path / 'data').mkdir()
    clauses, weak = [], []
    for label in ('SHALL', 'SHOULD', 'MAY', 'NONE'):
        for i in range(70):
            cid = f'in-mdr:{label}.{i}'
            clauses.append(dict(id=cid, doc='in-mdr', text=f'{label} requirement item {i}'))
            weak.append(dict(id=cid, weak_label=label, needs_review=i % 2 == 0))
    (tmp_path / 'data/clauses.json').write_text(json.dumps(clauses))
    (tmp_path / 'data/weak_labels.json').write_text(json.dumps(weak))
    run(tmp_path)
    folder = tmp_path / 'goldset'
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    run(tmp_path)
    assert before == {p.name: p.read_bytes() for p in folder.iterdir()}
    read = lambda p: list(csv.DictReader(p.open(newline='', encoding='utf-8')))
    gold = {r['id'] for r in read(folder / 'gold_ids.csv')}
    train = {r['id'] for r in read(folder / 'train_ids.csv')}
    assert len(gold) == 200 and len(read(folder / 'retest_ids.csv')) == 50
    assert not gold & train
    assert not train & {r['id'] for r in weak if r['needs_review']}
    blind = read(tmp_path / 'data/gold/gold_round1.csv')
    assert set(blind[0]) == {'clause_id', 'text', 'label', 'mixed', 'restated_reg', 'low_confidence', 'note'}
    assert set(json.loads((tmp_path / 'data/gold/gold_round1_labelstudio.json').read_text())[0]['data']) == {'clause_id', 'text'}


def test_scoring_pairing_and_nominal_alpha():
    g = ['SHALL', 'SHOULD', 'MAY', 'NONE'] * 4
    bad = ['NONE'] * len(g)
    result = paired_comparison(g, bad, g)
    assert result['delta_macro_f1'] == pytest.approx(.9)
    assert result['fixes_breaks']['fixes'] == 12
    assert result['mcnemar_exact_p'] == pytest.approx(2 / 2**12)
    assert result['ci95'][0] > 0
    assert report(g, g)['macro_f1'] == 1
    a = np.array([['SHALL', 'SHALL', 'NONE', 'NONE']])
    b = np.array([['SHALL', 'NONE', 'NONE', 'NONE']])
    agreement, kappa, alpha = agreement_values(a, b)
    assert agreement[0] == .75 and kappa[0] == .5
    assert alpha[0] == pytest.approx(1 - .25 / (30 / 56))
    result = retest_report(dict(zip(map(str, range(16)), g)), dict(zip(map(str, range(16)), g)))
    assert result['krippendorff_alpha']['value'] == 1 and result['decision'] == 'rely'


def test_weighting_near_dup_subset_and_import(tmp_path, capsys):
    gold = {'in-mdr:1': 'SHALL', 'in-mdr:2': 'MAY'}
    weak = [dict(id='in-mdr:1', weak_label='SHALL'), dict(id='in-mdr:2', weak_label='NONE')]
    allocation = [dict(weak_label=label, doc_type='INDIA', pool_size=n, sampled=1)
                  for label, n in [('SHALL', 10), ('NONE', 90)]]
    result = score(gold, weak, allocation, gold, {'in-mdr:2'})
    assert result['subsets']['all']['regex_corpus_weighted']['accuracy'] == .1
    assert result['subsets']['without_near_duplicates']['n'] == 1
    print_tables(result)
    printed = capsys.readouterr().out
    assert 'gold support' in printed and 'confusion matrix' in printed and 'exact McNemar' in printed
    p = tmp_path / 'gold.json'
    p.write_text(json.dumps([{'data': {'clause_id': 'in-mdr:1'}, 'annotations': [{'result': [
        {'from_name': 'label', 'value': {'choices': ['SHALL']}}]}]}]))
    assert read_gold(p) == {'in-mdr:1': 'SHALL'}
    p.write_text(json.dumps([{'data': {'clause_id': 'in-mdr:1'}, 'annotations': []}]))
    with pytest.raises(ValueError):
        read_gold(p)
