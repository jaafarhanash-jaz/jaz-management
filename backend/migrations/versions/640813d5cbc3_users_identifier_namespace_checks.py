"""users: keep the email and phone value spaces disjoint (CHECKs, created NOT VALID)

POST /auth/login takes one free-text identifier and resolves it as an email or a phone. That is
unambiguous only while no string can be both somebody's email and somebody's phone. This revision adds the
database half of that guarantee (the application half is services/identifiers.py):

  ck_users_email_has_at   CHECK (position('@' in email) > 0)
  ck_users_phone_no_at    CHECK (position('@' in phone) = 0)

users.email and users.phone are both NOT NULL, so no `IS NULL OR` branch is needed.

NOT VALID - deliberately, and this revision NEVER validates
  * Both constraints are created NOT VALID: they are enforced for every INSERT and UPDATE from the moment
    they exist, but the existing rows are not scanned. So this migration cannot fail on production data.
  * VALIDATE CONSTRAINT is a separate, later, manual step, run only after the production read-only
    detection queries (below) return 0:
        ALTER TABLE users VALIDATE CONSTRAINT ck_users_email_has_at;
        ALTER TABLE users VALIDATE CONSTRAINT ck_users_phone_no_at;
    (SHARE UPDATE EXCLUSIVE lock: it does not block reads or writes.)

READ THIS BEFORE DEPLOYING
  A NOT VALID CHECK still applies to every UPDATE of a row - including an UPDATE that touches neither
  column (e.g. the heartbeat's last_seen_at write). A row that already violates a constraint can therefore
  not be updated at all until it is corrected. Run these on the target database first; deploy only when
  every count is 0:
        SELECT count(*) FROM users WHERE position('@' in phone) > 0;   -- phones that look like emails
        SELECT count(*) FROM users WHERE position('@' in email) = 0;   -- emails that are not
  (soft-deleted rows count: a table CHECK applies to every row.)

Downgrade only drops the two constraints; no data is touched.

Revision ID: 640813d5cbc3
Revises: bb595f6d8dae
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = '640813d5cbc3'
down_revision: Union[str, Sequence[str], None] = 'bb595f6d8dae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    # Raw SQL on purpose: NOT VALID is the whole point and must be visible in review.
    op.execute("ALTER TABLE users ADD CONSTRAINT ck_users_email_has_at CHECK (position('@' in email) > 0) NOT VALID")
    op.execute("ALTER TABLE users ADD CONSTRAINT ck_users_phone_no_at CHECK (position('@' in phone) = 0) NOT VALID")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_phone_no_at")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_email_has_at")
