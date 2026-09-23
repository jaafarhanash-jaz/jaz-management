"""sales: audit trail (staff_audit_events) and revocation integrity

Pre-Phase-2 hardening of the JAZ Sales foundation. Hand-written (never autogenerate here: it would emit
DROP INDEX for ~30 unrelated FK indexes). Chained after the core identifier revision 640813d5cbc3.

Upgrade
  1. staff_user_roles: CHECK ck_staff_user_roles_revoked_pair - a revocation always records who did it:
         (revoked_at IS NULL AND revoked_by IS NULL) OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL)
     Added as a plain (validated) CHECK: the table is tiny and Phase 1 code always writes both columns in
     one UPDATE. If a database somehow holds a violating row this fails loudly and rolls back; find them with
         SELECT * FROM staff_user_roles WHERE NOT ((revoked_at IS NULL AND revoked_by IS NULL)
                                                OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL));
  2. staff_audit_events: the dedicated, append-only security/admin audit trail (see sales/models.py
     StaffAuditEvent). It is NOT the company-bound core audit_logs. UPDATE, DELETE and TRUNCATE are
     rejected by triggers; retention (when it exists) has to be a deliberate, privileged maintenance action.
     Only structure is created - no rows are seeded.

Downgrade
  * REFUSES to run while staff_audit_events holds any row: audit history is never dropped silently.
    Archive it and then remove the rows deliberately (the table owner must first disable the append-only
    trigger for that - which is the point) before downgrading.
  * otherwise drops the audit table, its trigger function and the revocation CHECK.

Revision ID: 2d916a1a993e
Revises: 640813d5cbc3
Create Date: 2026-09-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '2d916a1a993e'
down_revision: Union[str, Sequence[str], None] = '640813d5cbc3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REVOKED_PAIR = (
    "(revoked_at IS NULL AND revoked_by IS NULL) OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL)"
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")

    # ---- 1. revocation integrity ----
    op.create_check_constraint('ck_staff_user_roles_revoked_pair', 'staff_user_roles', _REVOKED_PAIR)

    # ---- 2. the audit trail ----
    op.create_table(
        'staff_audit_events',
        sa.Column('id', sa.UUID(), nullable=False),  # app-generated, like every other table
        sa.Column('seq', sa.BigInteger(), sa.Identity(always=True), nullable=False),  # monotonic cursor / export watermark
        sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False),
        sa.Column('module', sa.String(), nullable=False),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('outcome', sa.String(), server_default=sa.text("'success'"), nullable=False),
        sa.Column('actor_type', sa.String(), server_default=sa.text("'user'"), nullable=False),
        sa.Column('actor_user_id', sa.UUID(), nullable=True),
        sa.Column('actor_platform_role', sa.String(), nullable=True),
        sa.Column('target_type', sa.String(), nullable=False),
        sa.Column('target_id', sa.UUID(), nullable=True),  # polymorphic soft reference - deliberately no FK
        sa.Column('target_user_id', sa.UUID(), nullable=True),
        sa.Column('before_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('after_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('correlation_id', sa.UUID(), nullable=False),
        sa.Column('ip_address', postgresql.INET(), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        # Users are soft-deleted in this platform; NO ACTION (default) like every other FK to users.
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['target_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('seq', name='uq_staff_audit_events_seq'),
        sa.CheckConstraint("module ~ '^[a-z][a-z0-9_]{1,31}$'", name='ck_staff_audit_events_module'),
        sa.CheckConstraint("action ~ '^[a-z][a-z0-9_]{2,63}$'", name='ck_staff_audit_events_action'),
        sa.CheckConstraint("target_type ~ '^[a-z][a-z0-9_]{1,63}$'", name='ck_staff_audit_events_target_type'),
        sa.CheckConstraint("outcome IN ('success','denied','failed')", name='ck_staff_audit_events_outcome'),
        sa.CheckConstraint(
            "(actor_type = 'user' AND actor_user_id IS NOT NULL) OR (actor_type = 'system' AND actor_user_id IS NULL)",
            name='ck_staff_audit_events_actor',
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object' AND pg_column_size(metadata) <= 8192",
            name='ck_staff_audit_events_metadata',
        ),
        sa.CheckConstraint('user_agent IS NULL OR length(user_agent) <= 512', name='ck_staff_audit_events_ua_len'),
    )
    # One index per question an investigator asks; the two user-id ones double as FK-lookup indexes.
    op.create_index('ix_staff_audit_events_target_user', 'staff_audit_events', ['target_user_id', sa.text('seq DESC')],
                    postgresql_where=sa.text('target_user_id IS NOT NULL'))   # history of a person
    op.create_index('ix_staff_audit_events_actor', 'staff_audit_events', ['actor_user_id', sa.text('seq DESC')],
                    postgresql_where=sa.text('actor_user_id IS NOT NULL'))    # everything an actor did
    op.create_index('ix_staff_audit_events_target', 'staff_audit_events', ['target_type', 'target_id', sa.text('seq DESC')],
                    postgresql_where=sa.text('target_id IS NOT NULL'))        # history of one entity
    op.create_index('ix_staff_audit_events_action', 'staff_audit_events', ['module', 'action', sa.text('seq DESC')])
    op.create_index('ix_staff_audit_events_occurred_at', 'staff_audit_events', ['occurred_at'])  # date-range export

    # Append-only: UPDATE / DELETE (per row) and TRUNCATE (per statement) are rejected. This stops
    # application bugs and injected statements; the table owner can still disable a trigger, which is why
    # retention/erasure is a privileged, deliberate action rather than something the app can do.
    op.execute("""
        CREATE FUNCTION staff_audit_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION USING
                MESSAGE = 'staff_audit_events is append-only: ' || TG_OP || ' is not allowed',
                ERRCODE = 'insufficient_privilege';
        END
        $$
    """)
    op.execute("""
        CREATE TRIGGER trg_staff_audit_events_no_mutation
            BEFORE UPDATE OR DELETE ON staff_audit_events
            FOR EACH ROW EXECUTE FUNCTION staff_audit_events_append_only()
    """)
    op.execute("""
        CREATE TRIGGER trg_staff_audit_events_no_truncate
            BEFORE TRUNCATE ON staff_audit_events
            FOR EACH STATEMENT EXECUTE FUNCTION staff_audit_events_append_only()
    """)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")

    # Guard: audit history is never dropped silently.
    audit_rows = op.get_bind().execute(sa.text("SELECT count(*) FROM staff_audit_events")).scalar_one()
    if audit_rows:
        raise RuntimeError(
            f"Refusing to downgrade: staff_audit_events holds {audit_rows} row(s). "
            "Archive them and remove them deliberately (the table owner must disable the append-only "
            "trigger for that) before downgrading."
        )

    op.drop_table('staff_audit_events')  # takes its indexes and triggers with it
    op.execute("DROP FUNCTION staff_audit_events_append_only()")
    op.drop_constraint('ck_staff_user_roles_revoked_pair', 'staff_user_roles', type_='check')
