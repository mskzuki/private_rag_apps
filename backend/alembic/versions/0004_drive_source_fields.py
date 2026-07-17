"""drive_source_fields

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('''
    ALTER TABLE sources
        ADD COLUMN source_type text NOT NULL DEFAULT 'local_fs'
            CHECK (source_type IN ('local_fs', 'google_drive')),
        ADD COLUMN external_id text,
        ADD COLUMN source_url  text;
    ''')

    op.execute('''
    -- 既存の UNIQUE(path) を分離する
    ALTER TABLE sources DROP CONSTRAINT sources_path_key;
    ''')

    op.execute('''
    CREATE UNIQUE INDEX sources_path_unique_local
        ON sources (path) WHERE source_type = 'local_fs';
    ''')
    op.execute('''
    CREATE UNIQUE INDEX sources_external_id_unique_gdrive
        ON sources (external_id) WHERE source_type = 'google_drive';
    ''')

    op.execute('''
    ALTER TABLE ingest_runs
        ADD COLUMN source_type text NOT NULL DEFAULT 'local_fs'
            CHECK (source_type IN ('local_fs', 'google_drive'));
    ''')


def downgrade() -> None:
    op.execute('DROP INDEX sources_external_id_unique_gdrive;')
    op.execute('DROP INDEX sources_path_unique_local;')

    # 既知のダウングレード制約: Google Drive は同一フォルダ内の重複ファイル名を許容するため、
    # source_type='google_drive' の行同士（または local_fs の行と）で path が重複しうる。
    # その状態で UNIQUE(path) をテーブル全体に復元しようとすると unique violation で失敗する。
    # これは想定内の制約であり、ダウングレード前に重複 path を持つ行を削除/リネームする必要がある。
    # (トランザクショナルDDLにより、失敗時はこのdowngrade()全体がロールバックされ0004のまま残る)
    op.execute('ALTER TABLE sources ADD CONSTRAINT sources_path_key UNIQUE (path);')

    op.execute('ALTER TABLE sources DROP COLUMN source_url;')
    op.execute('ALTER TABLE sources DROP COLUMN external_id;')
    op.execute('ALTER TABLE sources DROP COLUMN source_type;')

    op.execute('ALTER TABLE ingest_runs DROP COLUMN source_type;')
