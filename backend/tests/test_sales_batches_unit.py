"""JAZ Sales - batches, wait list, search and performance: pure unit tests (no HTTP, no database rows).

The arithmetic (percentages, periods, attempt paths), the closed vocabularies staying identical across code / schemas / migration,
and the structural promises that no test of behaviour can pin as well as reading the source: the Sales Manager's routes are all
guarded by the batch / performance permission AND the all-leads scope, no Data Entry / Sales Employee response shape can carry a
batch, and nothing in the application rewrites who entered a lead.
"""
from sales_test_utils import assert_scratch_target

assert_scratch_target(need_http=False)

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from sales import batch_schemas as B
from sales import constants as C
from sales import lead_schemas as LS
from sales import permissions as P
from sales import setup_schemas as SS
from sales.services import activity as A
from sales.services import batch_views as V
from sales.services import batches as batches_service
from sales.services.access import StaffContext

BACKEND = Path(__file__).resolve().parents[1]
ROUTER = (BACKEND / "sales" / "router.py").read_text()
MIGRATION = (BACKEND / "migrations" / "versions" / "f2b6d8a1c4e9_sales_batches_and_performance.py").read_text()


def ctx(*keys):
    return StaffContext({"id": str(uuid.uuid4())}, uuid.uuid4(), False, (), frozenset(keys))


# =============================================================================
# constants and vocabularies
# =============================================================================
class TestConstants:
    def test_the_sizes_are_the_specified_ones(self):
        assert C.DATA_BATCH_SIZE == 100 and C.MASTER_BATCH_SIZE == 10 and C.WORK_BATCH_SIZE == 100
        assert C.SESSION_GAP == timedelta(minutes=15) and C.SESSION_TAIL_CREDIT == timedelta(minutes=1)
        assert C.PERFORMANCE_PERIODS == ("today", "month", "year", "lifetime")

    def test_the_vocabularies_match_the_migration(self):
        for vocab, name in (
            (C.DATA_BATCH_STATUSES, "_DATA_STATUSES"), (C.WORK_BATCH_STATUSES, "_WORK_STATUSES"), (C.BATCH_ATTEMPT_KINDS, "_KINDS"),
            (C.FIRST_OUTCOMES, "_OUTCOMES"), (C.ATTEMPT_RESULTS, "_RESULTS"),
        ):
            literal = re.search(rf'{name} = "(\([^"]+\))"', MIGRATION).group(1)
            assert set(re.findall(r"'([a-z_]+)'", literal)) == set(vocab), name

    def test_the_hardcoded_100_in_the_migration_is_the_batch_size(self):
        assert "lead_count <= 100" in MIGRATION and "lead_count = 100" in MIGRATION
        assert C.DATA_BATCH_SIZE == 100

    def test_the_paths_are_the_ones_the_ui_names(self):
        assert set(C.ATTEMPT_PATHS) == {"direct_accepted", "direct_rejected", "wait_accepted", "wait_rejected", "wait_pending", "released"}


# =============================================================================
# statistics arithmetic
# =============================================================================
def counts(**over):
    base = dict(total=0, accepted_direct=0, rejected_direct=0, wait_listed=0, wait_accepted=0, wait_rejected=0, wait_pending=0, released=0)
    base.update(over)
    return base


class TestStatistics:
    def test_an_empty_set_is_all_zero_never_a_division_error(self):
        s = V.stats_out(None)
        assert s["total"] == s["decided"] == 0 and s["pct_accepted"] == s["pct_rejected"] == s["wait_list_rate"] == 0.0
        assert V.stats_out({})["decided"] == 0

    def test_the_split_and_the_percentages(self):
        s = V.stats_out(counts(total=100, accepted_direct=30, rejected_direct=30, wait_listed=40, wait_accepted=25, wait_rejected=15))
        assert (s["accepted"], s["rejected"], s["decided"]) == (55, 45, 100)
        assert (s["pct_accepted_direct"], s["pct_rejected_direct"], s["pct_wait_accepted"], s["pct_wait_rejected"]) == (30.0, 30.0, 25.0, 15.0)
        assert (s["pct_accepted"], s["pct_rejected"], s["wait_list_rate"]) == (55.0, 45.0, 40.0)
        assert s["pct_accepted_direct"] + s["pct_rejected_direct"] + s["pct_wait_accepted"] + s["pct_wait_rejected"] == 100.0

    def test_pending_leads_are_not_decided(self):
        s = V.stats_out(counts(total=10, accepted_direct=2, wait_listed=8, wait_pending=8))
        assert s["decided"] == 2 and s["pct_accepted"] == 100.0 and s["wait_list_rate"] == 80.0 and s["wait_pending"] == 8

    def test_percentages_are_of_the_decided_and_round_to_one_decimal(self):
        s = V.stats_out(counts(total=7, accepted_direct=1, rejected_direct=2))
        assert (s["pct_accepted_direct"], s["pct_rejected_direct"]) == (33.3, 66.7)

    def test_a_reassigned_attempt_counts_the_leads_nobody_has_decided_yet(self):
        assert V.stats_out(counts(total=15, accepted_direct=15), lead_count=45)["not_started"] == 30
        assert V.stats_out(counts(total=45), lead_count=45)["not_started"] == 0
        assert V.stats_out(counts(total=50), lead_count=45)["not_started"] == 0                # never negative
        assert V.stats_out(counts(total=3))["not_started"] == 0

    def test_a_persons_period_uses_worked_as_the_total(self):
        s = V._performance_stats({"worked": 5, "accepted_direct": 1, "rejected_direct": 1, "wait_listed": 3, "wait_accepted": 1, "wait_rejected": 1, "wait_pending": 1})
        assert s["total"] == 5 and s["released"] == 0 and (s["accepted"], s["rejected"], s["decided"]) == (2, 2, 4)
        assert V._performance_stats(None)["total"] == 0

    @pytest.mark.parametrize("first,result,path", [
        ("accepted", "accepted", "direct_accepted"), ("rejected", "rejected", "direct_rejected"),
        ("wait_list", "accepted", "wait_accepted"), ("wait_list", "rejected", "wait_rejected"),
        ("wait_list", None, "wait_pending"), ("wait_list", "released", "released"),
    ])
    def test_the_path_of_an_attempt(self, first, result, path):
        assert V.attempt_path(first, result) == path and path in C.ATTEMPT_PATHS

    def test_hours_are_rounded_to_hundredths(self):
        assert V._hours(0) == 0 and V._hours(None) == 0 and V._hours(3600) == 1.0 and V._hours(5400) == 1.5 and V._hours(61) == 0.02


class TestPeriods:
    def test_today_this_month_this_year_and_lifetime_in_the_application_timezone(self):
        now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)                                   # 15:00 in Baghdad
        p = {x.key: x for x in V.periods(now)}
        assert list(p) == ["today", "month", "year", "lifetime"]
        assert p["today"].start == datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc) and p["today"].end == datetime(2026, 9, 26, 21, 0, tzinfo=timezone.utc)
        assert p["month"].start == datetime(2026, 8, 31, 21, 0, tzinfo=timezone.utc) and p["month"].end == datetime(2026, 9, 30, 21, 0, tzinfo=timezone.utc)
        assert p["year"].start == datetime(2025, 12, 31, 21, 0, tzinfo=timezone.utc) and p["year"].end == datetime(2026, 12, 31, 21, 0, tzinfo=timezone.utc)
        assert p["lifetime"].start is None and p["lifetime"].end is None

    def test_after_utc_evening_the_baghdad_day_has_already_rolled_over(self):
        now = datetime(2026, 12, 31, 22, 30, tzinfo=timezone.utc)                                 # 01:30 on 1 January in Baghdad
        p = {x.key: x for x in V.periods(now)}
        assert p["today"].start == datetime(2026, 12, 31, 21, 0, tzinfo=timezone.utc)
        assert p["month"].start == datetime(2026, 12, 31, 21, 0, tzinfo=timezone.utc) and p["month"].end == datetime(2027, 1, 31, 21, 0, tzinfo=timezone.utc)
        assert p["year"].start == datetime(2026, 12, 31, 21, 0, tzinfo=timezone.utc) and p["year"].end == datetime(2027, 12, 31, 21, 0, tzinfo=timezone.utc)

    def test_december_rolls_into_january(self):
        now = datetime(2026, 12, 15, 9, 0, tzinfo=timezone.utc)
        p = {x.key: x for x in V.periods(now)}
        assert p["month"].end == datetime(2026, 12, 31, 21, 0, tzinfo=timezone.utc)

    def test_the_periods_nest(self):
        p = {x.key: x for x in V.periods(datetime(2026, 2, 28, 22, 0, tzinfo=timezone.utc))}
        assert p["year"].start <= p["month"].start <= p["today"].start < p["today"].end <= p["month"].end <= p["year"].end


# =============================================================================
# who is Data Entry, who sees the wait list
# =============================================================================
class TestPermissionDecisions:
    def test_data_entry_is_the_intake_scope_without_the_all_leads_scope(self):
        assert batches_service.enters_data_batch(ctx(P.PERM_LEADS_SCOPE_INTAKE))
        assert not batches_service.enters_data_batch(ctx(P.PERM_LEADS_SCOPE_INTAKE, P.PERM_LEADS_SCOPE_ALL))
        assert not batches_service.enters_data_batch(ctx(P.PERM_LEADS_SCOPE_ASSIGNED))
        assert not batches_service.enters_data_batch(ctx())
        assert not batches_service.enters_data_batch(StaffContext({"id": "x"}, uuid.uuid4(), True, (), frozenset(P.ALL_PERMISSIONS)))   # Super Admin

    def test_the_wait_list_event_is_read_by_deciders_only(self):
        assert "lead_wait_listed" in A.visible_event_types(ctx(P.PERM_LEADS_CHANGE_STAGE))
        assert "lead_wait_listed" not in A.visible_event_types(ctx())
        assert "lead_wait_listed" not in A.visible_event_types(ctx(P.PERM_LEADS_VIEW, P.PERM_LEADS_SCOPE_INTAKE, P.PERM_LEADS_CREATE))   # Data Entry
        assert "lead_wait_listed" not in A.LEAD_EVENT_TYPES

    def test_the_new_permissions_and_modules(self):
        assert (P.PERM_BATCHES_VIEW, P.PERM_BATCHES_REASSIGN, P.PERM_PERFORMANCE_VIEW) == ("sales.batches.view", "sales.batches.reassign", "sales.performance.view")
        assert all(P.ALL_PERMISSIONS[k].strip() for k in (P.PERM_BATCHES_VIEW, P.PERM_BATCHES_REASSIGN, P.PERM_PERFORMANCE_VIEW))
        by_key = {m["key"]: m["permission"] for m in P.MODULES}
        assert by_key["batches"] == P.PERM_BATCHES_VIEW and by_key["performance"] == P.PERM_PERFORMANCE_VIEW
        assert [m["key"] for m in P.MODULES][-2:] == ["batches", "performance"]


# =============================================================================
# the routes
# =============================================================================
def route_block():
    start = ROUTER.index("# Batches, search and performance - the Sales Manager only")
    end = ROUTER.index("# Phase 5 - dashboard and reports")
    return ROUTER[start:end]


class TestRoutesAreTheManagersAlone:
    def routes(self):
        block = route_block()
        found = re.findall(r'@sales_router\.(\w+)\("([^"]+)"[^\n]*\n(?:async )?def (\w+)\((.*?)\n\):', block, flags=re.S)
        assert found, "no route found in the batches block"
        return [(m, path, name, args) for m, path, name, args in found]

    def test_every_route_is_guarded_by_its_permission_and_the_all_leads_scope(self):
        for method, path, name, args in self.routes():
            guard = re.search(r"require_permission\(([^)]*)\)", args)
            assert guard, f"{method} {path} has no permission guard"
            keys = guard.group(1)
            assert "_BATCH_VIEW" in keys or "_PERFORMANCE_VIEW" in keys or ("PERM_BATCHES_REASSIGN" in keys and "PERM_LEADS_SCOPE_ALL" in keys and "PERM_BATCHES_VIEW" in keys), (method, path, keys)

    def test_the_two_guard_tuples_demand_the_scope(self):
        assert "_BATCH_VIEW = (perms.PERM_BATCHES_VIEW, perms.PERM_LEADS_SCOPE_ALL)" in ROUTER
        assert "_PERFORMANCE_VIEW = (perms.PERM_PERFORMANCE_VIEW, perms.PERM_LEADS_SCOPE_ALL)" in ROUTER

    def test_only_the_reassignment_writes(self):
        writes = [(m, p) for m, p, _, _ in self.routes() if m != "get"]
        assert writes == [("post", "/batches/work/{batch_id}/reassign")]

    def test_the_routes_are_the_documented_ones(self):
        assert {(m, p) for m, p, _, _ in self.routes()} == {
            ("get", "/batches/overview"), ("get", "/batches/data"), ("get", "/batches/data/{batch_id}"), ("get", "/batches/master"),
            ("get", "/batches/master/{batch_id}"), ("get", "/batches/work"), ("get", "/batches/work/{batch_id}"),
            ("post", "/batches/work/{batch_id}/reassign"), ("get", "/batches/search"), ("get", "/batches/trace/{lead_id}"),
            ("get", "/performance/data-entry"), ("get", "/performance/sales"), ("get", "/performance/employees/{user_id}"),
            ("get", "/performance/employees/{user_id}/history"),
        }

    def test_the_wait_list_route_is_the_deciders_and_the_only_new_lead_route(self):
        m = re.search(r'@sales_router\.post\("/leads/\{lead_id\}/wait-list".*?\n\):', ROUTER, flags=re.S)
        assert m and "require_permission(perms.PERM_LEADS_CHANGE_STAGE)" in m.group(0)

    def test_the_manager_views_are_not_used_by_any_other_module(self):
        importers = sorted(p.name for p in (BACKEND / "sales").rglob("*.py") if "batch_views" in p.read_text() and p.name != "batch_views.py")
        assert importers == ["router.py"]
        assert "batches_repo" not in (BACKEND / "sales" / "router.py").read_text()

    def test_the_write_side_is_reached_only_from_the_lead_operations_and_the_reassignment(self):
        callers = sorted(p.name for p in (BACKEND / "sales").rglob("*.py")
                         if re.search(r"services import batches as batches_service|from sales.services import batches\b", p.read_text()))
        assert callers == ["batch_views.py", "customer_setup.py", "leads.py", "router.py"]      # router: settle() after a decision


# =============================================================================
# no batch reaches a Data Entry / Sales Employee response
# =============================================================================
def field_names(model, seen=None):
    seen = set() if seen is None else seen
    if model in seen:
        return set()
    seen.add(model)
    names = set()
    for name, field in model.model_fields.items():
        names.add(name)
        annotation = field.annotation
        for arg in [annotation, *getattr(annotation, "__args__", ())]:
            inner = getattr(arg, "__args__", None)
            for candidate in ([arg] + list(inner or ())):
                if isinstance(candidate, type) and hasattr(candidate, "model_fields"):
                    names |= field_names(candidate, seen)
    return names


class TestNothingLeaksToLeadAndQueueResponses:
    SHARED = (LS.LeadSummary, LS.LeadDetail, LS.LeadList, LS.LeadResult, LS.LeadCan, SS.QueueOut, LS.ActivityList, LS.BulkAssignResult)

    def test_no_lead_shape_carries_a_batch_a_master_an_attempt_or_a_session(self):
        for model in self.SHARED:
            leaked = {n for n in field_names(model) if any(w in n.lower() for w in ("batch", "master", "attempt", "session"))}
            assert leaked == set(), (model.__name__, leaked)

    def test_the_only_wait_list_state_in_a_lead_is_the_stamp_and_the_flag(self):
        added = set(LS.LeadDetail.model_fields) & {"wait_listed_at"}
        assert added == {"wait_listed_at"} and "wait_list" in LS.LeadCan.model_fields
        assert LS.LeadCan.model_fields["wait_list"].default is False

    def test_lead_out_never_puts_the_batch_id_in_a_response(self):
        source = (BACKEND / "sales" / "services" / "leads.py").read_text()
        body = source[source.index("def lead_out"):source.index("async def _recent_ids")]
        assert "data_batch" not in body

    def test_the_lead_summary_columns_do_not_list_the_batch_column(self):
        source = (BACKEND / "sales" / "services" / "leads.py").read_text()
        columns = source[source.index("_SUMMARY_COLUMNS = ("):source.index("def lead_out")]
        assert "data_batch" not in columns

    def test_the_export_never_includes_the_batch(self):
        assert "data_batch" not in (BACKEND / "sales" / "services" / "export.py").read_text()


class TestManagerShapes:
    def test_no_shape_carries_a_secret_or_a_normalised_column(self):
        for name in dir(B):
            model = getattr(B, name)
            if isinstance(model, type) and hasattr(model, "model_fields") and model.__module__ == B.__name__:
                bad = {n for n in field_names(model) if n.endswith("_norm") or any(w in n.lower() for w in ("password", "hash", "token", "secret"))}
                assert bad == set(), (name, bad)

    def test_the_reassignment_body_is_strict(self):
        assert B.ReassignBatch(employee_id=uuid.uuid4())
        with pytest.raises(ValidationError):
            B.ReassignBatch(employee_id=uuid.uuid4(), status="closed")
        with pytest.raises(ValidationError):
            B.ReassignBatch(employee_id="not-a-uuid")
        with pytest.raises(ValidationError):
            B.ReassignBatch()

    def test_the_stats_shape_names_the_four_way_split(self):
        assert {"accepted_direct", "rejected_direct", "wait_accepted", "wait_rejected", "pct_accepted_direct", "pct_rejected_direct",
                "pct_wait_accepted", "pct_wait_rejected", "wait_list_rate", "not_started"} <= set(B.AttemptStats.model_fields)


# =============================================================================
# who entered a lead is never rewritten by the application
# =============================================================================
class TestEntryIsPermanentInTheCode:
    def code(self):
        return {p: p.read_text() for p in (BACKEND / "sales").rglob("*.py")}

    def test_no_application_code_assigns_created_by_or_created_at_on_an_existing_lead(self):
        for path, text in self.code().items():
            assert not re.search(r"\blead\.(created_by|created_at)\s*=", text), path.name
            assert not re.search(r"\b(created_by|created_at)\s*=\s*.*\bwhere\(SalesLead", text), path.name

    def test_the_batch_id_is_assigned_in_exactly_one_place(self):
        places = [p.name for p, text in self.code().items() if re.search(r"\blead\.data_batch_id\s*=", text)]
        assert places == ["leads.py"]
        source = (BACKEND / "sales" / "services" / "leads.py").read_text()
        assert source.count("lead.data_batch_id =") == 1
        create = source[source.index("async def create_lead"):source.index("def _person")]
        assert "lead.data_batch_id = await batches_service.take_data_batch_slot(db)" in create        # only when a lead is CREATED

    def test_the_update_path_cannot_carry_these_fields(self):
        assert "created_by" not in LS.LeadUpdate.model_fields and "created_at" not in LS.LeadUpdate.model_fields
        assert "data_batch_id" not in LS.LeadUpdate.model_fields and "data_batch_id" not in LS.LeadCreate.model_fields
        from sales.repositories.leads import RAW_FIELDS
        assert not {"created_by", "created_at", "data_batch_id"} & set(RAW_FIELDS)

    def test_every_timeline_event_is_also_a_moment_of_work(self):
        source = (BACKEND / "sales" / "services" / "activity.py").read_text()
        record = source[source.index("async def record"):source.index("async def list_events")]
        assert "await work_sessions.touch(db, actor.user_id)" in record

    def test_a_session_write_never_waits_for_a_lock(self):
        source = (BACKEND / "sales" / "services" / "work_sessions.py").read_text()
        assert "pg_try_advisory_xact_lock" in source and "pg_advisory_xact_lock(" not in source.replace("pg_try_advisory_xact_lock(", "")

    def test_work_batch_formation_never_runs_inside_a_decision(self):
        """Forming takes a BLOCKING per-person lock and the pool leads' KEY SHARE locks, so it must run holding no lead lock: only
        form_due_work_batches takes that lock, and it is called only by settle() (after the decision committed) and the catch-up."""
        source = (BACKEND / "sales" / "services" / "batches.py").read_text()
        assert source.count("pg_advisory_xact_lock(") == 1
        body = source[source.index("async def form_due_work_batches"):source.index("async def settle")]
        assert "pg_advisory_xact_lock(" in body
        callers = [m.start() for m in re.finditer(r"await form_due_work_batches\(", source)]
        assert len(callers) == 2
        for pos in callers:
            enclosing = source.rfind("\nasync def ", 0, pos)
            assert source[enclosing:enclosing + 60].split("(")[0].strip().endswith(("settle", "form_all_due_work_batches")), source[enclosing:enclosing + 60]
        new_attempt = source[source.index("async def _new_attempt"):source.index("async def _resolve")]
        assert "form_due_work_batches" not in new_attempt and "_form_one" not in new_attempt
        assert "db.info.setdefault(_POOL_KEY" in new_attempt
        router = (BACKEND / "sales" / "router.py").read_text()
        for route in ('"/leads/{lead_id}/stage"', '"/leads/{lead_id}/wait-list"', '"/leads/{lead_id}/setup"'):
            block = router[router.index(route):]
            block = block[:block.index("@sales_router", 10)]
            assert "await batches_service.settle(db)" in block, route
