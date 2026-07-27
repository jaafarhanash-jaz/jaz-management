"""work schedules table

Revision ID: f77bd76bc030
Revises: 9499f3ab1f87
Create Date: 2026-07-25 15:42:16.088288

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f77bd76bc030'
down_revision: Union[str, Sequence[str], None] = '9499f3ab1f87'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('work_schedules',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('start_time', sa.String(length=5), nullable=False),
    sa.Column('end_time', sa.String(length=5), nullable=False),
    sa.Column('break_start_time', sa.String(length=5), nullable=True),
    sa.Column('break_end_time', sa.String(length=5), nullable=True),
    sa.Column('required_hours', sa.Float(), nullable=False),
    sa.Column('working_days', sa.ARRAY(sa.Integer()), nullable=False),
    sa.Column('is_default', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('required_hours > 0', name='ck_work_schedules_required_hours'),
    sa.CheckConstraint('array_length(working_days, 1) > 0', name='ck_work_schedules_working_days_nonempty'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ),
    sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_work_schedules_company', 'work_schedules', ['company_id'], unique=False)
    op.create_index(
        'uq_work_schedules_company_default', 'work_schedules', ['company_id'],
        unique=True, postgresql_where=sa.text('is_default = true'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_work_schedules_company_default', table_name='work_schedules')
    op.drop_index('ix_work_schedules_company', table_name='work_schedules')
    op.drop_table('work_schedules')
