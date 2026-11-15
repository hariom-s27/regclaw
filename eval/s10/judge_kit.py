"""S10b2 (S5): build the per-device judge prompts (judges A, B) and chat batch files (judge C).

Rounds:
  practice  PR-1..PR-10 (practice_profiles.yaml + practice_probes.yaml, both sha-checked); codebook v0.1
            (LF sha must equal s10b1_build.json) and JUDGE_INSTRUCTIONS_v0.1.md (must exist; sha recorded).
  smoke     SMOKE-1 only (fixed generic profile below); same codebook/instruction checks as practice.
  eval      (S10b2b) refused unless eval/s10/manifests/codebook_v1.json exists, every recorded sha matches and
            it records CODEBOOK_v1.0.md, JUDGE_INSTRUCTIONS_v1.0.md and the schema. Devices = judge_file_v1.yaml
            (LF sha must equal s10b1_build.json; id = J-id; items = the entry's items, fda-knee included).
            Judge C batches = data/gold/s10/draws/c_batches.json, which must equal draws.c_batches(sorted J-ids).
  stability (S10b2b-10) same checks; devices = the 6 of data/gold/s10/draws/stability_set.json (must equal
            draws.stability(judge file)), in draw order; every judge gets the device's FULL item list in the
            REVERSE of that judge's main-pass order; judge C: 2 batches (3 + 3) in draw order. The prompt does
            not show which items are scored.
Prompt = four blocks with fixed headers (JUDGE INSTRUCTIONS, CODEBOOK, DEVICE SHEET, YOUR TASK). A device
sheet holds id, contact_category, duration, the DeviceProfile input fields (existing_evidence and derived
dropped; no flags) and the items in this judge's seeded order: Random("S10b-v1|order|<judge>|<id>").
Judge C (practice, smoke): devices in id order, 5 per batch, one message per batch. Outputs (LF, UTF-8,
write-once per round): data/gold/s10/kits/<round>/{A,B}/<id>.prompt.md, C/batch_NN.prompt.md, kit_manifest.json.
No engine, tool, classifier or LLM is run. Prints counts and shas only.

Run from the repo root:  python -m eval.s10.judge_kit --round practice
Exit codes: 0 written · 1 refused (sha mismatch, eval round) · 2 input error · 3 output exists.
"""
import argparse
import copy
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from standards.ruleset import normalised_sha

from . import draws
from .judge_file import load_parts

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_VERSION = 's10-judge-prompt.v1'
KIT_FORMAT = 's10-judge-kit-manifest.v1'
SEED = 'S10b-v1'
ROUNDS = ('practice', 'smoke', 'eval', 'stability')
EVAL_ROUNDS = ('eval', 'stability')
STABILITY_C_SPLIT = (3, 3)
FILE_JUDGES = ('A', 'B')
CHAT_JUDGE = 'C'
BATCH_SIZE = 5

CODEBOOK_V01_REL = 'eval/s10/codebook/CODEBOOK_v0.1.md'
INSTRUCTIONS_V01_REL = 'eval/s10/codebook/JUDGE_INSTRUCTIONS_v0.1.md'
SCHEMA_REL = 'eval/s10/codebook/judge_output.schema.json'
BUILD_MANIFEST_REL = 'eval/s10/manifests/s10b1_build.json'
CODEBOOK_V1_MANIFEST_REL = 'eval/s10/manifests/codebook_v1.json'
CODEBOOK_V1_REL = 'eval/s10/codebook/CODEBOOK_v1.0.md'
INSTRUCTIONS_V1_REL = 'eval/s10/codebook/JUDGE_INSTRUCTIONS_v1.0.md'
JUDGE_FILE_REL = 'data/gold/s10/judge_file_v1.yaml'
PRACTICE_REL = 'data/gold/s10/practice_profiles.yaml'
PROBES_REL = 'data/gold/s10/practice_probes.yaml'
KITS_REL = 'data/gold/s10/kits'
PROBES_SHA256_LF = 'de70acab3a954b472217176a55abc1dcb4aae801c785c209ca059d2873f3490d'   # "probes OK", Part B
DROP_COMPONENT_KEYS = ('existing_evidence', 'derived')

H_INSTR, H_CODEBOOK, H_SHEET, H_TASK = ('=== JUDGE INSTRUCTIONS ===', '=== CODEBOOK ===', '=== DEVICE SHEET ===',
                                        '=== YOUR TASK ===')

# Fixed generic smoke profile (not a practice or eval device; name-checked in S10b2a Part C).
SMOKE_ENTRY = dict(
    id='SMOKE-1', contact_category='surface/intact_skin', duration='A',
    profile=dict(device_name='Reusable blood pressure cuff', target_markets=['US'], components=[dict(
        name='Cuff sleeve', contact_nature='surface', contact_site='intact_skin', contact_mode='direct',
        exposure=dict(pattern='single', hours_per_use=1),
        materials=[dict(name='Nylon cuff fabric', material_class='polymer')], sterilization='non_sterile')]))


class KitError(ValueError):
    """An input broke the kit rules (message carries no profile content)."""


class KitRefused(PermissionError):
    """A sha check or the round rule refused the build."""


def to_lf_text(raw: bytes) -> str:
    return raw.decode('utf-8-sig').replace('\r\n', '\n')


def device_key(device_id):
    """Natural id order: PR-2 before PR-10."""
    head, _, tail = str(device_id).rpartition('-')
    return (head, int(tail)) if tail.isdigit() else (str(device_id), -1)


def item_seed(judge, device_id):
    return f'{SEED}|order|{judge}|{device_id}'


def item_order(items, judge, device_id):
    out = list(items)
    random.Random(item_seed(judge, device_id)).shuffle(out)
    return out


def sheet_profile(profile):
    out = copy.deepcopy(profile)
    out.pop('derived', None)
    for comp in out.get('components', []):
        for key in DROP_COMPONENT_KEYS:
            comp.pop(key, None)
    return out


def device_sheet(entry, items):
    """id, contact_category, duration, profile input fields, items: no flags, no derived, no split."""
    return dict(id=entry['id'], contact_category=entry['contact_category'], duration=entry['duration'],
                profile=sheet_profile(entry['profile']), items=list(items))


def sheet_yaml(sheet) -> str:
    return yaml.safe_dump(sheet, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _strip(text):
    return text.strip('\n')


def device_prompt(instructions, codebook, sheet) -> str:
    n = len(sheet['items'])
    task = (f'Return only the JSON object for device {sheet["id"]}, labelling exactly these {n} items '
            f'in this order: {"; ".join(sheet["items"])}.')
    return '\n'.join([H_INSTR, _strip(instructions), '', H_CODEBOOK, _strip(codebook), '',
                      H_SHEET, _strip(sheet_yaml(sheet)), '', H_TASK, task]) + '\n'


def batch_prompt(instructions, codebook, sheets) -> str:
    parts = [H_INSTR, _strip(instructions), '', H_CODEBOOK, _strip(codebook), '']
    for k, sheet in enumerate(sheets, start=1):
        parts += [f'=== DEVICE SHEET {k} ===', _strip(sheet_yaml(sheet)), '']
    ids = ', '.join(s['id'] for s in sheets)
    task = (f'Return one JSON array with {len(sheets)} objects, one per device, in this order: {ids}. '
            'For each device, label exactly the items listed in its sheet, in the order listed.')
    return '\n'.join(parts + [H_TASK, task]) + '\n'


def _read(root, rel):
    path = Path(root) / rel
    if not path.is_file():
        raise KitError(f'missing input: {rel}')
    return path.read_bytes()


def _build_manifest_sha(root, rel):
    try:
        files = json.loads(_read(root, BUILD_MANIFEST_REL).decode('utf-8'))['files']
        return files[rel]['sha256_lf']
    except (ValueError, KeyError, TypeError) as exc:
        raise KitError(f'{BUILD_MANIFEST_REL} has no sha for {rel}') from exc


def check_eval_manifest(root):
    """Raise KitRefused unless codebook_v1.json exists and every recorded file sha (and the template) matches."""
    path = Path(root) / CODEBOOK_V1_MANIFEST_REL
    if not path.is_file():
        raise KitRefused(f'eval round refused: {CODEBOOK_V1_MANIFEST_REL} does not exist')
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
        files = manifest['files']
    except (ValueError, KeyError, TypeError) as exc:
        raise KitRefused(f'eval round refused: {CODEBOOK_V1_MANIFEST_REL} unreadable') from exc
    if manifest.get('template_version') != TEMPLATE_VERSION:
        raise KitRefused('eval round refused: template version differs from codebook_v1.json')
    for rel, info in files.items():
        p = Path(root) / rel
        if not p.is_file() or normalised_sha(p.read_bytes()) != info.get('sha256_lf'):
            raise KitRefused(f'eval round refused: {rel} missing or sha differs from codebook_v1.json')
    return manifest


def load_round(root, round_name, probes_sha):
    """Practice / smoke. Returns (entries in id order, inputs {name: {path, sha256_lf}}, instructions, codebook)."""
    if round_name not in ('practice', 'smoke'):
        raise KitError(f'unknown round {round_name!r}')
    cb_raw = _read(root, CODEBOOK_V01_REL)
    cb_sha = normalised_sha(cb_raw)
    if cb_sha != _build_manifest_sha(root, CODEBOOK_V01_REL):
        raise KitRefused('codebook v0.1 sha differs from s10b1_build.json')
    instr_raw = _read(root, INSTRUCTIONS_V01_REL)
    schema_raw = _read(root, SCHEMA_REL)
    inputs = dict(codebook=dict(path=CODEBOOK_V01_REL, sha256_lf=cb_sha),
                  instructions=dict(path=INSTRUCTIONS_V01_REL, sha256_lf=normalised_sha(instr_raw)),
                  schema=dict(path=SCHEMA_REL, sha256_lf=normalised_sha(schema_raw)))
    if round_name == 'smoke':
        entries = [SMOKE_ENTRY]
    else:
        pr_raw, probes_raw = _read(root, PRACTICE_REL), _read(root, PROBES_REL)
        pr_sha, probes_sha_got = normalised_sha(pr_raw), normalised_sha(probes_raw)
        if pr_sha != _build_manifest_sha(root, PRACTICE_REL):
            raise KitRefused('practice_profiles.yaml sha differs from s10b1_build.json')
        if probes_sha_got != probes_sha:
            raise KitRefused('practice_probes.yaml sha differs from the approved one')
        inputs.update(practice_profiles=dict(path=PRACTICE_REL, sha256_lf=pr_sha),
                      practice_probes=dict(path=PROBES_REL, sha256_lf=probes_sha_got))
        entries = []
        for raw in (pr_raw, probes_raw):
            doc = yaml.safe_load(to_lf_text(raw))
            entries += list(doc['profiles'])
        ids = [e['id'] for e in entries]
        if len(set(ids)) != len(ids):
            raise KitError('practice ids are not unique')
    entries = sorted(entries, key=lambda e: device_key(e['id']))
    return entries, inputs, to_lf_text(instr_raw), to_lf_text(cb_raw)


def main_order(entry, judge):
    """Main-pass (eval) item order of one judge for one device."""
    return item_order(entry['items'], judge, entry['id'])


def reversed_order(entry, judge):
    """Stability re-label order: the reverse of that judge's main-pass order (S10b2b-10)."""
    return list(reversed(main_order(entry, judge)))


def _draw_file(root, name):
    rel = f'{draws.DRAWS_REL}/{draws.OUT[name]}'
    raw = _read(root, rel)
    return json.loads(raw.decode('utf-8')), dict(path=rel, sha256_lf=normalised_sha(raw))


def load_eval_round(root, round_name):
    """eval / stability. Returns (devices, inputs, instructions, codebook, C batches [[id, ...]], order fn)."""
    manifest = check_eval_manifest(root)
    for rel in (CODEBOOK_V1_REL, INSTRUCTIONS_V1_REL, SCHEMA_REL):
        if rel not in manifest['files']:
            raise KitRefused(f'{round_name} round refused: {rel} is not recorded in {CODEBOOK_V1_MANIFEST_REL}')
    jf_raw = _read(root, JUDGE_FILE_REL)
    jf_sha = normalised_sha(jf_raw)
    if jf_sha != _build_manifest_sha(root, JUDGE_FILE_REL):
        raise KitRefused('judge file sha differs from s10b1_build.json')
    entries = [dict(id=e['j_id'], contact_category=e['contact_category'], duration=e['duration'],
                    profile=e['profile'], items=list(e['items'])) for e in yaml.safe_load(to_lf_text(jf_raw))['entries']]
    ids = [e['id'] for e in entries]
    if len(set(ids)) != len(ids):
        raise KitError('judge file J-ids are not unique')
    entries = sorted(entries, key=lambda e: device_key(e['id']))
    cb_raw, instr_raw, schema_raw = _read(root, CODEBOOK_V1_REL), _read(root, INSTRUCTIONS_V1_REL), _read(root, SCHEMA_REL)
    inputs = dict(codebook=dict(path=CODEBOOK_V1_REL, sha256_lf=normalised_sha(cb_raw)),
                  instructions=dict(path=INSTRUCTIONS_V1_REL, sha256_lf=normalised_sha(instr_raw)),
                  schema=dict(path=SCHEMA_REL, sha256_lf=normalised_sha(schema_raw)),
                  judge_file=dict(path=JUDGE_FILE_REL, sha256_lf=jf_sha))
    if round_name == 'eval':
        doc, info = _draw_file(root, 'c-batches')
        expect = draws.c_batches(ids)
        if doc.get('assignment') != expect:
            raise KitRefused('c_batches.json differs from the c-batches draw')
        inputs['c_batches'] = info
        devices, batches, order = entries, draws.batches_of(expect), main_order
    else:
        doc, info = _draw_file(root, 'stability')
        expect = draws.stability([dict(j_id=e['id'], items=e['items']) for e in entries])
        if doc.get('devices') != expect:
            raise KitRefused('stability_set.json differs from the stability draw')
        inputs['stability_set'] = info
        by_id = {e['id']: e for e in entries}
        devices = [by_id[d['j_id']] for d in expect]
        drawn, batches = [d['j_id'] for d in expect], []
        for size in STABILITY_C_SPLIT:
            batches.append(drawn[:size])
            drawn = drawn[size:]
        order = reversed_order
    return devices, inputs, to_lf_text(instr_raw), to_lf_text(cb_raw), batches, order


def _write_once(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    return normalised_sha(text.encode('utf-8'))


def build(root, round_name, *, probes_sha=PROBES_SHA256_LF, now=None):
    """Write the kit for one round. Returns the manifest dict."""
    root = Path(root)
    out_dir = root / KITS_REL / round_name
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f'{KITS_REL}/{round_name} already holds files (write-once)')
    if round_name in EVAL_ROUNDS:
        entries, inputs, instructions, codebook, c_groups, order = load_eval_round(root, round_name)
        items_source = JUDGE_FILE_REL
    else:
        entries, inputs, instructions, codebook = load_round(root, round_name, probes_sha)
        parts = load_parts(root)

        def order(e, judge):
            return item_order(parts, judge, e['id'])
        ids = [e['id'] for e in entries]
        c_groups = [ids[start:start + BATCH_SIZE] for start in range(0, len(ids), BATCH_SIZE)]
        items_source = 'standards/data/endpoint_to_part.v1.yaml'
    files = []

    def record(path, judge, sheets, text):
        sha = _write_once(path, text)
        files.append(dict(path=path.relative_to(root).as_posix(), judge=judge, sha256_lf=sha,
                          device_ids=[s['id'] for s in sheets],
                          item_order={s['id']: s['items'] for s in sheets},
                          seed_strings=[item_seed(judge, s['id']) for s in sheets]))

    for judge in FILE_JUDGES:
        for e in entries:
            sheet = device_sheet(e, order(e, judge))
            record(out_dir / judge / f'{e["id"]}.prompt.md', judge, [sheet],
                   device_prompt(instructions, codebook, sheet))
    sheets_c = {e['id']: device_sheet(e, order(e, CHAT_JUDGE)) for e in entries}
    for n, group in enumerate(c_groups, start=1):
        chunk = [sheets_c[d] for d in group]
        record(out_dir / CHAT_JUDGE / f'batch_{n:02d}.prompt.md', CHAT_JUDGE, chunk,
               batch_prompt(instructions, codebook, chunk))
    manifest = dict(format=KIT_FORMAT, round=round_name, template_version=TEMPLATE_VERSION,
                    seed_template=f'{SEED}|order|<judge>|<device_id>', items_source=items_source,
                    inputs=inputs, files=files, date=now or datetime.now(timezone.utc).isoformat())
    if round_name == 'eval':
        manifest.update(c_batch_rule='data/gold/s10/draws/c_batches.json (Random("S10b-v1|c-batches"))')
    elif round_name == 'stability':
        manifest.update(item_order_rule='reverse of the judge\'s main-pass order (seed_strings = main-pass seeds)',
                        c_batch_rule=f'stability draw order, batches of {"+".join(map(str, STABILITY_C_SPLIT))}')
    _write_once(out_dir / 'kit_manifest.json', json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    return manifest


def run(root=ROOT, round_name='practice', *, probes_sha=PROBES_SHA256_LF, now=None, out=print) -> int:
    root = Path(root)
    try:
        manifest = build(root, round_name, probes_sha=probes_sha, now=now)
    except FileExistsError as exc:
        out(f'REFUSED (write-once): {exc}')
        return 3
    except KitRefused as exc:
        out(f'REFUSED: {exc}; nothing written')
        return 1
    except (KitError, OSError, ValueError, KeyError, TypeError) as exc:
        out(f'ERROR: {exc if isinstance(exc, KitError) else type(exc).__name__}')
        return 2
    counts = {j: sum(f['judge'] == j for f in manifest['files']) for j in FILE_JUDGES + (CHAT_JUDGE,)}
    devices = len({d for f in manifest['files'] for d in f['device_ids']})
    out(f'round={round_name} devices={devices} files: A={counts["A"]} B={counts["B"]} C batches={counts["C"]}')
    for name, info in manifest['inputs'].items():
        out(f'input {name}: sha256_lf={info["sha256_lf"]}')
    kit_manifest = root / KITS_REL / round_name / 'kit_manifest.json'
    out(f'{kit_manifest.relative_to(root).as_posix()} sha256_lf={normalised_sha(kit_manifest.read_bytes())}')
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='python -m eval.s10.judge_kit', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--round', choices=ROUNDS, required=True)
    args = ap.parse_args(argv)
    return run(ROOT, args.round)


if __name__ == '__main__':
    sys.exit(main())
