"""clock

Revision ID: 3bf540103556
Revises: 1bc2db9f34b8
Create Date: 2026-10-25 20:26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3bf540103556'
down_revision: Union[str, Sequence[str], None] = '1bc2db9f34b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'clock',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('real_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('shift', sa.Interval(), nullable=False),
        sa.CheckConstraint('id = 1', name='clock_single_row'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('clock')
