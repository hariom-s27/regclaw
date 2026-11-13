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
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from standards.ruleset import normalised_sha

QUERY_SUFFIX = ' biological evaluation ISO 10993'
TOP_K = 30
CANDIDATES_FORMAT = 's10-frozen-candidates.v1'
MANIFEST_FORMAT = 's10-frozen-candidates-manifest.v1'
CYPHER_CACHE_NAME = 'cypher_cache.jsonl'


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
