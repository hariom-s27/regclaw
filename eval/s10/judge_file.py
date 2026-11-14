"""S10b-05/06: build the blinded judge file from the 30 eval profiles (one run, approved D20 / S10P-01).

Order of operations (see logs/S10b1_report.md, Part C plan):
  1. pre-flight: refuse (exit 3, nothing read or written) if any output exists; inputs must exist;
  2. dev + practice read; dev LF sha vs eval/s10/manifests/profiles_v1.json; practice LF sha must equal
     the approved Part B sha; DeviceProfile (pydantic only) on dev;
  3. holdout read ONCE (one read_bytes call); LF sha vs profiles_v1.json, printed as match/mismatch;
     a mismatch writes the ledger only (the holdout is not parsed);
  4. holdout parsed from the same bytes; DeviceProfile (pydantic only);
  5. duplicate check: practice device names vs the 30 eval names (profile_checks.near_equal, check (a));
     any hit writes the ledger only;
  6. J-001..J-030 by random.Random("S10b-v1").shuffle of the sorted P-ids;
  7. judge_file_v1.yaml; 8. jid_map.json, sealed at once with split_guard.seal_holdout (write-once);
  9. holdout_access_ledger.json, always written once the holdout has been read.
No engine, rule, tool, classifier or LLM is run on any profile. Prints counts, shas and match/mismatch only.

Run from the repo root:  python -m eval.s10.judge_file
Exit codes: 0 written · 1 aborted (sha mismatch or duplicate names) · 2 input error · 3 output exists.
"""
import copy
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from standards.profile import DeviceProfile
from standards.ruleset import normalised_sha

from .profile_checks import near_equal
from .split_guard import seal_holdout

ROOT = Path(__file__).resolve().parents[2]
SEED = 'S10b-v1'
JUDGE_FORMAT = 's10-judge-file.v1'
MAP_FORMAT = 's10-jid-map.v1'
LEDGER_FORMAT = 's10-holdout-access-ledger.v1'
REASON = 'S10b judge file (D20 / S10P-01)'
KNEE = 'fda-knee'

DEV_REL = 'data/gold/s10/dev_profiles.yaml'
HOLDOUT_REL = 'data/gold/s10/holdout_profiles.yaml'
PRACTICE_REL = 'data/gold/s10/practice_profiles.yaml'
PROFILES_MANIFEST_REL = 'eval/s10/manifests/profiles_v1.json'
PARTS_REL = 'standards/data/endpoint_to_part.v1.yaml'
JUDGE_REL = 'data/gold/s10/judge_file_v1.yaml'
MAP_REL = 'data/gold/s10/jid_map.json'
MAP_MANIFEST_REL = 'data/gold/s10/jid_map_manifest.json'
LEDGER_REL = 'data/gold/s10/holdout_access_ledger.json'
OUTPUTS = (JUDGE_REL, MAP_REL, MAP_MANIFEST_REL, LEDGER_REL)
PRACTICE_SHA256_LF = 'cfd11a7466f99f8d875fd852dbd223383b52821735fc341edbd35c21f430f9e9'
EXPECTED_ROWS = {'dev': 10, 'holdout': 20}
DROP_COMPONENT_KEYS = ('existing_evidence',)   # Hariom, Part C plan: excluded from the judge file


class JudgeFileError(ValueError):
    """An input broke the judge-file rules."""


def _read_holdout_once(path) -> bytes:
    """The only place the holdout file is read."""
    return Path(path).read_bytes()


def load_parts(root):
    doc = yaml.safe_load((Path(root) / PARTS_REL).read_text(encoding='utf-8'))
    parts = [p['part'] for p in doc['parts']]
    if len(parts) != 20 or len(set(parts)) != 20:
        raise JudgeFileError(f'part map must list 20 distinct parts (got {len(parts)})')
    return parts


def parse_eval(raw: bytes, split: str):
    """Parse one eval profile set from bytes; pydantic shape check only. Returns the entry list."""
    doc = yaml.safe_load(raw.decode('utf-8'))
    if not isinstance(doc, dict) or doc.get('split') != split or not isinstance(doc.get('profiles'), list):
        raise JudgeFileError(f'{split} file: wrong file-level split or no profiles')
    rows = doc['profiles']
    if len(rows) != EXPECTED_ROWS[split]:
        raise JudgeFileError(f'{split} file: {len(rows)} rows, expected {EXPECTED_ROWS[split]}')
    for row in rows:
        if row.get('split') != split:
            raise JudgeFileError(f'{split} file: a row has a different split')
        for key in ('id', 'contact_category', 'duration', 'profile'):
            if key not in row:
                raise JudgeFileError(f'{split} file: a row lacks {key}')
        try:
            DeviceProfile.model_validate(row['profile'])
        except Exception as exc:  # pydantic ValidationError; message not printed (may quote content)
            raise JudgeFileError(f'{split} file: a profile is not a valid DeviceProfile.v1') from exc
    return rows


def practice_names(raw: bytes):
    doc = yaml.safe_load(raw.decode('utf-8'))
    return [p['profile']['device_name'] for p in doc['profiles']]


def count_duplicates(practice, eval_names):
    """Number of (practice, eval) name pairs that are equal or near-equal (check (a))."""
    return sum(1 for p in practice for e in eval_names if near_equal(p, e))


def assign_jids(pids, seed=SEED):
    """{J-id: P-id}; J-ids J-001.. follow a seeded shuffle of the sorted P-ids."""
    order = sorted(pids)
    random.Random(seed).shuffle(order)
    return {f'J-{i:03d}': pid for i, pid in enumerate(order, start=1)}


def judge_profile(profile: dict) -> dict:
    """Input fields only: a copy of the raw DeviceProfile input without the excluded component blocks."""
    out = copy.deepcopy(profile)
    for comp in out.get('components', []):
        for key in DROP_COMPONENT_KEYS:
            comp.pop(key, None)
    return out


def judge_entry(j_id, row, parts):
    profile = row['profile']
    items = list(parts) + ([KNEE] if KNEE in (profile.get('device_specific_standards') or []) else [])
    return dict(j_id=j_id, contact_category=row['contact_category'], duration=row['duration'],
                profile=judge_profile(profile), items=items)


def _write_once(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    return normalised_sha(text.encode('utf-8'))


def _safe(exc) -> str:
    """Own messages carry no content; other errors (e.g. YAML parse) may quote the file, so name the type only."""
    return str(exc) if isinstance(exc, JudgeFileError) else type(exc).__name__


def _expected_sha(manifest, name):
    try:
        return manifest['files'][name]['sha256_lf']
    except (KeyError, TypeError) as exc:
        raise JudgeFileError(f'profiles manifest has no sha for {name}') from exc


def run(root=ROOT, now=None, practice_sha=PRACTICE_SHA256_LF, out=print) -> int:
    root = Path(root)
    p = {rel: root / rel for rel in (DEV_REL, HOLDOUT_REL, PRACTICE_REL, PROFILES_MANIFEST_REL) + OUTPUTS}
    existing = [rel for rel in OUTPUTS if p[rel].exists()]
    if existing:
        out('REFUSED (write-once): already exists: ' + ', '.join(existing))
        return 3
    missing = [rel for rel in (DEV_REL, HOLDOUT_REL, PRACTICE_REL, PROFILES_MANIFEST_REL) if not p[rel].is_file()]
    if missing:
        out('ERROR: missing input: ' + ', '.join(missing))
        return 2
    try:
        parts = load_parts(root)
        manifest = json.loads(p[PROFILES_MANIFEST_REL].read_text(encoding='utf-8'))
        dev_raw, practice_raw = p[DEV_REL].read_bytes(), p[PRACTICE_REL].read_bytes()
        dev_sha, pr_sha = normalised_sha(dev_raw), normalised_sha(practice_raw)
        dev_check = 'match' if dev_sha == _expected_sha(manifest, Path(DEV_REL).name) else 'mismatch'
        out(f'dev sha: {dev_check}')
        if dev_check != 'match':
            out('ABORTED: dev sha mismatch; holdout not read; nothing written')
            return 1
        if pr_sha != practice_sha:
            out('ABORTED: practice_profiles.yaml sha differs from the approved one; holdout not read; nothing written')
            return 1
        dev_rows = parse_eval(dev_raw, 'dev')
        practice = practice_names(practice_raw)
        holdout_expected = _expected_sha(manifest, Path(HOLDOUT_REL).name)
    except (JudgeFileError, OSError, ValueError, KeyError, TypeError) as exc:
        out(f'ERROR before the holdout read: {_safe(exc)}; nothing written')
        return 2

    ledger = dict(format=LEDGER_FORMAT, reason=REASON, script='eval/s10/judge_file.py', seed=SEED,
                  files_read=[], outputs_written=[], counts={}, outcome='aborted: unexpected error')
    code = 2
    try:
        holdout_raw = _read_holdout_once(p[HOLDOUT_REL])
        ho_sha = normalised_sha(holdout_raw)
        ho_check = 'match' if ho_sha == holdout_expected else 'mismatch'
        ledger['files_read'] = [
            dict(path=HOLDOUT_REL, sha256_lf=ho_sha, reads=1, expected_from=PROFILES_MANIFEST_REL, sha_check=ho_check),
            dict(path=DEV_REL, sha256_lf=dev_sha, reads=1, expected_from=PROFILES_MANIFEST_REL, sha_check=dev_check),
            dict(path=PRACTICE_REL, sha256_lf=pr_sha, reads=1, expected='approved Part B sha', sha_check='match')]
        out(f'holdout sha: {ho_check}')
        if ho_check != 'match':
            ledger['outcome'] = 'aborted: holdout sha mismatch (not parsed)'
            code = 1
            return code
        try:
            ho_rows = parse_eval(holdout_raw, 'holdout')
        except (JudgeFileError, ValueError, KeyError, TypeError) as exc:
            ledger['outcome'] = 'aborted: holdout input error'
            out(f'ERROR: {_safe(exc)}')
            return code
        rows = dev_rows + ho_rows
        pids = [r['id'] for r in rows]
        if len(set(pids)) != 30:
            ledger['outcome'] = 'aborted: P-ids not 30 distinct'
            out('ERROR: P-ids are not 30 distinct values')
            return code
        hits = count_duplicates(practice, [r['profile']['device_name'] for r in rows])
        ledger['counts'] = dict(dev=len(dev_rows), holdout=len(ho_rows), practice_names=len(practice),
                                eval_names=len(rows), duplicate_hits=hits)
        out(f'duplicate check: practice names={len(practice)} eval names={len(rows)} hits={hits}')
        if hits:
            ledger['outcome'] = 'aborted: duplicate names'
            code = 1
            return code

        jmap = assign_jids(pids)
        by_pid = {r['id']: r for r in rows}
        entries = [judge_entry(j, by_pid[pid], parts) for j, pid in jmap.items()]
        other_dss = sum(1 for r in rows for s in (r['profile'].get('device_specific_standards') or []) if s != KNEE)
        knee = sum(1 for e in entries if KNEE in e['items'])
        judge_doc = dict(format=JUDGE_FORMAT, seed=SEED, items_source=PARTS_REL, entries=entries)
        judge_sha = _write_once(p[JUDGE_REL], yaml.safe_dump(judge_doc, sort_keys=False, allow_unicode=True))
        ledger['outputs_written'].append(dict(path=JUDGE_REL, sha256_lf=judge_sha))
        map_text = json.dumps(dict(format=MAP_FORMAT, seed=SEED, map=jmap), indent=2) + '\n'
        map_sha = _write_once(p[MAP_REL], map_text)
        ledger['outputs_written'].append(dict(path=MAP_REL, sha256_lf=map_sha, sealed=True))
        seal_holdout([p[MAP_REL]], p[MAP_MANIFEST_REL], now=now)
        ledger['outputs_written'].append(dict(path=MAP_MANIFEST_REL,
                                              sha256_lf=normalised_sha(p[MAP_MANIFEST_REL].read_bytes())))
        n_items = sum(len(e['items']) for e in entries)
        ledger['counts'].update(entries=len(entries), items_total=n_items, knee_entries=knee,
                                other_device_specific_standards=other_dss)
        ledger['outcome'] = 'written'
        out(f'judge file: entries={len(entries)} items_total={n_items} knee_entries={knee} '
            f'other_device_specific_standards={other_dss}')
        out(f'{JUDGE_REL} sha256_lf={judge_sha}')
        out(f'{MAP_REL} written and sealed -> {MAP_MANIFEST_REL}')
        code = 0
        return code
    finally:
        ledger['timestamp_utc'] = now or datetime.now(timezone.utc).isoformat()
        ledger_sha = _write_once(p[LEDGER_REL], json.dumps(ledger, indent=2) + '\n')
        out(f'{LEDGER_REL} written (outcome: {ledger["outcome"]}) sha256_lf={ledger_sha}')


def main() -> int:
    return run()


if __name__ == '__main__':
    sys.exit(main())
