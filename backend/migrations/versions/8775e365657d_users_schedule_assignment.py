"""users schedule assignment

Revision ID: 8775e365657d
Revises: f77bd76bc030
Create Date: 2026-07-25 15:42:54.572430

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8775e365657d'
down_revision: Union[str, Sequence[str], None] = 'f77bd76bc030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('schedule_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_users_schedule_id_work_schedules', 'users', 'work_schedules', ['schedule_id'], ['id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_users_schedule_id_work_schedules', 'users', type_='foreignkey')
    op.drop_column('users', 'schedule_id')
