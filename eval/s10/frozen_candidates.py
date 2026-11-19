"""S10-16 frozen retrieval candidates for the S10 evaluation.

The tool's retrieval query depends only on device_name (standards/tool.py):
    retriever.retrieve(device_name + ' biological evaluation ISO 10993', top_k=30)
`build_candidates` calls a real retriever ONCE per distinct query and saves the candidates;
`FrozenRetriever` replays them, so every setting and repeat sees identical candidates.

Files:
- candidates file (under data/, git-ignored): holds clause text.
- manifest (committed): per profile id, the number of candidates and one sha256 over
  [clause_id, doc, edition, sha256(text)] with CRLF->LF normalised text. No clause text, no device names.

Both files are write-once. Cache folder rule (S10-26): the Text2Cypher cache and the
query-embedding cache must both sit in an explicit cache folder that is not under
data/runs/azure_openai/ and has no "s8" anywhere in its path.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from standards.ruleset import normalised_sha

QUERY_SUFFIX = ' biological evaluation ISO 10993'
TOP_K = 30
CANDIDATES_FORMAT = 's10-frozen-candidates.v1'
MANIFEST_FORMAT = 's10-frozen-candidates-manifest.v1'
CYPHER_CACHE_NAME = 'cypher_cache.jsonl'
EMBEDDING_CACHE_NAME = 'query_embedding_cache.jsonl'
CALL_LEDGER_NAME = 's10c_call_ledger.jsonl'


class FrozenCandidatesError(RuntimeError):
    """Building or replaying frozen candidates was refused."""


def tool_query(device_name):
    return device_name + QUERY_SUFFIX


def check_folder(path, what):
    """Refuse data/runs/azure_openai/ and any path with 's8' in it. Returns the resolved path."""
    resolved = Path(path).resolve()
    parts = [p.casefold() for p in resolved.parts]
    if any(parts[i:i + 3] == ['data', 'runs', 'azure_openai'] for i in range(len(parts))):
        raise FrozenCandidatesError(f'{what} must not be under data/runs/azure_openai/')
    if 's8' in str(resolved).casefold():
        raise FrozenCandidatesError(f'{what} must not have "s8" in its path')
    return resolved


def _text_sha(text):
    return hashlib.sha256(text.replace('\r\n', '\n').encode('utf-8')).hexdigest()


def list_sha(candidates):
    """One sha256 for a candidate list: ids + doc + edition + sha of the text, in order."""
    rows = [[c['clause_id'], c['doc'], c['edition'], _text_sha(c['text'])] for c in candidates]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def _normalise(hit):
    """Same field mapping as standards/tool.py."""
    value = hit if isinstance(hit, dict) else vars(hit)
    return dict(clause_id=value['clause_id'], doc=value.get('doc', value.get('standard', '')),
                edition=value.get('edition', 'UNVERIFIED'), text=value.get('text', ''))


def _cache_paths(retriever):
    try:
        return Path(retriever.cypher_cache.path), Path(retriever.query_embeddings.cache.path)
    except AttributeError as exc:
        raise FrozenCandidatesError('cannot confirm the Text2Cypher and query-embedding cache paths of this '
                                    'retriever; refusing to build') from exc


def _write_once(path, text):
    path = Path(path)
    if path.exists():
        raise FrozenCandidatesError(f'{path.name} already exists (write-once)')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as fh:
        fh.write(text)


def build_candidates(profiles, *, cache_dir, out_path, manifest_path, retriever_factory, top_k=TOP_K, now=None):
    """Call the retriever once per distinct tool query; write the candidates file and its manifest.

    profiles: validated profile set {id: entry}. retriever_factory(cache_file=<path>) -> retriever.
    Any retrieval error stops the build (nothing is written)."""
    if top_k != TOP_K:
        raise FrozenCandidatesError(f'top_k must be {TOP_K}, the value the tool sends (got {top_k})')
    cache_dir = check_folder(cache_dir, 'cache_dir')
    check_folder(out_path, 'out_path')
    for path in (out_path, manifest_path):
        if Path(path).exists():
            raise FrozenCandidatesError(f'{Path(path).name} already exists (write-once)')
    cache_dir.mkdir(parents=True, exist_ok=True)
    retriever = retriever_factory(cache_file=cache_dir / CYPHER_CACHE_NAME)
    for name, path in zip(('Text2Cypher cache', 'query-embedding cache'), _cache_paths(retriever)):
        resolved = check_folder(path, name)
        if resolved.parent != cache_dir:
            raise FrozenCandidatesError(f'{name} is not inside cache_dir; refusing to build')
    candidates = {}
    for pid in sorted(profiles):
        query = tool_query(profiles[pid]['profile']['device_name'])
        if query not in candidates:
            hits, _ = retriever.retrieve(query, top_k=TOP_K)
            candidates[query] = [_normalise(h) for h in hits]
    doc = dict(format=CANDIDATES_FORMAT, top_k=TOP_K, query_suffix=QUERY_SUFFIX, candidates=candidates)
    text = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    manifest = dict(format=MANIFEST_FORMAT, built_at=now or datetime.now(timezone.utc).isoformat(),
                    top_k=TOP_K, file=Path(out_path).name, file_sha256=normalised_sha(text.encode('utf-8')),
                    retriever=type(retriever).__name__,
                    profiles={pid: dict(n=len(candidates[tool_query(profiles[pid]['profile']['device_name'])]),
                                        sha256=list_sha(candidates[tool_query(profiles[pid]['profile']['device_name'])]))
                              for pid in sorted(profiles)})
    _write_once(out_path, text)
    _write_once(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


class FrozenRetriever:
    """Replays saved candidates for the exact (query, top_k) the tool sends.

    A wrong top_k or an unknown query raises LookupError AND is counted in `misses`; the tool
    swallows retriever errors, so the runner checks `misses` and refuses to write a summary."""

    def __init__(self, path, *, manifest_path=None, profiles=None):
        path = Path(path)
        raw = path.read_bytes()
        doc = json.loads(raw.decode('utf-8'))
        if not isinstance(doc, dict) or doc.get('format') != CANDIDATES_FORMAT:
            raise FrozenCandidatesError(f'candidates file must have format {CANDIDATES_FORMAT}')
        if doc.get('top_k') != TOP_K or doc.get('query_suffix') != QUERY_SUFFIX:
            raise FrozenCandidatesError('candidates were built for a different query or top_k than the tool sends')
        self.candidates, self.top_k = doc['candidates'], TOP_K
        self.requests, self.misses = [], []
        if manifest_path is not None:
            manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
            if manifest.get('format') != MANIFEST_FORMAT:
                raise FrozenCandidatesError('candidates manifest has the wrong format')
            if normalised_sha(raw) != manifest['file_sha256']:
                raise FrozenCandidatesError('candidates file does not match its manifest (file changed after build)')
            for pid, entry in sorted((profiles or {}).items()):
                listed = manifest['profiles'].get(pid)
                found = self.candidates.get(tool_query(entry['profile']['device_name']))
                if listed is None or found is None or list_sha(found) != listed['sha256']:
                    raise FrozenCandidatesError(f'candidates for profile {pid} are missing or differ from the manifest')

    def retrieve(self, query, top_k=TOP_K):
        self.requests.append((query, top_k))
        if top_k != self.top_k or query not in self.candidates:
            self.misses.append((query, top_k))
            raise LookupError('no frozen candidates for this query and top_k')
        return [dict(c) for c in self.candidates[query]], None


def cache_row_count(path):
    """Count persisted request rows without exposing cache contents."""
    path = Path(path)
    if not path.exists():
        return 0
    with path.open(encoding='utf-8') as stream:
        return sum(1 for line in stream if line.strip())


def ledger_total(path):
    """Validate and sum the shared append-only S10c ledger."""
    path = Path(path)
    if not path.exists():
        return 0
    total = 0
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if (not isinstance(row, dict) or set(row) != {'step', 'calls', 'timestamp'}
                    or not isinstance(row['step'], str) or not row['step']
                    or type(row['calls']) is not int or row['calls'] < 0
                    or not isinstance(row['timestamp'], str) or not row['timestamp']):
                raise FrozenCandidatesError('invalid call ledger row')
            total += row['calls']
    return total


def append_call_ledger(path, *, step, calls, now=None):
    """Append one charge. Only the approved ledger uses append mode."""
    if type(calls) is not int or calls < 0 or not isinstance(step, str) or not step:
        raise FrozenCandidatesError('invalid call ledger charge')
    path = Path(path)
    ledger_total(path)
    row = dict(step=step, calls=calls, timestamp=now or datetime.now(timezone.utc).isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a' if path.exists() else 'x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(row, sort_keys=True) + '\n')


def _live_retriever_factory(*, cache_file):
    # Import only after CLI preflight; fake factories never import live dependencies.
    from retrieval.hybrid_retriever import HybridRetriever
    return HybridRetriever(cache_file=cache_file)


def main(argv=None, *, data=None, retriever_factory=None, now=None):
    """Build dev candidates and account for new retrieval cache rows."""
    from llm.provider import run_dir
    from standards.data_loader import load_data
    from .split_guard import load_eval_files

    parser = argparse.ArgumentParser(prog='python -m eval.s10.frozen_candidates')
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build', help='build dev frozen candidates (write-once)')
    build.add_argument('--profiles', required=True)
    build.add_argument('--cache-dir', required=True)
    build.add_argument('--out', required=True)
    build.add_argument('--manifest', required=True)
    args = parser.parse_args(argv)
    try:
        if any(Path(path).exists() for path in (args.out, args.manifest)):
            print('REFUSED: candidate output already exists (write-once)')
            return 3
        cache_dir = check_folder(args.cache_dir, 'cache_dir')
        check_folder(args.out, 'out_path')
        folder = run_dir()
        if folder is None:
            raise FrozenCandidatesError('REGCLAW_RUN_DIR required for call accounting')
        folder = check_folder(folder, 'run_dir')
        ledger_path = folder / CALL_LEDGER_NAME
        ledger_total(ledger_path)
        cache_paths = [cache_dir / name for name in (CYPHER_CACHE_NAME, EMBEDDING_CACHE_NAME)]
        destinations = [Path(args.out).resolve(), Path(args.manifest).resolve()]
        reserved = {Path(args.profiles).resolve(), ledger_path, *cache_paths}
        if len(set(destinations)) != len(destinations) or any(p in reserved for p in destinations):
            raise FrozenCandidatesError('output paths overlap an input, cache or ledger')
        data = load_data() if data is None else data
        split, profiles, _ = load_eval_files(args.profiles, data=data)
        if split != 'dev':
            raise FrozenCandidatesError('dev profiles required')
        before = [cache_row_count(path) for path in cache_paths]
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}')
        return 2

    made = []

    def factory(*, cache_file):
        retriever = (retriever_factory or _live_retriever_factory)(cache_file=cache_file)
        made.append(retriever)
        return retriever

    error = None
    counts = None
    disabled = logging.root.manager.disable
    try:
        # Dependencies log query/profile content; the CLI exposes only aggregates.
        logging.disable(logging.CRITICAL)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                manifest = build_candidates(profiles, cache_dir=cache_dir, out_path=args.out,
                                            manifest_path=args.manifest, retriever_factory=factory, now=now)
                manifest_sha = normalised_sha(Path(args.manifest).read_bytes())
            finally:
                try:
                    counts = [cache_row_count(path) - n for path, n in zip(cache_paths, before)]
                    if any(n < 0 for n in counts):
                        raise FrozenCandidatesError('retrieval cache row count decreased')
                    append_call_ledger(ledger_path, step='candidates', calls=sum(counts), now=now)
                finally:
                    for retriever in made:
                        close = getattr(retriever, 'close', None)
                        if close is not None:
                            close()
    except Exception as exc:
        error = type(exc).__name__
    finally:
        logging.disable(disabled)
    if counts is not None:
        calls = sum(counts)
        print(f'cypher_rows={counts[0]} embedding_rows={counts[1]} retrieval_calls={calls} '
              f'retrieval_calls_upper_bound={calls * 3}')
    if error is not None:
        print(f'ERROR: {error}')
        return 2
    print(f'profiles={len(profiles)} candidates={sum(row["n"] for row in manifest["profiles"].values())} '
          f'candidates_sha256_lf={manifest["file_sha256"]} '
          f'manifest_sha256_lf={manifest_sha}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
