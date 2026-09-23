"""attendance_grace_period_snapshot

Revision ID: 12bac1033654
Revises: d65bf4c26b15
Create Date: 2026-08-31 23:10:00.000000

Adds Attendance.grace_period_minutes - the grace-period value actually
used to compute one specific record's forgotten-checkout deadline,
snapshotted once at check-in (from the employee's group, or the global
default when ungrouped) so an Owner editing a group's grace period later
never rewrites the deadline already-open or already-resolved past
sessions were held to. Purely additive, nullable.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '12bac1033654'
down_revision: Union[str, Sequence[str], None] = 'd65bf4c26b15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('attendance', sa.Column('grace_period_minutes', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('attendance', 'grace_period_minutes')
