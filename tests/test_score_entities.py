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


# S4b offset-free canonical path (synthetic fixtures only).
import csv
import json

from goldset.score_entities import (ledger_cost, load_gold_csv, load_predictions,
                                    paired_diff_ci, top_errors)

FIELDS = ['clause_id', 'source', 'text', 'biological_effects', 'chemicals', 'device_types',
          'regulatory_actors', 'lifecycle_phases', 'notes']
TEXTS = {'x:1': 'The Manufacturer shall test EtO   residuals.', 'x:2': 'No entities here.'}


def write_gold(tmp_path, rows):
    path = tmp_path / 'gold.csv'
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for row in rows:
            w.writerow({**{k: '' for k in FIELDS}, 'notes': 'complete', **row})
    return path


def test_canonical_only_path_accepts_entities_without_offsets():
    gold = {'a': [{'type': 'chemicals', 'canonical': 'EtO'}], 'b': []}
    pred = {'a': [{'type': 'chemicals', 'canonical': 'ethylene oxide'}], 'b': []}
    result = score(gold, pred, schemes=('canonical',), resamples=5)
    assert set(result['metrics']) == {'canonical'}
    assert result['metrics']['canonical']['micro']['f1'] == 1
    with pytest.raises(ValueError, match='Invalid typed span'):
        score(gold, pred, resamples=5)
    with pytest.raises(ValueError, match='Unknown scheme'):
        score(gold, pred, schemes=('fuzzy',))


def test_before_stage_skips_normaliser():
    gold = {'a': [{'type': 'chemicals', 'canonical': 'ethylene oxide'}]}
    pred = {'a': [{'type': 'chemicals', 'canonical': 'EtO'}]}
    raw = score(gold, pred, schemes=('canonical',), normalise=False, resamples=5)
    assert raw['metrics']['canonical']['micro']['f1'] == 0
    enum = {'a': [{'type': 'regulatory_actors', 'canonical': 'Notified_Body'}]}
    gold_enum = {'a': [{'type': 'regulatory_actors', 'canonical': 'notified_body'}]}
    assert score(gold_enum, enum, schemes=('canonical',), normalise=False,
                 resamples=5)['metrics']['canonical']['micro']['f1'] == 1


def test_load_gold_csv_parses_cells(tmp_path):
    path = write_gold(tmp_path, [
        {'clause_id': 'x:1', 'chemicals': 'EtO residuals => ethylene oxide',
         'regulatory_actors': 'manufacturer => manufacturer; Manufacturer => other'},
        {'clause_id': 'x:2'}])
    gold = load_gold_csv(path, TEXTS)
    assert gold['x:2'] == []
    assert gold['x:1'] == [{'type': 'chemicals', 'canonical': 'ethylene oxide'},
                           {'type': 'regulatory_actors', 'canonical': 'manufacturer'},
                           {'type': 'regulatory_actors', 'canonical': 'other'}]
    assert all(set(e) == {'type', 'canonical'} for e in gold['x:1'])


@pytest.mark.parametrize('row, message', [
    ({'chemicals': 'nickel => nickel'}, 'mention not in clause text'),
    ({'chemicals': 'EtO ethylene oxide'}, 'bad cell item'),
    ({'regulatory_actors': 'Manufacturer => maker'}, 'invalid enum canonical'),
    ({'notes': 'draft'}, 'notes not complete'),
])
def test_load_gold_csv_rejects(tmp_path, row, message):
    path = write_gold(tmp_path, [{'clause_id': 'x:1', **row}])
    with pytest.raises(ValueError, match=message):
        load_gold_csv(path, TEXTS)


def test_load_predictions_filters_ids_and_stages(tmp_path):
    entities = lambda name: {'chemicals': [{'mention': name, 'canonical': name, 'evidence': ''}]}
    ner = tmp_path / 'ner.json'
    ner.write_text(json.dumps([{'id': 'dev', 'entities': entities('nickel')},
                               {'id': 'sealed', 'entities': None}]), encoding='utf-8')
    assert load_predictions(ner, ['dev']) == {'dev': [{'type': 'chemicals', 'canonical': 'nickel'}]}
    sha = __import__('hashlib').sha256(b'text').hexdigest()
    cache = tmp_path / 'cache.json'
    cache.write_text(json.dumps({f'dev|{sha}|p|azure|m|low': {'data': {'entities': entities('Ni')}},
                                 f'dev|{sha}|p|azure|m|high': {'data': None},
                                 f'sealed|{sha}|p|azure|m|low': {'data': None}}), encoding='utf-8')
    got = load_predictions(cache, ['dev'], stage='before', effort='low', texts={'dev': 'text'})
    assert got == {'dev': [{'type': 'chemicals', 'canonical': 'Ni'}]}
    with pytest.raises(ValueError, match='missing'):
        load_predictions(ner, ['dev', 'absent'])


def test_paired_diff_and_top_errors():
    nickel = [{'type': 'chemicals', 'canonical': 'nickel'}]
    gold = {'a': nickel, 'b': nickel}
    low = {'a': nickel, 'b': [{'type': 'chemicals', 'canonical': 'cobalt'}]}
    high = {'a': nickel, 'b': nickel}
    diff = paired_diff_ci(gold, low, high, resamples=200)
    assert diff['diff'] == pytest.approx(1 - .5)
    assert 0 <= diff['ci95'][0] <= diff['ci95'][1] <= 1
    assert paired_diff_ci(gold, low, high, resamples=200) == diff
    errors = top_errors(gold, low)
    assert errors['chemicals'] == {'false_positives': [('cobalt', 1)], 'false_negatives': [('nickel', 1)]}


def test_ledger_cost_counts_retries(tmp_path):
    path = tmp_path / 'ledger.json'
    row = lambda cid, effort, cost: {'clause_id': cid, 'effort': effort, 'cost_estimate_usd': cost, 'latency_s': 2.}
    path.write_text(json.dumps([row('a', 'high', 1.), row('a', 'high', 1.), row('b', 'high', 2.),
                                row('a', 'low', 9.)]), encoding='utf-8')
    got = ledger_cost(path, ['a', 'b'], 'high')
    assert got['calls'] == 3 and got['cost_per_clause_usd'] == 2. and got['latency_per_clause_s'] == 3.
