"""company notifications

Revision ID: 2aefb2469666
Revises: 9466396cf2c1
Create Date: 2026-08-31 00:00:00.000000

Adds the Super Admin "Company Notifications" broadcast feature:

- users.language: per-account UI/notification language preference
  (ar/en, default ar - matches the mobile app's existing Arabic-first
  default). Used to pick which language variant of a broadcast a given
  recipient is sent.
- company_notifications: the durable audit record for a Super Admin
  broadcast (title/message in both languages, resolved targeting mode
  and config, recipient/delivery/failure counts, status, timestamps).
  Sending one fans out real per-recipient rows through the existing
  Notification/publish() framework - this table does not duplicate that
  framework, it's the audience-resolution + audit layer on top of it.
  Deliberately separate from the existing `announcements` table, which
  is a narrower, already-shipped feature (one company_owner broadcasting
  to their own company's employees only) that this migration does not
  touch.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '2aefb2469666'
down_revision: Union[str, Sequence[str], None] = '9466396cf2c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column('language', sa.String(), nullable=False, server_default='ar'),
    )
    op.create_check_constraint('ck_users_language', 'users', "language IN ('ar','en')")

    op.create_table(
        'company_notifications',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('sender_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('sender_name', sa.String(), nullable=False),
        sa.Column('title_ar', sa.String(), nullable=False),
        sa.Column('title_en', sa.String(), nullable=False),
        sa.Column('message_ar', sa.Text(), nullable=False),
        sa.Column('message_en', sa.Text(), nullable=False),
        sa.Column('recipient_mode', sa.String(), nullable=False),
        sa.Column('target_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('recipient_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('delivered_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failed_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['sender_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            "recipient_mode IN ('all_managers','all_employees','everyone','custom')",
            name='ck_company_notifications_recipient_mode',
        ),
        sa.CheckConstraint(
            "status IN ('sent','partial_failure','failed')", name='ck_company_notifications_status'
        ),
    )
    op.create_index(
        op.f('ix_company_notifications_sender_id'), 'company_notifications', ['sender_id'], unique=False
    )
    op.create_index(
        op.f('ix_company_notifications_created_at'), 'company_notifications', ['created_at'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_company_notifications_created_at'), table_name='company_notifications')
    op.drop_index(op.f('ix_company_notifications_sender_id'), table_name='company_notifications')
    op.drop_table('company_notifications')

    op.drop_constraint('ck_users_language', 'users', type_='check')
    op.drop_column('users', 'language')
