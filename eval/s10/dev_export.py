"""Write-once S10c dev reference export; real runs are performed separately.

Only the fixed input paths below are read. Blinded associations live in memory;
outputs contain dev P-ids or aggregates, never J-ids or unmatched entry content.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys

import jsonschema
import yaml

from standards import ruleset
from standards.ruleset import normalised_sha
from .addendum_freeze import SEALED_NAMES
from . import draws as D
from . import formats as F
from . import judge_file as JF
from . import label_freeze as LF
from . import label_quality as Q
from . import second_rater as SR
from .merge import JUDGES, LABELS, cell_id

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_FILE_NAMES = SEALED_NAMES | frozenset({'.env', Path(JF.MAP_REL).name, Path(JF.HOLDOUT_REL).name})
PROFILES_MANIFEST_REL = JF.PROFILES_MANIFEST_REL
BUILD_REL = D.BUILD_MANIFEST_REL
CELLS_REL = 'data/gold/s10/dev/dev_cells.json'
GOLD_REL = 'data/gold/s10/dev/gold_dev_v2.yaml'
SUMMARY_REL = 'data/gold/s10/dev/export_summary.json'
MANIFEST_REL = 'eval/s10/manifests/s10c_dev_export.json'
OUTPUTS = (CELLS_REL, GOLD_REL, SUMMARY_REL, MANIFEST_REL)
INPUTS = (JF.DEV_REL, PROFILES_MANIFEST_REL, JF.JUDGE_REL, BUILD_REL,
          D.HUMAN_REL, Q.HUMAN_MANIFEST_REL, SR.LABELS_REL, SR.MANIFEST_REL,
          SR.DRAW_REL, SR.SHEET_REL, D.LIST_REL, D.MERGE_REL, JF.PARTS_REL)
SUMMARY_FORMAT = 's10c-dev-export-summary.v1'
MANIFEST_FORMAT = 's10c-dev-export-manifest.v1'
REFERENCE_METHOD = F.GOLD_REFERENCE_V2['properties']['method']['const']
EXPECTED_E1_DISPLAY = '0.0149'
EXPECTED_E1_POPULATION = 298
EXPECTED_E2 = {'x': 1, 'n': 126}
# G-09: merge_v1.json LF sha frozen at A2 signing (prefix and suffix as approved).
MERGE_SHA_PREFIX = '08616cf9'
MERGE_SHA_SUFFIX = 'ad903'


class ExportError(ValueError):
    """Only a fixed check name and aggregate failure count cross the boundary."""

    def __init__(self, check, count=1, code=2):
        self.check, self.count, self.code = check, count, code
        super().__init__(check)


def _require(condition, check, count=1, code=2):
    if not condition:
        raise ExportError(check, count, code)


def fixed_path(root, rel):
    root = Path(root).resolve()
    path = root / rel
    resolved = path.resolve()
    names = FORBIDDEN_FILE_NAMES
    _require(not any(part.casefold() in names for part in (*path.parts, *resolved.parts)), 'forbidden_path')
    _require(resolved.is_relative_to(root), 'path_boundary')
    return path


def preflight(root):
    # Existing outputs take precedence over all input reads and directory creation.
    count = sum((Path(root) / rel).exists() or (Path(root) / rel).is_symlink() for rel in OUTPUTS)
    _require(not count, 'output_exists', count, 3)
    for rel in OUTPUTS + INPUTS:
        fixed_path(root, rel)


def _check_sha(raw, expected, check):
    _require(isinstance(expected, str) and re.fullmatch(r'[0-9a-f]{64}', expected) is not None
             and normalised_sha(raw) == expected, check, code=1)


def check_audit_judge_labels(rows, audit):
    """Count invalid judge-cell labels before the locked report loader can reject them."""
    by_id = {}
    for row in rows:
        _require(isinstance(row, dict) and isinstance(row.get('cell_id'), str), 'merge_structure')
        _require(row['cell_id'] not in by_id, 'merge_duplicate')
        by_id[row['cell_id']] = row
    _require(len(audit) >= LF.UPTO, 'audit_prefix')
    invalid = _invalid_judge_labels(by_id, [cell_id(cell['j_id'], cell['item']) for cell in audit[:LF.UPTO]])
    _require(not invalid, 'audit_judge_labels', invalid)


def _invalid_judge_labels(by_id, ids):
    invalid = 0
    for cid in ids:
        row = by_id.get(cid)
        labels = row.get('labels') if row is not None else None
        invalid += sum(not isinstance(labels, dict) or labels.get(judge) not in LABELS for judge in JUDGES)
    return invalid


def load_inputs(root):
    """Load fixed inputs without changing the locked loaders' validation.

    Exception inventory for D.load_audit_rows, D.load_merge,
    Q.load_report_inputs, JF.parse_eval and JF.load_parts:
    - custom: D.DrawError, Q.QualityError, SR.FriendError, LF.FreezeError,
      JF.JudgeFileError (all ValueError subclasses);
    - I/O: OSError and its subclasses, including PermissionError,
      FileNotFoundError, FileExistsError, IsADirectoryError, NotADirectoryError;
    - parsing: UnicodeError (including UnicodeDecodeError), ValueError
      (including json.JSONDecodeError), yaml.YAMLError and csv.Error;
    - malformed shape/arithmetic: KeyError, IndexError, TypeError,
      AttributeError, OverflowError, ZeroDivisionError;
    - parser/runtime limits: RuntimeError (including RecursionError), MemoryError.
    LF.checked_rows wraps LabelToolError/UnicodeError/csv.Error in FreezeError;
    JF.parse_eval wraps pydantic.ValidationError in JudgeFileError.
    These functions do not explicitly exit; SystemExit is contained defensively.
    run catches every Exception subclass plus SystemExit, so unexpected loader
    errors also cannot expose input content. KeyboardInterrupt is not swallowed.
    """
    raw = {rel: fixed_path(root, rel).read_bytes() for rel in INPUTS}
    shas = {rel: normalised_sha(value) for rel, value in raw.items()}
    # G-09: refuse before any parsing unless the merge file is the A2-signed one.
    _require(shas[D.MERGE_REL].startswith(MERGE_SHA_PREFIX) and shas[D.MERGE_REL].endswith(MERGE_SHA_SUFFIX),
             'merge_sha', code=1)
    profiles_manifest = json.loads(raw[PROFILES_MANIFEST_REL])
    build = json.loads(raw[BUILD_REL])
    _require(profiles_manifest.get('format') == 's10-profiles-manifest.v1', 'profiles_manifest')
    _require(build.get('format') == 's10b1-build-manifest.v1', 'build_manifest')
    _check_sha(raw[JF.DEV_REL], profiles_manifest['files'][Path(JF.DEV_REL).name]['sha256_lf'], 'dev_sha')
    _check_sha(raw[JF.JUDGE_REL], build['files'][JF.JUDGE_REL]['sha256_lf'], 'judge_sha')
    human_manifest = json.loads(raw[Q.HUMAN_MANIFEST_REL])
    friend_manifest = json.loads(raw[SR.MANIFEST_REL])
    _check_sha(raw[D.HUMAN_REL], human_manifest['sha256_lf'], 'human_sha')
    _check_sha(raw[SR.LABELS_REL], friend_manifest['sha256_lf'], 'friend_sha')

    dev_doc = yaml.safe_load(raw[JF.DEV_REL].decode('utf-8'))
    jsonschema.Draft202012Validator(F.PROFILE_SET_SCHEMA).validate(dev_doc)
    dev = JF.parse_eval(raw[JF.DEV_REL], 'dev')
    _require(len({row['id'] for row in dev}) == len(dev), 'dev_duplicate', code=1)
    judge_doc = yaml.safe_load(raw[JF.JUDGE_REL].decode('utf-8'))
    _require(isinstance(judge_doc, dict) and judge_doc.get('format') == JF.JUDGE_FORMAT, 'judge_format')
    entries = judge_doc['entries']
    _require(isinstance(entries, list), 'judge_structure')
    parts = JF.load_parts(root)

    # No normalization or repair: invalid audit labels exit 2 with their count.
    audit = D.load_audit_rows(root)
    check_audit_judge_labels(D.load_merge(root), audit)
    draw, friend, merged, hariom = Q.load_report_inputs(root)
    _require(not any(row['routed'] for row in merged), 'merge_routed', code=1)
    report = Q.build_report(draw, friend, merged, hariom)
    if report['p21']['exit_code']:
        raise ExportError('label_quality_guard', code=report['p21']['exit_code'])
    return dict(dev=dev, entries=entries, parts=parts, draw=draw, friend=friend,
                merged=merged, hariom=hariom, report=report, input_shas=shas)


def _content_key(contact_category, duration, profile):
    return json.dumps(dict(contact_category=contact_category, duration=duration, profile=profile),
                      sort_keys=True, separators=(',', ':'))


def match_dev_entries(dev, entries, parts):
    index = defaultdict(list)
    jids = []
    for entry in entries:
        _require(isinstance(entry, dict) and isinstance(entry.get('j_id'), str), 'judge_structure')
        jids.append(entry['j_id'])
        index[_content_key(entry['contact_category'], entry['duration'], entry['profile'])].append(entry)
    _require(len(set(jids)) == len(jids), 'judge_duplicate', code=1)
    matches, used = {}, set()
    for row in dev:
        key = _content_key(row['contact_category'], row['duration'], JF.judge_profile(row['profile']))
        found = index.get(key, [])
        _require(len(found) == 1, 'dev_match', code=1)
        entry = found[0]
        _require(entry['j_id'] not in used, 'dev_match_reused', code=1)
        expected = parts + ([JF.KNEE] if JF.KNEE in row['profile'].get('device_specific_standards', []) else [])
        _require(entry['items'] == expected, 'dev_items', code=1)
        used.add(entry['j_id'])
        matches[row['id']] = entry
    unmatched = len(entries) - len(used)
    _require(len(matches) == JF.EXPECTED_ROWS['dev'] and unmatched == JF.EXPECTED_ROWS['holdout'],
             'match_counts', code=1)
    return matches, unmatched


def build_dev_cells(dev, matches, merged, hariom, friend):
    by_id = {row['cell_id']: row for row in merged}
    cells = []
    for profile in sorted(dev, key=lambda row: row['id']):
        entry = matches[profile['id']]
        for item in entry['items']:
            cid = cell_id(entry['j_id'], item)
            _require(cid in by_id, 'dev_merge_coverage', code=1)
            row = by_id[cid]
            cells.append(dict(profile_id=profile['id'], stratum=profile['stratum'], item=item,
                              final=row['final'], routed=row['routed'],
                              split_cell=len(set(row['labels'].values())) > 1,
                              labels=dict(row['labels']), human=hariom.get(cid),
                              friend=friend[cid]['label'] if cid in friend else None))
    return cells


def audit_gaps(merged, hariom):
    """Binary mandatory/not-mandatory gaps on every frozen Audit-A cell.

    Split cells use each judge's recorded label, exactly like unanimous cells;
    merge.final never substitutes for a judge's vote in these comparisons.
    """
    by_id = {row['cell_id']: row for row in merged}
    invalid = _invalid_judge_labels(by_id, hariom)
    _require(not invalid, 'audit_judge_labels', invalid)
    _require(all(label in LABELS for label in hariom.values()), 'audit_human_labels')
    mandatory = sum(label == 'mandatory' for label in hariom.values())
    result = {}
    for judge in JUDGES:
        under = sum(human == 'mandatory' and by_id[cid]['labels'][judge] != 'mandatory'
                    for cid, human in hariom.items())
        over = sum(human != 'mandatory' and by_id[cid]['labels'][judge] == 'mandatory'
                   for cid, human in hariom.items())
        result[judge] = dict(under_call=dict(x=under, n=mandatory),
                             over_call=dict(x=over, n=len(hariom) - mandatory))
    return result


def check_report_checkpoint(report):
    e1, e2 = report['e1_input'], report['e2_pool']
    rate = e1['weighted_binary_rate_on_final_mandatory']
    checks = (not e1['undefined'] and isinstance(rate, (int, float)) and format(rate, '.4f') == EXPECTED_E1_DISPLAY,
              e1['population'] == EXPECTED_E1_POPULATION, e2 == EXPECTED_E2)
    _require(all(checks), 'label_quality_checkpoint', sum(not check for check in checks), 1)


def build_export_summary(inputs, cells, unmatched):
    by_stratum = {}
    for stratum in sorted({row['stratum'] for row in inputs['dev']}):
        subset = [cell for cell in cells if cell['stratum'] == stratum]
        counts = Counter(cell['final'] for cell in subset)
        by_stratum[stratum] = dict(profiles=sum(row['stratum'] == stratum for row in inputs['dev']),
                                   cells=len(subset), by_class={label: counts[label] for label in LABELS})
    counts = Counter(cell['final'] for cell in cells)
    cardinalities = dict(dev_profiles=len(inputs['dev']), matched_entries=len(inputs['dev']),
                         unmatched_entries=unmatched, dev_cells=len(cells), audit_cells=len(inputs['hariom']),
                         friend_cells=len(inputs['friend']))
    return dict(format=SUMMARY_FORMAT, counts=cardinalities,
                by_class={label: counts[label] for label in LABELS}, by_stratum=by_stratum,
                audit_a_gaps=audit_gaps(inputs['merged'], inputs['hariom']),
                e1_input=inputs['report']['e1_input'], e2_pool=inputs['report']['e2_pool'],
                checkpoint=dict(e1_match=True, e2_match=True), input_shas=inputs['input_shas'])


def _json_text(doc):
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n'


def render_outputs(inputs, cells, summary):
    cells_text = _json_text(cells)
    gold = dict(format=F.GOLD_FORMAT_V2, split='dev',
                reference=dict(method=REFERENCE_METHOD, merge_sha256_lf=inputs['input_shas'][D.MERGE_REL],
                               export_sha256_lf=normalised_sha(cells_text.encode('utf-8'))),
                labels=[dict(profile_id=cell['profile_id'], item=cell['item'], label=cell['final']) for cell in cells])
    F.validate_gold(gold, {row['id']: row for row in inputs['dev']},
                    data={'part_map': {'parts': [{'part': part} for part in inputs['parts']]}})
    payloads = [(CELLS_REL, cells_text),
                (GOLD_REL, yaml.safe_dump(gold, sort_keys=False, allow_unicode=True)),
                (SUMMARY_REL, _json_text(summary))]
    manifest = dict(format=MANIFEST_FORMAT, input_shas=inputs['input_shas'], counts=summary['counts'],
                    outputs={rel: dict(sha256_lf=normalised_sha(text.encode('utf-8'))) for rel, text in payloads})
    return payloads + [(MANIFEST_REL, _json_text(manifest))]


def write_once(root, rel, text):
    path = fixed_path(root, rel)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
    except FileExistsError as exc:
        # Only output creation can select the write-once refusal.
        raise ExportError('output_exists', code=3) from exc


def run(root=ROOT, *, out=print):
    try:
        preflight(root)
        ruleset.check_ruleset()
        inputs = load_inputs(root)
        out(f'merge_sha256_lf={inputs["input_shas"][D.MERGE_REL]}')
        matches, unmatched = match_dev_entries(inputs['dev'], inputs['entries'], inputs['parts'])
        cells = build_dev_cells(inputs['dev'], matches, inputs['merged'], inputs['hariom'], inputs['friend'])
        check_report_checkpoint(inputs['report'])
        summary = build_export_summary(inputs, cells, unmatched)
        payloads = render_outputs(inputs, cells, summary)
        changed = sum(normalised_sha(fixed_path(root, rel).read_bytes()) != sha
                      for rel, sha in inputs['input_shas'].items())
        _require(not changed, 'input_changed', changed, 1)
        # Validate/render everything before the first exclusive create. Never roll back files.
        for rel, text in payloads:
            write_once(root, rel, text)
    except ruleset.RulesetChanged:
        out('ruleset_mismatch count=1')
        return 1
    except ExportError as exc:
        out(f'{exc.check} count={exc.count}')
        return exc.code
    except (Exception, SystemExit):
        # Parser/validator exception text can quote profile or label content.
        out('input_or_output_validation count=1')
        return 2
    out(' '.join(f'{key}={value}' for key, value in summary['counts'].items()))
    for judge, gaps in summary['audit_a_gaps'].items():
        out(f'judge={judge} under_x={gaps["under_call"]["x"]} under_n={gaps["under_call"]["n"]} '
            f'over_x={gaps["over_call"]["x"]} over_n={gaps["over_call"]["n"]}')
    e1, e2 = summary['e1_input'], summary['e2_pool']
    out(f'E1_rate={e1["weighted_binary_rate_on_final_mandatory"]:.12g} '
        f'E1_population={e1["population"]} E1_match=1 E2_x={e2["x"]} E2_n={e2["n"]} E2_match=1')
    for rel, text in payloads:
        out(f'{rel} sha256_lf={normalised_sha(text.encode("utf-8"))}')
    return 0


def main(argv=None, *, root=ROOT, out=print):
    parser = argparse.ArgumentParser(prog='python -m eval.s10.dev_export')
    parser.add_argument('command', choices=('run',))
    parser.parse_args(argv)
    return run(root, out=out)


if __name__ == '__main__':
    sys.exit(main())
