"""sender pause

Revision ID: 23f5bb9640c8
Revises: 9796cc2f8e3a
Create Date: 2026-10-25 20:01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '23f5bb9640c8'
down_revision: Union[str, Sequence[str], None] = '9796cc2f8e3a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('sender_state', sa.Column('paused_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('sender_state', sa.Column('paused_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('sender_state', sa.Column('pause_reason', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('sender_state', 'pause_reason')
    op.drop_column('sender_state', 'paused_until')
    op.drop_column('sender_state', 'paused_at')
