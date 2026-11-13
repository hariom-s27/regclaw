"""S10-16 frozen candidates and S10-26 cache-folder rule. Fake retrievers and fake profiles only:
no real candidates are built, no database or network is touched."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import _netguard
import standards.tool
from eval.s10 import frozen_candidates as FC
from eval.s10 import runner as R
from eval.s10.split_guard import load_eval_files
from standards.classifier import FakeLLM
from standards.tool import get_applicable_standards

FIX = Path(__file__).parent / 'fixtures' / 's10_eval'
PROFILES = FIX / 'fake_profiles.yaml'
NOW = '2026-11-13T00:00:00+00:00'
FAKE_TEXT = 'FAKE clause text line one.\nFAKE clause text line two.'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')
    monkeypatch.setattr(standards.tool, 'save_record', lambda *a, **k: None)


@pytest.fixture
def profiles():
    return load_eval_files(PROFILES)[1]


class RealShaped:
    """Fake with the two cache attributes of the real retriever (both derived from cache_file)."""

    def __init__(self, cache_file, embedding_cache=None, text=FAKE_TEXT):
        cache_file = Path(cache_file)
        self.cypher_cache = NS(path=cache_file)
        self.query_embeddings = NS(cache=NS(path=Path(embedding_cache) if embedding_cache
                                            else cache_file.with_name('query_embedding_cache.jsonl')))
        self.text, self.calls = text, []

    def retrieve(self, query, top_k=30):
        self.calls.append((query, top_k))
        n = 1 + len(self.calls) % 2
        return [NS(clause_id=f'fda-iso10993-1:FAKE.{i}', standard='fda-iso10993-1', text=self.text,
                   score=0.5) for i in range(n)], None


def build(tmp_path, profiles, factory=None, **kw):
    made = []

    def default(cache_file):
        made.append(RealShaped(cache_file))
        return made[-1]

    args = dict(cache_dir=tmp_path / 'cache', out_path=tmp_path / 'data' / 'candidates.json',
                manifest_path=tmp_path / 'manifest.json', retriever_factory=factory or default, now=NOW)
    args.update(kw)
    return FC.build_candidates(profiles, **args), args, made


def test_build_calls_the_retriever_once_per_query_with_the_tools_query(tmp_path, profiles):
    manifest, args, made = build(tmp_path, profiles)
    names = [e['profile']['device_name'] for e in profiles.values()]
    assert sorted(made[0].calls) == sorted((n + ' biological evaluation ISO 10993', 30) for n in names)
    assert made[0].cypher_cache.path == (tmp_path / 'cache').resolve() / 'cypher_cache.jsonl'
    doc = json.loads(args['out_path'].read_text(encoding='utf-8'))
    assert doc['format'] == FC.CANDIDATES_FORMAT and doc['top_k'] == 30
    first = next(iter(doc['candidates'].values()))[0]
    assert first == dict(clause_id='fda-iso10993-1:FAKE.0', doc='fda-iso10993-1', edition='UNVERIFIED', text=FAKE_TEXT)


def test_manifest_has_shas_per_profile_and_no_text_or_device_names(tmp_path, profiles):
    manifest, args, _ = build(tmp_path, profiles)
    raw = args['manifest_path'].read_text(encoding='utf-8')
    assert json.loads(raw) == manifest and manifest['format'] == FC.MANIFEST_FORMAT
    assert set(manifest['profiles']) == set(profiles) == {'FAKE-1', 'FAKE-2', 'FAKE-3'}
    assert all(len(p['sha256']) == 64 and p['n'] in (1, 2) for p in manifest['profiles'].values())
    assert 'FAKE clause text' not in raw and 'test patch' not in raw and 'not a real device' not in raw
    assert b'\r' not in args['manifest_path'].read_bytes() and b'\r' not in args['out_path'].read_bytes()


def test_list_sha_is_stable_under_crlf_and_sensitive_to_content():
    lf = [dict(clause_id='a:1', doc='a', edition='2023', text='line one\nline two')]
    crlf = [dict(lf[0], text='line one\r\nline two')]
    assert FC.list_sha(lf) == FC.list_sha(crlf)
    for change in (dict(text='line one\nline 2'), dict(clause_id='a:2'), dict(doc='b'), dict(edition='2024')):
        assert FC.list_sha([dict(lf[0], **change)]) != FC.list_sha(lf)
    assert FC.list_sha(lf + lf) != FC.list_sha(lf)


def test_crlf_copy_of_the_candidates_file_still_matches_the_manifest(tmp_path, profiles):
    _, args, _ = build(tmp_path, profiles)
    out = args['out_path']
    out.write_bytes(out.read_bytes().replace(b'\n', b'\r\n'))
    assert b'\r\n' in out.read_bytes()
    FC.FrozenRetriever(out, manifest_path=args['manifest_path'], profiles=profiles)
    out.write_bytes(out.read_bytes().replace(b'FAKE clause text line one', b'CHANGED clause text line one'))
    with pytest.raises(FC.FrozenCandidatesError, match='does not match'):
        FC.FrozenRetriever(out, manifest_path=args['manifest_path'], profiles=profiles)


def test_replay_through_the_tool_hits_every_query_and_feeds_the_candidates(tmp_path, profiles):
    _, args, _ = build(tmp_path, profiles)
    frozen = FC.FrozenRetriever(args['out_path'], manifest_path=args['manifest_path'], profiles=profiles)
    for entry in profiles.values():
        llm = FakeLLM()
        get_applicable_standards(entry['profile'], frozen, llm=llm)
        sent = json.loads(llm.requests[0]['messages'][0]['content'])['clauses']
        assert sent == frozen.candidates[entry['profile']['device_name'] + ' biological evaluation ISO 10993']
    assert frozen.misses == [] and len(frozen.requests) == 3
    assert all(top_k == 30 for _, top_k in frozen.requests)       # the tool's own top_k


def test_replay_is_identical_on_every_call_and_returns_copies(tmp_path, profiles):
    _, args, _ = build(tmp_path, profiles)
    frozen = FC.FrozenRetriever(args['out_path'])
    query = FC.tool_query(profiles['FAKE-1']['profile']['device_name'])
    first, _ = frozen.retrieve(query, top_k=30)
    first[0]['text'] = 'mutated by the caller'
    again, _ = frozen.retrieve(query, top_k=30)
    assert again[0]['text'] == FAKE_TEXT


def test_wrong_top_k_or_unknown_query_is_a_miss(tmp_path, profiles):
    _, args, _ = build(tmp_path, profiles)
    frozen = FC.FrozenRetriever(args['out_path'])
    query = FC.tool_query(profiles['FAKE-1']['profile']['device_name'])
    with pytest.raises(LookupError):
        frozen.retrieve(query, top_k=10)
    with pytest.raises(LookupError):
        frozen.retrieve('unknown device biological evaluation ISO 10993', top_k=30)
    with pytest.raises(LookupError):
        frozen.retrieve(profiles['FAKE-1']['profile']['device_name'], top_k=30)     # query without the suffix
    assert len(frozen.misses) == 3


def test_build_refuses_a_different_top_k(tmp_path, profiles):
    with pytest.raises(FC.FrozenCandidatesError, match='top_k'):
        build(tmp_path, profiles, top_k=10)
    assert not (tmp_path / 'data').exists() and not (tmp_path / 'cache').exists()


def test_candidates_file_built_for_another_top_k_is_refused_at_replay(tmp_path, profiles):
    _, args, _ = build(tmp_path, profiles)
    doc = json.loads(args['out_path'].read_text(encoding='utf-8'))
    doc['top_k'] = 10
    other = tmp_path / 'other.json'
    other.write_text(json.dumps(doc), encoding='utf-8')
    with pytest.raises(FC.FrozenCandidatesError, match='different query or top_k'):
        FC.FrozenRetriever(other)


def test_a_miss_blocks_the_summary(tmp_path, profiles):
    two = {k: v for k, v in profiles.items() if k != 'FAKE-3'}
    _, args, _ = build(tmp_path, two)
    frozen = FC.FrozenRetriever(args['out_path'])
    out = tmp_path / 'out'
    with pytest.raises(RuntimeError, match='no frozen candidates'):
        R.main(['--profiles', str(PROFILES), '--out', str(out), '--repeats', '1'],
               client_factory=lambda s: FakeLLM(), retriever=frozen, now=NOW)
    assert not (out / 'summary.json').exists() and len(frozen.misses) == 1


def test_no_miss_lets_the_runner_finish(tmp_path, profiles):
    _, args, _ = build(tmp_path, profiles)
    frozen = FC.FrozenRetriever(args['out_path'], manifest_path=args['manifest_path'], profiles=profiles)
    out = tmp_path / 'out'
    assert R.main(['--profiles', str(PROFILES), '--out', str(out), '--repeats', '2'],
                  client_factory=lambda s: FakeLLM(), retriever=frozen, now=NOW) == 0
    summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
    assert summary['provenance']['retriever'] == 'FrozenRetriever' and len(frozen.requests) == 6


# ---- S10-26: both caches inside an explicit, allowed cache folder ----
FORBIDDEN = [('data', 'runs', 'azure_openai', 'cand'), ('runs_s8',), ('data', 'runs', 'S8', 'b1')]


@pytest.mark.parametrize('parts', FORBIDDEN)
def test_forbidden_cache_folder_is_refused(tmp_path, profiles, parts):
    made = []
    with pytest.raises(FC.FrozenCandidatesError, match='cache_dir'):
        build(tmp_path, profiles, factory=lambda cache_file: made.append(cache_file), cache_dir=tmp_path.joinpath(*parts))
    assert made == [] and not tmp_path.joinpath(*parts).exists()         # refused before anything is created


@pytest.mark.parametrize('parts', FORBIDDEN)
def test_embedding_cache_in_a_forbidden_folder_is_refused(tmp_path, profiles, parts):
    elsewhere = tmp_path.joinpath(*parts) / 'query_embedding_cache.jsonl'
    with pytest.raises(FC.FrozenCandidatesError, match='query-embedding cache'):
        build(tmp_path, profiles, factory=lambda cache_file: RealShaped(cache_file, embedding_cache=elsewhere))
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('parts', FORBIDDEN)
def test_cypher_cache_in_a_forbidden_folder_is_refused(tmp_path, profiles, parts):
    elsewhere = tmp_path.joinpath(*parts) / 'cypher_cache.jsonl'
    with pytest.raises(FC.FrozenCandidatesError, match='Text2Cypher cache'):
        build(tmp_path, profiles, factory=lambda cache_file: RealShaped(
            elsewhere, embedding_cache=Path(cache_file).with_name('query_embedding_cache.jsonl')))


def test_embedding_cache_outside_cache_dir_is_refused(tmp_path, profiles):
    elsewhere = tmp_path / 'other' / 'query_embedding_cache.jsonl'
    with pytest.raises(FC.FrozenCandidatesError, match='not inside cache_dir'):
        build(tmp_path, profiles, factory=lambda cache_file: RealShaped(cache_file, embedding_cache=elsewhere))


def test_retriever_without_readable_cache_paths_is_refused(tmp_path, profiles):
    class NoPaths:
        def retrieve(self, query, top_k=30):
            raise AssertionError('must not be called')
    with pytest.raises(FC.FrozenCandidatesError, match='cannot confirm'):
        build(tmp_path, profiles, factory=lambda cache_file: NoPaths())
    assert not (tmp_path / 'data').exists()


def test_forbidden_output_path_is_refused(tmp_path, profiles):
    with pytest.raises(FC.FrozenCandidatesError, match='out_path'):
        build(tmp_path, profiles, out_path=tmp_path / 'data' / 'runs' / 'azure_openai' / 'candidates.json')


def test_build_is_write_once_and_a_failed_retrieval_writes_nothing(tmp_path, profiles):
    _, args, _ = build(tmp_path, profiles)
    before = args['out_path'].read_bytes()
    with pytest.raises(FC.FrozenCandidatesError, match='write-once'):
        build(tmp_path, profiles)
    assert args['out_path'].read_bytes() == before

    class Broken(RealShaped):
        def retrieve(self, query, top_k=30):
            raise ConnectionError('offline fixture')
    fresh = tmp_path / 'fresh'
    with pytest.raises(ConnectionError):
        build(fresh, profiles, factory=Broken)
    assert not (fresh / 'data').exists() and not (fresh / 'manifest.json').exists()
