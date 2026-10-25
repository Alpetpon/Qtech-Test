"""campaigns and outbox

Revision ID: 8904589657be
Revises: 17699fa4b792
Create Date: 2026-10-25 19:43

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8904589657be'
down_revision: Union[str, Sequence[str], None] = '17699fa4b792'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'campaigns',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('preset', sa.String(), nullable=True),
        sa.Column('supplier_status', sa.String(), nullable=True),
        sa.Column('template', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('(preset IS NULL) <> (supplier_status IS NULL)', name='campaigns_one_selection'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'campaign_suppliers',
        sa.Column('campaign_id', sa.Integer(), nullable=False),
        sa.Column('supplier_id', sa.String(), nullable=False),
        sa.Column('chat_id', sa.String(), nullable=True),
        sa.Column('reasons', sa.ARRAY(sa.String()), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.CheckConstraint(
            '(chat_id IS NULL) <> (cardinality(reasons) = 0)', name='campaign_suppliers_chat_or_reasons'
        ),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id']),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id']),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id']),
        sa.PrimaryKeyConstraint('campaign_id', 'supplier_id'),
    )
    op.create_table(
        'outbox',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('campaign_id', sa.Integer(), nullable=False),
        sa.Column('chat_id', sa.String(), nullable=False),
        sa.Column('text', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'sending', 'sent', 'unknown')", name='outbox_status'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id']),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('campaign_id', 'chat_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('outbox')
    op.drop_table('campaign_suppliers')
    op.drop_table('campaigns')
