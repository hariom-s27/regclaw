"""S10 offline evaluation runner (R23).

    python -m eval.s10.runner --profiles P.yaml [--gold G.yaml] --out DIR \
        [--repeats 5] [--setting NAME[:effort=LEVEL]]... [--seed 0] [--n-boot 2000] \
        [--final-holdout [--manifest M.json]] [--llm fake]

Writes DIR/runs.jsonl (one line per tool call), DIR/summary.json (metrics + provenance)
and DIR/citation_review.csv (blank human-judgement sheet). Only `--llm fake` exists here:
the live run plugs a client factory into run() later. Order is interleaved across settings.
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
from standards.ruleset import MATRIX_FILE, RULESET_FILES_SHA256, RULESET_VERSION, check_ruleset, file_shas
from standards.tool import get_applicable_standards

from . import metrics as M
from .split_guard import file_sha, load_eval_files

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
SUMMARY_FORMAT = 's10-eval-summary.v1'
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
        self.last_request = self.last_response = None

    def __getattr__(self, name):          # model / provider / effort of the wrapped client
        return getattr(self.inner, name)

    def classify(self, request):
        request = copy.deepcopy(request)
        if self.setting.effort is not None:
            request.setdefault('output_config', {})['effort'] = self.setting.effort
        self.last_request = request
        self.last_response = self.inner.classify(request)
        return self.last_response

    @property
    def effort_effective(self):
        return (self.last_request or {}).get('output_config', {}).get('effort')


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
    return dict(setting=setting.name, effort_effective=llm.effort_effective, repeat=repeat,
                profile_id=entry['id'], split=entry['split'], stratum=entry['stratum'],
                llm_status=result['provenance']['llm_status'], model=result['provenance']['model'],
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
    data = data or load_data()
    retriever = retriever or FakeRetriever()
    clients = {s.name: client_factory(s) for s in settings}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    with (out_dir / 'runs.jsonl').open('w', encoding='utf-8') as fh:
        for i, (r, pid, s) in enumerate(plan_order(list(profiles), list(settings), repeats, seed)):
            record = dict(call_index=i, **run_one(profiles[pid], s, r, clients[s.name], retriever, data))
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
            records.append(record)
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


def score_setting(records, gold, *, n_boot, seed):
    out = dict(n_runs=len(records), n_profiles=len({r['profile_id'] for r in records}),
               effort_effective=sorted({str(r['effort_effective']) for r in records}),
               llm_status={s: sum(r['llm_status'] == s for r in records) for s in ('ok', 'fallback')},
               overclaim_hits=sum(len(r['overclaim_hits']) for r in records),
               wording_events={k: sum(r['wording_events'][k] for r in records) for k in ('banned_wording', 'banned_wording_note')},
               citation_validity={k: sum(r['citation_events'][k] for r in records) for k in ('kept', 'unretrieved_clause')})
    every = cells_for(records, gold or {})[1]
    out['blocked_downgrades'] = M.blocked_downgrades(every)
    out['stability'] = M.stability(every)
    if gold:
        scored, _, coverage = cells_for(records, gold)
        out['coverage'] = dict(n_cells=len(scored), **coverage)
        out['metrics'] = M.bootstrap_metrics(scored, n_boot=n_boot, seed=seed)
        out['upgrades'] = M.upgrade_pr(scored)
        out['disagreements'] = M.disagreement_table(scored)
    return out


def git_info():
    try:
        sha = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip())
        return dict(git_sha=sha, git_dirty=dirty)
    except (OSError, subprocess.CalledProcessError):
        return dict(git_sha='unknown', git_dirty=None)


def summarise(records, gold, *, settings, repeats, seed, n_boot, split, files, llm_name, retriever_name, now=None):
    actual = file_shas()
    provenance = dict(**git_info(), ruleset=sorted({r['ruleset'] for r in records}), ruleset_version=RULESET_VERSION,
                      ruleset_sha256=check_ruleset(), matrix_sha256=actual[MATRIX_FILE],
                      matrix_sha256_stored=RULESET_FILES_SHA256[MATRIX_FILE],
                      engine_version=ENGINE_VERSION, prompt_sha256=PROMPT_SHA256,
                      settings=[asdict(s) for s in settings],
                      effort_effective={s.name: sorted({str(r['effort_effective']) for r in records if r['setting'] == s.name})
                                        for s in settings},
                      repeats=repeats, seed=seed, n_boot=n_boot, split=split, llm=llm_name, retriever=retriever_name,
                      files={Path(f).name: file_sha(f) for f in files},
                      order='interleaved: repeat > seeded profile shuffle > rotated settings',
                      timestamp=now or datetime.now(timezone.utc).isoformat())
    by_setting = {s.name: score_setting([r for r in records if r['setting'] == s.name], gold, n_boot=n_boot, seed=seed)
                  for s in settings}
    return dict(format=SUMMARY_FORMAT, provenance=provenance, settings=by_setting,
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
    ap.add_argument('--manifest')
    ap.add_argument('--llm', choices=['fake'], default='fake',
                    help='only the offline FakeLLM exists here; the live run injects client_factory')
    args = ap.parse_args(argv)
    settings = args.settings or [Setting('default')]
    if len({s.name for s in settings}) != len(settings):
        ap.error('setting names must be unique')
    if args.repeats < 1:
        ap.error('--repeats must be >= 1')
    split, profiles, gold = load_eval_files(args.profiles, args.gold, final_holdout=args.final_holdout,
                                            manifest_path=args.manifest)
    client_factory = client_factory or (lambda setting: FakeLLM())
    retriever = retriever or FakeRetriever()
    records = run(profiles, settings, args.repeats, client_factory, retriever=retriever, seed=args.seed, out_dir=args.out)
    summary = summarise(records, gold, settings=settings, repeats=args.repeats, seed=args.seed, n_boot=args.n_boot,
                        split=split, files=[f for f in (args.profiles, args.gold) if f],
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
