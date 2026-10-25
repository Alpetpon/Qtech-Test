"""inbox and replies

Revision ID: 6afada9035e1
Revises: 23f5bb9640c8
Create Date: 2026-10-25 20:09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6afada9035e1'
down_revision: Union[str, Sequence[str], None] = '23f5bb9640c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'replies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('outbox_id', sa.Integer(), nullable=False),
        sa.Column('sender', sa.String(), nullable=False),
        sa.Column('supplier_id', sa.String(), nullable=True),
        sa.Column('mark', sa.String(), nullable=True),
        sa.Column('last_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['outbox_id'], ['outbox.id']),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'inbox',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chat_id', sa.String(), nullable=False),
        sa.Column('external_id', sa.String(), nullable=False),
        sa.Column('sender', sa.String(), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('text', sa.String(), nullable=False),
        sa.Column('attachment', sa.String(), nullable=True),
        sa.Column('reply_id', sa.Integer(), nullable=True),
        sa.Column('mark', sa.String(), nullable=True),
        sa.CheckConstraint('mark IS NULL OR reply_id IS NULL', name='inbox_reply_or_mark'),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id']),
        sa.ForeignKeyConstraint(['reply_id'], ['replies.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('chat_id', 'external_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('inbox')
    op.drop_table('replies')
