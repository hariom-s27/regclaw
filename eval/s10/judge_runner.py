"""S10b2 (S6): run judges A (Claude Code) and B (Codex CLI) on a judge kit, import judge C's chat output.

Hariom runs every judge call himself. Subcommands (global: --claude-exe, --codex-exe, --run-dir):
  canary --judge A|B --dir DIR          fixed canary question with the real flags (no schema); prints the
                                        model's first two lines; writes nothing (DIR is only the cwd)
  smoke  --judge A|B                    round smoke (SMOKE-1)
  run    --round practice --judge A|B   resumes: skips devices with a valid result
  import-chat --round practice|smoke --batch NN --file PATH --antigravity-version V --model-label L
  status --round practice|smoke         valid / invalid / missing per judge
Rounds practice, smoke, eval and stability (S10b2b); every round needs its kit_manifest.json (judge_kit builds
the eval and stability kits only after the codebook_v1.json checks pass).

Before EVERY call: the run-dir (outside the repo, existing, empty) is checked and `<exe> --version` must
equal the configured version, else the whole run STOPs. Child: argument list (no shell), cwd = run-dir,
env = os.environ minus env_remove plus env_set, prompt on stdin (UTF-8), configured timeout. After the call
the run-dir must still be empty (guard reason run_dir_written; the run then STOPs).
Every attempt keeps its raw files as <id>.attemptN.* (never overwritten); a valid result is written to
<id>.result.json. One retry per device; max_consecutive_failures devices in a row still invalid -> STOP.
run_log.jsonl per round: one row per call with times, versions, shas, verdicts and rule names only.
Prints counts, verdicts, rule names, versions and timings; never labels, reasons or profile text (canary
lines are the only model text printed).

Exit codes: 0 done · 1 refused (eval round, wrong label) · 2 input error · 4 STOP (version, run-dir,
failures, run_dir_written).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from standards.ruleset import normalised_sha

from .judge_kit import KITS_REL, SCHEMA_REL
from .judge_output import validate

ROOT = Path(__file__).resolve().parents[2]
CONFIG_REL = 'eval/s10/judge_config.yaml'
JUDGES_REL = 'data/gold/s10/judges'
RUN_ROUNDS = ('practice', 'smoke', 'eval', 'stability')
FILE_JUDGES = ('A', 'B')
B_BANNED_ITEMS = frozenset({'command_execution', 'file_change', 'mcp_tool_call', 'web_search'})
B_MODEL_PINNED = 'pinned by -m (not echoed in --json events)'
VERSION_TIMEOUT_S = 60
FENCE_RE = re.compile(r'^```(?:json)?[ \t]*\r?\n(.*)\r?\n```$', re.DOTALL)


class RunnerStop(RuntimeError):
    """Stop the whole run (version, run-dir, consecutive failures)."""


class RunnerError(ValueError):
    """Bad input (kit, config, arguments)."""


class RunnerRefused(PermissionError):
    """Refused by rule (eval round, wrong model label)."""


def load_config(root):
    return yaml.safe_load((Path(root) / CONFIG_REL).read_text(encoding='utf-8'))


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def check_run_dir(run_dir, root, *, must_be_empty=True):
    p = Path(run_dir)
    if not p.is_dir():
        raise RunnerStop(f'run-dir does not exist: {p}')
    rp, rr = p.resolve(), Path(root).resolve()
    if rp == rr or rr in rp.parents:
        raise RunnerStop('run-dir is inside the repo')
    if must_be_empty and any(p.iterdir()):
        raise RunnerStop('run-dir is not empty')
    return p


def child_env(cfg, judge):
    drop = {k.upper() for k in cfg.get('env_remove') or []}
    env = {k: v for k, v in os.environ.items() if k.upper() not in drop}
    env.update({str(k): str(v) for k, v in (cfg['judges'][judge].get('env_set') or {}).items()})
    return env


def tool_version(prefix, exe, env, cwd):
    proc = subprocess.run([*prefix, str(exe), '--version'], capture_output=True, env=env, cwd=str(cwd),
                          timeout=VERSION_TIMEOUT_S)
    return proc.stdout.decode('utf-8', 'replace').strip()


def run_child(cmd, stdin_text, cwd, env, timeout):
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, input=stdin_text.encode('utf-8'), capture_output=True, cwd=str(cwd), env=env,
                              timeout=timeout)
        return dict(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, timed_out=False,
                    duration_s=round(time.monotonic() - start, 2))
    except subprocess.TimeoutExpired as exc:
        return dict(exit_code=None, stdout=exc.stdout or b'', stderr=exc.stderr or b'', timed_out=True,
                    duration_s=round(time.monotonic() - start, 2))


def _numbers(d):
    return {k: v for k, v in (d or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)} \
        if isinstance(d, dict) else {}


def _blocks(ev):
    msg = ev.get('message')
    content = msg.get('content') if isinstance(msg, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def guard_a(stdout: bytes, model, tool_name=None, mode='run'):
    """Claude --output-format stream-json. The envelope is the final event (type "result").

    Returns (reasons, observed model keys, structured result or None, tokens, tool_use names, result event).
    mode 'run': with tool_name set, the tool_use count must equal num_turns - 1, all named tool_name;
    with tool_name None the attempt is structured_tool_unpinned. mode 'canary': no tool_use at all, no
    structured output expected.
    """
    events, bad = _events(stdout)
    reasons = ['event_not_json'] if bad else []
    names = [b.get('name') for ev in events if ev.get('type') == 'assistant'
             for b in _blocks(ev) if b.get('type') == 'tool_use']
    env = events[-1] if events else {}
    if env.get('type') != 'result':
        return reasons + ['no_result_event'], [], None, {}, names, {}
    if env.get('is_error') is not False:
        reasons.append('is_error')
    if env.get('subtype') != 'success':
        reasons.append('subtype')
    turns = env.get('num_turns')
    turns = turns if isinstance(turns, int) and not isinstance(turns, bool) else None
    if turns not in (1, 2):
        reasons.append('num_turns')
    if env.get('permission_denials') != []:
        reasons.append('permission_denials')
    usage = env.get('modelUsage') if isinstance(env.get('modelUsage'), dict) else {}
    observed = sorted(usage)
    if model not in usage:
        reasons.append('model_missing')
    if any(k != model and not k.startswith('claude-haiku') for k in usage):
        reasons.append('model_other')
    if 'subagent_stats' in env:
        stats = env['subagent_stats']
        if not (isinstance(stats, dict) and stats.get('spawned') == 0):
            reasons.append('subagents')
    if mode == 'canary':
        if names:
            reasons.append('tool_use')
        return reasons, observed, None, _numbers(env.get('usage')), names, env
    if tool_name is None:
        reasons.append('structured_tool_unpinned')
    elif any(n != tool_name for n in names) or len(names) > 1 or (turns in (1, 2) and len(names) != turns - 1):
        reasons.append('tool_use')
    result = env.get('structured_output')
    if not isinstance(result, (dict, list)):
        result = None
        reasons.append('structured_output_missing')
    return reasons, observed, result, _numbers(env.get('usage')), names, env


def _events(stdout: bytes):
    events, bad = [], 0
    for line in stdout.decode('utf-8', 'replace').splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
            events.append(ev if isinstance(ev, dict) else {})
        except ValueError:
            bad += 1
    return events, bad


def _item_types(ev):
    item = ev.get('item') if isinstance(ev.get('item'), dict) else {}
    return {ev.get('type'), ev.get('item_type'), item.get('type'), item.get('item_type')}


def agent_text(events):
    texts = [ev['item'].get('text') for ev in events if isinstance(ev.get('item'), dict)
             and ev['item'].get('type') == 'agent_message' and isinstance(ev['item'].get('text'), str)]
    return texts[-1] if texts else ''


def guard_b(stdout: bytes, stderr: bytes, last_path, model, effort):
    """Returns (reasons, observed model, parsed last message or None, tokens)."""
    events, bad = _events(stdout)
    reasons = ['event_not_json'] if bad else []
    if any(_item_types(ev) & B_BANNED_ITEMS for ev in events):
        reasons.append('tool_use_event')
    done = [ev for ev in events if ev.get('type') == 'turn.completed']
    if not done:
        reasons.append('no_turn_completed')
    observed = None
    for line in stderr.decode('utf-8', 'replace').splitlines():
        m = re.match(r'^\s*model:\s*(\S+)', line)
        if m:
            observed = m.group(1)
            if m.group(1) != model:
                reasons.append('wrong_model')
        m = re.match(r'^\s*reasoning effort:\s*(\S+)', line)
        if m and m.group(1) != effort:
            reasons.append('wrong_effort')
    if observed is None:                     # codex --json does not echo the model; -m pins it (not a guard failure)
        observed = B_MODEL_PINNED
    result = None
    if last_path is not None:
        try:
            result = json.loads(Path(last_path).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            reasons.append('last_not_json')
    tokens = _numbers(done[-1].get('usage')) if done else {}
    return reasons, observed, result, tokens


def _write_bytes_once(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as fh:
        fh.write(data)


def next_attempt(out_dir: Path, stem):
    nums = [int(m.group(1)) for p in out_dir.glob(f'{stem}.attempt*.*')
            if (m := re.match(rf'^{re.escape(stem)}\.attempt(\d+)\.', p.name))] if out_dir.exists() else []
    return max(nums, default=0) + 1


def append_log(path: Path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + '\n')


def load_kit(root, round_name):
    path = Path(root) / KITS_REL / round_name / 'kit_manifest.json'
    if not path.is_file():
        raise RunnerError(f'kit manifest missing: {KITS_REL}/{round_name}/kit_manifest.json')
    return json.loads(path.read_text(encoding='utf-8'))


def kit_devices(root, kit, judge):
    """[(device_id, prompt text, prompt sha, item order)] for one file judge, sha-checked vs the kit manifest."""
    out = []
    for f in kit['files']:
        if f['judge'] != judge:
            continue
        raw = (Path(root) / f['path']).read_bytes()
        sha = normalised_sha(raw)
        if sha != f['sha256_lf']:
            raise RunnerError(f'kit file sha differs from kit_manifest.json: {f["path"]}')
        d = f['device_ids'][0]
        out.append((d, raw.decode('utf-8'), sha, f['item_order'][d]))
    return out


class Runner:
    def __init__(self, root, cfg, judge, exe, run_dir, prefix=(), out=print):
        if judge not in FILE_JUDGES:
            raise RunnerError('judge must be A or B')
        if not exe:
            raise RunnerError(f'judge {judge}: the tool path is required (--claude-exe / --codex-exe)')
        self.root, self.cfg, self.judge, self.exe = Path(root), cfg, judge, exe
        self.jcfg = cfg['judges'][judge]
        self.run_dir, self.prefix, self.out = Path(run_dir), list(prefix), out
        self.env = child_env(cfg, judge)
        self.last_reasons = []
        self.schema_path = self.root / SCHEMA_REL
        schema_raw = self.schema_path.read_bytes()
        self.schema = json.loads(schema_raw.decode('utf-8'))
        self.schema_sha = normalised_sha(schema_raw)

    def check_version(self):
        got = tool_version(self.prefix, self.exe, self.env, self.run_dir)
        if got != self.jcfg['expected_version']:
            raise RunnerStop(f'judge {self.judge}: tool version {got!r} != expected {self.jcfg["expected_version"]!r}')
        return got

    def call(self, round_name, device_id, prompt, prompt_sha, items):
        """One attempt. Returns True if a valid result was written."""
        out_dir = self.root / JUDGES_REL / round_name / self.judge
        log = self.root / JUDGES_REL / round_name / 'run_log.jsonl'
        check_run_dir(self.run_dir, self.root)
        version = self.check_version()
        n = next_attempt(out_dir, device_id)
        stem = out_dir / f'{device_id}.attempt{n}'
        cmd = [*self.prefix, str(self.exe), *self.jcfg['args']]
        last = None
        if self.judge == 'A':
            cmd += ['--json-schema', json.dumps(self.schema, separators=(',', ':'), ensure_ascii=False)]
        else:
            last = Path(f'{stem}.last.json')
            last.parent.mkdir(parents=True, exist_ok=True)
            cmd += ['--output-schema', str(self.schema_path), '-o', str(last), '-']
        res = run_child(cmd, prompt, self.run_dir, self.env, self.cfg['timeout_s'])
        if self.judge == 'A':
            _write_bytes_once(Path(f'{stem}.stream.jsonl'), res['stdout'])
            reasons, observed, result, tokens, names, _ = guard_a(res['stdout'], self.jcfg['model'],
                                                                  self.jcfg.get('structured_output_tool'))
            observed = ','.join(observed) or None
            extra = dict(tool_use_names=names)
        else:
            extra = {}
            _write_bytes_once(Path(f'{stem}.events.jsonl'), res['stdout'])
            reasons, observed, result, tokens = guard_b(res['stdout'], res['stderr'], last, self.jcfg['model'],
                                                        self.jcfg['effort'])
        _write_bytes_once(Path(f'{stem}.stderr.txt'), res['stderr'])
        if res['timed_out']:
            reasons.insert(0, 'timeout')
        elif res['exit_code'] != 0:
            reasons.insert(0, 'exit_code')
        dir_written = any(self.run_dir.iterdir())
        if dir_written:
            reasons.append('run_dir_written')
        guard_ok = not reasons
        valid, rules = False, []
        if guard_ok:
            valid, rules = validate(result, dict(id=device_id, items=items), self.schema)
        if valid:
            _write_bytes_once(out_dir / f'{device_id}.result.json',
                              (json.dumps(result, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))
        append_log(log, dict(time_utc=utc_now(), judge=self.judge, device_id=device_id, attempt=n,
                             tool_version=version, model_observed=observed, prompt_sha256_lf=prompt_sha,
                             schema_sha256_lf=self.schema_sha, exit_code=res['exit_code'],
                             duration_s=res['duration_s'], guard_ok=guard_ok, guard_reasons=reasons,
                             valid=valid, rule_names=rules, tokens=tokens, **extra))
        self.last_reasons = reasons
        self.out(f'{self.judge} {device_id} attempt {n}: version ok | exit={res["exit_code"]} | '
                 f'{res["duration_s"]}s | guard={"ok" if guard_ok else ",".join(reasons)} | '
                 f'validator={"valid" if valid else ("-" if not guard_ok else ",".join(rules))}')
        if 'structured_tool_unpinned' in reasons:
            self.out(f'{self.judge} {device_id}: tool_use names observed: {",".join(map(str, names)) or "none"}')
        if dir_written:
            raise RunnerStop('run_dir_written: the tool wrote into the run-dir')
        return valid

    def run_round(self, round_name):
        if round_name not in RUN_ROUNDS:
            raise RunnerRefused(f'round {round_name} is not a run round')
        kit = load_kit(self.root, round_name)
        devices = kit_devices(self.root, kit, self.judge)
        out_dir = self.root / JUDGES_REL / round_name / self.judge
        counts = dict(valid=0, invalid=0, skipped=0)
        consecutive = 0
        for device_id, prompt, sha, items in devices:
            if (out_dir / f'{device_id}.result.json').exists():
                counts['skipped'] += 1
                continue
            ok = self.call(round_name, device_id, prompt, sha, items)
            retried = False
            if not ok and 'structured_tool_unpinned' in self.last_reasons:
                self.out(f'{self.judge} {device_id}: structured_tool_unpinned, no retry')
            elif not ok:
                retried = True
                ok = self.call(round_name, device_id, prompt, sha, items)
            if ok:
                counts['valid'] += 1
                consecutive = 0
                continue
            counts['invalid'] += 1
            consecutive += 1
            self.out(f'{self.judge} {device_id}: ' + ('still invalid after retry (marked)' if retried
                                                      else 'not retried (marked)'))
            if consecutive >= self.cfg['max_consecutive_failures']:
                self.out(f'counts: {counts}')
                raise RunnerStop(f'{consecutive} devices in a row still invalid after retry')
        self.out(f'{self.judge} round={round_name} counts: valid={counts["valid"]} invalid={counts["invalid"]} '
                 f'skipped_already_valid={counts["skipped"]}')
        return counts


def canary(root, cfg, judge, exe, cdir, prefix=(), out=print):
    """Canary: real flags without schema; prints the first two non-empty lines of the answer. Writes nothing."""
    jcfg = cfg['judges'][judge]
    cdir = check_run_dir(cdir, root, must_be_empty=False)
    env = child_env(cfg, judge)
    got = tool_version(prefix, exe, env, cdir)
    if got != jcfg['expected_version']:
        raise RunnerStop(f'judge {judge}: tool version {got!r} != expected {jcfg["expected_version"]!r}')
    out(f'{judge} version: {got}')
    cmd = [*prefix, str(exe), *jcfg['args']] + (['-'] if judge == 'B' else [])
    res = run_child(cmd, cfg['canary_question'], cdir, env, cfg['timeout_s'])
    out(f'{judge} canary: exit={res["exit_code"]} timed_out={res["timed_out"]} {res["duration_s"]}s')
    if res['exit_code'] != 0:
        first = next((l for l in res['stderr'].decode('utf-8', 'replace').splitlines() if l.strip()), '')
        out(f'{judge} stderr first line: {first[:300]}')
    if judge == 'A':
        reasons, observed, _, _, names, env = guard_a(res['stdout'], jcfg['model'], mode='canary')
        text = env.get('result') or ''
        out(f'{judge} models observed: {",".join(observed) or "none"}')
        if names:
            out(f'{judge} tool_use names: {",".join(map(str, names))}')
    else:
        reasons, observed, _, _ = guard_b(res['stdout'], res['stderr'], None, jcfg['model'], jcfg['effort'])
        text = agent_text(_events(res['stdout'])[0])
        out(f'{judge} model observed (stderr): {observed or "not shown"}')
    out(f'{judge} guard: {"ok" if not reasons else ",".join(reasons)}')
    lines = [l.strip() for l in str(text).splitlines() if l.strip()][:2]
    for i, line in enumerate(lines, start=1):
        out(f'{judge} line {i}: {line[:300]}')
    if not lines:
        out(f'{judge} answer: (empty)')
    return 0 if res['exit_code'] == 0 else 4


def strip_fence(text):
    m = FENCE_RE.match(text.strip())
    return (m.group(1), True) if m else (text.strip(), False)


def import_chat(root, cfg, round_name, batch, file_path, version, label, out=print):
    if round_name not in RUN_ROUNDS:
        raise RunnerRefused(f'import-chat: round {round_name} is not a run round')
    if label != cfg['judges']['C']['model_label']:
        raise RunnerRefused('import-chat: model label differs from judge_config.yaml')
    root = Path(root)
    kit = load_kit(root, round_name)
    name = f'batch_{int(batch):02d}'
    entry = next((f for f in kit['files'] if f['judge'] == 'C' and Path(f['path']).name == f'{name}.prompt.md'), None)
    if entry is None:
        raise RunnerError(f'kit has no C batch {name}')
    schema_raw = (root / SCHEMA_REL).read_bytes()
    schema, schema_sha = json.loads(schema_raw.decode('utf-8')), normalised_sha(schema_raw)
    out_dir = root / JUDGES_REL / round_name / 'C'
    raw = Path(file_path).read_bytes()
    n = next_attempt(out_dir, name)
    _write_bytes_once(out_dir / f'{name}.attempt{n}.paste.txt', raw)
    text, fenced = strip_fence(raw.decode('utf-8-sig', 'replace'))
    batch_rule = None
    try:
        arr = json.loads(text)
        if not isinstance(arr, list) or len(arr) != len(entry['device_ids']):
            batch_rule = 'batch_length'
    except ValueError:
        arr, batch_rule = None, 'batch_not_json'
    guard_reasons = ['fence_stripped'] if fenced else []
    counts = dict(valid=0, invalid=0, already_valid=0)
    for i, device_id in enumerate(entry['device_ids']):
        if batch_rule:
            valid, rules = False, [batch_rule]
        else:
            valid, rules = validate(arr[i], dict(id=device_id, items=entry['item_order'][device_id]), schema)
        result_path = out_dir / f'{device_id}.result.json'
        if valid and result_path.exists():
            counts['already_valid'] += 1
        elif valid:
            _write_bytes_once(result_path, (json.dumps(arr[i], indent=2, ensure_ascii=False) + '\n').encode('utf-8'))
            counts['valid'] += 1
        else:
            counts['invalid'] += 1
        append_log(root / JUDGES_REL / round_name / 'run_log.jsonl', dict(
            time_utc=utc_now(), judge='C', device_id=device_id, attempt=n, tool_version=version,
            model_observed=label, prompt_sha256_lf=entry['sha256_lf'], schema_sha256_lf=schema_sha,
            paste_sha256_lf=normalised_sha(raw), exit_code=None, duration_s=None, guard_ok=True,
            guard_reasons=guard_reasons, valid=valid, rule_names=rules, tokens={}))
        out(f'C {device_id} ({name} attempt {n}): validator={"valid" if valid else ",".join(rules)}')
    out(f'C {name}: valid={counts["valid"]} invalid={counts["invalid"]} already_valid={counts["already_valid"]}'
        + (' | fence_stripped' if fenced else ''))
    return counts


def status(root, round_name, out=print):
    if round_name not in RUN_ROUNDS:
        raise RunnerRefused(f'status: round {round_name} is not a run round')
    root = Path(root)
    kit = load_kit(root, round_name)
    table = {}
    for judge in ('A', 'B', 'C'):
        ids = [d for f in kit['files'] if f['judge'] == judge for d in f['device_ids']]
        d = root / JUDGES_REL / round_name / judge
        valid = sum((d / f'{i}.result.json').exists() for i in ids)
        if judge == 'C':
            tried = {i for f in kit['files'] if f['judge'] == 'C'
                     and next_attempt(d, Path(f['path']).name.split('.')[0]) > 1 for i in f['device_ids']}
        else:
            tried = {i for i in ids if next_attempt(d, i) > 1}
        invalid = sum(1 for i in ids if i in tried and not (d / f'{i}.result.json').exists())
        table[judge] = dict(valid=valid, invalid=invalid, missing=len(ids) - valid - invalid)
        out(f'{round_name} {judge}: valid={valid} invalid={invalid} missing={table[judge]["missing"]} (of {len(ids)})')
    return table


def main(argv=None, *, root=ROOT, prefix=(), out=print) -> int:
    ap = argparse.ArgumentParser(prog='python -m eval.s10.judge_runner', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--claude-exe')
    ap.add_argument('--codex-exe')
    ap.add_argument('--run-dir')
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('canary')
    c.add_argument('--judge', choices=FILE_JUDGES, required=True)
    c.add_argument('--dir', required=True)
    s = sub.add_parser('smoke')
    s.add_argument('--judge', choices=FILE_JUDGES, required=True)
    r = sub.add_parser('run')
    r.add_argument('--round', choices=('practice', 'eval', 'stability'), required=True)
    r.add_argument('--judge', choices=FILE_JUDGES, required=True)
    i = sub.add_parser('import-chat')
    i.add_argument('--round', choices=('practice', 'smoke', 'eval', 'stability'), required=True)
    i.add_argument('--batch', type=int, required=True)
    i.add_argument('--file', required=True)
    i.add_argument('--antigravity-version', required=True)
    i.add_argument('--model-label', required=True)
    t = sub.add_parser('status')
    t.add_argument('--round', choices=('practice', 'smoke', 'eval', 'stability'), required=True)
    args = ap.parse_args(argv)
    try:
        cfg = load_config(root)
        exe = {'A': args.claude_exe, 'B': args.codex_exe}
        if args.cmd == 'canary':
            if not exe[args.judge]:
                raise RunnerError('the tool path is required (--claude-exe / --codex-exe)')
            return canary(root, cfg, args.judge, exe[args.judge], args.dir, prefix, out)
        if args.cmd in ('smoke', 'run'):
            round_name = 'smoke' if args.cmd == 'smoke' else args.round
            if not args.run_dir:
                raise RunnerError('--run-dir is required')
            check_run_dir(args.run_dir, root)
            runner = Runner(root, cfg, args.judge, exe[args.judge], args.run_dir, prefix, out)
            runner.run_round(round_name)
            return 0
        if args.cmd == 'import-chat':
            import_chat(root, cfg, args.round, args.batch, args.file, args.antigravity_version, args.model_label, out)
            return 0
        status(root, args.round, out)
        return 0
    except RunnerStop as exc:
        out(f'STOP: {exc}')
        return 4
    except RunnerRefused as exc:
        out(f'REFUSED: {exc}')
        return 1
    except (RunnerError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        out(f'ERROR: {exc if isinstance(exc, RunnerError) else type(exc).__name__}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
