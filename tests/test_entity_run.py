"""Deterministic freeze and run safety checks without network or corpus text."""
import csv
import json
from types import SimpleNamespace as NS

import pytest

from goldset.sample_entities import freeze
from ner.client import FakeClient, RawResult
from ner.extract import key_for, run_pipeline
from ner.live_controls import LiveControls, RunStopped, azure_cost, retry_delay


def test_freeze_reproducible_and_excludes_prior_use(tmp_path):
    def setup(folder):
        (folder / 'data').mkdir(parents=True)
        (folder / 'tests/fixtures').mkdir(parents=True)
        clauses = [dict(id=f'{doc}:{i}', doc=doc, text='x'*(100+i))
                   for doc in ('fda-iso10993-1', 'eu-mdr', 'imdrf-n47', 'in-mdr', 'epa-63o')
                   for i in range(40)]
        (folder / 'data/clauses.json').write_text(json.dumps(clauses))
        (folder / 'tests/fixtures/used.json').write_text('"in-mdr:1"')
        rows = freeze(folder)
        assert len(rows) == 50 and sum(r['split'] == 'dev' for r in rows) == 30
        assert len({(r['source'], r['length_tercile'], r['split']) for r in rows}) == 30
        assert not {'fda-iso10993-1:0', 'in-mdr:1'} & {r['clause_id'] for r in rows}
        with pytest.raises(FileExistsError):
            freeze(folder)
        return (folder / 'goldset/entity_gold_ids.csv').read_bytes()
    assert setup(tmp_path/'a') == setup(tmp_path/'b')


def test_provider_key_and_high_output_isolation(tmp_path):
    c = dict(id='x:1', doc='x', text='synthetic')
    client = FakeClient()
    base = key_for(c, client)
    client.provider = 'azure_openai'
    assert key_for(c, client) != base
    low = key_for(c, client)
    client.effort = 'high'
    assert key_for(c, client) != low
    run_pipeline([c], client, root=tmp_path)
    assert (tmp_path/'data/runs/azure_openai/s4/high/ner.json').exists()
    assert not (tmp_path/'data/runs/azure_openai/s4/ner.json').exists()


def test_retry_after_and_reasoning_not_double_billed():
    exc = NS(response=NS(headers={'retry-after': '130'}))
    assert retry_delay(exc, 2) == 130
    exc.response.headers = {'retry-after-ms': '3500'}
    assert retry_delay(exc, 2) == 3.5
    assert azure_cost(dict(input_tokens=1000, output_tokens=1000, reasoning_tokens=800)) == pytest.approx(.002475)


def test_budget_and_error_guards_prevent_further_calls(tmp_path):
    client = NS(mode='structured', effort='low', provider='azure_openai', model='synthetic',
                map_request=lambda req: dict(messages=req['messages'], response_format={}, max_completion_tokens=4000))
    now = [0.]
    sleeps = []
    def sleep(n):
        sleeps.append(n); now[0] += n
    controls = LiveControls(client, tmp_path, 10, clock=lambda: now[0], sleep=sleep)
    c = dict(id='x:1', doc='x', text='synthetic')
    for _ in range(20):
        ticket = controls.before_request(c, 4000)
        controls.after_request(ticket, raw=RawResult({}, usage={'input_tokens': 10, 'output_tokens': 10}))
    assert sleeps and all(s <= 30 for s in sleeps)
    with pytest.raises(RunStopped, match='error rows'):
        controls.completed({'x:1': {'error': 'refusal'}})
    with pytest.raises(RunStopped):
        controls.before_request(c, 4000)
    controls.stopped = None
    controls.ledger.append({'cost_estimate_usd': 8})
    with pytest.raises(RunStopped, match='cost'):
        controls.before_request(c, 4000)
    controls.stopped = None
    controls.total = controls.requested = 1143
    controls.ledger = [dict(clause_id=f'x:{i}', effort='low', status='response', cost_estimate_usd=.01) for i in range(10)]
    with pytest.raises(RunStopped, match='projected full-run cost'):
        controls.completed({})


def test_merge_cli_separates_provisional_outputs_and_keeps_weak_default(tmp_path, monkeypatch):
    import ner.merge as module
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    folder = tmp_path / 'data/runs/azure_openai/s4'
    folder.mkdir(parents=True)
    c = dict(id='in-mdr:1', doc='in-mdr', number='1', title='Synthetic', text='The manufacturer shall test.',
             parent=None, source_pdf='synthetic.pdf', page=1, informative=False)
    (tmp_path/'data/clauses.json').write_text(json.dumps([c]))
    (tmp_path/'data/weak_labels.json').write_text(json.dumps([dict(id=c['id'], weak_label='SHALL')]))
    metadata = dict(provider='azure_openai', model='gpt-5-mini', model_version='snapshot', reasoning_effort='low', provisional=True)
    (folder/'ner.json').write_text(json.dumps([dict(id=c['id'], entities={}, provenance=metadata)]))
    args = ['--ner', str(folder/'ner.json'), '--output', str(folder/'enriched.json')]
    assert module.main(args) == 0
    assert json.loads((folder/'enriched.json').read_text())[0]['obligation'] == 'SHALL'
    manifest = json.loads((folder/'enriched.provenance.json').read_text())
    assert manifest['provenance'] == metadata
    assert manifest['obligation_source'].endswith('weak_labels.json')
    assert not (tmp_path/'data/enriched.json').exists()
    with pytest.raises(ValueError, match='provider-separated'):
        module.main(['--ner', str(folder/'ner.json')])


def test_error_row_retains_reported_response_version(tmp_path):
    c = dict(id='x:1', doc='x', text='synthetic')
    metadata = dict(provider='azure_openai', model='gpt-5-mini', model_version='reported-snapshot', reasoning_effort='high', provisional=True)
    client = FakeClient({'x:1': RawResult({}, stop_reason='max_tokens', provenance=metadata)}, effort='high')
    client.provider = 'azure_openai'
    rows, summary = run_pipeline([c], client, root=tmp_path)
    assert rows[0]['status'] == 'error'
    assert rows[0]['provenance'] == metadata
    assert summary['provenance']['model_version'] == 'reported-snapshot'
