"""reject null elements in tasks proof_files

Revision ID: f587f273218b
Revises: 9a1e3f0c5b7d
Create Date: 2026-07-20 20:00:26.227559

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f587f273218b'
down_revision: Union[str, Sequence[str], None] = '9a1e3f0c5b7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Defense in depth, Layer 4 (see the proof-upload incident report):
    # a malformed `POST /employee/tasks/{id}/proof` request previously
    # got as far as `array_append(proof_files, NULL)`, corrupting the
    # column and breaking TaskResponse serialization for that task.
    # Layers 1-3 (Pydantic request model, service re-validation,
    # repository guard) now stop this before it reaches SQL - this
    # constraint is the last-resort backstop in case any of them is
    # ever bypassed. `array_position(arr, NULL)` is Postgres's
    # NULL-aware search (unlike `NULL = ANY(arr)`, which is always
    # NULL/unknown per three-valued logic and can never be used in a
    # CHECK); it returns the index of a NULL element if one exists, so
    # this only ever rejects a NULL element - non-null values,
    # including an empty array, are unaffected. Non-breaking: verified
    # no existing row violates it before this migration was written.
    op.create_check_constraint(
        'ck_tasks_proof_files_no_null',
        'tasks',
        'array_position(proof_files, NULL) IS NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_tasks_proof_files_no_null', 'tasks', type_='check')
