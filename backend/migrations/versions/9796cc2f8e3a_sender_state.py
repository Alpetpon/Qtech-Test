"""sender state

Revision ID: 9796cc2f8e3a
Revises: 8904589657be
Create Date: 2026-10-25 19:57

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9796cc2f8e3a'
down_revision: Union[str, Sequence[str], None] = '8904589657be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'sender_state',
        sa.Column('account', sa.String(), nullable=False),
        sa.Column('next_send_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('account'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('sender_state')
