"""cascade rules for daily task template deletion

Revision ID: a7f14eb00a17
Revises: c59f9dbb8729
Create Date: 2026-07-14 11:44:59.606396

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7f14eb00a17'
down_revision: Union[str, Sequence[str], None] = 'c59f9dbb8729'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint('daily_task_assignees_daily_task_id_fkey', 'daily_task_assignees', type_='foreignkey')
    op.create_foreign_key(
        'daily_task_assignees_daily_task_id_fkey', 'daily_task_assignees', 'daily_tasks',
        ['daily_task_id'], ['id'], ondelete='CASCADE',
    )
    op.drop_constraint('tasks_daily_task_id_fkey', 'tasks', type_='foreignkey')
    op.create_foreign_key(
        'tasks_daily_task_id_fkey', 'tasks', 'daily_tasks',
        ['daily_task_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('tasks_daily_task_id_fkey', 'tasks', type_='foreignkey')
    op.create_foreign_key('tasks_daily_task_id_fkey', 'tasks', 'daily_tasks', ['daily_task_id'], ['id'])
    op.drop_constraint('daily_task_assignees_daily_task_id_fkey', 'daily_task_assignees', type_='foreignkey')
    op.create_foreign_key(
        'daily_task_assignees_daily_task_id_fkey', 'daily_task_assignees', 'daily_tasks',
        ['daily_task_id'], ['id'],
    )
