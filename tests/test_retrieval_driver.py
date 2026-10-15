"""Managed transaction compatibility without a database or installed driver."""
from contextlib import contextmanager
import sys
from types import SimpleNamespace

import pytest

from retrieval.hybrid_retriever import HybridRetriever


def test_run_ids_uses_string_query_read_transaction_and_timeout(monkeypatch):
    class Query:
        def __init__(self, text, **kwargs):
            self.text = text

    def unit_of_work(metadata=None, timeout=None):
        def decorate(fn):
            fn.metadata, fn.timeout = metadata, timeout
            return fn
        return decorate

    monkeypatch.setitem(sys.modules, 'neo4j', SimpleNamespace(Query=Query, unit_of_work=unit_of_work))
    state = dict(open=False, reads=0)
    calls = []

    class ManagedTransaction:
        def run(self, query, parameters=None):
            if isinstance(query, Query):
                raise TypeError('Query object is only supported for session.run')
            assert isinstance(query, str)
            calls.append((query, parameters))
            def records():
                assert state['open'], 'Result escaped its transaction'
                yield {'clause_id': 'd:1'}
                yield {'clause_id': None}
                yield {'clause_id': 'd:2'}
            return records()

    tx = ManagedTransaction()
    with pytest.raises(TypeError, match='only supported for session.run'):
        tx.run(Query('RETURN 1'))

    @contextmanager
    def session(**kwargs):
        assert kwargs == {'database': 'neo4j'}
        state['open'] = True
        def execute_read(work):
            assert work.timeout == 2.75 and work.metadata is None
            state['reads'] += 1
            return work(tx)
        try:
            yield SimpleNamespace(execute_read=execute_read)
        finally:
            state['open'] = False

    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever.driver = SimpleNamespace(session=session)
    retriever.db, retriever.timeout = 'neo4j', 2.75
    query = 'MATCH (c:Clause) WHERE c.text CONTAINS $term RETURN c.id AS clause_id'
    parameters = {'term': 'ethylene oxide'}
    assert retriever._run_ids(query, parameters) == ['d:1', 'd:2']
    assert calls == [(query, parameters)] and calls[0][1] is parameters
    assert state == dict(open=False, reads=1)
