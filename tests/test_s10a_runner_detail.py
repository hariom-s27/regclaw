"""llm_detail per run, primary vs secondary metrics, the exact under-call interval in summary.json
and the split by cell tag. Fake profiles, fake gold, fake tags and FakeLLM only."""
import json
from pathlib import Path

import pytest

import _netguard
import standards.tool
from eval.s10 import metrics as M
from eval.s10 import runner as R
from eval.s10.tagger import TAGS, TagError
from standards.classifier import FakeLLM, LLMResponse

FIX = Path(__file__).parent / 'fixtures' / 's10_eval'
PROFILES, GOLD, TAGS_FILE = FIX / 'fake_profiles.yaml', FIX / 'fake_gold.yaml', FIX / 'fake_tags.json'
NOW = '2026-11-13T00:00:00+00:00'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')
    monkeypatch.setattr(standards.tool, 'save_record', lambda *a, **k: None)


def resp(stop='end_turn', finish=None, data=None):
    return LLMResponse(dict(standards=[], notes=[]) if data is None else data, stop, finish_reason=finish)


@pytest.mark.parametrize('response,error,status,expected', [
    (resp(), None, 'ok', 'ok'),
    (resp('max_tokens', 'length'), None, 'fallback', 'truncated'),
    (resp('end_turn', 'length'), None, 'fallback', 'truncated'),          # raw finish_reason wins
    (resp('refusal', 'stop'), None, 'fallback', 'refusal'),
    (resp('refusal', 'content_filter'), None, 'fallback', 'refusal'),
    (resp(data={}), None, 'fallback', 'parse_error'),                    # empty data
    (resp(data='not json'), None, 'fallback', 'parse_error'),
    (None, 'APIStatusError', 'fallback', 'error'),                       # exception from the client
    (resp('tool_calls'), None, 'fallback', 'error'),
    (resp('unavailable'), None, 'fallback', 'unavailable'),
    (None, None, 'fallback', 'unavailable'),
])
def test_llm_detail_mapping(response, error, status, expected):
    assert R.llm_detail(response, error, status) == expected and expected in R.LLM_DETAILS


class Flaky(FakeLLM):
    """Mirrors the rule labels and upgrades -4 on FAKE-2 and -3 on FAKE-3 (both warranted by the
    fake gold), so a clean run has no under-call; behaves badly on one device, by name."""

    def __init__(self, bad_device=None, how=None):
        super().__init__()
        self.bad_device, self.how = bad_device, how

    def classify(self, request):
        payload = json.loads(request['messages'][0]['content'])
        device = payload['device_profile']['device_name']
        if self.bad_device and device.startswith(self.bad_device):
            if self.how == 'raise':
                raise RuntimeError('offline fixture failure')
            if self.how == 'empty':
                return LLMResponse({})
            return LLMResponse(dict(standards=[], notes=[]), self.how)
        out = super().classify(request)
        for item in out.data['standards']:
            if (item['part'], device[:6]) in {('ISO 10993-4', 'FAKE-2'), ('ISO 10993-3', 'FAKE-3')}:
                item['label'] = 'mandatory'
        return out


def run_cli(tmp_path, factory, *extra, repeats='2'):
    out = tmp_path / 'out'
    R.main(['--profiles', str(PROFILES), '--gold', str(GOLD), '--out', str(out), '--repeats', repeats,
            '--n-boot', '30', *extra], client_factory=factory, now=NOW)
    runs = [json.loads(line) for line in (out / 'runs.jsonl').read_text(encoding='utf-8').splitlines()]
    return runs, json.loads((out / 'summary.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('how,detail', [('max_tokens', 'truncated'), ('refusal', 'refusal'),
                                        ('empty', 'parse_error'), ('raise', 'error')])
def test_each_bad_run_gets_its_llm_detail_and_guard_status_is_unchanged(tmp_path, how, detail):
    runs, summary = run_cli(tmp_path, lambda s: Flaky('FAKE-2', how))
    for r in runs:
        bad = r['profile_id'] == 'FAKE-2'
        assert r['llm_detail'] == (detail if bad else 'ok')
        assert r['llm_status'] == ('fallback' if bad else 'ok')            # guard.py still says ok/fallback only
        assert r['llm_error'] == ('RuntimeError' if bad and how == 'raise' else None)
    s = summary['settings']['default']
    assert s['llm_detail'] == {**{d: 0 for d in R.LLM_DETAILS}, 'ok': 4, detail: 2}
    assert s['llm_status'] == dict(ok=4, fallback=2)


def test_primary_uses_all_runs_and_secondary_only_ok_runs(tmp_path):
    runs, summary = run_cli(tmp_path, lambda s: Flaky('FAKE-2', 'max_tokens'))
    s = summary['settings']['default']
    sec = s['secondary_ok_only']
    assert s['metrics_scope'] == R.PRIMARY_SCOPE and sec['metrics_scope'] == R.SECONDARY_SCOPE
    assert summary['metric_scopes'] == dict(primary=R.PRIMARY_SCOPE, secondary_ok_only=R.SECONDARY_SCOPE)
    assert (s['n_runs'], s['n_profiles'], s['coverage']['n_cells']) == (6, 3, 18)
    assert (sec['n_runs'], sec['n_profiles'], sec['coverage']['n_cells']) == (4, 2, 12)
    # FAKE-2's truncated runs fall back to the rule floor, which under-calls -4 (fake gold: mandatory).
    primary = s['metrics']['metrics']['under_call_rate']['hybrid']['point']
    secondary = sec['metrics']['metrics']['under_call_rate']['hybrid']['point']
    assert primary > 0 and secondary == 0
    assert s['profiles_with_under_call']['count'] == 1 and sec['profiles_with_under_call']['count'] == 0


def test_all_ok_runs_make_primary_equal_secondary(tmp_path):
    _, summary = run_cli(tmp_path, lambda s: Flaky())
    s = summary['settings']['default']
    assert s['llm_detail']['ok'] == 6 == s['secondary_ok_only']['n_runs']
    assert s['metrics'] == s['secondary_ok_only']['metrics']


def test_summary_has_exact_interval_and_labels_the_bootstrap_as_approximate(tmp_path):
    _, summary = run_cli(tmp_path, lambda s: Flaky('FAKE-2', 'max_tokens'))
    s = summary['settings']['default']
    u = s['profiles_with_under_call']
    lo, hi = M.clopper_pearson(1, 3)
    assert (u['count'], u['n'], u['method'], u['unit']) == (1, 3, 'exact Clopper-Pearson interval', 'profile')
    assert u['rate'] == pytest.approx(1 / 3) and (u['lo'], u['hi']) == pytest.approx((lo, hi))
    assert s['metrics']['ci_label'] == 'approximate (few profiles)'
    assert summary['intervals']['bootstrap'].startswith('approximate (few profiles)')


def test_no_tags_file_means_no_split(tmp_path):
    _, summary = run_cli(tmp_path, lambda s: Flaky())
    s = summary['settings']['default']
    assert 'by_tag' not in s and 'by_tag' not in s['secondary_ok_only']
    assert summary['provenance']['tag_split'].startswith('none')


def test_every_cell_metric_is_split_by_all_three_tags(tmp_path):
    _, summary = run_cli(tmp_path, lambda s: Flaky('FAKE-2', 'max_tokens'), '--tags', str(TAGS_FILE))
    assert summary['provenance']['tag_split'].startswith('by matrix_settled')
    assert TAGS_FILE.name in summary['provenance']['files']
    for block in (summary['settings']['default'], summary['settings']['default']['secondary_ok_only']):
        by = block['by_tag']
        assert set(by) == set(TAGS) == {'matrix_settled', 'always_rule', 'judgement'}
        assert sum(t['n_cells'] for t in by.values()) == block['coverage']['n_cells']
        for t in by.values():
            for key in ('metrics', 'upgrades', 'disagreements', 'profiles_with_under_call',
                        'blocked_downgrades', 'stability', 'n_output_cells'):
                assert key in t, key
            assert set(t['metrics']['metrics']) == set(M.CM_METRICS)
    by = summary['settings']['default']['by_tag']
    assert (by['matrix_settled']['n_cells'], by['always_rule']['n_cells'], by['judgement']['n_cells']) == (8, 0, 10)
    assert by['always_rule']['n_output_cells'] == 4                       # -1 of FAKE-1 and FAKE-2, two repeats
    # The -4 under-call of FAKE-2 sits in a judgement cell.
    assert by['judgement']['profiles_with_under_call']['count'] == 1
    assert by['matrix_settled']['profiles_with_under_call']['count'] == 0


def test_a_scored_cell_without_a_tag_is_refused(tmp_path):
    doc = json.loads(TAGS_FILE.read_text(encoding='utf-8'))
    doc['tags'] = [t for t in doc['tags'] if (t['profile_id'], t['item']) != ('FAKE-3', 'ISO 10993-13')]
    short = tmp_path / 'short_tags.json'
    short.write_text(json.dumps(doc), encoding='utf-8')
    with pytest.raises(TagError, match='no tag'):
        run_cli(tmp_path, lambda s: Flaky(), '--tags', str(short))
    assert not (tmp_path / 'out').exists()
