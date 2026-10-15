import copy
from types import SimpleNamespace

import pytest

from graph.build_graph import NODE_LABELS, REL_QUERIES, constraint_queries, counts, load, node_query, plan_graph
from graph.checks import CHECKS, evaluate
from graph.near_dups import find_pairs


def fixture():
    standards = [dict(id='doc', number='Local 1', title='Local', edition=2026, authority='FDA')]
    rows = [dict(id=f'doc:{i}', doc='doc', number=str(i), title='title', text='text', obligation='SHALL',
                 entities={'chemicals': ['ethylene oxide', 'ethylene oxide'], 'lifecycle_phases': ['sterilization'], 'keywords': ['ignored']},
                 iso_refs=['ISO 10993-4', 'ISO 10993-4', 'Local 1']) for i in range(2)]
    return rows, standards


class FakeDriver:
    def __init__(self):
        self.calls = []
        self.seen = set()

    def execute_query(self, query, database_, **params):
        assert database_ == 'neo4j'
        self.calls.append((query, params))
        created = sum((query, str(row)) not in self.seen for row in params.get('rows', []))
        self.seen.update((query, str(row)) for row in params.get('rows', []))
        is_rel = 'MERGE (a)-[' in query
        counters = SimpleNamespace(nodes_created=0 if is_rel else created, relationships_created=created if is_rel else 0)
        return SimpleNamespace(summary=SimpleNamespace(counters=counters))


def test_plan_counts_and_stub_standard():
    rows, standards = fixture()
    plan = plan_graph(rows, standards)
    c = counts(plan)
    assert c['nodes'] == dict(Module=1, Standard=2, Clause=2, LifecyclePhase=1, RegulatoryAuthority=1,
                              Chemical=1, BiologicalEffect=0, DeviceType=0, RegulatoryActor=0)
    assert c['relationships'] == dict(BELONGS_TO=3, MENTIONS=4, REGULATED_BY=1, REFERENCES=4)
    assert c['stub_standards'] == 1
    assert {'id': 'ISO 10993-4', 'number': 'ISO 10993-4'} in plan['nodes']['Standard']


def test_batches_constraints_order_and_idempotent_fake():
    plan = plan_graph(*fixture())
    driver = FakeDriver()
    assert load(driver, plan, batch_size=1) == dict(nodes_created=8, relationships_created=12)
    assert load(driver, plan, batch_size=1) == dict(nodes_created=0, relationships_created=0)
    queries = [q for q, _ in driver.calls]
    assert len(constraint_queries()) == 9
    assert all('IF NOT EXISTS' in q and 'IS UNIQUE' in q for q in queries[:9])
    data = [q for q in queries[:queries.index(constraint_queries()[0], 1)] if 'UNWIND' in q]
    assert all(q.count('MERGE') == 1 for q in data)
    first_rel = next(i for i, q in enumerate(data) if 'MERGE (a)-[' in q)
    assert all('MERGE (n:' in q for q in data[:first_rel])
    assert all('MERGE (a)-[' in q for q in data[first_rel:])
    assert not any('FULLTEXT' in q or 'IN TRANSACTIONS' in q for q in queries)
    assert len(REL_QUERIES) == 4


def test_label_injection_rejected_before_mutation():
    with pytest.raises(ValueError):
        node_query('Chemical) DELETE n //')
    rows, standards = fixture()
    rows[0]['entities']['Chemical) DELETE n'] = ['bad']
    with pytest.raises(ValueError):
        plan_graph(rows, standards)
    plan = plan_graph(*fixture())
    plan['relationships']['MENTIONS'][0]['label'] = 'Chemical) DELETE n'
    driver = FakeDriver()
    with pytest.raises(ValueError):
        load(driver, plan)
    assert not driver.calls


def test_default_batch_cap():
    rows, standards = fixture()
    rows = [{**rows[0], 'id': f'doc:{i}'} for i in range(1001)]
    driver = FakeDriver()
    load(driver, plan_graph(rows, standards))
    assert max(len(params.get('rows', [])) for _, params in driver.calls) == 1000


def test_near_dups_exact_edited_and_short_text():
    text = ' '.join(f'word{i}' for i in range(100))
    rows = [dict(id='a:1', doc='a', text=text), dict(id='b:1', doc='b', text=text.upper()),
            dict(id='c:1', doc='c', text=text + ' extra'), dict(id='d:1', doc='d', text='unrelated'),
            dict(id='e:1', doc='e', text='short text'), dict(id='f:1', doc='f', text='short text')]
    pairs = find_pairs(rows)
    assert sum(r['exact'] for r in pairs) == 2
    assert any(r['category'] == 'same' for r in pairs)
    assert not any('d:1' in (r['id_a'], r['id_b']) for r in pairs)
    edited = text + ' ' + ' '.join(f'new{i}' for i in range(25))
    assert find_pairs([rows[0], dict(id='b:2', doc='b', text=edited)])[0]['category'] == 'edited'


def test_all_thirteen_checks_and_thresholds():
    assert len(CHECKS) == 13
    assert 'SHOW CONSTRAINTS' in CHECKS[11][1]
    assert 'LifecyclePhase' in CHECKS[10][1]
    assert all(query.strip() and threshold for _, query, threshold in CHECKS)
    plan = counts(plan_graph(*fixture()))
    rows = [dict(kind=k, name=n, value=v) for k in ('nodes', 'relationships') for n, v in plan[k].items() if v]
    assert evaluate(0, rows, plan)[1] == 'PASS'
    assert evaluate(3, [{'total': 10, 'value': 10}], plan)[1] == 'WARN'
    assert evaluate(12, [], plan)[1] == 'WARN'
    assert evaluate(12, [], plan, dict(nodes_created=0, relationships_created=0))[1] == 'PASS'
    constraints = [dict(labelsOrTypes=[label], properties=['id' if label in ('Clause', 'Standard') else 'name'], type='UNIQUENESS') for label in NODE_LABELS]
    assert evaluate(11, constraints, plan)[1] == 'PASS'


@pytest.mark.parametrize('reload_value,expected', [(0, 0), (1, 1)])
def test_load_checks_measures_reload_without_verify_flag(monkeypatch, tmp_path, reload_value, expected):
    import json
    import sys
    from graph import build_graph, checks
    rows, standards = fixture()
    path = tmp_path / 'rows.json'
    path.write_text(json.dumps(rows))
    metadata = tmp_path / 'standards.yaml'
    metadata.write_text(json.dumps(standards))
    driver = FakeDriver()
    class Context:
        def __enter__(self):
            return driver
        def __exit__(self, *args):
            pass
    def factory(*args, **kwargs):
        assert kwargs['notifications_disabled_classifications'] == ['UNRECOGNIZED']
        return Context()
    monkeypatch.setitem(sys.modules, 'neo4j', SimpleNamespace(GraphDatabase=SimpleNamespace(driver=factory)))
    monkeypatch.setenv('NEO4J_URI', 'bolt://localhost:7687')
    monkeypatch.setenv('NEO4J_PASSWORD', 'fake')
    loads = []
    def fake_load(*args):
        loads.append(args)
        return dict(nodes_created=8 if len(loads) == 1 else reload_value, relationships_created=0)
    monkeypatch.setattr(build_graph, 'load', fake_load)
    def run(_driver, plan, reload_counts):
        assert len(loads) == 2
        assert reload_counts == dict(nodes_created=reload_value, relationships_created=0)
        return [dict(status=evaluate(12, [], plan, reload_counts)[1])]
    monkeypatch.setattr(checks, 'run', run)
    assert build_graph.main(['--load', '--checks', '--input', str(path), '--standards', str(metadata)]) == expected
