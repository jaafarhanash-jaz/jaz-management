"""user profile photo fields

Revision ID: 9a1e3f0c5b7d
Revises: 807283509ab4
Create Date: 2026-07-17 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a1e3f0c5b7d'
down_revision: Union[str, Sequence[str], None] = '807283509ab4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Profile photo (User Profile & Account Settings feature) - same object
    # storage architecture as the CV fields above (metadata here, bytes in
    # the S3-compatible bucket via services/storage.py). Deliberately
    # separate from the pre-existing, never-populated `avatar` string
    # column rather than repurposing it, to avoid any ambiguity about that
    # column's contract for existing/future consumers.
    op.add_column('users', sa.Column('avatar_storage_path', sa.String(), nullable=True))
    op.add_column('users', sa.Column('avatar_mime_type', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'avatar_mime_type')
    op.drop_column('users', 'avatar_storage_path')
