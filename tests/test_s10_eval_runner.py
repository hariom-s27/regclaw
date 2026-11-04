"""End-to-end S10 eval runner on FAKE profiles with a scripted FakeLLM (agreed items 6-8, C6)."""
import csv
import json
from pathlib import Path

import pytest

import _netguard
import standards.tool
from eval.s10 import runner as R
from standards import ruleset as RS
from standards.classifier import FakeLLM, LLMResponse
from standards.data_loader import ENDPOINTS

FIX = Path(__file__).parent / 'fixtures' / 's10_eval'
PROFILES, GOLD = FIX / 'fake_profiles.yaml', FIX / 'fake_gold.yaml'
NOW = '2026-11-04T00:00:00+00:00'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')
    writes = []
    monkeypatch.setattr(standards.tool, 'save_record', lambda *a, **k: writes.append(a))
    yield
    assert writes == []          # nothing persisted under data/


class ScriptedFake(FakeLLM):
    """Mirrors rule labels, then: upgrades -4 on FAKE-2 (warranted by fake gold), tries to
    downgrade -1 everywhere (guard blocks), flips -6 on FAKE-3 between calls, and in the
    'high' setting adds an over-claiming note (guard drops it)."""

    def __init__(self, name):
        super().__init__()
        self.name, self.calls = name, 0

    def classify(self, request):
        self.requests.append(request)
        self.calls += 1
        payload = json.loads(request['messages'][0]['content'])
        device = payload['device_profile']['device_name']
        items = []
        for p in payload['rule_labels']['parts']:
            label = p['label']
            if p['part'] == 'ISO 10993-1':
                label = 'not_applicable'
            if p['part'] == 'ISO 10993-4' and device.startswith('FAKE-2'):
                label = 'mandatory'
            if p['part'] == 'ISO 10993-6' and device.startswith('FAKE-3'):
                label = 'recommended' if self.calls % 2 else 'not_applicable'
            items.append(dict(part=p['part'], label=label, reason='Scripted fake reason.', clause_ids=[],
                              unsupported_by_corpus=True, superseded_edition=False))
        notes = [dict(text='FDA requires this, guaranteed.', clause_ids=[])] if self.name == 'high' else []
        return LLMResponse(dict(standards=items, notes=notes))


def run_cli(tmp_path, *extra, factory=None, seed='3'):
    out = tmp_path / 'out'
    clients = {}

    def make(setting):
        clients[setting.name] = ScriptedFake(setting.name)
        return clients[setting.name]

    rc = R.main(['--profiles', str(PROFILES), '--gold', str(GOLD), '--out', str(out), '--repeats', '2',
                 '--setting', 'medium:effort=medium', '--setting', 'high:effort=high', '--seed', seed,
                 '--n-boot', '50', *extra], client_factory=factory or make, now=NOW)
    return rc, out, clients


def load_runs(out):
    return [json.loads(line) for line in (out / 'runs.jsonl').read_text(encoding='utf-8').splitlines()]


def strict_json(path):
    def bad(token):
        raise ValueError(f'non-JSON constant {token}')
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=bad)


def test_end_to_end_fake_run(tmp_path):
    rc, out, clients = run_cli(tmp_path)
    assert rc == 0
    runs = load_runs(out)
    assert len(runs) == 3 * 2 * 2                                  # profiles x settings x repeats
    assert [r['call_index'] for r in runs] == list(range(12))
    for r in runs:
        assert set(r['final']) == set(r['rule']) and r['llm_status'] == 'ok'
        assert set(ENDPOINTS) <= set(r['rule_endpoints'])          # C1 diagnostic endpoint labels
        assert r['ruleset'] == RS.FDA_RULESET and r['ruleset_sha256'] == RS.RULESET_SHA256
        assert r['final']['ISO 10993-1'] == 'mandatory' and r['llm']['ISO 10993-1'] == 'not_applicable'
        assert r['overclaim_hits'] == []
    summary = strict_json(out / 'summary.json')
    prov = summary['provenance']
    assert prov['engine_version'] == 'S10.v1' and prov['engine_sha256'] == RS.ENGINE_SHA256
    assert prov['engine_files_sha256'] == RS.ENGINE_FILES_SHA256
    for key in ('git_sha', 'git_dirty', 'ruleset', 'ruleset_version', 'ruleset_sha256', 'matrix_sha256', 'settings',
                'seed', 'timestamp', 'repeats', 'n_boot', 'effort_effective', 'engine_version', 'prompt_sha256', 'files'):
        assert key in prov, key
    assert prov['ruleset'] == [RS.FDA_RULESET] and prov['matrix_sha256'] == RS.RULESET_FILES_SHA256[RS.MATRIX_FILE]
    assert prov['seed'] == 3 and prov['repeats'] == 2 and prov['timestamp'] == NOW and prov['split'] == 'dev'
    assert prov['settings'] == [dict(name='medium', effort='medium'), dict(name='high', effort='high')]
    medium, high = summary['settings']['medium'], summary['settings']['high']
    assert medium['blocked_downgrades']['blocked_downgrades'] == 6            # -1 on 3 profiles x 2 repeats
    assert high['wording_events']['banned_wording_note'] == 6 and medium['wording_events']['banned_wording_note'] == 0
    assert medium['upgrades']['warranted_upgrades'] == 2                     # FAKE-2 -4, two repeats
    assert medium['stability']['flipping_cells'] == [dict(profile_id='FAKE-3', item='ISO 10993-6',
                                                          labels=['not_applicable', 'recommended'])]
    m = medium['metrics']['metrics']['under_call_rate']
    assert m['rules_only']['point'] > m['hybrid']['point']                  # LLM fixes the -4 under-call
    assert medium['metrics']['resampling_unit'] == 'profile' and medium['coverage']['n_cells'] == 18
    assert medium['citation_validity'] == dict(kept=0, unretrieved_clause=0)


def test_setting_reaches_outgoing_request(tmp_path):
    _, out, clients = run_cli(tmp_path)
    for name in ('medium', 'high'):
        sent = {req['output_config']['effort'] for req in clients[name].requests}
        assert sent == {name}                                     # effort sent == setting
    runs = load_runs(out)
    assert all(r['effort_effective'] == r['setting'] for r in runs)
    prov = strict_json(out / 'summary.json')['provenance']
    assert prov['effort_effective'] == {'medium': ['medium'], 'high': ['high']}
    assert strict_json(out / 'summary.json')['settings']['high']['effort_effective'] == ['high']


def test_setting_without_effort_keeps_request_default(tmp_path):
    out = tmp_path / 'o'
    fake = FakeLLM()
    R.main(['--profiles', str(PROFILES), '--out', str(out), '--repeats', '1', '--setting', 'base'],
           client_factory=lambda s: fake, now=NOW)
    assert {r['output_config']['effort'] for r in fake.requests} == {'low'}
    assert {r['effort_effective'] for r in load_runs(out)} == {'low'}


def test_order_is_interleaved_across_settings(tmp_path):
    _, out, _ = run_cli(tmp_path)
    runs = load_runs(out)
    pairs = [runs[i:i + 2] for i in range(0, len(runs), 2)]
    orders = set()
    for a, b in pairs:                               # each (repeat, profile) runs every setting back to back
        assert (a['repeat'], a['profile_id']) == (b['repeat'], b['profile_id']) and a['setting'] != b['setting']
        orders.add((a['setting'], b['setting']))
    assert orders == {('medium', 'high'), ('high', 'medium')}     # rotation: no fixed first position
    assert [r['repeat'] for r in runs] == sorted(r['repeat'] for r in runs)


def test_plan_order_is_seeded():
    s = [R.Setting('a'), R.Setting('b')]
    ids = [f'P{i}' for i in range(6)]
    assert R.plan_order(ids, s, 3, 1) == R.plan_order(ids, s, 3, 1)
    assert R.plan_order(ids, s, 3, 1) != R.plan_order(ids, s, 3, 2)


def test_rerun_same_seed_is_identical(tmp_path):
    _, out1, _ = run_cli(tmp_path / 'a')
    _, out2, _ = run_cli(tmp_path / 'b')
    s1, s2 = strict_json(out1 / 'summary.json'), strict_json(out2 / 'summary.json')
    assert s1['settings'] == s2['settings'] and s1['provenance']['ruleset_sha256'] == s2['provenance']['ruleset_sha256']


def test_review_csv_is_blank_for_humans(tmp_path):
    _, out, _ = run_cli(tmp_path)
    with (out / 'citation_review.csv').open(encoding='utf-8', newline='') as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ['profile_id', 'setting', 'repeat', 'item', 'label', 'cited_clause_ids',
                       'judgement_supported_partial_not', 'notes']
    body = rows[1:]
    assert body and {r[2] for r in body} == {'1'} and all(r[6] == '' and r[7] == '' for r in body)
    assert {r[1] for r in body} == {'medium', 'high'}


def test_citations_reuse_guard_validity(tmp_path):
    class Retriever:
        def retrieve(self, query, top_k=30):
            return [dict(clause_id='fda-iso10993-1:FAKE.1', doc='fda-iso10993-1')], None

    class Citing(FakeLLM):
        def classify(self, request):
            resp = super().classify(request)
            for item in resp.data['standards']:
                item['clause_ids'] = ['FDA-ISO10993-1:fake.1', 'invented:9']
            return resp

    out = tmp_path / 'c'
    R.main(['--profiles', str(PROFILES), '--gold', str(GOLD), '--out', str(out), '--repeats', '1', '--n-boot', '10'],
           client_factory=lambda s: Citing(), retriever=Retriever(), now=NOW)
    runs = load_runs(out)
    assert all(v == ['fda-iso10993-1:FAKE.1'] for r in runs for v in r['citations'].values())
    validity = strict_json(out / 'summary.json')['settings']['default']['citation_validity']
    assert validity['unretrieved_clause'] == validity['kept'] > 0          # one invented id dropped per kept id
    rows = list(csv.reader((out / 'citation_review.csv').open(encoding='utf-8')))
    assert all(r[5] == 'fda-iso10993-1:FAKE.1' for r in rows[1:])


def test_runner_refuses_when_rule_data_changed(tmp_path, monkeypatch):
    monkeypatch.setitem(RS.RULESET_FILES_SHA256, RS.MATRIX_FILE, '0' * 64)
    with pytest.raises(RS.RulesetChanged):
        run_cli(tmp_path)


def test_runner_refuses_when_engine_code_changed(tmp_path, monkeypatch):
    monkeypatch.setitem(RS.ENGINE_FILES_SHA256, 'guard.py', '0' * 64)
    with pytest.raises(RS.EngineChanged):
        run_cli(tmp_path)
    assert not (tmp_path / 'out' / 'runs.jsonl').exists()


def test_cli_rejects_live_and_bad_settings(tmp_path):
    with pytest.raises(SystemExit):
        R.main(['--profiles', str(PROFILES), '--out', str(tmp_path), '--llm', 'live'])
    with pytest.raises(SystemExit):
        R.main(['--profiles', str(PROFILES), '--out', str(tmp_path), '--setting', 'a:temperature=1'])
    with pytest.raises(SystemExit):
        R.main(['--profiles', str(PROFILES), '--out', str(tmp_path), '--setting', 'a', '--setting', 'a'])
