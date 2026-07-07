"""chunks_content_bigm

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('''
    CREATE INDEX IF NOT EXISTS chunks_content_bigm
        ON chunks USING gin (content gin_bigm_ops);
    ''')

def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS chunks_content_bigm;')
