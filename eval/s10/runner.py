"""S10 offline evaluation runner (R23).

    python -m eval.s10.runner --profiles P.yaml [--gold G.yaml] --out DIR \
        [--repeats 5] [--setting NAME[:effort=LEVEL]]... [--seed 0] [--n-boot 2000] \
        [--tags TAGS.json] [--final-holdout [--holdout-manifest M.json]...] [--llm fake]

Writes DIR/runs.jsonl (one line per tool call), DIR/summary.json (metrics + provenance)
and DIR/citation_review.csv (blank human-judgement sheet). Only `--llm fake` exists here:
the live run plugs a client factory (e.g. azure_client_factory) into run() later.
Order is interleaved across settings.

Each run gets llm_detail in {ok, truncated, refusal, parse_error, error, unavailable}, derived
from the wrapped client's last response; guard.py and its llm_status (ok/fallback) are unchanged.
summary.json reports metrics twice: primary = all runs as the tool returned them,
secondary = only runs with llm_detail == ok. With --tags every cell metric is also split by tag.
"""
import argparse
import copy
import csv
import json
import logging
import random
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from standards import ENGINE_VERSION
from standards.classifier import PROMPT_SHA256, FakeLLM
from standards.data_loader import load_data
from standards.demo import FakeRetriever
from standards.engine import RANK, evaluate
from standards.ruleset import (MATRIX_FILE, RULESET_FILES_SHA256, RULESET_VERSION, check_engine, check_ruleset,
                               engine_file_shas, file_shas)
from standards.tool import get_applicable_standards

from . import metrics as M
from .split_guard import file_sha, load_eval_files
from .tagger import TAGS, load_tags

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
SUMMARY_FORMAT = 's10-eval-summary.v1'
LLM_DETAILS = ('ok', 'truncated', 'refusal', 'parse_error', 'error', 'unavailable')
PRIMARY_SCOPE = 'primary: all runs as the tool returned them'
SECONDARY_SCOPE = 'secondary: only runs with llm_detail == ok'
# Azure S10 provenance fields copied into each run record (S10-09 v2 c).
LLM_PROVENANCE_KEYS = ('provider', 'reasoning_effort', 'max_completion_tokens', 'finish_reason', 'output_tokens',
                       'reasoning_tokens', 'cached_input_tokens', 'latency_s', 'attempts', 'http_attempts', 'http_log')
REVIEW_COLUMNS = ['profile_id', 'setting', 'repeat', 'item', 'label', 'cited_clause_ids',
                  'judgement_supported_partial_not', 'notes']


@dataclass(frozen=True)
class Setting:
    name: str
    effort: str | None = None


def parse_setting(text):
    """'name' or 'name:effort=high'."""
    name, _, rest = text.partition(':')
    opts = dict(kv.split('=', 1) for kv in rest.split(',') if kv)
    unknown = set(opts) - {'effort'}
    if not name or unknown:
        raise ValueError(f'bad --setting {text!r} (unknown keys {sorted(unknown)})')
    return Setting(name, opts.get('effort'))


class SettingLLM:
    """Applies a setting to the outgoing request and keeps the raw request/response."""

    def __init__(self, inner, setting):
        self.inner, self.setting = inner, setting
        self.last_request = self.last_response = self.last_error = None

    def __getattr__(self, name):          # model / provider / effort of the wrapped client
        return getattr(self.inner, name)

    def classify(self, request):
        request = copy.deepcopy(request)
        if self.setting.effort is not None:
            request.setdefault('output_config', {})['effort'] = self.setting.effort
        self.last_request = request
        self.last_response = self.last_error = None
        try:
            self.last_response = self.inner.classify(request)
        except Exception as exc:
            self.last_error = type(exc).__name__     # type only; the tool swallows the exception
            raise
        return self.last_response

    @property
    def effort_effective(self):
        """Effort actually sent. Azure ignores the request field and sends the client's own effort
        (recorded in the response provenance, S10-25); other clients use the request field."""
        sent = (getattr(self.last_response, 'provenance', None) or {})
        if sent.get('provider') == 'azure_openai' and sent.get('reasoning_effort'):
            return sent['reasoning_effort']
        if getattr(self.inner, 'provider', None) == 'azure_openai':
            return self.inner.effort
        return (self.last_request or {}).get('output_config', {}).get('effort')

    @property
    def http_attempts(self):
        """HTTP requests sent for the last call (transport retries + truncation re-run), or None
        when the client does not count them. Read from the client so failed calls count too."""
        log = getattr(self.inner, 'last_http_log', None)
        return None if log is None or self.last_request is None else len(log)


def llm_detail(response, error, llm_status):
    """One of LLM_DETAILS for a single run, from the wrapped client's last response/exception."""
    if error:
        return 'error'
    if response is None:
        return 'unavailable'
    stop, finish = getattr(response, 'stop_reason', None), getattr(response, 'finish_reason', None)
    if stop == 'max_tokens' or finish == 'length':
        return 'truncated'
    if stop == 'refusal' or finish == 'content_filter':
        return 'refusal'
    if stop in (None, 'unavailable'):
        return 'unavailable'
    if stop == 'end_turn':
        return 'ok' if llm_status == 'ok' else 'parse_error'     # empty or schema-invalid data
    return 'error'


def azure_client_factory(setting):
    """Live factory for S10c (never called in tests): one Azure client per setting, built with
    that setting's effort so the effort sent equals the setting (S10-25)."""
    from llm.azure_openai import AzureOpenAIAdapter
    return AzureOpenAIAdapter('S10', effort=setting.effort)


def plan_order(profile_ids, settings, repeats, seed):
    """Interleaved: repeat outer; per repeat a seeded profile shuffle; per profile every
    setting once, setting order rotated by (repeat + position) to spread position effects."""
    order = []
    for r in range(1, repeats + 1):
        ids = sorted(profile_ids)
        random.Random(f'{seed}:{r}').shuffle(ids)
        for i, pid in enumerate(ids):
            shift = (r + i) % len(settings)
            for s in settings[shift:] + settings[:shift]:
                order.append((r, pid, s))
    return order


def _llm_labels(response, status, parts):
    if status != 'ok' or response is None:
        return None
    payload = json.loads(response.data) if isinstance(response.data, str) else response.data
    labels = {}
    for item in payload['standards']:
        old = labels.get(item['part'])
        if old is None or RANK[item['label']] > RANK[old]:
            labels[item['part']] = item['label']
    return {p: labels.get(p, 'omitted') for p in parts} | {p: v for p, v in labels.items() if p not in parts}


def run_one(entry, setting, repeat, inner, retriever, data):
    llm = SettingLLM(inner, setting)
    result = get_applicable_standards(entry['profile'], retriever, llm=llm, data=data)
    rules = evaluate(entry['profile'], data)
    rule = {p['part']: p['label'] for p in rules['parts']}
    final = {p['part']: p['label'] for p in result['standards']}
    events = result['provenance']['disagreements']
    count = lambda kind: sum(e['type'] == kind for e in events)  # noqa: E731
    sent = getattr(llm.last_response, 'provenance', None) or {}
    return dict(setting=setting.name, effort_effective=llm.effort_effective, repeat=repeat,
                profile_id=entry['id'], split=entry['split'], stratum=entry['stratum'],
                llm_status=result['provenance']['llm_status'], model=result['provenance']['model'],
                llm_detail=llm_detail(llm.last_response, llm.last_error, result['provenance']['llm_status']),
                llm_error=llm.last_error, http_attempts=llm.http_attempts,
                llm_provenance={k: sent[k] for k in LLM_PROVENANCE_KEYS if k in sent},
                final=final, rule=rule, llm=_llm_labels(llm.last_response, result['provenance']['llm_status'], rule),
                # Diagnostic only (not scored): engine labels per ENDPOINT, merged over components.
                rule_endpoints={ep: lab for p in rules['parts'] for ep, lab in p['endpoints'].items()},
                citations={p['part']: p['clause_ids'] for p in result['standards']},
                citation_events=dict(unretrieved_clause=count('unretrieved_clause'),
                                     kept=sum(len(p['clause_ids']) for p in result['standards'])),
                wording_events=dict(banned_wording=count('banned_wording'),
                                    banned_wording_note=count('banned_wording_note')),
                overclaim_hits=result['provenance']['overclaim_hits'],
                disagreements=events, ruleset=result['ruleset'], ruleset_version=result['ruleset_version'],
                ruleset_sha256=result['ruleset_sha256'], prompt_sha256=result['provenance']['prompt_sha256'],
                usage=result['provenance'].get('usage'))


def run(profiles, settings, repeats, client_factory, *, retriever=None, seed=0, data=None, out_dir):
    """Execute every (repeat, profile, setting) call in interleaved order; write runs.jsonl."""
    check_ruleset()
    check_engine()
    data = data or load_data()
    retriever = retriever or FakeRetriever()
    clients = {s.name: client_factory(s) for s in settings}
    for s in settings:      # S10-25: an Azure client sends its own effort, so it must equal the setting's
        client = clients[s.name]
        if (s.effort is not None and getattr(client, 'provider', None) == 'azure_openai'
                and getattr(client, 'effort', None) != s.effort):
            raise ValueError(f'setting {s.name!r} asks for effort {s.effort!r} but its client sends '
                             f'{getattr(client, "effort", None)!r}; build one client per setting')
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    with (out_dir / 'runs.jsonl').open('w', encoding='utf-8') as fh:
        for i, (r, pid, s) in enumerate(plan_order(list(profiles), list(settings), repeats, seed)):
            record = dict(call_index=i, **run_one(profiles[pid], s, r, clients[s.name], retriever, data))
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
            records.append(record)
    misses = len(getattr(retriever, 'misses', ()))
    if misses:      # the tool swallows retriever errors; a frozen-candidate miss must never be scored
        raise RuntimeError(f'{misses} retrieval call(s) had no frozen candidates; no summary written')
    return records


def cells_for(records, gold):
    """Scored cells (gold items only) and all-output cells (for gold-free counts)."""
    scored, missing = [], 0
    for rec in records:
        for (pid, item), g in sorted(gold.items()):
            if pid != rec['profile_id']:
                continue
            if item not in rec['final']:
                missing += 1     # tool silent on a gold item: scored as not_applicable
            scored.append(dict(profile_id=pid, item=item, gold=g['label'], setting=rec['setting'],
                               repeat=rec['repeat'], final=rec['final'].get(item, 'not_applicable'),
                               rule=rec['rule'].get(item, 'not_applicable'),
                               llm=None if rec['llm'] is None else rec['llm'].get(item, 'omitted')))
    gold_items = {k for k in gold}
    unscored = sum((rec['profile_id'], item) not in gold_items for rec in records for item in rec['final'])
    every = [dict(profile_id=rec['profile_id'], item=item, gold=None, final=lab, rule=rec['rule'].get(item, 'not_applicable'),
                  llm=None if rec['llm'] is None else rec['llm'].get(item, 'omitted'), repeat=rec['repeat'],
                  setting=rec['setting']) for rec in records for item, lab in rec['final'].items()]
    return scored, every, dict(gold_items_missing_in_output=missing, output_items_without_gold=unscored)


def _gold_block(scored, *, n_boot, seed, rows=True):
    table = M.disagreement_table(scored)
    return dict(metrics=M.bootstrap_metrics(scored, n_boot=n_boot, seed=seed), upgrades=M.upgrade_pr(scored),
                disagreements=table if rows else dict(table=table['table']),
                profiles_with_under_call=M.profiles_with_under_call(scored))


def cell_metrics(records, gold, *, n_boot, seed, tags=None):
    """Every cell-level metric for a set of runs; with `tags`, the same metrics per tag."""
    scored, every, coverage = cells_for(records, gold or {})
    out = dict(blocked_downgrades=M.blocked_downgrades(every), stability=M.stability(every))
    if gold:
        out['coverage'] = dict(n_cells=len(scored), **coverage)
        out.update(_gold_block(scored, n_boot=n_boot, seed=seed))
    if tags is not None:
        tag_of = lambda c: tags.get((c['profile_id'], c['item']))  # noqa: E731
        out['by_tag'] = {}
        for tag in TAGS:
            e, sc = [c for c in every if tag_of(c) == tag], [c for c in scored if tag_of(c) == tag]
            sub = dict(n_output_cells=len(e), blocked_downgrades=M.blocked_downgrades(e), stability=M.stability(e))
            if gold:
                sub['n_cells'] = len(sc)
                sub.update(_gold_block(sc, n_boot=n_boot, seed=seed, rows=False))
            out['by_tag'][tag] = sub
        out['untagged_output_cells'] = sum(tag_of(c) is None for c in every)   # output parts without gold or tag
    return out


def score_setting(records, gold, *, n_boot, seed, tags=None):
    out = dict(n_runs=len(records), n_profiles=len({r['profile_id'] for r in records}),
               effort_effective=sorted({str(r['effort_effective']) for r in records}),
               llm_status={s: sum(r['llm_status'] == s for r in records) for s in ('ok', 'fallback')},
               llm_detail={d: sum(r['llm_detail'] == d for r in records) for d in LLM_DETAILS},
               http_attempts=sum(r['http_attempts'] or 0 for r in records),
               overclaim_hits=sum(len(r['overclaim_hits']) for r in records),
               wording_events={k: sum(r['wording_events'][k] for r in records) for k in ('banned_wording', 'banned_wording_note')},
               citation_validity={k: sum(r['citation_events'][k] for r in records) for k in ('kept', 'unretrieved_clause')})
    # Primary: all runs as the tool returned them (a failed LLM call falls back to the rule floor).
    out['metrics_scope'] = PRIMARY_SCOPE
    out.update(cell_metrics(records, gold, n_boot=n_boot, seed=seed, tags=tags))
    ok = [r for r in records if r['llm_detail'] == 'ok']
    out['secondary_ok_only'] = dict(metrics_scope=SECONDARY_SCOPE, n_runs=len(ok),
                                    n_profiles=len({r['profile_id'] for r in ok}),
                                    **cell_metrics(ok, gold, n_boot=n_boot, seed=seed, tags=tags))
    return out


def git_info():
    try:
        sha = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip())
        return dict(git_sha=sha, git_dirty=dirty)
    except (OSError, subprocess.CalledProcessError):
        return dict(git_sha='unknown', git_dirty=None)


def summarise(records, gold, *, settings, repeats, seed, n_boot, split, files, llm_name, retriever_name, now=None,
              tags=None):
    actual = file_shas()
    provenance = dict(**git_info(), ruleset=sorted({r['ruleset'] for r in records}), ruleset_version=RULESET_VERSION,
                      ruleset_sha256=check_ruleset(), matrix_sha256=actual[MATRIX_FILE],
                      matrix_sha256_stored=RULESET_FILES_SHA256[MATRIX_FILE],
                      engine_version=ENGINE_VERSION, engine_sha256=check_engine(),
                      engine_files_sha256=engine_file_shas(), prompt_sha256=PROMPT_SHA256,
                      settings=[asdict(s) for s in settings],
                      effort_effective={s.name: sorted({str(r['effort_effective']) for r in records if r['setting'] == s.name})
                                        for s in settings},
                      repeats=repeats, seed=seed, n_boot=n_boot, split=split, llm=llm_name, retriever=retriever_name,
                      files={Path(f).name: file_sha(f) for f in files},
                      order='interleaved: repeat > seeded profile shuffle > rotated settings',
                      timestamp=now or datetime.now(timezone.utc).isoformat())
    provenance['tag_split'] = ('by matrix_settled / always_rule / judgement' if tags is not None
                               else 'none (no --tags file given)')
    by_setting = {s.name: score_setting([r for r in records if r['setting'] == s.name], gold, n_boot=n_boot, seed=seed,
                                        tags=tags)
                  for s in settings}
    return dict(format=SUMMARY_FORMAT, provenance=provenance, settings=by_setting,
                metric_scopes=dict(primary=PRIMARY_SCOPE, secondary_ok_only=SECONDARY_SCOPE),
                intervals=dict(bootstrap=M.BOOTSTRAP_CI_LABEL + ': percentile bootstrap over profiles',
                               profiles_with_under_call='exact Clopper-Pearson 95% interval'),
                unit='ISO part (endpoint labels in runs.jsonl are diagnostic only)')


def write_review_csv(records, path, repeat=1):
    """Blank sheet for HUMAN citation precision/recall (one row per part, first repeat)."""
    with Path(path).open('w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(REVIEW_COLUMNS)
        for rec in records:
            if rec['repeat'] != repeat:
                continue
            for item, label in rec['final'].items():
                w.writerow([rec['profile_id'], rec['setting'], rec['repeat'], item, label,
                            ';'.join(rec['citations'].get(item, [])), '', ''])


def _nan_to_none(value):
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, dict):
        return {k: _nan_to_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_nan_to_none(v) for v in value]
    return value


def main(argv=None, *, client_factory=None, retriever=None, now=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--profiles', required=True)
    ap.add_argument('--gold')
    ap.add_argument('--out', required=True)
    ap.add_argument('--repeats', type=int, default=5)
    ap.add_argument('--setting', action='append', type=parse_setting, dest='settings')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--n-boot', type=int, default=2000)
    ap.add_argument('--final-holdout', action='store_true')
    ap.add_argument('--holdout-manifest', '--manifest', dest='manifests', action='append',
                    help='holdout sha manifest; repeatable (each holdout file must be in exactly one)')
    ap.add_argument('--tags', help='s10-cell-tags.v1 file; without it no metric is split by tag')
    ap.add_argument('--llm', choices=['fake'], default='fake',
                    help='only the offline FakeLLM exists here; the live run injects client_factory')
    args = ap.parse_args(argv)
    settings = args.settings or [Setting('default')]
    if len({s.name for s in settings}) != len(settings):
        ap.error('setting names must be unique')
    if args.repeats < 1:
        ap.error('--repeats must be >= 1')
    split, profiles, gold = load_eval_files(args.profiles, args.gold, final_holdout=args.final_holdout,
                                            manifest_path=args.manifests)
    tags = load_tags(args.tags, required=list(gold or {})) if args.tags else None
    client_factory = client_factory or (lambda setting: FakeLLM())
    retriever = retriever or FakeRetriever()
    records = run(profiles, settings, args.repeats, client_factory, retriever=retriever, seed=args.seed, out_dir=args.out)
    summary = summarise(records, gold, settings=settings, repeats=args.repeats, seed=args.seed, n_boot=args.n_boot,
                        tags=tags, split=split, files=[f for f in (args.profiles, args.gold, args.tags) if f],
                        llm_name=records[0]['model'] if records else None,
                        retriever_name=type(retriever).__name__, now=now)
    out = Path(args.out)
    (out / 'summary.json').write_text(json.dumps(_nan_to_none(summary), indent=2, sort_keys=True) + '\n', encoding='utf-8')
    write_review_csv(records, out / 'citation_review.csv')
    log.info('S10 eval: %d calls, split=%s, out=%s', len(records), split, out)
    return 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    sys.exit(main())
