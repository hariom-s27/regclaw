"""S8 Part E0: pre-registered analysis + qrels manifest, tested on FAKE data only.

Every test runs under a poison guard: opening any sealed path (pool_key / item maps / audit labels by name,
the real data/runs/azure_openai/s8 tree, the judge kits or the calibration archive) fails the test.
"""
import builtins
from datetime import datetime, timezone
import io
import json
import math
import random
from pathlib import Path

import numpy as np
import pytest

from retrieval_eval import s8_analysis as m
from retrieval_eval.s8_labels import qrels_text
from retrieval_eval.s8_runs import ROOT, bytes_sha, jsonl_text

NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
REAL_OPEN = io.open  # saved before the guard: used only to plant the fake world's poison files
SEALED_NAMES = {'pool_key.jsonl', 'item_map.jsonl', 'practice_item_map.jsonl', 'labels_shyam_audit.csv'}
SEALED_PREFIXES = [str(ROOT / 'data' / 'runs' / 'azure_openai' / 's8').lower(), 'd:\\s8_judge', 'd:\\s8_calib_archive']


def _sealed(file):
    if isinstance(file, int):
        return False
    path = Path(file)
    text = str(path.resolve()).lower()
    return path.name in SEALED_NAMES or any(text.startswith(p) for p in SEALED_PREFIXES)


@pytest.fixture(autouse=True)
def poison(monkeypatch):
    real_builtin, real_io = builtins.open, io.open

    def guard(real):
        def wrapped(file, *a, **kw):
            assert not _sealed(file), f'sealed path opened: {file}'
            return real(file, *a, **kw)
        return wrapped
    monkeypatch.setattr(builtins, 'open', guard(real_builtin))
    monkeypatch.setattr(io, 'open', guard(real_io))


# ---------------------------------------------------------------- fake world
QIDS = ('R1', 'R2', 'Q1', 'Q2', 'Q7')
N = 80
CIDS = [f'doc:{i}' for i in range(N)]
FILTERED = ('R1', 'Q1')  # v1 gen 0 filters on obligation for these


def world(tmp, *, fail_v1_q1=False, noob_na_q2=False, replay_miss=False, limit25_varies=True, seed=7):
    """A consistent fake run folder: runs / generations / noob / qrels / merge outputs / clauses.json."""
    rng = random.Random(seed)
    out = tmp / 'run'
    (out / 'labels').mkdir(parents=True)
    (out / 'audit').mkdir()
    (out / 'sheet').mkdir()
    for poison_file in (out / 'pool_key.jsonl', out / 'sheet' / 'item_map.jsonl'):
        with REAL_OPEN(poison_file, 'w', encoding='utf-8') as stream:  # present, but must never be read
            stream.write('{"system": "SECRET"}\n')
    queries = [dict(qid=q) for q in QIDS]
    grades = {}
    for q in QIDS:
        g = {c: rng.choice([0, 0, 1, 2, 3]) for c in rng.sample(CIDS, 40)}
        if q == 'Q7':
            g = {c: min(v, 1) for c, v in g.items()}  # zero relevant
        if q == 'Q2':
            g = {c: min(v, 2) for c, v in g.items()}  # no grade 3 -> swap-eligible
        grades[q] = g
    runs, gens, noob = [], [], []

    def add(qid, system, arm, variant, top_k, ranked=None, neighbours=None, status='ok', **extra):
        runs.append(dict(qid=qid, system=system, arm=arm, variant=variant, top_k=top_k, gen_index=0, status=status,
                         ranked_ids=ranked, neighbour_ids=neighbours,
                         final_ids=None if ranked is None else ranked + neighbours, **extra))

    def pipeline_lists(cands, pool, k):
        judged = [c for c in cands if c in pool]
        ranked = rng.sample(judged, min(k, len(judged)))
        neighbours = rng.sample([c for c in pool if c not in ranked], 2)
        return ranked, neighbours

    for q in QIDS:
        pool = list(grades[q])
        outside = [c for c in CIDS if c not in pool]
        cand = {}
        for arm, version in (('v1', '1.0'), ('v1.1', '1.1')):
            for v in ('a', 'b'):
                c = rng.sample(pool, 25) + rng.sample(outside, 5)
                failed = fail_v1_q1 and (arm, q, v) == ('v1', 'Q1', 'a')
                cand[(arm, v)] = None if failed else c
                gens.append(dict(arm=arm, prompt_version=version, qid=q, variant=v, gen_index=0,
                                 status='generation_error' if failed else 'ok', filter_path='text2cypher',
                                 obligation_filter=arm == 'v1' and q in FILTERED,
                                 candidate_ids=[] if failed else sorted(c), candidate_count=0 if failed else 30))
                for g in range(1, 10):
                    sub = sorted(rng.sample(c, 20 + g % 3))
                    gens.append(dict(arm=arm, prompt_version=version, qid=q, variant=v, gen_index=g, status='ok',
                                     filter_path='text2cypher', obligation_filter=rng.random() < 0.5,
                                     candidate_ids=sub, candidate_count=len(sub)))
                for k in (10, 20):
                    if failed:
                        add(q, 'pipeline', arm, v, k, [], [], status='system_failure')
                    elif replay_miss and (arm, q, v, k) == ('v1.1', 'R2', 'b', 10):
                        add(q, 'pipeline', arm, v, k, status='replay_miss', reason='ReplayMiss')
                    else:
                        add(q, 'pipeline', arm, v, k, *pipeline_lists(c, pool, k))
            for k in (10, 20):  # variant c: stage 1 = the variant-a candidates
                if cand[(arm, 'a')] is None:
                    add(q, 'pipeline', arm, 'c', k, [], [], status='system_failure')
                else:
                    add(q, 'pipeline', arm, 'c', k, *pipeline_lists(cand[(arm, 'a')], pool, k))
        for v in ('a', 'b'):
            v1 = cand[('v1', v)]
            for g in range(10):
                if g == 0 and v1 is None:
                    row = dict(status='n/a', reason='v1 generation error', candidate_ids=None)
                elif noob_na_q2 and q == 'Q2' and v == 'a':
                    row = dict(status='n/a', reason='WHERE has a top-level OR / XOR / NOT', candidate_ids=None)
                elif q in FILTERED and g == 0:
                    row = dict(status='rewritten', reason='top-level AND conjunct of WHERE',
                               candidate_ids=sorted(set(v1) | set(rng.sample(pool, 6))))
                else:
                    row = dict(status='equal_v1', reason='no obligation predicate',
                               candidate_ids=sorted(v1 if v1 is not None else rng.sample(pool, 20)))
                noob.append(dict(qid=q, variant=v, gen_index=g, **row))
        noob0 = {r['variant']: r for r in noob if r['qid'] == q and r['gen_index'] == 0}
        for v in ('a', 'b', 'c'):
            src = noob0['a' if v == 'c' else v]
            for k in (10, 20):
                if cand[('v1', 'a' if v == 'c' else v)] is None:
                    add(q, 'pipeline', 'v1-noob', v, k, [], [], status='system_failure', derived_from='v1')
                elif src['status'] == 'n/a':
                    add(q, 'pipeline', 'v1-noob', v, k, status='n/a', reason=src['reason'])
                else:
                    add(q, 'pipeline', 'v1-noob', v, k, *pipeline_lists(src['candidate_ids'], pool, k))
        for v in ('a', 'b'):
            add(q, 'vector_all', None, v, 20, rng.sample(pool, 20), [])
            add(q, 'bm25', None, v, 20, rng.sample(pool, 20), [])
        add(q, 'hyde', None, 'c', 20, rng.sample(pool, 20), [])
        base = [c for c in cand[('v1', 'a')] if c in pool] if cand[('v1', 'a')] is not None else None
        for limit in (5, 10, 25):
            for repeat in range(10):
                for k in (10, 20):
                    if base is None:
                        add(q, 'limit', 'v1', 'a', k, [], [], status='system_failure', limit=limit, repeat=repeat)
                        continue
                    subset = list(base[:limit])
                    if limit == 25 and limit25_varies and repeat == 3:
                        subset = list(base[1:limit + 1])
                    add(q, 'limit', 'v1', 'a', k, subset[:min(k, limit)], [], limit=limit, repeat=repeat)
    (out / 'runs.jsonl').write_text(jsonl_text(runs), encoding='utf-8')
    (out / 'generations.jsonl').write_text(jsonl_text(gens), encoding='utf-8')
    (out / 'noob.jsonl').write_text(jsonl_text(noob), encoding='utf-8')
    rows = [dict(qid=q, clause_id=c, grade=str(g), judge='ABC_median', date='2026-10-22')
            for q in QIDS for c, g in sorted(grades[q].items())]
    labels = out / 'labels'
    (labels / 'qrels.tsv').write_text(qrels_text(rows), encoding='utf-8', newline='')
    write_merge_outputs(out)
    (tmp / 'clauses.json').write_text(json.dumps([dict(id=c) for c in CIDS]), encoding='utf-8')
    return out, queries, grades, runs


def write_merge_outputs(out, flag='PASS', mode='three_judge'):
    labels = out / 'labels'

    def dump(path, value):
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    dump(labels / 'qrels_meta.json', dict(mode=mode, judges_in_force=['A', 'B', 'C'], categories={'accepted_median': 9},
                                          reason_codes={'clear_reading': 1}, pairs=200, relevant=60,
                                          override_flipped_side=0, source={'ABC_median': 200},
                                          two_judge_rule_items_missing_row=0, fallback_reason=None))
    dump(labels / 'agreement_abc.json', dict(mode=mode, judges_read=['A', 'B', 'C'], judges_in_force=['A', 'B', 'C'],
                                             pairs={'A-B': {'kappa_binary': 0.8}}, fleiss={'kappa_binary': 0.7},
                                             accepted=180, threshold_disputes=20, inputs={'sheet_sha256': 'x'}))
    dump(labels / 'audit_check.json', dict(flag=flag, mode=mode, kappa_binary=0.72, ci95_kappa_binary={'lo': .5, 'hi': .9},
                                           secondary={'kappa_binary': 0.75}, disagreements=3,
                                           report_sentence='The author, a non-specialist, audited 60 items blind; ...'))
    for j in 'ABC':
        dump(labels / f'judge_{j}_freeze.json', dict(judge=j, files={}))
    dump(out / 'audit' / 'audit_freeze.json', dict(n=60, median_seconds_per_item=41.0))


def freeze(tmp, out):
    return m.write_qrels_manifest(out, tmp / 'manifest.json', repo=ROOT, now=NOW)


def analyse(tmp, out, queries, **kw):
    kw.setdefault('manifest_path', tmp / 'manifest.json')
    return m.run_analysis(out, queries=queries, clauses_path=tmp / 'clauses.json', now=NOW, **kw)


def load(folder, name):
    return json.loads((folder / name).read_text(encoding='utf-8'))


# ---------------------------------------------------------------- hand-computed metrics
GRADES = {'a': 3, 'b': 2, 'c': 0, 'd': 1, 'e': 2}  # gold (>= 2) = {a, b, e}


def run_record(ranked, neighbours=(), system='pipeline', status='ok'):
    return dict(qid='Q1', system=system, arm='v1' if system == 'pipeline' else None, variant='a', top_k=10,
                gen_index=0, status=status, ranked_ids=list(ranked), neighbour_ids=list(neighbours),
                final_ids=list(ranked) + list(neighbours))


def test_pipeline_metrics_hand_computed():
    got = m.score_run(run_record(['c', 'a', 'd', 'b'], ['e']), ['c', 'a', 'd', 'b', 'e', 'x'], GRADES, 20)
    dcg = 7 / math.log2(3) + 1 / math.log2(4) + 3 / math.log2(5)
    idcg = 7 / 1 + 3 / math.log2(3) + 3 / math.log2(4) + 1 / math.log2(5)
    assert got['candidate_recall'] == 1.0                 # |C ∩ G| / |G| = 3/3
    assert got['r@5'] == pytest.approx(2 / 3) and got['r@10'] == pytest.approx(2 / 3)
    assert got['conditional_r@10'] == pytest.approx(2 / 3)  # 2 / |C ∩ G|
    assert got['mrr@10'] == 0.5                           # first relevant at rank 2
    assert got['ndcg@10'] == pytest.approx(dcg / idcg) and got['ndcg@10'] == pytest.approx(0.573618, abs=1e-6)
    assert got['reduction_ratio'] == pytest.approx(1 - 6 / 20)
    assert got['r@final'] == 1.0 and got['expansion_dr'] == pytest.approx(1 / 3)
    assert got['expansion_precision'] == 1.0              # 1 relevant of 1 added


def test_conditional_recall_and_cutoffs():
    ranked = ['c', 'd', 'x1', 'x2', 'x3', 'a', 'x4', 'x5', 'x6', 'x7', 'b']
    grades = dict(GRADES, **{f'x{i}': 0 for i in range(1, 8)})
    got = m.score_run(run_record(ranked), ranked + ['e'], grades, 100)
    assert got['r@5'] == 0.0 and got['r@10'] == pytest.approx(1 / 3)  # b at rank 11 is past the cutoff
    assert got['conditional_r@10'] == pytest.approx(1 / 3)             # 1 of the 3 relevant candidates
    assert got['mrr@10'] == pytest.approx(1 / 6)
    got = m.score_run(run_record(['c', 'd']), ['c', 'd'], GRADES, 10)
    assert got['candidate_recall'] == 0.0 and got['conditional_r@10'] is None  # 0/0 -> n/a
    assert got['expansion_precision'] is None                                  # nothing added: 0/0 -> n/a


def test_filter_off_cut_to_10_and_no_stage_3():
    ranked = ['c', 'd'] + [f'x{i}' for i in range(8)] + ['a', 'b']
    got = m.score_run(run_record(ranked, system='vector_all'), None, GRADES, 50)
    assert got['r@10'] == 0.0 and got['mrr@10'] == 0.0  # a and b are at ranks 11-12
    assert got['candidate_recall'] == 1.0 and got['reduction_ratio'] == 0.0
    assert got['r@final'] is None and got['expansion_dr'] is None and got['expansion_precision'] is None


def test_system_failure_scores_zero_and_na():
    got = m.score_run(run_record([], [], status='system_failure'), [], GRADES, 20)
    assert all(got[k] == 0.0 for k in ('candidate_recall', 'r@5', 'r@10', 'mrr@10', 'ndcg@10', 'r@final'))
    assert got['conditional_r@10'] is None and got['expansion_precision'] is None and got['reduction_ratio'] is None
    assert got['expansion_dr'] == 0.0  # Addendum 4 note 1 section 2: 0 - 0, stays in the mean


def test_ranking_outside_candidates_is_a_join_error():
    with pytest.raises(ValueError, match='join error'):
        m.score_run(run_record(['a', 'z']), ['a'], GRADES, 20)


def test_judged_at_k():
    judged = {'a', 'b', 'c'}
    assert m.judged_at(['a', 'b'], judged, 5) == 1.0          # min(k, length) = 2
    assert m.judged_at(['a', 'z', 'b', 'c'], judged, 2) == 0.5
    assert m.judged_at(['a', 'z'], judged) == 0.5 and m.judged_at([], judged, 10) is None


# ---------------------------------------------------------------- statistics
def test_bootstrap_constants_match_the_part_e_freeze():
    freeze = json.loads((ROOT / 'retrieval_eval/s8_partE_freeze.json').read_text(encoding='utf-8'))
    assert (m.BOOTSTRAP_RESAMPLES, m.BOOTSTRAP_SEED) == (10_000, 20261019)
    assert freeze['bootstrap']['resamples'] == m.BOOTSTRAP_RESAMPLES and freeze['bootstrap']['seed'] == m.BOOTSTRAP_SEED
    assert freeze['label'] == m.LABEL == 'provisional (GPT): stage 1 = Azure gpt-5-mini'


def test_bootstrap_uses_the_seed_and_is_deterministic(monkeypatch):
    d = [0.1, -0.2, 0.0, 0.3, 0.25, -0.05]
    a, b = m.paired_bootstrap(d), m.paired_bootstrap(d)
    assert a == b and a['seed'] == 20261019 and a['resamples'] == 10_000
    idx = np.random.default_rng(20261019).integers(len(d), size=(10_000, len(d)))
    lo, hi = np.percentile(np.asarray(d)[idx].mean(axis=1), [2.5, 97.5])
    assert (a['lo'], a['hi']) == (round(float(lo), 6), round(float(hi), 6))
    assert m.paired_bootstrap(d, seed=1) != a
    seen = []
    real = np.random.default_rng
    monkeypatch.setattr(np.random, 'default_rng', lambda seed: seen.append(seed) or real(seed))
    m.paired_bootstrap(d)
    assert seen == [20261019]


def test_exact_sign_flip_on_tiny_examples():
    # d = (1, 2, -0.5): |sum| over the 8 sign patterns = 3.5, 2.5, 1.5, 0.5, 0.5, 1.5, 3.5, 2.5; observed 2.5 -> 4/8
    assert m.sign_flip_p([1, 2, -0.5]) == 0.5
    assert m.sign_flip_p([1, 1, 1]) == 0.25            # only all-plus and all-minus reach |3|
    assert m.sign_flip_p([0, 0, 0]) == 1.0 and m.sign_flip_p([]) is None


def test_holm_known_values():
    assert m.holm([0.01, 0.04, 0.03]) == [0.03, 0.06, 0.06]
    assert m.holm([0.5, 0.6, 0.02]) == [1.0, 1.0, 0.06]
    assert m.holm([0.2, None, 0.01]) == [0.4, None, 0.03]
    assert m.holm_levels([0.04, 0.01, 0.03]) == pytest.approx([0.95, 1 - 0.05 / 3, 0.975])


# Reference quantiles: scipy.stats.t.ppf (scipy 1.17.1); the 0.975 values also match standard t tables.
T_REFERENCE = [(0.975, 2, 4.302653), (0.975, 5, 2.570582), (0.975, 10, 2.228139), (0.975, 15, 2.131450),
               (0.975, 30, 2.042272), (1 - 0.05 / 6, 15, 2.693739), (1 - 0.05 / 4, 10, 2.633767)]


@pytest.mark.parametrize('p,df,expected', T_REFERENCE)
def test_t_quantile_matches_reference(p, df, expected):
    assert m.t_ppf(p, df) == pytest.approx(expected, abs=1e-5)  # stricter than 4 decimals
    assert m.t_ppf(1 - p, df) == pytest.approx(-m.t_ppf(p, df))


def test_t_cdf_and_paired_t():
    assert m.t_cdf(2.0, 15) == pytest.approx(0.9680275, abs=1e-7)    # scipy.stats.t.cdf
    assert m.t_sf2(2.5, 9) == pytest.approx(0.03386183, abs=1e-8)    # 2 * scipy.stats.t.sf
    got = m.paired_t([1, 2, 3])                                       # scipy.stats.ttest_1samp([1, 2, 3], 0)
    assert got['p'] == pytest.approx(0.0741799, abs=1e-7)
    assert got['ci']['0.950000'] == pytest.approx([-0.484138, 4.484138], abs=1e-6)
    assert m.paired_t([0, 0, 0])['p'] == 1.0 and m.paired_t([1])['p'] is None


def test_effective_n():
    got = m.effective_n([0, 0.5, 0, -0.25])
    assert got['k'] == 2 and got['mean_over_k'] == 0.125


def test_query_sets_swap_and_zero_relevant():
    grades = {'R1': {'x': 3}, 'Q2': {'x': 2}, 'Q7': {'x': 1}, 'Q12': {'x': 2}}
    s = m.query_sets([dict(qid=q) for q in ('R1', 'Q2', 'Q7', 'Q12', 'Q3')], grades)
    assert s['zero_relevant'] == ['Q7', 'Q3']             # Q3 has no qrels rows at all
    assert s['swap_eligible'] == ['Q2', 'Q3']             # Q7 and Q12 are exempt deliberate negatives
    assert s['analysed'] == ['R1', 'Q2', 'Q12'] and s['sensitivity'] == ['R1', 'Q12']


# ---------------------------------------------------------------- full pipeline on fakes
def test_full_analysis_on_fakes(tmp_path):
    out, queries, grades, _ = world(tmp_path)
    freeze(tmp_path, out)
    folder, shas = analyse(tmp_path, out, queries)
    assert folder == out / 'analysis'
    names = {p.name for p in folder.iterdir()}
    for section in ('primary', 'secondary_arms', 'report_queries', 'query_handling', 'terminology_gap', 'limit',
                    'stability', 'stagewise', 'judged', 'agreement'):
        assert {f'{section}.json', f'{section}.md'} <= names
    for path in folder.iterdir():
        assert m.LABEL in path.read_text(encoding='utf-8'), path.name
    primary = load(folder, 'primary.json')
    assert primary['inputs']['gate'] == 'verified' and primary['inputs']['corpus_size'] == N
    assert primary['inputs']['runs_sha256'] == bytes_sha(out / 'runs.jsonl')
    comps = primary['comparisons']
    assert [(c['metric'], c['x'], c['y']) for c in comps] == [('candidate_recall', 'v1', 'v1-noob'),
                                                              ('r@10', 'v1', 'v1.1'), ('r@10', 'v1', 'vector_all')]
    for c in comps:
        qids = [r['qid'] for r in c['per_query']]
        assert qids == ['R1', 'R2', 'Q1', 'Q2'] and 'Q7' not in qids  # Q7: zero relevant
        assert [r['flag'] for r in c['per_query']] == ['', '', '', m.SWAP_FLAG]
        assert c['sensitivity']['n'] == 3 and c['sensitivity']['dropped_qids'] == ['Q2']
        assert c['bootstrap']['seed'] == 20261019 and c['bootstrap']['resamples'] == 10_000
        assert c['diff_summary']['mean'] == pytest.approx(np.mean([r['diff'] for r in c['per_query']]), abs=1e-6)
        assert set(c['secondary']) == {'sign_flip_p', 'sign_flip_p_holm', 'paired_t', 'paired_t_p_holm', 'holm_level'}
    assert 'effective_n' in comps[0] and 'effective_n' not in comps[1]
    assert comps[0]['effective_n']['k'] == sum(r['diff'] != 0 for r in comps[0]['per_query'])
    assert {c['per_query'][0]['v1_obligation_filter'] for c in comps} == {'yes'}  # R1 is filtered
    assert sorted(c['secondary']['holm_level'] for c in comps) == pytest.approx([.95, .975, 1 - .05 / 3])
    qh = load(folder, 'query_handling.json')
    assert qh['zero_relevant'] == ['Q7'] and qh['swap_eligible'] == ['Q2']
    z = qh['zero_relevant_report']['Q7']
    assert {r['highest_grade'] for r in z} <= {0, 1} and all(r['top10_grade1'] + r['top10_grade0'] == 10
                                                              for r in z if r['status'] == 'ok')
    arms = load(folder, 'secondary_arms.json')['by_variant']['a']
    v1 = next(r for r in arms if r['system'] == 'v1')
    assert v1['r@10']['n'] == 4 and v1['reduction_ratio']['n'] == 5   # relevance means: analysed; ratio: all
    assert v1['expansion_precision']['descriptive'] is True and v1['judged@10']['descriptive'] is True
    assert 'descriptive' not in v1['r@10']
    va = next(r for r in arms if r['system'] == 'vector_all')  # Addendum 4 note 1 section 3
    assert va['candidate_recall']['mean'] == 1.0 and va['reduction_ratio']['mean'] == 0.0
    assert {m for m in m.METRICS if 'by_definition' in va[m]} == {'candidate_recall', 'reduction_ratio',
                                                                 'conditional_r@10'}
    assert va['conditional_r@10']['mean'] == va['r@10']['mean']
    assert not any('by_definition' in v1[m] for m in m.METRICS)
    sec_md = (folder / 'secondary_arms.md').read_text(encoding='utf-8')
    va_line = next(line for line in sec_md.splitlines() if line.startswith('| vector_all | a |'))
    assert va_line.count('(by definition)') == 3
    assert '(by definition)' not in next(line for line in sec_md.splitlines() if line.startswith('| v1 | a |'))
    split = load(folder, 'secondary_arms.json')['split_by_v1_obligation_filter']
    assert split['yes']['queries'] == ['R1', 'Q1'] and split['no']['analysed'] == ['R2', 'Q2']
    rq = load(folder, 'report_queries.json')
    assert rq['queries'] == ['R1', 'R2'] and set(rq['per_query']) == {'R1', 'R2'}
    by_def = {r['system']: r['by_definition'] for r in rq['per_query']['R1']}
    assert by_def['hyde'] == by_def['bm25'] == sorted(m.BY_DEFINITION) and by_def['v1'] == []
    assert '(by definition)' in (folder / 'report_queries.md').read_text(encoding='utf-8')
    gap = load(folder, 'terminology_gap.json')['rows']
    by = {(r['system'], r['metric'], r['gap']): r for r in gap}
    assert ('v1', 'candidate_recall', 'c-a') not in by and ('bm25', 'r@10', 'c-a') not in by
    assert by[('vector_all', 'r@10', 'c-a')]['minuend'] == 'hyde(c)' and by[('vector_all', 'r@10', 'c-a')]['descriptive']
    assert 'descriptive' not in by[('v1', 'r@10', 'b-a')] and by[('v1', 'r@10', 'b-a')]['main_row']
    r1 = {r['system'] + r['variant']: r for r in rq['per_query']['R1']}
    assert by[('v1', 'r@10', 'b-a')]['per_query']['R1'] == pytest.approx(r1['v1b']['r@10'] - r1['v1a']['r@10'], abs=1e-6)
    stab = load(folder, 'stability.json')['rows']
    s = next(r for r in stab if (r['arm'], r['qid'], r['variant']) == ('v1', 'R1', 'a'))
    assert s['gens'] == 10 and s['jaccard']['pairs'] == 45 and s['jaccard']['descriptive'] is True
    assert s['obligation_filter_gen0'] is True and 0 <= s['obligation_filter_rate'] <= 1
    assert s['candidate_count_min'] == 20 and s['candidate_count_max'] == 30
    judged = load(folder, 'judged.json')
    assert judged['descriptive'] is True and judged['limit_runs_min'] == 1.0
    assert all(r['judged@final']['min'] == 1.0 for r in judged['scored'])
    stage = load(folder, 'stagewise.json')['rows']
    assert stage[0]['summary']['expansion_precision']['descriptive'] is True
    ag = load(folder, 'agreement.json')
    assert ag['audit']['n'] == 60 and ag['audit']['flag'] == 'PASS' and 'κ = 0.72' in ag['sentences']['addendum_2']
    assert 'inputs' not in ag['agreement'] and ag['sentences']['addendum_2_wording_note']
    md = (folder / 'primary.md').read_text(encoding='utf-8')
    assert m.SWAP_FLAG in md and 'sensitivity' in md and 'intervals are wide' in md
    assert '*Descriptive only' in (folder / 'limit.md').read_text(encoding='utf-8')
    assert shas == {p.name: bytes_sha(p) for p in sorted(folder.iterdir())}


def test_limit_experiment_on_fakes(tmp_path):
    out, queries, grades, _ = world(tmp_path)
    freeze(tmp_path, out)
    folder, _ = analyse(tmp_path, out, queries)
    lim = load(folder, 'limit.json')
    assert lim['descriptive'] is True and set(lim['limits']) == {'5', '10', '25'}
    for limit in ('5', '10'):
        block = lim['limits'][limit]
        assert block['descriptive'] is True and block['replay_misses'] == 0
        assert all(v['candidate_recall']['exact'] for v in block['per_query'].values())
        assert all(v['candidate_recall']['lo'] == v['candidate_recall']['hi'] for v in block['per_query'].values())
        assert block['repeat_identity']['R1/k=10']['distinct_ranked'] == 1
    l25 = lim['limits']['25']
    assert all(not v['candidate_recall']['exact'] for v in l25['per_query'].values())  # 20 ranked of 25
    assert all(v['candidate_recall']['lo'] <= v['candidate_recall']['hi'] for v in l25['per_query'].values())
    assert l25['repeat_identity']['R1/k=20'] == dict(repeats=10, ok=10, distinct_ranked=2, distinct_final=2)
    assert 'Q7' not in l25['per_query'] and 'Q7/k=10' in l25['repeat_identity']
    v1 = load(folder, 'report_queries.json')['per_query']['R1'][0]
    assert v1['system'] == 'v1' and v1['variant'] == 'a'


def test_limit_k20_list_must_hold_every_candidate(tmp_path):
    out, queries, grades, runs = world(tmp_path)
    for run in runs:
        if run['system'] == 'limit' and run['limit'] == 5 and run['top_k'] == 20 and run['qid'] == 'R1':
            run['ranked_ids'] = run['final_ids'] = list(grades['R1'])[:20]
    (out / 'runs.jsonl').write_text(jsonl_text(runs), encoding='utf-8')
    freeze(tmp_path, out)
    with pytest.raises(ValueError, match='must hold every candidate'):
        analyse(tmp_path, out, queries)


def test_system_failure_world(tmp_path):
    out, queries, _, _ = world(tmp_path, fail_v1_q1=True)
    freeze(tmp_path, out)
    folder, _ = analyse(tmp_path, out, queries)
    comps = load(folder, 'primary.json')['comparisons']
    q1 = next(r for r in comps[0]['per_query'] if r['qid'] == 'Q1')
    assert q1['x'] == 0.0 and q1['y'] == 0.0 and q1['x_status'] == q1['y_status'] == 'system_failure'
    assert next(r for r in comps[1]['per_query'] if r['qid'] == 'Q1')['x'] == 0.0  # never excluded (Addendum 3)
    arms = load(folder, 'secondary_arms.json')['by_variant']['a']
    v1 = next(r for r in arms if r['system'] == 'v1')
    assert v1['failures'] == 1 and v1['conditional_r@10']['na'] == 1 and v1['reduction_ratio']['na'] == 1
    assert v1['candidate_recall']['na'] == 0
    qh = load(folder, 'query_handling.json')
    assert qh['system_failures'] == {'v1/a': ['Q1'], 'v1/c': ['Q1'], 'v1-noob/a': ['Q1'], 'v1-noob/c': ['Q1']}
    assert load(folder, 'limit.json')['limits']['10']['per_query']['Q1']['candidate_recall']['lo'] == 0.0


def test_noob_na_dropped_from_comparison_1_only(tmp_path):
    out, queries, _, _ = world(tmp_path, noob_na_q2=True)
    freeze(tmp_path, out)
    folder, _ = analyse(tmp_path, out, queries)
    comps = load(folder, 'primary.json')['comparisons']
    assert [r['qid'] for r in comps[0]['per_query']] == ['R1', 'R2', 'Q1'] and comps[0]['n'] == 3
    assert comps[0]['dropped'][0]['qid'] == 'Q2' and 'top-level OR' in comps[0]['dropped'][0]['reason']
    assert comps[1]['n'] == comps[2]['n'] == 4
    assert load(folder, 'query_handling.json')['not_applicable']['v1-noob/a'][0]['qid'] == 'Q2'


def test_replay_miss_on_a_scored_run_stops(tmp_path):
    out, queries, _, _ = world(tmp_path, replay_miss=True)
    freeze(tmp_path, out)
    with pytest.raises(RuntimeError, match='replay_miss'):
        analyse(tmp_path, out, queries)
    assert not (out / 'analysis').exists()


def test_judged_below_one_stops(tmp_path):
    out, queries, _, runs = world(tmp_path)
    run = next(r for r in runs if (r['system'], r['variant'], r['qid']) == ('bm25', 'a', 'R1'))
    run['ranked_ids'][3] = run['final_ids'][3] = 'doc:unjudged'
    (out / 'runs.jsonl').write_text(jsonl_text(runs), encoding='utf-8')
    freeze(tmp_path, out)
    with pytest.raises(RuntimeError, match='judged'):
        analyse(tmp_path, out, queries)


# ---------------------------------------------------------------- manifest + gate
def test_manifest_contents_and_write_once(tmp_path):
    out, _, _, _ = world(tmp_path)
    record = freeze(tmp_path, out)
    assert record['merge_mode'] == 'three_judge' and record['guideline_in_force'] == 'v1.0.1'
    assert set(record['files']) == {'audit_freeze', 'judge_A_freeze', 'judge_B_freeze', 'judge_C_freeze', 'agreement',
                                    'audit_check', 'qrels_meta', 'qrels', 'guideline', 'partD_freeze'}
    assert record['files']['qrels']['sha256'] == bytes_sha(out / 'labels/qrels.tsv')
    assert record['files']['guideline']['path'] == 'retrieval_eval/S8_RELEVANCE_GUIDELINE_v1.0.1.md'
    assert record['files']['guideline']['hashing'] == 'lf_normalised'
    assert json.loads((tmp_path / 'manifest.json').read_text(encoding='utf-8')) == record
    with pytest.raises(FileExistsError):
        freeze(tmp_path, out)


def test_manifest_refuses_audit_stop_and_missing_qrels(tmp_path):
    out, _, _, _ = world(tmp_path)
    write_merge_outputs(out, flag='STOP')
    with pytest.raises(PermissionError, match='audit STOP'):
        freeze(tmp_path, out)
    (out / 'labels/qrels.tsv').unlink()
    with pytest.raises(PermissionError, match='qrels.tsv missing'):
        freeze(tmp_path, out)
    assert not (tmp_path / 'manifest.json').exists()


def test_gate_refuses_without_manifest_or_qrels(tmp_path):
    out, queries, _, _ = world(tmp_path)
    with pytest.raises(PermissionError, match=m.LOCKED):
        analyse(tmp_path, out, queries)
    freeze(tmp_path, out)
    (out / 'labels/qrels.tsv').rename(out / 'labels/qrels.moved')
    with pytest.raises(PermissionError, match=f'{m.LOCKED}.*qrels.tsv missing'):
        analyse(tmp_path, out, queries)


@pytest.mark.parametrize('rel', ['labels/qrels.tsv', 'labels/audit_check.json', 'labels/judge_B_freeze.json',
                                 'audit/audit_freeze.json', 'labels/agreement_abc.json'])
def test_gate_refuses_sha_mismatch(tmp_path, rel):
    out, queries, _, _ = world(tmp_path)
    freeze(tmp_path, out)
    with (out / rel).open('a', encoding='utf-8') as stream:
        stream.write('\n')
    with pytest.raises(PermissionError, match=f'{m.LOCKED}.*sha256 mismatch'):
        analyse(tmp_path, out, queries)
    assert not (out / 'analysis').exists()


def test_gate_refuses_audit_stop_even_with_matching_shas(tmp_path):
    out, queries, _, _ = world(tmp_path)
    record = freeze(tmp_path, out)
    (tmp_path / 'manifest.json').unlink()
    write_merge_outputs(out, flag='STOP')
    record['files']['audit_check']['sha256'] = bytes_sha(out / 'labels/audit_check.json')
    for key in ('qrels_meta', 'agreement', 'audit_freeze', 'judge_A_freeze', 'judge_B_freeze', 'judge_C_freeze'):
        record['files'][key]['sha256'] = bytes_sha(out / record['files'][key]['path'])
    (tmp_path / 'manifest.json').write_text(json.dumps(record), encoding='utf-8')
    with pytest.raises(PermissionError, match=f'{m.LOCKED}.*audit STOP'):
        analyse(tmp_path, out, queries)


def test_corpus_size_and_runs_sha_checks(tmp_path):
    out, queries, _, _ = world(tmp_path)
    freeze(tmp_path, out)
    with pytest.raises(ValueError, match='corpus-size'):
        analyse(tmp_path, out, queries, corpus_size=N + 1)
    with pytest.raises(ValueError, match='expect-runs-sha'):
        analyse(tmp_path, out, queries, expect_runs_sha='0' * 64)
    folder, _ = analyse(tmp_path, out, queries, corpus_size=N, expect_runs_sha=bytes_sha(out / 'runs.jsonl'))
    assert load(folder, 'inputs.json')['clauses_sha256'] == bytes_sha(tmp_path / 'clauses.json')


def test_outputs_are_write_once(tmp_path):
    out, queries, _, _ = world(tmp_path)
    freeze(tmp_path, out)
    first, shas = analyse(tmp_path, out, queries)
    second, shas2 = analyse(tmp_path, out, queries)
    assert second == first / 'run_20261022T120000Z' and shas2 == shas  # never overwritten; same content
    with pytest.raises(FileExistsError):
        analyse(tmp_path, out, queries)


def test_test_bypass_is_test_only_and_never_on_real_folders(tmp_path, monkeypatch):
    out, queries, _, _ = world(tmp_path)
    folder, _ = analyse(tmp_path, out, queries, _test_skip_gate=True)  # no manifest at all
    assert load(folder, 'inputs.json')['gate'] == 'TEST-ONLY bypass'
    with pytest.raises(PermissionError, match='real run folder'):
        m.run_analysis(m.SEALED_ROOT / 'azure_openai' / 's8' / 'c2', queries=queries, _test_skip_gate=True)
    monkeypatch.delitem(__import__('sys').modules, 'pytest')
    with pytest.raises(PermissionError, match='tests only'):
        m.run_analysis(out, queries=queries, _test_skip_gate=True)


def test_cli_has_no_bypass_option():
    parser = m.build_parser()
    options = {opt for action in parser._actions for opt in action.option_strings}
    for sub in parser._subparsers._group_actions[0].choices.values():
        options |= {opt for action in sub._actions for opt in action.option_strings}
    assert options and not any('skip' in o or 'test' in o or 'gate' in o or 'manifest' in o for o in options)
    with pytest.raises(SystemExit):
        m.build_parser().parse_args(['s8-analyze', '--_test_skip_gate'])


def test_cli_is_locked_without_the_manifest(tmp_path, monkeypatch):
    out, _, _, _ = world(tmp_path)
    monkeypatch.setattr(m, 'MANIFEST', tmp_path / 'absent_manifest.json')
    monkeypatch.setattr(m, 'output_dir', lambda: out)
    with pytest.raises(PermissionError, match=m.LOCKED):
        m.main(['s8-analyze'])
    assert not (out / 'analysis').exists()


def test_tasks_dispatches_both_commands():
    import tasks
    assert tasks.S8_D_COMMANDS['s8-analyze'] == tasks.S8_D_COMMANDS['s8-qrels-manifest'] == 'retrieval_eval.s8_analysis'


def test_poison_guard_trips(tmp_path):
    out, _, _, _ = world(tmp_path)
    for path in (out / 'pool_key.jsonl', out / 'sheet' / 'item_map.jsonl'):
        with pytest.raises(AssertionError, match='sealed path'):
            path.read_text(encoding='utf-8')
    with pytest.raises(AssertionError, match='sealed path'):
        open(ROOT / 'data/runs/azure_openai/s8/c2/runs.jsonl', encoding='utf-8')


def test_no_internship_numbers_in_the_code():
    text = (ROOT / 'retrieval_eval/s8_analysis.py').read_text(encoding='utf-8')
    for number in ('0.5639', '0.6465', '0.5724', '0.4863', '1,149', '1149', '72 candidates'):
        assert number not in text
