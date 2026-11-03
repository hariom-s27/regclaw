"""R22 R7: navigator failures and cited-page validation, with a scripted FAKE Anthropic client."""
import importlib
import socket
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')

    def deny(*args, **kwargs):
        raise AssertionError('Network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', deny)


@pytest.fixture
def pi(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'pageindex'))
    agent = importlib.import_module('pi_agent')
    common = importlib.import_module('pi_common')
    return NS(agent=agent, common=common)


def usage():
    return NS(input_tokens=1, output_tokens=1)


def text(t, stop='end_turn'):
    return NS(content=[NS(type='text', text=t)] if t else [], stop_reason=stop, model='nav', usage=usage())


def tool(name, args, tid='t1'):
    return NS(content=[NS(type='tool_use', name=name, input=args, id=tid)], stop_reason='tool_use',
              model='nav', usage=usage())


def verdicts(*items):
    return NS(content=[NS(type='tool_use', input={'verdicts': list(items)})], model='ver', usage=usage())


class Client:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []
        self.messages = NS(create=self.create)

    def create(self, **kw):
        self.requests.append(kw)
        return self.responses.pop(0)


def session(pi, max_turns=12):
    rec = NS(title='Doc', version='v', page_count=5, doc_description='', doc_id='gtp@2019', pdf_path='x.pdf')
    return pi.agent.Session(rec=rec, tree={'structure': []}, nodes={},
                            s=pi.common.Settings(agent_max_turns=max_turns))


@pytest.mark.parametrize('first,reason', [(text('partial', 'max_tokens'), 'max_tokens'),
                                          (text(''), 'empty_answer')])
def test_one_retry_then_answer(pi, first, reason, caplog):
    sess = session(pi)
    client = Client(first, text('Answer.'))
    answer, messages, use = pi.agent.run_agent(client, sess, 'q')
    assert answer == 'Answer.' and sess.failure is None and sess.retries == 1
    assert use['turns'] == 2
    assert all(m['content'] != first.content for m in messages if m['role'] == 'assistant')
    assert reason in caplog.text


@pytest.mark.parametrize('bad,reason', [(lambda: text('x', 'max_tokens'), 'max_tokens'),
                                        (lambda: text(''), 'empty_answer')])
def test_second_failure_fails_the_item(pi, bad, reason):
    sess = session(pi)
    client = Client(bad(), bad())
    answer, _, _ = pi.agent.run_agent(client, sess, 'q')
    assert answer == '' and sess.failure == reason and len(client.requests) == 2


def test_max_turns_is_a_failure_not_a_forced_answer(pi):
    sess = session(pi, max_turns=2)
    client = Client(tool('get_tree_overview', {}, 'a'), tool('get_tree_overview', {}, 'b'))
    answer, _, use = pi.agent.run_agent(client, sess, 'q')
    assert answer == '' and sess.failure == 'max_turns' and use['turns'] == 2
    assert all(r['tool_choice'] == {'type': 'auto'} for r in client.requests)


def test_answer_question_reports_failure_and_skips_verify(pi, monkeypatch):
    s = pi.common.Settings(agent_max_turns=1)
    rec = NS(title='Doc', version='v', page_count=5, doc_description='', doc_id='gtp@2019',
             pdf_path='x.pdf', tree_path='t.json')
    monkeypatch.setattr(pi.agent, 'resolve_edition', lambda *a: rec)
    monkeypatch.setattr(pi.agent, 'load_manifest', lambda s: None)
    monkeypatch.setattr(pi.agent, 'load_tree', lambda p: {'structure': []})
    monkeypatch.setattr(pi.agent, 'fetch_pages', lambda path, pages: {p: f'page {p}' for p in pages})
    client = Client(tool('fetch_pages', {'pages': '2'}))
    monkeypatch.setattr(pi.agent.anthropic, 'Anthropic', lambda: client)
    r = pi.agent.answer_question('q', 'gtp', s=s)
    assert r['status'] == 'failed:max_turns' and r['answer'] == '' and r['cited_pages'] == []
    assert r['fetched_pages'] == [2] and r['verdicts'] == []
    assert len(client.requests) == 1          # no verifier call for a failed item


def test_cited_pages_are_validated_against_fetched(pi, monkeypatch):
    s = pi.common.Settings()
    rec = NS(title='Doc', version='v', page_count=5, doc_description='', doc_id='gtp@2019',
             pdf_path='x.pdf', tree_path='t.json')
    monkeypatch.setattr(pi.agent, 'resolve_edition', lambda *a: rec)
    monkeypatch.setattr(pi.agent, 'load_manifest', lambda s: None)
    monkeypatch.setattr(pi.agent, 'load_tree', lambda p: {'structure': []})
    monkeypatch.setattr(pi.agent, 'fetch_pages', lambda path, pages: {p: 'Storage shall be at 2-8 C.' for p in pages})
    ok = {'claim_id': 1, 'verdict': 'supported', 'evidence': 'Storage shall be at 2-8 C.', 'reason': ''}
    client = Client(tool('fetch_pages', {'pages': '2'}),
                    text('Storage shall be at 2-8 C [p. 2]. Labels are required [p. 4].'),
                    verdicts(ok, {**ok, 'claim_id': 2}),
                    # repair round: the unfetched citation is dropped
                    text('Storage shall be at 2-8 C [p. 2].'),
                    verdicts(ok))
    monkeypatch.setattr(pi.agent.anthropic, 'Anthropic', lambda: client)
    r = pi.agent.answer_question('q', 'gtp', s=s)
    assert r['status'] == 'ok' and r['repaired'] is True
    assert r['cited_pages'] == [2] and r['cited_not_fetched'] == []
    assert set(r['cited_pages']) <= set(r['fetched_pages'])


def test_unfetched_citation_is_listed_and_never_cited(pi):
    out = pi.agent.verify(Client(verdicts({'claim_id': 1, 'verdict': 'supported', 'evidence': '', 'reason': ''})),
                          pi.common.Settings(), 'Labels are required [p. 4].', {2: 'x'})
    assert out[0]['verdict'] == 'unsupported' and out[0]['pages'] == [4]
