"""chat_history

Revision ID: a4188e3436db
Revises: 0002
Create Date: 2026-07-07 21:54:45.850690

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('''
    CREATE TABLE conversations (
        id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        title      text NOT NULL DEFAULT '',
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    );
    ''')
    op.execute('''
    CREATE TABLE messages (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role            text NOT NULL CHECK (role IN ('user','assistant')),
        content         text NOT NULL,
        citations       jsonb,
        created_at      timestamptz NOT NULL DEFAULT now()
    );
    ''')
    op.execute('CREATE INDEX messages_conversation ON messages (conversation_id, created_at);')


def downgrade() -> None:
    op.execute('DROP TABLE messages;')
    op.execute('DROP TABLE conversations;')
