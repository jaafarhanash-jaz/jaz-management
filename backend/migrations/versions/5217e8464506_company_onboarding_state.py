"""company onboarding state

Revision ID: 5217e8464506
Revises: 9466396cf2c1
Create Date: 2026-09-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5217e8464506'
down_revision: Union[str, Sequence[str], None] = '9466396cf2c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # NOT NULL + server_default backfills every existing row to
    # 'completed' as part of this ADD COLUMN - no separate UPDATE, no risk
    # of missing a row. Only services/admin.py's create_company() ever
    # writes 'not_started' explicitly, for a genuinely brand-new company.
    op.add_column(
        'companies',
        sa.Column('onboarding_status', sa.String(), nullable=False, server_default='completed'),
    )
    op.add_column('companies', sa.Column('onboarding_current_step', sa.String(), nullable=True))
    op.add_column('companies', sa.Column('onboarding_completed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        'ck_companies_onboarding_status',
        'companies',
        "onboarding_status IN ('not_started','in_progress','completed')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_companies_onboarding_status', 'companies', type_='check')
    op.drop_column('companies', 'onboarding_completed_at')
    op.drop_column('companies', 'onboarding_current_step')
    op.drop_column('companies', 'onboarding_status')
