"""reply analysis and offers

Revision ID: 08162d1ddfc0
Revises: 6afada9035e1
Create Date: 2026-10-25 20:19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '08162d1ddfc0'
down_revision: Union[str, Sequence[str], None] = '6afada9035e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('replies', sa.Column('type', sa.String(), nullable=True))
    op.add_column('replies', sa.Column('notes', sa.ARRAY(sa.String()), server_default='{}', nullable=False))
    op.add_column('replies', sa.Column('review', sa.ARRAY(sa.String()), server_default='{}', nullable=False))
    op.add_column('replies', sa.Column('supplier_hint', sa.String(), nullable=True))
    op.add_column('replies', sa.Column('analyzed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key('replies_supplier_hint_fkey', 'replies', 'suppliers', ['supplier_hint'], ['id'])
    op.create_check_constraint('replies_type', 'replies', "type IN ('offer', 'refusal', 'other')")
    op.create_check_constraint('replies_analyzed', 'replies', '(type IS NULL) = (analyzed_at IS NULL)')
    op.create_table(
        'offers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reply_id', sa.Integer(), nullable=False),
        sa.Column('supplier_id', sa.String(), nullable=True),
        sa.Column('product', sa.String(), nullable=True),
        sa.Column('price', sa.Numeric(), nullable=True),
        sa.Column('currency', sa.String(), nullable=True),
        sa.Column('moq', sa.Integer(), nullable=True),
        sa.Column('lead_time_days', sa.Integer(), nullable=True),
        sa.Column('regions', sa.ARRAY(sa.String()), nullable=False),
        sa.Column('quote', sa.String(), nullable=False),
        sa.Column('review', sa.ARRAY(sa.String()), nullable=False),
        sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('supplement_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['reply_id'], ['replies.id']),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('offers')
    op.drop_constraint('replies_analyzed', 'replies', type_='check')
    op.drop_constraint('replies_type', 'replies', type_='check')
    op.drop_constraint('replies_supplier_hint_fkey', 'replies', type_='foreignkey')
    op.drop_column('replies', 'analyzed_at')
    op.drop_column('replies', 'supplier_hint')
    op.drop_column('replies', 'review')
    op.drop_column('replies', 'notes')
    op.drop_column('replies', 'type')
