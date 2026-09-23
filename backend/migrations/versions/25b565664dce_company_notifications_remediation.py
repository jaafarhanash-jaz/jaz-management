"""company notifications remediation

Revision ID: 25b565664dce
Revises: 2aefb2469666
Create Date: 2026-08-31 12:00:00.000000

Follow-up to 2aefb2469666, after a security/correctness audit of the
Company Notifications feature:

- company_notifications.delivered_count / failed_count renamed to
  notification_created_count / push_failed_count (they only ever measured
  whether the per-recipient Notification DB row was created, never actual
  FCM delivery - the old names were misleading). New push_delivered_count
  column added, now backed by real per-token FCM outcomes.
- company_notifications.idempotency_key (nullable, unique) added - same
  client-supplied-key + DB-uniqueness pattern as the existing
  idempotency_keys table (see services/tasks.py::_idempotent), scoped to
  this table instead because idempotency_keys.company_id is NOT NULL and
  doesn't fit a cross-company broadcast.

No other tables touched. Does not repair the pre-existing, unrelated local
Alembic drift noted in 2aefb2469666's own review.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '25b565664dce'
down_revision: Union[str, Sequence[str], None] = '2aefb2469666'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('company_notifications', 'delivered_count', new_column_name='notification_created_count')
    op.alter_column('company_notifications', 'failed_count', new_column_name='push_failed_count')
    op.add_column(
        'company_notifications',
        sa.Column('push_delivered_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'company_notifications',
        sa.Column('idempotency_key', sa.String(length=100), nullable=True),
    )
    op.create_unique_constraint(
        'uq_company_notifications_idempotency_key', 'company_notifications', ['idempotency_key']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_company_notifications_idempotency_key', 'company_notifications', type_='unique')
    op.drop_column('company_notifications', 'idempotency_key')
    op.drop_column('company_notifications', 'push_delivered_count')
    op.alter_column('company_notifications', 'push_failed_count', new_column_name='failed_count')
    op.alter_column('company_notifications', 'notification_created_count', new_column_name='delivered_count')
