"""attendance events table

Revision ID: 05c0a2518ccd
Revises: d5ba81d3284c
Create Date: 2026-07-26 02:39:53.247988

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '05c0a2518ccd'
down_revision: Union[str, Sequence[str], None] = 'd5ba81d3284c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('attendance_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('attendance_record_id', sa.UUID(), nullable=True),
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=False),
    sa.Column('action_type', sa.String(), nullable=False),
    sa.Column('event_date', sa.Date(), nullable=False),
    sa.Column('event_time', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('latitude', sa.Float(), nullable=True),
    sa.Column('longitude', sa.Float(), nullable=True),
    # Nullable, not just Optional-in-Python: "not evaluated" (e.g. GPS was
    # never checked because the QR itself already failed) is a real third
    # state, distinct from True/False - forcing a boolean here would
    # misrepresent an untested stage as having passed or failed.
    sa.Column('qr_valid', sa.Boolean(), nullable=True),
    sa.Column('gps_valid', sa.Boolean(), nullable=True),
    sa.Column('failure_reason', sa.Text(), nullable=True),
    sa.Column('device_platform', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("action_type IN ('check_in','check_out')", name='ck_attendance_events_action_type'),
    sa.ForeignKeyConstraint(['attendance_record_id'], ['attendance.id'], ),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ),
    sa.ForeignKeyConstraint(['employee_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_attendance_events_record', 'attendance_events', ['attendance_record_id'], unique=False)
    op.create_index('ix_attendance_events_company_date', 'attendance_events', ['company_id', 'event_date'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_attendance_events_company_date', table_name='attendance_events')
    op.drop_index('ix_attendance_events_record', table_name='attendance_events')
    op.drop_table('attendance_events')
