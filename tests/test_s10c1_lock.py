"""S10c v4 lock tests: invented source/metadata, offline, write-once fixtures."""
import ast
import builtins
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace as NS

import pytest

import _netguard
from eval.s10 import live as L
from eval.s10 import s10c_lock as K

PROMPT = 'FAKE prompt for lock tests'
SOURCE = b'# invented source, never copied from the repository\n'
REVIEW_REL = 'data/review/source-listing.txt'
STAMP = '2026-11-20T00:00:00+00:00'
TEST_NAMES = (
    'tests/conftest.py', 'tests/_netguard.py', 'tests/test_s10c1_part_a.py',
    'tests/test_s10c1_dev_export.py', 'tests/test_s10c1_live.py',
    'tests/test_s10c1_analysis.py', 'tests/test_s10c1_lock.py',
    'tests/test_s10c1_paid_lock.py',
    'tests/test_s10c1_addendum_freeze_n.py',
)


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path_factory):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    _netguard.block_network(monkeypatch, 'lock tests forbid network')

    def denied(*args, **kwargs):
        raise AssertionError('lock tests forbid subprocesses')

    monkeypatch.setattr(subprocess, 'run', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)
    # Even the environment-provenance tests use in-memory distributions.
    monkeypatch.setattr(K.metadata, 'distributions', lambda: ())
    repo = Path(__file__).resolve().parents[1]
    base = tmp_path_factory.getbasetemp().resolve()

    def guarded(real):
        def opened(file, mode='r', *args, **kwargs):
            if isinstance(file, (str, os.PathLike)):
                path = Path(file).resolve()
                if any(part.casefold() in K.SEALED_NAMES | {'.git'}
                       or part.casefold().startswith(('.env', '.venv')) for part in path.parts):
                    raise AssertionError('forbidden input')
                if not path.is_relative_to(base) and any(path.is_relative_to(folder)
                        for folder in (repo / 'data', repo / 'standards/data')):
                    raise AssertionError('real data forbidden')
                if any(char in mode for char in 'wax+') and not path.is_relative_to(base):
                    raise AssertionError('write outside pytest temp base')
            return real(file, mode, *args, **kwargs)
        return opened

    monkeypatch.setattr(builtins, 'open', guarded(builtins.open))
    monkeypatch.setattr(io, 'open', guarded(io.open))
    yield
    assert os.environ['REGCLAW_STUB'] == '1'


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)
    return path


def encoded(doc):
    return (json.dumps(doc, sort_keys=True) + '\n').encode('utf-8')


@pytest.fixture
def world(tmp_path_factory, monkeypatch):
    def build(*, manifest_change=None, source_change=None, missing_source=None,
              missing_manifest=None, extra_test=False):
        root = tmp_path_factory.mktemp('lockworld')
        assert 's8' not in str(root).casefold(), 'lockworld temp root must not contain s8; keep path guards active'
        stats = {}
        for rel in K.LOCKED:
            raw = f'PROMPT = {PROMPT!r}\n'.encode() if rel == 'standards/classifier.py' else SOURCE
            if source_change is not None:
                raw = source_change(rel, raw)
            stats[rel] = K.compute(raw)
            if rel != missing_source:
                put(root / rel, raw)
        for rel in TEST_NAMES + (('tests/test_s10c1_future.py',) if extra_test else ()):
            put(root / rel, b'# invented test provenance\n')
        for rel in K.OVERLAP_MANIFESTS:
            if rel in K.LEGACY_LOCKS:
                fmt, files = K.LEGACY_LOCKS[rel]
                doc = dict(format=fmt, files={name: stats.get(name, dict(sha256_lf='0' * 64)) for name in files})
            elif rel.endswith('/codebook_v1.json'):
                doc = dict(format='s10-codebook-manifest.v1', files={
                    'eval/s10/judge_kit.py': stats['eval/s10/judge_kit.py'],
                    'data/FAKE-never-open.yaml': dict(sha256_lf='0' * 64)})
            else:
                doc = dict(format='FAKE-non-code-metadata', files={})
            if manifest_change is not None:
                manifest_change(rel, doc)
            if rel != missing_manifest:
                put(root / rel, encoded(doc))
        monkeypatch.setattr(K.ruleset, 'ENGINE_FILES_SHA256', {
            name: stats['standards/' + name]['sha256_lf'] for name in ('engine.py', 'guard.py', 'overclaim.py')})
        monkeypatch.setattr(K, 'PROMPT_SHA256', hashlib.sha256(PROMPT.encode()).hexdigest())
        return NS(root=root, stats=stats)
    return build


def invoke(w, args, **kwargs):
    out, err = [], []
    code = K.main(args, root=w.root, now=STAMP, out=out.append, err=err.append, **kwargs)
    return code, out, err


def reviewed(w, *, stats=None, raw=None):
    raw = K.listing_text(w.stats if stats is None else stats).encode('utf-8') if raw is None else raw
    put(w.root / REVIEW_REL, raw)
    return ['--reviewed-listing', REVIEW_REL, '--expected-listing-sha', K.compute(raw)['sha256_lf']]


def file_set(w):
    return {path.relative_to(w.root).as_posix() for path in w.root.rglob('*') if path.is_file()}


def test_locked_exact_source_inventory_and_provenance_separation(world):
    assert len(K.LOCKED) == 58
    assert 'eval/s10/addendum_freeze_n.py' in K.LOCKED
    assert tuple(sorted(set(K.LOCKED))) == K.LOCKED
    assert all(rel.endswith('.py') and not rel.startswith('tests/') for rel in K.LOCKED)
    assert {'ner/__init__.py', 'ner/checks.py', 'ner/client.py', 'ner/normalise.py',
            'ner/prompt.py', 'ner/stdref.py', 'standards/demo.py',
            'ingest/__init__.py', 'ingest/validate.py'} <= set(K.LOCKED)
    w = world(extra_test=True)
    assert set(K.provenance_test_stats(w.root)) == set(TEST_NAMES) | {'tests/test_s10c1_future.py'}


def test_locked_equals_static_repo_import_closure():
    root = Path(__file__).resolve().parents[1]
    seeds = ('eval/s10/live.py', 'eval/s10/frozen_candidates.py', 'eval/s10/analysis.py',
             'eval/s10/dev_export.py', 'eval/s10/s10c_lock.py', 'eval/s10/addendum_freeze_n.py')

    def checked_source(path):
        resolved = path.resolve()
        assert resolved.is_relative_to(root)
        relative = resolved.relative_to(root)
        assert not any(p.casefold() == 'data' or p.casefold().startswith('.env')
                       or any(word in p.casefold() for word in ('holdout', 'jid_map', 'profiles_draft'))
                       for p in relative.parts)
        return resolved

    def sources(parts):
        paths = [root.joinpath(*parts[:i], '__init__.py') for i in range(1, len(parts) + 1)]
        if parts:
            paths.append(root.joinpath(*parts).with_suffix('.py'))
        result = []
        for path in paths:
            if not path.is_file():
                continue
            resolved = checked_source(path)
            relative = resolved.relative_to(root)
            result.append(relative.as_posix())
        return result

    pending, closure = list(seeds), set()
    while pending:
        relative = pending.pop()
        if relative in closure:
            continue
        closure.add(relative)
        package = list(Path(relative).parent.parts)
        pending.extend(sources(list(Path(relative).with_suffix('').parts)))
        tree = ast.parse(checked_source(root / relative).read_text(encoding='utf-8'), filename=relative)
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name.split('.') for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = package[:len(package) - node.level + 1] if node.level else []
                base += node.module.split('.') if node.module else []
                targets = [base] + [base + [alias.name] for alias in node.names if alias.name != '*']
            for target in targets:
                pending.extend(sources(target))
    assert closure == set(K.LOCKED)
    assert 'eval/s10/paid_lock.py' in closure


def test_show_is_read_only_sorted_and_has_exact_listing_hash(world):
    w = world()
    before = file_set(w)
    code, out, err = invoke(w, ['--show'])
    listing = K.listing_text(w.stats)
    assert (code, err) == (0, [])
    assert out[:-1] == listing.splitlines()
    assert out[-1] == 'listing_sha256=' + hashlib.sha256(listing.encode()).hexdigest()
    assert file_set(w) == before


def test_save_listing_exact_utf8_lf_write_once(world):
    w = world()
    code, out, err = invoke(w, ['--show', '--save-listing', REVIEW_REL])
    raw = (w.root / REVIEW_REL).read_bytes()
    assert (code, err) == (0, [])
    assert raw == K.listing_text(w.stats).encode('utf-8')
    assert b'\r' not in raw
    assert out[-1] == 'listing_sha256=' + K.compute(raw)['sha256_lf']
    assert invoke(w, ['--show', '--save-listing', REVIEW_REL])[0] == 3
    assert (w.root / REVIEW_REL).read_bytes() == raw


@pytest.mark.parametrize('name', (
    'listing.txt', 'data/../listing.txt', 'data/cache-s8/listing.txt',
    'data/runs/azure_openai/listing.txt', 'data/.env.secret', 'data/.venv/listing.txt',
    '.git/listing.txt', *('data/' + name for name in sorted(K.SEALED_NAMES)),
))
def test_listing_guard_refusals_write_nothing(world, name):
    w = world()
    before = file_set(w)
    assert invoke(w, ['--show', '--save-listing', name])[0] == 2
    assert invoke(w, ['--reviewed-listing', name, '--expected-listing-sha', '0' * 64])[0] == 2
    assert file_set(w) == before


def test_outside_root_refused_before_open(world):
    w = world()
    before = file_set(w)
    assert invoke(w, ['--show', '--save-listing', str(w.root.parent / 'escaped.txt')])[0] == 2
    assert file_set(w) == before


def test_success_manifest_matches_live_consumer_and_ignores_provenance(world):
    w = world()
    code, out, err = invoke(w, reviewed(w))
    assert (code, err) == (0, [])
    raw = (w.root / K.MANIFEST_REL).read_bytes()
    doc = json.loads(raw)
    assert doc['format'] == K.MANIFEST_FORMAT == L.LOCK_FORMAT
    assert doc['files'] == w.stats
    assert set(doc['provenance_tests']) == set(TEST_NAMES)
    assert doc['created_at'] == STAMP
    assert doc['python_version'] == K.platform.python_version()
    assert doc['installed_versions'] == {name: 'not installed' for name in K.PACKAGES}
    assert doc['distribution_count'] == 0
    assert doc['environment_sha256'] == hashlib.sha256(b'').hexdigest()
    assert doc['listing_sha256'] == K.compute(K.listing_text(w.stats).encode())['sha256_lf']
    assert L.verify_code_lock(w.root) == K.compute(raw)['sha256_lf']
    # Build a second fake root with deliberately unusable provenance values.
    other = world()
    doc['provenance_tests'] = {'data/NEVER-open': None}
    doc['python_version'], doc['environment_sha256'] = 'different', 'provenance only'
    put(other.root / K.MANIFEST_REL, encoded(doc))
    assert L.verify_code_lock(other.root) == K.compute(encoded(doc))['sha256_lf']


def test_second_lock_refuses_and_preserves_existing_bytes(world):
    w = world()
    args = reviewed(w)
    assert invoke(w, args)[0] == 0
    raw = (w.root / K.MANIFEST_REL).read_bytes()
    assert invoke(w, args) == (3, [], ['output_exists count=1'])
    assert (w.root / K.MANIFEST_REL).read_bytes() == raw


def test_listing_sha_mismatch_has_no_content_or_writes(world):
    w = world()
    args = reviewed(w)
    args[-1] = '0' * 64
    before = file_set(w)
    assert invoke(w, args) == (1, [], ['listing_sha_mismatch'])
    assert file_set(w) == before


def test_save_listing_roundtrip_is_the_reviewed_lock_input(world):
    w = world()
    code, out, err = invoke(w, ['--show', '--save-listing', REVIEW_REL])
    assert (code, err) == (0, [])
    sha = out[-1].split('=', 1)[1]
    assert invoke(w, ['--reviewed-listing', REVIEW_REL, '--expected-listing-sha', sha])[0] == 0


@pytest.mark.parametrize('variant', ('invalid-sha', 'invalid-utf8'))
def test_reviewed_listing_invalid_encoding_or_expected_sha(world, variant):
    w = world()
    args = reviewed(w, raw=b'\xff\n') if variant == 'invalid-utf8' else reviewed(w)
    if variant == 'invalid-sha':
        args[-1] = 'INVALID'
    before = file_set(w)
    diagnostic = 'expected_listing_sha' if variant == 'invalid-sha' else 'input_or_output_validation count=1'
    assert invoke(w, args) == (2, [], [diagnostic])
    assert file_set(w) == before


def test_changed_source_hash_prints_only_differing_names(world):
    w = world()
    expected = {rel: dict(info) for rel, info in w.stats.items()}
    changed = ('embed/embed.py', 'eval/s10/analysis.py')
    for rel in changed:
        expected[rel]['sha256_lf'] = '0' * 64
    args = reviewed(w, stats=expected)
    before = file_set(w)
    assert invoke(w, args) == (1, [], sorted(changed))
    assert file_set(w) == before


@pytest.mark.parametrize('variant', ('missing', 'extra', 'duplicate', 'reverse', 'bad-sha', 'bad-stats', 'no-lf'))
def test_malformed_reviewed_listing_refuses_without_writes(world, variant):
    w = world()
    lines = K.listing_text(w.stats).splitlines(keepends=True)
    if variant == 'missing':
        lines = lines[:-1]
    elif variant == 'extra':
        lines.append('data/NEVER-open.py: sha256_lf=' + '0' * 64 + ' bytes_lf=0 lines=0\n')
    elif variant == 'duplicate':
        lines.append(lines[0])
    elif variant == 'reverse':
        lines.reverse()
    elif variant == 'bad-sha':
        lines[0] = lines[0].replace('sha256_lf=', 'sha256_lf=INVALID')
    elif variant == 'bad-stats':
        lines[0] = lines[0].replace('lines=1', 'lines=999')
    else:
        lines[-1] = lines[-1].rstrip('\n')
    args = reviewed(w, raw=''.join(lines).encode())
    before = file_set(w)
    diagnostic = 'reviewed_listing_stats' if variant == 'bad-stats' else 'reviewed_listing'
    assert invoke(w, args) == (2, [], [diagnostic])
    assert file_set(w) == before


def test_crlf_sources_and_listing_use_lf_sha_and_counts(world):
    w = world(source_change=lambda rel, raw: raw.replace(b'\n', b'\r\n'))
    raw = K.listing_text(w.stats).replace('\n', '\r\n').encode()
    assert invoke(w, reviewed(w, raw=raw))[0] == 0
    assert w.stats['embed/embed.py'] == K.compute(SOURCE)


def test_empty_source_stats_follow_locked_helper(world):
    w = world(source_change=lambda rel, raw: b'' if rel == 'embed/__init__.py' else raw)
    assert invoke(w, reviewed(w))[0] == 0
    assert w.stats['embed/__init__.py'] == dict(sha256_lf=hashlib.sha256(b'').hexdigest(), bytes_lf=0, lines=0)


@pytest.mark.parametrize('rel', K.OVERLAP_MANIFESTS)
def test_every_fixed_manifest_overlap_digest_is_enforced(world, rel):
    def change(name, doc):
        if name == rel:
            doc['files']['eval/s10/analysis.py'] = dict(sha256_lf='0' * 64)
            # Legacy exact sets must stay exact; change an existing overlap.
            if name in K.LEGACY_LOCKS:
                doc['files'].pop('eval/s10/analysis.py')
                overlap = sorted(set(doc['files']) & set(K.LOCKED))
                if overlap:
                    doc['files'][overlap[0]] = dict(sha256_lf='0' * 64)
                else:
                    # v3 has no overlap, so inject a single-file declaration.
                    doc['file'], doc['sha256_lf'] = 'eval/s10/analysis.py', '0' * 64
    w = world(manifest_change=change)
    before = file_set(w)
    code, out, err = invoke(w, ['--show'])
    assert code == 1 and out == [] and len(err) == 1 and err[0] in K.LOCKED
    assert file_set(w) == before


@pytest.mark.parametrize('rel', K.LEGACY_LOCKS)
@pytest.mark.parametrize('variant', ('format', 'file-set'))
def test_legacy_format_and_exact_sets(world, rel, variant):
    def change(name, doc):
        if name == rel:
            if variant == 'format':
                doc['format'] = 'INVALID'
            else:
                doc['files']['data/NEVER-open.py'] = dict(sha256_lf='0' * 64)
    w = world(manifest_change=change)
    assert invoke(w, ['--show']) == (2, [], ['legacy_manifest'])


def test_codebook_cannot_drop_its_known_source_entry(world):
    def change(rel, doc):
        if rel.endswith('/codebook_v1.json'):
            doc['files'].pop('eval/s10/judge_kit.py')
    w = world(manifest_change=change)
    before = file_set(w)
    assert invoke(w, ['--show']) == (2, [], ['codebook_source'])
    assert file_set(w) == before


def test_engine_and_prompt_digest_mismatches_refuse(world, monkeypatch):
    w = world()
    monkeypatch.setattr(K.ruleset, 'ENGINE_FILES_SHA256', {'engine.py': '0' * 64})
    monkeypatch.setattr(K, 'PROMPT_SHA256', '0' * 64)
    assert invoke(w, ['--show']) == (1, [], ['standards/classifier.py', 'standards/engine.py'])


def test_engine_version_mismatch_refuses(world, monkeypatch):
    w = world()
    monkeypatch.setattr(K.ruleset, 'ENGINE_FILES_VERSION', 'INVALID')
    assert invoke(w, ['--show']) == (1, [], [
        'standards/engine.py', 'standards/guard.py', 'standards/overclaim.py'])


def test_source_alias_refused_before_open(world, monkeypatch):
    w = world()
    aliased = w.root / K.LOCKED[0]
    real = Path.resolve

    def redirected(path, *args, **kwargs):
        if path == aliased:
            return w.root / 'data' / 'NEVER-open.py'
        return real(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', redirected)
    before = file_set(w)
    assert invoke(w, ['--show']) == (2, [], ['path_alias'])
    assert file_set(w) == before


def test_all_reads_use_fixed_paths_and_never_manifest_selected_paths(world, monkeypatch):
    def change(rel, doc):
        if rel not in K.LEGACY_LOCKS:
            doc['files'].update({
                name: dict(sha256_lf='0' * 64)
                for name in ('data/NEVER-open.py', '../outside.py', *sorted(K.SEALED_NAMES))})
    w = world(manifest_change=change)
    args = reviewed(w)
    reads = set()
    real = Path.open

    def record(path, mode='r', *args, **kwargs):
        if not any(char in mode for char in 'wax+'):
            reads.add(path.resolve())
        return real(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', record)
    assert invoke(w, args)[0] == 0
    allowed = set(K.LOCKED) | set(K.OVERLAP_MANIFESTS) | set(TEST_NAMES) | {REVIEW_REL}
    assert reads == {w.root / rel for rel in allowed}


@pytest.mark.parametrize('missing', ('source', 'manifest'))
def test_missing_input_refuses_without_writes(world, missing):
    w = world(**({'missing_source': K.LOCKED[0]} if missing == 'source'
                 else {'missing_manifest': K.OVERLAP_MANIFESTS[0]}))
    before = file_set(w)
    assert invoke(w, ['--show']) == (2, [], ['input_or_output_validation count=1'])
    assert file_set(w) == before


def test_environment_sorted_all_distributions_named_versions_and_absence(monkeypatch):
    distributions = [NS(metadata={'Name': name}, version=version) for name, version in (
        ('z-fake', '9'), ('numpy', '2.0'), ('openai', '2.54.0'), ('ppi_python', '0.2.3'), ('a-fake', '1'))]
    monkeypatch.setattr(K.metadata, 'distributions', lambda: iter(distributions))
    got = K.environment_provenance()
    expected = ''.join(sorted(f'{d.metadata["Name"]}=={d.version}\n' for d in distributions))
    assert got['environment_sha256'] == hashlib.sha256(expected.encode()).hexdigest()
    assert got['distribution_count'] == 5
    assert got['installed_versions'] == dict(openai='2.54.0', tiktoken='not installed',
                                            numpy='2.0', scipy='not installed', **{'ppi-python': '0.2.3'})
    monkeypatch.setattr(K.metadata, 'distributions', lambda: reversed(distributions))
    assert K.environment_provenance() == got


@pytest.mark.parametrize('args', ([], ['--save-listing', REVIEW_REL],
    ['--show', '--expected-listing-sha', '0' * 64], ['--reviewed-listing', REVIEW_REL],
    ['--show', '--reviewed-listing', REVIEW_REL]))
def test_argument_combinations_refuse_without_writes(world, args):
    w = world()
    before = file_set(w)
    assert invoke(w, args) == (2, [], ['arguments count=1'])
    assert file_set(w) == before


@pytest.mark.parametrize('phase', ('show', 'lock'))
def test_exclusive_create_race_preserves_winner(world, monkeypatch, phase):
    w = world()
    args = ['--show', '--save-listing', REVIEW_REL] if phase == 'show' else reviewed(w)
    target = w.root / (REVIEW_REL if phase == 'show' else K.MANIFEST_REL)
    target.parent.mkdir(parents=True, exist_ok=True)
    real = Path.open
    winner = b'FAKE concurrent writer\n'

    def raced(path, mode='r', *args, **kwargs):
        if path == target and mode == 'x':
            with real(path, 'xb') as stream:
                stream.write(winner)
        return real(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', raced)
    assert invoke(w, args) == (3, [], ['output_exists count=1'])
    assert target.read_bytes() == winner


@pytest.mark.parametrize('variant', ('changed-source', 'missing-key', 'extra-key', 'format'))
def test_live_verifier_rejects_source_and_manifest_changes(world, variant):
    w = world()
    files = {rel: dict(info) for rel, info in w.stats.items()}
    doc = dict(format=K.MANIFEST_FORMAT, files=files)
    if variant == 'changed-source':
        files[K.LOCKED[0]]['sha256_lf'] = '0' * 64
    elif variant == 'missing-key':
        files.pop(K.LOCKED[0])
    elif variant == 'extra-key':
        files['data/NEVER-open.py'] = dict(sha256_lf='0' * 64)
    else:
        doc['format'] = 'INVALID'
    put(w.root / K.MANIFEST_REL, encoded(doc))
    with pytest.raises(L.LiveCheck):
        L.verify_code_lock(w.root)


def test_committed_manifest_matches_code_read_only(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    path = root / K.MANIFEST_REL
    if not path.exists():
        pytest.skip('write-once S10c manifest not created yet')
    real = Path.open

    def readonly(path, mode='r', *args, **kwargs):
        assert not any(char in mode for char in 'wax+'), 'real lock verification must be read-only'
        return real(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', readonly)
    raw = path.read_bytes()
    doc = json.loads(raw)
    assert doc['format'] == K.MANIFEST_FORMAT
    assert set(doc['files']) == set(K.LOCKED)
    assert doc['files'] == K.source_stats(root)
    assert doc['listing_sha256'] == K.compute(K.listing_text(doc['files']).encode())['sha256_lf']
    K.check_overlaps(root, doc['files'])
    assert L.verify_code_lock(root) == K.compute(raw)['sha256_lf']
    # provenance_tests and installed versions are deliberately never enforced.


@pytest.mark.parametrize('path,check', [('listing.txt', 'listing_path'),
    ('data/../listing.txt', 'path_boundary'), ('data/.env.secret', 'forbidden_path')])
def test_path_validation_prints_fixed_check_name(world, path, check):
    w = world()
    before = file_set(w)
    assert invoke(w, ['--show', '--save-listing', path]) == (2, [], [check])
    assert file_set(w) == before


@pytest.mark.parametrize('kind,check', [('overlap', 'overlap_manifest'), ('engine', 'engine_file_set'),
                                      ('prompt', 'prompt_literal'), ('distribution', 'distribution_metadata')])
def test_remaining_lock_input_checks_are_named_without_writes(world, monkeypatch, kind, check):
    w = world(**({'source_change': lambda rel, raw: b'OTHER = 1\n'
                  if rel == 'standards/classifier.py' else raw} if kind == 'prompt' else {}))
    if kind == 'overlap':
        (w.root / K.OVERLAP_MANIFESTS[0]).write_text('[]', encoding='utf-8')
    elif kind == 'engine':
        monkeypatch.setattr(K.ruleset, 'ENGINE_FILES_SHA256', {'unknown.py': '0' * 64})
    elif kind == 'distribution':
        monkeypatch.setattr(K.metadata, 'distributions', lambda: [NS(metadata={'Name': ''}, version='1')])
    args = reviewed(w) if kind == 'distribution' else ['--show']
    before = file_set(w)
    assert invoke(w, args) == (2, [], [check])
    assert file_set(w) == before
