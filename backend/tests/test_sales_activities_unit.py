"""JAZ Sales - Phase 3: unit tier - no HTTP, no database writes.

The vocabulary (call results, statuses), the request schemas, the permission catalog, the timeline's event vocabulary and
who may read which events, the query-string filters, the small shared helpers, and the DRIFT GUARDS that keep the
code, the migration and the test expectations saying the same thing. Also a few static guarantees over the source
(no hard deletes of work items; a Sales trial never touches the platform's subscriptions).
"""
from sales_test_utils import ALL_MODULES, ALL_PERMISSION_KEYS, SYSTEM_ROLE_PERMISSIONS, assert_scratch_target

assert_scratch_target(need_http=False)  # must run BEFORE importing database (which loads .env)

import importlib.util
import re
import typing
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from sales import activity_params as params
from sales import activity_schemas as S
from sales import constants as C
from sales import permissions as P
from sales.repositories import work as work_repo
from sales.services import activity as A
from sales.services import work
from sales.services.access import StaffContext

BACKEND = Path(__file__).resolve().parent.parent
KINDS = ("calls", "followups", "demos", "trials")
LEAD_ID = "00000000-0000-4000-8000-000000000000"


def ctx(*permissions, super_admin=False) -> StaffContext:
    return StaffContext({"id": "x", "role": "jaz_staff", "status": "active"}, uuid.uuid4(), super_admin, (), frozenset(permissions))


def load_migration():
    path = BACKEND / "migrations" / "versions" / "e3a9c5b07d12_sales_activities.py"
    spec = importlib.util.spec_from_file_location("mig_e3a9c5b07d12", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =============================================================================
class TestVocabulary:
    def test_the_schemas_the_constants_and_the_models_agree(self):
        assert typing.get_args(S.CallResult) == C.CALL_RESULTS
        assert typing.get_args(S.FollowupStatus) == C.FOLLOWUP_STATUSES
        assert typing.get_args(S.DemoStatus) == C.DEMO_STATUSES
        assert typing.get_args(S.TrialStatus) == C.TRIAL_STATUSES

    def test_the_call_results_are_exactly_the_specified_seven(self):
        assert set(C.CALL_RESULTS) == {"answered", "no_answer", "busy", "wrong_number", "interested", "not_interested", "call_back"}

    def test_the_statuses_are_exactly_the_specified_ones(self):
        assert C.FOLLOWUP_STATUSES == ("pending", "completed", "cancelled")
        assert C.DEMO_STATUSES == ("scheduled", "completed", "cancelled", "no_show")
        assert C.TRIAL_STATUSES == ("active", "completed", "cancelled")

    def test_every_kind_has_one_open_status_and_the_rest_are_terminal(self):
        for statuses, open_status in ((C.FOLLOWUP_STATUSES, C.FOLLOWUP_OPEN), (C.DEMO_STATUSES, C.DEMO_OPEN), (C.TRIAL_STATUSES, C.TRIAL_OPEN)):
            assert open_status in statuses and statuses[0] == open_status and len(set(statuses)) == len(statuses)

    def test_model_check_constraints_match_the_constants(self):
        from sales.models import SalesCall, SalesDemo, SalesFollowup, SalesTrial
        checks = {c.name: str(c.sqltext) for t in (SalesCall, SalesFollowup, SalesDemo, SalesTrial) for c in t.__table__.constraints if c.name and c.name.startswith("ck_")}
        for result in C.CALL_RESULTS:
            assert f"'{result}'" in checks["ck_sales_calls_result"]
        for status in C.FOLLOWUP_STATUSES:
            assert f"'{status}'" in checks["ck_sales_followups_status"]
        for status in C.DEMO_STATUSES:
            assert f"'{status}'" in checks["ck_sales_demos_status"]
        for status in C.TRIAL_STATUSES:
            assert f"'{status}'" in checks["ck_sales_trials_status"]
        assert str(C.MAX_CALL_SECONDS) in checks["ck_sales_calls_duration"]

    def test_the_time_constants_are_sane(self):
        assert timedelta(0) < C.CLOCK_SKEW_TOLERANCE <= timedelta(minutes=15)
        assert C.MIN_TIMESTAMP < datetime.now(timezone.utc) < C.MAX_TIMESTAMP
        assert 1 <= C.DEFAULT_ENDING_SOON_DAYS <= C.MAX_ENDING_SOON_DAYS
        assert C.MAX_CALL_SECONDS == 86400


# =============================================================================
class TestPermissionCatalog:
    PHASE3 = {f"sales.{k}.{a}" for k in KINDS for a in ("view", "manage")}

    def test_every_kind_has_view_and_manage_named_the_usual_way(self):
        assert self.PHASE3 <= set(P.ALL_PERMISSIONS)
        for key in self.PHASE3:
            assert re.fullmatch(r"sales\.(calls|followups|demos|trials)\.(view|manage)", key)
            assert P.ALL_PERMISSIONS[key].strip()
        assert len({P.ALL_PERMISSIONS[k] for k in self.PHASE3}) == len(self.PHASE3)          # no two share a description

    def test_the_catalog_matches_the_test_expectations(self):
        assert set(P.ALL_PERMISSIONS) == ALL_PERMISSION_KEYS
        assert set(P.ALL_PERMISSIONS) >= self.PHASE3

    def test_workspace_modules_point_at_real_view_permissions(self):
        assert {m["permission"] for m in P.MODULES} <= set(P.ALL_PERMISSIONS)
        assert [m["key"] for m in P.MODULES] == ALL_MODULES
        by_key = {m["key"]: m["permission"] for m in P.MODULES}
        for kind in KINDS:
            assert by_key[kind] == f"sales.{kind}.view"

    def test_no_staff_management_permission_was_granted_by_this_phase(self):
        for role, keys in SYSTEM_ROLE_PERMISSIONS.items():
            assert not {k for k in keys if k.startswith("sales.staff.")}, role

    def test_lead_data_entry_and_onboarding_get_no_phase_3_permission(self):
        for role in ("lead_data_entry", "onboarding_employee"):
            assert not self.PHASE3 & SYSTEM_ROLE_PERMISSIONS[role], role
        for role in ("sales_manager", "sales_employee"):
            assert self.PHASE3 <= SYSTEM_ROLE_PERMISSIONS[role], role


# =============================================================================
class TestMigrationDriftGuard:
    def test_the_migration_is_chained_after_the_leads_revision(self):
        mig = load_migration()
        assert mig.revision == "e3a9c5b07d12" and mig.down_revision == "d764d5f44e91"
        assert mig.branch_labels is None and mig.depends_on is None

    def test_the_migration_history_has_a_single_linear_head(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
        heads = script.get_heads()
        assert len(heads) == 1, heads                                                       # no branch: exactly one head
        chain = [rev.revision for rev in script.walk_revisions()]
        assert "e3a9c5b07d12" in chain and chain.index("e3a9c5b07d12") < chain.index("d764d5f44e91")   # walk_revisions is head -> base
        for rev in script.walk_revisions():
            assert not isinstance(rev.down_revision, tuple), rev.revision                   # nothing merges two branches either

    def test_the_frozen_seed_equals_the_code(self):
        """A migration freezes its values as literals (so it never changes meaning). This is the drift guard: the code
        catalogs, the test expectations and the migration must all say the same thing."""
        mig = load_migration()
        seeded = dict(mig._PERMISSIONS)
        assert set(seeded) == self_phase3()
        assert seeded == {k: P.ALL_PERMISSIONS[k] for k in seeded}                          # keys AND descriptions

        granted = {}
        for role, key in mig._GRANTS:
            granted.setdefault(role, []).append(key)
        assert set(granted) == {"sales_manager", "sales_employee"}                          # Lead Data Entry / Onboarding: nothing
        for role, keys in granted.items():
            assert sorted(keys) == sorted(set(keys)) and set(keys) == self_phase3(), role
            assert set(keys) == SYSTEM_ROLE_PERMISSIONS[role] & self_phase3()

        phase2_path = BACKEND / "migrations" / "versions" / "d764d5f44e91_sales_leads_foundation.py"
        spec = importlib.util.spec_from_file_location("mig_d764d5f44e91_for_roles", phase2_path)
        phase2 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(phase2)
        assert mig._ROLE_IDS == {r: phase2._ROLE_IDS[r] for r in ("sales_manager", "sales_employee")}    # the same seeded roles

    def test_the_frozen_check_literals_equal_the_code(self):
        mig = load_migration()
        for result in C.CALL_RESULTS:
            assert f"'{result}'" in mig._CALL_RESULTS
        assert mig._CALL_RESULTS.count("'") == 2 * len(C.CALL_RESULTS)                     # ... and nothing extra
        for statuses, literal in ((C.FOLLOWUP_STATUSES, mig._FOLLOWUP_STATUSES), (C.DEMO_STATUSES, mig._DEMO_STATUSES), (C.TRIAL_STATUSES, mig._TRIAL_STATUSES)):
            assert literal == "(" + ",".join(f"'{s}'" for s in statuses) + ")"
        assert mig._MAX_CALL_SECONDS == C.MAX_CALL_SECONDS

    def test_the_downgrade_refuses_while_work_history_exists_and_never_drops_leads(self):
        source = (BACKEND / "migrations" / "versions" / "e3a9c5b07d12_sales_activities.py").read_text()
        down = source[source.index("def downgrade()"):]
        assert "Refusing to downgrade" in down
        four = {"sales_calls", "sales_followups", "sales_demos", "sales_trials"}
        checked = set(re.findall(r"'(sales_[a-z]+)'", re.search(r"for table in \(([^)]*)\)", down).group(1)))
        assert checked == four                                                                # every one of the four is checked for rows
        assert set(re.findall(r"op\.drop_table\('([a-z_]+)'\)", down)) == four               # ... and exactly those four are dropped
        assert "sales_leads" not in down and "sales_lead_activities" not in down             # leads and their timeline are never touched

    def test_the_migration_touches_only_its_own_tables_and_the_rbac_catalog(self):
        source = (BACKEND / "migrations" / "versions" / "e3a9c5b07d12_sales_activities.py").read_text()
        created = set(re.findall(r"op\.create_table\(\s*'([a-z_]+)'", source))
        assert created == {"sales_calls", "sales_followups", "sales_demos", "sales_trials"}
        assert not re.findall(r"op\.(alter_column|add_column|drop_column|drop_index|drop_constraint)\(", source)     # no existing table is altered
        assert set(re.findall(r"sa\.table\(\s*'([a-z_]+)'", source)) == {"staff_permissions", "staff_role_permissions"}
        assert "Hand-written" in source.split('"""')[1] and "autogenerate" not in source.split('"""')[2]      # the header says so; the code never calls it


def self_phase3():
    return {f"sales.{k}.{a}" for k in KINDS for a in ("view", "manage")}


# =============================================================================
class TestRequestSchemas:
    def call(self, **kw):
        return S.CallCreate(**{"lead_id": LEAD_ID, "result": "answered", **kw})

    def test_a_valid_call_and_its_defaults(self):
        c = self.call()
        assert c.called_at is None and c.duration_seconds is None and c.notes is None

    def test_timestamps_must_carry_a_timezone_and_be_sane(self):
        assert self.call(called_at="2026-09-25T10:00:00Z").called_at.utcoffset() == timedelta(0)
        assert self.call(called_at="2026-09-25T10:00:00+03:00").called_at.utcoffset() == timedelta(hours=3)
        for bad in ("2026-09-25T10:00:00", "2026-09-25", "1999-12-31T23:59:59Z", "2100-01-02T00:00:00Z", "soon", 12345, ""):
            with pytest.raises(ValidationError):
                self.call(called_at=bad)

    @pytest.mark.parametrize("field,make", [
        ("due_at", lambda v: S.FollowupCreate(lead_id=LEAD_ID, due_at=v)),
        ("due_at", lambda v: S.FollowupUpdate(due_at=v)),
        ("scheduled_at", lambda v: S.DemoCreate(lead_id=LEAD_ID, scheduled_at=v)),
        ("scheduled_at", lambda v: S.DemoReschedule(scheduled_at=v)),
        ("expected_end_at", lambda v: S.TrialCreate(lead_id=LEAD_ID, expected_end_at=v)),
        ("expected_end_at", lambda v: S.TrialUpdate(expected_end_at=v)),
        ("started_at", lambda v: S.TrialCreate(lead_id=LEAD_ID, expected_end_at="2030-01-01T00:00:00Z", started_at=v)),
        ("actual_end_at", lambda v: S.TrialComplete(actual_end_at=v)),
    ])
    def test_every_timestamp_field_is_checked_the_same_way(self, field, make):
        assert make("2030-01-01T00:00:00Z") is not None
        for bad in ("2030-01-01T00:00:00", "1999-01-01T00:00:00Z", "2101-01-01T00:00:00Z"):
            with pytest.raises(ValidationError):
                make(bad)

    def test_required_fields(self):
        for factory in (lambda: S.CallCreate(lead_id=LEAD_ID), lambda: S.CallCreate(result="busy"), lambda: S.FollowupCreate(lead_id=LEAD_ID),
                        lambda: S.DemoCreate(lead_id=LEAD_ID), lambda: S.TrialCreate(lead_id=LEAD_ID), lambda: S.DemoReschedule()):
            with pytest.raises(ValidationError):
                factory()
        assert S.CallUpdate().model_dump(exclude_unset=True) == {}                            # an update needs nothing ...
        assert S.CallUpdate(notes=None).model_dump(exclude_unset=True) == {"notes": None}     # ... and tells "cleared" from "not sent"

    def test_unknown_fields_are_refused_everywhere(self):
        for model, base in ((S.CallCreate, {"lead_id": LEAD_ID, "result": "busy"}), (S.CallUpdate, {}), (S.FollowupCreate, {"lead_id": LEAD_ID, "due_at": "2030-01-01T00:00:00Z"}),
                            (S.FollowupUpdate, {}), (S.DemoCreate, {"lead_id": LEAD_ID, "scheduled_at": "2030-01-01T00:00:00Z"}), (S.DemoUpdate, {}),
                            (S.DemoReschedule, {"scheduled_at": "2030-01-01T00:00:00Z"}), (S.TrialCreate, {"lead_id": LEAD_ID, "expected_end_at": "2030-01-01T00:00:00Z"}),
                            (S.TrialUpdate, {}), (S.TrialComplete, {}), (S.ActionNote, {})):
            for extra in ("status", "employee_id", "created_by", "id", "completed_at", "lead"):
                with pytest.raises(ValidationError):
                    model(**base, **{extra: "x"})

    def test_the_fields_a_client_must_not_control_are_not_in_the_requests(self):
        forbidden = {"id", "status", "created_by", "created_at", "updated_at", "completed_at", "employee_id"}
        for model in (S.CallCreate, S.CallUpdate, S.FollowupCreate, S.FollowupUpdate, S.DemoCreate, S.DemoUpdate, S.DemoReschedule,
                      S.TrialCreate, S.TrialUpdate, S.TrialComplete, S.ActionNote):
            assert not forbidden & set(model.model_fields), model.__name__

    def test_notes_are_trimmed_blank_means_none_and_nul_and_length_are_refused(self):
        assert self.call(notes="  x  ").notes == "x" and self.call(notes="   ").notes is None
        assert self.call(notes="x" * 2000).notes == "x" * 2000
        for bad in ("x" * 2001, "a\x00b"):
            with pytest.raises(ValidationError):
                self.call(notes=bad)
        assert S.ActionNote(note="  ").note is None
        with pytest.raises(ValidationError):
            S.ActionNote(note="x" * 1001)

    def test_duration_bounds(self):
        for good in (0, 1, 86400):
            assert self.call(duration_seconds=good).duration_seconds == good
        for bad in (-1, 86401, 1.5, "x"):
            with pytest.raises(ValidationError):
                self.call(duration_seconds=bad)

    def test_results_and_ids_are_validated(self):
        for bad in ("great", "", None, "ANSWERED"):
            with pytest.raises(ValidationError):
                self.call(result=bad)
        for bad in ("not-a-uuid", "", None, 5):
            with pytest.raises(ValidationError):
                S.CallCreate(lead_id=bad, result="busy")

    def test_responses_declare_no_secret_looking_field(self):
        secret = re.compile(r"password|passwd|token|secret|hash|api_key|credential|authorization", re.I)
        for name in dir(S):
            model = getattr(S, name)
            if isinstance(model, type) and hasattr(model, "model_fields"):
                assert not [f for f in model.model_fields if secret.search(f)], (name, [f for f in model.model_fields if secret.search(f)])


# =============================================================================
class TestTimelineVocabularyAndVisibility:
    PHASE3_EVENTS = {
        "call_created", "call_updated",
        "followup_created", "followup_updated", "followup_completed", "followup_cancelled",
        "demo_scheduled", "demo_rescheduled", "demo_updated", "demo_completed", "demo_cancelled", "demo_no_show",
        "trial_started", "trial_updated", "trial_completed", "trial_cancelled",
    }
    PHASE4_EVENTS = {"lead_converted", "onboarding_assigned", "onboarding_stage_changed", "onboarding_notes_updated"}
    WAIT_LIST_EVENTS = {"lead_wait_listed"}       # f2b6d8a1c4e9: the salesperson's own working state - read by whoever decides on leads
    REQUIRED_BY_THE_SPEC = {
        "call_created", "call_updated", "followup_created", "followup_completed", "followup_cancelled", "demo_scheduled", "demo_rescheduled",
        "demo_completed", "demo_cancelled", "demo_no_show", "trial_started", "trial_completed", "trial_cancelled",
    }

    def test_the_vocabulary_contains_every_event_the_spec_names(self):
        assert self.REQUIRED_BY_THE_SPEC <= self.PHASE3_EVENTS
        constants = {v for k, v in vars(A).items() if k.startswith("EVENT_")}
        assert self.PHASE3_EVENTS <= constants

    def test_every_event_is_classified_exactly_once(self):
        constants = {v for k, v in vars(A).items() if k.startswith("EVENT_")}
        work_events = [t for types in A.WORK_EVENT_TYPES.values() for t in types]
        assert len(work_events) == len(set(work_events))                                     # no event under two permissions
        assert constants == set(A.LEAD_EVENT_TYPES) | set(work_events)                         # nothing unclassified ...
        assert not set(A.LEAD_EVENT_TYPES) & set(work_events)                                  # ... and no overlap
        assert set(work_events) == self.PHASE3_EVENTS | self.PHASE4_EVENTS | self.WAIT_LIST_EVENTS

    def test_event_names_fit_the_databases_format_check(self):
        for event in {v for k, v in vars(A).items() if k.startswith("EVENT_")}:
            assert re.fullmatch(r"[a-z][a-z0-9_]{2,63}", event), event

    def test_each_kinds_events_sit_under_that_kinds_view_permission(self):
        for permission, types in A.WORK_EVENT_TYPES.items():
            kind = permission.split(".")[1]
            if permission == P.PERM_LEADS_CHANGE_STAGE:       # the wait list: read by whoever may decide on leads (Data Entry may not)
                assert types == self.WAIT_LIST_EVENTS
                continue
            prefix = {"calls": "call_", "followups": "followup_", "demos": "demo_", "trials": "trial_", "customers": "lead_converted", "onboarding": "onboarding_"}[kind]
            assert permission.endswith(".view") and all(t.startswith(prefix) for t in types), permission

    def test_what_each_reader_may_see(self):
        assert A.visible_event_types(ctx()) == A.LEAD_EVENT_TYPES                              # holding nothing: only the lead's own events
        assert A.visible_event_types(ctx(*(f"sales.{k}.manage" for k in KINDS))) == A.LEAD_EVENT_TYPES     # `manage` alone reads nothing back
        for permission, types in A.WORK_EVENT_TYPES.items():
            assert A.visible_event_types(ctx(permission)) == A.LEAD_EVENT_TYPES | types, permission
        everything = A.LEAD_EVENT_TYPES | {t for types in A.WORK_EVENT_TYPES.values() for t in types}
        assert A.visible_event_types(ctx(*A.WORK_EVENT_TYPES)) == everything
        assert A.visible_event_types(ctx(*P.ALL_PERMISSIONS, super_admin=True)) == everything

    def test_an_unclassified_future_event_is_visible_to_nobody(self):
        assert "some_future_event" not in A.visible_event_types(ctx(*P.ALL_PERMISSIONS))      # fail closed: an allow-list, not a deny-list

    def test_the_payload_screening_still_refuses_secret_keys(self):
        """Phase-3 payloads go through the same record() screening as every other event."""
        import asyncio
        from sales.services.audit import new_audit_context

        async def _go(**kw):
            await A.record(None, ctx(), new_audit_context(), uuid.uuid4(), A.EVENT_CALL_CREATED, **kw)

        for key in ("password", "token", "Authorization", "refresh_token"):
            with pytest.raises(ValueError):
                asyncio.run(_go(after={"result": "busy", key: "x"}))
            with pytest.raises(ValueError):
                asyncio.run(_go(metadata={"nested": {key: "x"}}))


# =============================================================================
class TestQueryParameters:
    def test_choices(self):
        assert params._choices(None, C.CALL_RESULTS, "result") is None and params._choices("", C.CALL_RESULTS, "result") is None
        assert params._choices(" answered , busy ", C.CALL_RESULTS, "result") == ["answered", "busy"]
        assert params._choices(",", C.CALL_RESULTS, "result") is None
        with pytest.raises(HTTPException) as exc:
            params._choices("answered,great", C.CALL_RESULTS, "result")
        assert exc.value.status_code == 400 and exc.value.detail["field"] == "result" and "great" in exc.value.detail["message"]

    def test_person(self):
        me = ctx()
        assert params._person("me", me, "assigned_to") == me.user_id
        other = uuid.uuid4()
        assert params._person(str(other), me, "assigned_to") == other and params._person(None, me, "x") is None and params._person("", me, "x") is None
        for bad in ("nobody", "12345", "ME"):
            with pytest.raises(HTTPException) as exc:
                params._person(bad, me, "assigned_to")
            assert exc.value.status_code == 400 and exc.value.detail["field"] == "assigned_to"

    def test_each_kind_builds_its_filters(self):
        me = ctx()
        f = params.FollowupFilterParams(lead_id=None, assigned_to="me", status="pending,completed", due="overdue", due_from=date(2026, 1, 1), due_to=None, q="x").to_filters(me)
        assert f.assigned_to == me.user_id and f.statuses == ["pending", "completed"] and f.due == "overdue" and f.due_from == date(2026, 1, 1)
        d = params.DemoFilterParams(lead_id=None, assigned_to=None, status="cancelled,no_show", scheduled_from=None, scheduled_to=None, q=None).to_filters(me)
        assert d.statuses == ["cancelled", "no_show"] and d.assigned_to is None
        t = params.TrialFilterParams(lead_id=None, status="active", ending_within_days=3, ending_from=None, ending_to=None, q=None).to_filters(me)
        assert t.statuses == ["active"] and t.ending_within_days == 3
        c = params.CallFilterParams(lead_id=None, employee_id="me", result="busy", called_from=None, called_to=None, q=None).to_filters(me)
        assert c.employee_id == me.user_id and c.results == ["busy"]
        for bad in (lambda: params.FollowupFilterParams(None, None, "done", None, None, None, None).to_filters(me),
                    lambda: params.DemoFilterParams(None, None, "held", None, None, None).to_filters(me),
                    lambda: params.TrialFilterParams(None, "paused", None, None, None, None).to_filters(me),
                    lambda: params.CallFilterParams(None, None, "great", None, None, None).to_filters(me)):
            with pytest.raises(HTTPException):
                bad()


# =============================================================================
class TestSharedHelpers:
    def test_clip(self):
        assert work.clip(None) is None and work.clip("short") == "short"
        assert work.clip("x" * 1000) == "x" * 1000
        assert work.clip("x" * 1001) == "x" * 1000 + "…" and len(work.clip("x" * 5000)) == 1001
        assert work.clip("abcdef", limit=3) == "abc…"

    def test_changed_fields_ignores_what_did_not_change(self):
        class Item:
            a, b, c = 1, "x", None
        assert work.changed_fields(Item, {"a": 1, "b": "y", "c": None}) == {"b": "y"}
        assert work.changed_fields(Item, {"c": "now set"}) == {"c": "now set"} and work.changed_fields(Item, {}) == {}

    def test_the_same_instant_in_another_offset_is_no_change(self):
        class Item:
            at = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
        assert work.changed_fields(Item, {"at": datetime(2026, 9, 25, 13, 0, tzinfo=timezone(timedelta(hours=3)))}) == {}

    def test_reject_future_has_a_tolerance(self):
        now = datetime.now(timezone.utc)
        work.reject_future("called_at", now + timedelta(minutes=1), tolerance=C.CLOCK_SKEW_TOLERANCE)
        work.reject_future("called_at", now - timedelta(days=400), tolerance=C.CLOCK_SKEW_TOLERANCE)
        with pytest.raises(HTTPException) as exc:
            work.reject_future("called_at", now + timedelta(hours=1), tolerance=C.CLOCK_SKEW_TOLERANCE)
        assert exc.value.status_code == 400 and exc.value.detail["code"] == "called_at_in_future"

    def test_error_shapes(self):
        assert work.not_found("Call").detail == "Call not found" and work.not_found("Call").status_code == 404
        e = work.lead_not_found()
        assert e.status_code == 404 and e.detail == {"field": "lead_id", "message": "Lead not found", "code": "lead_not_found"}
        e = work.not_open("status", "already done", "followup_not_pending")
        assert e.status_code == 409 and e.detail["code"] == "followup_not_pending"
        assert work.empty_update().status_code == 400

    def test_not_clearable(self):
        work.not_clearable({"a": 1, "b": None}, ("a",))
        with pytest.raises(HTTPException) as exc:
            work.not_clearable({"a": 1, "b": None}, ("a", "b"))
        assert exc.value.detail["field"] == "b"

    def test_repository_helpers(self):
        assert work_repo.escape_like("50%_off\\") == "50\\%\\_off\\\\"
        assert work_repo.search_pattern(None) is None and work_repo.search_pattern("  ") is None and work_repo.search_pattern("a\x00 ") == "%a%"
        assert work_repo.search_pattern("100%") == "%100\\%%"
        # inclusive dates, and a day is a Baghdad day (UTC+3): it starts at 21:00 UTC of the day before
        lower, upper = work_repo.day_bounds(date(2026, 9, 1), date(2026, 9, 30))
        assert lower == datetime(2026, 8, 31, 21, tzinfo=timezone.utc) and upper == datetime(2026, 9, 30, 21, tzinfo=timezone.utc)
        assert work_repo.day_bounds(None, None) == (None, None)
        assert work_repo.day_bounds(None, date(2026, 12, 31))[1] == datetime(2026, 12, 31, 21, tzinfo=timezone.utc)


# =============================================================================
class TestSourceGuarantees:
    """Static checks over the Phase-3 source: things that must be true however the code is refactored."""

    FILES = [BACKEND / "sales" / rel for rel in (
        "services/calls.py", "services/followups.py", "services/demos.py", "services/trials.py", "services/work.py",
        "repositories/calls.py", "repositories/followups.py", "repositories/demos.py", "repositories/trials.py", "repositories/work.py",
    )]

    def test_no_work_item_is_ever_hard_deleted(self):
        for path in self.FILES:
            source = path.read_text()
            assert not re.search(r"\bdelete\(|\.delete\(|DELETE\s+FROM|TRUNCATE", source, re.I), path.name

    def test_there_is_no_delete_route_for_any_work_item(self):
        from server import app
        for route in app.routes:
            path = getattr(route, "path", "")
            if path.startswith("/api/sales/") and path.split("/")[3] in KINDS:
                assert "DELETE" not in route.methods, path

    def test_a_sales_trial_never_touches_the_platforms_subscriptions(self):
        from sales.models import SalesTrial
        for name in ("services/trials.py", "repositories/trials.py"):
            imports = "\n".join(re.findall(r"^(?:from|import) .*$", (BACKEND / "sales" / name).read_text(), re.M))
            assert not re.search(r"Company|Subscription|subscription|companies", imports), (name, imports)
            assert all(item.strip() == "User" for item in re.findall(r"^from models import (.*)$", imports, re.M)), name   # a person's name, nothing else
        assert not any(re.search(r"company|subscription|plan", c.name) for c in SalesTrial.__table__.columns)
        assert {fk.column.table.name for fk in SalesTrial.__table__.foreign_keys} == {"sales_leads", "users"}     # it points at a lead and a person, nothing else

    def test_work_items_never_write_the_lead(self):
        """Activities record history and never edit the lead row: no lead assignment, no stage, no updated_at."""
        for path in self.FILES:
            source = path.read_text()
            assert not re.search(r"lead\.(pipeline_stage|assigned_to|assigned_at|lost_reason|closed_at|archived_at|updated_at)\s*=(?!=)", source), path.name
            assert "STAGE_" not in source and "transition_error" not in source, path.name           # no stage logic in the work items at all

    def test_every_function_that_writes_also_records_its_timeline_event(self):
        import ast
        for name in ("calls", "followups", "demos", "trials"):
            source = (BACKEND / "sales" / "services" / f"{name}.py").read_text()
            writers = 0
            for fn in ast.walk(ast.parse(source)):
                if isinstance(fn, ast.AsyncFunctionDef):
                    text = ast.get_source_segment(source, fn)
                    if "db.flush()" in text:
                        writers += 1
                        assert "activity_service.record(" in text, (name, fn.name)             # a change can never be saved without its event
            assert writers >= 2, name
