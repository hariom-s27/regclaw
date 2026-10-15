import copy

import pytest

from ner.merge import merge
from ner.validate_enriched import validate


def clause(cid='in-mdr:1'):
    return dict(id=cid, doc='in-mdr', number='1', title='Test', text='The notified body shall test.',
                parent=None, source_pdf='fake.pdf', page=1, informative=False)


def test_merge_contract_and_canonical_strings():
    c = clause()
    ner = [{'id': c['id'], 'entities': {'regulatory_actors': [
        {'mention': 'notified body', 'canonical': 'notified_body'},
        {'mention': 'notified body', 'canonical': 'notified_body'}]},
        'keywords': ['Test', 'test'], 'iso_refs': ['EN ISO 10993-1:2018']}]
    rows = merge([c], ner, [{'id': c['id'], 'weak_label': 'SHALL', 'flags': ['prohibition']}])
    assert rows[0]['entities']['regulatory_actors'] == ['notified body']
    assert rows[0]['entities']['keywords'] == ['test']
    assert rows[0]['iso_refs'] == ['ISO 10993-1']
    assert validate(rows, [c])['empty_entities'] == 0
    for change in ({'extra': 1}, {'obligation': None}, {'text': 'changed'}, {'entities': {}}):
        bad = copy.deepcopy(rows)
        bad[0].update(change)
        with pytest.raises(ValueError):
            validate(bad, [c])


def test_missing_duplicate_unknown_and_default():
    c = clause()
    with pytest.raises(ValueError, match='missing'):
        merge([c], [], [])
    rows = merge([c], [], [], True)
    assert rows[0]['obligation'] == 'NONE'
    assert validate(rows, [c])['empty_entities'] == 1
    for ner, predictions in [([{'id': 'unknown'}], []), ([], [{'id': 'unknown'}]),
                             ([{'id': c['id']}, {'id': c['id']}], []),
                             ([], [{'id': c['id']}, {'id': c['id']}])]:
        with pytest.raises(ValueError):
            merge([c], ner, predictions, True)
    with pytest.raises(ValueError, match='duplicate'):
        merge([c, c], [], [], True)
