"""Write-once S10c source lock; tests and environment are provenance only.

--show prints sorted source-stat lines, followed by listing_sha256. The hash
covers precisely those UTF-8, LF source lines, including the final LF, without
the checksum line. --save-listing optionally saves that same listing with 'x'
under guarded data/. Otherwise --show writes nothing. Lock creation requires
--reviewed-listing and --expected-listing-sha; no per-file arguments are used.

Live preflight checks rule data with check_ruleset and engine code with
check_engine. Prompts are protected by the enforced standards/classifier.py
source sha (including PROMPT_SHA256), not by check_ruleset/check_engine.
Legacy manifests are read only at fixed paths, and their paths are never
followed. Only fixed source paths below may be opened for overlap checking.

Exit codes: 0 shown/written; 1 digest mismatch; 2 input/output validation;
3 destination already exists. Failed saves may retain partial files; no cleanup
or automatic repair is performed. Hariom runs this module after review.
"""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import re
import sys

from standards import ruleset
from standards.classifier import PROMPT_SHA256
from .addendum_freeze import SEALED_NAMES
from .frozen_candidates import check_folder, FrozenCandidatesError
from .prereg_freeze import compute

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_REL = 'eval/s10/manifests/s10c_code_lock.json'
MANIFEST_FORMAT = 's10c-code-lock.v1'
LOCKED = (
    'embed/__init__.py',
    'embed/embed.py',
    'embed/store.py',
    'embed/token_stats.py',
    'eval/s10/__init__.py',
    'eval/s10/addendum_freeze.py',
    'eval/s10/addendum_freeze_n.py',
    'eval/s10/agreement.py',
    'eval/s10/analysis.py',
    'eval/s10/dev_export.py',
    'eval/s10/draws.py',
    'eval/s10/formats.py',
    'eval/s10/frozen_candidates.py',
    'eval/s10/judge_file.py',
    'eval/s10/judge_kit.py',
    'eval/s10/judge_output.py',
    'eval/s10/label_freeze.py',
    'eval/s10/label_quality.py',
    'eval/s10/label_tool.py',
    'eval/s10/live.py',
    'eval/s10/merge.py',
    'eval/s10/metrics.py',
    'eval/s10/paid_lock.py',
    'eval/s10/prereg_freeze.py',
    'eval/s10/profile_checks.py',
    'eval/s10/runner.py',
    'eval/s10/s10c_lock.py',
    'eval/s10/second_rater.py',
    'eval/s10/split_guard.py',
    'eval/s10/tagger.py',
    'ingest/__init__.py',
    'ingest/validate.py',
    'llm/__init__.py',
    'llm/azure_openai.py',
    'llm/cypher.py',
    'llm/provider.py',
    'ner/__init__.py',
    'ner/checks.py',
    'ner/client.py',
    'ner/normalise.py',
    'ner/prompt.py',
    'ner/stdref.py',
    'observability/tracing.py',
    'retrieval/cypher_cache.py',
    'retrieval/hybrid_retriever.py',
    'retrieval/query_embeddings.py',
    'retrieval/setup.py',
    'standards/__init__.py',
    'standards/assemble.py',
    'standards/classifier.py',
    'standards/data_loader.py',
    'standards/demo.py',
    'standards/engine.py',
    'standards/guard.py',
    'standards/overclaim.py',
    'standards/profile.py',
    'standards/ruleset.py',
    'standards/tool.py',
)

# Fixed metadata paths; their contents can supply expected hashes, never paths
# to open. The non-code freezes are also inspected for any source overlap.
OVERLAP_MANIFESTS = tuple('eval/s10/manifests/' + name for name in (
    'addendum_a2.json', 'addendum_a3.json', 'auditA_labels_hariom_150.json',
    'codebook_v1.json', 'e1_grid_check.json', 'friend_labels_v1.json',
    'prereg_v1.json', 'profiles_v1.json', 'retest_session_v1.json',
    's10b1_build.json', 's10b2b_code_lock.json', 's10b2e_code_lock.json',
    's10b2h_code_lock.json',
))
# Exact sets declared by the existing lock producers. These are metadata,
# not copies of their loading or hashing implementations.
LEGACY_LOCKS = {
    'eval/s10/manifests/s10b2b_code_lock.json': ('s10b2b-code-lock.v1', (
        'eval/s10/judge_output.py', 'eval/s10/judge_runner.py', 'eval/s10/metrics.py',
        'eval/s10/agreement.py', 'eval/s10/merge.py', 'eval/s10/gate.py',
        'eval/s10/draws.py', 'eval/s10/sheets.py', 'eval/s10/diagnostics.py')),
    'eval/s10/manifests/s10b2e_code_lock.json': ('s10b2e-code-lock.v1', (
        'eval/s10/second_rater.py', 'eval/s10/label_quality.py',
        'eval/s10/census.py', 'eval/s10/addendum_freeze.py')),
    'eval/s10/manifests/s10b2h_code_lock.json': ('s10b2h-code-lock.v1', (
        'eval/s10/retest_a3.py',)),
}
PACKAGES = ('openai', 'tiktoken', 'numpy', 'scipy', 'ppi-python')
LINE = re.compile(r'([^\s:]+): sha256_lf=([0-9a-f]{64}) bytes_lf=(0|[1-9][0-9]*) lines=(0|[1-9][0-9]*)')


class LockInput(ValueError):
    """Fixed diagnostic only; never echo an input payload."""


class DigestMismatch(Exception):
    def __init__(self, names=()):
        self.names = tuple(sorted(set(names)))


def checked_path(root, value, *, data_only=False):
    root = Path(root).resolve()
    lexical = root / value
    resolved = lexical.resolve()
    if (not resolved.is_relative_to(root) or '..' in Path(value).parts
            or ':' in str(value).replace(str(Path(value).drive), '', 1)):
        raise LockInput('path_boundary')
    for part in (*lexical.parts, *resolved.parts):
        if (part.casefold() in SEALED_NAMES | {'.git'}
                or part.casefold().startswith(('.env', '.venv'))):
            raise LockInput('forbidden_path')
    check_folder(lexical, 'lock_path')
    check_folder(resolved, 'lock_path')
    # Fixed source/manifest paths must not alias data or another source through
    # an internal symlink or junction. Listings obey the same conservative rule.
    if resolved != lexical.absolute():
        raise LockInput('path_alias')
    if data_only and (not resolved.is_relative_to(root / 'data')
                      or not lexical.absolute().is_relative_to(root / 'data')
                      or resolved == root / 'data'):
        raise LockInput('listing_path')
    return lexical


def source_stats(root):
    return {rel: compute(checked_path(root, rel).read_bytes()) for rel in LOCKED}


def listing_text(stats):
    return ''.join(f'{rel}: sha256_lf={stats[rel]["sha256_lf"]} '
                   f'bytes_lf={stats[rel]["bytes_lf"]} lines={stats[rel]["lines"]}\n'
                   for rel in sorted(LOCKED))


def parse_listing(raw, expected_sha):
    if not re.fullmatch('[0-9a-f]{64}', expected_sha):
        raise LockInput('expected_listing_sha')
    if compute(raw)['sha256_lf'] != expected_sha:
        raise DigestMismatch()
    text = raw.replace(b'\r\n', b'\n').decode('utf-8')
    stats = {}
    for line in text.splitlines():
        match = LINE.fullmatch(line)
        if not match or match[1] not in LOCKED or match[1] in stats:
            raise LockInput('reviewed_listing')
        stats[match[1]] = dict(sha256_lf=match[2], bytes_lf=int(match[3]), lines=int(match[4]))
    if set(stats) != set(LOCKED) or text != listing_text(stats):
        raise LockInput('reviewed_listing')
    return stats


def check_overlaps(root, stats):
    bad = set()
    for rel in OVERLAP_MANIFESTS:
        doc = json.loads(checked_path(root, rel).read_bytes())
        if not isinstance(doc, dict):
            raise LockInput('overlap_manifest')
        files = doc.get('files', {})
        if rel in LEGACY_LOCKS:
            fmt, expected = LEGACY_LOCKS[rel]
            if not isinstance(files, dict) or doc.get('format') != fmt or set(files) != set(expected):
                raise LockInput('legacy_manifest')
        if rel == 'eval/s10/manifests/codebook_v1.json':
            if (doc.get('format') != 's10-codebook-manifest.v1' or not isinstance(files, dict)
                    or 'eval/s10/judge_kit.py' not in files):
                raise LockInput('codebook_source')
        # Inspect nested metadata too (e.g. a single-file freeze). The only
        # reads stay the fixed source/manifest reads, regardless of these keys.
        def visit(value):
            if isinstance(value, dict):
                for name, info in value.items():
                    if name in stats:
                        if not isinstance(info, dict) or info.get('sha256_lf') != stats[name]['sha256_lf']:
                            bad.add(name)
                    visit(info)
                name = value.get('file', value.get('path'))
                if isinstance(name, str) and name in stats and value.get('sha256_lf') != stats[name]['sha256_lf']:
                    bad.add(name)
            elif isinstance(value, list):
                for item in value:
                    visit(item)
        visit(doc)
    for name, sha in ruleset.ENGINE_FILES_SHA256.items():
        rel = 'standards/' + name
        if rel not in stats:
            raise LockInput('engine_file_set')
        if stats[rel]['sha256_lf'] != sha:
            bad.add(rel)
    if ruleset.ENGINE_FILES_VERSION != ruleset.ENGINE_VERSION:
        bad.update('standards/' + name for name in ruleset.ENGINE_FILES_SHA256)
    # PROMPT_SHA256 describes the embedded literal; the reviewed classifier
    # source sha is the enforced prompt freeze in v4.
    tree = ast.parse(checked_path(root, 'standards/classifier.py').read_bytes())
    prompts = [ast.literal_eval(node.value) for node in tree.body
               if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == 'PROMPT' for target in node.targets)]
    if len(prompts) != 1 or not isinstance(prompts[0], str):
        raise LockInput('prompt_literal')
    if hashlib.sha256(prompts[0].encode('utf-8')).hexdigest() != PROMPT_SHA256:
        bad.add('standards/classifier.py')
    if bad:
        raise DigestMismatch(bad)


def provenance_test_stats(root):
    folder = checked_path(root, 'tests')
    names = {'tests/conftest.py', 'tests/_netguard.py', 'tests/test_s10c1_lock.py'}
    names.update('tests/' + path.name for path in folder.glob('test_s10c1_*.py'))
    return {rel: compute(checked_path(root, rel).read_bytes()) for rel in sorted(names)}


def environment_provenance():
    # No package imports, installation, subprocesses or network access.
    distributions = list(metadata.distributions())
    lines, versions = [], {}
    for dist in distributions:
        name, version = dist.metadata['Name'], dist.version
        if not isinstance(name, str) or not name or not isinstance(version, str):
            raise LockInput('distribution_metadata')
        lines.append(f'{name}=={version}\n')
        key = re.sub('[-_.]+', '-', name).lower()
        versions.setdefault(key, []).append(version)
    return dict(python_version=platform.python_version(),
                installed_versions={name: ', '.join(sorted(versions.get(name, []))) or 'not installed'
                                    for name in PACKAGES},
                environment_sha256=hashlib.sha256(''.join(sorted(lines)).encode('utf-8')).hexdigest(),
                distribution_count=len(distributions))


def write_once(path, text):
    if path.exists() or path.is_symlink():
        raise FileExistsError
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text)


def main(argv=None, *, root=ROOT, now=None, out=print, err=None):
    err = err or (lambda text: print(text, file=sys.stderr))
    parser = argparse.ArgumentParser(prog='python -m eval.s10.s10c_lock', description=__doc__)
    parser.add_argument('--show', action='store_true')
    parser.add_argument('--save-listing')
    parser.add_argument('--reviewed-listing')
    parser.add_argument('--expected-listing-sha')
    args = parser.parse_args(argv)
    if ((args.show and (args.reviewed_listing is not None or args.expected_listing_sha is not None))
            or (not args.show and (args.save_listing is not None or args.reviewed_listing is None
                                   or args.expected_listing_sha is None))):
        err('arguments count=1')
        return 2
    try:
        root = Path(root).resolve()
        destination = checked_path(root, args.save_listing, data_only=True) if args.save_listing is not None else None
        if not args.show:
            destination = checked_path(root, MANIFEST_REL)
        if destination is not None and (destination.exists() or destination.is_symlink()):
            raise FileExistsError
        reviewed = None
        if not args.show:
            reviewed = parse_listing(checked_path(root, args.reviewed_listing, data_only=True).read_bytes(),
                                     args.expected_listing_sha)
        stats = source_stats(root)
        if reviewed is not None:
            bad = [rel for rel in LOCKED if stats[rel]['sha256_lf'] != reviewed[rel]['sha256_lf']]
            if bad:
                raise DigestMismatch(bad)
            if stats != reviewed:
                raise LockInput('reviewed_listing_stats')
        check_overlaps(root, stats)
        listing = listing_text(stats)
        sha = compute(listing.encode('utf-8'))['sha256_lf']
        if args.show:
            if destination is not None:
                write_once(destination, listing)
            for line in listing.splitlines():
                out(line)
            out('listing_sha256=' + sha)
        else:
            manifest = dict(format=MANIFEST_FORMAT,
                            created_at=now or datetime.now(timezone.utc).isoformat(),
                            files=stats, provenance_tests=provenance_test_stats(root),
                            listing_sha256=sha, **environment_provenance())
            write_once(destination, json.dumps(manifest, sort_keys=True, indent=2) + '\n')
            out(f'written {MANIFEST_REL} files={len(LOCKED)} listing_sha256={sha}')
        return 0
    except DigestMismatch as exc:
        if not exc.names:
            err('listing_sha_mismatch')
        for name in exc.names:
            err(name)
        return 1
    except FileExistsError:
        err('output_exists count=1')
        return 3
    except LockInput as exc:
        err(str(exc))
        return 2
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, RecursionError, FrozenCandidatesError):
        err('input_or_output_validation count=1')
        return 2


if __name__ == '__main__':
    sys.exit(main())
