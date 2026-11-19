"""S10c live orchestration and request accounting with in-test fakes only."""
import builtins
import io
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace as NS

import pytest
import yaml

import _netguard
import standards.tool
from eval.s10 import live as L
from eval.s10 import frozen_candidates as FC
from eval.s10 import formats as F
from eval.s10 import runner as R
from llm import provider
from standards.classifier import LLMResponse
from standards.ruleset import normalised_sha

STAMP = '2026-11-19T00:00:00+00:00'
FAKE_LOCKED = ('fake_module.py',)


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path_factory):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    monkeypatch.setenv('LLM_PROVIDER', 'azure_openai')
    monkeypatch.setenv('REGCLAW_RUN_DIR', 'data/runs/live-fake')
    monkeypatch.setenv('AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS', '64000')
    monkeypatch.setenv('AZURE_OPENAI_S10_TIMEOUT_S', '600')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    _netguard.block_network(monkeypatch, 'live tests forbid network')

    def denied(*args, **kwargs):
        raise AssertionError('real dependencies forbidden')

    monkeypatch.setattr(subprocess, 'run', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)
    monkeypatch.setattr(L, 'load_data', denied)
    monkeypatch.setattr(F, 'load_data', denied)
    monkeypatch.setattr(R, 'run', denied)
    monkeypatch.setattr(R, 'summarise', denied)
    monkeypatch.setattr(R, 'azure_client_factory', denied)
    monkeypatch.setattr(standards.tool, 'save_record', denied)
    repo = Path(__file__).resolve().parents[1]
    temp_root = tmp_path_factory.getbasetemp().resolve()

    def guarded(real):
        def opened(file, mode='r', *args, **kwargs):
            if isinstance(file, (str, os.PathLike)):
                path = Path(file).resolve()
                if any(part.casefold() in L.SEALED_NAMES or part.casefold().startswith('.env') for part in path.parts):
                    raise AssertionError('forbidden input')
                if not path.is_relative_to(temp_root) and any(
                        path.is_relative_to(folder) for folder in (repo / 'data', repo / 'standards/data')):
                    raise AssertionError('real data forbidden')
                if any(c in mode for c in 'wax+') and not path.is_relative_to(temp_root):
                    raise AssertionError('write outside pytest temp base')
            return real(file, mode, *args, **kwargs)
        return opened

    monkeypatch.setattr(builtins, 'open', guarded(builtins.open))
    monkeypatch.setattr(io, 'open', guarded(io.open))
    yield
    assert os.environ['REGCLAW_STUB'] == '1'


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
    return path


def dumps(doc):
    return json.dumps(doc, sort_keys=True, indent=2) + '\n'


def fake_data():
    parts = [('ISO 10993-1', 'Framework'), ('ISO 10993-5', 'Cyto'), ('ISO 10993-9', 'Degradation')]
    return dict(matrix=dict(rows=[dict(nature='surface', site='intact_skin', duration='A',
                                      cells=dict(Cyto=dict(consider=True)))]), anchors=dict(rows=[]),
                part_map=dict(parts=[dict(part=part, endpoints=[ep], edition='FAKE', edition_verified=False)
                                     for part, ep in parts]),
                labels=dict(labels={label: 'FAKE evaluation rationale.' for label in F.LABELS},
                            banned_phrases=[], caveat='FAKE data.'), data_version={'fake': 'in-test'})


def profile(index, split='dev'):
    return dict(id=f'FAKE-{index}', stratum=f'S{index + 1}' if index < 8 else 'trap', split=split,
                contact_category='surface/intact_skin', duration='A', flags=[], profile=dict(
                    device_name=f'FAKE device {index}', components=[dict(name='FAKE component',
                    contact_nature='surface', contact_site='intact_skin', exposure=dict(pattern='single', hours_per_use=2.0),
                    materials=[dict(name='FAKE material', material_class='other')], sterilization='non_sterile')]))


class FakeClient:
    provider, role, model = 'azure_openai', 'S10', 'FAKE-classifier'

    def __init__(self, setting, world, *, error=False, sent=1, finish='stop'):
        self.effort, self.world = setting.effort, world
        self.cap, self.timeout = 64000, 600.0
        self.last_http_log = []
        self.error, self.sent, self.finish = error, sent, finish

    def classify(self, request):
        assert os.environ['REGCLAW_STUB'] == '1'
        self.world.calls.append((self.effort, json.loads(request['messages'][0]['content'])['device_profile']['device_name']))
        self.last_http_log.extend(dict(status='error' if self.error else 'ok') for _ in range(self.sent))
        if self.error:
            raise RuntimeError('PRIVATE fake error')
        provenance = dict(provider=self.provider, reasoning_effort=self.effort, max_completion_tokens=self.cap,
                          finish_reason=self.finish, latency_s=100.0, http_attempts=self.sent,
                          attempts=[dict(max_completion_tokens=self.cap, timeout_s=self.timeout,
                                         finish_reason=self.finish, usage=dict(output_tokens=100, input_tokens=20))])
        return LLMResponse(dict(standards=[], notes=[]), model=self.model, provenance=provenance,
                           finish_reason=self.finish)


def world(tmp_path_factory, monkeypatch, *, split='dev', lock_kind=None, sidecars='normal', candidate_calls=0):
    root = tmp_path_factory.mktemp('liveworld')
    assert 's8' not in str(root).casefold(), "liveworld root contains 's8'; choose a --basetemp without 's8'"
    data = fake_data()
    rows = [profile(i, split) for i in range(10)]
    profiles = {row['id']: row for row in rows}
    p = put(root / 'P.yaml', yaml.safe_dump(dict(format=F.PROFILES_FORMAT, split=split, profiles=rows)))
    g = put(root / 'G.yaml', yaml.safe_dump(dict(format=F.GOLD_FORMAT_V2, split=split,
        reference=dict(method='jury median with boundary routing (S10P-04); 0 routed',
                       merge_sha256_lf='a' * 64, export_sha256_lf='b' * 64),
        labels=[dict(profile_id=pid, item=part, label='mandatory' if part != 'ISO 10993-9' else 'not_applicable')
                for pid in profiles for part in ('ISO 10993-1', 'ISO 10993-5', 'ISO 10993-9')])))
    t = put(root / 'T.json', dumps(dict(format='s10-cell-tags.v1', tags=[
        dict(profile_id=pid, item=part, tag='judgement') for pid in profiles
        for part in ('ISO 10993-1', 'ISO 10993-5', 'ISO 10993-9')])))
    candidates, candidate_manifest = root / 'C.json', root / 'CM.json'

    def retriever_factory(*, cache_file):
        return NS(cypher_cache=NS(path=Path(cache_file)), query_embeddings=NS(cache=NS(
            path=Path(cache_file).with_name(FC.EMBEDDING_CACHE_NAME))), retrieve=lambda *a, **kw: ([], None))

    FC.build_candidates(profiles, cache_dir=root / 'cache', out_path=candidates,
                        manifest_path=candidate_manifest, retriever_factory=retriever_factory, now=STAMP)
    candidate_folder = root / 'data/runs/candidates-fake'
    FC.append_call_ledger(candidate_folder / FC.CALL_LEDGER_NAME, step='candidates', calls=candidate_calls, now=STAMP)
    FC.write_candidate_run_manifest(root=root, run_dir=candidate_folder, candidates_path=candidates,
        manifest_path=candidate_manifest, ledger_start=0, calls=candidate_calls)
    code = put(root / FAKE_LOCKED[0], 'FAKE source for hash validation\n')
    lock = dict(format=L.LOCK_FORMAT, files={FAKE_LOCKED[0]: dict(sha256_lf=normalised_sha(code.read_bytes()))})
    if lock_kind == 'format':
        lock['format'] = 'FAKE invalid'
    elif lock_kind == 'files':
        lock['files'] = {}
    elif lock_kind == 'sha':
        lock['files'][FAKE_LOCKED[0]]['sha256_lf'] = '0' * 64
    if lock_kind != 'missing':
        put(root / L.LOCK_REL, dumps(lock))
    monkeypatch.setattr(provider, 'ROOT', root)
    obj = NS(root=root, data=data, p=p, g=g, t=t, c=candidates, cm=candidate_manifest,
             candidate_run=candidate_folder / FC.CANDIDATE_RUN_NAME,
             pilot_settings=root / 'pilot/pilot_settings.json',
             calls=[], clients=[], summaries=[], saved=[])

    def factory(setting):
        client = FakeClient(setting, obj)
        obj.clients.append(client)
        return client

    def summarize(records, gold, **kwargs):
        obj.summaries.append((records, gold, kwargs))
        return dict(format=R.SUMMARY_FORMAT)

    def save(role, record):
        assert role == 'S10'
        obj.saved.append(record)
        if sidecars == 'none' or sidecars == 'collision' and len(obj.saved) > 1:
            return
        put(provider.run_dir() / 's10' / f'fake-{len(obj.saved):04d}.json', 'FAKE sidecar\n')

    monkeypatch.setattr(standards.tool, 'save_record', save)
    obj.factory, obj.summarize = factory, summarize
    return obj


def invoke(obj, phase='pilot', *, out_name=None, **overrides):
    name = out_name or phase
    args = [phase, '--profiles', str(obj.p), '--candidates', str(obj.c), '--candidates-manifest', str(obj.cm),
            '--candidates-run-manifest', str(obj.candidate_run),
            '--out-dir', str(obj.root / name)]
    if phase == 'main':
        args += ['--gold', str(obj.g), '--tags', str(obj.t), '--pilot-settings', str(obj.pilot_settings)]
    lines = []
    kwargs = dict(root=obj.root, data=obj.data, client_factory=obj.factory, summary_factory=obj.summarize,
                  locked_files=FAKE_LOCKED, lock_checks=(),
                  env={'REGCLAW_STUB': '0', 'LLM_PROVIDER': 'azure_openai'}, now=STAMP, out=lines.append)
    kwargs.update(overrides)
    code = L.main(args, **kwargs)
    return code, lines


def main_run_dir(monkeypatch):
    monkeypatch.setenv('REGCLAW_RUN_DIR', 'data/runs/main-fake')
    return provider.run_dir()


def snapshot_tree(root):
    result = {}
    for path in root.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream:
                result[path.relative_to(root).as_posix()] = stream.read()
        else:
            result[path.relative_to(root).as_posix()] = None
    return result


def test_pilot_is_complete_unscored_and_manifest_matches_runs(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    assert invoke(obj)[0] == 0
    manifest = json.loads((obj.root / 'pilot/run_manifest.json').read_bytes())
    runs_raw = (obj.root / 'pilot/runs.jsonl').read_bytes()
    records = [json.loads(line) for line in runs_raw.splitlines()]
    assert manifest['counts']['records'] == manifest['counts']['sidecars'] == len(records) == 4
    assert manifest['outputs']['runs.jsonl']['sha256_lf'] == normalised_sha(runs_raw)
    assert obj.summaries == [] and not (obj.root / 'pilot/summary.json').exists()
    assert {r['profile_id'] for r in records} == {L.choose_pilot({f'FAKE-{i}': profile(i) for i in range(10)})}
    assert all(r['stratum'] != 'trap' for r in records)
    assert FC.ledger_total(provider.run_dir() / FC.CALL_LEDGER_NAME) == 4
    receipt = (obj.root / 'eval/s10/manifests/s10c_pilot_run.json').read_text(encoding='utf-8')
    assert not any(r['profile_id'] in receipt for r in records)
    assert json.loads(receipt)['shas']['run_manifest_sha256_lf'] == normalised_sha(
        (obj.root / 'pilot/run_manifest.json').read_bytes())


def test_main_owns_runner_order_and_scores_only_when_complete(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    assert invoke(obj)[0] == 0
    pilot_folder = provider.run_dir()
    main_folder = main_run_dir(monkeypatch)
    assert invoke(obj, 'main')[0] == 0
    records = [json.loads(line) for line in (obj.root / 'main/runs.jsonl').read_text(encoding='utf-8').splitlines()]
    expected = [(r, pid, setting.name) for r, pid, setting in R.plan_order(
        [f'FAKE-{i}' for i in range(10)], L.SETTINGS, 5, 0)]
    assert [(r['repeat'], r['profile_id'], r['setting']) for r in records] == expected
    assert len(records) == 100 and len(obj.summaries) == 1 and len(obj.summaries[0][0]) == 100
    assert [c.effort for c in obj.clients] == ['medium', 'high', 'medium', 'high']
    summary = json.loads((obj.root / 'main/summary.json').read_bytes())
    assert summary['note'] == L.SUMMARY_NOTE and summary['label'] == 'provisional (GPT)'
    manifest = json.loads((obj.root / 'main/run_manifest.json').read_bytes())
    assert manifest['counts']['sidecars'] == 100 and manifest['counts']['ledger_total'] == 100
    assert manifest['counts']['call_totals'] == dict(candidates=0, pilot=4, current_ledger=100, total=104)
    assert manifest['outputs']['summary.json']['sha256_lf'] == normalised_sha((obj.root / 'main/summary.json').read_bytes())
    assert main_folder != pilot_folder
    assert len(list((main_folder / 's10').glob('*.json'))) == 100
    assert len(list((pilot_folder / 's10').glob('*.json'))) == 4


def test_verified_step_totals_are_printed_and_not_double_counted(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch, candidate_calls=7)
    pilot_ledger = provider.run_dir() / FC.CALL_LEDGER_NAME
    FC.append_call_ledger(pilot_ledger, step='pilot', calls=3, now=STAMP)
    code, lines = invoke(obj)
    assert code == 0
    assert lines[0] == 'candidates_calls=7 pilot_calls=0 current_ledger_calls=3 total_calls=10'
    pilot = json.loads((obj.root / 'pilot/run_manifest.json').read_bytes())
    assert pilot['counts']['ledger_total'] == 7
    assert pilot['counts']['call_totals']['total'] == 14
    main_ledger = main_run_dir(monkeypatch) / FC.CALL_LEDGER_NAME
    FC.append_call_ledger(main_ledger, step='main', calls=5, now=STAMP)
    code, lines = invoke(obj, 'main')
    assert code == 0
    assert lines[0] == 'candidates_calls=7 pilot_calls=7 current_ledger_calls=5 total_calls=19'
    main = json.loads((obj.root / 'main/run_manifest.json').read_bytes())
    assert main['counts']['ledger_total'] == 105
    assert main['counts']['call_totals'] == dict(candidates=7, pilot=7, current_ledger=105, total=119)
    assert FC.ledger_total(pilot_ledger) == 7 and FC.ledger_total(main_ledger) == 105


@pytest.mark.parametrize('phase,earlier', [('pilot', 'candidates'), ('main', 'candidates'), ('main', 'pilot')])
@pytest.mark.parametrize('failure', ['missing_manifest', 'missing_receipt', 'missing_ledger',
                                   'manifest_sha', 'ledger_sha', 'stopped'])
def test_earlier_manifest_refusals_write_nothing(tmp_path_factory, monkeypatch, phase, earlier, failure):
    obj = world(tmp_path_factory, monkeypatch, candidate_calls=7)
    if phase == 'main':
        assert invoke(obj)[0] == 0
        main_run_dir(monkeypatch)
    if earlier == 'candidates':
        manifest = obj.candidate_run
        receipt = obj.root / FC.CANDIDATE_RECEIPT_REL
        ledger = manifest.parent / FC.CALL_LEDGER_NAME
    else:
        manifest = obj.root / 'pilot/run_manifest.json'
        receipt = obj.root / 'eval/s10/manifests/s10c_pilot_run.json'
        pilot_doc = json.loads(manifest.read_bytes())
        ledger = obj.root / pilot_doc['run_dir'] / FC.CALL_LEDGER_NAME
    if failure == 'stopped':
        put(manifest.parent / 'STOPPED.json', dumps(dict(reason='FAKE stopped', counts={})))
    else:
        target = ledger if failure in ('missing_ledger', 'ledger_sha') else (
            receipt if failure == 'missing_receipt' else manifest)
        read = Path.read_bytes
        def corrupt(path):
            if path == target:
                if failure.startswith('missing'):
                    raise FileNotFoundError('PRIVATE fake missing input')
                return read(path) + b' '
            return read(path)
        monkeypatch.setattr(Path, 'read_bytes', corrupt)
    before, calls, clients = snapshot_tree(obj.root), len(obj.calls), len(obj.clients)
    code, lines = invoke(obj, phase)
    assert code == 2 and 'PRIVATE' not in '\n'.join(lines)
    assert len(obj.calls) == calls and len(obj.clients) == clients
    assert snapshot_tree(obj.root) == before and not (obj.root / phase).exists()


@pytest.mark.parametrize('phase,own_calls', [('pilot', 31), ('main', 27)])
def test_starting_total_over_cap_refuses_before_calls_or_writes(tmp_path_factory, monkeypatch, phase, own_calls):
    obj = world(tmp_path_factory, monkeypatch, candidate_calls=120)
    if phase == 'main':
        assert invoke(obj)[0] == 0
        main_run_dir(monkeypatch)
    FC.append_call_ledger(provider.run_dir() / FC.CALL_LEDGER_NAME, step=phase, calls=own_calls, now=STAMP)
    before, calls = snapshot_tree(obj.root), len(obj.calls)
    code, lines = invoke(obj, phase)
    assert code == 2 and lines[-1] == 'call_cap count=1' and lines[0].endswith('total_calls=151')
    assert len(obj.calls) == calls and snapshot_tree(obj.root) == before
    assert not (obj.root / phase).exists()


@pytest.mark.parametrize('failure', ['ledger', 'stopped'])
def test_changed_earlier_step_stops_before_next_call(tmp_path_factory, monkeypatch, failure):
    obj = world(tmp_path_factory, monkeypatch)
    original = R.run_one
    def changes(*args, **kwargs):
        record = original(*args, **kwargs)
        if failure == 'ledger':
            FC.append_call_ledger(obj.candidate_run.parent / FC.CALL_LEDGER_NAME,
                                  step='candidates', calls=1, now=STAMP)
        else:
            put(obj.candidate_run.parent / 'STOPPED.json', dumps(dict(reason='FAKE stopped', counts={})))
        return record
    monkeypatch.setattr(R, 'run_one', changes)
    code, lines = invoke(obj)
    expected = 'input_changed' if failure == 'ledger' else 'earlier_stopped'
    assert code == 4 and lines[-1] == f'{expected} count=1' and len(obj.calls) == 1
    assert obj.summaries == [] and not (obj.root / 'pilot/run_manifest.json').exists()


@pytest.mark.parametrize('env', [{'REGCLAW_STUB': '1', 'LLM_PROVIDER': 'azure_openai'},
                                 {'REGCLAW_STUB': '0', 'LLM_PROVIDER': 'anthropic'}])
def test_environment_refuses_before_calls_or_writes(tmp_path_factory, monkeypatch, env):
    obj = world(tmp_path_factory, monkeypatch)
    assert invoke(obj, env=env)[0] == 2
    assert obj.calls == [] and not (obj.root / 'pilot').exists()


def test_production_environment_has_no_stub_bypass(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    assert invoke(obj, env=None)[0] == 2
    assert os.environ['REGCLAW_STUB'] == '1' and obj.calls == []


@pytest.mark.parametrize('value', [None, 'data/runs', 'data/runs/azure_openai/s10', 'data/runs/s8-test', '../outside'])
def test_run_dir_refusals(tmp_path_factory, monkeypatch, value):
    obj = world(tmp_path_factory, monkeypatch)
    if value is None:
        monkeypatch.delenv('REGCLAW_RUN_DIR')
    else:
        monkeypatch.setenv('REGCLAW_RUN_DIR', value)
    assert invoke(obj)[0] == 2 and obj.calls == [] and not (obj.root / 'pilot').exists()


@pytest.mark.parametrize('phase', ['pilot', 'main'])
@pytest.mark.parametrize('entry', ['old.json', 'notes.txt', 'nested'])
def test_nonempty_sidecars_refuse_without_writes(tmp_path_factory, monkeypatch, phase, entry):
    obj = world(tmp_path_factory, monkeypatch)
    if phase == 'main':
        assert invoke(obj)[0] == 0
        main_run_dir(monkeypatch)
    existing = provider.run_dir() / 's10' / entry
    if entry == 'nested':
        existing.mkdir(parents=True)
    else:
        put(existing, 'FAKE pre-existing sidecar entry\n')
    def snapshot():
        return {path.relative_to(obj.root).as_posix(): path.read_bytes() if path.is_file() else None
                for path in obj.root.rglob('*')}
    before, calls, clients = snapshot(), len(obj.calls), len(obj.clients)
    code, lines = invoke(obj, phase)
    assert code == 2 and lines == ['sidecar_not_empty count=1']
    assert snapshot() == before and len(obj.calls) == calls and len(obj.clients) == clients
    assert not (obj.root / phase).exists()


@pytest.mark.parametrize('phase', ['pilot', 'main'])
def test_empty_sidecar_folder_is_accepted(tmp_path_factory, monkeypatch, phase):
    obj = world(tmp_path_factory, monkeypatch)
    if phase == 'main':
        assert invoke(obj)[0] == 0
        main_run_dir(monkeypatch)
    (provider.run_dir() / 's10').mkdir(parents=True)
    assert invoke(obj, phase)[0] == 0


def test_main_cannot_reuse_pilot_sidecar_folder(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    assert invoke(obj)[0] == 0
    ledger = provider.run_dir() / FC.CALL_LEDGER_NAME
    before = ledger.read_bytes()
    code, lines = invoke(obj, 'main')
    assert code == 2 and lines == ['sidecar_not_empty count=1']
    assert ledger.read_bytes() == before and len(obj.calls) == 4
    assert not (obj.root / 'main').exists()


@pytest.mark.parametrize('key,value', [
    ('AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS', None),
    ('AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS', '0'),
    ('AZURE_OPENAI_S10_TIMEOUT_S', None),
    ('AZURE_OPENAI_S10_TIMEOUT_S', 'nan'),
    ('AZURE_OPENAI_S10_TIMEOUT_S', 'inf'),
])
def test_missing_or_invalid_request_settings_refuse(tmp_path_factory, monkeypatch, key, value):
    obj = world(tmp_path_factory, monkeypatch)
    if value is None:
        monkeypatch.delenv(key)
    else:
        monkeypatch.setenv(key, value)
    assert invoke(obj)[0] == 2 and obj.calls == [] and not (obj.root / 'pilot').exists()


@pytest.mark.parametrize('attribute,value', [('provider', 'FAKE-other'), ('role', 'S4'),
                                          ('cap', 1), ('timeout', 1), ('last_http_log', None)])
def test_client_safety_refusals(tmp_path_factory, monkeypatch, attribute, value):
    obj = world(tmp_path_factory, monkeypatch)
    def invalid(setting):
        client = FakeClient(setting, obj)
        setattr(client, attribute, value)
        return client
    assert invoke(obj, client_factory=invalid)[0] == 2
    assert obj.calls == [] and not (obj.root / 'pilot').exists()


def test_invalid_ledger_refuses_before_calls(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    put(provider.run_dir() / FC.CALL_LEDGER_NAME, dumps(dict(step='FAKE', calls=True, timestamp=STAMP)))
    assert invoke(obj)[0] == 2 and obj.calls == [] and not (obj.root / 'pilot').exists()


@pytest.mark.parametrize('kind', ['missing', 'format', 'files', 'sha'])
def test_code_lock_refusals(tmp_path_factory, monkeypatch, kind):
    obj = world(tmp_path_factory, monkeypatch, lock_kind=kind)
    assert invoke(obj)[0] == 2 and obj.calls == [] and not (obj.root / 'pilot').exists()


def test_holdout_refusal(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch, split='holdout')
    assert invoke(obj)[0] == 2 and obj.calls == [] and not (obj.root / 'pilot').exists()


def test_bad_candidates_manifest_refusal(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    obj.cm = put(obj.root / 'wrong-CM.json', dumps(dict(format=FC.MANIFEST_FORMAT, file_sha256='0' * 64)))
    assert invoke(obj)[0] == 2 and obj.calls == []


def test_nonempty_folder_and_existing_run_manifest_refuse(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    path = put(obj.root / 'pilot/runs.jsonl', 'FAKE existing\n')
    assert invoke(obj)[0] == 2 and path.read_text(encoding='utf-8') == 'FAKE existing\n' and obj.calls == []


def test_completed_folder_is_write_once(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    assert invoke(obj)[0] == 0
    before = (obj.root / 'pilot/runs.jsonl').read_bytes()
    assert invoke(obj, out_name='new-pilot')[0] == 2
    assert (obj.root / 'pilot/runs.jsonl').read_bytes() == before and len(obj.calls) == 4


def test_runs_exclusive_open_survives_creation_race(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    target, real_open = obj.root / 'pilot/runs.jsonl', Path.open
    raced = []
    def open_once(path, mode='r', *args, **kwargs):
        if path == target and mode == 'x' and not raced:
            raced.append(True)
            with real_open(path, mode, *args, **kwargs) as stream:
                stream.write('FAKE competing writer\n')
        return real_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', open_once)
    assert invoke(obj)[0] == 4 and obj.calls == []
    assert target.read_text(encoding='utf-8') == 'FAKE competing writer\n'
    assert not (obj.root / 'pilot/run_manifest.json').exists()


@pytest.mark.parametrize('boundary,requests,records', [(145, 1, 0), (144, 6, 1)])
def test_call_cap_reserves_six(tmp_path_factory, monkeypatch, boundary, requests, records):
    obj = world(tmp_path_factory, monkeypatch)
    ledger = provider.run_dir() / FC.CALL_LEDGER_NAME
    FC.append_call_ledger(ledger, step='candidates', calls=boundary, now=STAMP)
    code, lines = invoke(obj, client_factory=lambda setting: FakeClient(setting, obj, sent=requests))
    assert code == 4 and lines[-1] == 'call_cap count=1' and len(obj.calls) == records
    stopped = json.loads((obj.root / 'pilot/STOPPED.json').read_bytes())
    assert stopped['counts']['records'] == records
    assert FC.ledger_total(ledger) == boundary + records * requests
    assert obj.summaries == [] and not (obj.root / 'pilot/run_manifest.json').exists()


def test_main_cap_stop_never_calls_summary(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    assert invoke(obj)[0] == 0
    main_run_dir(monkeypatch)
    ledger = provider.run_dir() / FC.CALL_LEDGER_NAME
    FC.append_call_ledger(ledger, step='main', calls=141, now=STAMP)
    code, lines = invoke(obj, 'main')
    assert code == 4 and lines[-1] == 'call_cap count=1'
    assert len(obj.calls) == 4 and obj.summaries == [] and FC.ledger_total(ledger) == 141
    assert (obj.root / 'main/STOPPED.json').exists() and not (obj.root / 'main/summary.json').exists()


def test_reservation_uses_candidate_total_through_global_boundary(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch, candidate_calls=140)
    ledger = provider.run_dir() / FC.CALL_LEDGER_NAME
    FC.append_call_ledger(ledger, step='pilot', calls=4, now=STAMP)
    code, lines = invoke(obj, client_factory=lambda setting: FakeClient(setting, obj, sent=6))
    assert code == 4 and lines[-1] == 'call_cap count=1' and len(obj.calls) == 1
    assert FC.ledger_total(ledger) == 10
    stopped = json.loads((obj.root / 'pilot/STOPPED.json').read_bytes())
    assert stopped['counts']['call_totals']['total'] == 150
    assert not (obj.root / 'pilot/run_manifest.json').exists()


def test_ledger_avoids_double_counting(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    ledger = provider.run_dir() / FC.CALL_LEDGER_NAME
    FC.append_call_ledger(ledger, step='candidates', calls=20, now=STAMP)
    assert invoke(obj)[0] == 0 and FC.ledger_total(ledger) == 24
    rows = [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines()]
    assert [r['calls'] for r in rows] == [20, 1, 1, 1, 1]
    assert all(set(row) == {'step', 'calls', 'timestamp'} for row in rows)


def test_three_consecutive_errors_stop_and_charge(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    code, lines = invoke(obj, client_factory=lambda setting: FakeClient(setting, obj, error=True))
    assert code == 4 and lines[-1] == 'consecutive_errors count=1'
    assert len(obj.calls) == 3 and FC.ledger_total(provider.run_dir() / FC.CALL_LEDGER_NAME) == 3
    assert obj.summaries == [] and not (obj.root / 'pilot/pilot_settings.json').exists()


def test_non_error_resets_consecutive_count(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    original = R.run_one
    sequence = iter([True, True, False, True])
    def scripted(entry, setting, repeat, client, retriever, data):
        client.error = next(sequence)
        return original(entry, setting, repeat, client, retriever, data)
    monkeypatch.setattr(R, 'run_one', scripted)
    code, lines = invoke(obj)
    assert code == 1 and lines[-1] == 'pilot gate failed' and len(obj.calls) == 4
    assert json.loads((obj.root / 'pilot/STOPPED.json').read_bytes())['reason'] == 'pilot_gate_failed'


def test_effort_mismatch_refuses_before_calls(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    def wrong(setting):
        client = FakeClient(setting, obj)
        client.effort = 'high'
        return client
    assert invoke(obj, client_factory=wrong)[0] == 2 and obj.calls == []


@pytest.mark.parametrize('phase', ['pilot', 'main'])
def test_frozen_miss_stops_before_summary(tmp_path_factory, monkeypatch, phase):
    obj = world(tmp_path_factory, monkeypatch)
    if phase == 'main':
        assert invoke(obj)[0] == 0
        main_run_dir(monkeypatch)
    before = len(obj.calls)
    class Miss(FC.FrozenRetriever):
        def retrieve(self, *args, **kwargs):
            self.misses.append(('FAKE', 30))
            raise LookupError('PRIVATE fake miss')
    code, lines = invoke(obj, phase, retriever_factory=Miss)
    assert code == 4 and lines[-1] == 'frozen_miss count=1' and len(obj.calls) == before + 1
    assert obj.summaries == [] and not (obj.root / phase / 'run_manifest.json').exists()
    assert not (obj.root / phase / 'summary.json').exists()


def test_runtime_lock_guard_keeps_partial_unscored(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    count = [0]
    def changes():
        count[0] += 1
        if count[0] >= 3:
            raise L.LiveCheck('fake_lock_changed')
    assert invoke(obj, lock_checks=(changes,))[0] == 4
    assert len(obj.calls) == 1 and obj.summaries == []
    assert not (obj.root / 'pilot/run_manifest.json').exists()


def test_unexpected_exception_after_requests_is_charged(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    def broken(entry, setting, repeat, client, retriever, data):
        client.last_http_log = [dict(status='ok'), dict(status='ok')]
        raise RuntimeError('PRIVATE post-request failure')
    monkeypatch.setattr(R, 'run_one', broken)
    code, lines = invoke(obj)
    assert code == 4 and 'PRIVATE' not in '\n'.join(lines)
    assert FC.ledger_total(provider.run_dir() / FC.CALL_LEDGER_NAME) == 2
    assert json.loads((obj.root / 'pilot/STOPPED.json').read_bytes())['counts']['records'] == 0


@pytest.mark.parametrize('mode', ['none', 'collision'])
def test_sidecar_count_must_equal_records(tmp_path_factory, monkeypatch, mode):
    obj = world(tmp_path_factory, monkeypatch, sidecars=mode)
    code, lines = invoke(obj)
    assert code == 4 and lines[-1] == 'sidecar_count count=1'
    assert not (obj.root / 'pilot/run_manifest.json').exists() and obj.summaries == []


def test_sidecar_names_must_be_unique():
    with pytest.raises(L.LiveCheck, match='sidecar_names'):
        L.validate_sidecars(['same.json', 'same.json'], 2)
    assert L.validate_sidecars(['main.json'], 1) == 1


def test_main_sidecar_failure_never_calls_summary(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    assert invoke(obj)[0] == 0
    main_run_dir(monkeypatch)
    monkeypatch.setattr(standards.tool, 'save_record', lambda *args, **kwargs: None)
    code, lines = invoke(obj, 'main')
    assert code == 4 and lines[-1] == 'sidecar_count count=1'
    assert len(obj.calls) == 104 and obj.summaries == []
    assert not (obj.root / 'main/summary.json').exists()


@pytest.mark.parametrize('value,expected', [(31999, 64000), (32000, 64000), (40000, 80000), (100000, 128000)])
@pytest.mark.parametrize('latency,timeout', [(100, 600), (300, 900)])
def test_pilot_settings_cap_and_timeout(value, expected, latency, timeout):
    records = []
    for arm in ('medium', 'high', 'medium', 'high'):
        records.append(dict(setting=arm, effort_effective=arm, llm_provenance=dict(reasoning_effort=arm,
            max_completion_tokens=64000, finish_reason='stop', latency_s=latency,
            attempts=[dict(max_completion_tokens=64000, timeout_s=600, finish_reason='length',
                           usage=dict(output_tokens=value)),
                      dict(max_completion_tokens=128000, timeout_s=1200, finish_reason='stop',
                           usage=dict(output_tokens=1))])))
    settings = L.pilot_settings(records)
    assert settings['max_output_tokens'] == value and settings['cap'] == expected
    assert settings['max_latency_s'] == latency and settings['timeout_s'] == timeout


def test_pilot_gate_failure_is_unscored(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    code, lines = invoke(obj, client_factory=lambda setting: FakeClient(setting, obj, finish=None))
    assert code == 1 and lines[-1] == 'pilot gate failed'
    assert obj.summaries == [] and not (obj.root / 'pilot/pilot_settings.json').exists()


@pytest.mark.parametrize('key,value', [('AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS', '65000'),
                                     ('AZURE_OPENAI_S10_TIMEOUT_S', '601')])
def test_main_refuses_env_different_from_pilot(tmp_path_factory, monkeypatch, key, value):
    obj = world(tmp_path_factory, monkeypatch)
    assert invoke(obj)[0] == 0
    main_run_dir(monkeypatch)
    monkeypatch.setenv(key, value)
    assert invoke(obj, 'main')[0] == 2 and len(obj.calls) == 4 and not (obj.root / 'main').exists()


def test_main_refuses_profiles_sha_different_from_pilot(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    assert invoke(obj)[0] == 0
    main_run_dir(monkeypatch)
    original = obj.p.read_bytes()
    pilot = json.loads((obj.root / 'pilot/run_manifest.json').read_bytes())
    assert pilot['input_shas'][obj.p.relative_to(obj.root).as_posix()] == normalised_sha(original)
    read = Path.read_bytes
    def changed_profiles(path):
        value = read(path)
        return value + b'\n# FAKE changed profiles input\n' if path == obj.p else value
    monkeypatch.setattr(Path, 'read_bytes', changed_profiles)
    before, calls, clients = snapshot_tree(obj.root), len(obj.calls), len(obj.clients)
    code, lines = invoke(obj, 'main')
    assert code == 2 and lines == ['pilot_profiles_sha count=1']
    assert len(obj.calls) == calls and len(obj.clients) == clients
    assert snapshot_tree(obj.root) == before and not (obj.root / 'main').exists()


def test_main_rejects_stopped_pilot(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory, monkeypatch)
    assert invoke(obj)[0] == 0
    main_run_dir(monkeypatch)
    put(obj.root / 'pilot/STOPPED.json', dumps(dict(reason='FAKE', counts={})))
    assert invoke(obj, 'main')[0] == 2 and len(obj.calls) == 4


def test_pilot_seed_source_and_deterministic_selection():
    profiles = {f'FAKE-{i}': profile(i) for i in range(10)}
    import random
    assert L.PILOT_SEED == 'S10c-v1|pilot'
    assert L.choose_pilot(profiles) == random.Random('S10c-v1|pilot').choice(sorted(f'FAKE-{i}' for i in range(8)))


def candidate_case(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp('livecache')
    assert 's8' not in str(root).casefold(), "livecache root contains 's8'; choose a --basetemp without 's8'"
    monkeypatch.setattr(provider, 'ROOT', root)
    p = put(root / 'P.yaml', yaml.safe_dump(dict(format=F.PROFILES_FORMAT, split='dev', profiles=[profile(0)])))
    args = ['build', '--profiles', str(p), '--cache-dir', str(root / 'cache'),
            '--out', str(root / 'C.json'), '--manifest', str(root / 'M.json')]
    return root, args, root / 'data/runs/live-fake' / FC.CALL_LEDGER_NAME


class CounterRetriever:
    def __init__(self, *, cache_file, cypher=1, embeddings=5, fail=False, counter_only=False):
        self.cypher_cache = NS(path=Path(cache_file))
        self.query_embeddings = NS(cache=NS(path=Path(cache_file).with_name(FC.EMBEDDING_CACHE_NAME)),
                                   embedder=NS(api_requests=0, attempts=5))
        self.max_retries, self.cypher, self.embeddings, self.fail = 2, cypher, embeddings, fail
        self.closed = False
        self.llm = NS(api_requests=0, generate=self.generate) if counter_only else NS(last_http_log=[], generate=self.generate)

    def generate(self, request):
        if hasattr(self.llm, 'last_http_log'):
            self.llm.last_http_log.extend(dict(status='ok') for _ in range(self.cypher))
        else:
            self.llm.api_requests += self.cypher
        return dict(provenance=dict(http_attempts=self.cypher))

    def retrieve(self, query, top_k):
        if self.cypher:
            self.llm.generate({})
        self.query_embeddings.embedder.api_requests += self.embeddings
        if self.fail:
            raise RuntimeError('PRIVATE embedding failure')
        return [], None

    def close(self):
        self.closed = True


@pytest.mark.parametrize('counter_only', [False, True])
def test_retrieval_charges_requests_not_rows(tmp_path_factory, monkeypatch, capsys, counter_only):
    root, args, ledger = candidate_case(tmp_path_factory, monkeypatch)
    made = []
    def factory(**kwargs):
        made.append(CounterRetriever(**kwargs, counter_only=counter_only))
        return made[-1]
    assert FC.main(args, data=fake_data(), retriever_factory=factory, now=STAMP) == 0
    assert FC.ledger_total(ledger) == 6 and made[0].closed
    text = capsys.readouterr().out
    assert 'cypher_rows=0 embedding_rows=0' in text
    assert 'cypher_requests=1 embedding_requests=5 retrieval_calls=6' in text
    raw = (ledger.parent / FC.CANDIDATE_RUN_NAME).read_bytes()
    manifest = json.loads(raw)
    receipt = json.loads((root / FC.CANDIDATE_RECEIPT_REL).read_bytes())
    assert manifest['counts']['ledger_total'] == 6
    assert receipt['shas']['run_manifest_sha256_lf'] == normalised_sha(raw)
    assert manifest['ledger_sha256_lf'] == receipt['shas']['ledger_sha256_lf'] == normalised_sha(ledger.read_bytes())


def test_failed_embedding_requests_charged_without_cache_rows(tmp_path_factory, monkeypatch, capsys):
    root, args, ledger = candidate_case(tmp_path_factory, monkeypatch)
    assert FC.main(args, data=fake_data(), retriever_factory=lambda **kw: CounterRetriever(
        **kw, cypher=0, embeddings=5, fail=True), now=STAMP) == 2
    assert FC.ledger_total(ledger) == 5
    assert 'embedding_rows=0' in capsys.readouterr().out
    assert not (ledger.parent / FC.CANDIDATE_RUN_NAME).exists()
    assert not (root / FC.CANDIDATE_RECEIPT_REL).exists()


def test_candidate_completion_receipts_are_write_once(tmp_path_factory, monkeypatch):
    root, args, ledger = candidate_case(tmp_path_factory, monkeypatch)
    assert FC.main(args, data=fake_data(), retriever_factory=CounterRetriever, now=STAMP) == 0
    before = snapshot_tree(root)
    args[args.index('--out') + 1] = str(root / 'second-C.json')
    args[args.index('--manifest') + 1] = str(root / 'second-M.json')
    def forbidden_factory(**kwargs):
        pytest.fail('existing completion must refuse before constructing a retriever')
    assert FC.main(args, data=fake_data(), retriever_factory=forbidden_factory, now=STAMP) == 3
    assert snapshot_tree(root) == before and FC.ledger_total(ledger) == 6


def test_retrieval_cap_refuses_before_requests(tmp_path_factory, monkeypatch):
    root, args, ledger = candidate_case(tmp_path_factory, monkeypatch)
    FC.append_call_ledger(ledger, step='pilot', calls=145, now=STAMP)
    made = []
    def factory(**kwargs):
        made.append(CounterRetriever(**kwargs))
        return made[-1]
    assert FC.main(args, data=fake_data(), retriever_factory=factory, now=STAMP) == 2
    assert made[0].query_embeddings.embedder.api_requests == 0 and FC.ledger_total(ledger) == 145
    assert not (root / 'C.json').exists()
