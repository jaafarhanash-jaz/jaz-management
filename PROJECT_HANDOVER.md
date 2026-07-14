# JAZ Platform — Project Handover

Document created at the end of a single extended development session. It reflects the codebase as of this point: a working MVP that was substantially extended with subscription management, presence monitoring, and a full task management system.

> **Superseded architecture note:** everything below predates a later session in which the local MongoDB instance suffered unrecoverable corruption and the backend was migrated in full to PostgreSQL (SQLAlchemy 2.x async + Alembic), with `backend/server.py` split into a layered `repositories/` + `services/` architecture, plus three feature modules (Work Messaging, Calendar, and this migration itself) that did not exist when this document was written. §1, §2, §3, §8, and §9 below describe the **pre-migration MongoDB state** and are kept for historical reference only — do not use them to understand the current database engine, schema, or file layout. See `backend/docs/BACKUP_RESTORE.md` for the current Postgres backup/restore procedure. Everything else in this document (feature history, coding conventions unrelated to the DB engine, frontend structure) remains accurate.

---

## 1. Overview

**JAZ Platform** is an Arabic-first (RTL/LTR) HR & workforce-management SaaS. Three roles: **Super Admin** (manages companies & subscription plans, platform-wide analytics), **Company Owner** (manages employees, tasks, attendance, departments, billing), **Employee** (works tasks, clocks in/out via QR+GPS, views performance).

**Stack (as of this document's writing — see the superseded-architecture note above for what changed since)**
- Backend: FastAPI + Motor (async MongoDB), single monolithic file `backend/server.py` (2,355 lines). JWT + bcrypt auth.
- Frontend: React 19 (CRA via craco), Shadcn/Radix UI, Tailwind (RTL via logical properties), React Router 7, Recharts for charts, `html5-qrcode` for attendance scanning, `sonner` for toasts.
- Database: MongoDB, 10 collections (see §3). **Now PostgreSQL** — see the note above.
- Payments: Stripe via `emergentintegrations` (a private Emergent-platform package — not on public PyPI; a local stand-in implementation exists in the dev venv's site-packages so the app can boot, see §9).

---

## 2. Current Architecture

- **No microservices, no message queue, no cache layer.** At the time of writing, every request hit FastAPI → Motor → MongoDB directly; the request path is now FastAPI → SQLAlchemy async session → PostgreSQL, routed through `repositories/` + `services/` (see the superseded-architecture note above).
- **Self-heal on read** is the dominant architectural pattern for anything time-based, used in place of a scheduler/cron (there is none, deliberately):
  - Subscription expiry: `resolve_subscription_status()` flips `active` → `expired` the moment any endpoint reads a company whose `subscription_end_date` has passed.
  - Presence/online status: `get_company_presence()` computes online/offline from `last_seen_at` timestamps against a 60-second timeout, computed fresh on every read — nothing is pre-computed or cached.
  - Daily task instances: `generate_daily_task_instances()` lazily creates today's occurrence of each active daily-task template the first time an employee's task list is read that day.
- **Access control gate**: `enforce_company_access()` runs inside `get_current_user` (used by every authenticated route) and inside `login()`. If the caller's company is `expired` or `suspended`, it raises a structured 403 and the frontend's global axios interceptor (`utils/api.js`) logs the user out and redirects to a dedicated status page. Super Admins have no `company_id` and are structurally exempt.
- **Fan-out, not schema-breaking, for multi-assignee actions**: the original `tasks` collection is single-assignee by design (`assigned_to: str`). Rather than changing that type (which would ripple through the whole legacy system), assigning an Urgent Task or a Daily Task to N employees creates N independent rows (sharing a `batch_id` or `daily_task_id` to group them logically).
- **Additive-only schema evolution**: virtually every new field added this session is `Optional[... ] = None` on the relevant Pydantic model, so old documents that don't have the field simply deserialize with `None` — no migration was ever required.

---

## 3. Database Schema (MongoDB, 10 collections — historical, pre-migration)

The schema below reflects MongoDB collections that no longer exist. The current PostgreSQL schema has native UUID primary keys, soft-delete on companies/users/tasks/departments, a unified `audit_logs` table, MinIO-backed object storage for attachments, and additional tables for Work Messaging, Calendar, and Subscriptions/Stripe that were built after this document was written. See `backend/models.py` for the authoritative current schema.

### `users`
| Field | Notes |
|---|---|
| id, email, phone, password (bcrypt), name, role | `super_admin` \| `company_owner` \| `employee` |
| company_id | absent for super_admin |
| department, position, status, avatar, created_at | |
| **last_seen_at** | *(new)* ISO timestamp, updated by `POST /heartbeat`, drives online/offline presence |

### `companies`
| Field | Notes |
|---|---|
| id, name, owner_id, qr_code, address, created_at, employee_count | |
| subscription_status | `active` \| `expired` \| `suspended` |
| subscription_plan_id | **required** — every company must reference a plan |
| subscription_start_date, subscription_end_date | set by Activate/Renew |
| subscription_price, max_employees, subscription_duration_months, subscription_features | all **derived from the linked plan** at assign/change/renew time via `plan_config_for_company()` — never hand-typed |

### `subscription_plans`
id, name, max_employees (>0), price (≥0), duration_months (>0), features[], is_active. Validated via Pydantic `Field` constraints.

### `tasks` (the core, heavily extended collection)
| Field | Notes |
|---|---|
| id, company_id, assigned_to (single employee id), title, description, priority, due_date, requires_proof, proof_files[], created_by, created_at, completed_at | original fields, **unchanged** |
| status | `new`(=Pending) → `seen` → `in_progress` → `completed`, or `cancelled` (soft-terminal). Legacy-only values `pending_review`/`rejected`/`overdue` still valid but unused by the new workflow |
| task_category | `None` (legacy) \| `daily` \| `urgent` |
| daily_task_id, occurrence_date | only for `task_category="daily"` — links back to its template and the calendar day it represents |
| execution_date, execution_time | urgent tasks only, **both optional** — informational metadata, no scheduling engine reads them |
| due_time | urgent tasks only, optional companion to the pre-existing `due_date` |
| started_at, seen_at, completed_by, batch_id | task history/timeline fields |

### `daily_tasks` (new collection — recurring task **templates**, not instances)
id, company_id, title, description, assigned_to (**list** of employee ids — the one place multi-assignee is a real list, since this is a template, not a per-person row), execution_time (optional), requires_proof, is_active, recurrence_type (only `"daily"` implemented; `weekly`/`monthly`/`selected_days` reserved in the enum for future work), recurrence_config (empty dict today, reserved), created_by, created_at.

### `attendance`, `reports`, `departments`, `notifications`, `payment_transactions`
Unchanged from the original MVP. `notifications.type` gained one new value this session: `"urgent_task"` (alongside the pre-existing `"task_assigned"`).

**No indexes exist on any collection.** This was flagged as tech debt in the initial audit and was never addressed — every query is a full collection scan. Fine at current data volume, a real cost at scale (see TODO list).

---

## 4. APIs Implemented

All routes are prefixed `/api`. Auth: `Authorization: Bearer <JWT>`, obtained from `/auth/login`.

### Auth & Presence
- `POST /auth/login`, `GET /auth/me`
- `POST /heartbeat` — any authenticated owner/employee; stamps `last_seen_at`

### Super Admin
- `GET /admin/statistics` — companies, employees, revenue, subscription counts, **online/offline counts**
- `GET/POST/PUT/DELETE /admin/companies`, plus `.../activate`, `.../suspend`, `.../reactivate`, `.../renew`
- `GET/POST/PUT/DELETE /admin/subscription-plans`

### Company Owner
- `GET /owner/dashboard` — attendance/task summary + `active_daily_tasks`, `pending_urgent_tasks`, `completed_urgent_tasks`
- `GET /owner/analytics` — **new**: full performance analytics (see §6)
- `GET/POST/PUT/DELETE /owner/employees`
- `GET/POST/PUT/DELETE /owner/tasks`, plus `POST /owner/tasks/{id}/cancel`
- `GET/POST/PUT/DELETE /owner/daily-tasks`, plus `POST /owner/daily-tasks/{id}/toggle`
- `POST /owner/urgent-tasks` — multi-employee fan-out
- `GET /owner/attendance`, `GET /owner/reports`, `GET/POST /owner/departments`
- `GET /owner/subscription-plans` (active-only, reused by Super Admin's Companies page too), `GET /owner/subscription`
- `POST /payments/checkout`, `GET /payments/status/{session_id}`

### Employee
- `GET /employee/dashboard`
- `GET /employee/tasks` (self-heals daily instances + seen-marking on every call), `PUT /employee/tasks/{id}/status` (legacy, untouched), `POST /employee/tasks/{id}/proof`
- `POST /employee/tasks/{id}/start`, `POST /employee/tasks/{id}/complete` — **new**, dedicated workflow actions
- `POST /employee/attendance/check-in`, `POST /employee/attendance/check-out`, `GET /employee/attendance/history`
- `GET /employee/performance`, `POST /employee/reports`, `PUT /employee/profile`

### Common
- `GET /notifications`, `PUT /notifications/{id}/read`, `GET /company/{id}/qr`, `POST /webhook/stripe`, `POST /seed`

---

## 5. Frontend Structure

```
frontend/src/
  App.js                        — routes, auth state, heartbeat interval, global route guard
  utils/api.js                  — axios instance + global 403 interceptor (subscription-block auto-logout)
  utils/translations.js
  components/
    Layout.js                   — sidebar/header shell, role-driven nav
    CreateUrgentTaskDialog.js   — NEW, shared by Owner Dashboard + Owner Tasks page
    ui/*                        — shadcn/radix primitives (unmodified this session)
  pages/
    LoginPage.js                 — redirects to /subscription-blocked on the structured 403
    SubscriptionBlocked.js       — NEW, full-screen Expired/Suspended status page
    SuperAdmin/
      Dashboard.js, Companies.js, Plans.js   — subscription + presence management
    Owner/
      Dashboard.js               — Emergency Task button, task stats, full Analytics section w/ 4 charts
      Tasks.js                   — legacy task list + Daily Tasks panel + timeline + cancel-not-delete
      Employees.js, Attendance.js, Reports.js, Departments.js, Subscription.js  — unmodified this session
    Employee/
      Tasks.js                   — Urgent/Daily/Other sections, Start/Complete confirmations, sound+popup
      Attendance.js, Dashboard.js, Performance.js, Reports.js  — unmodified this session
```

Polling intervals in use (no WebSockets anywhere): Owner Dashboard, Owner Tasks, SuperAdmin Dashboard, SuperAdmin Companies all silently re-fetch every 10s; heartbeat fires every 20s from any logged-in owner/employee tab.

---

## 6. Every Completed Feature (this session, chronological)

1. **Subscription price on companies** — manual price field on create/edit (later superseded by plan-driven pricing, see #4).
2. **Owner Account management** — Super Admin can edit a company's owner name/email/phone/password from the Edit Company dialog, with duplicate-email/phone validation and password confirmation.
3. **Subscription lifecycle** — Activate (manual start/end dates)/Suspend/Reactivate actions; self-heal expiration; access-control gate blocking login and every API call for expired/suspended companies; dashboard stats (active/expired/expiring-soon) + two dashboard tables; 4-color status badges on the Companies table.
4. **Subscription Plans as source of truth** — full Plan CRUD (create/edit/delete with a "can't delete if in use" guard); Company create/edit now requires selecting a Plan via a searchable combobox (built from `cmdk`+Popover, no manual price entry); `max_employees` derived from the plan and enforced when creating employees; Renew action (current or a different plan) with correct date-extension math (extends from the later of now/current end date, not a hard reset).
5. **Plan-config inheritance** — a generic helper copies *every* plan field (not just price/max_employees) onto the company whenever a plan is assigned/changed/renewed, so future plan fields propagate automatically.
6. **Professional subscription-blocked experience** — global axios interceptor auto-logs-out and redirects any blocked request to a dedicated full-screen page (separate Expired/Suspended copy, WhatsApp/email placeholders, "Return to Login"); repeated login attempts while blocked always redirect back.
7. **Real-time-ish presence monitoring** — lightweight heartbeat (not just "has a valid JWT"), 60s offline timeout, Owner/Employee online status, company online = owner OR any employee online; Super Admin dashboard gained Online/Offline company counts; Companies table gained Company/Owner status + "N/M employees online" columns. All via short-interval polling, no WebSockets.
8. **Smart Task Management** (the largest feature):
   - Daily Recurring Tasks: templates (multi-employee), auto-generated daily instances, enable/disable without deleting, schema forward-compatible with weekly/monthly/selected-days recurrence (not implemented).
   - Urgent Tasks: multi-employee fan-out, in-app notification + toast popup + synthesized Web-Audio beep on arrival (detected via polling), immediate delivery regardless of whether Execution Date/Time are set.
   - Unified 5-state workflow: Pending → Seen → In Progress → Completed, plus soft Cancelled (history never deleted for in-progress/completed/cancelled tasks — only a not-yet-started task can be hard-deleted).
   - Full timeline (Assigned/Seen/Started/Completed, who completed it) visible to the owner.
   - Server-side photo-proof enforcement on the new Complete action (closed a pre-existing client-only-validation gap).
   - Owner Analytics dashboard: total/completed/pending/urgent task counts, avg completion time, avg response time, today/week/month completed counts, attendance rate, late count, a weighted employee performance score (0–100, mapped to a 4-tier star rating), employee ranking, rule-based insights (no AI), and 4 Recharts charts (completed-over-time, task-distribution, performance-ranking, attendance-trend).
9. **Urgent Task date/time made optional** — Execution Date/Time and Due Date/Time are now independently optional via two toggle checkboxes ("Schedule Execution" / "Set Due Date"); leaving them off means Immediate/no-deadline; the task is always created and delivered immediately regardless — no scheduling engine exists or was implied.

---

## 7. Pending / Not Implemented Features

- **Recurrence types beyond Daily** — schema (`recurrence_type`, `recurrence_config`) is ready; weekly/monthly/selected-days generation logic is not written.
- **True scheduled delivery for urgent tasks** — Execution Date/Time are informational only; nothing holds back notification/visibility until that time arrives. Explicitly deferred to "a separate feature" per your instruction.
- **WebSocket/push-based real-time** — everything "real-time" in this session is short-interval polling (10s dashboards, 20s heartbeat). Explicitly chosen over WebSockets to avoid new infrastructure.
- ~~**MongoDB indexes** — none exist on any collection; flagged repeatedly, never implemented.~~ Moot after the Postgres migration — indexes/constraints now exist per `backend/models.py` and the Alembic migration history.
- **Owner-facing Stripe self-service subscription payment** — still exists (`Owner/Subscription.js`, `POST /payments/checkout`) but its relationship to the newer Plan/Renew system was never fully reconciled (see TODO #2).
- **`Owner/Employees.js`, `Attendance.js`, `Reports.js`, `Departments.js`** — untouched all session; still on the original MVP implementation.
- **No automated test suite updates** — `backend/tests/backend_test.py` (38 tests, pre-existing) was never re-run or extended for any feature built this session.
- **Rate limiting on login** — still absent (flagged in the original security audit).
- **Email/SMS notifications** — still in-app/toast only, as in the original MVP.

---

## 8. Coding Conventions (as established/reinforced this session)

- ~~**Backend is one file** (`backend/server.py`) by original design; this session added ~950 net lines to it without splitting it into routers.~~ No longer accurate — `server.py` is now a thin route layer delegating to `repositories/` + `services/` (see the superseded-architecture note at the top of this document). Still follow the existing section-comment style (`# ============ ... ============`) for route grouping within `server.py`.
- **Enum-like constants are plain classes**, not Python's `enum.Enum` (e.g. `TaskStatus`, `TaskCategory`). Match this in `server.py`'s Pydantic-model-support constants — but note the services layer (`services/*.py`) instead uses plain string literals directly (`"completed"`, `"active"`, etc.), matching the pattern established across the Postgres migration; several of the constant classes referenced here (`TaskCategory`, `TaskStatus`, `SubscriptionStatus`) have since been removed as unreferenced dead code.
- **Pydantic models**: new optional fields always `Optional[X] = None`, never a bare required type, unless the field is genuinely new *and* required for a brand-new entity (e.g. `DailyTaskCreate.assigned_to`).
- **Field whitelisting on generic PUT endpoints**: never trust a raw request dict for a mutable field that has business-rule implications (price, status, dates) — either whitelist explicitly or route through a dedicated action endpoint. This session repeatedly chose **dedicated action endpoints** (`/activate`, `/suspend`, `/start`, `/complete`, `/cancel`) over generic field setters whenever the field carries workflow meaning.
- **Self-heal on read, not cron** — the house style for anything time-based. Do not introduce APScheduler/Celery/cron without a very deliberate, separately-approved decision.
- **Frontend polling pattern**: `fetch(silent = false)` — `silent` calls skip the loading spinner and error toast, used for background polling so it never visually disrupts an open dialog or flashes a loading state.
- **Currency/date formatting**: small local helper functions per file (`formatCurrency`, `formatLastSeen`, etc.), not a shared utils module — consistent with how the codebase already handled `formatDate`/`formatTime` before this session.
- **Confirmations**: `window.confirm()` for simple yes/no (matches original MVP), a proper `Dialog` component only when the confirmation needs to show contextual content (e.g. Start/Complete task confirmations).
- **RTL-first**: Arabic strings are the primary UI language throughout; English is a secondary toggle. New UI copy should default to Arabic first, matching existing files.
- **No comments explaining *what* code does** — this codebase (and this session's additions) only comment on non-obvious *why* (e.g. why self-heal instead of cron, why a field is excluded from a whitelist).

---

## 9. Current Project Status

- ~~**Local dev environment is running**: MongoDB (portable binary, not a system service) on `127.0.0.1:27017`~~ — MongoDB was later abandoned after unrecoverable data corruption. The local dev environment now runs PostgreSQL and MinIO as portable binaries (same no-sudo pattern), backend on `http://localhost:8000` (uvicorn), frontend on `http://localhost:3001` (craco).
- **`backend/.env` and `frontend/.env`** are untracked (gitignored, never committed) — a fresh clone needs these recreated. Backend now needs `DATABASE_URL` (Postgres), `JWT_SECRET_KEY`, `CORS_ORIGINS`, and the `S3_*` MinIO variables (see `backend/.env` for the current full list) — `MONGO_URL`/`DB_NAME` are no longer used. Frontend needs `REACT_APP_BACKEND_URL`.
- **`emergentintegrations` (Stripe wrapper) is not on public PyPI.** A local stand-in implementation was written directly into the venv's `site-packages` (not part of the repo) so the app can boot outside the Emergent platform. It implements the real Stripe SDK underneath, so it will actually work if a real `STRIPE_API_KEY` is supplied — but it's not the vendor's original code.
- **Seed data**: `POST /api/seed` (unauthenticated — a pre-existing, never-fixed security gap) creates `admin@jaz.com`/`admin123`, `owner@demo.com`/`owner123`, `employee1@demo.com`/`emp123`, `employee2@demo.com`/`emp123`.
- **Live demo data exists** in the local database from feature verification: 2 companies (both active, both on real subscription plans), 3 subscription plans, a handful of daily-task templates and task history entries. This was left in place deliberately as it demonstrates the shipped features.
- **No code has been committed to git this session** — everything above is uncommitted working-tree changes (`git status` shows the full list). Nothing has been pushed anywhere.
- **All automated verification this session was done via direct API calls (curl)**, not a live browser click-through — this project's browser preview tooling isn't available in this environment. Frontend correctness was verified via clean webpack compiles (no new errors/warnings across every edit) plus careful tracing of the API response shapes each component consumes, not visual confirmation.

---

## 10. TODO List (Priority Order)

**High priority**
1. ~~**Add MongoDB indexes** on the fields now hit on every authenticated request...~~ Moot after the Postgres migration.
2. **Reconcile the owner-facing Stripe checkout flow with the Plan/Renew system.** Two ways to activate/extend a subscription now coexist (`POST /payments/checkout` success path vs. Super Admin's `/renew`) and were never explicitly unified — decide whether owner self-service payment should remain, and if so make sure its date math matches the corrected renewal formula.
3. **Secure or remove `POST /api/seed`** — unauthenticated, creates a super-admin account with hardcoded credentials. Flagged in the very first audit of this codebase and never addressed.
4. **Get real browser verification** of everything built this session (especially sound/popup on urgent-task arrival, chart rendering, and the toggle-checkbox UI) — all verification so far is API-level; nothing has been visually confirmed in an actual browser.

**Medium priority**
5. Decide on and implement true scheduling for Execution Date/Time (currently informational-only by design) if the product actually needs delayed delivery.
6. Extend `daily_tasks.recurrence_type` handling to actually support weekly/monthly/selected-days (schema is ready, generation logic is Daily-only).
7. Re-run and extend the backend test suite (`backend_test.py`) to cover everything built this session — it currently only covers the original MVP.
8. Add rate limiting to `/auth/login`.
9. Review whether `Owner/Employees.js`, `Attendance.js`, `Reports.js`, `Departments.js` need any updates now that Companies have `max_employees` enforcement and Daily/Urgent tasks exist alongside legacy tasks.

**Low priority**
10. Consider WebSocket-based push if polling intervals (10s dashboard, 20s heartbeat) prove too slow or too chatty at real scale — deliberately deferred this session in favor of simplicity.
11. ~~Split `backend/server.py` into routers~~ — the Postgres migration split all business logic and data access into `repositories/`+`services/`; `server.py` is now a thin route-declaration layer. Splitting the route declarations themselves into per-domain FastAPI routers is still open if desired.
12. Trim `backend/requirements.txt` — it still contains a large number of unused ML/data-science packages unrelated to this app (pandas, numpy, boto3, openai, google-generativeai, etc.), noted in the original audit, never cleaned up.
13. Email/SMS notification channel (currently in-app/toast only).
