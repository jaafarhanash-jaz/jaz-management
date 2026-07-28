"""add remaining fk indexes

Revision ID: 56d3b26afbbb
Revises: 801cb5b4de8b
Create Date: 2026-07-28 15:57:18.891372

The previous FK-index pass (ca3f9acc6d73) covered the company_id/employee_id/
etc. columns that are the primary filter on nearly every list endpoint. This
pass covers the remaining 29 FK columns found with no supporting index at
all (verified directly against production via information_schema/pg_index) -
mostly "secondary" audit-style FKs (created_by, updated_by, completed_by,
uploaded_by, parent/forwarded references). Without an index, Postgres must
sequentially scan the referencing table on every UPDATE/DELETE of a
referenced row (e.g. deleting a user) to enforce the FK, and any query
filtering/joining on these columns does the same.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '56d3b26afbbb'
down_revision: Union[str, Sequence[str], None] = '801cb5b4de8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(op.f('ix_announcements_created_by'), 'announcements', ['created_by'], unique=False)
    op.create_index(op.f('ix_attendance_schedule_id'), 'attendance', ['schedule_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_user_id'), 'audit_logs', ['user_id'], unique=False)
    op.create_index(op.f('ix_calendar_attachments_uploaded_by'), 'calendar_attachments', ['uploaded_by'], unique=False)
    op.create_index(op.f('ix_calendar_attachments_event_id'), 'calendar_attachments', ['event_id'], unique=False)
    op.create_index(op.f('ix_calendar_event_participants_participant_id'), 'calendar_event_participants', ['participant_id'], unique=False)
    op.create_index(op.f('ix_calendar_event_reminders_event_id'), 'calendar_event_reminders', ['event_id'], unique=False)
    op.create_index(op.f('ix_calendar_events_linked_thread_id'), 'calendar_events', ['linked_thread_id'], unique=False)
    op.create_index(op.f('ix_calendar_events_updated_by'), 'calendar_events', ['updated_by'], unique=False)
    op.create_index(op.f('ix_calendar_events_created_by'), 'calendar_events', ['created_by'], unique=False)
    op.create_index(op.f('ix_companies_owner_id'), 'companies', ['owner_id'], unique=False)
    op.create_index(op.f('ix_companies_attendance_settings_updated_by'), 'companies', ['attendance_settings_updated_by'], unique=False)
    op.create_index(op.f('ix_companies_subscription_plan_id'), 'companies', ['subscription_plan_id'], unique=False)
    op.create_index(op.f('ix_daily_tasks_created_by'), 'daily_tasks', ['created_by'], unique=False)
    op.create_index(op.f('ix_departments_head_id'), 'departments', ['head_id'], unique=False)
    op.create_index(op.f('ix_message_attachments_uploaded_by'), 'message_attachments', ['uploaded_by'], unique=False)
    op.create_index(op.f('ix_message_attachments_message_id'), 'message_attachments', ['message_id'], unique=False)
    op.create_index(op.f('ix_message_reminders_message_id'), 'message_reminders', ['message_id'], unique=False)
    op.create_index(op.f('ix_messages_parent_message_id'), 'messages', ['parent_message_id'], unique=False)
    op.create_index(op.f('ix_messages_forwarded_from_id'), 'messages', ['forwarded_from_id'], unique=False)
    op.create_index(op.f('ix_notifications_sender_id'), 'notifications', ['sender_id'], unique=False)
    op.create_index(op.f('ix_payment_transactions_plan_id'), 'payment_transactions', ['plan_id'], unique=False)
    op.create_index(op.f('ix_refresh_tokens_replaced_by_id'), 'refresh_tokens', ['replaced_by_id'], unique=False)
    op.create_index(op.f('ix_task_attachments_task_id'), 'task_attachments', ['task_id'], unique=False)
    op.create_index(op.f('ix_task_attachments_uploaded_by'), 'task_attachments', ['uploaded_by'], unique=False)
    op.create_index(op.f('ix_tasks_created_by'), 'tasks', ['created_by'], unique=False)
    op.create_index(op.f('ix_tasks_completed_by'), 'tasks', ['completed_by'], unique=False)
    op.create_index(op.f('ix_tasks_daily_task_id'), 'tasks', ['daily_task_id'], unique=False)
    op.create_index(op.f('ix_users_cv_uploaded_by'), 'users', ['cv_uploaded_by'], unique=False)
    op.create_index(op.f('ix_users_schedule_id'), 'users', ['schedule_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_users_schedule_id'), table_name='users')
    op.drop_index(op.f('ix_users_cv_uploaded_by'), table_name='users')
    op.drop_index(op.f('ix_tasks_daily_task_id'), table_name='tasks')
    op.drop_index(op.f('ix_tasks_completed_by'), table_name='tasks')
    op.drop_index(op.f('ix_tasks_created_by'), table_name='tasks')
    op.drop_index(op.f('ix_task_attachments_uploaded_by'), table_name='task_attachments')
    op.drop_index(op.f('ix_task_attachments_task_id'), table_name='task_attachments')
    op.drop_index(op.f('ix_refresh_tokens_replaced_by_id'), table_name='refresh_tokens')
    op.drop_index(op.f('ix_payment_transactions_plan_id'), table_name='payment_transactions')
    op.drop_index(op.f('ix_notifications_sender_id'), table_name='notifications')
    op.drop_index(op.f('ix_messages_forwarded_from_id'), table_name='messages')
    op.drop_index(op.f('ix_messages_parent_message_id'), table_name='messages')
    op.drop_index(op.f('ix_message_reminders_message_id'), table_name='message_reminders')
    op.drop_index(op.f('ix_message_attachments_message_id'), table_name='message_attachments')
    op.drop_index(op.f('ix_message_attachments_uploaded_by'), table_name='message_attachments')
    op.drop_index(op.f('ix_departments_head_id'), table_name='departments')
    op.drop_index(op.f('ix_daily_tasks_created_by'), table_name='daily_tasks')
    op.drop_index(op.f('ix_companies_subscription_plan_id'), table_name='companies')
    op.drop_index(op.f('ix_companies_attendance_settings_updated_by'), table_name='companies')
    op.drop_index(op.f('ix_companies_owner_id'), table_name='companies')
    op.drop_index(op.f('ix_calendar_events_created_by'), table_name='calendar_events')
    op.drop_index(op.f('ix_calendar_events_updated_by'), table_name='calendar_events')
    op.drop_index(op.f('ix_calendar_events_linked_thread_id'), table_name='calendar_events')
    op.drop_index(op.f('ix_calendar_event_reminders_event_id'), table_name='calendar_event_reminders')
    op.drop_index(op.f('ix_calendar_event_participants_participant_id'), table_name='calendar_event_participants')
    op.drop_index(op.f('ix_calendar_attachments_event_id'), table_name='calendar_attachments')
    op.drop_index(op.f('ix_calendar_attachments_uploaded_by'), table_name='calendar_attachments')
    op.drop_index(op.f('ix_audit_logs_user_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_attendance_schedule_id'), table_name='attendance')
    op.drop_index(op.f('ix_announcements_created_by'), table_name='announcements')
