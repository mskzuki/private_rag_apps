"""init

Revision ID: 0001
Revises: 
Create Date: 2026-07-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Extensions
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector";')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_bigm";')

    # Tables
    op.execute('''
    CREATE TABLE sources (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        path               text NOT NULL,
        title              text NOT NULL DEFAULT '',
        content_hash       text NOT NULL,
        source_updated_at  timestamptz,
        deleted_at         timestamptz,
        created_at         timestamptz NOT NULL DEFAULT now(),
        updated_at         timestamptz NOT NULL DEFAULT now(),
        UNIQUE (path)
    );
    ''')

    op.execute('''
    CREATE TABLE chunks (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        source_id    uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        position     int  NOT NULL,
        content      text NOT NULL,
        embedding    vector(1024) NOT NULL,
        metadata     jsonb NOT NULL DEFAULT '{}',
        created_at   timestamptz NOT NULL DEFAULT now(),
        UNIQUE (source_id, position)
    );
    ''')

    op.execute('''
    CREATE TABLE ingest_runs (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        trigger     text NOT NULL CHECK (trigger IN ('cli','api','demo')),
        status      text NOT NULL CHECK (status IN ('running','success','error')),
        stats       jsonb NOT NULL DEFAULT '{}',
        error       text,
        started_at  timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz
    );
    ''')

    # Indexes
    op.execute('''
    CREATE INDEX chunks_embedding_hnsw
        ON chunks USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64);
    ''')

    op.execute('''
    CREATE INDEX chunks_content_bigm
        ON chunks USING gin (content gin_bigm_ops);
    ''')

    op.execute('CREATE INDEX chunks_source_id ON chunks (source_id);')
    op.execute('CREATE INDEX sources_not_deleted ON sources (updated_at) WHERE deleted_at IS NULL;')
    op.execute('CREATE INDEX ingest_runs_started ON ingest_runs (started_at DESC);')


def downgrade() -> None:
    op.execute('DROP TABLE ingest_runs;')
    op.execute('DROP TABLE chunks;')
    op.execute('DROP TABLE sources;')
    op.execute('DROP EXTENSION IF EXISTS "pg_bigm";')
    op.execute('DROP EXTENSION IF EXISTS "vector";')
    op.execute('DROP EXTENSION IF EXISTS "pgcrypto";')
