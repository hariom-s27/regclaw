"""Query vector replay and fail-closed S7 identity checks (read-only SQL)."""
import json
from datetime import datetime, timezone
from pathlib import Path

from embed.embed import MODEL, make_embedder
from embed.store import DIMS, validate_vector
from .cypher_cache import JsonlCache, sha

DEFAULT_IDENTITY = Path(__file__).resolve().parents[1] / 'data/embed_identity.json'
EXPECTED = dict(provider='azure_openai', deployment=MODEL, model=MODEL, dims=DIMS)


class EmbeddingIdentityError(RuntimeError):
    pass


def manifest_identity(path):
    """Read the separate S7 identity addendum; never infer model from deployment."""
    try:
        saved = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise EmbeddingIdentityError('Embedding identity missing: readable S7 addendum required') from exc
    if not isinstance(saved, dict):
        raise EmbeddingIdentityError('Embedding identity missing: manifest identity object required')
    required = (*EXPECTED, 'model_version', 'input_version', 'clause_vectors')
    if any(saved.get(k) is None or saved[k] == '' for k in required):
        raise EmbeddingIdentityError('Embedding identity missing: addendum requires ' + ', '.join(required))
    return {k: saved[k] for k in required}


def check_identity(embedder, manifest_path, pg, table='clause_embeddings'):
    stored = manifest_identity(manifest_path)
    config = getattr(embedder, 'config', None)
    query = dict(provider=getattr(embedder, 'provider', None),
                 deployment=getattr(config, 'deployment', getattr(embedder, 'deployment', None)),
                 model=getattr(embedder, 'embedding_model', getattr(embedder, 'model', None)),
                 dims=getattr(embedder, 'dimensions', None))
    if ({k: stored[k] for k in EXPECTED} != EXPECTED or query != EXPECTED
            or getattr(embedder, 'model', None) != query['deployment']
            or stored['input_version'] != 'v1' or stored['model_version'] != '1'
            or getattr(config, 'model_version', getattr(embedder, 'model_version', None)) != stored['model_version']):
        raise EmbeddingIdentityError(
            f'Embedding identity mismatch: expected {EXPECTED}; manifest={stored}; query={query}')
    row = pg.execute(
        'SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a '
        'WHERE a.attrelid = to_regclass(%s) AND a.attname = %s AND NOT a.attisdropped',
        (table, 'embedding')).fetchone()
    if not row or row[0] != f'vector({DIMS})':
        raise EmbeddingIdentityError(f'Embedding column identity mismatch: expected vector({DIMS})')
    # The identifier is quoted by psycopg, never interpolated from configuration.
    from psycopg import sql
    row = pg.execute(sql.SQL(
        'SELECT (SELECT vector_dims(embedding) FROM {} WHERE embedding IS NOT NULL LIMIT 1), '
        '(SELECT count(*) FROM {})').format(sql.Identifier(table), sql.Identifier(table)), ()).fetchone()
    if not row or row[0] != stored['dims'] or row[1] != stored['clause_vectors'] or row[1] < 1:
        raise EmbeddingIdentityError('Embedding stored vector/count identity mismatch')
    return query


def confirm_response(identity, response_model, vector):
    """Exact response identity only: unknown version suffixes fail closed."""
    if response_model != identity['model'] or len(vector) != identity['dims']:
        raise EmbeddingIdentityError(
            f'Embedding live response identity mismatch: model={response_model!r}, dims={len(vector)}')
    validate_vector(vector)
    return dict(response_model=response_model, vector_length=len(vector),
                identity_sha=sha(identity))


class QueryEmbeddings:
    def __init__(self, path, embedder=None, *, identity_path=None, confirmation_path=None):
        self.embedder = embedder if embedder is not None else make_embedder()
        self.cache = JsonlCache(path)
        self.identity_path = Path(identity_path) if identity_path else None
        self.confirmation_path = (Path(confirmation_path) if confirmation_path else
                                  self.identity_path.with_name('embed_identity_confirmed.json')
                                  if self.identity_path else None)
        self.last = {}

    def get(self, text):
        identity = manifest_identity(self.identity_path) if self.identity_path else None
        key = dict(text_sha=sha(text), model=self.embedder.model, provider=self.embedder.provider)
        if identity:
            key['identity_sha'] = sha(identity)
        row = self.cache.find(key)
        if row is None:
            batch = self.embedder.embed([text])
            if len(batch.vectors) != 1:
                raise ValueError('Query embedder must return exactly one vector')
            vector = list(batch.vectors[0])
            validate_vector(vector)
            evidence = confirm_response(identity, batch.response_model, vector) if identity else {}
            if identity:
                if self.confirmation_path.exists():
                    confirmed = json.loads(self.confirmation_path.read_text(encoding='utf-8'))
                    if any(confirmed.get(k) != v for k, v in evidence.items()):
                        raise EmbeddingIdentityError('Embedding confirmation identity mismatch; will not overwrite')
                else:
                    with self.confirmation_path.open('x', encoding='utf-8', newline='\n') as stream:
                        json.dump({**evidence, 'timestamp': datetime.now(timezone.utc).isoformat()}, stream, indent=2)
                        stream.write('\n')
            row = dict(key=key, vector=vector, reusable=True, **evidence,
                       prompt_tokens=batch.prompt_tokens)
            self.cache.append(row)
            self.last = dict(cache_hit=False, key=sha(key), **evidence, prompt_tokens=batch.prompt_tokens)
        else:
            vector = row['vector']
            validate_vector(vector)
            evidence = confirm_response(identity, row.get('response_model'), vector) if identity else {}
            if identity and row.get('identity_sha') != sha(identity):
                raise EmbeddingIdentityError('Embedding cache identity mismatch')
            self.last = dict(cache_hit=True, key=sha(key), **evidence, prompt_tokens=0)
        return vector, sha(key)
