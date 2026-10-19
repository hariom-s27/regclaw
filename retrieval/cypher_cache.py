"""Append-only local S8 caches. Equality uses the complete canonical key."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def sha(value):
    text = value if isinstance(value, str) else canonical(value)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def cache_path(provider, family=None):
    family = family or 'b1'
    if Path(family).name != family or family in {'.', '..'}:
        raise ValueError('Run family must be a single directory name')
    return ROOT / 'data' / 'runs' / provider / 's8' / family / 'cypher_cache.jsonl'


def generation_key(question, *, qid, variant, gen_index, hashes, provider,
                   requested_model, effort, attempt_no=1, feedback=None, prev=None, prompt_version='1.0'):
    if variant not in {'a', 'b', 'c'}:
        raise ValueError('Query variant must be a, b or c')
    if not isinstance(gen_index, int) or gen_index < 0:
        raise ValueError('gen_index must be a nonnegative integer; metrics use 0')
    key = dict(qid=qid or 'adhoc:' + sha(question)[:8], variant=variant,
               query_text=question, query_text_sha=sha(question), gen_index=gen_index,
               **hashes, provider=provider, role='s8_cypher',
               requested_model=requested_model, effort=effort,
               attempt_no=attempt_no, feedback_sha=sha([feedback, prev]))
    # The v1.0 key stays byte-identical to pre-B3 keys so frozen v1 caches (B2) still replay.
    # Versions already separate through the template/few-shot/rendered shas; later versions
    # add prompt_version explicitly. Rows record prompt_version for every version.
    if prompt_version != '1.0':
        key['prompt_version'] = prompt_version
    return key


class JsonlCache:
    def __init__(self, path):
        self.path = Path(path)

    def find(self, key):
        if not self.path.exists():
            return None
        found = None
        with self.path.open(encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line)
                if row.get('key') == key and row.get('reusable', False):
                    found = row
        return found  # Most recent exact generation, including after --fresh.

    def append(self, row):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {**row, 'timestamp': row.get('timestamp') or datetime.now(timezone.utc).isoformat()}
        with self.path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(canonical(record) + '\n')
