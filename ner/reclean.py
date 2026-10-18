"""Cache-only re-clean: rebuild ner.json from a model cache into a NEW folder.

Never calls a model: every key must already be cached, and the client raises if
asked. The source run folder and cache are only read.
"""
import argparse
import json
from pathlib import Path
import sys

from .extract import ROOT, key_for, run_pipeline
from .normalise import NORMALISER_VERSION, VERSIONS


class CacheOnlyClient:
    """Reproduces the cache key of a recorded run; any extract() call is a bug."""

    def __init__(self, *, provider='azure_openai', model='gpt-5-mini', effort='low', mode='structured'):
        self.provider, self.model, self.effort, self.mode = provider, model, effort, mode
        self.calls = 0

    def extract(self, clause, *, max_tokens=4000):
        self.calls += 1
        raise RuntimeError('cache-only re-clean attempted a live call')


def reclean(clauses, client, *, cache_path, output_dir, root=ROOT, normaliser_version=NORMALISER_VERSION):
    cache_path, output_dir = Path(cache_path), Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f'{output_dir} is not empty; re-clean writes new folders only')
    cache = json.loads(cache_path.read_text(encoding='utf-8'))
    missing = [c['id'] for c in clauses if key_for(c, client) not in cache]
    if missing:
        raise RuntimeError(f'{len(missing)} clauses are not cached; aborting before any live call')
    rows, summary = run_pipeline(clauses, client, root=root, output_dir=output_dir, cache_path=cache_path,
                                 normaliser_version=normaliser_version)
    if client.calls or summary['calls']:
        raise RuntimeError('live calls were attempted during a cache-only re-clean')
    return rows, summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--ids-from', type=Path, help='ner.json whose ids to rebuild (default: all clauses)')
    p.add_argument('--effort', default='low')
    p.add_argument('--model', default='gpt-5-mini')
    p.add_argument('--provider', default='azure_openai')
    p.add_argument('--normaliser-version', choices=VERSIONS, default=NORMALISER_VERSION)
    args = p.parse_args(argv)
    clauses = json.loads((ROOT / 'data/clauses.json').read_text(encoding='utf-8'))
    if args.ids_from:
        wanted = {r['id'] for r in json.loads(args.ids_from.read_text(encoding='utf-8'))}
        clauses = [c for c in clauses if c['id'] in wanted]
        if len(clauses) != len(wanted):
            p.error('--ids-from lists ids that are not in data/clauses.json')
    client = CacheOnlyClient(provider=args.provider, model=args.model, effort=args.effort)
    _, summary = reclean(clauses, client, cache_path=args.cache, output_dir=args.output_dir,
                         normaliser_version=args.normaliser_version)
    print(f"re-clean: clauses={summary['clauses']} calls={summary['calls']} cache_hits={summary['cache_hits']} "
          f"normaliser_version={summary['normaliser_version']}", file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
