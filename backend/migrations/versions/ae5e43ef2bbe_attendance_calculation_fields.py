"""attendance calculation fields

Revision ID: ae5e43ef2bbe
Revises: 8775e365657d
Create Date: 2026-07-25 15:43:33.319628

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ae5e43ef2bbe'
down_revision: Union[str, Sequence[str], None] = '8775e365657d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('attendance', sa.Column('schedule_id', sa.UUID(), nullable=True))
    op.add_column('attendance', sa.Column('scheduled_start_time', sa.String(length=5), nullable=True))
    op.add_column('attendance', sa.Column('scheduled_end_time', sa.String(length=5), nullable=True))
    op.add_column('attendance', sa.Column('scheduled_break_minutes', sa.Float(), nullable=True))
    op.add_column('attendance', sa.Column('required_minutes', sa.Float(), nullable=True))
    op.add_column('attendance', sa.Column('late_minutes', sa.Float(), nullable=True))
    op.add_column('attendance', sa.Column('early_arrival_minutes', sa.Float(), nullable=True))
    op.add_column('attendance', sa.Column('overtime_minutes', sa.Float(), nullable=True))
    op.add_column('attendance', sa.Column('missing_minutes', sa.Float(), nullable=True))
    op.add_column('attendance', sa.Column('early_leave_minutes', sa.Float(), nullable=True))
    op.add_column('attendance', sa.Column('net_minutes', sa.Float(), nullable=True))
    op.create_foreign_key(
        'fk_attendance_schedule_id_work_schedules', 'attendance', 'work_schedules', ['schedule_id'], ['id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_attendance_schedule_id_work_schedules', 'attendance', type_='foreignkey')
    op.drop_column('attendance', 'net_minutes')
    op.drop_column('attendance', 'early_leave_minutes')
    op.drop_column('attendance', 'missing_minutes')
    op.drop_column('attendance', 'overtime_minutes')
    op.drop_column('attendance', 'early_arrival_minutes')
    op.drop_column('attendance', 'late_minutes')
    op.drop_column('attendance', 'required_minutes')
    op.drop_column('attendance', 'scheduled_break_minutes')
    op.drop_column('attendance', 'scheduled_end_time')
    op.drop_column('attendance', 'scheduled_start_time')
    op.drop_column('attendance', 'schedule_id')
