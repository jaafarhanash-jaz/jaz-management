# PostgreSQL Backup & Restore

This project uses a portable, no-sudo PostgreSQL binary under `postgres-portable/`
(the same local-binary pattern used for MinIO — no system service, no Docker).
Connection details live in `backend/.env` as `DATABASE_URL`:

```
postgresql+asyncpg://jaz:jazpassword@127.0.0.1:5432/jaz_db
```

The `postgres-portable/pgsql/bin/` directory ships `pg_dump`, `pg_restore`,
`pg_dumpall`, and `psql` — use these binaries (or any Postgres 14+ client
that matches the server version) rather than a system-installed `psql`,
since a version mismatch between client and server tools can fail silently
on some commands.

## Local backup (logical, `--format=custom`)

`--format=custom` is compressed and restorable selectively (single table,
schema-only, data-only) — preferred over plain-SQL dumps for anything past
throwaway dev use.

```bash
cd backend
PGPASSWORD=jazpassword ../postgres-portable/pgsql/bin/pg_dump \
  -h 127.0.0.1 -U jaz -d jaz_db \
  --format=custom \
  --file="backups/jaz_db_$(date +%Y%m%d_%H%M%S).dump"
```

Create the `backend/backups/` directory once (`mkdir -p backend/backups`);
it's gitignored — dumps are never committed.

## Restore

Restoring into a **fresh** database (recommended — avoids constraint
conflicts with any partial state):

```bash
# 1. Create a fresh target database
PGPASSWORD=jazpassword ../postgres-portable/pgsql/bin/psql \
  -h 127.0.0.1 -U jaz -d postgres \
  -c "CREATE DATABASE jaz_db_restored;"

# 2. Restore into it
PGPASSWORD=jazpassword ../postgres-portable/pgsql/bin/pg_restore \
  -h 127.0.0.1 -U jaz -d jaz_db_restored \
  --no-owner --no-privileges \
  backups/jaz_db_20260714_120000.dump

# 3. Point DATABASE_URL at the restored database (or rename it to jaz_db
#    after verifying, once the original is dropped/renamed aside)
```

`--no-owner --no-privileges` avoids `ROLE does not exist` errors when
restoring on a machine where the `jaz` role wasn't created with the exact
same name/permissions as the source.

To restore **in place** over the existing `jaz_db` (destructive — only do
this after confirming the dump is what you want):

```bash
PGPASSWORD=jazpassword ../postgres-portable/pgsql/bin/pg_restore \
  -h 127.0.0.1 -U jaz -d jaz_db \
  --clean --if-exists --no-owner --no-privileges \
  backups/jaz_db_20260714_120000.dump
```

`--clean --if-exists` drops each object before recreating it, so the dump
becomes the new source of truth for the schema as well as the data.

## What's NOT in the Postgres dump

File attachments (Work Messaging, Calendar, Task proof files) live in
**MinIO**, not Postgres — only their metadata rows (`storage_path`,
checksum, filename, etc.) are in the database. A full backup that
includes attachment bytes needs a separate MinIO backup:

```bash
# MinIO's data directory is a plain filesystem tree - back it up like any
# other directory (adjust the path to match your MinIO data dir).
tar -czf "backups/minio_data_$(date +%Y%m%d_%H%M%S).tar.gz" minio-portable/data/
```

Restoring a Postgres dump without restoring the matching MinIO snapshot
leaves attachment metadata rows pointing at objects that may no longer
exist — restore both together, from backups taken at the same time.

## Recommended local backup schedule (while iterating)

- **Daily**, before starting a work session, if you've been actively
  changing schema or running destructive test data.
- **Before every `alembic upgrade`** that isn't a simple additive
  migration (column type changes, drops, renames) — the exact class of
  migration this project has already run several of (e.g. `recipient_ids`
  UUID→String on both `messages` and `calendar_events`).
- **Before any manual `psql` session** that will run `UPDATE`/`DELETE`
  outside the application.

A simple cron-free habit is enough at this stage — there's no scheduler
running in this local dev environment (deliberately, per the project's
"self-heal on read, not cron" convention), so backups here are a manual
discipline, not an automated job.

## What a real production deployment would add

This project runs entirely on portable local binaries with no managed
infrastructure. A production deployment should not roll its own backup
scheduling — instead, use whatever a managed Postgres provider gives you:

- **Supabase**: automatic daily backups + point-in-time recovery (PITR)
  on paid tiers, configurable retention, one-click restore from the
  dashboard. Given this schema is already designed to be RLS/Supabase-
  ready (every tenant-scoped table carries `company_id`, UUID PKs avoid
  cross-shard collisions), Supabase is the most natural production target
  if/when this project moves off local binaries.
- **AWS RDS for PostgreSQL**: automated snapshots + PITR via WAL archiving,
  configurable backup window and retention period, cross-region snapshot
  copy for disaster recovery.
- **Any managed provider**: prefer PITR over daily-snapshot-only — it
  bounds data loss to seconds/minutes instead of "since the last nightly
  dump," which matters once real user data (not dev/test fixtures) is at
  stake.

For object storage (replacing local MinIO), a managed provider's own
backup/versioning (S3 versioning + lifecycle rules, or Supabase Storage's
equivalent) should be enabled — this is a config change only, since the
storage layer (`services/storage.py`) already speaks the S3 API and moving
off MinIO means changing the `S3_ENDPOINT_URL`/credentials, not the code.
