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
from .client import FakeClient
from .normalise import Normaliser, review_candidates, surface
from .prompt import SYSTEM_PROMPT
from .stdref import references
from .live_controls import LiveControls, RunStopped, retry_delay, azure_cost
from llm.provider import live_configured, make_client, provenance

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
                     hashlib.sha256((schema + SYSTEM_PROMPT).encode()).hexdigest(),
                     getattr(client, 'provider', 'anthropic'), client.model, client.effort])


def run_pipeline(clauses, client, *, root=ROOT, force=False, sleep=time.sleep, controls=None,
                 output_dir=None, cache_path=None, normaliser_version=None):
    """output_dir/cache_path default to the provider run folder; with cache_path, the
    cache is read there and its updated copy is written to output_dir only."""
    started = time.perf_counter()
    root = Path(root)
    default_dir = root / 'data'
    if getattr(client, 'provider', 'anthropic') != 'anthropic':
        default_dir = root / 'data/runs' / client.provider / 's4'
        if client.effort != 'low':
            default_dir = default_dir / client.effort
    explicit_output = output_dir is not None
    output_dir = Path(output_dir) if explicit_output else default_dir
    footnote_path = root / 'data/footnotes.json'
    footnotes = defaultdict(list)
    if footnote_path.exists():
        for note in json.loads(footnote_path.read_text(encoding='utf-8')):
            footnotes[note['clause_id']].append(note)
    source_cache = Path(cache_path) if cache_path else output_dir / 'ner_cache.json'
    cache = json.loads(source_cache.read_text(encoding='utf-8')) if source_cache.exists() else {}
    cache_path = output_dir / 'ner_cache.json'
    if explicit_output and (output_dir.resolve() == default_dir.resolve()
                            or cache_path.resolve() == source_cache.resolve()):
        raise ValueError('An explicit output_dir must differ from the run folder and the source cache')
    previous_path = output_dir / 'ner.json'
    previous = json.loads(previous_path.read_text(encoding='utf-8')) if previous_path.exists() else []
    normaliser = Normaliser(version=normaliser_version) if normaliser_version else Normaliser()
    usage, lock = Counter(), threading.Lock()
    requests_made = 0
    pending = [c for c in clauses if force or key_for(c, client) not in cache]
    hits = len(clauses)-len(pending)
    raw_results = {}
    events = Counter()

    def call(c, budget):
        nonlocal requests_made
        for attempt in range(4):
            ticket = controls.before_request(c, budget) if controls else None
            try:
                with lock:
                    requests_made += 1
                raw = client.extract(c, max_tokens=budget)
                if controls:
                    controls.after_request(ticket, raw=raw)
                with lock:
                    usage.update({k: v or 0 for k, v in raw.usage.items() if isinstance(v, (int, float))})
                    events['stop:' + str(raw.stop_reason)] += 1
                return raw
            except Exception as exc:
                if controls:
                    controls.after_request(ticket, exc=exc)
                status = getattr(exc, 'status_code', None)
                with lock:
                    events['http:' + str(status)] += 1
                if status not in (429, 529) or attempt == 3:
                    raise
                with lock:
                    events['retry:rate_limit'] += 1
                sleep(retry_delay(exc, 2**attempt))

    def process(c):
        budget, retried_budget, retried_schema = 4000, False, False
        try:
            while True:
                raw = call(c, budget)
                if raw.stop_reason == 'max_tokens' and not retried_budget:
                    with lock:
                        events['retry:budget'] += 1
                    retried_budget = True; budget *= 2
                    continue
                if not valid_stop_reason(raw.stop_reason, client.mode):
                    return c['id'], {'error': 'stop_reason:' + str(raw.stop_reason), 'model': raw.model,
                                     'provenance': raw.provenance}
                try:
                    checked, rejects, review, grounding = check_response(raw.data, c['text'], normaliser)
                except jsonschema.ValidationError:
                    if not retried_schema:
                        with lock:
                            events['retry:schema'] += 1
                        retried_schema = True
                        continue
                    return c['id'], {'error': 'schema', 'model': raw.model, 'provenance': raw.provenance}
                result = {'data': raw.data, 'model': raw.model, 'stop_reason': raw.stop_reason,
                          'usage': raw.usage}
                if raw.provenance:
                    result['provenance'] = raw.provenance
                    result['latency_s'] = raw.latency_s
                    result['finish_reason'] = raw.finish_reason
                return c['id'], result
        except RunStopped:
            raise
        except Exception as exc:
            # Preserve the id and avoid writing exception strings that may contain secrets.
            return c['id'], {'error': 'client:' + type(exc).__name__, 'model': client.model}

    if pending:
        # The first result must arrive before any worker can issue the next request.
        cid, result = process(pending[0])
        raw_results[cid] = result
        def checkpoint():
            for c in pending:
                result = raw_results.get(c['id'])
                if result and 'error' not in result:
                    cache[key_for(c, client)] = result
            json_write(cache_path, cache)
            if controls:
                controls.completed(raw_results)
                print(f'PROVISIONAL Azure progress: {len(raw_results)}/{len(pending)} uncached clauses; '
                      f'calls={requests_made}, errors={sum("error" in r for r in raw_results.values())}', flush=True)
        checkpoint()
        workers = 3 if controls else 5
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # Bounded batches let error/cost guards halt further scheduling.
            for offset in range(1, len(pending), workers):
                for cid, result in pool.map(process, pending[offset:offset+workers]):
                    raw_results[cid] = result
                checkpoint()

    rows, rejects, review = [], [], []
    flags = Counter()
    grounding_totals = {k: Counter(total=0, rejected=0) for k in ENTITY_TYPES}
    resolved = Counter()
    review_mention_counts = Counter()
    existing = defaultdict(set)
    for old in previous:
        for kind in ENTITY_TYPES:
            existing[kind].update(e['canonical'] for e in old.get('entities', {}).get(kind, []))
    for c in clauses:
        key = key_for(c, client)
        cached = raw_results.get(c['id']) if c['id'] in raw_results else cache[key]
        ids, raw_refs = references(c['text'])
        if footnote_path.exists():
            raw_refs = [ref + ' [clause text]' for ref in raw_refs]
            for note in footnotes[c['id']]:
                note_ids, note_raw = references(note['text'])
                ids = list(dict.fromkeys(ids + note_ids))
                source = f"footnote {note['marker']}" if note['marker'] is not None else f"footnote page {note['page']} fallback"
                raw_refs.extend(ref + f' [{source}]' for ref in note_raw)
            raw_refs = list(dict.fromkeys(raw_refs))
        row = {'id': c['id'], 'text_sha256': hashlib.sha256(c['text'].encode()).hexdigest(),
               'model': cached['model'], 'effort': client.effort,
               'status': 'error' if 'error' in cached else 'stub' if isinstance(client, FakeClient) else 'ok',
               'entities': {k: [] for k in ENTITY_TYPES}, 'keywords': [], 'iso_refs': ids, 'iso_refs_raw': raw_refs,
               'normaliser_version': normaliser.version}
        details = cached.get('provenance') or provenance(client)
        if details:
            row['provenance'] = details
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
                        candidates = review_candidates(canonical, existing[kind], kind)
                        review.extend(candidates)
                        if candidates or any(p['type'] == kind and p['candidate'] == entity['mention'] for p in proposals):
                            review_mention_counts[kind] += 1
                    if alias and any(p['type'] == kind and p['candidate'] == entity['mention'] for p in proposals):
                        review_mention_counts[kind] += 1
                    row['entities'][kind].append(dict(entity, canonical=canonical))
                    existing[kind].add(canonical)
            canonicals = {e['canonical'] for es in row['entities'].values() for e in es}
            row['keywords'] = [k for k in checked['entities']['keywords'] if k not in canonicals][:8]
        rows.append(row)
    # Deduplicate proposals in stable order. No proposal changes an entity canonical.
    review = [dict(zip(REVIEW_FIELDS, values)) for values in dict.fromkeys(tuple(r[k] for k in REVIEW_FIELDS) for r in review)]
    json_write(cache_path, cache)
    json_write(output_dir / 'ner.json', rows)
    csv_write(output_dir / 'ner_rejects.csv', REJECT_FIELDS, rejects)
    csv_write(output_dir / 'alias_review.csv', REVIEW_FIELDS, review)

    def run_metrics(rs):
        return {k: {'mean': sum(len(r['entities'][k]) for r in rs)/len(rs) if rs else 0,
                    'empty_rate': sum(not r['entities'][k] for r in rs)/len(rs) if rs else 0} for k in ENTITY_TYPES}

    comparable = [r['id'] for r in rows] == [r['id'] for r in previous]
    alerts = drift(run_metrics(rows), run_metrics(previous)) if comparable else []
    cost = (usage['input_tokens']*.10 + usage['cache_read_input_tokens']*.01 +
            usage['cache_creation_input_tokens']*.125 + usage['output_tokens']*.50) / 1_000_000
    summary = {'clauses': len(rows), 'calls': requests_made, 'cache_hits': hits, 'flags': dict(flags),
               'wall_time_s': time.perf_counter() - started, 'events': dict(events),
               'errors': sum(r['status'] == 'error' for r in rows), 'rejects': len(rejects),
               'review_rows': len(review), 'usage': dict(usage), 'cost_estimate_usd': cost,
               'normaliser_version': normaliser.version,
               'grounding': {k: dict(g) for k, g in grounding_totals.items()}, 'drift_alerts': alerts,
               'types': {k: {'mentions': sum(len(r['entities'][k]) for r in rows),
                             'unique_canonicals': len({e['canonical'] for r in rows for e in r['entities'][k]}),
                             'resolved_alias': resolved[k],
                             'review_mentions': review_mention_counts[k]}
                         for k in ENTITY_TYPES}}
    details = provenance(client)
    if details:
        versions = {r['provenance']['model_version'] for r in rows if r.get('provenance', {}).get('model_version')}
        details['model_version'] = next(iter(versions)) if len(versions) == 1 else None
        summary['provenance'] = details
        if client.provider != 'anthropic':
            summary['legacy_cost_estimate_usd'] = cost
            cost = summary['cost_estimate_usd'] = azure_cost(usage)
            summary['cost_prices_verified'] = 'published regional rate; subscription billing unverified'
            summary['cost_rate_source'] = 'Microsoft Foundry August 2025: $0.275 input/$2.20 output per 1M; no cache discount'
            json_write(output_dir / 'run_summary.json', summary)
    print(f"NER: rows={len(rows)}, requests={requests_made}, uncached clauses={len(pending)}, cache hits={hits}, error rows={summary['errors']}, rejects={len(rejects)}, review rows={len(review)}, flags={dict(flags)}")
    if alerts:
        print('WARN run-level drift >25%; stopping summary for inspection:', json.dumps(alerts))
        return rows, summary
    for kind, s in summary['types'].items():
        g = grounding_totals[kind]
        denominator = s['mentions'] or 1
        print(f"{kind}: mentions={s['mentions']}, unique={s['unique_canonicals']}, alias={100*s['resolved_alias']/denominator:.1f}%, review={100*s['review_mentions']/denominator:.1f}%, grounding rejects={g['rejected']}/{g['total']} ({100*g['rejected']/(g['total'] or 1):.1f}%)")
    print(f'Cost estimate ({summary.get("cost_rate_source", "R11 A1 rates")}): ${cost:.6f}; usage={dict(usage)}')
    if len(pending) > 1 and not usage['cache_read_input_tokens']:
        print('WARN cache-read tokens are zero after the first request')
    return rows, summary


def main(argv=None):
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    p = argparse.ArgumentParser()
    p.add_argument('--limit', type=int)
    p.add_argument('--ids')
    p.add_argument('--force', action='store_true')
    p.add_argument('--stub', action='store_true')
    p.add_argument('--mode', choices=['structured', 'strict_tool'], default='structured')
    p.add_argument('--effort', default='low')
    args = p.parse_args(argv)
    clauses = json.loads((ROOT / 'data/clauses.json').read_text(encoding='utf-8'))
    corpus_size = len(clauses)
    if args.ids:
        wanted = set(args.ids.split(','))
        clauses = [c for c in clauses if c['id'] in wanted]
    if args.limit is not None:
        if args.limit < 0:
            p.error('--limit must be nonnegative')
        clauses = clauses[:args.limit]
    stub = args.stub or not live_configured()
    if stub:
        print('STUB MODE: entities are recorded fixtures, numbers are not meaningful.')
    client = FakeClient(effort=args.effort, mode=args.mode) if stub else make_client('S4', effort=args.effort, mode=args.mode)
    controls = None
    if getattr(client, 'provider', None) == 'azure_openai':
        folder = ROOT / 'data/runs/azure_openai/s4'
        controls = LiveControls(client, folder, corpus_size, requested=len(clauses))
    try:
        _, summary = run_pipeline(clauses, client, force=args.force, controls=controls)
    except RunStopped as exc:
        print('STOP:', str(exc))
        return 3
    return 2 if summary['drift_alerts'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
