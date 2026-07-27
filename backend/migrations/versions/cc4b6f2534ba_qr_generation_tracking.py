"""qr generation tracking

Revision ID: cc4b6f2534ba
Revises: ae5e43ef2bbe
Create Date: 2026-07-26 02:38:36.941504

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cc4b6f2534ba'
down_revision: Union[str, Sequence[str], None] = 'ae5e43ef2bbe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('companies', sa.Column('qr_generated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('companies', 'qr_generated_at')
