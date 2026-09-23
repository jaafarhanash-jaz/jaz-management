"""employee_groups_work_locations_surprise_attendance

Revision ID: d65bf4c26b15
Revises: 25b565664dce
Create Date: 2026-08-31 22:33:16.106524

Adds the new Attendance Group / Assignment architecture - deliberately
independent of Department (see the 2026-08-31 architecture decision):
WorkLocation (named check-in locations, replacing the legacy single
Company.attendance_* location for companies that adopt them),
EmployeeGroup (the Owner-facing "group/assignment" - schedule + locations
+ independently-toggleable QR/zone/photo/grace policy), its
EmployeeGroupLocation join table (a group may span multiple zones), and
EmployeeGroupAssignment (historical record of employee->group changes).
Also adds Attendance.group_id/location_id traceability, the
'did_not_check_out' status, AuditLog.reason (for manual attendance
corrections), and the SurpriseAttendanceRequest/Response tables.

Purely additive - every new column is nullable or has a default that
reproduces today's exact behavior for any company that never touches the
new group UI (require_zone/require_qr default true, matching today's
implicit always-on GPS+QR gate). Fully reversible.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd65bf4c26b15'
down_revision: Union[str, Sequence[str], None] = '25b565664dce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'employee_groups',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('work_schedule_id', sa.UUID(), nullable=True),
        sa.Column('require_zone', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('require_qr', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('photo_proof_required', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('grace_period_minutes', sa.Integer(), server_default='180', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('grace_period_minutes >= 0', name='ck_employee_groups_grace_period_nonneg'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_employee_groups_company_id_companies'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_employee_groups_created_by_users'),
        sa.ForeignKeyConstraint(
            ['work_schedule_id'], ['work_schedules.id'], name='fk_employee_groups_work_schedule_id_work_schedules',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_employee_groups_company', 'employee_groups', ['company_id'], unique=False)

    op.create_table(
        'employee_group_assignments',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('employee_id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('group_id', sa.UUID(), nullable=True),
        sa.Column('effective_from', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('effective_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('assigned_by', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], name='fk_employee_group_assignments_assigned_by_users'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_employee_group_assignments_company_id_companies'),
        sa.ForeignKeyConstraint(['employee_id'], ['users.id'], name='fk_employee_group_assignments_employee_id_users'),
        sa.ForeignKeyConstraint(
            ['group_id'], ['employee_groups.id'], name='fk_employee_group_assignments_group_id_employee_groups',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_employee_group_assignments_company', 'employee_group_assignments', ['company_id'], unique=False,
    )
    op.create_index(
        'ix_employee_group_assignments_employee', 'employee_group_assignments',
        ['employee_id', 'effective_from'], unique=False,
    )

    op.create_table(
        'work_locations',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('latitude', sa.Float(), nullable=False),
        sa.Column('longitude', sa.Float(), nullable=False),
        sa.Column('radius_meters', sa.Float(), server_default='50.0', nullable=False),
        sa.Column('qr_token', sa.String(), nullable=True),
        sa.Column('qr_code', sa.Text(), nullable=True),
        sa.Column('qr_generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_work_locations_company_id_companies'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_work_locations_company', 'work_locations', ['company_id'], unique=False)

    op.create_table(
        'employee_group_locations',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('group_id', sa.UUID(), nullable=False),
        sa.Column('location_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['group_id'], ['employee_groups.id'], name='fk_employee_group_locations_group_id_employee_groups',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['location_id'], ['work_locations.id'], name='fk_employee_group_locations_location_id_work_locations',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_id', 'location_id', name='uq_employee_group_locations_pair'),
    )
    op.create_index('ix_employee_group_locations_group_id', 'employee_group_locations', ['group_id'], unique=False)
    op.create_index(
        'ix_employee_group_locations_location_id', 'employee_group_locations', ['location_id'], unique=False,
    )

    op.create_table(
        'surprise_attendance_requests',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('requested_by', sa.UUID(), nullable=False),
        sa.Column('target_mode', sa.String(), nullable=False),
        sa.Column('target_ids', postgresql.ARRAY(sa.UUID()), nullable=True),
        sa.Column('require_photo', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('status', sa.String(), server_default='pending', nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','completed','expired')", name='ck_surprise_attendance_requests_status',
        ),
        sa.CheckConstraint(
            "target_mode IN ('everyone','selected_employees','selected_groups')",
            name='ck_surprise_attendance_requests_target_mode',
        ),
        sa.ForeignKeyConstraint(
            ['company_id'], ['companies.id'], name='fk_surprise_attendance_requests_company_id_companies',
        ),
        sa.ForeignKeyConstraint(
            ['requested_by'], ['users.id'], name='fk_surprise_attendance_requests_requested_by_users',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_surprise_attendance_requests_company', 'surprise_attendance_requests', ['company_id'], unique=False,
    )

    op.create_table(
        'surprise_attendance_responses',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('request_id', sa.UUID(), nullable=False),
        sa.Column('employee_id', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(), server_default='pending', nullable=False),
        sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('latitude', sa.Float(), nullable=True),
        sa.Column('longitude', sa.Float(), nullable=True),
        sa.Column('zone_valid', sa.Boolean(), nullable=True),
        sa.Column('photo_storage_path', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','completed','expired')", name='ck_surprise_attendance_responses_status',
        ),
        sa.ForeignKeyConstraint(
            ['employee_id'], ['users.id'], name='fk_surprise_attendance_responses_employee_id_users',
        ),
        sa.ForeignKeyConstraint(
            ['request_id'], ['surprise_attendance_requests.id'],
            name='fk_surprise_attendance_responses_request_id_requests', ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('request_id', 'employee_id', name='uq_surprise_attendance_responses_pair'),
    )
    op.create_index(
        'ix_surprise_attendance_responses_employee_id', 'surprise_attendance_responses', ['employee_id'],
        unique=False,
    )
    op.create_index(
        'ix_surprise_attendance_responses_request_id', 'surprise_attendance_responses', ['request_id'],
        unique=False,
    )

    op.add_column('attendance', sa.Column('group_id', sa.UUID(), nullable=True))
    op.add_column('attendance', sa.Column('location_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_attendance_group_id_employee_groups', 'attendance', 'employee_groups', ['group_id'], ['id'],
    )
    op.create_foreign_key(
        'fk_attendance_location_id_work_locations', 'attendance', 'work_locations', ['location_id'], ['id'],
    )
    op.drop_constraint('ck_attendance_status', 'attendance', type_='check')
    op.create_check_constraint(
        'ck_attendance_status', 'attendance', "status IN ('present','late','absent','did_not_check_out')",
    )

    op.add_column('audit_logs', sa.Column('reason', sa.Text(), nullable=True))

    op.add_column('users', sa.Column('group_id', sa.UUID(), nullable=True))
    op.create_index('ix_users_group_id', 'users', ['group_id'], unique=False)
    op.create_foreign_key('fk_users_group_id_employee_groups', 'users', 'employee_groups', ['group_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_users_group_id_employee_groups', 'users', type_='foreignkey')
    op.drop_index('ix_users_group_id', table_name='users')
    op.drop_column('users', 'group_id')

    op.drop_column('audit_logs', 'reason')

    op.drop_constraint('ck_attendance_status', 'attendance', type_='check')
    op.create_check_constraint('ck_attendance_status', 'attendance', "status IN ('present','late','absent')")
    op.drop_constraint('fk_attendance_location_id_work_locations', 'attendance', type_='foreignkey')
    op.drop_constraint('fk_attendance_group_id_employee_groups', 'attendance', type_='foreignkey')
    op.drop_column('attendance', 'location_id')
    op.drop_column('attendance', 'group_id')

    op.drop_index('ix_surprise_attendance_responses_request_id', table_name='surprise_attendance_responses')
    op.drop_index('ix_surprise_attendance_responses_employee_id', table_name='surprise_attendance_responses')
    op.drop_table('surprise_attendance_responses')

    op.drop_index('ix_surprise_attendance_requests_company', table_name='surprise_attendance_requests')
    op.drop_table('surprise_attendance_requests')

    op.drop_index('ix_employee_group_locations_location_id', table_name='employee_group_locations')
    op.drop_index('ix_employee_group_locations_group_id', table_name='employee_group_locations')
    op.drop_table('employee_group_locations')

    op.drop_index('ix_work_locations_company', table_name='work_locations')
    op.drop_table('work_locations')

    op.drop_index('ix_employee_group_assignments_employee', table_name='employee_group_assignments')
    op.drop_index('ix_employee_group_assignments_company', table_name='employee_group_assignments')
    op.drop_table('employee_group_assignments')

    op.drop_index('ix_employee_groups_company', table_name='employee_groups')
    op.drop_table('employee_groups')
