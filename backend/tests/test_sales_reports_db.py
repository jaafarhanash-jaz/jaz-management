"""JAZ Sales - Phase 5: performance and database guarantees (direct-DB tier, scratch database only).

The service functions are run IN-PROCESS against the scratch database with a SQL listener attached, so the tests see exactly
what the database is asked and what it answers:

  * NO N+1: the number of statements a dashboard / report needs is the same for a window with a handful of leads as for a period
    with thousands (and for the whole year, with every employee and every kind of work), and no statement is ever repeated (a per-row query is the same text again and again);
  * AGGREGATION IN SQL: every statement is an aggregate (COUNT / SUM ... GROUP BY) or one of the two paginated list pages, and
    none returns anything like one row per lead - checked with the row count the database reports for each statement;
  * INDEXES: the statements that filter on a period are EXPLAINed with sequential scans switched off, and none needs one -
    including the four indexes migration b6d1f3a8c294 added;
  * the migration's state on this database (head, catalog rows, grants, the four indexes).
"""
import asyncio
import re
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import event

from sales_reports_test_utils import build_world, make_personas, wind_down
from sales_test_utils import ALL_PERMISSION_KEYS, SYSTEM_ROLE_PERMISSIONS, admin_token, assert_scratch_target, auth  # noqa: F401

# A CLOSED period in the past, big enough to mean something: January - August 2026. Nothing writes into it while the suite runs
# (tests create data "now" or backdate it to 2001-2015), so two reads of it - the service's and an independent COUNT - can be
# compared exactly even though other workers hammer the same database. `ensure_volume` puts the rows there.
BIG = ("2026-01-01", "2026-08-31")
YEAR_START, YEAR_END = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc)
VOLUME_DATE = datetime(2026, 3, 15, 12, tzinfo=timezone.utc)
# The whole year, "now" included: every employee, every kind of work, everything the accumulated scratch data holds. Only used
# where the number of STATEMENTS is compared - that does not depend on what the (moving) data says.
WIDE = ("2026-01-01", "2026-12-31")


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module", autouse=True)
def volume():
    """Every test of this module runs against a closed period (BIG) holding at least VOLUME leads."""
    ensure_volume()
    assert big_lead_count() >= VOLUME


@pytest.fixture(scope="module")
def world(admin_h):
    p = make_personas(admin_h, "rdb")
    made = build_world(admin_h, p["manager"], p["employee"], p["employee2"])
    yield made
    wind_down(p["manager"]["headers"], made.open_items)                 # leave no open work in the shared scratch database


# ---- running a service in-process and watching the SQL ---------------------------------------------------------------------
def run_watched(fn):
    """Run `async def fn(db)` against the scratch DB. Returns (result, statements) - every statement the database executed:
    {"sql", "params", "rows"} with the number of rows it answered."""
    from database import SessionLocal, engine

    captured = []

    def after(conn, cursor, statement, parameters, context, executemany):
        captured.append({"sql": statement, "params": parameters, "rows": cursor.rowcount})

    async def _go():
        event.listen(engine.sync_engine, "after_cursor_execute", after)
        try:
            async with SessionLocal() as db:
                return await fn(db)
        finally:
            event.remove(engine.sync_engine, "after_cursor_execute", after)
            await engine.dispose()

    result = asyncio.run(_go())
    return result, captured


def staff_ctx(permissions, *, super_admin=False):
    from sales.services.access import StaffContext

    uid = uuid.uuid4()
    user = {"id": str(uid), "name": "in-process", "email": "in-process@example.com", "phone": None, "role": "super_admin" if super_admin else "jaz_staff", "status": "active"}
    return StaffContext(user, uid, super_admin, tuple(), frozenset(permissions))


def team_ctx():
    from sales.permissions import ALL_PERMISSIONS
    return staff_ctx(ALL_PERMISSIONS, super_admin=True)


def employee_ctx():
    return staff_ctx(SYSTEM_ROLE_PERMISSIONS["sales_employee"])


def scope_for(ctx, date_from, date_to, employee_id=None):
    from datetime import date
    from sales.metrics import resolve_period
    from sales.services.report_scope import build_scope

    return build_scope(ctx, resolve_period(None, date.fromisoformat(date_from), date.fromisoformat(date_to), date.today()), employee_id)


def calls(ctx, dates):
    """Every dashboard / report as (name, async fn(db)) for one caller and one period - fixed, typical arguments."""
    from sales.services import dashboard as dashboard_service
    from sales.services import reports as r

    from sales.services.report_scope import has_onboarding_scope

    scope = scope_for(ctx, *dates)
    out = {
        "dashboard": lambda db: dashboard_service.dashboard(db, scope),
        "leads": lambda db: r.lead_report(db, scope, source=None, campaign_id=None, priority=None, stages=None, sort="created_at", descending=True, limit=25, offset=0),
        "pipeline": lambda db: r.pipeline_report(db, scope, basis="created", source=None, campaign_id=None),
        "sources": lambda db: r.source_report(db, scope, campaign_id=None),
        "campaigns": lambda db: r.campaign_report(db, scope, source=None, campaign_id=None, campaign_status=None, limit=25, offset=0),
        "conversion": lambda db: r.conversion_report(db, scope, source=None, campaign_id=None),
        "activities": lambda db: r.activity_report(db, scope, limit=50, offset=0),
    }
    if has_onboarding_scope(ctx):
        out["onboarding"] = lambda db: r.onboarding_report(db, scope, stages=None, assigned_to=None, sort="started_at", descending=True, limit=25, offset=0)
    if scope.team:
        out["employee-performance"] = lambda db: r.employee_performance_report(db, scope, source=None, campaign_id=None, limit=50, offset=0)
    return out


# the most statements each needs (see the module docstrings of services/dashboard.py and services/reports.py)
BUDGET = {
    "dashboard": 17, "leads": 3, "pipeline": 1, "sources": 1, "campaigns": 3, "conversion": 5, "activities": 5,
    "onboarding": 5, "employee-performance": 7,
}


VOLUME = 1500          # leads in the closed period the volume checks need (topped up once; the scratch DB keeps them)


def ensure_volume(minimum: int = VOLUME) -> int:
    """Top the closed period (BIG) up to `minimum` non-archived leads with ONE set-based INSERT (scratch only), so the
    performance checks never depend on how many leads earlier runs happened to leave behind - and never skip on a fresh
    database. Idempotent: it inserts only the shortfall, so the scratch database does not keep growing. The rows are plain
    unassigned `new` leads dated inside the period; they satisfy every table constraint and no test asserts on the whole table."""
    from sqlalchemy import text
    from sales_reports_test_utils import run_db

    async def _go(db):
        have = (await db.execute(text("select count(*) from sales_leads where archived_at is null and created_at >= :lo and created_at < :hi"), {"lo": YEAR_START, "hi": YEAR_END})).scalar_one()
        missing = max(0, minimum - have)
        if missing:
            admin = (await db.execute(text("select id from users where role = 'super_admin' and deleted_at is null order by created_at limit 1"))).scalar_one()
            await db.execute(
                text(
                    "insert into sales_leads (id, business_name, name_norm, source, pipeline_stage, priority, created_by, created_at, updated_at) "
                    "select gen_random_uuid(), 'Volume lead ' || g, 'volume lead ' || g, 'manual_entry', 'new', 'medium', :admin, :at, :at "
                    "from generate_series(1, :n) g"
                ),
                {"admin": admin, "n": missing, "at": VOLUME_DATE},
            )
        return missing

    return run_db(_go)


def big_lead_count() -> int:
    from sqlalchemy import func, select
    from sales.models import SalesLead
    from sales_reports_test_utils import run_db

    async def _go(db):
        return (await db.execute(select(func.count()).select_from(SalesLead).where(SalesLead.created_at >= YEAR_START, SalesLead.created_at < YEAR_END, SalesLead.archived_at.is_(None)))).scalar_one()

    return run_db(_go)


# =============================================================================
# no N+1
# =============================================================================
class TestNoNPlusOne:
    @pytest.mark.parametrize("who", ["team", "employee"])
    def test_a_handful_of_leads_and_a_year_of_them_cost_the_same_number_of_queries(self, world, who):
        ctx = team_ctx() if who == "team" else employee_ctx()
        small_dates = (world.window.start.isoformat(), world.window.end.isoformat())
        small = calls(ctx, small_dates)
        for other_dates in (BIG, WIDE):                     # 1500 leads | every employee and every kind of work there is
            other = calls(ctx, other_dates)
            for name in small:
                _, s = run_watched(small[name])
                _, b = run_watched(other[name])
                # The one legitimate difference: the optional lookup of employee names for people who did work but own no lead.
                assert s and b and abs(len(b) - len(s)) <= 1, (name, len(s), len(b))
                assert len(b) <= BUDGET[name] and len(s) <= BUDGET[name], (who, name, len(s), len(b))

    def test_no_statement_is_ever_repeated_which_is_what_a_per_row_query_looks_like(self, world):
        for name, fn in calls(team_ctx(), WIDE).items():
            _, statements = run_watched(fn)
            texts = [s["sql"] for s in statements]
            assert len(texts) == len(set(texts)), f"{name}: the same statement ran more than once"

    def test_narrowing_to_an_employee_adds_one_lookup_and_nothing_per_row(self, world):
        from sales.services import dashboard as dashboard_service

        plain = calls(team_ctx(), WIDE)["dashboard"]
        narrowed = scope_for(team_ctx(), *WIDE, employee_id=str(uuid.uuid4()))
        _, a = run_watched(plain)
        _, b = run_watched(lambda db: dashboard_service.dashboard(db, narrowed))
        assert len(b) <= len(a) + 1                                                    # the header names the employee (one query); the onboarding section drops out


# =============================================================================
# aggregation happens in SQL
# =============================================================================
_PAGE_STATEMENTS = re.compile(r"\blimit\b", re.I)


def is_staff_lookup(sql: str) -> bool:
    """The two-column staff lookup (names of people a result mentions, or the idle staff of a performance table). Its size is
    the number of staff accounts - not of leads - and MAX_GROUPS caps it."""
    return " ".join(sql.lower().split()).startswith("select users.id, users.name from users where")


def is_aggregate(sql: str) -> bool:
    low = " ".join(sql.lower().split())
    if "group by" in low:
        return True
    return bool(re.match(r"select count\(", low)) or bool(re.match(r"select coalesce\(sum\(", low))


class TestAggregationInSql:
    def test_the_year_of_leads_is_big_enough_to_mean_something(self):
        assert big_lead_count() >= VOLUME                  # (the module's `volume` fixture guarantees it - this pins the guarantee)

    def test_every_dashboard_statement_is_an_aggregate_a_names_lookup_or_a_page(self, world):
        _, statements = run_watched(calls(team_ctx(), BIG)["dashboard"])
        for s in statements:
            low = " ".join(s["sql"].lower().split())
            names_lookup = is_staff_lookup(s["sql"])
            assert is_aggregate(s["sql"]) or names_lookup, s["sql"][:200]
            assert "business_name" not in low and "phone" not in low and "email" not in low, s["sql"][:200]    # no lead is ever read

    @pytest.mark.parametrize("name", ["pipeline", "sources", "conversion", "activities", "employee-performance", "onboarding", "campaigns"])
    def test_every_report_statement_is_an_aggregate_a_lookup_or_a_bounded_page(self, world, name):
        _, statements = run_watched(calls(team_ctx(), BIG)[name])
        for s in statements:
            low = " ".join(s["sql"].lower().split())
            names_lookup = is_staff_lookup(s["sql"])
            page = bool(_PAGE_STATEMENTS.search(low)) and not is_aggregate(s["sql"]) and not names_lookup    # the paginated list of the onboarding report
            assert is_aggregate(s["sql"]) or names_lookup or page, (name, s["sql"][:200])
            if page:
                assert s["rows"] <= 25, (name, s["rows"])                                    # one page, never the whole table

    def test_the_lead_report_reads_one_page_of_leads_and_aggregates_the_rest(self, world):
        _, statements = run_watched(calls(team_ctx(), BIG)["leads"])
        pages = [s for s in statements if "sales_leads.business_name" in s["sql"]]
        assert len(pages) == 1 and pages[0]["rows"] <= 25                                    # exactly one statement reads leads, and only a page of them
        assert all(is_aggregate(s["sql"]) or s in pages for s in statements)

    def test_no_statement_returns_anything_like_a_row_per_lead(self, world):
        leads = big_lead_count()
        assert leads >= VOLUME
        for who, ctx in (("team", team_ctx()), ("employee", employee_ctx())):
            for name, fn in calls(ctx, BIG).items():
                _, statements = run_watched(fn)
                assert max(s["rows"] for s in statements) <= 5000, (who, name)               # the runaway guard on every statement (MAX_GROUPS) ...
                most = max([s["rows"] for s in statements if not is_staff_lookup(s["sql"])] or [0])
                assert most < leads / 4, (who, name, most, leads)                            # ... and in practice far below the number of leads (a staff lookup is as big as the staff, not the leads)

    def test_the_results_are_the_same_whichever_way_they_are_asked_for(self, world):
        """The aggregate the dashboard reports equals a plain COUNT written independently, on the closed period of data."""
        from sqlalchemy import func, select
        from sales.models import SalesLead
        from sales_reports_test_utils import run_db

        async def independent(db):
            return (await db.execute(select(func.count()).select_from(SalesLead).where(
                SalesLead.created_at >= YEAR_START, SalesLead.created_at < YEAR_END, SalesLead.archived_at.is_(None)))).scalar_one()

        body, _ = run_watched(calls(team_ctx(), BIG)["dashboard"])
        assert body["kpis"]["total_leads"] == run_db(independent)


# =============================================================================
# indexes
# =============================================================================
async def _explain_all(statements):
    from database import engine

    plans = []
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SET enable_seqscan = off")
            for s in statements:
                rows = (await conn.exec_driver_sql("EXPLAIN " + s["sql"], s["params"])).all()
                plans.append("\n".join(r[0] for r in rows))
            await conn.exec_driver_sql("RESET enable_seqscan")
    finally:
        await engine.dispose()
    return plans


# A period predicate (a range on a column, with bound values) -> the index that must serve it. Checked on a team caller and
# a narrow window, where the range is selective. `>= $n` (a bound value) tells the period filter from `>= now()` (a "now"
# reading such as follow-ups due from now on, which is served by the partial open-work indexes).
EXPECTED_INDEX = [
    (r"sales_leads\.created_at >= \$\d+", "ix_sales_leads_created_at"),
    (r"sales_leads\.closed_at >= \$\d+", "ix_sales_leads_closed_at"),                  # Phase 5
    (r"sales_calls\.called_at >= \$\d+", "ix_sales_calls_called_at"),
    (r"sales_followups\.due_at >= \$\d+", "ix_sales_followups_due_at"),                # Phase 5
    (r"sales_demos\.scheduled_at >= \$\d+", "ix_sales_demos_scheduled_at"),
    (r"sales_trials\.started_at >= \$\d+", "ix_sales_trials_started_at"),              # Phase 5
    (r"sales_onboarding\.started_at >= \$\d+", "ix_sales_onboarding_started_at"),
]


class TestIndexesServeThePeriodFilters:
    def test_each_period_filter_is_served_by_its_own_index(self, world):
        """EXPLAIN every period statement the dashboard and the reports run, with sequential scans switched off (so the planner
        must show the index it prefers), and require the index named above. A generic "no Seq Scan" would not do: without
        ix_sales_followups_due_at the planner falls back to the composite (lead_id, due_at) index - still no Seq Scan, but a
        full scan of it."""
        window = (world.window.start.isoformat(), world.window.end.isoformat())
        seen = {}
        for name, fn in calls(team_ctx(), window).items():
            _, statements = run_watched(fn)
            wanted = [(s, index) for s in statements for pattern, index in EXPECTED_INDEX if re.search(pattern, s["sql"])]
            plans = asyncio.run(_explain_all([s for s, _ in wanted]))
            for (s, index), plan in zip(wanted, plans):
                seen.setdefault(index, []).append(name)
                assert index in plan, f"{name}: expected {index}\n{s['sql'][:300]}\n{plan}"
        assert set(seen) == {index for _, index in EXPECTED_INDEX}, f"some period filters were never exercised: {set(i for _, i in EXPECTED_INDEX) - set(seen)}"


# =============================================================================
# the migration on this database
# =============================================================================
class TestMigrationState:
    def _q(self, sql):
        from sqlalchemy import text
        from sales_reports_test_utils import run_db

        async def _go(db):
            return [tuple(r) for r in (await db.execute(text(sql))).all()]

        return run_db(_go)

    def test_the_database_is_at_the_phase_5_head(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from pathlib import Path

        head = ScriptDirectory.from_config(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))).get_heads()
        assert head == ["b6d1f3a8c294"]
        assert self._q("select version_num from alembic_version") == [("b6d1f3a8c294",)]

    def test_the_catalog_and_the_grants_are_exactly_the_approved_ones(self):
        assert {k for (k,) in self._q("select key from staff_permissions")} == ALL_PERMISSION_KEYS
        grants = {(r, k) for r, k in self._q(
            "select r.key, p.permission_key from staff_role_permissions p join staff_roles r on r.id = p.role_id where r.is_system")}
        assert grants == {(role, key) for role, keys in SYSTEM_ROLE_PERMISSIONS.items() for key in keys}
        phase5 = {"sales.dashboard.view", "sales.reports.view"}
        for role in ("sales_manager", "sales_employee", "onboarding_employee"):
            assert phase5 <= SYSTEM_ROLE_PERMISSIONS[role]
        assert not phase5 & SYSTEM_ROLE_PERMISSIONS["lead_data_entry"]

    def test_the_three_reporting_indexes_exist_as_declared(self):
        defs = {n: d for n, d in self._q("select indexname, indexdef from pg_indexes where indexname in ("
                                         "'ix_sales_followups_due_at','ix_sales_trials_started_at','ix_sales_leads_closed_at','ix_sales_onboarding_completed_at')")}
        assert set(defs) == {"ix_sales_followups_due_at", "ix_sales_trials_started_at", "ix_sales_leads_closed_at"}       # and no fourth: nothing reads a completed_at range
        assert "ON public.sales_followups" in defs["ix_sales_followups_due_at"] and "(due_at)" in defs["ix_sales_followups_due_at"]
        assert "started_at DESC" in defs["ix_sales_trials_started_at"]
        assert "WHERE (closed_at IS NOT NULL)" in defs["ix_sales_leads_closed_at"]

    def test_the_models_declare_every_index_the_database_has_for_the_sales_tables(self):
        from sales import models

        declared = {i.name for cls in (models.SalesLead, models.SalesFollowup, models.SalesTrial, models.SalesOnboarding, models.SalesDemo, models.SalesCall) for i in cls.__table__.indexes}
        db = {n for (n,) in self._q(
            "select indexname from pg_indexes where tablename in ('sales_leads','sales_followups','sales_trials','sales_onboarding','sales_demos','sales_calls') and indexname not like '%_pkey'")}
        assert declared == db, (declared ^ db)

    def test_no_table_was_added_by_phase_5(self):
        tables = {t for (t,) in self._q("select tablename from pg_tables where tablename like 'sales_%' or tablename like 'staff_%'")}
        assert tables == {
            "staff_permissions", "staff_roles", "staff_role_permissions", "staff_user_roles", "staff_audit_events",
            "sales_lead_sources", "sales_campaigns", "sales_leads", "sales_lead_activities",
            "sales_calls", "sales_followups", "sales_demos", "sales_trials", "sales_customers", "sales_onboarding",
        }
