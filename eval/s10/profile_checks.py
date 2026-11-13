"""S10 eval profiles: contamination checks, seeded dev/holdout split, and the one-time seal.

Checks (per profile row; output is ids and counts only, never profile content):
  (a) device_name is not equal or near-equal to a known example/test/smoke device name;
  (b) no ISO part pattern and no endpoint/test word in device_name or comment;
  (c) device_name and a non-empty comment do not appear in the classifier PROMPT
      (component and material names are counted as info only).

Split: seed 'S10-split-v1'; strata in the order S1..S8 then trap, ids sorted inside a stratum;
1 row per stratum (2 for traps) goes to dev, the rest to holdout.

CLI:
  python -m eval.s10.profile_checks <draft.yaml> --dry-run
  python -m eval.s10.profile_checks <draft.yaml> --write --seal <holdout_manifest.json> --manifest-out <profiles_v1.json>
--write is write-once: it refuses to start if any output file exists, and it never deletes or
overwrites a file. The engine's evaluate, the tool, the classifier and every LLM are not run.
"""
import argparse
import copy
import difflib
import json
import platform
import random
import re
import sys
from datetime import date
from pathlib import Path

import yaml

from .formats import STRATA, read_yaml, validate_profile_set
from .split_guard import file_sha, seal_holdout

SPLIT_SEED = 'S10-split-v1'
MANIFEST_FORMAT = 's10-profiles-manifest.v1'
DEV_NAME, HOLDOUT_NAME = 'dev_profiles.yaml', 'holdout_profiles.yaml'
DEV_PER_STRATUM = {s: (2 if s == 'trap' else 1) for s in STRATA}
EXPECTED = dict(dev={s: DEV_PER_STRATUM[s] for s in STRATA},
                holdout={s: (4 if s == 'trap' else 2) for s in STRATA})
NEAR_RATIO = 0.85          # difflib ratio at or above this counts as near-equal
MIN_CHARS = 4              # shorter normalised strings are never tested as substrings
SMOKE_NAMES = ['Knee X', 'Skin electrode']                     # S9 smoke inputs
BANNED = ['10993-', 'iso 10993', 'cytotox', 'sensitiz', 'irritat', 'genotox', 'carcinogen', 'implantation',
          'hemocompat', 'pyrogen', 'systemic tox']
_NAME_LITERAL = re.compile(r'''device_name\s*=\s*(['"])(.+?)\1''')
ROOT = Path(__file__).resolve().parents[2]


class ProfileCheckError(RuntimeError):
    """The profile checks, the split or the write-once rule refused to continue."""


def normalise(text) -> str:
    """Lower case, punctuation to spaces, single spaces."""
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', str(text or '').lower()).split())


def reference_names(root=ROOT):
    """Device names a real profile must not copy: demo examples, test fakes, S9 smoke inputs."""
    from standards.demo import example_profiles
    examples = example_profiles()
    examples = examples.values() if isinstance(examples, dict) else examples
    names = [p['device_name'] for p in examples] + list(SMOKE_NAMES)
    tests = Path(root) / 'tests'
    for path in sorted(tests.rglob('*.py')):
        names += [m.group(2) for m in _NAME_LITERAL.finditer(path.read_text(encoding='utf-8'))]
    for path in sorted((tests / 'fixtures').rglob('*profiles*.yaml')):
        doc = yaml.safe_load(path.read_text(encoding='utf-8'))
        names += [e['profile']['device_name'] for e in (doc or {}).get('profiles', [])]
    return sorted({n for n in names if normalise(n)})


def near_equal(name, reference) -> bool:
    a, b = normalise(name), normalise(reference)
    if not a or not b:
        return False
    if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= NEAR_RATIO:
        return True
    short, long_ = sorted((a, b), key=len)
    # Whole-word containment, only for a name of two or more words (one word such as "test" is too loose).
    return len(short.split()) >= 2 and f' {short} ' in f' {long_} '


def _in_text(value, text) -> bool:
    value = normalise(value)
    return len(value) >= MIN_CHARS and value in text


def check_rows(rows, references, prompt):
    """Returns (fails {'a'|'b'|'c': [ids]}, info {'component_names_in_prompt', 'material_names_in_prompt'})."""
    prompt = normalise(prompt)
    fails = dict(a=[], b=[], c=[])
    info = dict(component_names_in_prompt=0, material_names_in_prompt=0)
    for row in rows:
        profile = row['profile']
        name, comment = profile.get('device_name') or '', row.get('comment') or ''
        if any(near_equal(name, ref) for ref in references):
            fails['a'].append(row['id'])
        low = f'{name}\n{comment}'.lower()
        if any(word in low for word in BANNED):
            fails['b'].append(row['id'])
        if _in_text(name, prompt) or _in_text(comment, prompt):
            fails['c'].append(row['id'])
        for comp in profile.get('components') or []:
            info['component_names_in_prompt'] += _in_text(comp.get('name'), prompt)
            info['material_names_in_prompt'] += sum(_in_text(m.get('name'), prompt) for m in comp.get('materials') or [])
    return fails, info


def split_ids(rows, seed=SPLIT_SEED):
    """Seeded split. Returns the set of dev ids; every other id is holdout."""
    rng = random.Random(seed)
    dev = set()
    for stratum in STRATA:
        ids = sorted(r['id'] for r in rows if r['stratum'] == stratum)
        if not ids:
            continue
        n = DEV_PER_STRATUM[stratum]
        if len(ids) <= n:
            raise ProfileCheckError(f'stratum {stratum}: {len(ids)} rows, need more than {n} to split')
        dev.update(rng.sample(ids, n))
    return dev


def split_docs(doc, dev_ids):
    """Two profile-set documents (dev, holdout), rows in id order, split set on file and rows."""
    out = {}
    for split in ('dev', 'holdout'):
        rows = [copy.deepcopy(r) for r in sorted(doc['profiles'], key=lambda r: r['id'])
                if (r['id'] in dev_ids) == (split == 'dev')]
        for row in rows:
            row['split'] = split
        out[split] = dict(format=doc['format'], split=split, profiles=rows)
    return out['dev'], out['holdout']


def count_table(docs):
    return {split: {s: sum(r['stratum'] == s for r in d['profiles']) for s in STRATA} for split, d in docs.items()}


def _write_once(path, text):
    with Path(path).open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(text)


def _dump(doc) -> str:
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False)


def prepare(draft_path, *, references=None, prompt=None, seed=SPLIT_SEED, data=None):
    """Validate the draft, run the checks, split. Nothing is written."""
    if references is None:
        references = reference_names()
    if prompt is None:
        from standards.classifier import PROMPT as prompt
    doc = read_yaml(draft_path)
    profiles = validate_profile_set(doc, data)
    fails, info = check_rows(doc['profiles'], references, prompt)
    dev_doc, holdout_doc = split_docs(doc, split_ids(doc['profiles'], seed))
    docs = dict(dev=dev_doc, holdout=holdout_doc)
    return dict(rows=len(profiles), fails=fails, info=info, docs=docs, counts=count_table(docs),
                references=len(references))


def report_lines(prep):
    n = prep['rows']
    lines = [f'validation: OK, {n} rows', f'reference device names: {prep["references"]}']
    for letter in 'abc':
        bad = prep['fails'][letter]
        lines.append(f'check ({letter}): pass {n - len(bad)} / fail {len(bad)}' + (f' | ids: {", ".join(bad)}' if bad else ''))
    lines.append('info (not failures): ' + ', '.join(f'{k} = {v}' for k, v in prep['info'].items()))
    lines.append('split      ' + ' '.join(f'{s:>4}' for s in STRATA) + '  total')
    for split, row in prep['counts'].items():
        lines.append(f'{split:<10} ' + ' '.join(f'{row[s]:>4}' for s in STRATA) + f'  {sum(row.values()):>5}')
    return lines


def write_and_seal(draft_path, prep, seal_path, manifest_out, *, seed=SPLIT_SEED, today=None, now=None, data=None):
    """Write dev + holdout, validate both, seal [holdout, draft], write the committed manifest.

    Every file is created with mode 'x' (never overwritten). Returns the committed manifest dict."""
    draft_path, seal_path, manifest_out = Path(draft_path), Path(seal_path), Path(manifest_out)
    dev_path, holdout_path = draft_path.parent / DEV_NAME, draft_path.parent / HOLDOUT_NAME
    _write_once(dev_path, _dump(prep['docs']['dev']))
    _write_once(holdout_path, _dump(prep['docs']['holdout']))
    for path in (dev_path, holdout_path):
        validate_profile_set(read_yaml(path), data)
    seal_holdout([holdout_path, draft_path], seal_path, now=now)      # the only hashing of the sealed files
    sealed = json.loads(seal_path.read_text(encoding='utf-8'))['files']
    rows = {DEV_NAME: len(prep['docs']['dev']['profiles']), HOLDOUT_NAME: len(prep['docs']['holdout']['profiles']),
            draft_path.name: prep['rows']}
    files = {DEV_NAME: dict(sha256_lf=file_sha(dev_path), split='dev', rows=rows[DEV_NAME], sealed=False)}
    for name, split in ((HOLDOUT_NAME, 'holdout'), (draft_path.name, 'draft (dev + holdout rows)')):
        files[name] = dict(sha256_lf=sealed[name], split=split, rows=rows[name], sealed=True)
    manifest = dict(format=MANIFEST_FORMAT, seed=seed, date=today or date.today().isoformat(),
                    python=platform.python_version(), seal_manifest=seal_path.name, files=files,
                    counts=prep['counts'])
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    _write_once(manifest_out, json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


def main(argv=None, *, references=None, prompt=None, out=print):
    ap = argparse.ArgumentParser(description='S10 profile contamination checks, seeded split and one-time seal')
    ap.add_argument('draft')
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--write', action='store_true')
    ap.add_argument('--seal', help='holdout seal manifest to create (write-once)')
    ap.add_argument('--manifest-out', help='committed profiles manifest to create (write-once)')
    ap.add_argument('--seed', default=SPLIT_SEED)
    args = ap.parse_args(argv)
    draft = Path(args.draft)
    outputs = []
    if args.write:
        if not args.seal or not args.manifest_out:
            ap.error('--write needs --seal and --manifest-out')
        outputs = [draft.parent / DEV_NAME, draft.parent / HOLDOUT_NAME, Path(args.seal), Path(args.manifest_out)]
        existing = [str(p) for p in outputs if p.exists()]
        if existing:
            out('REFUSED (write-once): already exists: ' + ', '.join(existing))
            return 4
    prep = prepare(draft, references=references, prompt=prompt, seed=args.seed)
    for line in report_lines(prep):
        out(line)
    if any(prep['fails'].values()):
        out('STOP: a check failed; nothing written')
        return 2
    if prep['counts'] != EXPECTED:
        out('STOP: split counts differ from the expected table; nothing written')
        return 2
    if args.dry_run:
        out('dry run: nothing written')
        return 0
    try:
        manifest = write_and_seal(draft, prep, args.seal, args.manifest_out, seed=args.seed)
    except Exception as exc:                         # report the type only; never delete or overwrite
        out(f'STOP: {type(exc).__name__} after writing started; not continuing. Files now present: '
            + ', '.join(str(p) for p in outputs if p.exists()))
        return 3
    out(f'seal manifest: {args.seal}')
    for name, entry in manifest['files'].items():
        out(f'sha256 (LF) {name}: {entry["sha256_lf"]}')
    out(f'python: {manifest["python"]} | seed: {manifest["seed"]} | date: {manifest["date"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
