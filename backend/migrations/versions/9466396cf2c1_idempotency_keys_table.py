"""idempotency_keys table

Revision ID: 9466396cf2c1
Revises: 56d3b26afbbb
Create Date: 2026-08-04 00:00:00.000000

Root cause of the duplicate-task-creation bug (confirmed directly against
production: two rows in `tasks`, identical in every field, `created_at`
0.0 seconds apart): the frontend's "saving" guard only disables the submit
button after a React re-render, which is asynchronous - two submit events
fired in the same tick (double-click, double-Enter) both reach
POST /owner/tasks before the button visually disables, and the backend had
no protection against processing the same logical request twice.

This table backs a general request-level idempotency-key mechanism (see
services/tasks.py) usable by any create endpoint, not just tasks: the
client sends a fresh key per user-initiated submit attempt; the first
request to reach the backend with a given key executes normally and its
response is cached here; and a second, concurrent, or retried request with
the same key gets that cached response replayed instead of creating a
second row. The `key` primary key is what makes this atomic even under
truly concurrent requests: both would attempt the same INSERT, one wins,
the other gets a unique-violation and rolls back its own would-be-duplicate
task insert in the same transaction (see `_idempotent` in services/tasks.py).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '9466396cf2c1'
down_revision: Union[str, Sequence[str], None] = '56d3b26afbbb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'idempotency_keys',
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('endpoint', sa.String(length=50), nullable=False),
        sa.Column('response_body', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('key'),
    )
    # Supports a future cleanup job (e.g. delete rows older than 24h) - not
    # implemented yet since the table is tiny and unbounded growth is a
    # much smaller problem than the bug this fixes; the index makes adding
    # that job later a cheap range scan instead of a sequential one.
    op.create_index('ix_idempotency_keys_created_at', 'idempotency_keys', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_idempotency_keys_created_at', table_name='idempotency_keys')
    op.drop_table('idempotency_keys')
