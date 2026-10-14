"""Cached, ordered extraction with one prompt-cache warm-up and five workers."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import csv
import json
import os
from pathlib import Path
import threading
import time

import jsonschema

from .checks import ENTITY_TYPES, ENUMS, check_response, drift, valid_stop_reason
from .client import AnthropicClient, FakeClient
from .normalise import Normaliser, review_candidates, surface
from .prompt import SYSTEM_PROMPT
from .stdref import references

ROOT = Path(__file__).resolve().parents[1]
REJECT_FIELDS = ['clause_id', 'type', 'mention', 'reason']
REVIEW_FIELDS = ['type', 'candidate', 'existing', 'ratio', 'accept']


def json_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def csv_write(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)


def key_for(clause, client):
    schema = Path(__file__).with_name('schema.json').read_text(encoding='utf-8')
    return '|'.join([clause['id'], hashlib.sha256(clause['text'].encode()).hexdigest(),
                     hashlib.sha256((schema + SYSTEM_PROMPT).encode()).hexdigest(), client.model, client.effort])


def run_pipeline(clauses, client, *, root=ROOT, force=False, sleep=time.sleep):
    root = Path(root)
    cache_path = root / 'data/ner_cache.json'
    cache = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
    previous_path = root / 'data/ner.json'
    previous = json.loads(previous_path.read_text(encoding='utf-8')) if previous_path.exists() else []
    normaliser = Normaliser()
    usage, lock = Counter(), threading.Lock()
    requests_made = 0
    pending = [c for c in clauses if force or key_for(c, client) not in cache]
    hits = len(clauses)-len(pending)
    raw_results = {}

    def call(c, budget):
        nonlocal requests_made
        for attempt in range(4):
            try:
                with lock:
                    requests_made += 1
                raw = client.extract(c, max_tokens=budget)
                with lock:
                    usage.update({k: v or 0 for k, v in raw.usage.items() if isinstance(v, (int, float))})
                return raw
            except Exception as exc:
                status = getattr(exc, 'status_code', None)
                if status not in (429, 529) or attempt == 3:
                    raise
                sleep(2**attempt)

    def process(c):
        budget, retried_budget, retried_schema = 4000, False, False
        try:
            while True:
                raw = call(c, budget)
                if raw.stop_reason == 'max_tokens' and not retried_budget:
                    retried_budget = True; budget *= 2
                    continue
                if not valid_stop_reason(raw.stop_reason, client.mode):
                    return c['id'], {'error': 'stop_reason:' + str(raw.stop_reason), 'model': raw.model}
                try:
                    checked, rejects, review, grounding = check_response(raw.data, c['text'], normaliser)
                except jsonschema.ValidationError:
                    if not retried_schema:
                        retried_schema = True
                        continue
                    return c['id'], {'error': 'schema', 'model': raw.model}
                return c['id'], {'data': raw.data, 'model': raw.model, 'stop_reason': raw.stop_reason,
                                 'usage': raw.usage}
        except Exception as exc:
            # Preserve the id and avoid writing exception strings that may contain secrets.
            return c['id'], {'error': 'client:' + type(exc).__name__, 'model': client.model}

    if pending:
        # The first result must arrive before any worker can issue the next request.
        cid, result = process(pending[0])
        raw_results[cid] = result
        with ThreadPoolExecutor(max_workers=5) as pool:
            for cid, result in pool.map(process, pending[1:]):
                raw_results[cid] = result

    rows, rejects, review = [], [], []
    flags = Counter()
    grounding_totals = {k: Counter(total=0, rejected=0) for k in ENTITY_TYPES}
    resolved = Counter()
    existing = defaultdict(set)
    for old in previous:
        for kind in ENTITY_TYPES:
            existing[kind].update(e['canonical'] for e in old.get('entities', {}).get(kind, []))
    for c in clauses:
        key = key_for(c, client)
        cached = raw_results.get(c['id']) if c['id'] in raw_results else cache[key]
        ids, raw_refs = references(c['text'])
        row = {'id': c['id'], 'text_sha256': hashlib.sha256(c['text'].encode()).hexdigest(),
               'model': cached['model'], 'effort': client.effort,
               'status': 'error' if 'error' in cached else 'stub' if isinstance(client, FakeClient) else 'ok',
               'entities': {k: [] for k in ENTITY_TYPES}, 'keywords': [], 'iso_refs': ids, 'iso_refs_raw': raw_refs}
        if 'error' in cached:
            rejects.append({'clause_id': c['id'], 'type': 'response', 'mention': '', 'reason': cached['error']})
        else:
            # Recheck and renormalise cache hits, so alias edits never need another call.
            checked, bad, proposals, ground = check_response(cached['data'], c['text'], normaliser)
            flags.update(p['reason'] for p in proposals)
            cache[key] = cached
            rejects.extend(dict(clause_id=c['id'], **r) for r in bad)
            review.extend({k: p[k] for k in REVIEW_FIELDS} for p in proposals)
            for kind in ENTITY_TYPES:
                grounding_totals[kind].update(ground[kind])
                seen = set()
                for entity in checked['entities'][kind]:
                    canonical, alias = normaliser.canonical(entity['mention'], kind, c['text'],
                                                           entity['canonical'] if ENUMS[kind] else None)
                    if canonical in seen:
                        rejects.append({'clause_id': c['id'], 'type': kind, 'mention': entity['mention'], 'reason': 'normalised_duplicate'})
                        continue
                    seen.add(canonical)
                    if alias:
                        resolved[kind] += 1
                    else:
                        review.extend(review_candidates(canonical, existing[kind], kind))
                    row['entities'][kind].append(dict(entity, canonical=canonical))
                    existing[kind].add(canonical)
            canonicals = {e['canonical'] for es in row['entities'].values() for e in es}
            row['keywords'] = [k for k in checked['entities']['keywords'] if k not in canonicals][:8]
        rows.append(row)
    # Deduplicate proposals in stable order. No proposal changes an entity canonical.
    review = [dict(zip(REVIEW_FIELDS, values)) for values in dict.fromkeys(tuple(r[k] for k in REVIEW_FIELDS) for r in review)]
    json_write(cache_path, cache)
    json_write(root / 'data/ner.json', rows)
    csv_write(root / 'data/ner_rejects.csv', REJECT_FIELDS, rejects)
    csv_write(root / 'data/alias_review.csv', REVIEW_FIELDS, review)

    def run_metrics(rs):
        return {k: {'mean': sum(len(r['entities'][k]) for r in rs)/len(rs) if rs else 0,
                    'empty_rate': sum(not r['entities'][k] for r in rs)/len(rs) if rs else 0} for k in ENTITY_TYPES}

    comparable = [r['id'] for r in rows] == [r['id'] for r in previous]
    alerts = drift(run_metrics(rows), run_metrics(previous)) if comparable else []
    cost = (usage['input_tokens']*.10 + usage['cache_read_input_tokens']*.01 +
            usage['cache_creation_input_tokens']*.125 + usage['output_tokens']*.50) / 1_000_000
    summary = {'clauses': len(rows), 'calls': requests_made, 'cache_hits': hits, 'flags': dict(flags),
               'errors': sum(r['status'] == 'error' for r in rows), 'rejects': len(rejects),
               'review_rows': len(review), 'usage': dict(usage), 'cost_estimate_usd': cost,
               'grounding': {k: dict(g) for k, g in grounding_totals.items()}, 'drift_alerts': alerts,
               'types': {k: {'mentions': sum(len(r['entities'][k]) for r in rows),
                             'unique_canonicals': len({e['canonical'] for r in rows for e in r['entities'][k]}),
                             'resolved_alias': resolved[k],
                             'review_mentions': len({str(p['candidate']) for p in review if p['type'] == k})}
                         for k in ENTITY_TYPES}}
    print(f"NER: rows={len(rows)}, requests={requests_made}, uncached clauses={len(pending)}, cache hits={hits}, error rows={summary['errors']}, rejects={len(rejects)}, review rows={len(review)}, flags={dict(flags)}")
    if alerts:
        print('WARN run-level drift >25%; stopping summary for inspection:', json.dumps(alerts))
        return rows, summary
    for kind, s in summary['types'].items():
        g = grounding_totals[kind]
        denominator = s['mentions'] or 1
        print(f"{kind}: mentions={s['mentions']}, unique={s['unique_canonicals']}, alias={100*s['resolved_alias']/denominator:.1f}%, review={100*s['review_mentions']/denominator:.1f}%, grounding rejects={g['rejected']}/{g['total']} ({100*g['rejected']/(g['total'] or 1):.1f}%)")
    print(f'Cost estimate (R11 A1 rates): ${cost:.6f}; usage={dict(usage)}')
    if len(pending) > 1 and not usage['cache_read_input_tokens']:
        print('WARN cache-read tokens are zero after the first request')
    return rows, summary


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--limit', type=int)
    p.add_argument('--ids')
    p.add_argument('--force', action='store_true')
    p.add_argument('--stub', action='store_true')
    p.add_argument('--mode', choices=['structured', 'strict_tool'], default='structured')
    p.add_argument('--effort', default='low')
    args = p.parse_args(argv)
    clauses = json.loads((ROOT / 'data/clauses.json').read_text(encoding='utf-8'))
    if args.ids:
        wanted = set(args.ids.split(','))
        clauses = [c for c in clauses if c['id'] in wanted]
    if args.limit is not None:
        if args.limit < 0:
            p.error('--limit must be nonnegative')
        clauses = clauses[:args.limit]
    stub = args.stub or os.environ.get('REGCLAW_STUB') == '1' or not os.environ.get('ANTHROPIC_API_KEY')
    if stub:
        print('STUB MODE: entities are recorded fixtures, numbers are not meaningful.')
    client = FakeClient(effort=args.effort, mode=args.mode) if stub else AnthropicClient(effort=args.effort, mode=args.mode)
    _, summary = run_pipeline(clauses, client, force=args.force)
    return 2 if summary['drift_alerts'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
