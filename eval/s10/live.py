"""Guarded S10c pilot/main driver. Real execution requires the Part E code lock.

The driver owns interleaving and reserves each run durably before calling it.
Normal counted returns settle the reservation; failures keep it. A phase holds
the paid-process lock through preflight, execution and completion. Global-first
reserves and folder-first settlements preserve conservative global accounting.
Stopped folders are never scored; partial artifacts are retained without repair.

Pilot selection seed source: the approved S10c pilot-selection specification
supplied with this task, which explicitly fixes "S10c-v1|pilot".
"""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import random
import signal
import sys

from llm import provider
from llm.azure_openai import s10_cap, s10_timeout
from standards.data_loader import load_data
from standards.ruleset import normalised_sha
from .addendum_freeze import SEALED_NAMES
from . import frozen_candidates as FC
from . import runner as R
from . import dev_export as E
from .tagger import build_tags
from .split_guard import load_eval_files
from .tagger import load_tags
from .paid_lock import paid_lock, PaidLockHeld

ROOT = Path(__file__).resolve().parents[2]
LOCK_REL = 'eval/s10/manifests/s10c_code_lock.json'
LOCK_FORMAT = 's10c-code-lock.v1'
PILOT_SEED = 'S10c-v1|pilot'
SEED = 0
CALL_CAP = 150
RUN_RESERVE = 6
# G-03: the pilot runs at the pre-registered 64k cap with a fixed 1200 s timeout.
PILOT_CAP = 64000
PILOT_TIMEOUT_S = 1200.0
# G-07: planned calls that must fit, with one run reserve, before a phase starts.
PHASE_HEADROOM = dict(pilot=4, main=100)
SETTINGS = (R.Setting('medium', 'medium'), R.Setting('high', 'high'))
RUN_FORMAT = 's10c-run-manifest.v1'
RECEIPT_FORMAT = 's10c-run-receipt.v1'
PILOT_FORMAT = 's10c-pilot-settings.v1'
SUMMARY_NOTE = 'diagnostic only: pools traps; primary numbers come from eval/s10/analysis.py'


class LiveCheck(ValueError):
    """Only fixed check names belong in diagnostics."""


class PilotGate(LiveCheck):
    pass


def require(condition, check):
    if not condition:
        raise LiveCheck(check)


@dataclass
class StopState:
    requested: bool = False


@contextmanager
def graceful_sigint(*, out=print):
    original = signal.getsignal(signal.SIGINT)
    state = StopState()

    def handler(signum, frame):
        state.requested = True
        signal.signal(signal.SIGINT, signal.default_int_handler)
        out('stop requested: finishing the current run; press Ctrl+C again to abort now')

    try:
        signal.signal(signal.SIGINT, handler)
        yield state
    finally:
        signal.signal(signal.SIGINT, original)


def checked_path(root, value):
    root = Path(root).resolve()
    lexical = root / value
    resolved = lexical.resolve()
    require(resolved.is_relative_to(root), 'path_boundary')
    for part in (*lexical.parts, *resolved.parts):
        require(part.casefold() not in SEALED_NAMES | {'.git'} and not part.casefold().startswith('.env'),
                'forbidden_path')
    FC.check_folder(resolved, 'live_path')
    return resolved


def check_live_environment(env):
    """Pure check; tests use synthetic mappings while the process stays stubbed."""
    require(env.get('REGCLAW_STUB') != '1', 'stub_mode')
    require(env.get('LLM_PROVIDER') == 'azure_openai', 'provider')
    # G-10: no external tracing of prompts during paid S10c steps.
    require(env.get('LANGFUSE_ENABLED') == 'false', 'langfuse')


def check_effective_environment(env=None, *, phase=None, cap=None, timeout=None):
    """The CLI uses the process mapping; explicit mappings are a test seam."""
    env = os.environ if env is None else env
    check_live_environment(env)
    if phase is not None:
        try:
            actual_cap = int(env.get('AZURE_OPENAI_S10_MAX_COMPLETION_TOKENS', ''))
        except (ValueError, TypeError):
            raise LiveCheck('effective_cap') from None
        require(actual_cap == cap, 'effective_cap')
        try:
            actual_timeout = float(env.get('AZURE_OPENAI_S10_TIMEOUT_S', ''))
        except (ValueError, TypeError):
            raise LiveCheck('effective_timeout') from None
        require(actual_timeout == timeout, 'effective_timeout')


def verify_code_lock(root, locked_files=None):
    if locked_files is None:
        # This module/list is created in Part E; absence must refuse live calls.
        from .s10c_lock import LOCKED
        locked_files = LOCKED
    expected = set(locked_files)
    require(bool(expected) and len(expected) == len(locked_files), 'code_lock_files')
    raw = checked_path(root, LOCK_REL).read_bytes()
    manifest = json.loads(raw)
    require(manifest.get('format') == LOCK_FORMAT, 'code_lock_format')
    require(isinstance(manifest.get('files'), dict) and set(manifest['files']) == expected, 'code_lock_files')
    for rel in expected:
        require(isinstance(rel, str) and not Path(rel).is_absolute() and '..' not in Path(rel).parts,
                'code_lock_path')
        actual = normalised_sha(checked_path(root, rel).read_bytes())
        require(actual == manifest['files'][rel]['sha256_lf'], 'code_lock_sha')
    return normalised_sha(raw)


def choose_pilot(profiles):
    return random.Random(PILOT_SEED).choice(sorted(pid for pid, row in profiles.items() if row['stratum'] != 'trap'))


def pilot_settings(records):
    """Require complete sent/returned provenance before deriving main settings."""
    try:
        require(len(records) == 4, 'pilot_records')
        outputs, latencies = [], []
        for record in records:
            provenance = record['llm_provenance']
            require(record['effort_effective'] == record['setting'] == provenance['reasoning_effort'], 'pilot_effort')
            require(record['setting'] in ('medium', 'high'), 'pilot_arm')
            require(type(provenance['max_completion_tokens']) is int and provenance['max_completion_tokens'] > 0,
                    'pilot_cap')
            require(isinstance(provenance['finish_reason'], str) and bool(provenance['finish_reason']), 'pilot_finish')
            latency = provenance['latency_s']
            require(type(latency) in (int, float) and math.isfinite(latency) and latency >= 0, 'pilot_latency')
            require(bool(provenance['attempts']), 'pilot_attempts')
            require(provenance['attempts'][0]['max_completion_tokens'] == PILOT_CAP, 'pilot_cap')
            for attempt in provenance['attempts']:
                cap, timeout = attempt['max_completion_tokens'], attempt['timeout_s']
                require(type(cap) is int and cap > 0, 'pilot_cap')
                require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0, 'pilot_timeout')
                require(isinstance(attempt['finish_reason'], str) and bool(attempt['finish_reason']), 'pilot_finish')
                attempt_latency = attempt['latency_s']
                require(type(attempt_latency) in (int, float) and math.isfinite(attempt_latency)
                        and attempt_latency >= 0, 'pilot_latency')
                # G-04: T uses only attempts sent at the pilot cap (not the 2x re-run).
                if cap == PILOT_CAP:
                    latencies.append(attempt_latency)
                value = attempt['usage']['output_tokens']
                require(type(value) is int and value >= 0, 'pilot_tokens')
                outputs.append(value)
        require(all(sum(r['setting'] == arm for r in records) == 2 for arm in ('medium', 'high')), 'pilot_arms')
        maximum, latency = max(outputs), max(latencies)
        return dict(format=PILOT_FORMAT, gate_passed=True, max_output_tokens=maximum,
                    cap=min(2 * maximum, 128000) if maximum >= 32000 else 64000,
                    max_latency_s=latency, timeout_s=max(600, 3 * latency))
    except (Exception, SystemExit) as exc:
        raise PilotGate('pilot_gate_failed') from exc


def sidecar_names(folder):
    if not folder.exists():
        return []
    require(folder.is_dir() and not folder.is_symlink(), 'sidecar_path')
    paths = list(folder.glob('*.json'))
    require(all(path.is_file() and not path.is_symlink() for path in paths), 'sidecar_path')
    return [path.name for path in paths]


def require_empty_sidecars(folder):
    """Each phase starts with its own empty sidecar folder, including non-JSON entries."""
    if folder.exists() or folder.is_symlink():
        require(folder.is_dir() and not folder.is_symlink(), 'sidecar_path')
        require(not any(folder.iterdir()), 'sidecar_not_empty')


def validate_sidecars(names, records):
    """Count the entire phase folder; collisions cannot supply distinct names."""
    require(len(names) == len({name.casefold() for name in names}), 'sidecar_names')
    require(len(names) == records, 'sidecar_count')
    return len(names)


@dataclass
class Plan:
    phase: str
    root: Path
    out_dir: Path
    run_dir: Path
    committed: Path
    profiles: dict
    gold: dict | None
    tags: dict | None
    retriever: object
    clients: dict
    data: dict
    cap: int
    timeout: float
    repeats: int
    input_shas: dict
    files: list
    lock_sha: str
    locked_files: object
    lock_checks: tuple
    env: object
    prior_calls: dict
    starting_ledger: int
    earlier_dirs: tuple
    global_path: Path
    global_start: int
    stop_state: StopState | None = None
    environment_error: str | None = None


def completed_step(root, phase, path, lock_sha=None):
    """Accept earlier totals only from a receipt-anchored, completed manifest.

    Validated v2 ledger sums verify the manifest's own-folder total; they never
    supply an unanchored replacement total.
    """
    path = checked_path(root, path)
    require(not (path.parent / 'STOPPED.json').exists(), 'earlier_stopped')
    rel = FC.CANDIDATE_RECEIPT_REL if phase == 'candidates' else 'eval/s10/manifests/s10c_pilot_run.json'
    receipt_path = checked_path(root, rel)
    raw = path.read_bytes()
    receipt = json.loads(receipt_path.read_bytes())
    expected_receipt = FC.CANDIDATE_RECEIPT_FORMAT if phase == 'candidates' else RECEIPT_FORMAT
    require(receipt.get('format') == expected_receipt, 'earlier_receipt')
    require(normalised_sha(raw) == receipt['shas']['run_manifest_sha256_lf'], 'earlier_manifest_sha')
    doc = json.loads(raw)
    require(doc.get('format') == (FC.CANDIDATE_RUN_FORMAT if phase == 'candidates' else RUN_FORMAT)
            and doc.get('phase') == phase, 'earlier_manifest')
    if phase == 'candidates':
        # G-02: candidates must have been built under this same code lock.
        require(lock_sha is not None and doc.get('code_lock_sha256_lf') == lock_sha
                and receipt['shas'].get('code_lock_sha256_lf') == lock_sha, 'candidate_code_lock')
    require(doc['counts'] == receipt['counts'], 'earlier_counts')
    counts = doc['counts']
    require(all(type(counts[key]) is int and counts[key] >= 0
                for key in ('ledger_start', 'http_attempts', 'ledger_total')), 'earlier_counts')
    require(counts['ledger_total'] == counts['ledger_start'] + counts['http_attempts'], 'earlier_counts')
    folder = checked_path(root, doc['run_dir'])
    require(not (folder / 'STOPPED.json').exists(), 'earlier_stopped')
    ledger = checked_path(root, folder / FC.CALL_LEDGER_NAME)
    raw_ledger = ledger.read_bytes()  # Missing zero-call ledgers must refuse too.
    require(normalised_sha(raw_ledger) == doc['ledger_sha256_lf'] == receipt['shas']['ledger_sha256_lf'],
            'earlier_ledger_sha')
    require(FC.ledger_total(ledger) == counts['ledger_total'], 'earlier_ledger_total')
    files = [path, receipt_path, ledger]
    if phase == 'pilot':
        require(counts['records'] == counts['planned_records'] == counts['sidecars'] == 4, 'pilot_incomplete')
        for name in ('runs.jsonl', 'pilot_settings.json'):
            output = checked_path(root, path.parent / name)
            sha = normalised_sha(output.read_bytes())
            require(sha == doc['outputs'][name]['sha256_lf'], 'earlier_output_sha')
            if name == 'runs.jsonl':
                require(sha == receipt['shas']['runs_sha256_lf'], 'earlier_output_sha')
            files.append(output)
    return doc, folder, files


def _main_pilot_check(path, cap, timeout, lock_sha):
    require(not (path.parent / 'STOPPED.json').exists(), 'pilot_stopped')
    raw = path.read_bytes()
    settings = json.loads(raw)
    manifest = json.loads((path.parent / 'run_manifest.json').read_bytes())
    require(settings.get('format') == PILOT_FORMAT and settings.get('gate_passed') is True, 'pilot_settings')
    require(manifest.get('format') == RUN_FORMAT and manifest.get('phase') == 'pilot', 'pilot_manifest')
    require(manifest['counts']['records'] == 4, 'pilot_manifest')
    require(manifest['outputs']['pilot_settings.json']['sha256_lf'] == normalised_sha(raw), 'pilot_settings_sha')
    require(manifest['input_shas'][LOCK_REL] == lock_sha, 'pilot_code_lock')
    require(cap == settings['cap'] and timeout == settings['timeout_s'], 'pilot_environment')


def preflight(args, *, root=ROOT, data=None, client_factory=None, retriever_factory=None,
              locked_files=None, lock_checks=None, env=None, out=print):
    env = os.environ if env is None else env
    check_live_environment(env)
    root = Path(root).resolve()
    folder = provider.run_dir()
    require(folder is not None, 'run_dir')
    folder = checked_path(root, folder)
    stopped = folder / 'STOPPED.json'
    require(not stopped.exists() and not stopped.is_symlink(), 'run_dir_stopped')
    require_empty_sidecars(folder / 's10')
    out_dir = checked_path(root, args.out_dir)
    require(not out_dir.is_relative_to(folder / 's10'), 'sidecar_output_overlap')
    require(not out_dir.exists() or out_dir.is_dir() and not any(out_dir.iterdir()), 'output_not_empty')
    committed = checked_path(root, f'eval/s10/manifests/s10c_{args.phase}_run.json')
    require(not committed.exists() and not committed.is_symlink(), 'run_manifest_exists')
    lock_sha = verify_code_lock(root, locked_files)
    checks = tuple(lock_checks) if lock_checks is not None else (R.check_ruleset, R.check_engine)
    for check in checks:
        check()
    cap, timeout = s10_cap(), s10_timeout()
    require(cap is not None and timeout is not None, 'request_settings')
    if args.phase == 'pilot':
        require(cap == PILOT_CAP and timeout == PILOT_TIMEOUT_S, 'pilot_request_settings')
    files = [checked_path(root, value) for value in (args.profiles, args.candidates, args.candidates_manifest)]
    profiles_manifest = checked_path(root, FC.PROFILES_MANIFEST_REL)
    try:
        profiles_sha = FC.verify_dev_profiles(root, files[0])
    except FC.CandidateInput as exc:
        raise LiveCheck(str(exc)) from exc
    candidate_run = checked_path(root, args.candidates_run_manifest)
    candidate_doc, candidate_folder, candidate_files = completed_step(root, 'candidates', candidate_run, lock_sha)
    require(candidate_doc.get('profiles_sha256_lf') == profiles_sha, 'candidate_profiles_sha')
    require(candidate_folder != folder, 'step_run_dirs')
    for name, path in zip(('candidates', 'candidates_manifest'), files[1:3]):
        require(normalised_sha(path.read_bytes()) == candidate_doc['outputs'][name]['sha256_lf'],
                'candidate_run_sha')
    files += [profiles_manifest, *candidate_files]
    prior_calls = dict(candidates=candidate_doc['counts']['ledger_total'], pilot=0)
    earlier_dirs = {candidate_run.parent, candidate_folder}
    gold_path = checked_path(root, args.gold) if args.phase == 'main' else None
    tag_path = checked_path(root, args.tags) if args.phase == 'main' else None
    if args.phase == 'main':
        export_manifest = checked_path(root, E.MANIFEST_REL)
        try:
            export = json.loads(export_manifest.read_bytes())
            require(export.get('format') == E.MANIFEST_FORMAT, 'export_manifest')
            expected_gold_sha = export['outputs'][E.GOLD_REL]['sha256_lf']
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise LiveCheck('export_manifest') from exc
        require(normalised_sha(gold_path.read_bytes()) == expected_gold_sha, 'gold_export_sha')
        pilot = checked_path(root, args.pilot_settings)
        checked_path(root, pilot.parent / 'run_manifest.json')
        pilot_doc, pilot_folder, pilot_files = completed_step(root, 'pilot', pilot.parent / 'run_manifest.json')
        earlier_dirs.update((pilot.parent, pilot_folder))
        require(pilot_folder not in (folder, candidate_folder), 'step_run_dirs')
        candidate_rel = candidate_run.relative_to(root).as_posix()
        require(pilot_doc['input_shas'].get(candidate_rel) == normalised_sha(candidate_run.read_bytes()),
                'pilot_candidate_run')
        profiles_rel = files[0].relative_to(root).as_posix()
        require(pilot_doc['input_shas'].get(profiles_rel) == normalised_sha(files[0].read_bytes()),
                'pilot_profiles_sha')
        prior_calls['pilot'] = pilot_doc['counts']['ledger_total']
        _main_pilot_check(pilot, cap, timeout, lock_sha)
        files += [gold_path, tag_path, export_manifest, *pilot_files]
    starting_ledger = FC.ledger_total(folder / FC.CALL_LEDGER_NAME)
    global_path = checked_path(root, FC.GLOBAL_LEDGER_REL)
    global_start = FC.ledger_total(global_path)
    out(f'candidates_calls={prior_calls["candidates"]} pilot_calls={prior_calls["pilot"]} '
        f'current_ledger_calls={starting_ledger} global_calls={global_start}')
    # G-01: a missing or edited global ledger cannot cover the verified totals.
    require(global_start >= sum(prior_calls.values()) + starting_ledger, 'global_ledger_short')
    # G-07: the whole phase plus one run reserve must fit under the cap.
    require(global_start + PHASE_HEADROOM[args.phase] + RUN_RESERVE <= CALL_CAP, 'call_cap')
    data = load_data() if data is None else data
    split, profiles, gold = load_eval_files(files[0], gold_path, data=data)
    require(split == 'dev', 'dev_split')
    strata = [row['stratum'] for row in profiles.values()]
    require(len(profiles) == 10 and strata.count('trap') == 2
            and all(strata.count(f'S{i}') == 1 for i in range(1, 9)), 'dev_composition')
    retriever = (retriever_factory or FC.FrozenRetriever)(files[1], manifest_path=files[2], profiles=profiles)
    require(not getattr(retriever, 'misses', ()), 'frozen_candidates')
    tags = load_tags(tag_path, required=list(gold)) if tag_path else None
    if args.phase == 'main':
        expected_tags = {(row['profile_id'], row['item']): row['tag']
                         for row in build_tags(profiles, data)['tags']}
        require(tags == expected_tags, 'tags_mismatch')
    files.append(checked_path(root, LOCK_REL))
    shas = {path.relative_to(root).as_posix(): normalised_sha(path.read_bytes()) for path in files}
    clients = {setting.name: (client_factory or R.azure_client_factory)(setting) for setting in SETTINGS}
    environment_error = None
    try:
        check_effective_environment(env, phase=args.phase, cap=cap, timeout=timeout)
    except LiveCheck as exc:
        # Route post-load drift through execute's normal STOPPED path.
        environment_error = str(exc)
    for setting in (() if environment_error else SETTINGS):
        client = clients[setting.name]
        require(getattr(client, 'provider', None) == 'azure_openai' and getattr(client, 'role', None) == 'S10',
                'client_role')
        require(getattr(client, 'effort', None) == setting.effort, 'client_effort')
        require(getattr(client, 'cap', None) == cap and getattr(client, 'timeout', None) == timeout,
                'client_settings')
        require(isinstance(getattr(client, 'last_http_log', None), list), 'client_http_counter')
    if args.phase == 'pilot':
        pid = choose_pilot(profiles)
        profiles = {pid: profiles[pid]}
    plan = Plan(args.phase, root, out_dir, folder, committed, profiles, gold, tags, retriever, clients, data,
                cap, timeout, 2 if args.phase == 'pilot' else 5, shas, files, lock_sha, locked_files, checks, env,
                prior_calls, starting_ledger, tuple(earlier_dirs), global_path, global_start)
    plan.environment_error = environment_error
    return plan


def _guards(plan):
    check_effective_environment(plan.env, phase=plan.phase, cap=plan.cap, timeout=plan.timeout)
    require(provider.run_dir() == plan.run_dir, 'run_dir_changed')
    require(verify_code_lock(plan.root, plan.locked_files) == plan.lock_sha, 'code_lock_changed')
    for check in plan.lock_checks:
        check()
    require(not any((folder / 'STOPPED.json').exists() for folder in plan.earlier_dirs), 'earlier_stopped')
    for rel, sha in plan.input_shas.items():
        require(normalised_sha(checked_path(plan.root, rel).read_bytes()) == sha, 'input_changed')
    require(not getattr(plan.retriever, 'misses', ()), 'frozen_miss')


def _json_text(doc):
    return json.dumps(R._nan_to_none(doc), indent=2, sort_keys=True, allow_nan=False) + '\n'


def write_once(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text)


def _global_end(plan):
    """Observed global ledger total; None if it cannot be read (STOPPED path must not fail)."""
    try:
        return FC.ledger_total(plan.global_path)
    except Exception:
        return None


def _counts(plan, records, calls, ledger_start):
    try:
        folder_total = FC.ledger_total(plan.run_dir / FC.CALL_LEDGER_NAME)
    except Exception:
        folder_total = ledger_start + calls
    return dict(records=len(records), planned_records=len(plan.profiles) * len(SETTINGS) * plan.repeats,
                http_attempts=calls, ledger_start=ledger_start, ledger_total=folder_total,
                call_totals=dict(**plan.prior_calls, current_ledger=folder_total,
                                 total=sum(plan.prior_calls.values()) + folder_total,
                                 global_start=plan.global_start, global_end=_global_end(plan)))


def finalize(plan, records, calls, ledger_start, *, summary_factory=None, now=None):
    require(not (plan.stop_state and plan.stop_state.requested), 'interrupted')
    counts = _counts(plan, records, calls, ledger_start)
    require(counts['records'] == counts['planned_records'], 'incomplete_run')
    _guards(plan)
    require(FC.ledger_total(plan.run_dir / FC.CALL_LEDGER_NAME) == ledger_start + calls, 'ledger_changed')
    require(FC.ledger_total(plan.global_path) == plan.global_start + calls, 'global_ledger_changed')
    for rel, sha in plan.input_shas.items():
        require(normalised_sha(checked_path(plan.root, rel).read_bytes()) == sha, 'input_changed')
    counts['sidecars'] = validate_sidecars(sidecar_names(plan.run_dir / 's10'), len(records))
    outputs = {'runs.jsonl': dict(sha256_lf=normalised_sha((plan.out_dir / 'runs.jsonl').read_bytes()))}
    if plan.phase == 'pilot':
        artifact_name, artifact = 'pilot_settings.json', pilot_settings(records)
    else:
        summary = (summary_factory or R.summarise)(
            records, plan.gold, settings=SETTINGS, repeats=plan.repeats, seed=SEED, n_boot=2000,
            split='dev', files=plan.files, llm_name=records[0]['model'],
            retriever_name=type(plan.retriever).__name__, now=now, tags=plan.tags)
        summary['note'] = SUMMARY_NOTE
        summary['label'] = 'provisional (GPT)'
        artifact_name, artifact = 'summary.json', summary
    artifact_text = _json_text(artifact)
    outputs[artifact_name] = dict(sha256_lf=normalised_sha(artifact_text.encode('utf-8')))
    ledger_sha = normalised_sha((plan.run_dir / FC.CALL_LEDGER_NAME).read_bytes())
    snapshot = FC.ledger_snapshot(plan.global_path)
    global_end = snapshot.pop('total')
    manifest = dict(format=RUN_FORMAT, phase=plan.phase, label='provisional (GPT)', counts=counts,
                    global_start=plan.global_start, global_end=global_end, **snapshot,
                    run_dir=plan.run_dir.relative_to(plan.root).as_posix(), ledger_sha256_lf=ledger_sha,
                    settings=[dict(name=s.name, effort=s.effort, max_completion_tokens=plan.cap,
                                   timeout_s=plan.timeout) for s in SETTINGS], repeats=plan.repeats, seed=SEED,
                    input_shas=plan.input_shas, outputs=outputs)
    if plan.phase == 'main':
        export_relative = checked_path(plan.root, E.MANIFEST_REL).relative_to(plan.root).as_posix()
        manifest['export_manifest_sha256_lf'] = plan.input_shas[export_relative]
    manifest_text = _json_text(manifest)
    receipt = dict(format=RECEIPT_FORMAT, counts=counts,
                   shas=dict(runs_sha256_lf=outputs['runs.jsonl']['sha256_lf'],
                             ledger_sha256_lf=ledger_sha,
                             run_manifest_sha256_lf=normalised_sha(manifest_text.encode('utf-8'))))
    require(not (plan.stop_state and plan.stop_state.requested), 'interrupted')
    write_once(plan.out_dir / 'run_manifest.json', manifest_text)
    require(not (plan.stop_state and plan.stop_state.requested), 'interrupted')
    write_once(plan.committed, _json_text(receipt))
    # Completion requires this last exclusive write too; any failure marks STOPPED.
    require(not (plan.stop_state and plan.stop_state.requested), 'interrupted')
    write_once(plan.out_dir / artifact_name, artifact_text)
    return manifest


def execute(plan, *, summary_factory=None, now=None, out=print):
    records, calls, ledger_start = [], 0, 0
    ledger = plan.run_dir / FC.CALL_LEDGER_NAME
    try:
        ledger_start = FC.ledger_total(ledger)
        require(ledger_start == plan.starting_ledger, 'ledger_changed')
        require(FC.ledger_total(plan.global_path) == plan.global_start, 'global_ledger_changed')
        require_empty_sidecars(plan.run_dir / 's10')
        require(not plan.out_dir.exists() or not any(plan.out_dir.iterdir()), 'output_not_empty')
        plan.out_dir.mkdir(parents=True, exist_ok=True)
        require(plan.environment_error is None, plan.environment_error or 'effective_environment')
        check_effective_environment(plan.env, phase=plan.phase, cap=plan.cap, timeout=plan.timeout)
        consecutive = 0
        with (plan.out_dir / 'runs.jsonl').open('x', encoding='utf-8', newline='\n') as stream:
            for index, (repeat, pid, setting) in enumerate(R.plan_order(plan.profiles, SETTINGS, plan.repeats, SEED)):
                require(not (plan.stop_state and plan.stop_state.requested), 'interrupted')
                _guards(plan)
                require(FC.ledger_total(ledger) == ledger_start + calls, 'ledger_changed')
                require(FC.ledger_total(plan.global_path) == plan.global_start + calls, 'global_ledger_changed')
                # G-01: the reserve check uses the global total (all steps and attempts).
                require(plan.global_start + calls + RUN_RESERVE <= CALL_CAP, 'call_cap')
                client = plan.clients[setting.name]
                require(client.effort == setting.effort, 'client_effort')
                client.last_http_log = []
                unit = f'{plan.run_dir.relative_to(plan.root).as_posix()}:{index}'
                require(not (plan.stop_state and plan.stop_state.requested), 'interrupted')
                FC.reserve_unit(plan.global_path, ledger, step=plan.phase, unit=unit, now=now)
                record = R.run_one(plan.profiles[pid], setting, repeat, client, plan.retriever, plan.data)
                sent = len(client.last_http_log)
                require(0 <= sent <= RUN_RESERVE and record['http_attempts'] == sent, 'http_attempts')
                record = dict(call_index=index, **record)
                stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + '\n')
                stream.flush()
                records.append(record)
                require(record['effort_effective'] == setting.effort, 'effective_effort')
                require(record['llm_detail'] in R.LLM_DETAILS, 'llm_detail')
                require(not getattr(plan.retriever, 'misses', ()), 'frozen_miss')
                consecutive = consecutive + 1 if record['llm_detail'] == 'error' else 0
                require(consecutive < 3, 'consecutive_errors')
                FC.settle_unit(plan.global_path, ledger, step=plan.phase, unit=unit, actual=sent, now=now)
                calls += sent
        manifest = finalize(plan, records, calls, ledger_start, summary_factory=summary_factory, now=now)
    except (Exception, SystemExit, KeyboardInterrupt) as exc:
        reason = ('interrupted' if isinstance(exc, KeyboardInterrupt) else str(exc)
                  if isinstance(exc, (LiveCheck, FC.LedgerError)) else 'run_validation')
        try:
            marker = _json_text(dict(reason=reason, counts=_counts(plan, records, calls, ledger_start)))
            # G-11: the marker goes to the output folder (if created) and the phase run dir.
            folders = ([plan.out_dir] if plan.out_dir.is_dir() else []) + [plan.run_dir]
        except (Exception, SystemExit):
            marker, folders = None, []
            out('stopped_marker_write count=1')
        for folder in folders:
            try:
                write_once(folder / 'STOPPED.json', marker)
            except (Exception, SystemExit):
                out('stopped_marker_write count=1')
        out('pilot gate failed' if isinstance(exc, PilotGate) else f'{reason} count=1')
        return 2 if isinstance(exc, FC.LedgerError) else 1 if isinstance(exc, PilotGate) else 4
    out(f'records={len(records)} http_attempts={calls} '
        f'runs_sha256_lf={manifest["outputs"]["runs.jsonl"]["sha256_lf"]}')
    return 0


def main(argv=None, *, root=ROOT, data=None, client_factory=None, retriever_factory=None,
         summary_factory=None, locked_files=None, lock_checks=None, env=None, now=None, out=print):
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(prog='python -m eval.s10.live')
    commands = parser.add_subparsers(dest='phase', required=True)
    for phase in ('pilot', 'main'):
        command = commands.add_parser(phase)
        for name in ('profiles', 'candidates', 'candidates-manifest', 'candidates-run-manifest', 'out-dir'):
            command.add_argument('--' + name, required=True)
        if phase == 'main':
            for name in ('gold', 'tags', 'pilot-settings'):
                command.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    try:
        with paid_lock(root), graceful_sigint(out=out) as state:
            plan = preflight(args, root=root, data=data, client_factory=client_factory,
                             retriever_factory=retriever_factory, locked_files=locked_files,
                             lock_checks=lock_checks, env=env, out=out)
            plan.stop_state = state
            return execute(plan, summary_factory=summary_factory, now=now, out=out)
    except PaidLockHeld as exc:
        out(str(exc))
        return 2
    except (Exception, SystemExit) as exc:
        out(f'{str(exc) if isinstance(exc, (LiveCheck, FC.LedgerError)) else "preflight_validation"} count=1')
        return 2


if __name__ == '__main__':
    sys.exit(main(env=os.environ))
