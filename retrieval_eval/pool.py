"""Union top-20 from pluggable evaluation systems; BM25 is the only built-in."""
import argparse
import csv
from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
from typing import Protocol

import yaml

from retrieval_eval.bm25 import BM25

ROOT = Path(__file__).resolve().parents[1]


class System(Protocol):
    def rank(self, query: str, k: int = 20) -> list[tuple[str, float]]: ...


def read_queries(path):
    document = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if not isinstance(document, dict) or document.get('status') != 'FROZEN':
        raise ValueError('Topics require FROZEN status before viewing rankings')
    queries = document['queries']
    seen = set()
    for q in queries:
        required = {'qid', 'query', 'rephrase_b', 'origin', 'type', 'jurisdiction',
                    'expected_sources', 'description', 'narrative'}
        if not required <= set(q) or set(q) - required - {'notes'}:
            raise ValueError('Invalid frozen topic fields')
        if not isinstance(q['narrative'], dict) or set(q['narrative']) != {'grade3', 'grade2', 'grade1_traps'}:
            raise ValueError('Frozen topics require graded narratives')
        if 'notes' in q and (not isinstance(q['notes'], str) or not q['notes'].strip()):
            raise ValueError('Topic notes must be nonempty strings')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', q['qid']) or q['qid'] in seen:
            raise ValueError('Invalid or duplicate qid')
        seen.add(q['qid'])
    return queries


def make_pool(query, clauses, systems, depth=20):
    if depth < 1:
        raise ValueError('Pooling depth must be positive')
    cmap = {c['id']: c for c in clauses}
    pooled = {}
    for name, system in systems.items():
        ranking = system.rank(query['query'], depth)
        seen = set()
        for rank, (cid, _) in enumerate(ranking[:depth], 1):
            if cid not in cmap or cid in seen:
                raise ValueError('System returned an unknown or duplicate clause id')
            seen.add(cid)
            entry = pooled.setdefault(cid, {'systems': [], 'best_rank': rank})
            entry['systems'].append(name)
            entry['best_rank'] = min(entry['best_rank'], rank)
    return [dict(qid=query['qid'], clause_id=cid, systems=';'.join(sorted(info['systems'])),
                 best_rank=info['best_rank'], title=cmap[cid]['title'], text=cmap[cid]['text'][:300], grade='')
            for cid, info in sorted(pooled.items(), key=lambda item: (item[1]['best_rank'], item[0]))]


def corpus_fit(queries, clauses, system):
    cmap = {c['id']: c for c in clauses}
    results = []
    for q in queries:
        ranking = system.rank(q['query'], 10)
        supported = any(fnmatchcase(cmap[cid]['doc'], pattern) for cid, _ in ranking for pattern in q['expected_sources'])
        flag = '' if supported else '⚠ likely unsupported'
        rows = [dict(id=cid, score=score, title=cmap[cid]['title']) for cid, score in ranking]
        print(f"{q['qid']} {q['query']} {flag}")
        for r in rows:
            print(f"  {r['id']} | {r['score']:.6f} | {r['title']}")
        results.append(dict(qid=q['qid'], flag=flag, top10=rows))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--systems', nargs='+', default=['bm25'])
    parser.add_argument('--input', default=ROOT / 'data/clauses.json')
    parser.add_argument('--queries', default=ROOT / 'retrieval_eval/queries.yaml')
    parser.add_argument('--output-dir', default=ROOT / 'data/retrieval_eval')
    parser.add_argument('--corpus-fit', action='store_true', help='Print BM25 top-10 source-presence check before pooling')
    args = parser.parse_args(argv)
    if args.systems != ['bm25']:
        parser.error('Only bm25 is implemented; vector_all, pipeline and hyde are future systems')
    queries = read_queries(args.queries)
    clauses = json.loads(Path(args.input).read_text(encoding='utf-8'))
    system = BM25(clauses)
    folder = Path(args.output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    if args.corpus_fit:
        results = corpus_fit(queries, clauses, system)
        (folder / 'corpus_fit.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for query in queries:
        rows = make_pool(query, clauses, {'bm25': system})
        with (folder / f"pool_{query['qid']}.csv").open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['qid', 'clause_id', 'systems', 'best_rank', 'title', 'text', 'grade'])
            writer.writeheader()
            writer.writerows(rows)
        print(f"{query['qid']}: pool={len(rows)}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
