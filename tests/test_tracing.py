"""S14 invariants with fake SDKs, a one-pass Result and strict cursors."""
from contextlib import contextmanager
import copy
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

from observability import tracing
from retrieval.hybrid_retriever import HybridRetriever

ROOT = Path(__file__).resolve().parents[1]


class FakeLangfuse:
    def __init__(self):
        self.spans, self.stack = [], []
        self.flushes = 0

    @contextmanager
    def start_as_current_observation(self, *, name, as_type='span', **attributes):
        row = dict(id=str(len(self.spans)), name=name, type=as_type,
                   parent=self.stack[-1]['id'] if self.stack else None, **attributes)
        self.spans.append(row)
        self.stack.append(row)
        try:
            yield NS(update=lambda **kw: row.update(kw))
        finally:
            assert self.stack.pop() is row

    def flush(self):
        self.flushes += 1


@pytest.fixture
def fake(monkeypatch):
    client = FakeLangfuse()
    monkeypatch.setenv('LANGFUSE_ENABLED', 'true')
    monkeypatch.setenv('LANGFUSE_TRACING_ENABLED', 'true')
    monkeypatch.setattr(tracing, '_get_client', lambda: client)
    def deny(*args, **kwargs):
        raise AssertionError('Network forbidden, including OTLP')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    return client


class OnePass:
    def __init__(self, rows):
        self.rows, self.reads = rows, 0

    def __iter__(self):
        self.reads += 1
        assert self.reads == 1, 'Result consumed twice'
        yield from self.rows

    def __len__(self):
        raise AssertionError('len on lazy result')

    def data(self):
        raise AssertionError('data on lazy result')


class Cursor:
    def __init__(self, rows):
        self.rows, self.reads = rows, 0

    def fetchall(self):
        self.reads += 1
        assert self.reads == 1
        return self.rows

    def __iter__(self):
        raise AssertionError('tracing iterated cursor')

    def __len__(self):
        raise AssertionError('tracing measured cursor')


def retriever(monkeypatch, fail=None):
    # CI needs no database drivers. These stand-ins only implement constructors
    # used by the production stage bodies; query and result handling stay real.
    monkeypatch.setitem(sys.modules, 'neo4j', NS(Query=lambda text, **kw: text))
    monkeypatch.setitem(sys.modules, 'pgvector', NS(Vector=lambda value: value))
    r = HybridRetriever.__new__(HybridRetriever)
    r.db, r.timeout, r.max_retries = 'neo4j', 5, 0
    r.labels, r.rels, r.schema = {'Clause'}, set(), 'Clause'
    r.lazy = OnePass([{'clause_id': 'd:1'}, {'clause_id': 'd:2'}])
    r.cursors = []
    @contextmanager
    def session(**kw):
        yield NS(execute_read=lambda work: work(NS(run=lambda *a: r.lazy)))
    def execute_query(query, params=None, **kw):
        if query.startswith('EXPLAIN'):
            return [], NS(query_type='r'), None
        if 'RETURN sid' in query:
            return [{'sid': 'd:1', 'nid': 'd:1.1', 'rel': 'child'}], None, None
        return [dict(id=cid, number=cid.split(':')[1], title='Title', text='PRIVATE clause body',
                     obligation='NONE', standard='Public document') for cid in params['ids']], None, None
    r.driver = NS(session=session, execute_query=execute_query)
    def execute(query, params):
        if fail:
            raise fail
        cursor = Cursor([('d:1', .9), ('d:2', .8)] if 'ORDER BY' in query else [('d:1.1', .85)])
        r.cursors.append(cursor)
        return cursor
    r.pg = NS(execute=execute)
    r.oai = NS(embeddings=NS(create=lambda **kw: NS(data=[NS(embedding=[1., 0.])])))
    response = NS(content=[NS(type='tool_use', input=dict(cypher='MATCH (c:Clause) RETURN c.id AS clause_id', params={}))],
                  model='fake-haiku', usage=NS(input_tokens=7, output_tokens=3))
    r.llm = NS(messages=NS(create=lambda **kw: response))
    r.snapshots = {}
    for name in ('stage1_filter', 'embed', 'stage2_rank', 'stage3_expand', 'stage4_fetch'):
        fn = getattr(r, name)
        def capture(*args, _name=name, _fn=fn, **kw):
            value = _fn(*args, **kw)
            r.snapshots[_name] = copy.deepcopy(value)
            return value
        setattr(r, name, capture)
    return r


def test_on_off_identity_and_trace_shape(monkeypatch, fake):
    outputs, snapshots = [], []
    for flag in ('false', 'true'):
        monkeypatch.setenv('LANGFUSE_ENABLED', flag)
        r = retriever(monkeypatch)
        hits, run = r.retrieve('PRIVATE prompt')
        outputs.append(hits)
        snapshots.append(r.snapshots)
        assert r.lazy.reads == 1 and all(c.reads == 1 for c in r.cursors)
        assert set(run.ms) == {'stage1', 'stage2', 'stage3', 'stage4'}
    assert snapshots[0] == snapshots[1]
    assert outputs[0] == outputs[1]
    assert [h.score for h in outputs[0]] == pytest.approx([.9, .8, .85])
    roots = [s for s in fake.spans if s['parent'] is None]
    assert len(roots) == 1 and roots[0]['name'] == 'retriever' and roots[0]['type'] == 'retriever'
    assert [s['name'] for s in fake.spans if s['parent'] == roots[0]['id']] == [
        'neo4j.cypher', 'embed.query', 'pgvector.rank', 'neighbour.expand', 'text.fetch']
    assert all(s['parent'] is None or s['parent'] in {v['id'] for v in fake.spans} for s in fake.spans)
    assert fake.spans[2]['name'] == 'cypher.generate' and fake.spans[2]['parent'] == fake.spans[1]['id']
    serialized = json.dumps(fake.spans)
    assert 'PRIVATE' not in serialized and 'input' not in roots[0] and 'output' not in roots[0]
    tracing.flush()
    assert fake.flushes == 1


def test_off_does_not_import_langfuse():
    code = '''import sys
from observability.tracing import stage
from retrieval.hybrid_retriever import HybridRetriever
with stage('off') as observation:
    assert observation is None
assert 'langfuse' not in sys.modules
'''
    env = {**os.environ, 'LANGFUSE_ENABLED': 'false'}
    subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, check=True)


def test_sdk_kill_switch_is_identity_without_otlp(monkeypatch, fake):
    baseline, _ = retriever(monkeypatch).retrieve('q')
    fake.spans.clear()
    monkeypatch.setenv('LANGFUSE_TRACING_ENABLED', 'false')
    def forbidden():
        raise AssertionError('kill switch must not create a client')
    monkeypatch.setattr(tracing, '_get_client', forbidden)
    actual, _ = retriever(monkeypatch).retrieve('q')
    assert actual == baseline and not fake.spans
    tracing.flush()


@pytest.mark.parametrize('flag', ['false', 'true'])
def test_stage_exception_preserves_type_and_instance(monkeypatch, fake, flag):
    monkeypatch.setenv('LANGFUSE_ENABLED', flag)
    error = LookupError('stage failed')
    with pytest.raises(LookupError) as caught:
        retriever(monkeypatch, fail=error).retrieve('q')
    assert caught.value is error and not fake.stack


def test_generation_response_metadata_and_no_io(fake):
    from standards.classifier import AnthropicLLM
    from ner.client import AnthropicClient
    response = NS(content=[NS(type='text', text='{}')], stop_reason='end_turn', model='response-model',
                  usage=NS(input_tokens=11, output_tokens=4, model_dump=lambda: {'input_tokens': 11, 'output_tokens': 4}))
    sdk = NS(messages=NS(create=lambda **kw: response))
    assert AnthropicLLM(sdk).classify({'model': 'requested'}).data == '{}'
    client = AnthropicClient.__new__(AnthropicClient)
    client.client, client.effort, client.mode = sdk, 'low', 'structured'
    assert client.extract(dict(id='d:1', doc='d', text='PRIVATE')).model == 'response-model'
    assert [s['name'] for s in fake.spans] == ['standards.classify', 'ner.extract']
    for row in fake.spans:
        assert row['type'] == 'generation' and row['model'] == 'response-model'
        assert row['usage_details'] == {'input': 11, 'output': 4}
        assert 'input' not in row and 'output' not in row


def test_pageindex_generation_and_tools(monkeypatch, fake):
    monkeypatch.syspath_prepend(str(ROOT / 'pageindex'))
    pi = importlib.import_module('pi_agent')
    common = importlib.import_module('pi_common')
    sess = pi.Session(rec=NS(title='Doc', version='v', page_count=1, doc_description=''),
                      tree={'structure': []}, nodes={}, s=common.Settings())
    responses = iter([
        NS(content=[NS(type='tool_use', name='get_tree_overview', input={}, id='tool')],
           stop_reason='tool_use', model='navigator', usage=NS(input_tokens=2, output_tokens=1)),
        NS(content=[NS(type='text', text='Answer.')], stop_reason='end_turn', model='navigator', usage=NS(input_tokens=3, output_tokens=1)),
        NS(content=[NS(type='tool_use', input={'verdicts': []})], model='verifier', usage=NS(input_tokens=2, output_tokens=1))])
    client = NS(messages=NS(create=lambda **kw: next(responses)))
    with tracing.stage('pageindex.agent', as_type='agent'):
        answer, _, usage = pi.run_agent(client, sess, 'PRIVATE')
        pi.verify(client, sess.s, answer, {})
    assert usage['turns'] == 2
    assert [s['name'] for s in fake.spans] == ['pageindex.agent', 'pageindex.navigate',
                                              'pageindex.tool', 'pageindex.navigate', 'pageindex.verify']
    assert 'PRIVATE' not in json.dumps(fake.spans)


def test_summary_fixture_math_and_pages(capsys):
    from observability.run_summary import fixture_rows, summarize, main, exclusive
    rows = fixture_rows(ROOT / 'tests/fixtures/tracing/observations.json')
    summary = summarize(rows, 60)
    assert summary.observation_latencies_s['retriever'] == pytest.approx([.1, .2, .3])
    assert summary.observation_latencies_s['retriever.self'] == pytest.approx([.02, .04, .06])
    from observability.run_summary import percentile
    assert percentile(summary.observation_latencies_s['pgvector.rank'], 50) == pytest.approx(.04)
    assert percentile(summary.observation_latencies_s['pgvector.rank'], 95) == pytest.approx(.06)
    main(['--fixture', str(ROOT / 'tests/fixtures/tracing/observations.json')])
    output = capsys.readouterr().out
    assert 'retriever.self | 3 | 40 ms | 60 ms' in output
    assert 'P95 from <100 runs is noisy' in output
    # Overlap and grandchildren: 0..100 parent, union 10..80 => self 30.
    root = dict(id='r', startTime='2026-10-15T00:00:00Z', endTime='2026-10-15T00:00:00.100Z')
    children = [dict(parentObservationId='r', startTime='2026-10-15T00:00:00.010Z', endTime='2026-10-15T00:00:00.060Z'),
                dict(parentObservationId='r', startTime='2026-10-15T00:00:00.050Z', endTime='2026-10-15T00:00:00.080Z'),
                dict(parentObservationId='child', startTime='2026-10-15T00:00:00Z', endTime='2026-10-15T00:00:00.100Z')]
    assert exclusive(root, children) == pytest.approx(.03)
    children[0]['endTime'] = None
    assert exclusive(root, children) is None


def test_summary_v2_cursor_pagination():
    from observability.run_summary import fetch_observations
    calls = []
    def get_many(**kw):
        calls.append(kw)
        return NS(data=[{'id': len(calls)}], meta=NS(cursor='next' if len(calls) == 1 else None))
    client = NS(api=NS(observations=NS(get_many=get_many)))
    assert fetch_observations(client, None, None, pause_s=0) == [{'id': 1}, {'id': 2}]
    assert [c['cursor'] for c in calls] == [None, 'next']
