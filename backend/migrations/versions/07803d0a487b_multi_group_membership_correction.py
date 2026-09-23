"""multi_group_membership_correction

Revision ID: 07803d0a487b
Revises: 12bac1033654
Create Date: 2026-08-31 23:57:54.496893

Architecture correction: an employee may belong to MULTIPLE Attendance
Groups simultaneously (e.g. "Sales Representatives" AND "Al-Bayaa Branch"
at once, per the approved spec's own worked example) - a single scalar
group_id on User cannot represent that. This migration:

- Drops users.group_id (column, its FK, its index) - it was never
  actually written to by any live code path (verified zero non-null rows
  before this migration), so this is a clean removal, not a data-loss
  concern.
- Makes employee_group_assignments.group_id NOT NULL - a row now always
  represents a specific membership; "no groups" is zero open rows, not a
  NULL-group placeholder (the 14 such placeholder rows that existed from
  local testing were deleted manually before this migration, verified
  disposable test data, not real business records).
- Adds a partial unique index so an employee can have at most one OPEN
  (effective_to IS NULL) row per group, while placing no limit on how
  many DIFFERENT groups they have open rows for at once.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '07803d0a487b'
down_revision: Union[str, Sequence[str], None] = '12bac1033654'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('employee_group_assignments', 'group_id', existing_type=sa.UUID(), nullable=False)
    op.create_index(
        'uq_employee_group_assignments_open_pair', 'employee_group_assignments', ['employee_id', 'group_id'],
        unique=True, postgresql_where=sa.text('effective_to IS NULL'),
    )

    op.drop_constraint('fk_users_group_id_employee_groups', 'users', type_='foreignkey')
    op.drop_index('ix_users_group_id', table_name='users')
    op.drop_column('users', 'group_id')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('users', sa.Column('group_id', sa.UUID(), nullable=True))
    op.create_index('ix_users_group_id', 'users', ['group_id'], unique=False)
    op.create_foreign_key('fk_users_group_id_employee_groups', 'users', 'employee_groups', ['group_id'], ['id'])

    op.drop_index('uq_employee_group_assignments_open_pair', table_name='employee_group_assignments')
    op.alter_column('employee_group_assignments', 'group_id', existing_type=sa.UUID(), nullable=True)
