"""Registered S10c dev analysis, without model calls or rule replay.

Inputs are completed, receipt-anchored main outputs and the dev-only export.
Only dev-cell human labels and exported aggregates are used; original
audit/merge files stay closed.
The optional classic PPI row uses lambda=1 and joint profile resampling, never
ppi_py.ppboot or its cell bootstrap. Its unlabelled target is ALL dev-strata
cell-runs of the arm, across all reference labels, including the labelled
overlap. Numerator and denominator indicators use this SAME population; each
is corrected by classic PPI and their ratio is recomputed in each profile draw.
"""
import argparse
from collections import Counter
from fractions import Fraction
from importlib import metadata
from itertools import product
import json
import math
from pathlib import Path
import random
import sys

import jsonschema
import yaml

from standards.ruleset import normalised_sha
from . import dev_export as E
from . import formats as F
from . import frozen_candidates as FC
from . import label_quality as Q
from . import live as L
from . import metrics as M
from . import runner as R
from .tagger import TAGS, load_tags

ROOT = Path(__file__).resolve().parents[2]
FORMAT = 's10c-analysis.v1'
ARMS = ('medium', 'high')
REPEATS, SEED, N_BOOT = 5, 0, 2000
STRUCTURAL_NOTE = ('under-call reduction >= 0 by construction (final = max(rule, LLM)); '
                   'the cost appears as over-call')
PPI_LABEL = 'exploratory; corrects toward one non-expert human'
UNLABELLED = ('all dev-strata cell-runs of the arm across all reference labels, including audit-labelled '
              'cell-runs; all five repeats, without removing the labelled overlap; '
              'the same population for numerator and denominator')
BAND_ZERO = 'band width 0: no judge or human label crossed the mandatory line'
CELL_FIELDS = {'profile_id', 'stratum', 'item', 'final', 'routed', 'split_cell', 'labels', 'human', 'friend'}


class AnalysisError(ValueError):
    """Fixed diagnostics only; input content never enters exception messages."""


def require(condition, check):
    if not condition:
        raise AnalysisError(check)


def strict_json(raw):
    def bad(_):
        raise AnalysisError('json_constant')
    return json.loads(raw, parse_constant=bad)


def clean(value):
    if isinstance(value, Fraction):
        return float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def fraction_string(value):
    return None if value is None else f'{value.numerator}/{value.denominator}'


def validate_global_accounting(manifest, path):
    start, end = manifest.get('global_start'), manifest.get('global_end')
    require(type(start) is int and type(end) is int and 0 <= start <= end <= L.CALL_CAP,
            'global_accounting_bounds')
    last_seq = manifest.get('global_ledger_last_seq')
    require(type(last_seq) is int and last_seq > 0, 'global_ledger_seq')
    try:
        snapshot = FC.ledger_prefix_snapshot(path, last_seq)
    except OSError as exc:
        raise AnalysisError('global_ledger_input') from exc
    require(snapshot['global_ledger_prefix_sha256_lf'] == manifest.get('global_ledger_prefix_sha256_lf'),
            'global_ledger_prefix_sha')
    require(snapshot['total'] == end, 'global_ledger_total')
    return dict(global_start=start, global_end=end,
                global_ledger_last_seq=snapshot['global_ledger_last_seq'],
                global_ledger_prefix_sha256_lf=snapshot['global_ledger_prefix_sha256_lf'])


def load_inputs(args, *, root=ROOT, locked_files=None):
    root = Path(root).resolve()
    dest = L.checked_path(root, args.out)
    require(not dest.exists() and not dest.is_symlink(), 'output_exists')
    directory = L.checked_path(root, args.run_dir)
    require(not (directory / 'STOPPED.json').exists(), 'stopped_run')
    paths = {name: L.checked_path(root, value) for name, value in (
        ('manifest', directory / 'run_manifest.json'),
        ('receipt', root / 'eval/s10/manifests/s10c_main_run.json'),
        ('runs', directory / 'runs.jsonl'), ('summary', directory / 'summary.json'),
        ('gold', args.gold), ('cells', args.dev_cells), ('tags', args.tags),
        ('export_summary', args.export_summary), ('export_manifest', root / E.MANIFEST_REL),
        ('lock', root / L.LOCK_REL))}
    raw = {name: path.read_bytes() for name, path in paths.items()}
    sha = {name: normalised_sha(value) for name, value in raw.items()}
    manifest, receipt, summary = (strict_json(raw[name]) for name in ('manifest', 'receipt', 'summary'))
    require(manifest['format'] == L.RUN_FORMAT and manifest['phase'] == 'main', 'main_manifest')
    require(receipt['format'] == L.RECEIPT_FORMAT and receipt['counts'] == manifest['counts'], 'main_receipt')
    require(receipt['shas']['run_manifest_sha256_lf'] == sha['manifest'], 'manifest_sha')
    require(manifest.get('export_manifest_sha256_lf') == sha['export_manifest'], 'export_manifest_sha')
    require(manifest['repeats'] == REPEATS and manifest['seed'] == SEED, 'main_settings')
    require(len(manifest['settings']) == 2 and
            {s['name'] for s in manifest['settings']} == set(ARMS) and
            all(s['effort'] == s['name'] for s in manifest['settings']), 'main_settings')
    for name, filename in (('runs', 'runs.jsonl'), ('summary', 'summary.json')):
        require(manifest['outputs'][filename]['sha256_lf'] == sha[name], 'run_output_sha')
    require(receipt['shas']['runs_sha256_lf'] == sha['runs'], 'runs_sha')
    require(summary['format'] == R.SUMMARY_FORMAT and summary['note'] == L.SUMMARY_NOTE and
            summary['label'] == 'provisional (GPT)' and summary['provenance']['split'] == 'dev' and
            summary['provenance']['repeats'] == REPEATS and summary['provenance']['seed'] == SEED,
            'main_summary')
    require(set(summary['settings']) == set(ARMS), 'main_summary')
    require(L.verify_code_lock(root, locked_files) == sha['lock'] == manifest['input_shas'][L.LOCK_REL], 'run_lock_sha')
    folder = L.checked_path(root, manifest['run_dir'])
    require(not (folder / 'STOPPED.json').exists(), 'stopped_run')
    paths['ledger'] = L.checked_path(root, folder / FC.CALL_LEDGER_NAME)
    raw['ledger'] = paths['ledger'].read_bytes()
    sha['ledger'] = normalised_sha(raw['ledger'])
    require(sha['ledger'] == manifest['ledger_sha256_lf'] == receipt['shas']['ledger_sha256_lf'], 'ledger_sha')
    counts = manifest['counts']
    require(counts['records'] == counts['planned_records'] == counts['sidecars'] == 100, 'main_completion')
    require(all(type(counts[k]) is int and counts[k] >= 0
                for k in ('http_attempts', 'ledger_start', 'ledger_total')), 'ledger_counts')
    require(FC.ledger_total(paths['ledger']) == counts['ledger_total'] ==
            counts['ledger_start'] + counts['http_attempts'], 'ledger_counts')
    totals = counts['call_totals']
    require(all(type(totals[k]) is int and totals[k] >= 0 for k in ('candidates', 'pilot', 'current_ledger', 'total'))
            and totals['current_ledger'] == counts['ledger_total'] and
            totals['total'] == totals['candidates'] + totals['pilot'] + totals['current_ledger'] <= L.CALL_CAP,
            'call_totals')
    global_path = L.checked_path(root, FC.GLOBAL_LEDGER_REL)
    global_accounting = validate_global_accounting(manifest, global_path)
    sha['global_ledger_prefix'] = global_accounting['global_ledger_prefix_sha256_lf']
    gold_doc = yaml.safe_load(raw['gold'])
    jsonschema.Draft202012Validator(F.GOLD_SCHEMA_V2).validate(gold_doc)
    require(gold_doc['split'] == 'dev', 'dev_gold')
    require(gold_doc['reference']['export_sha256_lf'] == sha['cells'], 'gold_export_sha')
    export = strict_json(raw['export_manifest'])
    export_summary = strict_json(raw['export_summary'])
    require(export['format'] == E.MANIFEST_FORMAT and export_summary['format'] == E.SUMMARY_FORMAT,
            'export_format')
    for name, rel in (('gold', E.GOLD_REL), ('cells', E.CELLS_REL), ('export_summary', E.SUMMARY_REL)):
        require(export['outputs'][rel]['sha256_lf'] == sha[name], 'export_output_sha')
    require(export['input_shas'] == export_summary['input_shas'] and
            gold_doc['reference']['merge_sha256_lf'] == export['input_shas'][E.D.MERGE_REL], 'export_identity')
    require(export['counts'] == export_summary['counts'], 'export_counts')
    require(export_summary['checkpoint'] == dict(e1_match=True, e2_match=True), 'export_checkpoint')
    for name in ('gold', 'tags'):
        require(manifest['input_shas'].get(paths[name].relative_to(root).as_posix()) == sha[name], 'run_input_sha')
    cells = strict_json(raw['cells'])
    gold = {(row['profile_id'], row['item']): row for row in gold_doc['labels']}
    require(len(gold) == len(gold_doc['labels']) and isinstance(cells, list) and len(cells) == len(gold), 'cell_coverage')
    roster, seen = {}, set()
    for cell in cells:
        require(isinstance(cell, dict) and set(cell) == CELL_FIELDS, 'dev_cell_fields')
        key = (cell['profile_id'], cell['item'])
        require(key in gold and key not in seen and cell['final'] == gold[key]['label'], 'cell_coverage')
        seen.add(key)
        require(cell['stratum'] in F.STRATA and cell['routed'] is False and type(cell['split_cell']) is bool,
                'dev_cell_fields')
        require(set(cell['labels']) == set('ABC') and all(lab in M.RANK for lab in cell['labels'].values()) and
                cell['human'] in (*M.LABELS, None) and cell['friend'] in (*M.LABELS, None), 'dev_cell_labels')
        votes = sorted(cell['labels'].values(), key=M.RANK.get)
        require(cell['split_cell'] == (len(set(votes)) > 1) and cell['final'] == votes[1], 'dev_cell_reference')
        require(cell['profile_id'] not in roster or roster[cell['profile_id']] == cell['stratum'], 'stratum_identity')
        roster[cell['profile_id']] = cell['stratum']
    require(len(roster) == 10 and Counter(roster.values()) == Counter([f'S{i}' for i in range(1, 9)] + ['trap'] * 2),
            'dev_composition')
    require(export['counts']['dev_profiles'] == 10 and export['counts']['dev_cells'] == len(cells), 'export_counts')
    records = [strict_json(line) for line in raw['runs'].splitlines() if line.strip()]
    expected = {(arm, repeat, pid) for arm in ARMS for repeat in range(1, REPEATS + 1) for pid in roster}
    actual = []
    for record in records:
        pid = record['profile_id']
        require(pid in roster and record['stratum'] == roster[pid] and record['split'] == 'dev', 'record_profile')
        require(record['setting'] in ARMS and record['effort_effective'] == record['setting'] and
                record['llm_detail'] in R.LLM_DETAILS and type(record['repeat']) is int, 'record_setting')
        require(type(record['http_attempts']) is int and 0 <= record['http_attempts'] <= L.RUN_RESERVE,
                'record_attempts')
        require(all(isinstance(record[k], dict) and all(v in M.RANK for v in record[k].values())
                    for k in ('final', 'rule')), 'record_labels')
        require(all(M.RANK[record['final'].get(item, 'not_applicable')] >=
                    M.RANK[record['rule'].get(item, 'not_applicable')]
                    for item in set(record['final']) | set(record['rule'])), 'record_rule_floor')
        require(record['llm'] is None or isinstance(record['llm'], dict) and
                all(v in (*M.LABELS, 'omitted') for v in record['llm'].values()), 'record_labels')
        actual.append((record['setting'], record['repeat'], pid))
    require(len(records) == 100 and len(set(actual)) == 100 and set(actual) == expected, 'main_completion')
    require(sum(r['http_attempts'] for r in records) == counts['http_attempts'], 'record_attempts')
    tags = load_tags(paths['tags'], required=list(gold))
    require(set(tags) == set(gold), 'tag_coverage')
    return dict(root=root, dest=dest, paths=paths, shas=sha, records=records, cells=cells, gold=gold,
                tags=tags, export_summary=export_summary, roster=roster, stop_dirs=(directory, folder),
                global_ledger_path=global_path, global_accounting=global_accounting)


def under_fraction(cells, pred='final'):
    mandatory = [c for c in cells if c['gold'] == 'mandatory']
    return Fraction(sum(c[pred] != 'mandatory' for c in mandatory), len(mandatory)) if mandatory else None


def directed_lift(cells, metric, *, reduction=False, n_boot=N_BOOT):
    value = M.lift(cells, metric, n_boot=n_boot, seed=SEED)
    sign = -1 if reduction else 1
    return dict(direction='rules - hybrid' if reduction else 'hybrid - rules',
                rules_rate=value['rules'], hybrid_rate=value['hybrid'], point=sign * value['lift'],
                lo=-value['hi'] if reduction else value['lo'], hi=-value['lo'] if reduction else value['hi'],
                ci_label=M.BOOTSTRAP_CI_LABEL, n_boot=n_boot, seed=SEED)


def primary(cells, *, n_boot=N_BOOT):
    per_profile = []
    for pid in sorted({c['profile_id'] for c in cells}):
        subset = [c for c in cells if c['profile_id'] == pid]
        h, rule = under_fraction(subset), under_fraction(subset, 'rule')
        per_profile.append(dict(profile_id=pid, hybrid_under_call_rate=h, rules_under_call_rate=rule,
                                under_call_reduction_rules_minus_hybrid=rule - h if h is not None else None))
    return dict(under_call_rate=M.under_call_rate(cells),
                under_call_ci=dict(**M.bootstrap_ci(cells, M.under_call_rate, n_boot=n_boot, seed=SEED),
                                   ci_label=M.BOOTSTRAP_CI_LABEL),
                rules_under_call_rate=M.under_call_rate(cells, 'rule'),
                under_call_reduction=directed_lift(cells, M.under_call_rate, reduction=True, n_boot=n_boot),
                profiles_with_under_call=dict(**M.profiles_with_under_call(cells), repeat_label='any of 5 repeats'),
                per_profile=per_profile)


def resources(records):
    token_keys = dict(input='input_tokens', output='output_tokens', reasoning='reasoning_tokens',
                      cached='cache_read_input_tokens')
    tokens, missing = dict.fromkeys(token_keys, 0), dict.fromkeys(token_keys, 0)
    latencies, absent_latency, attempts = [], 0, 0
    for record in records:
        provenance = record.get('llm_provenance') or {}
        rows = provenance.get('attempts') or []
        attempts += len(rows)
        latency = provenance.get('latency_s')
        if type(latency) in (int, float) and math.isfinite(latency) and latency >= 0:
            latencies.append(latency)
        else:
            absent_latency += 1
        for name, key in token_keys.items():
            for row in rows:
                value = (row.get('usage') or {}).get(key)
                if type(value) is int and value >= 0:
                    tokens[name] += value
                else:
                    missing[name] += 1
            missing[name] += not bool(rows)
    return dict(unit='run; token and latency totals are not allocated across cell tags', tokens=tokens,
                missing_token_usage=missing, latency_s=sum(latencies) if latencies else None,
                missing_latency_runs=absent_latency, attempts=attempts,
                http_attempts=sum(r['http_attempts'] for r in records))


def secondary_cells(scored, every, *, n_boot=N_BOOT):
    stable = M.stability(every)
    return dict(under_call_rate=M.under_call_rate(scored), over_call_rate=M.over_call_rate(scored), exact_accuracy=M.accuracy(scored),
                macro_f1=M.macro_f1(scored), qw_kappa=M.qw_kappa(scored), binary_kappa=M.binary_kappa(scored),
                under_call_reduction=directed_lift(scored, M.under_call_rate, reduction=True, n_boot=n_boot),
                over_call_increase=directed_lift(scored, M.over_call_rate, n_boot=n_boot),
                upgrade_pr=M.upgrade_pr(scored), blocked_downgrades=M.blocked_downgrades(every),
                disagreement_table=M.disagreement_table(scored)['table'],
                stability={k: stable[k] for k in ('identical_profile_share', 'cell_flip_rate', 'repeats')},
                profiles_with_under_call=M.profiles_with_under_call(scored))


def score_scope(records, gold, tags, *, n_boot=N_BOOT):
    scored, every, _ = R.cells_for(records, gold)
    block = secondary_cells(scored, every, n_boot=n_boot)
    block.update(llm_detail={d: sum(r['llm_detail'] == d for r in records) for d in R.LLM_DETAILS},
                 resources=resources(records), by_tag={})
    for tag in TAGS:
        select = lambda cs: [c for c in cs if tags.get((c['profile_id'], c['item'])) == tag]
        block['by_tag'][tag] = secondary_cells(select(scored), select(every), n_boot=n_boot)
    return block


def effort_decision(records, gold, strata):
    scored, _, _ = R.cells_for([r for r in records if r['profile_id'] in strata], gold)
    rates = {arm: under_fraction([c for c in scored if c['setting'] == arm]) for arm in ARMS}
    differences, n6 = {}, 0
    for pid in sorted(strata):
        per = {arm: under_fraction([c for c in scored if c['profile_id'] == pid and c['setting'] == arm]) for arm in ARMS}
        if all(value is None for value in per.values()):
            # G-05: undefined on both arms (no gold-mandatory cell) is a tie: high <= medium,
            # difference 0 in the per-profile differences and the sign-flip.
            differences[pid] = Fraction(0)
            n6 += 1
            continue
        defined = all(value is not None for value in per.values())
        differences[pid] = per['high'] - per['medium'] if defined else None
        n6 += defined and per['high'] <= per['medium']  # Exact ties count in high's favour (S10P-14).
    zero_mandatory = sum(not any(key[0] == pid and row['label'] == 'mandatory' for key, row in gold.items())
                         for pid in strata)
    d = rates['medium'] - rates['high'] if all(v is not None for v in rates.values()) else None
    failures = {arm: sum(r['setting'] == arm and r['llm_detail'] != 'ok' for r in records) for arm in ARMS}
    fail_gap = failures['medium'] - failures['high']
    rule = 1 if d is not None and d >= Fraction(1, 20) and n6 >= 6 else 2 if fail_gap >= 3 else 3
    return dict(choice='MEDIUM' if rule == 3 else 'HIGH', rule_fired=rule,
                medium_under_call_rate=rates['medium'], medium_under_call_rate_fraction=fraction_string(rates['medium']),
                high_under_call_rate=rates['high'], high_under_call_rate_fraction=fraction_string(rates['high']),
                d_pooled_medium_minus_high=d, d_pooled_medium_minus_high_fraction=fraction_string(d),
                threshold=Fraction(1, 20), threshold_fraction='1/20', high_le_medium_profiles=n6,
                strata_profiles_zero_gold_mandatory=zero_mandatory,
                non_ok_all_50_runs=failures, non_ok_medium_minus_high=fail_gap,
                per_profile_high_minus_medium=differences,
                per_profile_high_minus_medium_fraction={pid: fraction_string(value) for pid, value in differences.items()})


def sign_flip(differences):
    if len(differences) != 8 or any(d is None for d in differences):
        return dict(p=None, p_fraction=None, undefined=True, method='exact two-sided sign-flip; secondary; no Holm')
    observed = abs(sum(differences, Fraction()) / 8)
    extreme = sum(abs(sum((s * d for s, d in zip(signs, differences)), Fraction()) / 8) >= observed
                  for signs in product((-1, 1), repeat=8))
    p = Fraction(extreme, 256)
    return dict(p=p, p_fraction=fraction_string(p), undefined=False, patterns=256,
                method='exact two-sided sign-flip; secondary; no Holm')


def paired_comparison(records, gold, strata, decision, *, n_boot=N_BOOT):
    cells, _, _ = R.cells_for([r for r in records if r['profile_id'] in strata], gold)
    def difference(sample):
        return M.under_call_rate([c for c in sample if c['setting'] == 'high']) - M.under_call_rate(
            [c for c in sample if c['setting'] == 'medium'])
    ci = M.bootstrap_ci(cells, difference, n_boot=n_boot, seed=SEED)
    if math.isfinite(ci['lo']) and math.isfinite(ci['hi']):
        bound = max(abs(ci['lo']), abs(ci['hi']))
        wording = ('consistent with no difference / a difference of up to ' if ci['lo'] <= 0 <= ci['hi'] else
                   'consistent with a difference of up to ') + format(bound, '.12g')
    else:
        wording = 'undefined: paired interval unavailable'
    return dict(direction='high - medium', ci=dict(**ci, ci_label=M.BOOTSTRAP_CI_LABEL),
                per_profile_high_minus_medium=decision['per_profile_high_minus_medium'],
                per_profile_high_minus_medium_fraction=decision['per_profile_high_minus_medium_fraction'],
                sign_flip=sign_flip(list(decision['per_profile_high_minus_medium'].values())), wording=wording)


def reference_versions(cells):
    versions = {name: {} for name in ('main', 'jury_median', 'LOJO-A', 'LOJO-B', 'LOJO-C', 'conservative_max')}
    lower, higher = {}, {}
    for cell in cells:
        key = (cell['profile_id'], cell['item'])
        labels = sorted(cell['labels'].values(), key=M.RANK.get)
        versions['main'][key] = cell['final']
        versions['jury_median'][key] = labels[1]
        for judge in 'ABC':
            pair = [lab for who, lab in cell['labels'].items() if who != judge]
            # S10P-27: audit labels are not referee labels. With 0 routed cells,
            # no referee label exists, so a disagreeing pair takes the higher label.
            versions['LOJO-' + judge][key] = max(pair, key=M.RANK.get)
        votes = labels + ([cell['human']] if cell['human'] is not None else [])
        versions['conservative_max'][key] = max(votes, key=M.RANK.get)  # Second-rater labels never enter.
        lower[key], higher[key] = (labels[0], labels[-1]) if cell['split_cell'] else (cell['final'], cell['final'])
    as_gold = lambda values: {key: dict(profile_id=key[0], item=key[1], label=value) for key, value in values.items()}
    return {name: as_gold(values) for name, values in versions.items()}, dict(lower=as_gold(lower), higher=as_gold(higher))


def family_overlap(records, cells):
    lookup = {(c['profile_id'], c['item']): c for c in cells}
    result = {}
    for judge in 'ABC':
        n = x = 0
        for record in records:
            for key, cell in lookup.items():
                if key[0] != record['profile_id'] or cell['human'] is None or cell['labels'][judge] == cell['human']:
                    continue
                n += 1
                x += record['final'].get(key[1], 'not_applicable') == cell['labels'][judge]
        result[judge] = dict(x=x, n=n, share=Fraction(x, n) if n else None,
                             undefined=not bool(n), denominator='judge disagrees with Hariom; cell-runs')
    return result


def tipping_ranges(cells, gold, export_summary):
    base = {key for key, row in gold.items() if row['label'] == 'mandatory'}
    rate = export_summary['e1_input']['weighted_binary_rate_on_final_mandatory']
    pool = export_summary['e2_pool']
    def one(subset, keys):
        P = sum(c['gold'] == 'mandatory' for c in subset)
        U = sum(c['gold'] == 'mandatory' and c['final'] != 'mandatory' for c in subset)
        errors = dict(E0=0, E1=Q.e1(rate, len(keys)), E2=Q.e2(pool['x'], pool['n'], len(keys)))
        ranges = {}
        for name, count in errors.items():
            value = Q.tip(U, P, REPEATS * count)
            ranges[name] = dict(E_cells=count, lower=value.lower, upper=value.upper,
                                lower_undefined=value.lower_undefined, upper_undefined=value.upper_undefined)
        return dict(U=U, P=P, mandatory_cells=len(keys), k=REPEATS, ranges=ranges)
    out = one(cells, base)
    # G-12: per-profile U and P next to the pooled tipping range.
    out['per_profile'] = {pid: dict(U=sum(c['gold'] == 'mandatory' and c['final'] != 'mandatory'
                                          for c in cells if c['profile_id'] == pid),
                                    P=sum(c['gold'] == 'mandatory' for c in cells if c['profile_id'] == pid))
                          for pid in sorted({c['profile_id'] for c in cells})}
    out['leave_one_profile_out'] = {pid: one([c for c in cells if c['profile_id'] != pid],
                                             {key for key in base if key[0] != pid})
                                    for pid in sorted({c['profile_id'] for c in cells})}
    return out


def load_ppi():
    try:
        version = metadata.version('ppi-python')
    except metadata.PackageNotFoundError:
        return None, 'not run: ppi-python not installed'
    if version != '0.2.3':
        return None, 'not run: ppi-python 0.2.3 required'
    try:
        from ppi_py import ppi_mean_pointestimate
    except ImportError:
        return None, 'not run: ppi-python not installed'
    return ppi_mean_pointestimate, 'run'


def ppi_under_call(records, gold, cells, estimator, *, status='run', n_boot=N_BOOT):
    """D-08: ratio of classic PPI numerator/denominator means on one population.

    Labelled rows have numerator (Hariom mandatory AND below mandatory) and
    denominator (Hariom mandatory). Predictions replace Hariom with reference.
    Both unlabelled prediction arrays include EVERY scored strata cell-run.
    One common profile draw supplies both channels and both data sets.
    """
    out = dict(label=PPI_LABEL, status=status, lambda_value=1, unlabelled_set=UNLABELLED,
               labelled_set='dev strata audit-A cells with a Hariom label, represented at each observed run',
               ci_label=M.BOOTSTRAP_CI_LABEL, seed=SEED, n_boot=n_boot)
    if estimator is None:
        return out
    import numpy as np
    lookup = {(c['profile_id'], c['item']): c for c in cells}
    groups = {pid: [] for pid in sorted({r['profile_id'] for r in records})}
    for record in records:
        for key, row in gold.items():
            if key[0] != record['profile_id']:
                continue
            below = record['final'].get(key[1], 'not_applicable') != 'mandatory'
            human = lookup[key]['human']
            groups[key[0]].append((int(human == 'mandatory' and below), int(row['label'] == 'mandatory' and below),
                                    int(human == 'mandatory'), int(row['label'] == 'mandatory'), human is not None))
    def estimate(rows):
        labelled = [r for r in rows if r[4]]
        if not labelled or not rows:
            return None, None, None, None, None
        corrections = [Fraction(sum(r[y] - r[yhat] for r in labelled), len(labelled))
                       for y, yhat in ((0, 1), (2, 3))]
        numerator, denominator = [float(estimator(np.asarray([r[y] for r in labelled], dtype=float),
            np.asarray([r[yhat] for r in labelled], dtype=float),
            np.asarray([r[yhat] for r in rows], dtype=float), lam=1)) for y, yhat in ((0, 1), (2, 3))]
        point = numerator / denominator if math.isfinite(numerator) and math.isfinite(denominator) and denominator > 0 else None
        return point, numerator, denominator, *corrections
    point, numerator, denominator, num_correction, den_correction = estimate(
        [row for group in groups.values() for row in group])
    if num_correction is None:
        out['status'] = 'undefined: labelled or unlabelled set empty'
    elif denominator is not None and denominator <= 0:
        out['status'] = 'undefined: PPI denominator <= 0'
    elif point is None:
        out['status'] = 'undefined: non-finite PPI estimate'
    out.update(point=point, numerator_mean=numerator, denominator_mean=denominator, undefined=point is None,
               correction_note=dict(mean_numerator_correction_term=num_correction,
                   mean_denominator_correction_term=den_correction,
                   note='correction terms = 0; PPI adds no information here' if num_correction == den_correction == 0 else
                        'mean human-minus-reference numerator and denominator indicators on labelled cell-runs'))
    values, rng, ids = [], random.Random(SEED), sorted(groups)
    for _ in range(n_boot):
        rows = [row for pid in (rng.choice(ids) for _ in ids) for row in groups[pid]]
        value, *_ = estimate(rows)
        if value is not None:
            values.append(value)
    values.sort()
    out.update(lo=M._quantile(values, .025) if values and point is not None else None,
               hi=M._quantile(values, .975) if values and point is not None else None, n_undefined=n_boot - len(values))
    return out


def build_analysis(inputs, *, n_boot=N_BOOT, ppi=None):
    records, gold, cells, tags = (inputs[k] for k in ('records', 'gold', 'cells', 'tags'))
    strata = {pid for pid, s in inputs['roster'].items() if s != 'trap'}
    traps = set(inputs['roster']) - strata
    versions, two_way = reference_versions(cells)
    estimator, ppi_status = load_ppi() if ppi is None else ppi
    result = dict(format=FORMAT, label='provisional (GPT)', structural_note=STRUCTURAL_NOTE,
                  input_shas=inputs['shas'], metric_scopes=dict(primary='all runs', secondary='llm_detail == ok'),
                  primary={}, secondary={}, traps={}, family_overlap={}, split_sensitivity={}, tipping={}, ppi={})
    accounting = inputs['global_accounting']
    result['paid_requests_global_ledger'] = (
        f'paid requests (global ledger): {accounting["global_start"]} -> {accounting["global_end"]} (cap 150)')
    decision = effort_decision(records, gold, strata)
    result['effort_decision'] = decision
    result['paired_comparison'] = paired_comparison(records, gold, strata, decision, n_boot=n_boot)
    band = {}
    for name, reference in versions.items():
        rates = {arm: under_fraction(R.cells_for([r for r in records if r['profile_id'] in strata and
                                                  r['setting'] == arm], reference)[0]) for arm in ARMS}
        alternative = effort_decision(records, reference, strata)
        band[name] = dict(primary_under_call_rates=rates,
                          primary_under_call_rates_fraction={arm: fraction_string(rate) for arm, rate in rates.items()},
                          effort_decision=alternative,
                          decision_would_change=alternative['choice'] != decision['choice'])
        if name == 'jury_median':
            band[name]['note'] = 'identical to main by construction (0 routed)'
    result['robustness_band'] = dict(versions=band)
    if all(band['main']['primary_under_call_rates'][arm] is not None and
           len({row['primary_under_call_rates'][arm] for row in band.values()}) == 1 for arm in ARMS):
        result['robustness_band']['note'] = BAND_ZERO
    split_mandatory = sum(c['profile_id'] in strata and c['split_cell'] and c['final'] == 'mandatory' for c in cells)
    result['strata_split_gold_mandatory_cells'] = split_mandatory
    result['primary_two_way_required'] = split_mandatory > 0
    if split_mandatory:
        result['primary_two_way'] = {}
    for arm in ARMS:
        subset = [r for r in records if r['setting'] == arm and r['profile_id'] in strata]
        scored = R.cells_for(subset, gold)[0]
        result['primary'][arm] = primary(scored, n_boot=n_boot)
        result['primary'][arm]['LOJO-B'] = band['LOJO-B']['primary_under_call_rates'][arm]
        result['secondary'][arm], result['family_overlap'][arm] = {}, {}
        result['traps'][arm] = {}
        for scope, select in (('all_runs', lambda rs: rs), ('ok_only', lambda rs: [r for r in rs if r['llm_detail'] == 'ok'])):
            selected = select(subset)
            result['secondary'][arm][scope] = score_scope(selected, gold, tags, n_boot=n_boot)
            result['family_overlap'][arm][scope] = family_overlap(selected, cells)
            trap_records = select([r for r in records if r['setting'] == arm and r['profile_id'] in traps])
            result['traps'][arm][scope] = dict(primary=primary(R.cells_for(trap_records, gold)[0], n_boot=n_boot),
                                               secondary=score_scope(trap_records, gold, tags, n_boot=n_boot))
        strata_gold = {key: row for key, row in gold.items() if key[0] in strata}
        result['tipping'][arm] = tipping_ranges(scored, strata_gold, inputs['export_summary'])
        result['ppi'][arm] = ppi_under_call(subset, strata_gold, cells, estimator, status=ppi_status, n_boot=n_boot)
        result['split_sensitivity'][arm] = {}
        for direction, reference in two_way.items():
            block = {scope: score_scope(selected, reference, tags, n_boot=n_boot)
                     for scope, selected in (('all_runs', subset), ('ok_only', [r for r in subset if r['llm_detail'] == 'ok']))}
            block['ppi'] = ppi_under_call(subset, {key: row for key, row in reference.items() if key[0] in strata},
                                           cells, estimator, status=ppi_status, n_boot=n_boot)
            block['family_overlap'] = result['family_overlap'][arm]
            block['traps'] = {scope: score_scope(selected, reference, tags, n_boot=n_boot) for scope, selected in (
                ('all_runs', [r for r in records if r['setting'] == arm and r['profile_id'] in traps]),
                ('ok_only', [r for r in records if r['setting'] == arm and r['profile_id'] in traps and r['llm_detail'] == 'ok']))}
            result['split_sensitivity'][arm][direction] = block
            if split_mandatory:
                result['primary_two_way'].setdefault(arm, {})[direction] = primary(R.cells_for(subset, reference)[0], n_boot=n_boot)
    result['audit_a_gaps'] = inputs['export_summary']['audit_a_gaps']
    return result


def report_lines(result):
    def number(value):
        return 'undefined' if value is None or isinstance(value, float) and not math.isfinite(value) else format(float(value), '.12g')
    lines = []
    for population in ('secondary', 'traps'):
        for arm in ARMS:
            for scope, value in result[population][arm].items():
                block = value['secondary'] if population == 'traps' else value
                lines.append(f'llm_detail population={population} arm={arm} scope={scope} ' +
                             ' '.join(f'{d}={n}' for d, n in block['llm_detail'].items()))
    lines.append(STRUCTURAL_NOTE)
    lines.append(f'strata_split_gold_mandatory_cells={result["strata_split_gold_mandatory_cells"]} '
                 f'primary_two_way_required={result["primary_two_way_required"]}')
    def lifts(value, context):
        if not isinstance(value, dict):
            return
        for key, child in value.items():
            if key in ('under_call_reduction', 'over_call_increase'):
                lines.append(f'{context} metric={key} direction={child["direction"]} '
                             f'rules_rate={number(child["rules_rate"])} hybrid_rate={number(child["hybrid_rate"])} '
                             f'point={number(child["point"])} lo={number(child["lo"])} hi={number(child["hi"])}')
            elif key not in ('per_profile', 'per_profile_high_minus_medium'):
                lifts(child, context + ' ' + key)
    lifts(result, 'analysis')
    for name, row in result['robustness_band']['versions'].items():
        d = row['effort_decision']
        lines.append(f'effort_reference={name} medium_rate={number(d["medium_under_call_rate"])} '
                     f'high_rate={number(d["high_under_call_rate"])} '
                     f'd_medium_minus_high={number(d["d_pooled_medium_minus_high"])} threshold={number(d["threshold"])} '
                     f'high_le_medium_profiles={d["high_le_medium_profiles"]} '
                     f'strata_profiles_zero_gold_mandatory={d["strata_profiles_zero_gold_mandatory"]} '
                     f'medium_non_ok_all_50={d["non_ok_all_50_runs"]["medium"]} '
                     f'high_non_ok_all_50={d["non_ok_all_50_runs"]["high"]} '
                     f'non_ok_medium_minus_high={d["non_ok_medium_minus_high"]} '
                     f'rule_fired={d["rule_fired"]} choice={d["choice"]} decision_would_change={row["decision_would_change"]}')
        if 'note' in row:
            lines.append(row['note'])
    lines.append('paired_high_minus_medium sign_flip_p=' + number(result['paired_comparison']['sign_flip']['p']) +
                 ' ' + result['paired_comparison']['wording'])
    if 'note' in result['robustness_band']:
        lines.append(result['robustness_band']['note'])
    for arm, value in result['ppi'].items():
        lines.append(f'ppi arm={arm} status={value["status"]} label={PPI_LABEL} unlabelled_set={UNLABELLED}')
        if 'correction_note' in value:
            correction = value['correction_note']
            lines.append(f'ppi arm={arm} numerator_mean={number(value["numerator_mean"])} '
                         f'denominator_mean={number(value["denominator_mean"])} '
                         f'numerator_correction={number(correction["mean_numerator_correction_term"])} '
                         f'denominator_correction={number(correction["mean_denominator_correction_term"])} {correction["note"]}')
    lines.append(result['paid_requests_global_ledger'])
    return lines


def main(argv=None, *, root=ROOT, locked_files=None, n_boot=N_BOOT, ppi=None, out=print):
    parser = argparse.ArgumentParser(prog='python -m eval.s10.analysis')
    for name in ('run-dir', 'gold', 'dev-cells', 'tags', 'export-summary', 'out'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    try:
        inputs = load_inputs(args, root=root, locked_files=locked_files)
        result = build_analysis(inputs, n_boot=n_boot, ppi=ppi)
        text = json.dumps(clean(result), indent=2, allow_nan=False) + '\n'
        require(not any((folder / 'STOPPED.json').exists() for folder in inputs['stop_dirs']), 'stopped_run')
        require(L.verify_code_lock(inputs['root'], locked_files) == inputs['shas']['lock'], 'run_lock_sha')
        for name, path in inputs['paths'].items():
            require(normalised_sha(path.read_bytes()) == inputs['shas'][name], 'input_changed')
        validate_global_accounting(inputs['global_accounting'], inputs['global_ledger_path'])
        lines = report_lines(result)
        inputs['dest'].parent.mkdir(parents=True, exist_ok=True)
        try:
            with inputs['dest'].open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(text)
        except FileExistsError as exc:
            raise AnalysisError('output_exists') from exc
    except (Exception, SystemExit) as exc:
        check = str(exc) if isinstance(exc, (AnalysisError, FC.LedgerError)) else 'input_or_output_validation'
        out(check + ' count=1')
        return 3 if check == 'output_exists' else 2
    for line in lines:
        out(line)
    return 0


if __name__ == '__main__':
    sys.exit(main())
