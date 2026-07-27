-- =============================================================================
-- Quarantine the pre-existing legacy Supabase schema out of `public`
-- =============================================================================
--
-- WHY: This Supabase project already contained a complete, independent schema
-- before this codebase's Alembic migrations ever ran against it (11
-- Supabase-native migrations, 2026-07-05 -> 2026-07-15, tracked in
-- supabase_migrations.schema_migrations - a separate, earlier
-- Supabase-Auth-coupled build of this product). `alembic_version` does not
-- exist because Alembic has never run here at all. Six legacy table names
-- collide with this app's schema (companies, users, attendance, reports,
-- tasks, notifications) but have incompatible columns - e.g. the legacy
-- `users` has no email/password and FKs to auth.users, which this app's own
-- login flow could never have written or read.
--
-- Verified before writing this script (see chat for full detail):
--   - All 20 FK constraints on these 18 tables are internal to the set,
--     except users_id_fkey -> auth.users.id (cross-schema, unaffected by a
--     schema move).
--   - 30 indexes, all plain btree, all local to their own table.
--   - 0 sequences (every PK is gen_random_uuid()).
--   - All CHECK/UNIQUE constraints are self-contained, no external refs.
--   - The 3 enum types below are each used by exactly one column in this set.
--   - 0 triggers, 0 views, 0 publications reference any of these tables.
--   - 2 SECURITY DEFINER functions (current_user_role, current_user_company_id)
--     hardcode `public.users` and back the 29 RLS policies on these tables -
--     moved and repointed below so they don't break.
--
-- EFFECT: `public` ends up completely empty of tables, so `alembic upgrade
-- head` runs its full 22-migration chain cleanly, exactly as it would
-- against a fresh database. No row is dropped or altered - this only moves
-- objects to a new namespace. Fully reversible (see bottom of file).
--
-- HOW TO RUN: see chat instructions. Runs as a single transaction - if
-- anything fails, everything rolls back and `public` is left untouched.
-- =============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS legacy_pre_alembic;

-- Table-level grants (e.g. GRANT SELECT ON companies TO authenticated) live
-- on the table itself (pg_class.relacl) and move automatically with SET
-- SCHEMA below. Schema-level USAGE does NOT travel with the tables - it's a
-- separate grant on the namespace object itself. Verified public's actual
-- ACL before writing this: anon/authenticated/service_role/postgres all
-- hold USAGE on public today. Mirrored here so access parity is preserved
-- if this schema is ever exposed the same way public is.
GRANT USAGE ON SCHEMA legacy_pre_alembic TO postgres, anon, authenticated, service_role;

-- --- Tables -------------------------------------------------------------
ALTER TABLE public.admin_notifications    SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.announcement_receipts  SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.announcements          SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.attendance             SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.companies              SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.employees              SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.leave_requests         SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.locations              SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.notifications          SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.plans                  SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.reports                SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.subscription_history   SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.subscriptions          SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.task_events            SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.task_proofs            SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.task_templates         SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.tasks                  SET SCHEMA legacy_pre_alembic;
ALTER TABLE public.users                  SET SCHEMA legacy_pre_alembic;

-- --- Enum types -----------------------------------------------------------
ALTER TYPE public.report_status       SET SCHEMA legacy_pre_alembic;
ALTER TYPE public.notification_type   SET SCHEMA legacy_pre_alembic;
ALTER TYPE public.announcement_target SET SCHEMA legacy_pre_alembic;

-- --- RLS helper functions (moved + repointed to the new table location) ---
ALTER FUNCTION public.current_user_role()       SET SCHEMA legacy_pre_alembic;
ALTER FUNCTION public.current_user_company_id() SET SCHEMA legacy_pre_alembic;

CREATE OR REPLACE FUNCTION legacy_pre_alembic.current_user_role()
 RETURNS text
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
  select role from legacy_pre_alembic.users where id = auth.uid();
$function$;

CREATE OR REPLACE FUNCTION legacy_pre_alembic.current_user_company_id()
 RETURNS uuid
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
  select company_id from legacy_pre_alembic.users where id = auth.uid();
$function$;

COMMIT;

-- =============================================================================
-- POST-CHECK (run after commit, not part of the transaction):
--
--   SELECT count(*) FROM information_schema.tables WHERE table_schema='public';
--   -- expect 0
--
--   SELECT count(*) FROM information_schema.tables WHERE table_schema='legacy_pre_alembic';
--   -- expect 18
-- =============================================================================


-- =============================================================================
-- ROLLBACK - run this instead, in full, ONLY to undo the migration above.
-- Do not run both blocks in the same session.
-- =============================================================================
--
-- BEGIN;
--
-- -- Note: this rollback does NOT undo any alembic migrations that ran in the
-- -- meantime. If you've already run `alembic upgrade head` against a clean
-- -- `public` before rolling this back, you must `alembic downgrade base` (or
-- -- drop the app's tables manually) first, or the ALTER TABLE ... SET SCHEMA
-- -- public statements below will hit the same DuplicateTableError this
-- -- migration was written to fix.
--
-- -- Tables and types moved back first, so the functions recreated further
-- -- down can reference public.users while it actually exists there again -
-- -- avoids relying on Postgres deferring body validation for LANGUAGE sql
-- -- functions until first call.
-- ALTER TABLE legacy_pre_alembic.admin_notifications    SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.announcement_receipts  SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.announcements          SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.attendance             SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.companies              SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.employees              SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.leave_requests         SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.locations              SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.notifications          SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.plans                  SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.reports                SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.subscription_history   SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.subscriptions          SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.task_events            SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.task_proofs            SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.task_templates         SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.tasks                  SET SCHEMA public;
-- ALTER TABLE legacy_pre_alembic.users                  SET SCHEMA public;
--
-- ALTER TYPE legacy_pre_alembic.report_status       SET SCHEMA public;
-- ALTER TYPE legacy_pre_alembic.notification_type   SET SCHEMA public;
-- ALTER TYPE legacy_pre_alembic.announcement_target SET SCHEMA public;
--
-- ALTER FUNCTION legacy_pre_alembic.current_user_role()       SET SCHEMA public;
-- ALTER FUNCTION legacy_pre_alembic.current_user_company_id() SET SCHEMA public;
--
-- CREATE OR REPLACE FUNCTION public.current_user_role()
--  RETURNS text
--  LANGUAGE sql
--  STABLE SECURITY DEFINER
--  SET search_path TO ''
-- AS $function$
--   select role from public.users where id = auth.uid();
-- $function$;
--
-- CREATE OR REPLACE FUNCTION public.current_user_company_id()
--  RETURNS uuid
--  LANGUAGE sql
--  STABLE SECURITY DEFINER
--  SET search_path TO ''
-- AS $function$
--   select company_id from public.users where id = auth.uid();
-- $function$;
--
-- -- Schema is empty again at this point - full inverse of CREATE SCHEMA IF
-- -- NOT EXISTS above. No CASCADE: if anything unexpectedly still lives here,
-- -- this fails safely instead of dropping it.
-- DROP SCHEMA IF EXISTS legacy_pre_alembic;
--
-- COMMIT;
-- =============================================================================
