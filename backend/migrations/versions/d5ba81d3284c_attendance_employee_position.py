"""attendance employee position

Revision ID: d5ba81d3284c
Revises: cc4b6f2534ba
Create Date: 2026-07-26 02:39:12.142506

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5ba81d3284c'
down_revision: Union[str, Sequence[str], None] = 'cc4b6f2534ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('attendance', sa.Column('employee_position', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('attendance', 'employee_position')
