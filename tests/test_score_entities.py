"""Known-answer synthetic span fixtures, never corpus/model/gold observations."""
import pytest

from goldset.score_entities import counts, main, score


def entity(start=0, end=4, kind='chemicals', canonical='nickel'):
    return dict(start=start, end=end, type=kind, canonical=canonical)


def test_four_schemes_wrong_type_and_partial_boundary():
    g = [entity()]
    wrong = [entity(kind='device_types')]
    assert counts(g, wrong, 'strict')['credit'] == 0
    assert counts(g, wrong, 'strict')['INC'] == 1
    assert counts(g, wrong, 'exact')['credit'] == 1
    assert counts(g, wrong, 'partial')['credit'] == 1
    assert counts(g, wrong, 'type')['credit'] == 0
    overlap = [entity(2, 6)]
    assert counts(g, overlap, 'strict')['credit'] == 0
    assert counts(g, overlap, 'exact')['credit'] == 0
    assert counts(g, overlap, 'partial')['credit'] == .5
    assert counts(g, overlap, 'type')['credit'] == 1


def test_matching_one_to_one_and_not_greedy():
    gold = [entity(0, 4), entity(4, 8)]
    pred = [entity(0, 8), entity(0, 4)]
    assert counts(gold, pred, 'partial')['credit'] == 1.5
    assert counts(gold, pred[::-1], 'partial')['credit'] == 1.5
    assert counts(gold, pred, 'strict')['COR'] == 1
    dup = counts([entity()], [entity(), entity()], 'strict')
    assert dup['COR'] == 1 and dup['SPU'] == 1
    assert counts([entity()], [entity(8, 12)], 'strict')['MIS'] == 1
    assert counts([entity()], [entity(), entity()], 'canonical')['predicted'] == 1


def test_known_micro_macro_and_bootstrap_seed():
    gold = {'a': [entity()], 'b': [entity()]}
    pred = {'a': [entity()], 'b': []}
    result = score(gold, pred)
    strict = result['metrics']['strict']
    assert strict['micro']['precision'] == 1
    assert strict['micro']['recall'] == .5
    assert strict['micro']['f1'] == pytest.approx(2/3)
    assert strict['macro']['precision'] == .2
    assert strict['macro']['recall'] == .1
    assert strict['macro']['f1'] == pytest.approx(2/15)
    assert strict['micro']['ci95']['f1'] == [0., 1.]
    assert result == score(gold, pred)
    assert result['resamples'] == 1000


def test_normalised_canonical_set_and_zero_entities():
    result = score({'a': [entity(canonical='EtO')], 'b': []},
                   {'a': [entity(canonical='ethylene oxide')], 'b': []}, resamples=2)
    assert result['metrics']['canonical']['micro']['f1'] == 1
    empty = score({'a': []}, {'a': []}, resamples=2)
    assert empty['metrics']['strict']['micro']['f1'] == 0


def test_validation_and_blank_gold_guard():
    with pytest.raises(ValueError, match='ids differ'):
        score({'a': []}, {'b': []})
    with pytest.raises(ValueError, match='No annotated'):
        score({}, {})
    with pytest.raises(ValueError, match='Invalid typed span'):
        score({'a': [entity(4, 4)]}, {'a': []})
    with pytest.raises(SystemExit):
        main(['--gold', 'blank.csv', '--predictions', 'unused.json', '--output', 'unused.json'])
