"""leave_requests

Revision ID: 60b5feec85d9
Revises: 07803d0a487b
Create Date: 2026-09-01 14:23:58.977495

Adds the Leave domain (Feature A) - a fully independent entity, never an
Attendance row or Attendance.status value. Three duration shapes share one
table: time_based (a clock interval within a single shift), full_day (one
or more whole calendar dates), and remaining_of_day (backend-resolved end
time). See models.py's Leave docstring for the start_date/end_date
(shift-owning date, same concept as Attendance.date) vs. start_at/end_at
(absolute instants, used only for overlap detection) distinction, and
services/leave_attendance.py (added in a later migration-free change) for
how an APPROVED leave's effect on attendance obligations is computed at
read time, mirroring services/holidays.py's annotate-on-read pattern
rather than mutating any stored Attendance field.

Enables btree_gist (not previously used anywhere in this schema) to
support an EXCLUDE constraint that is the DB-level backstop preventing two
overlapping APPROVED leaves for the same employee, even under concurrent
approvals - GIST has no native support for '=' on a UUID column without
it. This is a defense-in-depth guarantee alongside the transactional
row-locked overlap re-check the service layer performs at approval time
(services/leaves.py); the service-layer check is the primary path (a
friendly 409 in the common case), this constraint is what makes the
guarantee airtight under a true race.

Purely additive - no existing table is altered. Note: this migration
intentionally does NOT include the ~28 "removed index" operations
`alembic revision --autogenerate` also proposed (e.g.
ix_users_schedule_id, ix_tasks_created_by, ix_companies_owner_id, ...) -
those are pre-existing indexes present in the live database but
undeclared in the current models.py (their fk_uuid() calls don't pass
index=True). That drift predates this change, is unrelated to Leave, and
is being reported separately rather than silently cleaned up here (Rule
0.4) - dropping ~28 unrelated indexes as a side effect of a Leave
migration would be exactly the kind of silent, out-of-scope schema change
this project's rules prohibit.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '60b5feec85d9'
down_revision: Union[str, Sequence[str], None] = '07803d0a487b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        'leaves',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('employee_id', sa.UUID(), nullable=False),
        sa.Column('duration_type', sa.String(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('status', sa.String(), server_default='pending', nullable=False),
        sa.Column('origin', sa.String(), nullable=False),
        sa.Column('requested_by', sa.UUID(), nullable=False),
        sa.Column('approved_by', sa.UUID(), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rejected_by', sa.UUID(), nullable=True),
        sa.Column('rejected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('cancelled_by', sa.UUID(), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancellation_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "duration_type IN ('time_based','full_day','remaining_of_day')", name='ck_leaves_duration_type',
        ),
        sa.CheckConstraint("origin IN ('employee','owner')", name='ck_leaves_origin'),
        sa.CheckConstraint("status IN ('pending','approved','rejected','cancelled')", name='ck_leaves_status'),
        sa.CheckConstraint('end_at > start_at', name='ck_leaves_instant_order'),
        sa.CheckConstraint('end_date >= start_date', name='ck_leaves_date_order'),
        postgresql.ExcludeConstraint(
            (sa.column('employee_id'), '='),
            (sa.text('tstzrange(start_at, end_at)'), '&&'),
            where=sa.text("status = 'approved'"),
            using='gist',
            name='ex_leaves_no_overlap_when_approved',
        ),
        sa.ForeignKeyConstraint(['approved_by'], ['users.id'], name='fk_leaves_approved_by_users'),
        sa.ForeignKeyConstraint(['cancelled_by'], ['users.id'], name='fk_leaves_cancelled_by_users'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_leaves_company_id_companies'),
        sa.ForeignKeyConstraint(['employee_id'], ['users.id'], name='fk_leaves_employee_id_users'),
        sa.ForeignKeyConstraint(['rejected_by'], ['users.id'], name='fk_leaves_rejected_by_users'),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id'], name='fk_leaves_requested_by_users'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_leaves_company_status', 'leaves', ['company_id', 'status'], unique=False)
    op.create_index('ix_leaves_employee_dates', 'leaves', ['employee_id', 'start_date', 'end_date'], unique=False)
    op.create_index('ix_leaves_employee_status', 'leaves', ['employee_id', 'status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_leaves_employee_status', table_name='leaves')
    op.drop_index('ix_leaves_employee_dates', table_name='leaves')
    op.drop_index('ix_leaves_company_status', table_name='leaves')
    op.drop_table('leaves')
    # Safe to drop unconditionally - this migration is the only feature in
    # this schema that depends on btree_gist (confirmed via
    # pg_available_extensions before adding it; nothing else uses it).
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
