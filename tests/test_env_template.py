"""Guard the secret boundary and the repository's explicit environment reads."""
import ast
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_dotenv_is_never_tracked():
    result = subprocess.run(['git', 'ls-files', '--', '.env'], cwd=ROOT,
                            capture_output=True, text=True, check=True)
    assert not result.stdout.strip(), '.env must never be tracked'


def test_template_covers_explicit_environment_reads_and_has_empty_values():
    entries = [line for line in (ROOT / '.env.example').read_text().splitlines()
               if line and not line.startswith('#')]
    assert all(line.endswith('=') and line.count('=') == 1 for line in entries)
    declared = {line[:-1] for line in entries}
    needed = set()
    # Scope excludes environments, data and frozen PDFs. Inspect syntax only.
    paths = list(ROOT.glob('*.py'))
    for folder in ('corpus', 'ingest', 'ner', 'graph', 'embed', 'retrieval',
                   'mcp', 'standards', 'pageindex', 'eval', 'observability', 'obligation'):
        paths.extend((ROOT / folder).glob('*.py'))
    for path in paths:
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            key = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                name = ast.unparse(node.func)
                if name in ('os.getenv', 'os.environ.get', 'os.environ.setdefault') and node.args:
                    key = node.args[0]
            if isinstance(node, ast.Subscript) and ast.unparse(node.value) == 'os.environ':
                key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                needed.add(key.value)
    assert needed <= declared, needed - declared
