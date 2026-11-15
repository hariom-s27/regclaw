"""S10b2 judge runner, with FAKE executables (Python scripts in the pytest temp folder) and a FAKE repo root.

No real claude or codex binary is ever launched: the runner gets prefix=[sys.executable] and the fake script
path as the tool path.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from eval.s10 import judge_kit as JK
from eval.s10 import judge_runner as JR
from tests.test_s10b2_judge_kit import setup as kit_setup

REPO = Path(__file__).resolve().parents[1]
YN = ['eo_sterilised', 'sold_sterile', 'implant', 'blood_lymph_or_csf_contact', 'designed_to_degrade',
      'may_degrade_or_corrode', 'novel_material', 'nanomaterial', 'reproductive_concern_stated',
      'special_population_stated', 'knee_prosthesis']

FAKE = r'''
import json, os, pathlib, re, sys, time
ROLE = 'claude' if 'claude' in pathlib.Path(__file__).name else 'codex'
YN = %s
args = sys.argv[1:]
if args == ['--version']:
    print(os.environ.get('FAKE_VERSION', '2.1.325 (Claude Code)' if ROLE == 'claude' else 'codex-cli 0.157.1'))
    sys.exit(0)
if os.environ.get('FAKE_ARGV_LOG'):
    with open(os.environ['FAKE_ARGV_LOG'], 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(dict(argv=args, has_key='ANTHROPIC_API_KEY' in os.environ,
                                 env_set=os.environ.get('CLAUDE_CODE_DISABLE_AUTO_MEMORY'), cwd=os.getcwd())) + '\n')
prompt = sys.stdin.buffer.read().decode('utf-8')
mode = os.environ.get('FAKE_MODE', 'valid')
if mode == 'bad_once':
    p = pathlib.Path(os.environ['FAKE_STATE'])
    n = int(p.read_text()) if p.exists() else 0
    p.write_text(str(n + 1))
    mode = 'missing_item' if n %% 2 == 0 else 'valid'
if mode == 'sleep':
    time.sleep(30)
if mode == 'write_file':
    pathlib.Path('stray.txt').write_text('x')
if mode == 'nonzero':
    sys.stderr.write('error: option rejected\nmore\n')
    sys.exit(3)
canary = prompt.startswith('Do not use any tools')
res = None
if not canary:
    m = re.search(r'device (\S+), labelling exactly these \d+ items in this order: (.*)\.\s*$', prompt, re.M)
    dev, items = m.group(1), m.group(2).split('; ')
    res = dict(device_id=dev, checklist=dict(components=[dict(name='part', contact_category='c', duration='A',
               table_row='r')], **{k: 'no' for k in YN}, material_classes=['polymer']),
               cells=[dict(item=i, reason='FAKE-REASON', label='recommended', confidence='medium', section='§4.1',
                           needs_sme='') for i in items])
    if mode == 'missing_item':
        res['cells'].pop()
    if mode == 'extra_item':
        res['cells'].append(dict(res['cells'][0], item='ISO 99999'))
text = 'NONE\nNONE' if canary else json.dumps(res)
if ROLE == 'claude':
    usage = {'claude-opus-5-5': {}}
    if mode == 'haiku':
        usage['claude-haiku-4-5'] = {}
    if mode == 'wrong_model':
        usage = {'claude-sonnet-5-5': {}}
    tool = os.environ.get('FAKE_TOOL', 'FakeStructTool')
    if canary or mode in ('turn1_no_tool', 'turn2_no_tool'):
        tools = []
    elif mode == 'extra_tool':
        tools = [tool, tool]
    elif mode == 'wrong_tool':
        tools = ['Bash']
    else:
        tools = [tool]
    turns = dict(turn1_no_tool=1, turn2_no_tool=2, num_turns3=3).get(mode, 2 if tools else 1)
    ev = [dict(type='system', subtype='init')]
    if tools:
        ev.append(dict(type='assistant', message=dict(content=[dict(type='text', text='ok')] + [
            dict(type='tool_use', id=f'tu{k}', name=n, input=res or {}) for k, n in enumerate(tools)])))
        ev.append(dict(type='user', message=dict(content=[dict(type='tool_result', tool_use_id='tu0')])))
    ev.append(dict(type='assistant', message=dict(content=[dict(type='text', text=text)])))
    env = dict(type='result', subtype='error_max_turns' if mode == 'subtype' else 'success',
               is_error=(mode == 'is_error'), num_turns=turns, permission_denials=[], modelUsage=usage,
               result=text, usage=dict(input_tokens=10, output_tokens=20))
    if mode in ('subagent', 'subagent_ok'):
        env['subagent_stats'] = dict(spawned=1 if mode == 'subagent' else 0)
    if res is not None and mode != 'result_text':
        env['structured_output'] = res
    if mode != 'no_result_event':
        ev.append(env)
    print('\n'.join(json.dumps(e) for e in ev))
else:
    if '-o' in args:
        out = pathlib.Path(args[args.index('-o') + 1])
        out.write_text('not json' if mode == 'last_not_json' else text, encoding='utf-8')
    ev = [dict(type='thread.started'), dict(type='turn.started')]
    if mode == 'tool_event':
        ev.append(dict(type='item.completed', item=dict(type='command_execution')))
    ev.append(dict(type='item.completed', item=dict(type='agent_message', text=text)))
    if mode != 'no_turn':
        ev.append(dict(type='turn.completed', usage=dict(input_tokens=5, output_tokens=7)))
    print('\n'.join(json.dumps(e) for e in ev))
    model = 'gpt-5-other' if mode == 'wrong_model_stderr' else 'gpt-6-astra'
    if mode != 'no_model_line':
        sys.stderr.write(f'model: {model}\nreasoning effort: high\n')
''' % repr(YN)


FAKE_TOOL = 'FakeStructTool'          # test-only name; the real one comes from Hariom's smoke run


def set_struct_tool(cfg_path, name):
    cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8'))
    assert 'structured_output_tool' in cfg['judges']['A']
    cfg['judges']['A']['structured_output_tool'] = name
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding='utf-8')


@pytest.fixture
def env(tmp_path, monkeypatch):
    root, run = tmp_path / 'root', tmp_path / 'run'
    run.mkdir()
    probes = kit_setup(root)
    assert JK.run(root, 'practice', probes_sha=probes, now='t', out=lambda s: None) == 0
    assert JK.run(root, 'smoke', now='t', out=lambda s: None) == 0
    cfg = root / JR.CONFIG_REL
    cfg.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / JR.CONFIG_REL, cfg)
    set_struct_tool(cfg, FAKE_TOOL)
    fakes = tmp_path / 'fakes'
    fakes.mkdir()
    for name in ('fake_claude.py', 'fake_codex.py'):
        (fakes / name).write_text(FAKE, encoding='utf-8')
    argv_log = tmp_path / 'argv.jsonl'
    monkeypatch.setenv('FAKE_ARGV_LOG', str(argv_log))
    monkeypatch.setenv('FAKE_STATE', str(tmp_path / 'state.txt'))
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'fake-not-a-key')
    monkeypatch.delenv('FAKE_MODE', raising=False)
    monkeypatch.delenv('FAKE_VERSION', raising=False)
    return dict(root=root, run=run, claude=fakes / 'fake_claude.py', codex=fakes / 'fake_codex.py',
                argv=argv_log, tmp=tmp_path)


def go(e, *args, run_dir=None):
    lines = []
    code = JR.main(['--claude-exe', str(e['claude']), '--codex-exe', str(e['codex']),
                    '--run-dir', str(run_dir or e['run']), *args], root=e['root'], prefix=[sys.executable],
                   out=lines.append)
    return code, lines


def log_rows(e, rnd='smoke'):
    p = e['root'] / JR.JUDGES_REL / rnd / 'run_log.jsonl'
    return [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


def argv_rows(e):
    return [json.loads(l) for l in e['argv'].read_text(encoding='utf-8').splitlines()] if e['argv'].exists() else []


def jdir(e, judge, rnd='smoke'):
    return e['root'] / JR.JUDGES_REL / rnd / judge


def test_smoke_a_valid(env):
    code, lines = go(env, 'smoke', '--judge', 'A')
    assert code == 0
    d = jdir(env, 'A')
    assert sorted(p.name for p in d.iterdir()) == ['SMOKE-1.attempt1.stderr.txt', 'SMOKE-1.attempt1.stream.jsonl',
                                                  'SMOKE-1.result.json']
    rows = log_rows(env)
    assert len(rows) == 1 and rows[0]['valid'] and rows[0]['guard_ok'] and rows[0]['rule_names'] == []
    assert rows[0]['tool_use_names'] == [FAKE_TOOL]
    assert rows[0]['tool_version'] == '2.1.325 (Claude Code)' and rows[0]['model_observed'] == 'claude-opus-5-5'
    assert rows[0]['tokens'] == dict(input_tokens=10, output_tokens=20)
    assert len(rows[0]['prompt_sha256_lf']) == 64 and len(rows[0]['schema_sha256_lf']) == 64
    cfg = yaml.safe_load((env['root'] / JR.CONFIG_REL).read_text(encoding='utf-8'))
    a = argv_rows(env)[0]
    assert a['argv'][:-2] == cfg['judges']['A']['args'] and a['argv'][-2] == '--json-schema'
    assert json.loads(a['argv'][-1]) == json.loads((env['root'] / JK.SCHEMA_REL).read_text(encoding='utf-8'))
    assert a['argv'][a['argv'].index('--tools') + 1] == '' and a['argv'][a['argv'].index('--setting-sources') + 1] == ''
    assert a['argv'][a['argv'].index('--output-format') + 1] == 'stream-json' and '--verbose' in a['argv']
    assert a['has_key'] is False and a['env_set'] == '1'
    assert Path(a['cwd']).resolve() == env['run'].resolve()
    text = '\n'.join(lines)
    assert 'recommended' not in text and 'FAKE-REASON' not in text


def test_smoke_b_valid(env):
    code, _ = go(env, 'smoke', '--judge', 'B')
    assert code == 0
    names = sorted(p.name for p in jdir(env, 'B').iterdir())
    assert names == ['SMOKE-1.attempt1.events.jsonl', 'SMOKE-1.attempt1.last.json', 'SMOKE-1.attempt1.stderr.txt',
                     'SMOKE-1.result.json']
    cfg = yaml.safe_load((env['root'] / JR.CONFIG_REL).read_text(encoding='utf-8'))
    a = argv_rows(env)[0]['argv']
    assert a[:-5] == cfg['judges']['B']['args']
    assert a[-5] == '--output-schema' and Path(a[-4]) == env['root'] / JK.SCHEMA_REL
    assert a[-3] == '-o' and Path(a[-2]).name == 'SMOKE-1.attempt1.last.json' and a[-1] == '-'
    row = log_rows(env)[0]
    assert row['valid'] and row['model_observed'] == 'gpt-6-astra' and row['tokens'] == dict(input_tokens=5, output_tokens=7)


def test_smoke_b_no_model_line_is_pinned(env, monkeypatch):
    monkeypatch.setenv('FAKE_MODE', 'no_model_line')          # real codex 0.157.1 --json: empty stderr
    code, _ = go(env, 'smoke', '--judge', 'B')
    assert code == 0
    row = log_rows(env)[0]
    assert row['valid'] and row['guard_ok'] and row['guard_reasons'] == []
    assert row['model_observed'] == JR.B_MODEL_PINNED


def test_superseded_folders_ignored(env):
    junk_kit = env['root'] / JK.KITS_REL / '_superseded' / 'smoke_schema0' / 'kit_manifest.json'
    junk_kit.parent.mkdir(parents=True)
    junk_kit.write_text('not json', encoding='utf-8')
    junk_res = env['root'] / JR.JUDGES_REL / '_superseded' / 'smoke_schema0' / 'A' / 'SMOKE-1.result.json'
    junk_res.parent.mkdir(parents=True)
    junk_res.write_text('{}', encoding='utf-8')
    code, lines = go(env, 'status', '--round', 'smoke')
    assert code == 0 and 'smoke A: valid=0 invalid=0 missing=1 (of 1)' in lines
    code, _ = go(env, 'smoke', '--judge', 'B')
    assert code == 0 and (jdir(env, 'B') / 'SMOKE-1.result.json').exists()
    code, lines = go(env, 'status', '--round', 'smoke')
    assert 'smoke B: valid=1 invalid=0 missing=0 (of 1)' in lines
    assert junk_kit.read_text(encoding='utf-8') == 'not json' and junk_res.read_text(encoding='utf-8') == '{}'


@pytest.mark.parametrize('judge,mode,reasons,rules,valid', [
    ('A', 'is_error', ['is_error'], [], False),
    ('A', 'wrong_model', ['model_missing', 'model_other'], [], False),
    ('A', 'nonzero', ['exit_code', 'no_result_event'], [], False),
    ('A', 'missing_item', [], ['items_missing'], False),
    ('A', 'extra_item', [], ['items_extra'], False),
    ('A', 'haiku', [], [], True),
    ('A', 'result_text', ['structured_output_missing'], [], False),
    ('A', 'turn1_no_tool', [], [], True),
    ('A', 'turn2_no_tool', ['tool_use'], [], False),
    ('A', 'extra_tool', ['tool_use'], [], False),
    ('A', 'wrong_tool', ['tool_use'], [], False),
    ('A', 'num_turns3', ['num_turns'], [], False),
    ('A', 'subagent', ['subagents'], [], False),
    ('A', 'subagent_ok', [], [], True),
    ('A', 'subtype', ['subtype'], [], False),
    ('A', 'no_result_event', ['no_result_event'], [], False),
    ('B', 'tool_event', ['tool_use_event'], [], False),
    ('B', 'no_turn', ['no_turn_completed'], [], False),
    ('B', 'last_not_json', ['last_not_json'], [], False),
    ('B', 'wrong_model_stderr', ['wrong_model'], [], False),
    ('B', 'missing_item', [], ['items_missing'], False),
])
def test_guards_and_validator(env, monkeypatch, judge, mode, reasons, rules, valid):
    monkeypatch.setenv('FAKE_MODE', mode)
    code, lines = go(env, 'smoke', '--judge', judge)
    assert code == 0                                   # one device: retried once, marked, run continues
    rows = log_rows(env)
    assert len(rows) == (1 if valid else 2)
    assert all(r['guard_reasons'] == reasons and r['rule_names'] == rules and r['valid'] is valid for r in rows)
    assert (jdir(env, judge) / 'SMOKE-1.result.json').exists() is valid
    if not valid:
        assert any(p.name.startswith('SMOKE-1.attempt2.') for p in jdir(env, judge).iterdir())
        assert any('still invalid after retry' in l for l in lines)


def test_unpinned_tool_logs_names_and_skips_retry(env):
    set_struct_tool(env['root'] / JR.CONFIG_REL, None)
    code, lines = go(env, 'smoke', '--judge', 'A')
    assert code == 0
    rows = log_rows(env)
    assert len(rows) == 1                                           # no retry
    assert rows[0]['guard_reasons'] == ['structured_tool_unpinned'] and rows[0]['tool_use_names'] == [FAKE_TOOL]
    assert not rows[0]['valid'] and not (jdir(env, 'A') / 'SMOKE-1.result.json').exists()
    assert f'A SMOKE-1: tool_use names observed: {FAKE_TOOL}' in lines
    assert 'A SMOKE-1: structured_tool_unpinned, no retry' in lines
    assert 'A SMOKE-1: not retried (marked)' in lines and not any('still invalid after retry' in l for l in lines)
    assert len(argv_rows(env)) == 1


def test_real_config_pins_structured_tool():
    cfg = yaml.safe_load((REPO / JR.CONFIG_REL).read_text(encoding='utf-8'))
    assert cfg['judges']['A']['structured_output_tool'] == 'StructuredOutput'     # observed in Hariom's smoke A
    args = cfg['judges']['A']['args']
    assert args[args.index('--output-format') + 1] == 'stream-json' and '--verbose' in args


def test_wrong_version_stops_before_call(env, monkeypatch):
    monkeypatch.setenv('FAKE_VERSION', '2.1.326 (Claude Code)')
    code, lines = go(env, 'smoke', '--judge', 'A')
    assert code == 4 and any(l.startswith('STOP:') and 'version' in l for l in lines)
    assert not argv_rows(env) and not log_rows(env) and not jdir(env, 'A').exists()


def test_timeout(env, monkeypatch):
    p = env['root'] / JR.CONFIG_REL
    cfg = yaml.safe_load(p.read_text(encoding='utf-8'))
    cfg['timeout_s'] = 2
    p.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    monkeypatch.setenv('FAKE_MODE', 'sleep')
    code, _ = go(env, 'smoke', '--judge', 'A')
    rows = log_rows(env)
    assert code == 0 and len(rows) == 2
    assert all(r['guard_reasons'][0] == 'timeout' and r['exit_code'] is None and r['duration_s'] < 20 for r in rows)


def test_run_dir_written_stops(env, monkeypatch):
    monkeypatch.setenv('FAKE_MODE', 'write_file')
    code, lines = go(env, 'smoke', '--judge', 'A')
    assert code == 4 and any('run_dir_written' in l for l in lines)
    rows = log_rows(env)
    assert len(rows) == 1 and 'run_dir_written' in rows[0]['guard_reasons'] and not rows[0]['valid']


def test_run_dir_rules(env):
    inside = env['root'] / 'rundir'
    inside.mkdir()
    assert go(env, 'smoke', '--judge', 'A', run_dir=inside)[0] == 4
    assert go(env, 'smoke', '--judge', 'A', run_dir=env['tmp'] / 'nope')[0] == 4
    (env['run'] / 'x.txt').write_text('x')
    code, lines = go(env, 'smoke', '--judge', 'A')
    assert code == 4 and any('not empty' in l for l in lines)
    assert not argv_rows(env)


def test_two_devices_invalid_in_a_row_stop(env, monkeypatch):
    monkeypatch.setenv('FAKE_MODE', 'missing_item')
    code, lines = go(env, 'run', '--round', 'practice', '--judge', 'A')
    rows = log_rows(env, 'practice')
    assert code == 4 and len(rows) == 4
    assert [r['device_id'] for r in rows] == ['PR-1', 'PR-1', 'PR-2', 'PR-2']
    assert any('2 devices in a row' in l for l in lines)


def test_retry_then_valid_and_resume(env, monkeypatch):
    monkeypatch.setenv('FAKE_MODE', 'bad_once')
    code, lines = go(env, 'run', '--round', 'practice', '--judge', 'B')
    assert code == 0
    rows = log_rows(env, 'practice')
    assert len(rows) == 20 and sum(r['valid'] for r in rows) == 10
    d = jdir(env, 'B', 'practice')
    assert len(list(d.glob('*.result.json'))) == 10 and len(list(d.glob('*.attempt1.events.jsonl'))) == 10
    code, lines = go(env, 'run', '--round', 'practice', '--judge', 'B')
    assert code == 0 and len(log_rows(env, 'practice')) == 20
    assert any('skipped_already_valid=10' in l for l in lines)


def test_eval_round_refused(env, tmp_path):
    assert go(env, 'run', '--round', 'eval', '--judge', 'A')[0] == 1
    assert go(env, 'status', '--round', 'eval')[0] == 1
    paste = tmp_path / 'p.txt'
    paste.write_text('[]', encoding='utf-8')
    assert go(env, 'import-chat', '--round', 'eval', '--batch', '1', '--file', str(paste),
              '--antigravity-version', '1.0', '--model-label', 'Gemini 3.8 Flash (High)')[0] == 1
    assert not argv_rows(env)


def c_results(e, batch=1):
    kit = json.loads((e['root'] / JK.KITS_REL / 'practice' / 'kit_manifest.json').read_text(encoding='utf-8'))
    entry = [f for f in kit['files'] if f['judge'] == 'C'][batch - 1]
    return [dict(device_id=d, checklist=dict(components=[dict(name='p', contact_category='c', duration='A',
                                                              table_row='r')],
                                             **{k: 'no' for k in YN}, material_classes=[]),
                 cells=[dict(item=i, reason='r', label='mandatory', confidence='low', section='§6', needs_sme='')
                        for i in entry['item_order'][d]]) for d in entry['device_ids']]


def test_import_chat(env, tmp_path):
    arr = c_results(env)
    arr[2]['cells'].pop()
    paste = tmp_path / 'paste.txt'
    paste.write_text('```json\n' + json.dumps(arr) + '\n```\n', encoding='utf-8')
    args = ['import-chat', '--round', 'practice', '--batch', '1', '--file', str(paste),
            '--antigravity-version', '1.2.3', '--model-label', 'Gemini 3.8 Flash (High)']
    code, lines = go(env, *args)
    assert code == 0 and any('valid=4 invalid=1 already_valid=0 | fence_stripped' in l for l in lines)
    d = jdir(env, 'C', 'practice')
    assert sorted(p.name for p in d.glob('*.result.json')) == [f'PR-{i}.result.json' for i in (1, 2, 4, 5)]
    assert (d / 'batch_01.attempt1.paste.txt').read_bytes() == paste.read_bytes()
    rows = log_rows(env, 'practice')
    assert len(rows) == 5 and all(r['guard_reasons'] == ['fence_stripped'] and r['tool_version'] == '1.2.3'
                                  and r['model_observed'] == 'Gemini 3.8 Flash (High)' for r in rows)
    assert rows[2]['rule_names'] == ['items_missing']
    assert 'mandatory' not in '\n'.join(lines)
    code, lines = go(env, *args)
    assert code == 0 and any('valid=0 invalid=1 already_valid=4' in l for l in lines)
    assert (d / 'batch_01.attempt2.paste.txt').exists()


def test_import_chat_smoke_round(env, tmp_path):
    kit = json.loads((env['root'] / JK.KITS_REL / 'smoke' / 'kit_manifest.json').read_text(encoding='utf-8'))
    entry = [f for f in kit['files'] if f['judge'] == 'C'][0]
    assert entry['device_ids'] == ['SMOKE-1']
    arr = [dict(device_id='SMOKE-1', checklist=dict(components=[dict(name='p', contact_category='c', duration='A',
                                                                     table_row='r')],
                                                    **{k: 'no' for k in YN}, material_classes=[]),
                cells=[dict(item=i, reason='r', label='recommended', confidence='low', section='§4.2, §5.1',
                            needs_sme='') for i in entry['item_order']['SMOKE-1']])]
    paste = tmp_path / 'smoke.txt'
    paste.write_bytes('﻿'.encode('utf-8') + json.dumps(arr).encode('utf-8'))       # UTF-8 with BOM
    code, lines = go(env, 'import-chat', '--round', 'smoke', '--batch', '1', '--file', str(paste),
                     '--antigravity-version', '1.0', '--model-label', 'Gemini 3.8 Flash (High)')
    assert code == 0 and 'C batch_01: valid=1 invalid=0 already_valid=0' in lines
    assert (jdir(env, 'C') / 'SMOKE-1.result.json').exists()
    code, lines = go(env, 'status', '--round', 'smoke')
    assert 'smoke C: valid=1 invalid=0 missing=0 (of 1)' in lines


def test_import_chat_bad_inputs(env, tmp_path):
    paste = tmp_path / 'bad.txt'
    paste.write_text('Here is the answer: not json', encoding='utf-8')
    base = ['import-chat', '--round', 'practice', '--batch', '2', '--file', str(paste), '--antigravity-version', '1']
    code, lines = go(env, *base, '--model-label', 'Gemini 3.8 Flash (High)')
    assert code == 0 and all(r['rule_names'] == ['batch_not_json'] for r in log_rows(env, 'practice'))
    paste.write_text(json.dumps(c_results(env, 2)[:4]), encoding='utf-8')
    go(env, *base, '--model-label', 'Gemini 3.8 Flash (High)')
    assert log_rows(env, 'practice')[-1]['rule_names'] == ['batch_length']
    assert go(env, *base, '--model-label', 'Other model')[0] == 1
    assert go(env, *base[:4], '9', *base[5:], '--model-label', 'Gemini 3.8 Flash (High)')[0] == 2


def test_status(env, monkeypatch):
    go(env, 'smoke', '--judge', 'A')
    monkeypatch.setenv('FAKE_MODE', 'missing_item')
    go(env, 'smoke', '--judge', 'B')
    code, lines = go(env, 'status', '--round', 'smoke')
    assert code == 0
    assert lines == ['smoke A: valid=1 invalid=0 missing=0 (of 1)', 'smoke B: valid=0 invalid=1 missing=0 (of 1)',
                     'smoke C: valid=0 invalid=0 missing=1 (of 1)']


@pytest.mark.parametrize('judge', ['A', 'B'])
def test_canary(env, judge):
    cdir = env['tmp'] / 'canary'
    cdir.mkdir()
    (cdir / 'AGENTS.md').write_text('codeword note', encoding='utf-8')     # non-empty folder is allowed
    lines = []
    code = JR.main(['--claude-exe', str(env['claude']), '--codex-exe', str(env['codex']), 'canary', '--judge', judge,
                    '--dir', str(cdir)], root=env['root'], prefix=[sys.executable], out=lines.append)
    assert code == 0
    assert f'{judge} line 1: NONE' in lines and f'{judge} line 2: NONE' in lines and f'{judge} guard: ok' in lines
    cfg = yaml.safe_load((env['root'] / JR.CONFIG_REL).read_text(encoding='utf-8'))
    a = argv_rows(env)[0]['argv']
    assert a == cfg['judges'][judge]['args'] + (['-'] if judge == 'B' else [])
    assert sorted(p.name for p in cdir.iterdir()) == ['AGENTS.md']
    assert not (env['root'] / JR.JUDGES_REL).exists()


def test_canary_rejected_flag_reports_stderr(env, monkeypatch):
    monkeypatch.setenv('FAKE_MODE', 'nonzero')
    cdir = env['tmp'] / 'canary'
    cdir.mkdir()
    lines = []
    code = JR.main(['--claude-exe', str(env['claude']), 'canary', '--judge', 'A', '--dir', str(cdir)],
                   root=env['root'], prefix=[sys.executable], out=lines.append)
    assert code == 4 and 'A stderr first line: error: option rejected' in lines


def test_child_env(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'x')
    monkeypatch.setenv('GEMINI_API_KEY', 'x')
    cfg = yaml.safe_load((REPO / JR.CONFIG_REL).read_text(encoding='utf-8'))
    env = JR.child_env(cfg, 'A')
    assert not {'OPENAI_API_KEY', 'GEMINI_API_KEY'} & {k.upper() for k in env}
    assert env['CLAUDE_CODE_DISABLE_CLAUDE_MDS'] == '1' and env['CLAUDE_CODE_DISABLE_AUTO_MEMORY'] == '1'
    assert 'CLAUDE_CODE_DISABLE_CLAUDE_MDS' not in JR.child_env(cfg, 'B')


def test_config_matches_approved_lists():
    cfg = yaml.safe_load((REPO / JR.CONFIG_REL).read_text(encoding='utf-8'))
    assert cfg['judges']['A']['args'] == ['-p', '--output-format', 'stream-json', '--verbose', '--model',
                                          'claude-opus-5-5', '--effort', 'high', '--tools', '', '--setting-sources', '',
                                          '--disable-slash-commands', '--strict-mcp-config', '--no-session-persistence']
    assert cfg['judges']['B']['args'] == ['exec', '--skip-git-repo-check', '--ephemeral', '-s', 'read-only', '-m',
                                          'gpt-6-astra', '-c', 'model_reasoning_effort=high', '-c',
                                          'project_doc_max_bytes=0', '-c', 'web_search=disabled', '--json']
    assert cfg['judges']['A']['expected_version'] == '2.1.325 (Claude Code)'
    assert cfg['judges']['B']['expected_version'] == 'codex-cli 0.157.1'
    assert cfg['timeout_s'] == 1800 and cfg['max_consecutive_failures'] == 2
    assert cfg['env_remove'] == ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'OPENAI_API_KEY', 'CODEX_API_KEY',
                                 'GEMINI_API_KEY']
    assert cfg['canary_question'] == (
        'Do not use any tools and do not read any files. Answer on two lines. Line 1: if any instruction or project '
        'note given to you before this message contains a codeword, write only that codeword, otherwise write NONE. '
        'Line 2: list the names of any skills or MCP servers you can use, or write NONE.')
    assert cfg['judges']['C'] == dict(tool='antigravity-chat', model_label='Gemini 3.8 Flash (High)')
    for word in ('restricted', 'ignore-user-config'):
        assert not any(word in a for j in ('A', 'B') for a in cfg['judges'][j]['args'])
