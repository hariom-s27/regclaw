"""R22 R1-R6 + DC-2/4/5: Ragas evaluation wiring and outcome rules, with FAKE ragas and clients only."""
import importlib
import json
import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS

import pytest

import _netguard

ROOT = Path(__file__).resolve().parents[1]
SECRET = 'test-key-not-real'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for key in ('AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    # Denies socket.socket.connect except socketpair's own loopback (asyncio on Windows); see tests/_netguard.py
    _netguard.block_network(monkeypatch, 'Network forbidden')


@pytest.fixture
def ev(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'eval'))
    return importlib.import_module('eval_pageindex')


class Metric:
    """Fake collections metric: value per call from a script (number, NaN, None or exception)."""
    def __init__(self, name, script, calls, **kw):
        self.name, self.script, self.calls, self.init = name, script, calls, kw

    async def ascore(self, **kw):
        self.calls.append((self.name, kw))
        v = self.script(self.name, kw)
        if isinstance(v, Exception):
            raise v
        return NS(value=v)


@pytest.fixture
def fake_ragas(monkeypatch):
    rec = NS(llm=None, emb=None, calls=[], script=lambda name, kw: 0.8, imported=[])
    mods = {n: ModuleType(n) for n in ('ragas', 'ragas.llms', 'ragas.embeddings', 'ragas.embeddings.base',
                                       'ragas.metrics', 'ragas.metrics.collections')}

    def llm_factory(model, **kw):
        rec.llm = (model, kw)
        return NS(model=model)

    def embedding_factory(provider, **kw):
        rec.emb = (provider, kw)
        return NS(provider=provider)
    mods['ragas.llms'].llm_factory = llm_factory
    mods['ragas.embeddings.base'].embedding_factory = embedding_factory
    for cls in ('Faithfulness', 'AnswerRelevancy', 'AnswerCorrectness'):
        setattr(mods['ragas.metrics.collections'], cls,
                lambda cls=cls, **kw: Metric(cls, lambda n, k: rec.script(n, k), rec.calls, **kw))

    def forbidden(*a, **k):
        raise AssertionError('legacy ragas API used')
    mods['ragas'].evaluate = forbidden
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return rec


@pytest.fixture
def azure(monkeypatch):
    monkeypatch.setenv('AZURE_OPENAI_ENDPOINT', 'https://res.openai.azure.com/')
    monkeypatch.setenv('AZURE_OPENAI_API_KEY', SECRET)
    made = []
    import openai
    monkeypatch.setattr(openai, 'AsyncOpenAI', lambda **kw: made.append(kw) or NS(kind='async-openai'))
    return made


# ------------------------------------------------------------------ R1/R2/R3/D2 wiring
@pytest.mark.parametrize('judge,expected', [
    ('gpt-5-mini', {'max_tokens': 8192, 'reasoning_effort': 'low'}),
    ('o4-mini', {'max_tokens': 8192, 'reasoning_effort': 'low'}),
    ('gpt-4.1-mini', {'max_tokens': 8192})])
def test_judge_factory_args(ev, fake_ragas, azure, judge, expected):
    metrics = ev.build_ragas(judge)
    assert set(metrics) == {'faithfulness', 'answer_relevancy', 'answer_correctness'}
    model, kw = fake_ragas.llm
    assert model == judge and kw.pop('client').kind == 'async-openai'
    assert kw == expected and 'provider' not in kw            # default "openai", never "litellm"
    assert fake_ragas.emb[0] == 'openai'
    assert fake_ragas.emb[1]['model'] == 'text-embedding-3-small'
    assert azure == [{'base_url': 'https://res.openai.azure.com/openai/v1/', 'api_key': SECRET}]


@pytest.mark.parametrize('judge,emb', [('gpt-4o-mini', 'text-embedding-3-small'),
                                       ('claude-haiku-4-5', 'text-embedding-3-small'),
                                       ('gpt-5-mini', 'embed-prod')])
def test_unknown_deployments_refused_before_any_client(ev, fake_ragas, azure, judge, emb):
    with pytest.raises(ValueError, match='Unknown Azure deployment'):
        ev.build_ragas(judge, emb)
    assert azure == [] and fake_ragas.llm is None


def test_missing_azure_env_is_refused(ev, fake_ragas, monkeypatch):
    monkeypatch.delenv('AZURE_OPENAI_ENDPOINT', raising=False)
    with pytest.raises(RuntimeError, match='AZURE_OPENAI_ENDPOINT'):
        ev.build_ragas('gpt-5-mini')


def golden_file(tmp_path, items=None):
    items = items or [{'id': 'q1', 'question': 'Q1?', 'reference': 'R1', 'gold_pages': [12]},
                      {'id': 'q2', 'question': 'Q2?', 'reference': 'R2', 'gold_pages': [20, 21]},
                      {'id': 'q3', 'question': 'Q3?', 'reference': 'R3', 'gold_pages': []}]
    p = tmp_path / 'golden.json'
    p.write_text(json.dumps(items))
    return p


def settings_file(tmp_path, judge=None):
    p = tmp_path / 's.yaml'
    p.write_text('judge_model: %s\njudge_repeats: 3\n' % (judge or 'null'))
    return p


def test_no_judge_means_refusal(ev, fake_ragas, tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        ev.main(['--golden', str(golden_file(tmp_path)), '--dry-run', '--settings', str(settings_file(tmp_path))])
    assert e.value.code == 2 and 'no judge' in capsys.readouterr().err
    assert fake_ragas.llm is None


def test_no_ragas_needs_no_judge(ev, tmp_path):
    out = tmp_path / 'r.json'
    assert ev.main(['--golden', str(golden_file(tmp_path)), '--dry-run', '--no-ragas', '--runs', '1',
                    '--settings', str(settings_file(tmp_path)), '--out', str(out)]) == 0
    rows = json.loads(out.read_text())
    assert {r['id'] for r in rows} == {'q1', 'q2', 'q3'}
    assert not any(m in r for r in rows for m in ('context_precision', 'context_recall'))


# ------------------------------------------------------------------ scoring rules
def system_for(outputs):
    def run(question):
        return outputs[question]
    return run


OK = {'answer': 'Storage at 2-8 C [p. 12].', 'fetched_pages': [12, 13],
      'page_text': {12: 'p12', 13: 'p13'}, 'cited_pages': [12], 'status': 'ok'}
FAILED = {'answer': '', 'fetched_pages': [20], 'page_text': {20: 'p20'}, 'cited_pages': [],
          'status': 'failed:max_turns'}


def run_main(ev, monkeypatch, tmp_path, outputs, *extra, judge='gpt-5-mini'):
    monkeypatch.setattr(ev, 'load_system', lambda spec: system_for(outputs))
    out = tmp_path / 'rows.json'
    code = ev.main(['--golden', str(golden_file(tmp_path)), '--system', 'x:run', '--runs', '1',
                    '--settings', str(settings_file(tmp_path, judge)), '--out', str(out), *extra])
    return code, json.loads(out.read_text()), json.loads((tmp_path / 'rows.manifest.json').read_text())


def test_three_repeats_by_default_mean_min_max(ev, fake_ragas, azure, monkeypatch, tmp_path, capsys):
    values = iter([0.6, 0.9, 0.9] * 10)
    fake_ragas.script = lambda name, kw: next(values) if name == 'Faithfulness' else 0.8
    code, rows, manifest = run_main(ev, monkeypatch, tmp_path, {'Q1?': OK, 'Q2?': OK, 'Q3?': OK})
    assert code == 0 and manifest['valid'] and manifest['judge_repeats'] == 3
    q1 = [r for r in rows if r['id'] == 'q1']
    assert [r['judge_repeat'] for r in q1] == [1, 2, 3]
    assert ev.fmt([r['faithfulness'] for r in q1]) == '0.80 [0.60-0.90]'
    assert manifest['ragas']['metrics'] == ['faithfulness', 'answer_relevancy', 'answer_correctness']
    assert manifest['ragas']['judge_kwargs'] == {'max_tokens': 8192, 'reasoning_effort': 'low'}
    assert SECRET not in json.dumps(manifest) + capsys.readouterr().out


def test_unanswerable_excluded_counted_listed(ev, fake_ragas, azure, monkeypatch, tmp_path, capsys):
    code, rows, manifest = run_main(ev, monkeypatch, tmp_path, {'Q1?': OK, 'Q2?': OK, 'Q3?': OK})
    q3 = [r for r in rows if r['id'] == 'q3']
    assert all(r['ragas_note'] == 'unanswerable' for r in q3)
    assert all(r[m] is None for r in q3 for m in ('faithfulness', 'answer_relevancy', 'answer_correctness',
                                                  'hit', 'recall', 'precision'))
    assert not any(kw['user_input'] == 'Q3?' for _, kw in fake_ragas.calls)     # never judged
    assert manifest['counts']['unanswerable_ids'] == ['q3']
    assert manifest['counts']['ragas_na']['faithfulness']['excluded_unanswerable'] == 3
    assert "Unanswerable items (gold_pages == [], excluded from means): 1 ['q3']" in capsys.readouterr().out


def test_agent_failure_is_a_system_outcome_not_an_error(ev, fake_ragas, azure, monkeypatch, tmp_path, capsys):
    code, rows, manifest = run_main(ev, monkeypatch, tmp_path, {'Q1?': OK, 'Q2?': FAILED, 'Q3?': OK})
    assert code == 0 and manifest['valid']                               # agent failures never fail the run
    q2 = [r for r in rows if r['id'] == 'q2']
    assert len(q2) == 3
    for r in q2:
        # forced miss on hit/recall; precision stays over the fetched pages (p20 is gold), per DC-5
        assert (r['hit'], r['recall'], r['precision']) == (0.0, 0.0, 1.0)
        assert r['answer_correctness'] == 0.0
        assert r['faithfulness'] is None and r['answer_relevancy'] is None
    assert not any(kw['user_input'] == 'Q2?' for _, kw in fake_ragas.calls)
    assert manifest['counts']['agent_failures'] == [{'run': 1, 'id': 'q2', 'status': 'failed:max_turns'}]
    na = manifest['counts']['ragas_na']
    assert na['faithfulness']['not_computed_agent_failure'] == 3
    assert na['answer_correctness']['not_computed_agent_failure'] == 0
    assert 'run 1 q2: failed:max_turns' in capsys.readouterr().out


def test_agent_failure_with_nothing_fetched_has_precision_na(ev):
    assert ev.page_metrics([], [5], failed=True) == {'k': 0, 'hit': 0.0, 'recall': 0.0, 'precision': None}
    assert ev.page_metrics([5], [5], failed=True)['hit'] == 0.0
    assert ev.page_metrics([5, 6], [5]) == {'k': 2, 'hit': 1.0, 'recall': 1.0, 'precision': 0.5}


@pytest.mark.parametrize('bad', [float('nan'), None, RuntimeError('judge 500'), 'oops'])
def test_any_judge_error_invalidates_the_run(ev, fake_ragas, azure, monkeypatch, tmp_path, capsys, bad):
    fake_ragas.script = lambda name, kw: bad if (name == 'AnswerRelevancy' and kw['user_input'] == 'Q2?') else 0.7
    code, rows, manifest = run_main(ev, monkeypatch, tmp_path, {'Q1?': OK, 'Q2?': OK, 'Q3?': OK})
    assert code == 2 and not manifest['valid'] and manifest['exit_code'] == 2
    assert manifest['counts']['ragas_na']['answer_relevancy']['judge_error'] == 3
    assert manifest['counts']['ragas_na']['faithfulness']['judge_error'] == 0
    assert all(r['answer_relevancy'] is None for r in rows if r['id'] == 'q2')   # never 0, never dropped silently
    assert len(rows) == 9                                                       # raw rows still written
    captured = capsys.readouterr()
    assert 'WITHHELD' in captured.out and 'RUN INVALID' in captured.err


def test_cited_mode_uses_cited_pages(ev, fake_ragas, azure, monkeypatch, tmp_path):
    code, rows, manifest = run_main(ev, monkeypatch, tmp_path, {'Q1?': OK, 'Q2?': OK, 'Q3?': OK},
                                    '--retrieved', 'cited')
    q1 = [r for r in rows if r['id'] == 'q1'][0]
    assert q1['retrieved'] == [12] and q1['precision'] == 1.0 and manifest['retrieved'] == 'cited'


def test_golden_contract_is_validated(ev, tmp_path):
    with pytest.raises(ValueError, match='lacks'):
        ev.validate_golden([{'id': 'a', 'question': 'q', 'gold_pages': []}])
    with pytest.raises(ValueError, match='1-based'):
        ev.validate_golden([{'id': 'a', 'question': 'q', 'reference': 'r', 'gold_pages': [0]}])


def test_only_collections_api_is_referenced(ev):
    src = (ROOT / 'eval' / 'eval_pageindex.py').read_text()
    code = src.split('"""', 2)[2]          # skip the module docstring
    assert 'ragas.metrics.collections' in code
    assert 'from ragas import evaluate' not in code and 'ragas.evaluate' not in code
    assert 'provider="litellm"' not in code and 'provider="anthropic"' not in code
    assert 'ContextPrecision' not in code and 'ContextRecall' not in code
