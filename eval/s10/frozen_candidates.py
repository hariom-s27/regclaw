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
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from standards import ruleset
from standards.ruleset import normalised_sha

QUERY_SUFFIX = ' biological evaluation ISO 10993'
TOP_K = 30
CANDIDATES_FORMAT = 's10-frozen-candidates.v1'
MANIFEST_FORMAT = 's10-frozen-candidates-manifest.v1'
CYPHER_CACHE_NAME = 'cypher_cache.jsonl'
EMBEDDING_CACHE_NAME = 'query_embedding_cache.jsonl'
CALL_LEDGER_NAME = 's10c_call_ledger.jsonl'
# G-01: one fixed append-only ledger across every paid S10c step and attempt.
GLOBAL_LEDGER_REL = 'data/runs/s10c_global_call_ledger.jsonl'
CALL_CAP = 150
LEDGER_FORMAT = 's10c-ledger.v2'
CANDIDATE_RESERVE = 11
RESERVES = dict(candidates=CANDIDATE_RESERVE, pilot=6, main=6)
CANDIDATE_RUN_NAME = 's10c_candidates_run.json'
CANDIDATE_RUN_FORMAT = 's10c-candidates-run.v1'
CANDIDATE_RECEIPT_FORMAT = 's10c-candidates-receipt.v1'
CANDIDATE_RECEIPT_REL = 'eval/s10/manifests/s10c_candidates_run.json'
PROFILES_MANIFEST_REL = 'eval/s10/manifests/profiles_v1.json'
PROVENANCE_FILES = tuple('standards/data/' + name for name in ruleset.RULESET_FILES_SHA256) + (
    'data/embed_identity.json',)


class FrozenCandidatesError(RuntimeError):
    """Building or replaying frozen candidates was refused."""


class CandidateInput(FrozenCandidatesError):
    """Fixed preflight check names, without input content."""


class LedgerError(CandidateInput):
    """A malformed or inconsistent ledger must refuse without repair."""


def verify_dev_profiles(root, profiles_path):
    """Compare input bytes with the fixed dev entry; never follow manifest-selected paths."""
    try:
        manifest = json.loads((Path(root) / PROFILES_MANIFEST_REL).read_bytes())
        if not isinstance(manifest, dict) or manifest.get('format') != 's10-profiles-manifest.v1':
            raise CandidateInput('profiles_manifest')
        expected = manifest['files']['dev_profiles.yaml']['sha256_lf']
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise CandidateInput('profiles_manifest')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CandidateInput('profiles_manifest') from exc
    try:
        actual = normalised_sha(Path(profiles_path).read_bytes())
    except OSError as exc:
        raise CandidateInput('profiles_input') from exc
    if actual != expected:
        raise CandidateInput('profiles_sha')
    return actual


def candidate_provenance(root):
    """Capture fixed input hashes before constructing a retriever; provenance only."""
    try:
        return {rel: dict(sha256_lf=normalised_sha((Path(root) / rel).read_bytes()))
                for rel in PROVENANCE_FILES}
    except OSError as exc:
        raise CandidateInput('candidate_provenance') from exc


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


def build_candidates(profiles, *, cache_dir, out_path, manifest_path, retriever_factory, top_k=TOP_K, now=None,
                     before_publish=None):
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
    if before_publish is not None:
        before_publish()
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


def _validate_ledger_rows(rows):
    reserves, settled = {}, set()
    fields = {'format', 'seq', 'ts', 'step', 'kind', 'unit', 'amount'}
    for seq, row in enumerate(rows, 1):
        if (not isinstance(row, dict) or set(row) != fields or row['format'] != LEDGER_FORMAT
                or type(row['seq']) is not int or row['seq'] != seq
                or not isinstance(row['step'], str) or row['step'] not in RESERVES
                or not isinstance(row['unit'], str) or not row['unit']
                or type(row['amount']) is not int or not isinstance(row['ts'], str)):
            raise LedgerError('ledger_row')
        try:
            stamp = datetime.fromisoformat(row['ts'])
            if 'T' not in row['ts'] or stamp.utcoffset() != timezone.utc.utcoffset(stamp):
                raise ValueError
        except (ValueError, TypeError):
            raise LedgerError('ledger_timestamp') from None
        key = row['step'], row['unit']
        if row['kind'] == 'reserve':
            if key in reserves:
                raise LedgerError('ledger_duplicate_reserve')
            if row['amount'] != RESERVES[row['step']]:
                raise LedgerError('ledger_reserve')
            reserves[key] = row['amount']
        elif row['kind'] == 'settle':
            if key not in reserves or key in settled or not -reserves[key] <= row['amount'] <= 0:
                raise LedgerError('ledger_settle')
            settled.add(key)
        else:
            raise LedgerError('ledger_kind')
    return rows


def _parse_ledger(raw):
    if raw and not raw.endswith(b'\n'):
        raise LedgerError('ledger_torn_line')
    try:
        def invalid_constant(value):
            raise ValueError('non_json_constant')
        rows = [json.loads(line.decode('utf-8'), parse_constant=invalid_constant)
                for line in raw.split(b'\n')[:-1]]
    except (ValueError, UnicodeError):
        raise LedgerError('ledger_torn_line') from None
    return _validate_ledger_rows(rows)


def read_call_ledger(path):
    path = Path(path)
    return _parse_ledger(path.read_bytes()) if path.exists() else []


def ledger_total(path):
    return sum(row['amount'] for row in read_call_ledger(path))


def ledger_snapshot(path):
    path = Path(path)
    raw = path.read_bytes() if path.exists() else b''
    rows = _parse_ledger(raw)
    return dict(total=sum(row['amount'] for row in rows), global_ledger_last_seq=len(rows),
                global_ledger_prefix_sha256_lf=normalised_sha(raw))


def append_ledger_line(path, row):
    """The reserve is durable before this function returns to the paid caller."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(row, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def append_call_ledger(path, *, step, kind, unit, amount, now=None):
    rows = read_call_ledger(path)
    row = dict(format=LEDGER_FORMAT, seq=len(rows) + 1,
               ts=now or datetime.now(timezone.utc).isoformat(), step=step, kind=kind, unit=unit, amount=amount)
    _validate_ledger_rows([*rows, row])
    append_ledger_line(path, row)
    return row


def charge(global_ledger, folder_ledger, *, step, kind, unit, amount, now=None):
    """Reserve global first; settle folder first, preserving global >= folder."""
    stamp = now or datetime.now(timezone.utc).isoformat()
    paths = (global_ledger, folder_ledger) if kind == 'reserve' else (folder_ledger, global_ledger)
    for path in paths:
        append_call_ledger(path, step=step, kind=kind, unit=unit, amount=amount, now=stamp)


def reserve_unit(global_ledger, folder_ledger, *, step, unit, now=None):
    rows = read_call_ledger(global_ledger)
    if any((row['step'], row['unit']) == (step, unit) for row in rows):
        raise LedgerError('ledger_duplicate_reserve')
    if sum(row['amount'] for row in rows) + RESERVES[step] > CALL_CAP:
        raise FrozenCandidatesError('call_cap')
    charge(global_ledger, folder_ledger, step=step, kind='reserve', unit=unit, amount=RESERVES[step], now=now)


def settle_unit(global_ledger, folder_ledger, *, step, unit, actual, now=None):
    if type(actual) is not int or not 0 <= actual <= RESERVES[step]:
        raise LedgerError('ledger_actual')
    charge(global_ledger, folder_ledger, step=step, kind='settle', unit=unit,
           amount=actual - RESERVES[step], now=now)


def write_candidate_run_manifest(*, root, run_dir, candidates_path, manifest_path, ledger_start, calls,
                                  profiles_sha256_lf, provenance_files, code_lock_sha256_lf, global_start,
                                  before_publish=None):
    """Publish completion only after the request ledger and candidate outputs exist.

    The committed receipt anchors the local manifest's LF hash. Consumers verify
    both that hash and the ledger hash before accepting its recorded total.
    The code-lock sha (G-02) is recorded in both; the global ledger total at
    completion (G-01) is provenance in the run manifest.
    """
    root, run_dir = Path(root).resolve(), Path(run_dir).resolve()
    if not isinstance(code_lock_sha256_lf, str) or not re.fullmatch(r'[0-9a-f]{64}', code_lock_sha256_lf):
        raise FrozenCandidatesError('candidate code-lock sha missing')
    ledger = run_dir / CALL_LEDGER_NAME
    total = ledger_total(ledger)
    if total != ledger_start + calls or total > CALL_CAP:
        raise FrozenCandidatesError('candidate ledger total mismatch or cap exceeded')
    from .live import checked_path
    snapshot = ledger_snapshot(checked_path(root, GLOBAL_LEDGER_REL))
    global_total = snapshot.pop('total')
    if global_total < total or global_total > CALL_CAP:
        raise FrozenCandidatesError('global ledger total below the folder ledger or over the cap')
    ledger_sha = normalised_sha(ledger.read_bytes())
    counts = dict(ledger_start=ledger_start, http_attempts=calls, ledger_total=total)
    doc = dict(format=CANDIDATE_RUN_FORMAT, phase='candidates', run_dir=run_dir.relative_to(root).as_posix(),
               profiles_sha256_lf=profiles_sha256_lf, provenance_files=provenance_files,
               counts=counts, ledger_sha256_lf=ledger_sha, code_lock_sha256_lf=code_lock_sha256_lf,
                global_ledger_total=global_total, global_start=global_start, global_end=global_total, **snapshot,
               outputs={name: dict(sha256_lf=normalised_sha(Path(path).read_bytes()))
                        for name, path in (('candidates', candidates_path), ('candidates_manifest', manifest_path))})
    text = json.dumps(doc, indent=2, sort_keys=True) + '\n'
    receipt = dict(format=CANDIDATE_RECEIPT_FORMAT, counts=counts,
                   global_start=global_start, global_end=global_total, **snapshot,
                   shas=dict(run_manifest_sha256_lf=normalised_sha(text.encode('utf-8')),
                             ledger_sha256_lf=ledger_sha, code_lock_sha256_lf=code_lock_sha256_lf))
    if before_publish is not None:
        before_publish()
    _write_once(run_dir / CANDIDATE_RUN_NAME, text)
    # This final write is the completion anchor. Partial saves never qualify.
    if before_publish is not None:
        before_publish()
    _write_once(root / CANDIDATE_RECEIPT_REL, json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt


def _live_retriever_factory(*, cache_file):
    # Import only after CLI preflight; fake factories never import live dependencies.
    from retrieval.hybrid_retriever import HybridRetriever
    return HybridRetriever(cache_file=cache_file)


class RetrievalRequests:
    """Count Text2Cypher HTTP attempts and embedding requests, including failures.

    Cache-row deltas are an independent cross-check. Each query durably reserves
    its full budget, then settles only after both counters can be read. Failed
    queries and counting failures retain their individual reservations.
    """

    def __init__(self, retriever, ledger_path, global_path, *, stop_state=None, now=None):
        self.client = retriever.llm
        self.embedder = retriever.query_embeddings.embedder
        self.embedding_start = self._embedding_count()
        self.cypher_requests = 0
        self.ledger_path = ledger_path
        self.ledger_start = ledger_total(ledger_path)
        # G-01: with a global ledger, the cap check uses the global total.
        self.global_path = global_path
        self.global_start = ledger_total(global_path)
        self.global_expected, self.folder_expected = self.global_start, self.ledger_start
        # G-08: queries that passed the guard; charged at the reserve if counting fails.
        self.started = 0
        self.has_http_log = isinstance(getattr(self.client, 'last_http_log', None), list)
        self.has_counter = type(getattr(self.client, 'api_requests', None)) is int
        if not (self.has_http_log or self.has_counter):
            raise FrozenCandidatesError('Text2Cypher request counter unavailable')
        tries = getattr(retriever, 'max_retries', None)
        attempts = getattr(self.embedder, 'attempts', None)
        if type(tries) is not int or tries < 0 or type(attempts) is not int or attempts < 1:
            raise FrozenCandidatesError('retrieval request budget unavailable')
        self.query_reserve = 2 * (tries + 1) + attempts
        if self.query_reserve != CANDIDATE_RESERVE:
            raise FrozenCandidatesError('retrieval request budget differs from reserve')
        self.query_number = max((int(row['unit'][1:]) for row in read_call_ledger(global_path)
                                 if row['step'] == 'candidates' and row['kind'] == 'reserve'
                                 and re.fullmatch(r'q[0-9]+', row['unit'])), default=0)
        retrieve = retriever.retrieve
        generate = self.client.generate

        def counted_generate(request):
            if self.has_http_log:
                self.client.last_http_log = []
            before = getattr(self.client, 'api_requests', None)
            try:
                return generate(request)
            finally:
                n = (len(self.client.last_http_log) if self.has_http_log
                     else self.client.api_requests - before)
                if type(n) is not int or n < 0:
                    raise FrozenCandidatesError('invalid Text2Cypher request counter')
                self.cypher_requests += n

        def guarded_retrieve(*args, **kwargs):
            check_candidate_stop(stop_state)
            if ledger_total(ledger_path) != self.folder_expected:
                raise FrozenCandidatesError('call ledger changed during retrieval')
            if ledger_total(global_path) != self.global_expected:
                raise FrozenCandidatesError('global call ledger changed during retrieval')
            before = self.counts()
            self.query_number += 1
            unit = f'q{self.query_number:02d}'
            check_candidate_stop(stop_state)
            reserve_unit(global_path, ledger_path, step='candidates', unit=unit, now=now)
            self.started += 1
            result = retrieve(*args, **kwargs)
            after = self.counts()
            deltas = tuple(a - b for a, b in zip(after, before))
            if any(type(n) is not int or n < 0 for n in deltas) or sum(deltas) > CANDIDATE_RESERVE:
                raise FrozenCandidatesError('invalid query request count')
            settle_unit(global_path, ledger_path, step='candidates', unit=unit, actual=sum(deltas), now=now)
            self.global_expected += sum(deltas)
            self.folder_expected += sum(deltas)
            check_candidate_stop(stop_state)
            return result

        self.client.generate = counted_generate
        retriever.retrieve = guarded_retrieve

    def _embedding_count(self):
        value = getattr(self.embedder, 'api_requests', None)
        if type(value) is not int or value < 0:
            raise FrozenCandidatesError('embedding request counter unavailable')
        return value

    def counts(self):
        embeddings = self._embedding_count() - self.embedding_start
        if embeddings < 0:
            raise FrozenCandidatesError('embedding request counter decreased')
        return self.cypher_requests, embeddings

    def total(self):
        return sum(self.counts())


def check_candidate_stop(state):
    if state is not None and state.requested:
        raise FrozenCandidatesError('interrupted')


def _main(argv=None, *, data=None, retriever_factory=None, now=None, env=None, locked_files=None, stop_state=None):
    """Build dev candidates, charging actual requests; cache rows cross-check only.

    Every refusal (paths, write-once, environment, code lock, ruleset, profiles,
    ledgers) happens before any retriever is constructed.
    """
    from llm import provider
    from standards.data_loader import load_data
    from .split_guard import load_eval_files
    from . import live

    parser = argparse.ArgumentParser(prog='python -m eval.s10.frozen_candidates')
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build', help='build dev frozen candidates (write-once)')
    build.add_argument('--profiles', required=True)
    build.add_argument('--cache-dir', required=True)
    build.add_argument('--out', required=True)
    build.add_argument('--manifest', required=True)
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    try:
        root = Path(provider.ROOT)
        # G-06: sealed names, root boundary and folder rules before any read.
        profiles_path, cache_dir, out_path, manifest_path = (
            live.checked_path(root, value) for value in (args.profiles, args.cache_dir, args.out, args.manifest))
        if any(path.exists() or path.is_symlink() for path in (out_path, manifest_path)):
            print('REFUSED: candidate output already exists (write-once)')
            return 3
        cache_dir = check_folder(cache_dir, 'cache_dir')
        check_folder(out_path, 'out_path')
        folder = provider.run_dir()
        if folder is None:
            raise FrozenCandidatesError('REGCLAW_RUN_DIR required for call accounting')
        folder = check_folder(folder, 'run_dir')
        completion = folder / CANDIDATE_RUN_NAME
        receipt_path = root / CANDIDATE_RECEIPT_REL
        if completion.exists() or completion.is_symlink() or receipt_path.exists() or receipt_path.is_symlink():
            print('REFUSED: candidate completion manifest already exists (write-once)')
            return 3
        # G-02 + G-10: stub/provider/tracing checks, then the code lock.
        live.check_live_environment(env)
        lock_sha = live.verify_code_lock(root, locked_files)
        ruleset.check_ruleset()
        profiles_sha = verify_dev_profiles(root, profiles_path)
        provenance_files = candidate_provenance(root)
        if (folder / 'STOPPED.json').exists():
            raise FrozenCandidatesError('candidate folder stopped')
        ledger_path = folder / CALL_LEDGER_NAME
        ledger_start = ledger_total(ledger_path)
        # G-01: the global ledger must cover this folder's earlier charges.
        global_path = live.checked_path(root, GLOBAL_LEDGER_REL)
        global_start = ledger_total(global_path)
        if global_start < ledger_start:
            raise CandidateInput('global_ledger_short')
        cache_paths = [cache_dir / name for name in (CYPHER_CACHE_NAME, EMBEDDING_CACHE_NAME)]
        destinations = [out_path, manifest_path]
        reserved = {profiles_path, ledger_path, global_path, completion, receipt_path, *cache_paths}
        if len(set(destinations)) != len(destinations) or any(p in reserved for p in destinations):
            raise FrozenCandidatesError('output paths overlap an input, cache or ledger')
        data = load_data() if data is None else data
        split, profiles, _ = load_eval_files(profiles_path, data=data)
        if split != 'dev':
            raise FrozenCandidatesError('dev profiles required')
        before = [cache_row_count(path) for path in cache_paths]
    except ruleset.RulesetChanged:
        print('ruleset_mismatch count=1')
        return 2
    except (CandidateInput, live.LiveCheck) as exc:
        print(f'{exc} count=1')
        return 2
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}')
        return 2

    made, counters = [], []

    def factory(*, cache_file):
        retriever = (retriever_factory or _live_retriever_factory)(cache_file=cache_file)
        made.append(retriever)
        counters.append(RetrievalRequests(retriever, ledger_path, global_path, stop_state=stop_state, now=now))
        return retriever

    error = None
    counts = None
    request_counts = None
    disabled = logging.root.manager.disable
    try:
        check_candidate_stop(stop_state)
        # Dependencies log query/profile content; the CLI exposes only aggregates.
        logging.disable(logging.CRITICAL)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                manifest = build_candidates(profiles, cache_dir=cache_dir, out_path=out_path,
                                            manifest_path=manifest_path, retriever_factory=factory, now=now,
                                            before_publish=lambda: check_candidate_stop(stop_state))
                manifest_sha = normalised_sha(manifest_path.read_bytes())
            finally:
                try:
                    try:
                        request_counts = [counter.counts() for counter in counters]
                        calls = sum(sum(pair) for pair in request_counts)
                    except Exception:
                        request_counts = None
                        raise FrozenCandidatesError('request counting failed; reserve retained') from None
                    counts = [cache_row_count(path) - n for path, n in zip(cache_paths, before)]
                    if any(n < 0 for n in counts):
                        raise FrozenCandidatesError('retrieval cache row count decreased')
                finally:
                    for retriever in made:
                        close = getattr(retriever, 'close', None)
                        if close is not None:
                            close()
            check_candidate_stop(stop_state)
            receipt = write_candidate_run_manifest(root=root, run_dir=folder,
                candidates_path=out_path, manifest_path=manifest_path, ledger_start=ledger_start, calls=calls,
                profiles_sha256_lf=profiles_sha, provenance_files=provenance_files,
                code_lock_sha256_lf=lock_sha, global_start=global_start,
                before_publish=lambda: check_candidate_stop(stop_state))
    except (Exception, SystemExit, KeyboardInterrupt) as exc:
        error = str(exc) if isinstance(exc, LedgerError) else type(exc).__name__
    finally:
        logging.disable(disabled)
    if counts is not None and request_counts is not None:
        cypher = sum(row[0] for row in request_counts)
        embeddings = sum(row[1] for row in request_counts)
        print(f'cypher_rows={counts[0]} embedding_rows={counts[1]} '
              f'cypher_requests={cypher} embedding_requests={embeddings} retrieval_calls={cypher + embeddings}')
    if error is not None:
        print(f'ERROR: {error}')
        return 2
    print(f'profiles={len(profiles)} candidates={sum(row["n"] for row in manifest["profiles"].values())} '
          f'candidates_sha256_lf={manifest["file_sha256"]} '
          f'manifest_sha256_lf={manifest_sha} '
          f'run_manifest_sha256_lf={receipt["shas"]["run_manifest_sha256_lf"]} '
          f'ledger_sha256_lf={receipt["shas"]["ledger_sha256_lf"]} '
          f'ledger_total={receipt["counts"]["ledger_total"]}')
    return 0


def main(argv=None, *, data=None, retriever_factory=None, now=None, env=None, locked_files=None):
    from llm import provider
    from .live import graceful_sigint
    from .paid_lock import paid_lock, PaidLockHeld

    console = sys.stdout
    try:
        with paid_lock(provider.ROOT), graceful_sigint(out=lambda message: print(message, file=console)) as state:
            return _main(argv, data=data, retriever_factory=retriever_factory, now=now, env=env,
                         locked_files=locked_files, stop_state=state)
    except PaidLockHeld as exc:
        print(str(exc))
        return 2
    except (Exception, SystemExit, KeyboardInterrupt) as exc:
        print(f'ERROR: {str(exc) if isinstance(exc, LedgerError) else type(exc).__name__}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
