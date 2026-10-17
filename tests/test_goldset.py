import csv
import json

import numpy as np
import pytest

from goldset.sample import allocate, char_grams, jaccard, run
from goldset.score import agreement_report, agreement_values, paired_comparison, read_gold, report, retest_report, score, print_tables


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


def test_agreement_known_chance_and_degenerate_cases():
    from sklearn.metrics import cohen_kappa_score
    a = np.array([['SHALL', 'SHALL', 'NONE', 'NONE']])
    b = np.array([['SHALL', 'NONE', 'SHALL', 'NONE']])
    raw, kappa, alpha = agreement_values(a, b)
    assert raw[0] == .5
    assert kappa[0] == cohen_kappa_score(a[0], b[0]) == 0
    assert alpha[0] == pytest.approx(.125)
    mapping = {str(i): 'NONE' for i in range(4)}
    result = retest_report(mapping, mapping, resamples=20, seed=1)
    assert result['raw_agreement']['value'] == 1
    assert result['cohen_kappa']['value'] is None
    assert result['krippendorff_alpha']['ci95'] is None
    assert result['cohen_kappa']['undefined_resamples'] == 20


def test_paired_bootstrap_sign_symmetry_and_mcnemar():
    g = ['SHALL', 'SHOULD', 'MAY', 'NONE'] * 3
    bad = ['NONE'] * len(g)
    forward = paired_comparison(g, bad, g, resamples=100, seed=42)
    reverse = paired_comparison(g, g, bad, resamples=100, seed=42)
    assert reverse['delta_macro_f1'] == -forward['delta_macro_f1']
    assert reverse['ci95'] == pytest.approx([-forward['ci95'][1], -forward['ci95'][0]])
    assert forward['mcnemar_exact_p'] == reverse['mcnemar_exact_p'] == pytest.approx(2 / 2**9)
    tied = paired_comparison(g, g, g, resamples=100, seed=42)
    assert tied['ci95'] == [0, 0] and tied['mcnemar_exact_p'] == 1


def test_agreement_validation_and_practice_rows(tmp_path):
    fields = ['clause_id', 'text', 'label', 'mixed', 'restated_reg', 'low_confidence', 'note', 'source']
    gold, a, b = [], [], []
    for i in range(184):
        cid, label = f'in-mdr:{183-i}', ('SHALL', 'NONE')[i % 2]
        gold.append(dict(clause_id=cid, text='fixture', label=label, mixed='0', restated_reg='0',
                         low_confidence='0', note='', source='agree'))
        a.append(dict(row=i+1, clause_id=cid, label=label))
        b.append(dict(row=i+1, clause_id=cid, label='NONE' if i in (0, 20) else label))
    def write(name, columns, rows):
        path = tmp_path / name
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
            writer.writeheader(); writer.writerows(rows)
        return path
    gp = write('gold.csv', fields, gold)
    ep = write('export.csv', [k for k in fields if k not in ('text', 'note')], gold)
    ip = write('ids.csv', ['id'], [dict(id=r['clause_id']) for r in gold])
    ap = write('a.csv', ['row', 'clause_id', 'label'], a)
    bp = write('b.csv', ['row', 'clause_id', 'label'], b)
    result = agreement_report(ap, bp, gp, ep, ip, resamples=20)
    assert result['all']['raw_agreement']['value'] == pytest.approx(182 / 184)
    assert result['rows_21_184']['raw_agreement']['value'] == pytest.approx(163 / 164)
    assert result['validation']['label_disagreements'] == 2
    gold[0]['mixed'] = '2'
    write('gold.csv', fields, gold)
    with pytest.raises(ValueError, match='Flags must be 0/1'):
        agreement_report(ap, bp, gp, ep, ip, resamples=20)


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
