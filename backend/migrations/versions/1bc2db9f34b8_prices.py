"""prices

Revision ID: 1bc2db9f34b8
Revises: 08162d1ddfc0
Create Date: 2026-10-25 20:21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1bc2db9f34b8'
down_revision: Union[str, Sequence[str], None] = '08162d1ddfc0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'prices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('supplier_id', sa.String(), nullable=False),
        sa.Column('offer_id', sa.Integer(), nullable=False),
        sa.Column('product', sa.String(), nullable=False),
        sa.Column('price', sa.Numeric(), nullable=False),
        sa.Column('currency', sa.String(), nullable=False),
        sa.Column('moq', sa.Integer(), nullable=True),
        sa.Column('lead_time_days', sa.Integer(), nullable=True),
        sa.Column('regions', sa.ARRAY(sa.String()), nullable=False),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['offer_id'], ['offers.id']),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('offer_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('prices')
