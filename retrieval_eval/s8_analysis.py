"""S8 Part E: the pre-registered analysis and the qrels manifest (offline, 0 API calls).

Commands (via tasks.py):
  s8-qrels-manifest  after s8-merge-labels stage 3 (qrels written, audit PASS): write-once
                     retrieval_eval/s8_qrels_manifest.json (committed) with the sha256 of the audit freeze, the
                     judge freezes read, the agreement file of the merge mode, audit_check.json, qrels_meta.json,
                     qrels.tsv, the guideline in force and s8_partD_freeze.json, plus the merge mode.
  s8-analyze         locked until the qrels freeze: refuses unless the manifest exists, every sha matches and
                     audit_check.json says PASS. Then reads runs.jsonl, generations.jsonl, noob.jsonl and the qrels
                     and writes JSON + Markdown tables to <run dir>/analysis/ (write-once: if that folder already
                     holds files, a new timestamped subfolder is used).

Sources: S8_PREREGISTRATION.md ("Analysis plan"), Addendum 1 (b, e, f, g), Addendum 2 section 7 + notes 2-3,
Addendum 3 (system failures), Addendum 4 (sections 1-13). Items Addendum 4 section 8 calls descriptive carry
"descriptive": true. Every output carries LABEL. Item ids never matter here: qrels.tsv is keyed by
(qid, clause_id), so pool_key.jsonl and the item maps are never read.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
import json
import logging
import math
import statistics
import sys
from pathlib import Path

import numpy as np

from retrieval_eval import metrics as mx
from retrieval_eval import qrels as qrels_io
from retrieval_eval.s8_runs import (ARMS, CLAUSES, FILTER_OFF_DEPTH, LIMITS, NOOB, ROOT, TOP_KS, bytes_sha,
                                    file_sha, output_dir, read_jsonl, write_once)

log = logging.getLogger('s8_analysis')

LABEL = 'provisional (GPT): stage 1 = Azure gpt-5-mini'
LOCKED = 'Part E is locked until the qrels freeze.'
BOOTSTRAP_RESAMPLES = 10_000  # S8_PREREGISTRATION.md "Statistics"
BOOTSTRAP_SEED = 20261019
ALPHA = 0.05
MANIFEST = ROOT / 'retrieval_eval/s8_qrels_manifest.json'
PARTD_FREEZE = 'retrieval_eval/s8_partD_freeze.json'
SEALED_ROOT = ROOT / 'data' / 'runs'  # the test-only bypass never runs on a real run folder
K = 10
REPORT_QIDS = ('R1', 'R2', 'R3')
NEGATIVES = ('Q7', 'Q12')  # deliberate negatives: exempt from the swap rule (queries.yaml common_rules)
SWAP_FLAG = 'swap-eligible (no replacement possible)'
PIPELINE = (*ARMS, NOOB)  # v1, v1.1, v1-noob
FILTER_OFF = ('vector_all', 'bm25', 'hyde')
SCORED = (*[(arm, v) for arm in PIPELINE for v in ('a', 'b', 'c')],
          ('vector_all', 'a'), ('vector_all', 'b'), ('bm25', 'a'), ('bm25', 'b'), ('hyde', 'c'))
PRIMARY = ((1, 'candidate_recall', 'v1', NOOB), (2, 'r@10', 'v1', 'v1.1'), (3, 'r@10', 'v1', 'vector_all'))
RELEVANCE = ('candidate_recall', 'r@5', 'r@10', 'conditional_r@10', 'mrr@10', 'ndcg@10', 'r@final', 'expansion_dr',
             'expansion_precision')  # Addendum 4 section 4: means over the analysed queries only
ALL_QUERIES = ('reduction_ratio', 'judged@5', 'judged@10', 'judged@final')  # means over every query
METRICS = RELEVANCE + ALL_QUERIES
DESCRIPTIVE = {'expansion_precision', 'judged@5', 'judged@10', 'judged@final'}  # Addendum 4 section 8
# Addendum 4 note 1 section 3: filter-off systems have no candidate filter; tables mark these as "by definition"
BY_DEFINITION = {'candidate_recall': '1 by definition (no candidate filter)',
                 'reduction_ratio': '0 by definition (no candidate filter)',
                 'conditional_r@10': '= R@10 by definition'}
STAGEWISE = ('candidate_recall', 'reduction_ratio', 'conditional_r@10', 'r@10', 'r@final', 'expansion_dr',
             'expansion_precision')
INTERNSHIP_FREE = 'No internship number appears in any S8 output (Addendum 2 section 7).'
WIDE = 'With {n} queries the intervals are wide (S8_PREREGISTRATION.md "Secondary reporting").'
A2_SENTENCE = ('Relevance labels were produced by two LLM judges from different model families (Claude Opus 5.5, '
               'GPT-6 Astra) using a frozen guideline; threshold disagreements were adjudicated by the author, and a '
               'blind audit of {n} items by the author, made without AI assistance, gave κ = {kappa} against the LLM '
               'consensus. The author is not a regulatory specialist.')


def _r(x, nd=6):
    return None if x is None else round(float(x), nd)


# ---------------------------------------------------------------- statistics
def _betacf(a, b, x, max_iter=500, eps=1e-15, tiny=1e-300):
    """Continued fraction for the regularised incomplete beta (modified Lentz)."""
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1 + aa * d
            d = 1 / (d if abs(d) > tiny else tiny)
            c = 1 + aa / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1) < eps:
            break
    return h


def betainc(a, b, x):
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1 - front * _betacf(b, a, 1 - x) / b


def t_sf2(t, df):
    """Two-sided tail probability P(|T| >= |t|) of Student's t."""
    return betainc(df / 2, 0.5, df / (df + t * t))


def t_cdf(t, df):
    tail = t_sf2(t, df) / 2
    return 1 - tail if t > 0 else tail


def t_ppf(p, df):
    """Student-t quantile by bisection on t_cdf (pure Python: scipy is not a project dependency)."""
    if not 0 < p < 1:
        raise ValueError('p must be in (0, 1)')
    if p < 0.5:
        return -t_ppf(1 - p, df)
    lo, hi = 0.0, 1.0
    while t_cdf(hi, df) < p:
        hi *= 2
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if t_cdf(mid, df) < p else (lo, mid)
    return (lo + hi) / 2


def paired_bootstrap(diffs, resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED):
    """Paired bootstrap over queries: resample the per-query differences, 95% percentile interval of the mean."""
    d = np.asarray(diffs, dtype=float)
    base = dict(resamples=resamples, seed=seed, interval='95% percentile', unit='query')
    if len(d) == 0:
        return dict(base, mean=None, lo=None, hi=None)
    idx = np.random.default_rng(seed).integers(len(d), size=(resamples, len(d)))
    lo, hi = np.percentile(d[idx].mean(axis=1), [2.5, 97.5])
    return dict(base, mean=_r(d.mean()), lo=_r(lo), hi=_r(hi))


def sign_flip_p(diffs):
    """Exact paired sign-flip permutation p (two-sided, |mean|), full enumeration of the 2^n sign patterns."""
    d = np.asarray(diffs, dtype=float)
    n = len(d)
    if n == 0:
        return None
    if n > 24:
        raise ValueError('exact enumeration is limited to 24 queries')
    signs = 1 - 2 * ((np.arange(2 ** n)[:, None] >> np.arange(n)) & 1)
    observed = abs(d.sum())
    return float(np.mean(np.abs(signs @ d) >= observed - 1e-9 * max(1.0, observed)))


def paired_t(diffs, levels=(0.95,)):
    """Paired t: mean difference, two-sided p and CIs at the given confidence levels."""
    d = np.asarray(diffs, dtype=float)
    n = len(d)
    if n < 2:
        return dict(n=n, df=None, mean=_r(d.mean()) if n else None, t=None, p=None, ci={})
    mean, se = float(d.mean()), float(d.std(ddof=1)) / math.sqrt(n)
    if se == 0:
        t, p = None, 1.0 if mean == 0 else 0.0
        ci = {f'{lv:.6f}': [_r(mean), _r(mean)] for lv in levels}
    else:
        t = mean / se
        p = t_sf2(t, n - 1)
        ci = {f'{lv:.6f}': [_r(mean - t_ppf(0.5 + lv / 2, n - 1) * se), _r(mean + t_ppf(0.5 + lv / 2, n - 1) * se)]
              for lv in levels}
    return dict(n=n, df=n - 1, mean=_r(mean), t=_r(t), p=_r(p, 8), ci=ci)


def holm(pvalues):
    """Holm step-down adjusted p-values (None stays None and counts as p = 1 in m)."""
    m = len(pvalues)
    p = [1.0 if v is None else v for v in pvalues]
    adjusted, running = [None] * m, 0.0
    for step, i in enumerate(sorted(range(m), key=lambda i: (p[i], i))):
        running = max(running, min(1.0, (m - step) * p[i]))
        adjusted[i] = None if pvalues[i] is None else _r(running, 8)
    return adjusted


def holm_levels(pvalues, alpha=ALPHA):
    """Confidence level per comparison at its Holm step: 1 - alpha / (m - j + 1), steps ordered by p."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: (1.0 if pvalues[i] is None else pvalues[i], i))
    levels = [None] * m
    for j, i in enumerate(order, start=1):
        levels[i] = 1 - alpha / (m - j + 1)
    return levels


def effective_n(diffs):
    """Addendum 1 g: k = queries whose difference is non-zero; the mean over them is descriptive only."""
    nonzero = [x for x in diffs if x != 0]
    return dict(k=len(nonzero), mean_over_k=_r(statistics.fmean(nonzero)) if nonzero else None,
                note='mean over the k queries is descriptive only')


def jaccard(a, b):
    a, b = set(a), set(b)
    return 1.0 if not a | b else len(a & b) / len(a | b)


def describe(values, metric=None):
    vals = [v for v in values if v is not None]
    out = dict(n=len(vals), na=len(values) - len(vals), mean=_r(statistics.fmean(vals)) if vals else None,
               median=_r(statistics.median(vals)) if vals else None,
               min=_r(min(vals)) if vals else None, max=_r(max(vals)) if vals else None)
    if metric in DESCRIPTIVE:
        out['descriptive'] = True
    return out


# ---------------------------------------------------------------- per-run metrics
def _ratio_or_na(numerator, denominator):
    return numerator / denominator if denominator else None


def judged_at(ids, judged, k=None):
    """|top-k ∩ judged| / min(k, list length); None for an empty list."""
    top = list(ids) if k is None else list(ids)[:k]
    return len(set(top) & judged) / len(top) if top else None


def score_run(run, candidates, grades, corpus_size):
    """Metrics of one scored run (Addendum 1 e; Addendum 4 sections 2-3).
    run: a runs.jsonl record (status ok | system_failure); candidates: stage-1 ids (pipeline / limit) or
    None for a filter-off system (its candidate set is the whole corpus). Ranked metrics use the stage-2 ranked
    list cut to 10; R@final and the expansion metrics use final_ids (pipeline only)."""
    gold = qrels_io.relevant(grades)
    pipeline = run['system'] in ('pipeline', 'limit')
    if run['status'] == 'system_failure':  # Addendum 3 + Addendum 4 section 3
        return {'candidate_recall': 0.0, 'r@5': 0.0, 'r@10': 0.0, 'mrr@10': 0.0, 'ndcg@10': 0.0,
                'conditional_r@10': None, 'expansion_precision': None, 'reduction_ratio': None,
                'r@final': 0.0 if pipeline else None, 'expansion_dr': 0.0 if pipeline else None}
    if run['status'] != 'ok':
        raise ValueError(f"cannot score a run with status {run['status']!r}")
    ranked = run['ranked_ids'][:K]
    hits10 = len(set(ranked) & gold)
    out = {'r@5': mx.recall_at_k(ranked, gold, 5) if ranked else 0.0,
           'r@10': mx.recall_at_k(ranked, gold, K) if ranked else 0.0,
           'mrr@10': mx.reciprocal_rank(ranked, gold),
           'ndcg@10': mx.ndcg_at_k(ranked, grades, K) if ranked else 0.0}
    if not pipeline:
        out.update({'candidate_recall': mx.ratio(len(gold), len(gold)), 'reduction_ratio': 0.0,
                    'conditional_r@10': _ratio_or_na(hits10, len(gold)),
                    'r@final': None, 'expansion_dr': None, 'expansion_precision': None})
        return out
    if candidates is None:
        raise ValueError('a pipeline run needs its stage-1 candidate set')
    if not set(run['ranked_ids']) <= set(candidates):
        raise ValueError(f"join error: {run['qid']} {run.get('arm')}/{run['variant']} ranks ids outside its "
                         'stage-1 candidate set')
    final = run['final_ids']
    added = set(final) - set(ranked)
    r_final = mx.ratio(len(set(final) & gold), len(gold))
    out.update({'candidate_recall': mx.candidate_recall(candidates, gold),
                'reduction_ratio': mx.reduction_ratio(candidates, corpus_size),
                'conditional_r@10': _ratio_or_na(hits10, len(set(candidates) & gold)),
                'r@final': r_final, 'expansion_dr': r_final - out['r@10'],
                'expansion_precision': _ratio_or_na(len(added & gold), len(added))})
    return out


def system_of(run):
    return run['arm'] if run['system'] == 'pipeline' else run['system']


def candidates_for(system, variant, qid, run, gen0, noob0):
    """Stage-1 candidates of a scored run: variant c reuses the same arm's variant-a set (Addendum 1 a)."""
    if system in FILTER_OFF:
        return None
    if run['status'] == 'system_failure':
        return []
    source = 'a' if variant == 'c' else variant
    row = noob0.get((qid, source)) if system == NOOB else gen0.get((system, qid, source))
    if row is None or row.get('candidate_ids') is None or (system != NOOB and row['status'] != 'ok'):
        raise ValueError(f'join error: no gen-0 stage-1 candidate set for {system}/{source}/{qid}')
    return row['candidate_ids']


def build_records(queries, runs, gen0, noob0, grades_by_qid, corpus_size):
    """{(system, variant): {qid: record}} for every scored run (gen 0; pipeline k = 10; filter-off depth 20).
    Stops on a replay_miss (Addendum 4 section 6) and on judged@k < 1 (section 5)."""
    index = defaultdict(list)
    for run in runs:
        if run['gen_index'] != 0:
            raise ValueError('only gen_index 0 runs exist in runs.jsonl')
        if run['system'] != 'limit':
            index[(system_of(run), run['variant'], run['top_k'], run['qid'])].append(run)
    records = {}
    for system, variant in SCORED:
        depth = FILTER_OFF_DEPTH if system in FILTER_OFF else K
        records[(system, variant)] = {}
        for q in queries:
            qid = q['qid']
            found = index.get((system, variant, depth, qid), [])
            if len(found) != 1:
                raise ValueError(f'join error: {len(found)} scored run(s) for {system}/{variant}/{qid} (k={depth})')
            run = found[0]
            if run['status'] == 'replay_miss':
                raise RuntimeError(f'STOP: replay_miss on the scored gen-0 run {system}/{variant}/{qid} '
                                   '(Addendum 4 section 6)')
            grades = grades_by_qid.get(qid, {})
            record = dict(qid=qid, status=run['status'], reason=run.get('reason'))
            if run['status'] == 'n/a':
                record.update(metrics={m: None for m in METRICS}, candidate_count=None, top10_grades=None,
                              highest_grade=None)
                records[(system, variant)][qid] = record
                continue
            cands = candidates_for(system, variant, qid, run, gen0, noob0)
            metrics = score_run(run, cands, grades, corpus_size)
            judged = set(grades)
            final = run['final_ids']
            metrics.update({'judged@5': judged_at(run['ranked_ids'], judged, 5),
                            'judged@10': judged_at(run['ranked_ids'], judged, K),
                            'judged@final': judged_at(final, judged)})
            short = [k for k in ('judged@5', 'judged@10', 'judged@final') if metrics[k] is not None and metrics[k] < 1]
            if short:
                raise RuntimeError(f'STOP: {short} < 1 for {system}/{variant}/{qid}: join error between runs and '
                                   'qrels (Addendum 4 section 5)')
            top10 = run['ranked_ids'][:K]
            record.update(metrics=metrics,
                          candidate_count=corpus_size if cands is None else len(set(cands)),
                          top10_grades={str(g): sum(grades.get(c) == g for c in top10) for g in range(4)},
                          highest_grade=max((grades[c] for c in final if c in grades), default=None))
            records[(system, variant)][qid] = record
    return records


# ---------------------------------------------------------------- query handling
def query_sets(queries, grades_by_qid):
    """Addendum 4 sections 1 and 4: zero-relevant queries leave the relevance means; swap-eligible queries
    (no grade-3 clause; Q7 and Q12 exempt) stay and are flagged."""
    qids = [q['qid'] for q in queries]
    unknown = sorted(set(grades_by_qid) - set(qids))
    if unknown:
        raise ValueError(f'qrels hold unknown qids: {unknown}')
    zero = [q for q in qids if not qrels_io.relevant(grades_by_qid.get(q, {}))]
    swap = [q for q in qids if q not in NEGATIVES and 3 not in grades_by_qid.get(q, {}).values()]
    analysed = [q for q in qids if q not in zero]
    return dict(all=qids, zero_relevant=zero, swap_eligible=swap, analysed=analysed,
                sensitivity=[q for q in analysed if q not in swap], report=[q for q in qids if q in REPORT_QIDS])


def query_flags(qsets, gen0):
    flags = {}
    for qid in qsets['all']:
        v1 = gen0.get(('v1', qid, 'a'))
        flags[qid] = dict(swap_eligible=qid in qsets['swap_eligible'],
                          flag=SWAP_FLAG if qid in qsets['swap_eligible'] else '',
                          report_query=qid in REPORT_QIDS, zero_relevant=qid in qsets['zero_relevant'],
                          v1_obligation_filter=None if v1 is None else ('yes' if v1['obligation_filter'] else 'no'))
    return flags


def arm_row(records, system, variant, relevance_qids, all_qids):
    recs = records[(system, variant)]
    row = dict(system=system, variant=variant,
               failures=sum(recs[q]['status'] == 'system_failure' for q in all_qids),
               not_applicable=sum(recs[q]['status'] == 'n/a' for q in all_qids))
    for metric in METRICS:
        qids = relevance_qids if metric in RELEVANCE else all_qids
        row[metric] = describe([recs[q]['metrics'][metric] for q in qids], metric)
        if system in FILTER_OFF and metric in BY_DEFINITION:
            row[metric]['by_definition'] = BY_DEFINITION[metric]
    return row


def arm_table(records, keys, relevance_qids, all_qids):
    return [arm_row(records, s, v, relevance_qids, all_qids) for s, v in keys]


# ---------------------------------------------------------------- primary comparisons
def compare(records, metric, x, y, variant, qids, flags):
    """Per-query paired values x - y over qids; a query where either side is n/a is dropped and listed."""
    rows, dropped = [], []
    for qid in qids:
        a, b = records[(x, variant)][qid], records[(y, variant)][qid]
        va, vb = a['metrics'][metric], b['metrics'][metric]
        if va is None or vb is None:
            dropped.append(dict(qid=qid, reason=f"{x}: {a['status']}, {y}: {b['status']}"
                                + (f" ({b['reason'] or a['reason']})" if (b['reason'] or a['reason']) else '')))
            continue
        rows.append(dict(qid=qid, x=_r(va), y=_r(vb), diff=_r(va - vb), x_status=a['status'], y_status=b['status'],
                         **{k: flags[qid][k] for k in ('flag', 'report_query', 'v1_obligation_filter')}))
    return rows, dropped


def primary_comparisons(records, qsets, flags):
    out = []
    for number, metric, x, y in PRIMARY:
        rows, dropped = compare(records, metric, x, y, 'a', qsets['analysed'], flags)
        diffs = [r['diff'] for r in rows]
        sens_rows = [r for r in rows if not flags[r['qid']]['swap_eligible']]
        sens = [r['diff'] for r in sens_rows]
        item = dict(id=number, metric=metric, x=x, y=y, variant='a', gen_index=0, direction=f'{x} - {y}',
                    query_set='analysed (zero-relevant queries excluded)', n=len(rows), dropped=dropped,
                    per_query=rows, x_summary=describe([r['x'] for r in rows]),
                    y_summary=describe([r['y'] for r in rows]), diff_summary=describe(diffs),
                    bootstrap=paired_bootstrap(diffs),
                    secondary=dict(sign_flip_p=sign_flip_p(diffs), paired_t=paired_t(diffs)),
                    sensitivity=dict(excludes=f'queries flagged {SWAP_FLAG}', n=len(sens),
                                     dropped_qids=[r['qid'] for r in rows if flags[r['qid']]['swap_eligible']],
                                     diff_summary=describe(sens), bootstrap=paired_bootstrap(sens)))
        if number == 1:  # Addendum 1 g
            item['effective_n'] = effective_n(diffs)
        out.append(item)
    perm = [c['secondary']['sign_flip_p'] for c in out]
    tp = [c['secondary']['paired_t']['p'] for c in out]
    for c, p_holm, t_holm, level in zip(out, holm(perm), holm(tp), holm_levels(tp)):
        c['secondary']['sign_flip_p_holm'] = p_holm
        c['secondary']['paired_t_p_holm'] = t_holm
        c['secondary']['holm_level'] = _r(level, 8)
        diffs = [r['diff'] for r in c['per_query']]
        c['secondary']['paired_t'] = paired_t(diffs, levels=(0.95, level))
    return out


# ---------------------------------------------------------------- secondary sections
def split_by_v1_filter(records, qsets, flags):
    """S8_PREREGISTRATION.md: results split by 'v1 filtered on obligation: yes / no' (v1, variant a, gen 0)."""
    keys = [(s, 'a') for s in (*PIPELINE, 'vector_all', 'bm25')]
    out = {}
    for group in ('yes', 'no'):
        everyone = [q for q in qsets['all'] if flags[q]['v1_obligation_filter'] == group]
        analysed = [q for q in qsets['analysed'] if q in everyone]
        out[group] = dict(queries=everyone, analysed=analysed, arms=arm_table(records, keys, analysed, everyone))
    return out


def zero_relevant_report(records, qsets):
    """Addendum 4 section 4: per system, candidate count, top-10 grade 1 / grade 0 counts, highest grade."""
    out = {}
    for qid in qsets['zero_relevant']:
        out[qid] = [dict(system=s, variant=v, status=records[(s, v)][qid]['status'],
                         candidate_count=records[(s, v)][qid]['candidate_count'],
                         top10_grade1=(records[(s, v)][qid]['top10_grades'] or {}).get('1'),
                         top10_grade0=(records[(s, v)][qid]['top10_grades'] or {}).get('0'),
                         highest_grade=records[(s, v)][qid]['highest_grade']) for s, v in SCORED]
    return out


def terminology_gap(records, qsets):
    """Addendum 4 section 13. b - a and c - a for R@10 and nDCG@10; candidate recall b - a (pipeline only).
    vector_all c - a = HyDE(c) - vector_all(a); BM25 b - a only. vector_all / BM25 rows are descriptive."""
    rows = []
    plans = [(arm, False, {'b': (arm, 'b'), 'c': (arm, 'c')}) for arm in PIPELINE]
    plans += [('vector_all', True, {'b': ('vector_all', 'b'), 'c': ('hyde', 'c')}), ('bm25', True, {'b': ('bm25', 'b')})]
    for system, descriptive, targets in plans:
        base = records[(system, 'a')]
        metrics = ['r@10', 'ndcg@10'] + (['candidate_recall'] if system in PIPELINE else [])
        for metric in metrics:
            for gap, key in targets.items():
                if metric == 'candidate_recall' and gap == 'c':
                    continue  # variant c reuses variant a's candidates
                per_query = {}
                for qid in qsets['analysed']:
                    va, vb = base[qid]['metrics'][metric], records[key][qid]['metrics'][metric]
                    per_query[qid] = None if va is None or vb is None else _r(vb - va)
                row = dict(system=system, metric=metric, gap=f'{gap}-a', minuend=f'{key[0]}({key[1]})',
                           subtrahend=f'{system}(a)', main_row=system == 'v1', per_query=per_query,
                           summary=describe(list(per_query.values())))
                if descriptive:
                    row['descriptive'] = True
                rows.append(row)
    return rows


def limit_experiment(queries, runs, records, gen0, grades_by_qid, qsets):
    """Addendum 4 section 7 (descriptive): dose-response of the injected LIMIT on v1, variant a, gen 0."""
    index, misses = defaultdict(list), Counter()
    for run in runs:
        if run['system'] == 'limit':
            index[(run['qid'], run['limit'], run['top_k'])].append(run)
            misses[run['limit']] += run['status'] == 'replay_miss'
    per_limit = {}
    for limit in LIMITS:
        per_query, identity = {}, {}
        for q in queries:
            qid = q['qid']
            grades = grades_by_qid.get(qid, {})
            gold = qrels_io.relevant(grades)
            for k in TOP_KS:
                ok = [r for r in index[(qid, limit, k)] if r['status'] == 'ok']
                identity[f'{qid}/k={k}'] = dict(repeats=len(index[(qid, limit, k)]), ok=len(ok),
                                                distinct_ranked=len({tuple(r['ranked_ids']) for r in ok}),
                                                distinct_final=len({tuple(r['final_ids']) for r in ok}))
            base = records[('v1', 'a')][qid]
            if qid not in qsets['analysed'] or base['metrics']['r@10'] is None:
                continue
            base_hits = 0
            if base['status'] == 'ok':
                base_hits = len(set(gen0[('v1', qid, 'a')]['candidate_ids']) & gold)
            k20 = {r['repeat']: r for r in index[(qid, limit, 20)]}
            dr, lo, hi, lost_def, lost_pos, exact_all = [], [], [], 0, 0, True
            for run in sorted(index[(qid, limit, K)], key=lambda r: r['repeat']):
                if run['status'] == 'replay_miss':
                    continue
                r10 = 0.0 if run['status'] == 'system_failure' else mx.recall_at_k(run['ranked_ids'][:K], gold, K)
                dr.append(r10 - base['metrics']['r@10'])
            for repeat, run in sorted(k20.items()):
                if run['status'] == 'replay_miss':
                    continue
                ranked = [] if run['status'] == 'system_failure' else run['ranked_ids']
                if limit < FILTER_OFF_DEPTH and len(ranked) >= FILTER_OFF_DEPTH:
                    raise ValueError(f'LIMIT {limit} run {qid}/repeat {repeat} ranks {len(ranked)} ids: the k=20 '
                                     'list must hold every candidate (Addendum 4 section 7)')
                hits = len(set(ranked) & gold)
                exact = len(ranked) < FILTER_OFF_DEPTH
                upper = hits if exact else min(len(gold), hits + max(0, limit - len(ranked)))
                exact_all &= exact
                lo.append(mx.ratio(hits, len(gold)))
                hi.append(mx.ratio(upper, len(gold)))
                lost_def += upper < base_hits
                lost_pos += hits < base_hits
            base_cr = base['metrics']['candidate_recall']
            per_query[qid] = dict(
                dr10_mean=_r(statistics.fmean(dr)) if dr else None, repeats_scored=len(dr),
                candidate_recall=dict(exact=exact_all, lo=_r(statistics.fmean(lo)) if lo else None,
                                      hi=_r(statistics.fmean(hi)) if hi else None),
                d_candidate_recall=dict(lo=_r(statistics.fmean(lo) - base_cr) if lo else None,
                                        hi=_r(statistics.fmean(hi) - base_cr) if hi else None),
                repeats_losing_relevant_candidate=dict(definitely=lost_def, possibly=lost_pos))
        analysed = [q for q in qsets['analysed'] if q in per_query]
        n = len(analysed)
        per_limit[str(limit)] = dict(
            descriptive=True, per_query=per_query, repeat_identity=identity, replay_misses=misses[limit],
            mean_dr10=describe([per_query[q]['dr10_mean'] for q in analysed]),
            mean_d_candidate_recall=dict(lo=describe([per_query[q]['d_candidate_recall']['lo'] for q in analysed]),
                                         hi=describe([per_query[q]['d_candidate_recall']['hi'] for q in analysed])),
            share_queries_losing_relevant_candidate=dict(
                definitely=_r(sum(per_query[q]['repeats_losing_relevant_candidate']['definitely'] > 0
                                  for q in analysed) / n) if n else None,
                possibly=_r(sum(per_query[q]['repeats_losing_relevant_candidate']['possibly'] > 0
                                for q in analysed) / n) if n else None, n=n))
    return dict(descriptive=True, arm='v1', variant='a', gen_index=0, test='none (dose-response description)',
                limits=per_limit)


def stability(generations, noob_rows, queries):
    """Gens 0-9 (Addendum 1 b counts only; Addendum 4 section 9): obligation-filter rate, candidate count
    min/max, distinct candidate sets, pairwise Jaccard (descriptive). No R@10 spread."""
    groups = defaultdict(list)
    for row in generations:
        groups[(row['arm'], row['qid'], row['variant'])].append(row)
    for row in noob_rows:
        groups[(NOOB, row['qid'], row['variant'])].append(row)
    out = []
    for arm in PIPELINE:
        for q in queries:
            for variant in ('a', 'b'):
                rows = sorted(groups.get((arm, q['qid'], variant), []), key=lambda r: r['gen_index'])
                if not rows:
                    continue
                usable = [r for r in rows if r.get('candidate_ids') is not None
                          and r.get('status') in ('ok', 'rewritten', 'equal_v1')]
                sets = [frozenset(r['candidate_ids']) for r in usable]
                pairs = [jaccard(a, b) for a, b in combinations(sets, 2)]
                item = dict(arm=arm, qid=q['qid'], variant=variant, gens=len(rows), usable=len(usable),
                            status=dict(sorted(Counter(r['status'] for r in rows).items())),
                            candidate_count_min=min((len(s) for s in sets), default=None),
                            candidate_count_max=max((len(s) for s in sets), default=None),
                            distinct_candidate_sets=len(set(sets)),
                            jaccard=dict(descriptive=True, pairs=len(pairs),
                                         mean=_r(statistics.fmean(pairs)) if pairs else None,
                                         min=_r(min(pairs)) if pairs else None))
                if arm in ARMS:
                    gen0 = next((r for r in rows if r['gen_index'] == 0), None)
                    ok = [r for r in rows if r['status'] == 'ok']
                    item.update(obligation_filter_gen0=None if gen0 is None else bool(gen0['obligation_filter']),
                                obligation_filter_gens=sum(bool(r['obligation_filter']) for r in ok),
                                obligation_filter_rate=_r(sum(bool(r['obligation_filter']) for r in ok) / len(ok))
                                if ok else None)
                out.append(item)
    return out


def stagewise(records, qsets):
    """Pipeline arms, variant a: candidate recall, reduction ratio, conditional R@10, expansion dR and
    expansion precision (descriptive), per query and summarised."""
    out = []
    for arm in PIPELINE:
        recs = records[(arm, 'a')]
        row = dict(arm=arm, variant='a',
                   failures=sum(recs[q]['status'] == 'system_failure' for q in qsets['all']), summary={},
                   per_query={q: {m: _r(recs[q]['metrics'][m]) for m in STAGEWISE} | {'status': recs[q]['status']}
                              for q in qsets['all']})
        for metric in STAGEWISE:
            qids = qsets['analysed'] if metric in RELEVANCE else qsets['all']
            row['summary'][metric] = describe([recs[q]['metrics'][metric] for q in qids], metric)
        out.append(row)
    return out


def judged_report(records, runs, grades_by_qid, qsets):
    rows = []
    for system, variant in SCORED:
        recs = records[(system, variant)]
        rows.append(dict(system=system, variant=variant,
                         **{m: describe([recs[q]['metrics'][m] for q in qsets['all']], m)
                            for m in ('judged@5', 'judged@10', 'judged@final')}))
    limit_min = None
    for run in runs:
        if run['system'] == 'limit' and run['status'] == 'ok':
            judged = set(grades_by_qid.get(run['qid'], {}))
            for value in (judged_at(run['ranked_ids'], judged, 5), judged_at(run['ranked_ids'], judged, K),
                          judged_at(run['final_ids'], judged)):
                if value is not None:
                    if value < 1:
                        raise RuntimeError(f"STOP: judged@k < 1 for LIMIT run {run['qid']}/{run['limit']}/"
                                           f"{run['repeat']}/k={run['top_k']} (Addendum 4 section 5)")
                    limit_min = value if limit_min is None else min(limit_min, value)
    return dict(descriptive=True, definition='|top-k ∩ judged| / min(k, list length), k = 5, 10, final',
                rule='must be 1.0 for every scored run; < 1 stops the analysis', scored=rows,
                limit_runs_min=limit_min)


def agreement_report(labels_dir, audit_dir, mode):
    labels_dir, audit_dir = Path(labels_dir), Path(audit_dir)
    name = 'agreement_abc.json' if mode == 'three_judge' else 'agreement_ab.json'
    agr = json.loads((labels_dir / name).read_text(encoding='utf-8'))
    check = json.loads((labels_dir / 'audit_check.json').read_text(encoding='utf-8'))
    meta = json.loads((labels_dir / 'qrels_meta.json').read_text(encoding='utf-8'))
    freeze = json.loads((audit_dir / 'audit_freeze.json').read_text(encoding='utf-8'))
    kappa = check.get('kappa_binary')
    sentence = A2_SENTENCE.format(n=freeze.get('n'), kappa='n/a' if kappa is None else f'{kappa:.2f}')
    return dict(
        source=name, mode=mode, judges=agr.get('judges_in_force'),
        agreement={k: v for k, v in agr.items() if k not in ('inputs',)},
        audit=dict(n=freeze.get('n'), kappa_binary=kappa, ci95_kappa_binary=check.get('ci95_kappa_binary'),
                   flag=check.get('flag'), secondary=check.get('secondary'),
                   disagreements=check.get('disagreements'),
                   median_seconds_per_item=freeze.get('median_seconds_per_item')),
        merge=dict(categories=meta.get('categories'), reason_codes=meta.get('reason_codes'),
                   override_flipped_side=meta.get('override_flipped_side'), source=meta.get('source'),
                   pairs=meta.get('pairs'), relevant=meta.get('relevant'),
                   two_judge_rule_items_missing_row=meta.get('two_judge_rule_items_missing_row')),
        sentences=dict(note_2=check.get('report_sentence'), addendum_2=sentence,
                       addendum_2_wording_note=None if mode != 'three_judge' else
                       'the Addendum 2 section 7 sentence names two judges; note 3 uses three (A, B, C): '
                       'the write-up adapts the judge list'))


# ---------------------------------------------------------------- manifest + gate
def _entry(base, rel, root_dir):
    path = Path(root_dir) / rel
    if not path.is_file():
        raise PermissionError(f'{LOCKED} {rel} missing')
    lf = base == 'repo'
    return dict(base=base, path=rel, sha256=file_sha(path) if lf else bytes_sha(path),
                hashing='lf_normalised' if lf else 'raw_bytes')


def write_qrels_manifest(out=None, manifest_path=None, repo=ROOT, now=None):
    """Write-once qrels freeze (Addendum 2 section 4 step 7; Addendum 4 section 12)."""
    out = Path(out or output_dir())
    manifest_path = Path(manifest_path or MANIFEST)
    if manifest_path.exists():
        raise FileExistsError(f'{manifest_path.name} exists; the qrels are already frozen')
    labels = out / 'labels'
    for rel in ('labels/qrels.tsv', 'labels/qrels_meta.json', 'labels/audit_check.json', 'audit/audit_freeze.json'):
        if not (out / rel).is_file():
            raise PermissionError(f'{rel} missing: run s8-merge-labels stage 3 (and s8-freeze-audit) first')
    check = json.loads((labels / 'audit_check.json').read_text(encoding='utf-8'))
    if check.get('flag') != 'PASS':
        raise PermissionError(f"audit {check.get('flag')}: no qrels freeze and no metric (Addendum 2 section 5)")
    meta = json.loads((labels / 'qrels_meta.json').read_text(encoding='utf-8'))
    mode = meta['mode']
    agreement = 'agreement_abc.json' if mode == 'three_judge' else 'agreement_ab.json'
    agr = json.loads((labels / agreement).read_text(encoding='utf-8'))
    if agr.get('mode') != mode or check.get('mode') != mode:
        raise ValueError(f'merge mode differs between qrels_meta.json, {agreement} and audit_check.json')
    partd = json.loads((Path(repo) / PARTD_FREEZE).read_text(encoding='utf-8'))
    guideline = f"retrieval_eval/S8_RELEVANCE_GUIDELINE_{partd['guideline_in_force']}.md"
    files = {'audit_freeze': _entry('run', 'audit/audit_freeze.json', out)}
    files.update({f'judge_{j}_freeze': _entry('run', f'labels/judge_{j}_freeze.json', out) for j in agr['judges_read']})
    files.update(agreement=_entry('run', f'labels/{agreement}', out),
                 audit_check=_entry('run', 'labels/audit_check.json', out),
                 qrels_meta=_entry('run', 'labels/qrels_meta.json', out), qrels=_entry('run', 'labels/qrels.tsv', out),
                 guideline=_entry('repo', guideline, repo), partD_freeze=_entry('repo', PARTD_FREEZE, repo))
    record = dict(purpose='S8 qrels freeze (Addendum 2 section 4 step 7; Addendum 4 section 12). s8-analyze '
                          'refuses unless every sha below matches.',
                  frozen_at=(now or datetime.now(timezone.utc)).isoformat(timespec='seconds'),
                  merge_mode=mode, judges_read=agr['judges_read'], judges_in_force=meta.get('judges_in_force'),
                  fallback_reason=meta.get('fallback_reason'), guideline_in_force=partd['guideline_in_force'],
                  audit_flag=check['flag'], files=files, label=LABEL)
    write_once(manifest_path, json.dumps(record, indent=2, sort_keys=True) + '\n')
    return record


def gate(out, manifest_path=None, repo=ROOT):
    """Part E lock: the manifest and qrels.tsv exist, every manifest sha matches, audit PASS, same merge mode."""
    out = Path(out)
    manifest_path = Path(manifest_path or MANIFEST)
    if not manifest_path.is_file():
        raise PermissionError(f'{LOCKED} {manifest_path.name} missing (run s8-qrels-manifest after the merge)')
    if not (out / 'labels' / 'qrels.tsv').is_file():
        raise PermissionError(f'{LOCKED} labels/qrels.tsv missing')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    files = manifest.get('files', {})
    if 'qrels' not in files or 'audit_check' not in files:
        raise PermissionError(f'{LOCKED} the manifest does not cover qrels.tsv and audit_check.json')
    for key, entry in sorted(files.items(), key=lambda kv: kv[0] != 'qrels'):  # qrels first
        path = (Path(repo) if entry['base'] == 'repo' else out) / entry['path']
        if not path.is_file():
            raise PermissionError(f"{LOCKED} {entry['path']} missing")
        sha = file_sha(path) if entry['hashing'] == 'lf_normalised' else bytes_sha(path)
        if sha != entry['sha256']:
            raise PermissionError(f"{LOCKED} {entry['path']} differs from {manifest_path.name} (sha256 mismatch)")
    check = json.loads((out / files['audit_check']['path']).read_text(encoding='utf-8'))
    if check.get('flag') != 'PASS':
        raise PermissionError(f"{LOCKED} audit {check.get('flag')}: no metric is computed (Addendum 2 section 5)")
    meta = json.loads((out / files['qrels_meta']['path']).read_text(encoding='utf-8'))
    if meta.get('mode') != manifest['merge_mode']:
        raise PermissionError(f'{LOCKED} merge mode differs from the manifest')
    return dict(gate='verified', manifest_sha256=bytes_sha(manifest_path), merge_mode=manifest['merge_mode'])


def _test_bypass(out):
    if 'pytest' not in sys.modules:
        raise PermissionError('_test_skip_gate is for tests only')
    if Path(out).resolve().is_relative_to(SEALED_ROOT.resolve()):
        raise PermissionError('_test_skip_gate never runs on a real run folder')
    meta = json.loads((Path(out) / 'labels' / 'qrels_meta.json').read_text(encoding='utf-8'))
    return dict(gate='TEST-ONLY bypass', manifest_sha256=None, merge_mode=meta['mode'])


# ---------------------------------------------------------------- outputs
def utc_stamp(now=None):
    return (now or datetime.now(timezone.utc)).strftime('%Y%m%dT%H%M%SZ')


def analysis_folder(out, now=None):
    """<run dir>/analysis/, or a new timestamped subfolder when it already holds files (never overwrite)."""
    base = Path(out) / 'analysis'
    if base.is_dir() and any(base.iterdir()):
        target = base / f'run_{utc_stamp(now)}'
        if target.exists():
            raise FileExistsError(f'{target} exists; analysis outputs are never overwritten')
        return target
    return base


def _fmt(value):
    if value is None:
        return 'n/a'
    if isinstance(value, float):
        return f'{value:.4f}'
    return str(value)


def _fmt_ci(ci):
    return 'n/a' if not ci else f'[{_fmt(ci[0])}, {_fmt(ci[1])}]'


def md_table(headers, rows):
    lines = ['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)]
    lines += ['| ' + ' | '.join(_fmt(c) for c in row) + ' |' for row in rows]
    return lines


def _head(title, descriptive=False):
    lines = [f'# {title}', '', f'> **{LABEL}**', '']
    if descriptive:
        lines += ['*Descriptive only (Addendum 4 section 8).*', '']
    return lines


def _metric_header(metric):
    return f'{metric} (descriptive)' if metric in DESCRIPTIVE else metric


def md_arms(rows, title):
    headers = ['system', 'variant', 'failures', 'n/a'] + [f'{_metric_header(m)} mean [median; min–max] (n)'
                                                          for m in METRICS]
    body = [[r['system'], r['variant'], r['failures'], r['not_applicable']]
            + [f"{_fmt(r[m]['mean'])} [{_fmt(r[m]['median'])}; {_fmt(r[m]['min'])}–{_fmt(r[m]['max'])}] "
               f"({r[m]['n']})" + (' (by definition)' if 'by_definition' in r[m] else '') for m in METRICS]
            for r in rows]
    return [f'## {title}', ''] + md_table(headers, body) + ['']


def md_primary(primary, qsets):
    lines = _head('S8 primary comparisons (variant a, gen 0)')
    lines += [WIDE.format(n=len(qsets['all'])), '', f"Analysed queries: {len(qsets['analysed'])}; zero-relevant (excluded from relevance means): "
              f"{', '.join(qsets['zero_relevant']) or 'none'}.", '']
    head = ['#', 'comparison', 'n', 'mean diff', 'bootstrap 95% CI', 'sign-flip p', 'p Holm', 't 95% CI', 't p',
            't p Holm', 'Holm level', 't CI at Holm level']
    body = []
    for c in primary:
        t = c['secondary']['paired_t']
        level = c['secondary']['holm_level']
        ci = t['ci']
        body.append([c['id'], f"{c['metric']}: {c['direction']}", c['n'], c['bootstrap']['mean'],
                     f"[{_fmt(c['bootstrap']['lo'])}, {_fmt(c['bootstrap']['hi'])}]",
                     c['secondary']['sign_flip_p'], c['secondary']['sign_flip_p_holm'],
                     _fmt_ci(ci.get('0.950000')), t['p'], c['secondary']['paired_t_p_holm'], level,
                     _fmt_ci(ci.get(f'{level:.6f}')) if level is not None else 'n/a'])
        s = c['sensitivity']
        body.append([f"{c['id']}s", f"sensitivity: without {SWAP_FLAG}", s['n'], s['bootstrap']['mean'],
                     f"[{_fmt(s['bootstrap']['lo'])}, {_fmt(s['bootstrap']['hi'])}]"] + ['—'] * 7)
    lines += md_table(head, body) + ['', 'Primary statistic: paired bootstrap (10,000 resamples, seed 20261019, '
                                         '95% percentile). Sign-flip p and paired t are secondary (Addendum 1 f).', '']
    for c in primary:
        lines += [f"## Comparison {c['id']}: {c['metric']}, {c['direction']}", '']
        if 'effective_n' in c:
            e = c['effective_n']
            lines += [f"Effective n: k = {e['k']} queries with a non-zero difference; mean over k = "
                      f"{_fmt(e['mean_over_k'])} (descriptive only).", '']
        if c['dropped']:
            lines += ['Dropped (n/a): ' + '; '.join(f"{d['qid']} ({d['reason']})" for d in c['dropped']), '']
        lines += md_table(['qid', c['x'], c['y'], 'diff', 'statuses', 'flag', 'report query', 'v1 obligation filter'],
                          [[r['qid'], r['x'], r['y'], r['diff'], f"{r['x_status']}/{r['y_status']}", r['flag'],
                            r['report_query'], r['v1_obligation_filter']] for r in c['per_query']]) + ['']
        d = c['diff_summary']
        lines += [f"Difference: mean {_fmt(d['mean'])}, median {_fmt(d['median'])}, min–max "
                  f"{_fmt(d['min'])}–{_fmt(d['max'])}.", '']
    return lines


def render(sections, qsets):
    md = {'primary': md_primary(sections['primary']['comparisons'], qsets)}
    sec = sections['secondary_arms']
    lines = _head('S8 secondary: every arm and system')
    for variant, rows in sec['by_variant'].items():
        lines += md_arms(rows, f'Variant {variant}')
    for group, block in sec['split_by_v1_obligation_filter'].items():
        lines += md_arms(block['arms'], f"Variant a, v1 filtered on obligation: {group} ({len(block['queries'])} queries)")
    md['secondary_arms'] = lines
    rq = sections['report_queries']
    lines = _head('S8 report queries R1–R3 (also in the main means)') + md_arms(rq['arms'], 'R1–R3, variant a')
    for qid, rows in rq['per_query'].items():
        lines += [f'## {qid}', ''] + md_table(['system', 'variant', 'status'] + [_metric_header(m) for m in METRICS],
                                              [[r['system'], r['variant'], r['status']]
                                               + [f'{_fmt(r[m])} (by definition)' if m in r['by_definition'] else r[m]
                                                  for m in METRICS]
                                               for r in rows]) + ['']
    md['report_queries'] = lines
    qh = sections['query_handling']
    lines = _head('S8 query handling and excluded queries')
    lines += [f"- zero-relevant (excluded from relevance means): {', '.join(qh['zero_relevant']) or 'none'}",
              f"- {SWAP_FLAG}: {', '.join(qh['swap_eligible']) or 'none'} (kept; Addendum 4 section 1)",
              f"- system failures: {qh['system_failures']}", f"- n/a runs: {qh['not_applicable']}", '']
    for qid, rows in qh['zero_relevant_report'].items():
        lines += [f'## {qid} (zero relevant)', ''] + md_table(
            ['system', 'variant', 'status', 'candidate count', 'top-10 grade 1', 'top-10 grade 0', 'highest grade'],
            [[r['system'], r['variant'], r['status'], r['candidate_count'], r['top10_grade1'], r['top10_grade0'],
              r['highest_grade']] for r in rows]) + ['']
    md['query_handling'] = lines
    lines = _head('S8 terminology gap (variants b and c)')
    lines += ['vector_all and BM25 rows are descriptive (Addendum 4 section 8).', '']
    lines += md_table(['system', 'metric', 'gap', 'definition', 'descriptive', 'mean', 'median', 'min', 'max', 'n']
                      + qh['analysed'],
                      [[r['system'], r['metric'], r['gap'], f"{r['minuend']} − {r['subtrahend']}",
                        r.get('descriptive', False), r['summary']['mean'], r['summary']['median'], r['summary']['min'],
                        r['summary']['max'], r['summary']['n']] + [r['per_query'][q] for q in qh['analysed']]
                       for r in sections['terminology_gap']['rows']]) + ['']
    md['terminology_gap'] = lines
    lim = sections['limit']
    lines = _head('S8 LIMIT experiment (v1, variant a, gen 0)', descriptive=True)
    for limit, block in lim['limits'].items():
        share = block['share_queries_losing_relevant_candidate']
        lines += [f'## LIMIT {limit}', '', f"Replay misses: {block['replay_misses']}. Mean ΔR@10: "
                  f"{_fmt(block['mean_dr10']['mean'])}. Mean Δcandidate recall: "
                  f"[{_fmt(block['mean_d_candidate_recall']['lo']['mean'])}, "
                  f"{_fmt(block['mean_d_candidate_recall']['hi']['mean'])}]. Share of queries losing ≥ 1 relevant "
                  f"candidate: definitely {_fmt(share['definitely'])}, possibly {_fmt(share['possibly'])} "
                  f"(n = {share['n']}).", '']
        lines += md_table(['qid', 'ΔR@10 (mean over repeats)', 'cand. recall exact', 'cand. recall lo', 'hi',
                           'Δcand. recall lo', 'hi', 'repeats losing (definitely)', '(possibly)'],
                          [[q, v['dr10_mean'], v['candidate_recall']['exact'], v['candidate_recall']['lo'],
                            v['candidate_recall']['hi'], v['d_candidate_recall']['lo'], v['d_candidate_recall']['hi'],
                            v['repeats_losing_relevant_candidate']['definitely'],
                            v['repeats_losing_relevant_candidate']['possibly']] for q, v in block['per_query'].items()])
        lines += ['', 'Repeat identity:', ''] + md_table(
            ['qid / k', 'repeats', 'ok', 'distinct ranked lists', 'distinct final lists'],
            [[key, v['repeats'], v['ok'], v['distinct_ranked'], v['distinct_final']]
             for key, v in block['repeat_identity'].items()]) + ['']
    md['limit'] = lines
    lines = _head('S8 stability over gens 0–9')
    lines += ['Jaccard is descriptive (Addendum 4 section 8). No R@10 spread (Addendum 4 section 9).', '']
    lines += md_table(['arm', 'qid', 'variant', 'gens', 'usable', 'obligation gen 0', 'obligation gens',
                       'obligation rate', 'cand. min', 'cand. max', 'distinct sets', 'Jaccard pairs',
                       'Jaccard mean (descriptive)', 'Jaccard min (descriptive)'],
                      [[s['arm'], s['qid'], s['variant'], s['gens'], s['usable'], s.get('obligation_filter_gen0'),
                        s.get('obligation_filter_gens'), s.get('obligation_filter_rate'), s['candidate_count_min'],
                        s['candidate_count_max'], s['distinct_candidate_sets'], s['jaccard']['pairs'],
                        s['jaccard']['mean'], s['jaccard']['min']] for s in sections['stability']['rows']]) + ['']
    md['stability'] = lines
    lines = _head('S8 stage-wise numbers (pipeline arms, variant a)')
    for row in sections['stagewise']['rows']:
        lines += [f"## {row['arm']} (failures: {row['failures']})", '']
        lines += md_table(['metric', 'mean', 'median', 'min', 'max', 'n', 'n/a'],
                          [[_metric_header(m), s['mean'], s['median'], s['min'], s['max'], s['n'], s['na']]
                           for m, s in row['summary'].items()]) + ['']
        lines += md_table(['qid', 'status'] + [_metric_header(m) for m in STAGEWISE],
                          [[q, v['status']] + [v[m] for m in STAGEWISE] for q, v in row['per_query'].items()]) + ['']
    md['stagewise'] = lines
    jr = sections['judged']
    lines = _head('S8 judged@k', descriptive=True) + [jr['definition'] + '; ' + jr['rule'] + '.', '']
    lines += md_table(['system', 'variant', 'judged@5 min', 'judged@10 min', 'judged@final min', 'n'],
                      [[r['system'], r['variant'], r['judged@5']['min'], r['judged@10']['min'],
                        r['judged@final']['min'], r['judged@final']['n']] for r in jr['scored']])
    lines += ['', f"LIMIT runs: minimum judged@k = {_fmt(jr['limit_runs_min'])}.", '']
    md['judged'] = lines
    ag = sections['agreement']
    lines = _head('S8 relevance labels: agreement, audit and merge')
    lines += [f"Mode: {ag['mode']}; source: {ag['source']}.", '', '```json',
              json.dumps(dict(agreement=ag['agreement'], audit=ag['audit'], merge=ag['merge']), indent=1,
                         sort_keys=True, ensure_ascii=False), '```', '', ag['sentences']['note_2'] or '', '',
              ag['sentences']['addendum_2'], '']
    if ag['sentences']['addendum_2_wording_note']:
        lines += [f"*Note: {ag['sentences']['addendum_2_wording_note']}.*", '']
    md['agreement'] = lines
    return {name: '\n'.join(lines) + '\n' for name, lines in md.items()}


# ---------------------------------------------------------------- pipeline
def _index_gen0(generations, noob_rows):
    gen0, noob0 = {}, {}
    for row in generations:
        if row['gen_index'] == 0:
            key = (row['arm'], row['qid'], row['variant'])
            if key in gen0:
                raise ValueError(f'duplicate gen-0 generation {key}')
            gen0[key] = row
    for row in noob_rows:
        if row['gen_index'] == 0:
            key = (row['qid'], row['variant'])
            if key in noob0:
                raise ValueError(f'duplicate gen-0 v1-noob row {key}')
            noob0[key] = row
    return gen0, noob0


def analyse(queries, runs, generations, noob_rows, qrels_rows, corpus_size, labels_dir, audit_dir, mode):
    """Every section of the analysis map, as JSON-ready dicts (no file writes)."""
    grades_by_qid = qrels_io.grades_by_query(qrels_rows)
    gen0, noob0 = _index_gen0(generations, noob_rows)
    qsets = query_sets(queries, grades_by_qid)
    flags = query_flags(qsets, gen0)
    records = build_records(queries, runs, gen0, noob0, grades_by_qid, corpus_size)
    primary = primary_comparisons(records, qsets, flags)
    by_variant = {v: arm_table(records, [k for k in SCORED if k[1] == v], qsets['analysed'], qsets['all'])
                  for v in ('a', 'b', 'c')}
    report_analysed = [q for q in qsets['report'] if q in qsets['analysed']]
    sections = dict(
        primary=dict(comparisons=primary, holm=dict(m=len(PRIMARY), alpha=ALPHA,
                                                   applies_to=['sign_flip_p', 'paired_t p']),
                     note=WIDE.format(n=len(qsets['all'])), primary_statistic='paired bootstrap'),
        secondary_arms=dict(by_variant=by_variant, split_by_v1_obligation_filter=split_by_v1_filter(records, qsets,
                                                                                                    flags)),
        report_queries=dict(queries=qsets['report'], analysed=report_analysed,
                            arms=arm_table(records, [(s, 'a') for s in (*PIPELINE, 'vector_all', 'bm25')],
                                           report_analysed, qsets['report']),
                            per_query={q: [dict(system=s, variant=v, status=records[(s, v)][q]['status'],
                                                by_definition=sorted(BY_DEFINITION) if s in FILTER_OFF else [],
                                                **{m: _r(records[(s, v)][q]['metrics'][m]) for m in METRICS})
                                           for s, v in SCORED] for q in qsets['report']}),
        query_handling=dict(qsets, flags=flags, zero_relevant_report=zero_relevant_report(records, qsets),
                            system_failures={f'{s}/{v}': [q for q, r in records[(s, v)].items()
                                                          if r['status'] == 'system_failure']
                                             for s, v in SCORED},
                            not_applicable={f'{s}/{v}': [dict(qid=q, reason=r['reason'])
                                                         for q, r in records[(s, v)].items() if r['status'] == 'n/a']
                                            for s, v in SCORED}),
        terminology_gap=dict(rows=terminology_gap(records, qsets)),
        limit=limit_experiment(queries, runs, records, gen0, grades_by_qid, qsets),
        stability=dict(rows=stability(generations, noob_rows, queries)),
        stagewise=dict(rows=stagewise(records, qsets)),
        judged=judged_report(records, runs, grades_by_qid, qsets),
        agreement=agreement_report(labels_dir, audit_dir, mode))
    sections['query_handling']['system_failures'] = {k: v for k, v in
                                                     sections['query_handling']['system_failures'].items() if v}
    sections['query_handling']['not_applicable'] = {k: v for k, v in
                                                    sections['query_handling']['not_applicable'].items() if v}
    return sections, qsets


def run_analysis(out=None, *, queries=None, clauses_path=None, corpus_size=None, expect_runs_sha=None,
                 manifest_path=None, repo=ROOT, now=None, _test_skip_gate=False):
    """Gate, then the analysis; returns (folder, {file: sha256})."""
    out = Path(out or output_dir())
    unlocked = _test_bypass(out) if _test_skip_gate else gate(out, manifest_path, repo)
    if queries is None:
        from retrieval_eval.s8_runs import load_inputs
        queries, _, _ = load_inputs(need_clauses=False)
    clauses_path = Path(clauses_path or CLAUSES)
    n_clauses = len(json.loads(clauses_path.read_text(encoding='utf-8')))
    if corpus_size is not None and corpus_size != n_clauses:
        raise ValueError(f'--corpus-size {corpus_size} != {n_clauses} clauses in {clauses_path.name} '
                         '(Addendum 4 section 11)')
    runs_sha = bytes_sha(out / 'runs.jsonl')
    if expect_runs_sha is not None and expect_runs_sha != runs_sha:
        raise ValueError('runs.jsonl sha256 differs from --expect-runs-sha')
    labels = out / 'labels'
    sections, qsets = analyse(queries, read_jsonl(out / 'runs.jsonl'), read_jsonl(out / 'generations.jsonl'),
                              read_jsonl(out / 'noob.jsonl'), qrels_io.read(labels / 'qrels.tsv'), n_clauses,
                              labels, out / 'audit', unlocked['merge_mode'])
    inputs = dict(unlocked, runs_sha256=runs_sha, generations_sha256=bytes_sha(out / 'generations.jsonl'),
                  noob_sha256=bytes_sha(out / 'noob.jsonl'), qrels_sha256=bytes_sha(labels / 'qrels.tsv'),
                  clauses_sha256=bytes_sha(clauses_path), corpus_size=n_clauses, queries=len(queries),
                  bootstrap=dict(resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED), note=INTERNSHIP_FREE)
    folder = analysis_folder(out, now)
    texts = {f'{name}.json': json.dumps(dict(label=LABEL, section=name, inputs=inputs, **body), indent=2,
                                        sort_keys=True, ensure_ascii=False) + '\n'
             for name, body in sections.items()}
    texts.update({f'{name}.md': text for name, text in render(sections, qsets).items()})
    texts['inputs.json'] = json.dumps(dict(label=LABEL, **inputs), indent=2, sort_keys=True) + '\n'
    for name, text in sorted(texts.items()):
        write_once(folder / name, text)
    return folder, {name: bytes_sha(folder / name) for name in sorted(texts)}


# ---------------------------------------------------------------- CLI
def build_parser():
    parser = argparse.ArgumentParser(prog='tasks.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    analyze = sub.add_parser('s8-analyze', help='Pre-registered S8 analysis (locked until the qrels freeze)')
    analyze.add_argument('--corpus-size', type=int, help='expected number of clauses in data/clauses.json')
    analyze.add_argument('--expect-runs-sha', help='expected sha256 of runs.jsonl (from the s8-pool log)')
    sub.add_parser('s8-qrels-manifest', help='Write-once qrels freeze retrieval_eval/s8_qrels_manifest.json')
    return parser


def main(argv=None):
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(levelname)s %(message)s')
    if args.command == 's8-qrels-manifest':
        record = write_qrels_manifest(output_dir(), MANIFEST)
        print(f"qrels manifest: mode {record['merge_mode']}, {len(record['files'])} files")
        print(f'{MANIFEST}  sha256={bytes_sha(MANIFEST)}')
        return 0
    folder, shas = run_analysis(output_dir(), corpus_size=args.corpus_size, expect_runs_sha=args.expect_runs_sha,
                                manifest_path=MANIFEST)
    print(f'analysis written to {folder} ({LABEL})')
    for name, sha in shas.items():
        print(f'{folder / name}  sha256={sha}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
