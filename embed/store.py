"""Two-column pgvector contract, exact ranking, and an offline memory fake."""
import math

DIMS = 1536
CREATE_TABLE = 'CREATE TABLE IF NOT EXISTS clause_embeddings (clause_id text PRIMARY KEY, embedding vector(1536))'
UPSERT_SQL = '''INSERT INTO clause_embeddings (clause_id, embedding) VALUES (%s, %s)
    ON CONFLICT (clause_id) DO UPDATE SET embedding = EXCLUDED.embedding'''
RANK_SQL = '''SELECT clause_id, 1 - (embedding <=> %(q)s) AS score FROM clause_embeddings
    WHERE clause_id = ANY(%(ids)s) ORDER BY embedding <=> %(q)s LIMIT %(k)s'''
INDEX_SQL = "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() AND tablename = 'clause_embeddings'"


def assert_no_ann(rows):
    import re
    for row in rows:
        definition = row['indexdef'] if isinstance(row, dict) else row[0]
        if re.search(r'\b(hnsw|ivfflat)\b', definition, re.I):
            raise RuntimeError(f'DESIGN CONFLICT: ANN index on clause_embeddings: {definition}')


def validate_vector(vector):
    if len(vector) != DIMS or not all(math.isfinite(float(x)) for x in vector):
        raise ValueError('Embedding must have 1536 finite dimensions')


class MemoryStore:
    def __init__(self):
        self.rows = {}

    def ids(self):
        return set(self.rows)

    def upsert(self, rows):
        for cid, vector in rows:
            validate_vector(vector)
            self.rows[cid] = list(vector)

    def stats(self):
        norms = [math.sqrt(sum(x*x for x in vector)) for vector in self.rows.values()]
        return {'distinct_ids': len(self.rows), 'total_rows': len(self.rows),
                'norm_min': min(norms, default=None), 'norm_max': max(norms, default=None)}


class PostgresStore:
    def __init__(self, connection):
        from pgvector.psycopg import register_vector
        self.connection = connection
        connection.execute('CREATE EXTENSION IF NOT EXISTS vector')
        register_vector(connection)
        connection.execute(CREATE_TABLE)
        assert_no_ann(connection.execute(INDEX_SQL).fetchall())
        # Existing tables must obey the contract too; IF NOT EXISTS cannot ensure it.
        columns = connection.execute('''SELECT a.attname, format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = 'clause_embeddings' AND n.nspname = current_schema()
              AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum''').fetchall()
        if list(columns) != [('clause_id', 'text'), ('embedding', 'vector(1536)')]:
            raise RuntimeError('DESIGN CONFLICT: clause_embeddings columns differ from the contract')
        primary = connection.execute('''SELECT a.attname FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'clause_embeddings'::regclass AND i.indisprimary''').fetchall()
        if list(primary) != [('clause_id',)]:
            raise RuntimeError('DESIGN CONFLICT: clause_id must be the sole primary key')
        connection.commit()

    def ids(self):
        return {row[0] for row in self.connection.execute('SELECT clause_id FROM clause_embeddings').fetchall()}

    def upsert(self, rows):
        import numpy as np
        values = []
        for cid, vector in rows:
            validate_vector(vector)
            values.append((cid, np.asarray(vector, dtype=np.float32)))
        with self.connection.cursor() as cursor:
            cursor.executemany(UPSERT_SQL, values)
        self.connection.commit()

    def rank(self, ids, query, k=10):
        import numpy as np
        validate_vector(query)
        return self.connection.execute(RANK_SQL, {'ids': list(ids), 'q': np.asarray(query, dtype=np.float32), 'k': k}).fetchall()

    def stats(self):
        row = self.connection.execute('''SELECT count(DISTINCT clause_id), count(*),
            min(l2_norm(embedding)), max(l2_norm(embedding)) FROM clause_embeddings''').fetchone()
        return dict(zip(('distinct_ids', 'total_rows', 'norm_min', 'norm_max'), row))
