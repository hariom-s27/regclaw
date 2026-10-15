"""Repository commands; invoke with the active environment's Python."""

import argparse
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
PLACEHOLDERS = (
    "obligation", "graph", "embed", "retrieve", "eval"
)

# The existing summary file uses script-level assertions, not pytest functions.
# Collect it as one pytest test without editing the original file.
SUMMARY_TEST_RUNNER = """
import runpy
import sys
import pytest

class SummaryModule(pytest.Module):
    def collect(self):
        def run_assertions():
            runpy.run_path(str(self.path), run_name="__main__")

        return [pytest.Function.from_parent(
            self,
            name="test_run_summary",
            callobj=run_assertions,
        )]

class SummaryPlugin:
    def pytest_pycollect_makemodule(self, module_path, parent):
        if module_path.name == "test_run_summary.py":
            return SummaryModule.from_parent(parent, path=module_path)

sys.exit(pytest.main(["-q", "test_run_summary.py"], plugins=[SummaryPlugin()]))
"""


def run_tests():
    env = os.environ.copy()
    env["REGCLAW_STUB"] = "1"
    suites = (
        (ROOT / "mcp", [sys.executable, "-m", "pytest", "-q", "."]),
        (ROOT / "observability", [sys.executable, "-c", SUMMARY_TEST_RUNNER]),
        (ROOT, [sys.executable, "-m", "pytest", "-q", "tests/"]),
    )
    exit_code = 0
    for folder, command in suites:
        result = subprocess.run(command, cwd=folder, env=env, check=False)
        if result.returncode and not exit_code:
            exit_code = result.returncode
    return exit_code


def main():
    # Keep command-specific options with their implementation modules.
    if len(sys.argv) > 1 and sys.argv[1] == 'obligation-weak':
        argparse.ArgumentParser(description='Run the frozen offline weak labeler.').parse_args(sys.argv[2:])
        return subprocess.run([sys.executable, str(ROOT / 'obligation/weak_labeler.py'),
                               str(ROOT / 'data/clauses.json'), str(ROOT / 'data/weak_labels.json')],
                              cwd=ROOT, check=False).returncode
    if len(sys.argv) > 1 and sys.argv[1] == 'gold-sample':
        argparse.ArgumentParser(description='Prepare seeded gold ids and blind annotation files.').parse_args(sys.argv[2:])
        from goldset.sample import run
        run()
        return 0
    handlers = {'gold-score': 'goldset.score', 'enrich': 'ner.merge'}
    if len(sys.argv) > 1 and sys.argv[1] in handlers:
        from importlib import import_module
        return import_module(handlers[sys.argv[1]]).main(sys.argv[2:])
    parser = argparse.ArgumentParser(description="Run regclaw-rebuild tasks.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name, description in (
        ("up", "Start Docker Compose services."),
        ("check", "Run setup checks."),
        ("test", "Run offline tests in stub mode."),
        ("download", "Download the public substitute corpus."),
        ("count", "Count corpus pages, modal words, and headings."),
    ):
        subcommands.add_parser(name, help=description)
    for name in PLACEHOLDERS:
        subcommands.add_parser(name, help="Not implemented yet.")
    for name in ('obligation-weak', 'gold-sample', *handlers):
        subcommands.add_parser(name, help='Offline preparation; use command --help for options.')
    ingest = subcommands.add_parser('ingest', help='Parse manifest-listed PDFs offline.')
    ingest.add_argument('--doc')
    ingest.add_argument('--force', action='store_true')
    ingest.add_argument('--include-optional', action='store_true')
    ingest.add_argument('--pdf')
    ingest.add_argument('--as', dest='as_code')
    subcommands.add_parser('parse-sample', help='Export pre-filter lines for manual labels.')
    parse_score = subcommands.add_parser('parse-score', help='Score labelled parse samples.')
    parse_score.add_argument('--csv')
    subcommands.add_parser('token-stats', help='Count embed input v1 tokens offline.')
    ner = subcommands.add_parser('ner', help='Extract entities with fixtures or an explicitly configured SDK.')
    ner.add_argument('--limit', type=int)
    ner.add_argument('--ids')
    ner.add_argument('--force', action='store_true')
    ner.add_argument('--stub', action='store_true')
    ner.add_argument('--mode', choices=['structured', 'strict_tool'], default='structured')
    ner.add_argument('--effort', default='low')
    args = parser.parse_args()

    if args.command == "test":
        return run_tests()
    if args.command == 'parse-sample':
        from ingest.parse_eval import sample
        sample()
        return 0
    if args.command == 'parse-score':
        from ingest.parse_eval import score
        score(args.csv)
        return 0
    if args.command == 'token-stats':
        from embed.token_stats import run
        run()
        return 0
    if args.command == 'ner':
        from ner.extract import main as ner_main
        return ner_main(sys.argv[2:])
    if args.command == 'ingest':
        from ingest.ingest import run
        if bool(args.pdf) != bool(args.as_code):
            parser.error('--pdf and --as must be used together')
        return 0 if run(doc=args.doc, force=args.force, include_optional=args.include_optional,
                        pdf=args.pdf, as_code=args.as_code) else 1
    if args.command in ("download", "count"):
        return subprocess.run(
            [sys.executable, "-m", "corpus." + args.command], cwd=ROOT, check=False
        ).returncode
    if args.command == "up":
        return subprocess.run(
            ["docker", "compose", "up", "-d"], cwd=ROOT, check=False
        ).returncode
    if args.command == "check":
        return subprocess.run(
            [sys.executable, "check_setup.py"], cwd=ROOT, check=False
        ).returncode
    print("not implemented yet (step Sx)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
