"""JAZ Sales - Phase 5: unit tier - no HTTP, no database writes.

The period rules (every preset, at the awkward edges of the calendar), the rate rule, the metric definitions, the scope rules
(who is a team caller, whose work counts), the permission catalog and the DRIFT GUARDS that keep the code, the migration and
the test expectations saying the same thing. Also static guarantees over the source: the aggregates are computed in SQL, only
permissions are consulted (never role names), and the services contain no SQL of their own.
"""
from sales_test_utils import ALL_MODULES, ALL_PERMISSION_KEYS, SYSTEM_ROLE_PERMISSIONS, assert_scratch_target

assert_scratch_target(need_http=False)  # must run BEFORE importing database (which loads .env)

import importlib.util
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from sales import constants as C
from sales import metrics as M
from sales import permissions as P
from sales import report_schemas as S
from sales.repositories import reports as repo
from sales.services import report_data
from sales.services.access import StaffContext
from sales.services.report_scope import build_scope, has_onboarding_scope, viewable_kinds

BACKEND = Path(__file__).resolve().parent.parent
MIGRATION = BACKEND / "migrations" / "versions" / "b6d1f3a8c294_sales_dashboard_reports.py"
PHASE5 = {"sales.dashboard.view", "sales.reports.view"}


def ctx(*permissions, super_admin=False, user_id=None) -> StaffContext:
    return StaffContext({"id": "x", "role": "jaz_staff", "status": "active"}, user_id or uuid.uuid4(), super_admin, (), frozenset(permissions))


def load_migration(path=MIGRATION, name="mig_b6d1f3a8c294"):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def period(range_=None, date_from=None, date_to=None, today=date(2026, 9, 24)):
    return M.resolve_period(range_, date_from, date_to, today)


def bounds(p):
    return (p.date_from.isoformat(), p.date_to.isoformat())


# =============================================================================
# the period
# =============================================================================
class TestPresets:
    @pytest.mark.parametrize("today,expected", [
        (date(2026, 9, 24), ("2026-09-01", "2026-09-30")),
        (date(2026, 9, 1), ("2026-09-01", "2026-09-30")),
        (date(2026, 9, 30), ("2026-09-01", "2026-09-30")),
        (date(2024, 2, 15), ("2024-02-01", "2024-02-29")),        # a leap February
        (date(2026, 2, 10), ("2026-02-01", "2026-02-28")),        # an ordinary one
        (date(2026, 12, 31), ("2026-12-01", "2026-12-31")),       # the last day of the year
        (date(2026, 1, 1), ("2026-01-01", "2026-01-31")),
    ])
    def test_this_month(self, today, expected):
        assert bounds(period("this_month", today=today)) == expected

    @pytest.mark.parametrize("today,expected", [
        (date(2026, 9, 24), ("2026-08-01", "2026-08-31")),
        (date(2026, 1, 15), ("2025-12-01", "2025-12-31")),        # across the year boundary
        (date(2026, 3, 31), ("2026-02-01", "2026-02-28")),        # the current month is longer than the previous
        (date(2024, 3, 1), ("2024-02-01", "2024-02-29")),
        (date(2026, 5, 31), ("2026-04-01", "2026-04-30")),
    ])
    def test_last_month(self, today, expected):
        assert bounds(period("last_month", today=today)) == expected

    @pytest.mark.parametrize("today,expected", [
        (date(2026, 9, 21), ("2026-09-21", "2026-09-27")),        # a Monday: the week starts today
        (date(2026, 9, 24), ("2026-09-21", "2026-09-27")),        # a Thursday
        (date(2026, 9, 27), ("2026-09-21", "2026-09-27")),        # the Sunday: the week ends today
        (date(2026, 9, 28), ("2026-09-28", "2026-10-04")),        # the next Monday starts a new week (across a month)
        (date(2026, 12, 31), ("2026-12-28", "2027-01-03")),       # across the year
        (date(2026, 1, 1), ("2025-12-29", "2026-01-04")),         # a Thursday: the week began in December
    ])
    def test_this_week_is_monday_to_sunday(self, today, expected):
        assert bounds(period("this_week", today=today)) == expected
        assert period("this_week", today=today).date_from.weekday() == M.WEEK_STARTS_ON

    def test_today(self):
        assert bounds(period("today", today=date(2026, 9, 24))) == ("2026-09-24", "2026-09-24")

    def test_the_default_is_this_month_and_dates_alone_mean_custom(self):
        assert period().range == "this_month" and bounds(period()) == ("2026-09-01", "2026-09-30")
        p = period(None, date(2026, 1, 1), date(2026, 1, 31))
        assert p.range == "custom" and p.days == 31


class TestCustomAndValidation:
    def test_the_length_of_a_period_and_its_instants(self):
        p = period("custom", date(2026, 1, 1), date(2026, 1, 3))
        assert p.days == 3 and period("today").days == 1
        # [start, end): the end is exclusive; a day starts at midnight in the application timezone (Baghdad, UTC+3)
        assert p.start == datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=3))) == datetime(2025, 12, 31, 21, tzinfo=timezone.utc)
        assert p.end == datetime(2026, 1, 3, 21, tzinfo=timezone.utc)
        assert p.start.utcoffset() == timedelta(hours=3) and p.end - p.start == timedelta(days=3)

    def test_the_longest_period_is_a_year_and_a_day_more_is_refused(self):
        assert period("custom", date(2024, 1, 1), date(2024, 12, 31)).days == 366 == M.MAX_PERIOD_DAYS
        with pytest.raises(M.PeriodError) as e:
            period("custom", date(2025, 1, 1), date(2026, 1, 2))
        assert (e.value.code, e.value.field) == ("period_too_long", "date_to")

    @pytest.mark.parametrize("args,code,field", [
        (("custom", None, None), "period_incomplete", "date_from"),
        (("custom", date(2026, 1, 1), None), "period_incomplete", "date_to"),
        ((None, None, date(2026, 1, 1)), "period_incomplete", "date_from"),
        (("custom", date(2026, 2, 1), date(2026, 1, 1)), "period_reversed", "date_to"),
        (("today", date(2026, 1, 1), date(2026, 1, 2)), "period_conflict", "date_from"),
        (("this_month", date(2026, 1, 1), None), "period_conflict", "date_from"),
        (("yesterday", None, None), "invalid_range", "range"),
        (("", None, None), "invalid_range", "range"),
        (("custom", date(1999, 12, 31), date(2000, 1, 2)), "period_out_of_bounds", "date_from"),
        (("custom", date(2099, 12, 30), date(2100, 1, 1)), "period_out_of_bounds", "date_from"),
    ])
    def test_every_refusal_has_a_stable_code_and_names_its_field(self, args, code, field):
        with pytest.raises(M.PeriodError) as e:
            period(*args)
        assert (e.value.code, e.value.field) == (code, field) and e.value.message

    def test_a_series_is_daily_for_a_short_period_and_weekly_beyond_45_days(self):
        assert M.series_bucket(period("custom", date(2026, 1, 1), date(2026, 2, 14))) == "day"        # 45 days
        assert M.series_bucket(period("custom", date(2026, 1, 1), date(2026, 2, 15))) == "week"       # 46 days
        assert M.series_bucket(period("today")) == "day" and M.series_bucket(period("custom", date(2026, 1, 1), date(2026, 12, 31))) == "week"


# =============================================================================
# rates and figures
# =============================================================================
class TestRates:
    def test_no_whole_means_no_data_not_zero_percent(self):
        assert M.rate(0, 0) is None and M.rate(5, 0) is None

    @pytest.mark.parametrize("part,whole,expected", [(0, 5, 0.0), (1, 3, 33.3), (2, 3, 66.7), (1, 8, 12.5), (5, 5, 100.0), (2, 11, 18.2), (1, 7, 14.3)])
    def test_percentages_have_one_decimal(self, part, whole, expected):
        assert M.rate(part, whole) == expected

    def test_a_rate_over_a_cohort_cannot_exceed_100_percent(self):
        assert all(M.rate(w, n) <= 100.0 for n in range(1, 40) for w in range(0, n + 1))


class TestFigures:
    def test_lead_figures_and_their_sum(self):
        a, b = repo.LeadMeasures(10, 2, 3, 100.5, 40.25), repo.LeadMeasures(0, 0, 0, 0.0, 0.0)
        f = report_data.lead_figures(a)
        assert f == {"leads": 10, "won": 2, "lost": 3, "open": 5, "conversion_rate": 20.0, "value": 100.5, "won_value": 40.25}
        assert report_data.lead_figures(b)["conversion_rate"] is None and report_data.lead_figures(b)["open"] == 0
        assert report_data.add_measures([a, a, b]) == repo.LeadMeasures(20, 4, 6, 201.0, 80.5)

    def test_cancelled_work_is_not_activity_but_a_cancelled_call_does_not_exist(self):
        for kind in ("followups", "demos", "trials"):
            assert repo.counts_as_activity(kind, "cancelled") is False and repo.counts_as_activity(kind, "completed") is True
        assert repo.counts_as_activity("calls", "answered") is True
        assert repo.counts_as_activity("calls", "cancelled") is True          # not a call result: nothing to exclude

    def test_pipeline_rows_are_every_stage_in_board_order(self):
        rows = report_data.pipeline_rows({s: repo.StageTotal(i, i * 10.0, i) for i, s in enumerate(C.PIPELINE_STAGES)})
        assert [r["stage"] for r in rows] == list(C.PIPELINE_STAGES) and rows[3] == {"stage": "interested", "leads": 3, "value": 30.0, "valued": 3}


class TestMetricDefinitions:
    def test_every_kpi_stage_is_a_real_stage_and_the_groups_do_not_overlap(self):
        for stages in M.KPI_STAGES.values():
            assert set(stages) <= set(C.PIPELINE_STAGES)
        flat = [s for stages in M.KPI_STAGES.values() for s in stages]
        assert len(flat) == len(set(flat))                                       # a stage is counted by at most one KPI
        assert M.KPI_STAGES["demos"] == (C.STAGE_DEMO_SCHEDULED, C.STAGE_DEMO_COMPLETED)
        assert set(flat) == set(C.PIPELINE_STAGES) - {C.STAGE_TRIAL}             # `trial` is reported through the pipeline (and active_trials), not double-counted

    def test_every_metric_the_dashboard_returns_has_a_basis(self):
        kpis = set(S.DashboardKpis.model_fields)
        assert kpis <= set(M.METRIC_BASES)
        assert set(M.METRIC_BASES.values()) == {M.BASIS_COHORT, M.BASIS_EVENT, M.BASIS_NOW}
        sections = set(S.DashboardOut.model_fields) - set(S.Header.model_fields) - {"bases", "kpis"}
        assert sections <= set(M.METRIC_BASES) | {"onboarding"}
        # the now-readings are exactly the ones about the current state
        assert {k for k, b in M.METRIC_BASES.items() if b == M.BASIS_NOW} >= {"overdue_followups", "upcoming_followups", "upcoming_demos", "active_trials"}
        assert M.METRIC_BASES["conversion_rate"] == M.BASIS_COHORT and M.METRIC_BASES["activity"] == M.BASIS_EVENT

    def test_the_activity_kinds_are_the_four_kinds_the_repository_knows(self):
        assert M.ACTIVITY_KINDS == ("calls", "followups", "demos", "trials") and set(repo._ACTIVITY) == set(M.ACTIVITY_KINDS)
        assert set(report_data._BREAKDOWN_KEYS) == set(M.ACTIVITY_KINDS)
        assert report_data._BREAKDOWN_KEYS["followups"] == C.FOLLOWUP_STATUSES and report_data._BREAKDOWN_KEYS["calls"] == C.CALL_RESULTS

    def test_the_response_models_reject_nothing_they_should_carry(self):
        # a dashboard with every section null and every KPI null is a valid response (a caller with only the dashboard key)
        body = {"period": None, "scope": "personal", "employee": None, "as_of": datetime.now(timezone.utc), "bases": M.METRIC_BASES, "kpis": {}}
        out = S.DashboardOut(**body)
        assert out.pipeline is None and out.performance is None and out.onboarding is None and out.kpis.total_leads is None


# =============================================================================
# scope: who is asking about whom
# =============================================================================
class TestScope:
    P = period("this_month")

    def test_a_caller_with_the_all_scope_is_the_team(self):
        s = build_scope(ctx(P.PERM_LEADS_SCOPE_ALL), self.P, None)
        assert s.team and s.name == "team" and s.subject is None and s.lead_owner is None and s.actor is None       # everybody's leads, everybody's work

    def test_anybody_else_is_personal_and_their_work_is_their_own(self):
        me = uuid.uuid4()
        for keys in ((P.PERM_LEADS_SCOPE_ASSIGNED,), (P.PERM_LEADS_SCOPE_INTAKE,), (P.PERM_LEADS_VIEW,), ()):
            s = build_scope(ctx(*keys, user_id=me), self.P, None)
            assert not s.team and s.name == "personal" and s.subject is None and s.lead_owner is None and s.actor == me, keys

    def test_a_team_caller_may_narrow_to_anybody_and_it_changes_both_questions(self):
        who, target = uuid.uuid4(), uuid.uuid4()
        s = build_scope(ctx(P.PERM_LEADS_SCOPE_ALL, user_id=who), self.P, str(target))
        assert s.subject == target and s.lead_owner == target and s.actor == target
        assert build_scope(ctx(P.PERM_LEADS_SCOPE_ALL, user_id=who), self.P, "me").subject == who

    def test_a_personal_caller_may_only_ask_about_themself(self):
        me, other = uuid.uuid4(), uuid.uuid4()
        c = ctx(P.PERM_LEADS_SCOPE_ASSIGNED, user_id=me)
        for ok in (None, "", "me", str(me)):
            assert build_scope(c, self.P, ok).subject is None                     # never a narrowing: their own scope already is "me"
        for bad in (str(other), str(uuid.uuid4())):
            with pytest.raises(HTTPException) as e:
                build_scope(c, self.P, bad)
            assert e.value.status_code == 403 and e.value.detail == "Access denied"

    def test_a_malformed_employee_id_is_a_400_in_the_usual_shape(self):
        for c in (ctx(P.PERM_LEADS_SCOPE_ALL), ctx()):
            with pytest.raises(HTTPException) as e:
                build_scope(c, self.P, "not-a-uuid")
            assert e.value.status_code == 400 and e.value.detail["field"] == "employee_id"

    def test_the_visible_kinds_and_the_onboarding_scope_come_from_permissions_alone(self):
        assert viewable_kinds(ctx()) == () and viewable_kinds(ctx(P.PERM_CALLS_VIEW, P.PERM_TRIALS_VIEW)) == ("calls", "trials")
        assert viewable_kinds(ctx(P.PERM_CALLS_MANAGE)) == ()                       # managing is not viewing
        assert viewable_kinds(ctx(*P.ALL_PERMISSIONS)) == M.ACTIVITY_KINDS
        assert has_onboarding_scope(ctx(P.PERM_ONBOARDING_VIEW, P.PERM_ONBOARDING_SCOPE_ALL))
        assert has_onboarding_scope(ctx(P.PERM_ONBOARDING_VIEW, P.PERM_ONBOARDING_SCOPE_ASSIGNED))
        assert not has_onboarding_scope(ctx(P.PERM_ONBOARDING_VIEW)) and not has_onboarding_scope(ctx(P.PERM_ONBOARDING_SCOPE_ALL))

    def test_the_lead_population_is_the_predicate_the_lead_list_uses(self):
        from sales.repositories.leads import _conditions
        from sqlalchemy import true

        scope = repo.LeadScope(true(), period("custom", date(2026, 1, 1), date(2026, 1, 31)), assignee=uuid.uuid4(), source="facebook", priority="high")
        f = scope.filters()
        assert (f.created_from, f.created_to, f.archived) == (date(2026, 1, 1), date(2026, 1, 31), "exclude")      # never an archived lead
        mine = [str(c.compile(compile_kwargs={"literal_binds": False})) for c in repo.lead_conditions(scope)]
        theirs = [str(c.compile(compile_kwargs={"literal_binds": False})) for c in _conditions(scope.visibility, f)]
        assert mine == theirs


# =============================================================================
# permission catalog and drift guards
# =============================================================================
class TestCatalogAndMigration:
    def test_the_two_permissions_and_the_reports_module(self):
        assert PHASE5 <= set(P.ALL_PERMISSIONS) and set(P.ALL_PERMISSIONS) == ALL_PERMISSION_KEYS
        assert P.PERM_DASHBOARD_VIEW == "sales.dashboard.view" and P.PERM_REPORTS_VIEW == "sales.reports.view"
        assert len({P.ALL_PERMISSIONS[k] for k in PHASE5}) == 2
        assert [m["key"] for m in P.MODULES] == ALL_MODULES
        assert {m["key"]: m["permission"] for m in P.MODULES}["reports"] == P.PERM_REPORTS_VIEW
        assert [m["key"] for m in P.MODULES][-4:] == ["reports", "customers", "batches", "performance"]   # onboarding left the sections (simplified workflow); the batches' two follow

    def test_the_grants_of_the_system_roles(self):
        for role in ("sales_manager", "sales_employee", "onboarding_employee"):
            assert PHASE5 <= SYSTEM_ROLE_PERMISSIONS[role], role
        assert not PHASE5 & SYSTEM_ROLE_PERMISSIONS["lead_data_entry"]                # metrics are opt-in
        # the Onboarding Employee gets the two doors but nothing that would put a lead or a work item behind them
        behind = {P.PERM_LEADS_VIEW, P.PERM_CALLS_VIEW, P.PERM_FOLLOWUPS_VIEW, P.PERM_DEMOS_VIEW, P.PERM_TRIALS_VIEW, P.PERM_CAMPAIGNS_VIEW,
                  P.PERM_CUSTOMERS_VIEW, P.PERM_LEADS_SCOPE_ALL, P.PERM_LEADS_SCOPE_ASSIGNED, P.PERM_LEADS_SCOPE_INTAKE}
        assert not behind & SYSTEM_ROLE_PERMISSIONS["onboarding_employee"]
        assert P.PERM_LEADS_SCOPE_ALL not in SYSTEM_ROLE_PERMISSIONS["sales_employee"]      # so an employee is never "the team"

    def test_the_frozen_seed_equals_the_code(self):
        mig = load_migration()
        seeded = dict(mig._PERMISSIONS)
        assert set(seeded) == PHASE5 and seeded == {k: P.ALL_PERMISSIONS[k] for k in seeded}         # keys AND descriptions
        granted = {}
        for role, key in mig._GRANTS:
            granted.setdefault(role, []).append(key)
        assert set(granted) == {"sales_manager", "sales_employee", "onboarding_employee"}              # Lead Data Entry: nothing
        for role, keys in granted.items():
            assert sorted(keys) == sorted(set(keys)) and set(keys) == SYSTEM_ROLE_PERMISSIONS[role] & PHASE5, role
        phase1 = load_migration(BACKEND / "migrations" / "versions" / "bb595f6d8dae_sales_rbac_foundation.py", "mig_bb595_for_p5")
        ids = {key: rid for rid, key, *_ in phase1._ROLES}
        assert mig._ROLE_IDS == {r: ids[r] for r in ("sales_manager", "sales_employee", "onboarding_employee")}

    def test_the_migration_is_chained_after_phase_4_and_is_the_only_head(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        mig = load_migration()
        assert mig.revision == "b6d1f3a8c294" and mig.down_revision == "f4c8a1d92b63" and mig.branch_labels is None and mig.depends_on is None
        script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
        assert script.get_heads() == ["f2b6d8a1c4e9"]                                                # the simplified workflow's and the batches' revisions, chained after this one
        assert script.get_revision("a3f8c2d7e915").down_revision == "b6d1f3a8c294"
        assert all(not isinstance(r.down_revision, tuple) for r in script.walk_revisions())          # nothing merges two branches

    def test_the_migration_creates_no_table_and_touches_only_the_catalog_and_its_own_indexes(self):
        source = MIGRATION.read_text()
        assert not re.findall(r"op\.(create_table|drop_table|alter_column|add_column|drop_column|drop_constraint|create_check_constraint|create_foreign_key)\(", source)
        assert set(re.findall(r"sa\.table\(\s*'([a-z_]+)'", source)) == {"staff_permissions", "staff_role_permissions"}
        assert not re.search(r"INSERT INTO (users|companies|sales_)|op\.bulk_insert\((?!perms|role_perms)", source)      # nothing but reference data is seeded
        assert "Hand-written" in source.split('"""')[1] and "autogenerate" not in source.split('"""')[2]
        assert "SET LOCAL lock_timeout" in source
        down = source[source.index("def downgrade()"):]
        assert "drop_index" in down and "DELETE FROM staff_role_permissions" in down and "DELETE FROM staff_permissions" in down
        assert "Refusing" not in down and "raise" not in down                                           # no data to protect: it never refuses

    def test_the_three_indexes_are_declared_on_the_models_exactly_as_the_migration_creates_them(self):
        from sales import models

        mig = load_migration()
        table_of = {"sales_followups": models.SalesFollowup, "sales_trials": models.SalesTrial, "sales_leads": models.SalesLead}
        assert [n for n, *_ in mig._INDEXES] == ["ix_sales_followups_due_at", "ix_sales_trials_started_at", "ix_sales_leads_closed_at"]
        for name, table, columns, where in mig._INDEXES:
            declared = {i.name: i for i in table_of[table].__table__.indexes}[name]
            assert not declared.unique
            if where:
                assert str(declared.dialect_options["postgresql"]["where"]) == where, name
            else:
                assert declared.dialect_options["postgresql"]["where"] is None, name


# =============================================================================
# static guarantees over the source
# =============================================================================
SERVICES = [BACKEND / "sales" / "services" / n for n in ("report_scope.py", "report_data.py", "dashboard.py", "reports.py")]
PHASE5_SOURCES = SERVICES + [BACKEND / "sales" / n for n in ("metrics.py", "report_params.py", "report_schemas.py")] + [BACKEND / "sales" / "repositories" / "reports.py"]


class TestSourceGuarantees:
    def test_the_aggregates_are_sql_the_repository_never_loads_records(self):
        source = (BACKEND / "sales" / "repositories" / "reports.py").read_text()
        code = re.sub(r'""".*?"""', "", source, flags=re.S)
        assert ".scalars()" not in code and ".scalar_one_or_none()" not in code and "session.get" not in code
        # no select ever names an entity as a whole (select(SalesLead) would load rows); every select lists columns / aggregates
        assert not re.findall(r"select\(\s*(?:Sales[A-Za-z]+|User|Company)\s*[,)]", code)
        assert code.count("func.count") >= 15 and "group_by" in code and "func.sum" in code
        for aggregate_fn in ("stage_totals", "leads_by_assignee", "leads_by_source", "leads_by_campaign", "activity_by_person", "onboarding_by_stage", "conversion_series"):
            assert re.search(rf"async def {aggregate_fn}\(", code), aggregate_fn

    def test_the_services_hold_no_sql_and_no_models(self):
        for path in SERVICES:
            code = re.sub(r'""".*?"""', "", path.read_text(), flags=re.S)
            assert not re.search(r"^(?:from|import) sqlalchemy(?!\.ext\.asyncio)", code, re.M), path.name      # only the AsyncSession type
            assert not re.search(r"^from (models|sales\.models) import", code, re.M), path.name
            assert "db.execute" not in code and ".select(" not in code, path.name

    def test_only_permissions_are_consulted_never_role_names(self):
        for path in PHASE5_SOURCES + [BACKEND / "sales" / "router.py"]:
            code = re.sub(r'""".*?"""', "", path.read_text(), flags=re.S)
            code = "\n".join(line.split("#")[0] for line in code.splitlines())
            for role in ("sales_manager", "sales_employee", "onboarding_employee", "lead_data_entry", "is_super_admin"):
                if path.name == "router.py" and role == "is_super_admin":
                    continue                                                                # Phase 1's /me
                assert role not in code, (path.name, role)

    def test_the_report_routes_are_read_only_and_state_their_permissions(self):
        code = (BACKEND / "sales" / "router.py").read_text()
        phase5 = code[code.index("# Phase 5 - dashboard and reports"):]
        routes = re.findall(r"@sales_router\.(\w+)\(\"([^\"]+)\"", phase5)
        assert len(routes) == 9 and {m for m, _ in routes} == {"get"}
        assert phase5.count("require_permission(") == 9

    def test_every_grouped_query_carries_the_callers_scope(self):
        """Each aggregate is handed `visibility` (lead scope) or goes through lead_conditions - none is unscoped."""
        code = re.sub(r'""".*?"""', "", (BACKEND / "sales" / "repositories" / "reports.py").read_text(), flags=re.S)
        for fn in re.findall(r"async def (\w+)\(", code):
            if fn == "names_for":
                continue                                                                       # ids the caller already saw, resolved to names
            if fn == "assignable_staff":
                continue                                                                       # the assignment picker's people; the SERVICE gates it (see below)
            body = code[code.index(f"async def {fn}("):]
            body = body[:body.index("\nasync def ", 5)] if "\nasync def " in body[5:] else body
            assert "visibility" in body or "lead_conditions" in body or "scope" in body, fn

    def test_the_idle_staff_lookup_is_only_reachable_through_its_gate(self):
        """`assignable_staff` names people without a lead scope, so its ONE caller must check that the reader is the team and
        may use the assignment picker (which reveals the same people)."""
        callers = [p.name for p in (BACKEND / "sales").rglob("*.py") if "assignable_staff" in p.read_text() and p.name != "reports.py"]
        assert callers == ["report_data.py"], callers
        code = (BACKEND / "sales" / "services" / "report_data.py").read_text()
        gate = code[code.index("async def idle_staff("):]
        gate = gate[: gate.index("\n\n\n")]
        assert "scope.team" in gate and "scope.subject is None" in gate and "PERM_LEADS_ASSIGN" in gate


# =============================================================================
# the frontend strings (there is no JS test runner for the Sales UI; the parity rules are checked here)
# =============================================================================
UTILS = BACKEND.parent / "frontend" / "src" / "utils"


def _dictionary(filename: str) -> dict:
    """{'ar': {key: text}, 'en': {...}} read from one of the Sales translation modules (flat `key: 'text',` lines)."""
    out, lang = {"ar": {}, "en": {}}, None
    for line in (UTILS / filename).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^  (ar|en): \{", line)
        if m:
            lang = m.group(1)
        elif re.match(r"^  \},?$", line):
            lang = None
        elif lang and (kv := re.match(r"^    ([A-Za-z0-9_]+): (['\"])(.*)\2,\s*$", line)):
            out[lang][kv.group(1)] = kv.group(3)
    return out


class TestFrontendStrings:
    def test_arabic_and_english_have_the_same_keys_and_the_same_placeholders(self):
        d = _dictionary("salesReportsTranslations.js")
        assert len(d["ar"]) == len(d["en"]) > 100
        assert set(d["ar"]) == set(d["en"]), set(d["ar"]) ^ set(d["en"])
        for key in d["ar"]:
            assert sorted(re.findall(r"\{[a-z_]+\}", d["ar"][key])) == sorted(re.findall(r"\{[a-z_]+\}", d["en"][key])), key
            assert d["ar"][key].strip() and d["en"][key].strip(), key

    def test_no_key_shadows_a_string_of_an_earlier_phase(self):
        mine = _dictionary("salesReportsTranslations.js")
        for other in ("salesTranslations.js", "salesLeadsTranslations.js", "salesActivitiesTranslations.js", "salesCustomersTranslations.js"):
            theirs = _dictionary(other)
            for lang in ("ar", "en"):
                assert not set(mine[lang]) & set(theirs[lang]), (other, lang, set(mine[lang]) & set(theirs[lang]))

    def test_the_dictionary_is_merged_after_the_others_and_every_screen_key_exists(self):
        merged = (UTILS / "salesTranslations.js").read_text()
        assert "...reportsTranslations.ar" in merged and "...reportsTranslations.en" in merged
        d = _dictionary("salesReportsTranslations.js")
        # every kpi_/def_/basis_/range_ key the UI derives from a metric or a range name exists (a missing one would show its key)
        for kpi in S.DashboardKpis.model_fields:
            assert f"kpi_{kpi}" in d["ar"], kpi
        for basis in ("cohort", "event", "now"):
            assert f"basis_{basis}" in d["ar"] and f"basis_{basis}_hint" in d["ar"]
        for name in M.RANGES:
            assert f"range_{name}" in d["ar"], name
        js = (UTILS / "salesReports.js").read_text()
        assert [r for r in re.findall(r"RANGES = \[([^\]]*)\]", js)[0].replace("'", "").replace(" ", "").split(",")] == list(M.RANGES)
        assert f"MAX_PERIOD_DAYS = {M.MAX_PERIOD_DAYS}" in js                       # the client's friendly check mirrors the server's limit


# =============================================================================
# the application timezone (Baghdad, UTC+3): what a calendar date means
# =============================================================================
from sales import timezone as T


def at(y, mo, d, h=0, mi=0, s=0, us=0):
    return datetime(y, mo, d, h, mi, s, us, tzinfo=timezone.utc)


class TestApplicationTimezone:
    def test_the_zone_is_a_fixed_utc_plus_3_with_no_dependency_on_tzdata(self):
        assert T.APP_UTC_OFFSET == timedelta(hours=3) and T.APP_UTC_OFFSET_HOURS == 3
        assert T.APP_TZ.utcoffset(None) == timedelta(hours=3) and T.APP_TZ_NAME == "Asia/Baghdad"
        assert "zoneinfo" not in Path(T.__file__).read_text().replace("tzdata being installed", "")      # a fixed offset: nothing to install

    @pytest.mark.parametrize("now,expected", [
        (at(2026, 9, 24, 20, 59, 59, 999999), date(2026, 9, 24)),       # 23:59:59.999999 in Baghdad: still the 24th
        (at(2026, 9, 24, 21, 0, 0), date(2026, 9, 25)),                 # midnight in Baghdad: the 25th has begun
        (at(2026, 9, 25, 0, 30), date(2026, 9, 25)),                    # 03:30 local
        (at(2026, 12, 31, 21, 0, 0), date(2027, 1, 1)),                 # the new year arrives at 21:00 UTC on Dec 31st
        (at(2026, 12, 31, 20, 59, 59), date(2026, 12, 31)),
        (at(2026, 2, 28, 21, 0, 0), date(2026, 3, 1)),                  # month end, non-leap
        (at(2028, 2, 28, 21, 0, 0), date(2028, 2, 29)),                 # ... and in a leap year the 29th exists
    ])
    def test_today_flips_at_midnight_in_baghdad_not_at_midnight_utc(self, now, expected):
        assert T.app_today(now) == expected

    def test_a_day_starts_three_hours_before_utc_midnight_and_a_period_is_half_open(self):
        assert T.day_start(date(2026, 9, 25)) == at(2026, 9, 24, 21)
        assert T.day_start(date(2026, 9, 25)).utcoffset() == timedelta(hours=3)
        p = period("custom", date(2026, 9, 25), date(2026, 9, 27))
        assert p.start == at(2026, 9, 24, 21) and p.end == at(2026, 9, 27, 21)                 # [25th 00:00, 28th 00:00) local
        assert p.end - p.start == timedelta(days=3)

    @pytest.mark.parametrize("now,range_,expected", [
        # today
        (at(2026, 9, 24, 20, 59, 59), "today", ("2026-09-24", "2026-09-24")),
        (at(2026, 9, 24, 21, 0, 0), "today", ("2026-09-25", "2026-09-25")),
        # this week (Mon..Sun): Sunday 23:59:59 local is still the old week, Monday 00:00 local starts the next
        (at(2026, 9, 27, 20, 59, 59), "this_week", ("2026-09-21", "2026-09-27")),
        (at(2026, 9, 27, 21, 0, 0), "this_week", ("2026-09-28", "2026-10-04")),
        # this month / last month across a month end
        (at(2026, 9, 30, 20, 59, 59), "this_month", ("2026-09-01", "2026-09-30")),
        (at(2026, 9, 30, 21, 0, 0), "this_month", ("2026-10-01", "2026-10-31")),
        (at(2026, 9, 30, 21, 0, 0), "last_month", ("2026-09-01", "2026-09-30")),
        (at(2026, 9, 30, 20, 59, 59), "last_month", ("2026-08-01", "2026-08-31")),
        # across the year
        (at(2026, 12, 31, 21, 0, 0), "this_month", ("2027-01-01", "2027-01-31")),
        (at(2026, 12, 31, 21, 0, 0), "last_month", ("2026-12-01", "2026-12-31")),
        (at(2026, 12, 31, 21, 0, 0), "this_week", ("2026-12-28", "2027-01-03")),                  # Fri Jan 1 2027 local
    ])
    def test_every_preset_around_a_midnight_boundary(self, now, range_, expected):
        assert bounds(M.resolve_period(range_, None, None, T.app_today(now))) == expected

    def test_the_route_reads_today_from_the_application_timezone(self, monkeypatch):
        from sales import report_params as RP

        monkeypatch.setattr(RP, "app_today", lambda: date(2026, 9, 25))         # 00:30 in Baghdad, still Sep 24th in UTC
        assert bounds(RP.PeriodParams(range="today", date_from=None, date_to=None).to_period()) == ("2026-09-25", "2026-09-25")
        assert bounds(RP.PeriodParams(range=None, date_from=None, date_to=None).to_period()) == ("2026-09-01", "2026-09-30")

    def test_the_date_filters_of_the_lists_use_the_same_boundaries(self):
        from sales.repositories import leads as leads_repo
        from sales.repositories import work
        from sqlalchemy import true

        lower, upper = work.day_bounds(date(2026, 9, 25), date(2026, 9, 25))
        assert (lower, upper) == (at(2026, 9, 24, 21), at(2026, 9, 25, 21))
        assert work.day_bounds(None, None) == (None, None) and work.day_bounds(date(2026, 9, 25), None)[1] is None
        conds = leads_repo._conditions(true(), leads_repo.LeadFilters(created_from=date(2026, 9, 25), created_to=date(2026, 9, 25)))
        values = [v for c in conds for v in c.compile().params.values() if isinstance(v, datetime)]       # (a list: bind names repeat)
        assert sorted(values) == [at(2026, 9, 24, 21), at(2026, 9, 25, 21)]

    def test_the_trend_days_are_days_of_the_application_timezone(self):
        from sales.repositories import reports as repo_reports
        import inspect

        source = inspect.getsource(repo_reports.conversion_series)
        assert "APP_UTC_OFFSET_HOURS" in source and "interval" in source

    def test_no_sales_code_builds_a_day_boundary_from_utc_midnight_any_more(self):
        offenders = []
        for path in (BACKEND / "sales").rglob("*.py"):
            if path.name == "timezone.py":
                continue
            code = re.sub(r'""".*?"""', "", path.read_text(), flags=re.S)
            if re.search(r"datetime\.combine\([^)]*tzinfo=timezone\.utc", code) or re.search(r"time\.min[^\n]*timezone\.utc", code):
                offenders.append(path.name)
        assert offenders == [], f"a UTC-midnight day boundary is back in: {offenders}"
