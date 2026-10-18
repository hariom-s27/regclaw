"""Query vector replay and fail-closed S7 identity checks (read-only SQL)."""
import json
from pathlib import Path

from embed.embed import MODEL, make_embedder
from embed.store import DIMS, validate_vector
from .cypher_cache import JsonlCache, sha

EXPECTED = dict(provider='azure_openai', model=MODEL, dimensions=DIMS)


class EmbeddingIdentityError(RuntimeError):
    pass


def manifest_identity(path):
    """Project identity fields only; never return clause records or text.

    S7 manifests may store model under deployment. Dimensions must be explicit;
    input_version/model names are not proof of the vectors' dimensions.
    """
    try:
        saved = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise EmbeddingIdentityError('Embedding identity missing: readable S7 manifest required') from exc
    if not isinstance(saved, dict):
        raise EmbeddingIdentityError('Embedding identity missing: manifest identity object required')
    identity = {k: saved.get(k) for k in ('provider', 'model', 'dimensions')}
    identity['model'] = identity['model'] or saved.get('deployment')
    if any(v is None for v in identity.values()):
        raise EmbeddingIdentityError('Embedding identity missing: manifest requires provider, model/deployment, dimensions')
    return identity


def check_identity(embedder, manifest_path, pg, table='clause_embeddings'):
    stored = manifest_identity(manifest_path)
    query = dict(provider=getattr(embedder, 'provider', None),
                 model=getattr(embedder, 'model', None),
                 dimensions=getattr(embedder, 'dimensions', DIMS))
    if stored != EXPECTED or query != EXPECTED:
        raise EmbeddingIdentityError(
            f'Embedding identity mismatch: expected {EXPECTED}; manifest={stored}; query={query}')
    row = pg.execute(
        'SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a '
        'WHERE a.attrelid = to_regclass(%s) AND a.attname = %s AND NOT a.attisdropped',
        (table, 'embedding')).fetchone()
    if not row or row[0] != f'vector({DIMS})':
        raise EmbeddingIdentityError(f'Embedding column identity mismatch: expected vector({DIMS})')
    return query


class QueryEmbeddings:
    def __init__(self, path, embedder=None):
        self.embedder = embedder if embedder is not None else make_embedder()
        self.cache = JsonlCache(path)

    def get(self, text):
        key = dict(text_sha=sha(text), model=self.embedder.model, provider=self.embedder.provider)
        row = self.cache.find(key)
        if row is None:
            batch = self.embedder.embed([text])
            if len(batch.vectors) != 1:
                raise ValueError('Query embedder must return exactly one vector')
            vector = list(batch.vectors[0])
            validate_vector(vector)
            self.cache.append(dict(key=key, vector=vector, reusable=True))
        else:
            vector = row['vector']
            validate_vector(vector)
        return vector, sha(key)
