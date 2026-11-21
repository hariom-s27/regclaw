"""Registered analysis on hand-made fakes; never load real data or clients."""
import builtins
import copy
from fractions import Fraction
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest
import yaml

import _netguard
from eval.s10 import analysis as A
from standards.ruleset import normalised_sha

NA, REC, MAND = A.M.LABELS
STAMP = '2026-11-20T00:00:00+00:00'
LOCKED = ('fake_module.py',)
MISSING_PPI = (None, 'not run: ppi-python not installed')


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path_factory):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    _netguard.block_network(monkeypatch, 'analysis tests forbid network')
    def denied(*args, **kwargs):
        raise AssertionError('real dependencies forbidden')
    monkeypatch.setattr(subprocess, 'run', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)
    for name in ('run', 'run_one', 'summarise', 'score_setting', 'azure_client_factory'):
        monkeypatch.setattr(A.R, name, denied)
    monkeypatch.setattr(A.Q, 'load_report_inputs', denied)
    monkeypatch.setattr(A.F, 'load_data', denied)
    repo, temp = Path(__file__).resolve().parents[1], tmp_path_factory.getbasetemp().resolve()
    monkeypatch.setattr(sys, 'dont_write_bytecode', True)
    monkeypatch.setenv('NUMBA_CACHE_DIR', str(temp / 'analysis-numba-cache'))
    def guarded(real):
        def opened(file, mode='r', *args, **kwargs):
            if isinstance(file, (str, os.PathLike)):
                path = Path(file).resolve()
                if any(p.casefold() in A.L.SEALED_NAMES or p.casefold().startswith('.env') for p in path.parts):
                    raise AssertionError('forbidden input')
                if not path.is_relative_to(temp) and any(path.is_relative_to(folder) for folder in
                                                       (repo / 'data', repo / 'standards/data')):
                    raise AssertionError('real data forbidden')
                if any(c in mode for c in 'wax+') and not path.is_relative_to(temp):
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


def dumps(value):
    return json.dumps(value, indent=2) + '\n'


def dev_cell(pid, item, votes, *, human=None, friend=None, stratum='S1'):
    return dict(profile_id=pid, item=item, stratum=stratum, final=sorted(votes, key=A.M.RANK.get)[1],
                routed=False, split_cell=len(set(votes)) > 1, labels=dict(zip('ABC', votes)), human=human, friend=friend)


def fake_cells():
    rows = []
    for i in range(10):
        pid, stratum = f'FAKE-{i}', f'S{i + 1}' if i < 8 else 'trap'
        rows.extend([dev_cell(pid, 'FAKE-m', (MAND,) * 3, human=MAND, stratum=stratum),
                     dev_cell(pid, 'FAKE-n', (NA,) * 3, human=NA, stratum=stratum),
                     dev_cell(pid, 'FAKE-s', (NA, REC, NA), human=NA, stratum=stratum)])
    return rows


def fake_records(cells):
    roster = {c['profile_id']: c['stratum'] for c in cells}
    records = []
    for repeat, pid, setting in A.R.plan_order(roster, A.L.SETTINGS, 5, 0):
        final = {'FAKE-m': MAND, 'FAKE-n': NA, 'FAKE-s': NA}
        if pid == 'FAKE-0' and repeat == 1:
            final['FAKE-m'] = REC if setting.name == 'medium' else MAND
            final['FAKE-n'] = REC if setting.name == 'high' else NA
        records.append(dict(profile_id=pid, stratum=roster[pid], split='dev', setting=setting.name,
            effort_effective=setting.effort, repeat=repeat, llm_detail='ok', http_attempts=1,
            final=final, rule={'FAKE-m': REC, 'FAKE-n': NA, 'FAKE-s': NA}, llm=dict(final),
            llm_provenance=dict(latency_s=2.0, attempts=[dict(usage=dict(input_tokens=2, output_tokens=3,
                reasoning_tokens=1, cache_read_input_tokens=1), latency_s=2.0)])))
    return records


def world(tmp_path_factory, *, omit=(), corrupt=None, mutate=None, manifest_change=None, global_transform=None):
    root = tmp_path_factory.mktemp('analysisworld')
    assert 's8' not in str(root).casefold(), "analysisworld contains 's8'; choose a --basetemp without 's8'"
    cells = fake_cells()
    records = fake_records(cells)
    if mutate:
        mutate(cells, records)
    gold = dict(format=A.F.GOLD_FORMAT_V2, split='dev', reference=dict(
        method=A.E.REFERENCE_METHOD, merge_sha256_lf='a' * 64, export_sha256_lf=normalised_sha(dumps(cells).encode())),
        labels=[dict(profile_id=c['profile_id'], item=c['item'], label=c['final']) for c in cells])
    tags = dict(format='s10-cell-tags.v1', tags=[dict(profile_id=c['profile_id'], item=c['item'],
                tag={'FAKE-m': 'always_rule', 'FAKE-n': 'matrix_settled', 'FAKE-s': 'judgement'}[c['item']]) for c in cells])
    export_counts = dict(dev_profiles=10, matched_entries=10, unmatched_entries=20, dev_cells=len(cells))
    export_summary = dict(format=A.E.SUMMARY_FORMAT, counts=export_counts, input_shas={A.E.D.MERGE_REL: 'a' * 64},
        checkpoint=dict(e1_match=True, e2_match=True),
        audit_a_gaps={j: dict(under_call=dict(x=0, n=10), over_call=dict(x=0, n=10)) for j in 'ABC'},
        e1_input=dict(weighted_binary_rate_on_final_mandatory=0.0149123456789, population=1000, undefined=False),
        e2_pool=dict(x=0, n=1))
    summary = dict(format=A.R.SUMMARY_FORMAT, label='provisional (GPT)', note=A.L.SUMMARY_NOTE,
                   provenance=dict(split='dev', repeats=5, seed=0), settings={arm: {} for arm in A.ARMS})
    paths = dict(gold=root / A.E.GOLD_REL, cells=root / A.E.CELLS_REL, tags=root / 'tags.json',
        export_summary=root / A.E.SUMMARY_REL, export_manifest=root / A.E.MANIFEST_REL,
        manifest=root / 'main/run_manifest.json', receipt=root / 'eval/s10/manifests/s10c_main_run.json',
        runs=root / 'main/runs.jsonl', summary=root / 'main/summary.json', lock=root / A.L.LOCK_REL,
        ledger=root / 'data/runs/main-fake' / A.FC.CALL_LEDGER_NAME,
        global_ledger=root / A.FC.GLOBAL_LEDGER_REL)
    code = put(root / LOCKED[0], 'FAKE locked source\n')
    lock = dict(format=A.L.LOCK_FORMAT, files={LOCKED[0]: dict(sha256_lf=normalised_sha(code.read_bytes()))})
    text = dict(gold=yaml.safe_dump(gold), cells=dumps(cells), tags=dumps(tags), export_summary=dumps(export_summary),
                runs=''.join(json.dumps(r) + '\n' for r in records), summary=dumps(summary), lock=dumps(lock),
                ledger=''.join(json.dumps(dict(format=A.FC.LEDGER_FORMAT, seq=2 * i + j + 1, ts=STAMP,
                    step='main', kind=kind, unit=f'fake-run-{i}', amount=amount)) + '\n'
                    for i, record in enumerate(records) for j, (kind, amount) in enumerate((
                        ('reserve', A.L.RUN_RESERVE), ('settle', record['http_attempts'] - A.L.RUN_RESERVE)))))
    text['global_ledger'] = global_transform(text['ledger']) if global_transform else text['ledger']
    sha = {name: normalised_sha(value.encode()) for name, value in text.items()}
    export_manifest = dict(format=A.E.MANIFEST_FORMAT, input_shas=export_summary['input_shas'], counts=export_counts,
        outputs={rel: dict(sha256_lf=sha[name]) for name, rel in
                 (('gold', A.E.GOLD_REL), ('cells', A.E.CELLS_REL), ('export_summary', A.E.SUMMARY_REL))})
    http = sum(r['http_attempts'] for r in records)
    counts = dict(records=100, planned_records=100, sidecars=100, http_attempts=http, ledger_start=0, ledger_total=http,
                  call_totals=dict(candidates=0, pilot=0, current_ledger=http, total=http))
    manifest = dict(format=A.L.RUN_FORMAT, phase='main', label='provisional (GPT)', counts=counts,
        global_start=0, global_end=http, global_ledger_last_seq=2 * len(records),
        global_ledger_prefix_sha256_lf=sha['ledger'],
        run_dir='data/runs/main-fake', ledger_sha256_lf=sha['ledger'], repeats=5, seed=0,
        settings=[dict(name=arm, effort=arm, max_completion_tokens=64000, timeout_s=600) for arm in A.ARMS],
        outputs={name: dict(sha256_lf=sha[key]) for name, key in (('runs.jsonl', 'runs'), ('summary.json', 'summary'))},
        input_shas={A.L.LOCK_REL: sha['lock'], A.E.GOLD_REL: sha['gold'], 'tags.json': sha['tags']})
    if corrupt == 'phase':
        manifest['phase'] = 'pilot'
    if corrupt == 'run_lock':
        manifest['input_shas'][A.L.LOCK_REL] = '0' * 64
    if corrupt == 'runs_sha':
        manifest['outputs']['runs.jsonl']['sha256_lf'] = '0' * 64
    if corrupt == 'summary_sha':
        manifest['outputs']['summary.json']['sha256_lf'] = '0' * 64
    if corrupt == 'export_sha':
        export_manifest['outputs'][A.E.CELLS_REL]['sha256_lf'] = '0' * 64
    if corrupt == 'gold_export_sha':
        gold['reference']['export_sha256_lf'] = '0' * 64
        text['gold'] = yaml.safe_dump(gold)
    if corrupt == 'split_flag':
        cells[0]['split_cell'] = True
        text['cells'] = dumps(cells)
    text['export_manifest'] = dumps(export_manifest)
    manifest['export_manifest_sha256_lf'] = normalised_sha(text['export_manifest'].encode())
    if manifest_change:
        manifest_change(manifest)
    text['manifest'] = dumps(manifest)
    receipt = dict(format=A.L.RECEIPT_FORMAT, counts=counts, shas=dict(
        run_manifest_sha256_lf=normalised_sha(text['manifest'].encode()),
        runs_sha256_lf=sha['runs'], ledger_sha256_lf=sha['ledger']))
    if corrupt == 'manifest_sha':
        receipt['shas']['run_manifest_sha256_lf'] = '0' * 64
    if corrupt == 'ledger_sha':
        receipt['shas']['ledger_sha256_lf'] = '0' * 64
    text['receipt'] = dumps(receipt)
    for name, value in text.items():
        if name not in omit:
            put(paths[name], value)
    if corrupt == 'stopped':
        put(root / 'main/STOPPED.json', '{"reason":"FAKE"}\n')
    if corrupt == 'ledger_stopped':
        put(paths['ledger'].parent / 'STOPPED.json', '{"reason":"FAKE"}\n')
    args = ['--run-dir', str(root / 'main'), '--gold', str(paths['gold']), '--dev-cells', str(paths['cells']),
            '--tags', str(paths['tags']), '--export-summary', str(paths['export_summary']), '--out', str(root / 'A.json')]
    return NS(root=root, paths=paths, args=args, records=records, cells=cells, gold=gold, export_summary=export_summary)


def invoke(obj, **kwargs):
    lines = []
    code = A.main(obj.args, root=obj.root, locked_files=LOCKED, n_boot=8, ppi=MISSING_PPI, out=lines.append, **kwargs)
    return code, lines


def inputs(obj):
    args = NS(run_dir=obj.root / 'main', gold=obj.paths['gold'], dev_cells=obj.paths['cells'], tags=obj.paths['tags'],
              export_summary=obj.paths['export_summary'], out=obj.root / 'A.json')
    return A.load_inputs(args, root=obj.root, locked_files=LOCKED)


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_complete_main_cli_allowlist_and_write_once(tmp_path_factory):
    obj = world(tmp_path_factory)
    before = snapshot(obj.root)
    code, lines = invoke(obj)
    assert code == 0
    result = json.loads((obj.root / 'A.json').read_bytes())
    assert result['format'] == A.FORMAT and result['structural_note'] == A.STRUCTURAL_NOTE
    assert lines[0].startswith('llm_detail ')
    assert not any('FAKE-' in line or 'profile_id' in line for line in lines)
    assert all('rules_rate=' in line and 'hybrid_rate=' in line for line in lines if 'metric=' in line)
    assert result['primary']['medium']['under_call_rate'] == pytest.approx(Fraction(1, 40))
    assert result['primary']['high']['under_call_rate'] == 0
    decision = result['effort_decision']
    assert decision['medium_under_call_rate_fraction'] == '1/40'
    assert decision['high_under_call_rate_fraction'] == '0/1'
    assert decision['d_pooled_medium_minus_high_fraction'] == '1/40'
    assert decision['threshold_fraction'] == '1/20'
    assert result['paired_comparison']['per_profile_high_minus_medium_fraction']['FAKE-0'] == '-1/5'
    assert result['paired_comparison']['sign_flip']['p_fraction'] == '1/1'
    assert result['strata_split_gold_mandatory_cells'] == 0 and not result['primary_two_way_required']
    assert A.BAND_ZERO in lines
    assert result['robustness_band']['versions']['jury_median']['note'] == 'identical to main by construction (0 routed)'
    assert result['ppi']['medium']['status'] == MISSING_PPI[1]
    split = result['split_sensitivity']['medium']
    assert split['lower']['all_runs']['by_tag']['judgement']['exact_accuracy'] == 1
    assert split['higher']['all_runs']['by_tag']['judgement']['exact_accuracy'] == 0
    assert set(snapshot(obj.root)) == set(before) | {'A.json'}
    after = snapshot(obj.root)
    assert invoke(obj) == (3, ['output_exists count=1']) and snapshot(obj.root) == after


@pytest.mark.parametrize('name', ['manifest', 'receipt', 'runs', 'summary', 'lock', 'ledger', 'export_manifest'])
def test_missing_completion_inputs_refuse_without_writes(tmp_path_factory, name):
    obj = world(tmp_path_factory, omit={name})
    before = snapshot(obj.root)
    assert invoke(obj)[0] == 2 and snapshot(obj.root) == before


@pytest.mark.parametrize('kind', ['phase', 'manifest_sha', 'runs_sha', 'summary_sha', 'run_lock', 'ledger_sha',
                                 'export_sha', 'gold_export_sha', 'split_flag', 'stopped', 'ledger_stopped'])
def test_corrupt_completion_inputs_refuse_without_writes(tmp_path_factory, kind):
    obj = world(tmp_path_factory, corrupt=kind)
    before = snapshot(obj.root)
    assert invoke(obj)[0] == 2 and snapshot(obj.root) == before


@pytest.mark.parametrize('kind', ['missing', 'duplicate', 'holdout', 'effort', 'rule_floor'])
def test_invalid_record_grid_refuses(tmp_path_factory, kind):
    def change(cells, records):
        if kind == 'missing':
            records.pop()
        elif kind == 'duplicate':
            records[-1] = copy.deepcopy(records[0])
        elif kind == 'holdout':
            records[0]['split'] = 'holdout'
        elif kind == 'rule_floor':
            records[0]['final']['FAKE-m'] = NA
        else:
            records[0]['effort_effective'] = 'wrong'
    obj = world(tmp_path_factory, mutate=change)
    before = snapshot(obj.root)
    assert invoke(obj)[0] == 2 and snapshot(obj.root) == before


def worked_cells():
    def one(item, gold, final, rule):
        return dict(profile_id='FAKE-worked', item=item, gold=gold, final=final, rule=rule, llm=final,
                    repeat=1, setting='medium')
    return [one('a', MAND, REC, REC), one('b', MAND, MAND, REC), one('c', NA, MAND, NA)]


def test_primary_reduction_sign_and_interval_orientation(monkeypatch):
    value = A.primary(worked_cells(), n_boot=8)
    assert value['under_call_rate'] == Fraction(1, 2) and value['rules_under_call_rate'] == 1
    assert value['under_call_reduction']['point'] == Fraction(1, 2)
    assert value['under_call_reduction']['direction'] == 'rules - hybrid'
    assert value['profiles_with_under_call']['count'] == value['profiles_with_under_call']['n'] == 1
    monkeypatch.setattr(A.M, 'lift', lambda *a, **k: dict(hybrid=.2, rules=.6, lift=-.4, lo=-.6, hi=-.1))
    oriented = A.directed_lift([], A.M.under_call_rate, reduction=True)
    assert (oriented['point'], oriented['lo'], oriented['hi']) == (.4, .1, .6)


def test_secondary_metrics_and_tags_hand_fractions():
    cells = worked_cells()
    block = A.secondary_cells(cells, cells, n_boot=8)
    # Confusion: NA->M once, M->R once, M->M once.
    assert block['over_call_rate'] == 1 and block['exact_accuracy'] == pytest.approx(Fraction(1, 3))
    assert block['macro_f1'] == pytest.approx(Fraction(1, 6))
    assert block['qw_kappa'] == pytest.approx(Fraction(-4, 11))
    assert block['binary_kappa'] == pytest.approx(Fraction(-1, 2))
    assert block['over_call_increase']['point'] == 1 and block['over_call_increase']['direction'] == 'hybrid - rules'
    # Upgrades b,c; warranted a,b; warranted upgrade b: precision=1/2, recall=1/2.
    assert block['upgrade_pr']['precision'] == Fraction(1, 2) and block['upgrade_pr']['recall'] == Fraction(1, 2)
    assert block['disagreement_table'] == dict(rules_right=1, llm_right=1, both_wrong=0, n=2)
    assert math.isnan(block['stability']['cell_flip_rate'])
    record = dict(profile_id='FAKE-worked', setting='medium', repeat=1, llm_detail='ok', http_attempts=1,
                  final={c['item']: c['final'] for c in cells}, rule={c['item']: c['rule'] for c in cells},
                  llm={c['item']: c['llm'] for c in cells}, llm_provenance={})
    gold = {(c['profile_id'], c['item']): dict(label=c['gold']) for c in cells}
    tags = {('FAKE-worked', item): tag for item, tag in zip('abc', ('judgement', 'always_rule', 'matrix_settled'))}
    scoped = A.score_scope([record], gold, tags, n_boot=8)
    assert scoped['by_tag']['judgement']['under_call_rate'] == 1
    assert scoped['by_tag']['always_rule']['under_call_rate'] == 0
    assert scoped['by_tag']['matrix_settled']['over_call_rate'] == 1


def effort_fixture(medium, high):
    roster = [f'FAKE-{i}' for i in range(10)]
    gold = {(pid, f'i{item}'): dict(label=MAND) for pid in roster for item in range(4)}
    records = []
    for arm, failures in (('medium', medium), ('high', high)):
        for i, pid in enumerate(roster):
            for repeat in range(1, 6):
                final = {f'i{item}': REC if i < 8 and (repeat - 1) * 4 + item < failures[i] else MAND for item in range(4)}
                records.append(dict(profile_id=pid, setting=arm, repeat=repeat, final=final,
                                    rule=dict.fromkeys(final, REC), llm=final, llm_detail='ok'))
    return records, gold, set(roster[:8])


def test_effort_exact_point_05_boundary_and_six_profiles():
    records, gold, strata = effort_fixture([2, 2, 2, 2, 1, 1, 0, 0], [0, 0, 0, 0, 0, 0, 1, 1])
    value = A.effort_decision(records, gold, strata)
    assert value['medium_under_call_rate'] == Fraction(10, 160)
    assert value['high_under_call_rate'] == Fraction(2, 160)
    assert value['d_pooled_medium_minus_high'] == value['threshold'] == Fraction(1, 20)
    assert value['high_le_medium_profiles'] == 6 and value['rule_fired'] == 1 and value['choice'] == 'HIGH'
    assert isinstance(value['d_pooled_medium_minus_high'], Fraction)
    serialized = json.loads(json.dumps(A.clean(value)))
    assert serialized['medium_under_call_rate'] == .0625 and serialized['medium_under_call_rate_fraction'] == '1/16'
    assert serialized['high_under_call_rate'] == .0125 and serialized['high_under_call_rate_fraction'] == '1/80'
    assert serialized['d_pooled_medium_minus_high'] == .05 and serialized['d_pooled_medium_minus_high_fraction'] == '1/20'
    assert serialized['threshold'] == .05 and serialized['threshold_fraction'] == '1/20'
    assert serialized['per_profile_high_minus_medium']['FAKE-0'] == -.1
    assert serialized['per_profile_high_minus_medium_fraction']['FAKE-0'] == '-1/10'


def test_effort_ties_failure_branch_and_default():
    records, gold, strata = effort_fixture([0] * 8, [0] * 8)
    value = A.effort_decision(records, gold, strata)
    assert value['high_le_medium_profiles'] == 8 and value['rule_fired'] == 3 and value['choice'] == 'MEDIUM'
    # All three failures are trap runs: rule 2 must use the full fifty runs.
    for row in [r for r in records if r['profile_id'] == 'FAKE-8' and r['setting'] == 'medium'][:3]:
        row['llm_detail'] = 'error'
    value = A.effort_decision(records, gold, strata)
    assert value['rule_fired'] == 2 and value['choice'] == 'HIGH' and value['non_ok_medium_minus_high'] == 3


def test_exact_sign_flip_known_case_and_all_zero():
    # Only all-positive and all-negative patterns attain |mean|=1: 2/256.
    value = A.sign_flip([Fraction(1)] * 8)
    assert value['p'] == Fraction(1, 128) and value['patterns'] == 256
    assert isinstance(value['p'], Fraction)
    serialized = json.loads(json.dumps(A.clean(value)))
    assert serialized['p'] == float(Fraction(1, 128)) and serialized['p_fraction'] == '1/128'
    zero = A.sign_flip([Fraction(0)] * 8)
    assert zero['p'] == Fraction(1) and zero['p_fraction'] == '1/1'
    assert A.sign_flip([None] * 8)['p_fraction'] is None


def test_paired_comparison_keeps_whole_profile_arm_pairs():
    records, gold, strata = effort_fixture([1] * 8, [0] * 8)
    decision = A.effort_decision(records, gold, strata)
    value = A.paired_comparison(records, gold, strata, decision, n_boot=16)
    assert value['ci']['point'] == pytest.approx(Fraction(-1, 20))
    assert value['ci']['lo'] == value['ci']['hi'] == value['ci']['point']
    assert value['ci']['n_profiles'] == 8 and value['ci']['seed'] == 0
    assert value['ci']['ci_label'] == 'approximate (few profiles)'


def test_all_six_versions_ignore_audit_in_lojo_and_second_rater_in_max():
    cells = [dev_cell('FAKE-a', 'a', (NA, REC, MAND), human=NA),
             dev_cell('FAKE-a', 'b', (NA,) * 3, human=MAND),
             dev_cell('FAKE-a', 'c', (NA,) * 3, friend=MAND)]
    versions, two_way = A.reference_versions(cells)
    assert set(versions) == {'main', 'jury_median', 'LOJO-A', 'LOJO-B', 'LOJO-C', 'conservative_max'}
    key = ('FAKE-a', 'a')
    assert versions['main'][key]['label'] == versions['jury_median'][key]['label'] == REC
    assert versions['LOJO-A'][key]['label'] == MAND
    assert versions['LOJO-B'][key]['label'] == MAND
    assert versions['LOJO-C'][key]['label'] == REC  # Human NA exists, but is not a referee label.
    assert versions['conservative_max'][('FAKE-a', 'b')]['label'] == MAND
    assert versions['conservative_max'][('FAKE-a', 'c')]['label'] == NA
    assert two_way['lower'][key]['label'] == NA and two_way['higher'][key]['label'] == MAND


def test_two_way_adapter_matches_locked_function_on_fakes():
    cells = [dev_cell('FAKE-a', 'split', (NA, REC, MAND)), dev_cell('FAKE-a', 'same', (REC,) * 3)]
    rows = [dict(cell_id='FAKE-j|' + c['item'], j_id='FAKE-j', item=c['item'], final=c['final'],
                 labels=c['labels'], confidences=dict.fromkeys('ABC', 'high'), routed=False) for c in cells]
    assert A.Q.two_way(rows) == [('FAKE-j|split', NA, MAND)]
    _, two_way = A.reference_versions(cells)
    assert two_way['lower'][('FAKE-a', 'split')]['label'] == NA
    assert two_way['higher'][('FAKE-a', 'split')]['label'] == MAND
    assert two_way['lower'][('FAKE-a', 'same')]['label'] == two_way['higher'][('FAKE-a', 'same')]['label'] == REC


def test_primary_two_way_flag_without_refusal(tmp_path_factory):
    def change(cells, records):
        cells[0]['labels'] = dict(A=REC, B=MAND, C=MAND)
        cells[0]['split_cell'] = True
    obj = world(tmp_path_factory, mutate=change)
    assert invoke(obj)[0] == 0
    result = json.loads((obj.root / 'A.json').read_bytes())
    assert result['strata_split_gold_mandatory_cells'] == 1 and result['primary_two_way_required']
    lower, higher = (result['primary_two_way']['medium'][name]['under_call_rate'] for name in ('lower', 'higher'))
    assert lower == 0 and higher == pytest.approx(Fraction(1, 40))


def test_robustness_decision_change_is_reported_never_used_to_switch(tmp_path_factory):
    def change(cells, records):
        for cell in cells:
            if cell['stratum'] != 'trap' and cell['item'] == 'FAKE-s':
                cell['labels'] = dict(A=NA, B=MAND, C=NA)
        for record in records:
            if record['profile_id'] == 'FAKE-1' and record['setting'] == 'medium' and record['repeat'] == 1:
                record['final']['FAKE-m'] = record['llm']['FAKE-m'] = REC
    obj = world(tmp_path_factory, mutate=change)
    assert invoke(obj)[0] == 0
    value = json.loads((obj.root / 'A.json').read_bytes())
    assert value['effort_decision']['choice'] == 'HIGH' and value['effort_decision']['rule_fired'] == 1
    assert value['effort_decision']['d_pooled_medium_minus_high'] == .05
    alternative = value['robustness_band']['versions']['LOJO-A']
    assert alternative['effort_decision']['choice'] == 'MEDIUM' and alternative['decision_would_change']
    assert value['robustness_band']['versions']['LOJO-B']['effort_decision']['choice'] == 'HIGH'
    assert 'note' not in value['robustness_band']


def test_family_overlap_fraction_and_zero_denominator():
    cells = [dev_cell('FAKE-a', name, (NA, MAND, NA), human=NA) for name in ('a', 'b')]
    records = [dict(profile_id='FAKE-a', final=dict(a=MAND, b=NA))]
    value = A.family_overlap(records, cells)
    assert value['B']['x'] == 1 and value['B']['n'] == 2 and value['B']['share'] == Fraction(1, 2)
    assert value['A']['x'] == value['A']['n'] == 0 and value['A']['share'] is None and value['A']['undefined']


def test_tipping_full_precision_same_strata_base_and_leave_out():
    cells = [dict(profile_id=f'FAKE-{i}', item='m', gold=MAND, final=REC if i == 0 else MAND)
             for i in range(8) for _ in range(5)]
    gold = {(f'FAKE-{i}', 'm'): dict(label=MAND) for i in range(8)}
    summary = dict(e1_input=dict(weighted_binary_rate_on_final_mandatory=.12500000000000003), e2_pool=dict(x=0, n=1))
    value = A.tipping_ranges(cells, gold, summary)
    assert (value['U'], value['P'], value['mandatory_cells'], value['k']) == (5, 40, 8, 5)
    assert value['ranges']['E0']['lower'] == value['ranges']['E0']['upper'] == Fraction(1, 8)
    assert value['ranges']['E1']['E_cells'] == 2  # Full precision: ceil(8*r)=2; rounded .125 would give 1.
    assert value['ranges']['E1']['lower'] == 0 and value['ranges']['E1']['upper'] == pytest.approx(Fraction(3, 10))
    assert value['ranges']['E2']['E_cells'] == 8 and value['ranges']['E2']['lower'] is None
    assert value['ranges']['E2']['lower_undefined']
    omitted = value['leave_one_profile_out']['FAKE-0']
    assert (omitted['U'], omitted['P'], omitted['mandatory_cells']) == (0, 35, 7)
    assert omitted['ranges']['E1']['E_cells'] == 1


def test_effort_profile_undefined_on_both_arms_is_tie_with_zero_difference():
    # G-05: FAKE-7 has no gold-mandatory cell, so both per-arm rates are undefined.
    records, gold, strata = effort_fixture([1] * 8, [0] * 8)
    for item in range(4):
        gold[('FAKE-7', f'i{item}')] = dict(label=REC)
    value = A.effort_decision(records, gold, strata)
    assert value['strata_profiles_zero_gold_mandatory'] == 1
    assert value['per_profile_high_minus_medium']['FAKE-7'] == Fraction(0)
    assert value['per_profile_high_minus_medium_fraction']['FAKE-7'] == '0/1'
    assert value['high_le_medium_profiles'] == 8 and value['rule_fired'] == 1 and value['choice'] == 'HIGH'
    assert value['d_pooled_medium_minus_high'] == Fraction(1, 20)
    # Seven differences of -1/20 and one 0: only the 2 same-sign patterns of the seven
    # (times 2 free signs on the zero) reach |mean| = 7/160, so p = 4/256.
    flip = A.sign_flip(list(value['per_profile_high_minus_medium'].values()))
    assert flip['undefined'] is False and flip['p'] == Fraction(1, 64)
    paired = A.paired_comparison(records, gold, strata, value, n_boot=8)
    assert paired['sign_flip']['p'] == Fraction(1, 64)


def test_effort_zero_gold_mandatory_count_is_printed(tmp_path_factory):
    obj = world(tmp_path_factory)
    code, lines = invoke(obj)
    assert code == 0
    main_line = next(line for line in lines if line.startswith('effort_reference=main '))
    assert 'strata_profiles_zero_gold_mandatory=0 ' in main_line


def test_tipping_reports_per_profile_u_and_p():
    # G-12: per-profile U and P next to the pooled range.
    cells = [dict(profile_id=f'FAKE-{i}', item=item, gold=MAND if item == 'm' else REC,
                  final=REC if (i == 0 and item == 'm') else MAND)
             for i in range(3) for item in ('m', 'r') for _ in range(5)]
    gold = {(f'FAKE-{i}', item): dict(label=MAND if item == 'm' else REC) for i in range(3) for item in ('m', 'r')}
    summary = dict(e1_input=dict(weighted_binary_rate_on_final_mandatory=0.0), e2_pool=dict(x=0, n=10))
    value = A.tipping_ranges(cells, gold, summary)
    assert value['per_profile'] == {'FAKE-0': dict(U=5, P=5), 'FAKE-1': dict(U=0, P=5), 'FAKE-2': dict(U=0, P=5)}
    assert (value['U'], value['P']) == (5, 15)


def test_resources_sum_all_attempts_and_mark_missing():
    records = [dict(http_attempts=3, llm_provenance=dict(latency_s=7, attempts=[
        dict(usage=dict(input_tokens=2, output_tokens=5, reasoning_tokens=3, cache_read_input_tokens=1)),
        dict(usage=dict(input_tokens=4, output_tokens=9, reasoning_tokens=7, cache_read_input_tokens=2))])),
        dict(http_attempts=1, llm_provenance={})]
    value = A.resources(records)
    assert value['tokens'] == dict(input=6, output=14, reasoning=10, cached=3)
    assert value['latency_s'] == 7 and value['attempts'] == 2 and value['http_attempts'] == 4
    assert value['missing_latency_runs'] == 1 and value['missing_token_usage'] == dict.fromkeys(value['tokens'], 1)


def test_resources_use_actual_azure_attempt_usage_keys(monkeypatch):
    from llm.azure_openai import AzureOpenAIAdapter
    monkeypatch.setenv('AZURE_OPENAI_S10_DEPLOYMENT', 'FAKE-classifier')
    monkeypatch.setenv('AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS', '64000')
    monkeypatch.setenv('AZURE_OPENAI_S10_TIMEOUT_S', '600')
    pending = iter([('length', 11, 7, 3, 2), ('stop', 17, 13, 5, 4)])
    calls = []
    def create(**kwargs):
        assert os.environ['REGCLAW_STUB'] == '1'
        calls.append(kwargs)
        finish, prompt, completion, reasoning, cached = next(pending)
        return NS(model='FAKE-snapshot', choices=[NS(finish_reason=finish, message=NS(
            content=json.dumps(dict(standards=[], notes=[])), refusal=None))],
            usage=NS(prompt_tokens=prompt, completion_tokens=completion,
                     completion_tokens_details=NS(reasoning_tokens=reasoning),
                     prompt_tokens_details=NS(cached_tokens=cached)))
    adapter = AzureOpenAIAdapter('S10', effort='medium', sdk_client=NS(chat=NS(completions=NS(create=create))))
    response = adapter.classify(dict(system='FAKE system', messages=[dict(role='user', content='FAKE input')],
        max_tokens=50, output_config=dict(format=dict(schema=dict(type='object', properties={}, additionalProperties=False)))))
    assert len(calls) == 2
    assert all(set(row['usage']) == {'input_tokens', 'output_tokens', 'reasoning_tokens', 'cache_read_input_tokens'}
               for row in response.provenance['attempts'])
    # Top-level usage aliases describe the last attempt and must not replace the attempt totals.
    response.provenance['cached_input_tokens'] = 999999
    value = A.resources([dict(llm_provenance=response.provenance, http_attempts=response.provenance['http_attempts'])])
    assert value['tokens'] == dict(input=28, output=20, reasoning=8, cached=6)
    assert value['attempts'] == value['http_attempts'] == 2


def test_traps_excluded_from_all_strata_outputs(tmp_path_factory):
    obj = world(tmp_path_factory)
    data = inputs(obj)
    first = A.build_analysis(data, n_boot=4, ppi=MISSING_PPI)
    for row in data['records']:
        if row['stratum'] == 'trap':
            row['final'] = dict.fromkeys(row['final'], NA)
            row['llm_detail'] = 'error'
            row['llm_provenance'] = {}
    second = A.build_analysis(data, n_boot=4, ppi=MISSING_PPI)
    for key in ('primary', 'secondary', 'family_overlap', 'tipping', 'ppi'):
        assert A.clean(first[key]) == A.clean(second[key])
    assert A.clean(first['traps']) != A.clean(second['traps'])


def test_ppi_not_installed_and_version_gate(monkeypatch):
    def absent(_):
        raise A.metadata.PackageNotFoundError('FAKE')
    monkeypatch.setattr(A.metadata, 'version', absent)
    assert A.load_ppi() == MISSING_PPI
    value = A.ppi_under_call([], {}, [], None, status=MISSING_PPI[1])
    assert value['status'] == MISSING_PPI[1] and value['label'] == A.PPI_LABEL
    monkeypatch.setattr(A.metadata, 'version', lambda _: '0.0-FAKE')
    assert A.load_ppi() == (None, 'not run: ppi-python 0.2.3 required')


def ppi_case():
    cells = [dev_cell('FAKE-a', 'm', (MAND,) * 3, human=NA),
             dev_cell('FAKE-b', 'm', (MAND,) * 3, human=MAND)]
    gold = {(c['profile_id'], 'm'): dict(label=MAND) for c in cells}
    records = [dict(profile_id='FAKE-a', final=dict(m=NA)), dict(profile_id='FAKE-b', final=dict(m=MAND))]
    return records, gold, cells


def test_classic_ppi_lambda_one_and_joint_profile_bootstrap():
    pytest.importorskip('numpy')
    calls = []
    def classic(y, yhat, target, *, lam):
        assert lam == 1
        calls.append((tuple(y), tuple(yhat), tuple(target)))
        return sum(target) / len(target) + sum(y - yhat) / len(y)
    records, gold, cells = ppi_case()
    value = A.ppi_under_call(records, gold, cells, classic, n_boot=2000)
    assert value['point'] == value['lo'] == value['hi'] == 0
    assert value['numerator_mean'] == 0 and value['denominator_mean'] == Fraction(1, 2)
    assert value['correction_note']['mean_numerator_correction_term'] == Fraction(-1, 2)
    assert value['correction_note']['mean_denominator_correction_term'] == Fraction(-1, 2)
    assert len(calls) == 4002 and value['seed'] == 0 and value['n_boot'] == 2000
    assert value['n_undefined'] > 0  # Draws containing only FAKE-a have no human-mandatory denominator.
    assert all(yhat == target for _, yhat, target in calls)  # Both arrays move in the same profile draws.
    cells[0]['human'] = MAND
    zero = A.ppi_under_call(records, gold, cells, classic, n_boot=8)
    assert zero['point'] == Fraction(1, 2)
    assert zero['correction_note']['mean_numerator_correction_term'] == 0
    assert zero['correction_note']['mean_denominator_correction_term'] == 0
    assert zero['correction_note']['note'] == 'correction terms = 0; PPI adds no information here'


def test_ppi_ratio_same_population_differs_from_old_mixed_population():
    pytest.importorskip('numpy')
    cells = [dev_cell('FAKE-a', 'm', (MAND,) * 3, human=MAND),
             dev_cell('FAKE-a', 'n', (NA,) * 3, human=MAND),
             dev_cell('FAKE-b', 'm', (MAND,) * 3, human=MAND),
             dev_cell('FAKE-b', 'n', (NA,) * 3, human=NA)]
    gold = {(c['profile_id'], c['item']): dict(label=c['final']) for c in cells}
    records = [dict(profile_id='FAKE-a', final=dict(m=NA, n=NA)),
               dict(profile_id='FAKE-b', final=dict(m=MAND, n=NA))]
    calls = []
    def classic(y, yhat, target, *, lam):
        assert lam == 1 and len(y) == len(yhat) == len(target) == 4
        calls.append((tuple(y), tuple(yhat), tuple(target)))
        return sum(target) / len(target) + sum(y - yhat) / len(y)
    value = A.ppi_under_call(records, gold, cells, classic, n_boot=16)
    # Across the SAME four rows: num=.25+.25=.5; den=.5+.25=.75; ratio=2/3.
    assert value['numerator_mean'] == Fraction(1, 2) and value['denominator_mean'] == Fraction(3, 4)
    assert value['point'] == pytest.approx(Fraction(2, 3))
    assert value['correction_note']['mean_numerator_correction_term'] == Fraction(1, 4)
    assert value['correction_note']['mean_denominator_correction_term'] == Fraction(1, 4)
    # Old target used only the two gold-mandatory rows: .5+.25=3/4, a different estimand.
    old_mixed_population = Fraction(1, 2) + Fraction(1, 4)
    assert value['point'] != float(old_mixed_population)
    assert len(calls) == 34 and 'across all reference labels' in value['unlabelled_set']


@pytest.mark.parametrize('kind', ['zero', 'negative'])
def test_ppi_nonpositive_corrected_denominator_is_undefined(kind):
    pytest.importorskip('numpy')
    cells = [dev_cell('FAKE-a', 'm', (MAND,) * 3, human=NA),
             dev_cell('FAKE-b', 'm', (MAND,) * 3 if kind == 'zero' else (NA,) * 3,
                      human=NA if kind == 'zero' else None)]
    gold = {(c['profile_id'], c['item']): dict(label=c['final']) for c in cells}
    records = [dict(profile_id=c['profile_id'], final=dict(m=NA)) for c in cells]
    def classic(y, yhat, target, *, lam):
        assert lam == 1
        return sum(target) / len(target) + sum(y - yhat) / len(y)
    value = A.ppi_under_call(records, gold, cells, classic, n_boot=16)
    assert value['denominator_mean'] == (0 if kind == 'zero' else Fraction(-1, 2))
    assert value['point'] is value['lo'] is value['hi'] is None and value['undefined']
    assert value['status'] == 'undefined: PPI denominator <= 0'


def test_ppi_optional_installed_value_path():
    package = pytest.importorskip('ppi_py')
    if A.metadata.version('ppi-python') != '0.2.3':
        pytest.skip('optional value test requires ppi-python 0.2.3')
    records, gold, cells = ppi_case()
    value = A.ppi_under_call(records, gold, cells, package.ppi_mean_pointestimate, n_boot=16)
    assert value['point'] == pytest.approx(0) and value['lo'] == value['hi'] == pytest.approx(0)


def test_input_allowlist_and_fixed_diagnostics(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory)
    opened = set()
    real = io.open
    def record(file, mode='r', *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and not any(c in mode for c in 'wax+'):
            opened.add(Path(file).resolve())
        return real(file, mode, *args, **kwargs)
    monkeypatch.setattr(io, 'open', record)
    data = inputs(obj)
    assert opened <= {p.resolve() for p in (*obj.paths.values(), obj.root / LOCKED[0])}
    assert set(data['roster'].values()) == set(A.F.STRATA)


@pytest.mark.parametrize('error_type', [ValueError, SystemExit])
def test_input_exception_text_is_never_echoed(tmp_path_factory, monkeypatch, error_type):
    obj = world(tmp_path_factory)
    before = snapshot(obj.root)
    def fail(*args, **kwargs):
        raise error_type('PRIVATE fake input content')
    monkeypatch.setattr(A, 'load_inputs', fail)
    assert invoke(obj) == (2, ['input_or_output_validation count=1'])
    assert snapshot(obj.root) == before


def test_stopped_marker_appearing_during_analysis_refuses_save(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory)
    original = A.build_analysis
    def stop(*args, **kwargs):
        value = original(*args, **kwargs)
        put(obj.root / 'main/STOPPED.json', '{"reason":"FAKE late stop"}\n')
        return value
    monkeypatch.setattr(A, 'build_analysis', stop)
    assert invoke(obj) == (2, ['stopped_run count=1'])
    assert not (obj.root / 'A.json').exists()


def test_exclusive_output_race_preserves_existing_file(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory)
    original, target = Path.open, obj.root / 'A.json'
    raced = []
    def opened(path, mode='r', *args, **kwargs):
        if path == target and mode == 'x' and not raced:
            raced.append(True)
            with original(path, mode, *args, **kwargs) as stream:
                stream.write('FAKE competing output\n')
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', opened)
    assert invoke(obj) == (3, ['output_exists count=1'])
    assert target.read_text(encoding='utf-8') == 'FAKE competing output\n'


@pytest.mark.parametrize('missing', [False, True])
def test_analysis_export_manifest_sha_mismatch_refuses(tmp_path_factory, missing):
    def change(doc):
        if missing:
            doc.pop('export_manifest_sha256_lf')
        else:
            doc['export_manifest_sha256_lf'] = '0' * 64

    obj = world(tmp_path_factory, manifest_change=change)
    assert invoke(obj) == (2, ['export_manifest_sha count=1'])
    assert not (obj.root / 'A.json').exists()


@pytest.mark.parametrize('field,value', [
    ('global_start', 'MISSING'), ('global_end', 'MISSING'),
    ('global_start', -1), ('global_end', -1), ('global_start', True), ('global_end', True),
    ('global_start', 0.0), ('global_end', 100.0), ('global_end', '100'),
    ('global_start', 101), ('global_end', 151),
])
def test_analysis_global_accounting_bounds_refuse(tmp_path_factory, field, value):
    def change(doc):
        if value == 'MISSING':
            doc.pop(field)
        else:
            doc[field] = value

    obj = world(tmp_path_factory, manifest_change=change)
    assert invoke(obj) == (2, ['global_accounting_bounds count=1'])
    assert not (obj.root / 'A.json').exists()


@pytest.mark.parametrize('field,value,reason', [
    ('global_ledger_last_seq', 'MISSING', 'global_ledger_seq'),
    ('global_ledger_last_seq', True, 'global_ledger_seq'),
    ('global_ledger_last_seq', 0, 'global_ledger_seq'),
    ('global_ledger_last_seq', -1, 'global_ledger_seq'),
    ('global_ledger_last_seq', 201, 'global_ledger_seq'),
    ('global_ledger_last_seq', 199, 'global_ledger_prefix_sha'),
    ('global_ledger_prefix_sha256_lf', 'MISSING', 'global_ledger_prefix_sha'),
    ('global_ledger_prefix_sha256_lf', '0' * 64, 'global_ledger_prefix_sha'),
    ('global_end', 101, 'global_ledger_total'),
])
def test_analysis_global_ledger_prefix_refusals(tmp_path_factory, field, value, reason):
    def change(doc):
        if value == 'MISSING':
            doc.pop(field)
        else:
            doc[field] = value

    obj = world(tmp_path_factory, manifest_change=change)
    assert invoke(obj) == (2, [f'{reason} count=1'])
    assert not (obj.root / 'A.json').exists()


@pytest.mark.parametrize('variant,reason', [
    ('settle_without_reserve', 'ledger_settle'), ('out_of_range', 'ledger_settle'),
    ('duplicate_settle_after_main', 'ledger_settle'), ('torn_json', 'ledger_torn_line'),
    ('no_newline', 'ledger_torn_line'),
])
def test_analysis_invalid_global_ledger_refuses(tmp_path_factory, variant, reason):
    def change(raw):
        if variant == 'torn_json':
            return raw + '{broken\n'
        if variant == 'no_newline':
            return raw.rstrip('\n')
        rows = [json.loads(line) for line in raw.splitlines()]
        if variant == 'settle_without_reserve':
            rows[0].update(kind='settle', amount=-5)
        elif variant == 'out_of_range':
            rows[1]['amount'] = -7
        else:
            rows.append(dict(rows[-1], seq=len(rows) + 1))
        return ''.join(json.dumps(row) + '\n' for row in rows)

    obj = world(tmp_path_factory, global_transform=change)
    before = obj.paths['global_ledger'].read_bytes()
    assert invoke(obj) == (2, [f'{reason} count=1'])
    assert obj.paths['global_ledger'].read_bytes() == before
    assert not (obj.root / 'A.json').exists()


def later_global_rows(raw):
    seq = len(raw.splitlines())
    reserve = dict(format=A.FC.LEDGER_FORMAT, seq=seq + 1, ts=STAMP, step='pilot',
                   kind='reserve', unit='fake-later-pilot', amount=A.L.RUN_RESERVE)
    settle = dict(reserve, seq=seq + 2, kind='settle', amount=1 - A.L.RUN_RESERVE)
    return raw + json.dumps(reserve) + '\n' + json.dumps(settle) + '\n'


@pytest.mark.parametrize('timing', ['before_load', 'before_save'])
def test_analysis_accepts_later_valid_global_rows(tmp_path_factory, monkeypatch, timing):
    obj = world(tmp_path_factory, global_transform=later_global_rows if timing == 'before_load' else None)
    if timing == 'before_save':
        ready = [False]
        original, read = A.build_analysis, Path.read_bytes

        def build(*args, **kwargs):
            result = original(*args, **kwargs)
            ready[0] = True
            return result

        def appended(path):
            raw = read(path)
            return later_global_rows(raw.decode()).encode() if ready[0] and path == obj.paths['global_ledger'] else raw

        monkeypatch.setattr(A, 'build_analysis', build)
        monkeypatch.setattr(Path, 'read_bytes', appended)
    code, lines = invoke(obj)
    assert code == 0
    assert 'paid requests (global ledger): 0 -> 100 (cap 150)' in lines
    assert json.loads((obj.root / 'A.json').read_bytes())['paid_requests_global_ledger'] == (
        'paid requests (global ledger): 0 -> 100 (cap 150)')


def test_analysis_prints_and_stores_paid_request_range(tmp_path_factory):
    obj = world(tmp_path_factory)
    code, lines = invoke(obj)
    expected = 'paid requests (global ledger): 0 -> 100 (cap 150)'
    assert code == 0 and lines.count(expected) == 1
    assert json.loads((obj.root / 'A.json').read_bytes())['paid_requests_global_ledger'] == expected


def test_analysis_rechecks_global_prefix_before_save(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory)
    ready = [False]
    original, read = A.build_analysis, Path.read_bytes

    def build(*args, **kwargs):
        result = original(*args, **kwargs)
        ready[0] = True
        return result

    def changed(path):
        raw = read(path)
        if ready[0] and path == obj.paths['global_ledger']:
            rows = [json.loads(line) for line in raw.splitlines()]
            rows[1]['amount'] = -4
            return ''.join(json.dumps(row) + '\n' for row in rows).encode()
        return raw

    monkeypatch.setattr(A, 'build_analysis', build)
    monkeypatch.setattr(Path, 'read_bytes', changed)
    assert invoke(obj) == (2, ['global_ledger_prefix_sha count=1'])
    assert not (obj.root / 'A.json').exists()


def test_analysis_does_not_take_paid_lock(tmp_path_factory, monkeypatch):
    obj = world(tmp_path_factory)

    def denied(*args, **kwargs):
        pytest.fail('analysis acquired paid lock')

    monkeypatch.setattr(A.L, 'paid_lock', denied)
    assert invoke(obj)[0] == 0
    assert not (obj.root / 'data/runs/s10c_paid.lock').exists()


def test_analysis_missing_global_ledger_refuses(tmp_path_factory):
    obj = world(tmp_path_factory, omit={'global_ledger'})
    assert invoke(obj) == (2, ['global_ledger_input count=1'])
    assert not (obj.root / 'A.json').exists()
