"""reference data

Revision ID: 17699fa4b792
Revises:
Create Date: 2026-10-25 18:58

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '17699fa4b792'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'suppliers',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('manager', sa.String(), nullable=True),
        sa.Column('price_updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'chats',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('member', sa.Boolean(), nullable=False),
        sa.Column('last_outbound_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'chat_suppliers',
        sa.Column('chat_id', sa.String(), nullable=False),
        sa.Column('supplier_id', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id']),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id']),
        sa.PrimaryKeyConstraint('chat_id', 'supplier_id'),
    )
    op.create_table(
        'our_accounts',
        sa.Column('handle', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('handle'),
    )
    op.create_table(
        'supplier_contacts',
        sa.Column('handle', sa.String(), nullable=False),
        sa.Column('supplier_id', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id']),
        sa.PrimaryKeyConstraint('handle'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('supplier_contacts')
    op.drop_table('our_accounts')
    op.drop_table('chat_suppliers')
    op.drop_table('chats')
    op.drop_table('suppliers')
