"""JAZ Sales - Phase 4: unit tier - no HTTP, no database writes.

The customer / onboarding vocabulary and the onboarding state machine (every pair of stages), the request schemas (and that
the owner's password never prints), the permission catalog, the timeline's event vocabulary and who may read which
events, the query-string filters, the small shared helpers, and the DRIFT GUARDS that keep the code, the migration and the
test expectations saying the same thing. Also static guarantees over the source: the Customer Setup (which replaced the RETIRED
conversion - test_sales_conversion.py) reuses the platform's company service instead of reimplementing it, a password is
unwrapped only where it is hashed, nothing is hard-deleted.
"""
from sales_test_utils import ALL_MODULES, ALL_PERMISSION_KEYS, SYSTEM_ROLE_PERMISSIONS, assert_scratch_target, granted_before_workflow

assert_scratch_target(need_http=False)  # must run BEFORE importing database (which loads .env)

import ast
import importlib.util
import itertools
import re
import typing
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from sales import constants as C
from sales import customer_params as params
from sales import customer_schemas as S
from sales import permissions as P
from sales.repositories.onboarding import OnboardingRow
from sales.services import activity as A
from sales.services import conversion
from sales.services import duplicates as dup
from sales.services import onboarding as onboarding_service
from sales.services.access import StaffContext
from sales.services.onboarding_access import can_see_onboarding

BACKEND = Path(__file__).resolve().parent.parent
PHASE4 = {"sales.customers.view", "sales.customers.convert", "sales.onboarding.view", "sales.onboarding.manage",
          "sales.onboarding.assign", "sales.onboarding.scope_all", "sales.onboarding.scope_assigned"}
STAGES = ["won", "assigned", "contacted", "setup_started", "company_configured", "employees_added", "training", "activated"]
MIGRATION = BACKEND / "migrations" / "versions" / "f4c8a1d92b63_sales_customers_onboarding.py"
SECRET = "Owner#Pass-2026"


def ctx(*permissions, super_admin=False, user_id=None) -> StaffContext:
    return StaffContext({"id": "x", "role": "jaz_staff", "status": "active"}, user_id or uuid.uuid4(), super_admin, (), frozenset(permissions))


def load_migration(path=MIGRATION, name="mig_f4c8a1d92b63"):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =============================================================================
class TestVocabulary:
    def test_the_stages_are_exactly_the_specified_eight_in_order(self):
        assert list(C.ONBOARDING_STAGES) == STAGES
        assert (C.ONBOARDING_START, C.ONBOARDING_ASSIGNED, C.ONBOARDING_DONE) == ("won", "assigned", "activated")
        assert C.ONBOARDING_RANK == {stage: i for i, stage in enumerate(STAGES)}

    def test_the_customer_statuses_are_exactly_the_specified_three(self):
        assert C.CUSTOMER_STATUSES == ("active", "onboarding", "activated")

    def test_the_schemas_and_the_constants_agree(self):
        assert typing.get_args(S.OnboardingStage) == C.ONBOARDING_STAGES
        assert typing.get_args(S.CustomerStatus) == C.CUSTOMER_STATUSES

    def test_the_customer_status_follows_the_stage(self):
        assert [C.customer_status_for_stage(s) for s in STAGES] == ["active", "onboarding", "onboarding", "onboarding", "onboarding", "onboarding", "onboarding", "activated"]
        assert {C.customer_status_for_stage(s) for s in STAGES} == set(C.CUSTOMER_STATUSES)        # every status is reachable, none is unused

    def test_the_models_use_the_same_vocabulary_as_the_constants(self):
        from sales.models import SalesCustomer, SalesOnboarding
        checks = {c.name: str(c.sqltext) for c in SalesCustomer.__table__.constraints if c.name}
        assert all(f"'{s}'" in checks["ck_sales_customers_status"] for s in C.CUSTOMER_STATUSES)
        stage_check = {c.name: str(c.sqltext) for c in SalesOnboarding.__table__.constraints if c.name}["ck_sales_onboarding_stage"]
        assert all(f"'{s}'" in stage_check for s in C.ONBOARDING_STAGES) and stage_check.count("'") == 2 * len(C.ONBOARDING_STAGES)


class TestOnboardingStateMachine:
    def test_every_pair_of_stages_with_and_without_an_owner(self):
        for current, target, owned in itertools.product(STAGES, STAGES, (True, False)):
            got = C.onboarding_transition_error(current, target, has_assignee=owned)
            if target == current:
                expected = "onboarding_same_stage"
            elif target == "won":
                expected = "onboarding_transition_not_allowed"
            elif not owned:
                expected = "onboarding_assignee_required"
            else:
                expected = None
            assert (got[0] if got else None) == expected, (current, target, owned)

    def test_an_unknown_stage_is_invalid_on_either_side(self):
        for current, target in (("won", "nope"), ("nope", "assigned"), ("", ""), ("ACTIVATED", "assigned")):
            assert C.onboarding_transition_error(current, target, has_assignee=True)[0] == "onboarding_invalid_stage"

    def test_allowed_stages_are_exactly_the_stages_without_an_error(self):
        for current, owned in itertools.product(STAGES, (True, False)):
            allowed = C.onboarding_allowed_stages(current, has_assignee=owned)
            assert list(allowed) == [t for t in STAGES if C.onboarding_transition_error(current, t, has_assignee=owned) is None]

    def test_the_shape_of_the_machine(self):
        assert C.onboarding_allowed_stages("won", has_assignee=False) == ()                          # nothing moves until somebody owns it
        for stage in STAGES[1:]:
            assert C.onboarding_allowed_stages(stage, has_assignee=True) == tuple(s for s in STAGES[1:] if s != stage)   # everything but itself and `won`
        assert "won" not in C.onboarding_allowed_stages("activated", has_assignee=True)              # and a finished record can be reopened
        assert "training" in C.onboarding_allowed_stages("activated", has_assignee=True)

    def test_messages_are_never_empty(self):
        for current, target in itertools.product(STAGES, STAGES):
            got = C.onboarding_transition_error(current, target, has_assignee=False)
            assert got is None or (got[0] and got[1])


# =============================================================================
class TestPermissionCatalog:
    def test_the_phase_4_keys(self):
        # (+ sales.customers.setup: the simplified workflow's Customer Setup key, e7a2d4c9b1f3)
        assert {k for k in P.ALL_PERMISSIONS if k.split(".")[1] in ("customers", "onboarding")} - {P.PERM_CUSTOMERS_SETUP} == PHASE4
        assert PHASE4 <= ALL_PERMISSION_KEYS and set(P.ALL_PERMISSIONS) == ALL_PERMISSION_KEYS
        for key in PHASE4:
            assert re.fullmatch(r"sales\.[a-z]+\.[a-z_]+", key) and P.ALL_PERMISSIONS[key].strip()

    def test_the_constants_name_the_keys(self):
        assert {P.PERM_CUSTOMERS_VIEW, P.PERM_CUSTOMERS_CONVERT, P.PERM_ONBOARDING_VIEW, P.PERM_ONBOARDING_MANAGE, P.PERM_ONBOARDING_ASSIGN,
                P.PERM_ONBOARDING_SCOPE_ALL, P.PERM_ONBOARDING_SCOPE_ASSIGNED} == PHASE4

    def test_the_modules_and_their_order(self):
        keys = [m["key"] for m in P.MODULES]
        # simplified workflow: onboarding is no longer a workspace section (its permissions and routes stay, dormant)
        # (+ the Sales Manager's "batches" and "performance" sections after it, f2b6d8a1c4e9)
        assert keys == ALL_MODULES and keys[keys.index("customers") + 1:] == ["batches", "performance"] and "onboarding" not in keys
        by_key = {m["key"]: m["permission"] for m in P.MODULES}
        assert by_key["customers"] == "sales.customers.view"

    def test_who_sees_which_module(self):
        assert ctx(P.PERM_CUSTOMERS_VIEW).visible_modules() == ["customers"]
        assert ctx(P.PERM_ONBOARDING_VIEW, P.PERM_ACCESS).visible_modules() == ["home"]                  # no onboarding section any more
        assert ctx(P.PERM_CUSTOMERS_CONVERT, P.PERM_ONBOARDING_MANAGE, P.PERM_ONBOARDING_ASSIGN).visible_modules() == []      # acting is not seeing

    def test_the_approved_grants_of_each_system_role(self):
        assert PHASE4 & SYSTEM_ROLE_PERMISSIONS["sales_manager"] == PHASE4 - {P.PERM_ONBOARDING_SCOPE_ASSIGNED}
        assert PHASE4 & SYSTEM_ROLE_PERMISSIONS["sales_employee"] == {P.PERM_CUSTOMERS_VIEW}
        assert PHASE4 & SYSTEM_ROLE_PERMISSIONS["onboarding_employee"] == {P.PERM_ONBOARDING_VIEW, P.PERM_ONBOARDING_MANAGE, P.PERM_ONBOARDING_SCOPE_ASSIGNED}
        assert not PHASE4 & SYSTEM_ROLE_PERMISSIONS["lead_data_entry"]
        for role in ("sales_employee", "lead_data_entry", "onboarding_employee"):
            assert P.PERM_CUSTOMERS_CONVERT not in SYSTEM_ROLE_PERMISSIONS[role], role                # only a manager (or the Super Admin) converts
        # the Customer Setup has its own key (e7a2d4c9b1f3): Sales Manager and Sales Employee
        assert {r for r, keys in SYSTEM_ROLE_PERMISSIONS.items() if P.PERM_CUSTOMERS_SETUP in keys} == {"sales_manager", "sales_employee"}
        assert not {P.PERM_LEADS_VIEW, P.PERM_LEADS_SCOPE_ALL, P.PERM_LEADS_SCOPE_ASSIGNED, P.PERM_LEADS_SCOPE_INTAKE} & SYSTEM_ROLE_PERMISSIONS["onboarding_employee"]


# =============================================================================
class TestTimelineVocabularyAndVisibility:
    PHASE4_EVENTS = {"lead_converted", "onboarding_assigned", "onboarding_stage_changed", "onboarding_notes_updated"}

    def test_the_vocabulary(self):
        assert {A.EVENT_LEAD_CONVERTED, A.EVENT_ONBOARDING_ASSIGNED, A.EVENT_ONBOARDING_STAGE_CHANGED, A.EVENT_ONBOARDING_NOTES_UPDATED} == self.PHASE4_EVENTS
        assert A.ONBOARDING_EVENT_TYPES == self.PHASE4_EVENTS - {"lead_converted"}
        for event in self.PHASE4_EVENTS:
            assert re.fullmatch(r"[a-z][a-z0-9_]{2,63}", event), event                                # the database's format check

    def test_each_event_sits_under_exactly_one_view_permission(self):
        assert A.WORK_EVENT_TYPES[P.PERM_CUSTOMERS_VIEW] == frozenset({"lead_converted"})
        assert A.WORK_EVENT_TYPES[P.PERM_ONBOARDING_VIEW] == A.ONBOARDING_EVENT_TYPES
        assert not self.PHASE4_EVENTS & A.LEAD_EVENT_TYPES                                            # never visible to a reader with nothing but the lead

    def test_what_each_reader_may_see(self):
        assert self.PHASE4_EVENTS.isdisjoint(A.visible_event_types(ctx()))
        assert self.PHASE4_EVENTS.isdisjoint(A.visible_event_types(ctx(P.PERM_CUSTOMERS_CONVERT, P.PERM_ONBOARDING_MANAGE, P.PERM_ONBOARDING_ASSIGN)))    # acting reads nothing back
        assert A.visible_event_types(ctx(P.PERM_CUSTOMERS_VIEW)) == A.LEAD_EVENT_TYPES | {"lead_converted"}
        assert A.visible_event_types(ctx(P.PERM_ONBOARDING_VIEW)) == A.LEAD_EVENT_TYPES | A.ONBOARDING_EVENT_TYPES
        everything = A.visible_event_types(ctx(*P.ALL_PERMISSIONS, super_admin=True))
        assert self.PHASE4_EVENTS <= everything

    def test_the_secret_screening_refuses_the_owners_password_key(self):
        from sales.services.audit import _assert_no_secrets
        for key in ("owner_password", "password", "OWNER_PASSWORD"):
            with pytest.raises(ValueError):
                _assert_no_secrets({"after": {key: "x"}}, "test")
        _assert_no_secrets({"company_name": "x", "plan": "y", "customer_id": "z"}, "test")            # what the conversion actually records


# =============================================================================
BASE = dict(business_name="Acme", owner_name="Owner", owner_email="owner@acme.example.com", owner_phone="+9647701234567", owner_password=SECRET, subscription_plan_id=str(uuid.uuid4()))


class TestRequestSchemas:
    def test_a_valid_conversion_and_its_defaults(self):
        body = S.ConvertLead(**BASE)
        assert body.address is None and body.confirm_duplicates is False
        assert body.owner_password.get_secret_value() == SECRET

    def test_the_password_never_prints(self):
        body = S.ConvertLead(**BASE)
        for shown in (repr(body), str(body), body.model_dump_json(), repr(body.owner_password), str(body.owner_password), repr(body.model_dump()), f"{body!r}"):
            assert SECRET not in shown, shown
        assert "**********" in body.model_dump_json()

    @pytest.mark.parametrize("missing", ["business_name", "owner_name", "owner_email", "owner_phone", "owner_password", "subscription_plan_id"])
    def test_every_field_the_company_needs_is_required(self, missing):
        with pytest.raises(ValidationError):
            S.ConvertLead(**{k: v for k, v in BASE.items() if k != missing})

    @pytest.mark.parametrize("field", ["business_name", "owner_name", "owner_phone"])
    def test_blank_required_text_is_refused(self, field):
        for blank in ("", "   ", "\t\n"):
            with pytest.raises(ValidationError):
                S.ConvertLead(**{**BASE, field: blank})

    def test_text_is_trimmed_and_nul_and_length_are_refused(self):
        assert S.ConvertLead(**{**BASE, "business_name": "  Acme  "}).business_name == "Acme"
        assert S.ConvertLead(**{**BASE, "address": "   "}).address is None
        for field, bad in (("business_name", "a\x00b"), ("business_name", "x" * 201), ("owner_name", "x" * 201), ("owner_phone", "1" * 51), ("address", "x" * 501)):
            with pytest.raises(ValidationError):
                S.ConvertLead(**{**BASE, field: bad})

    def test_email_plan_and_password_bounds(self):
        for bad in ("nope", "a@", "@b.com", "a b@c.com"):
            with pytest.raises(ValidationError):
                S.ConvertLead(**{**BASE, "owner_email": bad})
        for bad in ("nope", 5, None):
            with pytest.raises(ValidationError):
                S.ConvertLead(**{**BASE, "subscription_plan_id": bad})
        with pytest.raises(ValidationError):
            S.ConvertLead(**{**BASE, "owner_password": ""})
        with pytest.raises(ValidationError):
            S.ConvertLead(**{**BASE, "owner_password": "x" * 201})
        assert S.ConvertLead(**{**BASE, "owner_password": "x"}).owner_password.get_secret_value() == "x"     # the STRENGTH rule is the core service's, not duplicated here

    def test_unknown_fields_are_refused_everywhere(self):
        cases = ((S.ConvertLead, BASE), (S.ConversionValues, {}), (S.OnboardingAssign, {"assigned_to": str(uuid.uuid4())}),
                 (S.OnboardingStageChange, {"stage": "training"}), (S.OnboardingUpdate, {}))
        for model, base in cases:
            for extra in ("company_id", "lead_id", "status", "converted_by", "role", "id", "customer_id", "completed_at", "started_at"):
                with pytest.raises(ValidationError):
                    model(**base, **{extra: "x"})

    def test_the_fields_a_client_must_not_control_are_not_in_any_request(self):
        forbidden = {"id", "status", "company_id", "lead_id", "customer_id", "converted_by", "converted_at", "created_at", "updated_at", "completed_at", "started_at", "role", "company"}
        for model in (S.ConvertLead, S.ConversionValues, S.OnboardingAssign, S.OnboardingStageChange, S.OnboardingUpdate):
            assert not forbidden & set(model.model_fields), model.__name__

    def test_the_preflight_values_are_all_optional_and_blank_means_none(self):
        assert S.ConversionValues().model_dump(exclude_unset=True) == {}
        v = S.ConversionValues(business_name="  ", owner_email="", owner_phone=" ", owner_name=None, address="")
        assert v.model_dump() == {"business_name": None, "owner_name": None, "owner_email": None, "owner_phone": None, "address": None}
        with pytest.raises(ValidationError):
            S.ConversionValues(owner_email="not-an-email")

    def test_stage_change_accepts_only_the_eight_stages(self):
        for stage in STAGES:
            assert S.OnboardingStageChange(stage=stage).stage == stage
        for bad in ("", "done", "Training", "lost", None, 3):
            with pytest.raises(ValidationError):
                S.OnboardingStageChange(stage=bad)

    def test_notes_and_action_notes(self):
        assert S.OnboardingUpdate(notes="  x  ").notes == "x" and S.OnboardingUpdate(notes="  ").notes is None
        assert S.OnboardingUpdate().model_dump(exclude_unset=True) == {} and S.OnboardingUpdate(notes=None).model_dump(exclude_unset=True) == {"notes": None}
        assert S.OnboardingUpdate(notes="n" * 5000).notes == "n" * 5000
        for bad in ("n" * 5001, "a\x00b"):
            with pytest.raises(ValidationError):
                S.OnboardingUpdate(notes=bad)
        assert S.OnboardingStageChange(stage="training", note="  ").note is None
        assert S.OnboardingAssign(assigned_to=str(uuid.uuid4()), note="x" * 1000).note == "x" * 1000
        for bad in ("x" * 1001,):
            with pytest.raises(ValidationError):
                S.OnboardingAssign(assigned_to=str(uuid.uuid4()), note=bad)
        with pytest.raises(ValidationError):
            S.OnboardingAssign(assigned_to="nope")

    def test_no_response_model_can_carry_a_secret(self):
        def fields(model, seen=()):
            for name, info in model.model_fields.items():
                yield name
                for arg in [info.annotation, *typing.get_args(info.annotation)]:
                    for sub in [arg, *typing.get_args(arg)]:
                        if isinstance(sub, type) and hasattr(sub, "model_fields") and sub not in seen:
                            yield from fields(sub, seen + (model,))
        for model in (S.CustomerOut, S.CustomerList, S.ConvertResult, S.ConversionStatusOut, S.ConversionPreflightOut, S.OnboardingOut, S.OnboardingList, S.OnboardingCounts, S.OnboardingAssigneeOut):
            names = {n.lower() for n in fields(model)}
            assert not {n for n in names if any(bad in n for bad in ("password", "hash", "secret", "token"))}, model.__name__


# =============================================================================
class TestQueryFilters:
    def test_customer_filters(self):
        f = params.CustomerFilterParams(status="active,onboarding", converted_from=None, converted_to=None, q="acme").to_filters(ctx())
        assert f.statuses == ["active", "onboarding"] and f.q == "acme"
        assert params.CustomerFilterParams(status=None, converted_from=None, converted_to=None, q=None).to_filters(ctx()).statuses is None
        with pytest.raises(HTTPException) as exc:
            params.CustomerFilterParams(status="active,nope", converted_from=None, converted_to=None, q=None).to_filters(ctx())
        assert exc.value.status_code == 400 and exc.value.detail["field"] == "status"

    def test_onboarding_filters(self):
        me = uuid.uuid4()
        f = params.OnboardingFilterParams(stage="training, activated", assigned_to="me", unassigned=True, open=True, started_from=None, started_to=None, q="x").to_filters(ctx(user_id=me))
        assert f.stages == ["training", "activated"] and f.assigned_to == me and f.unassigned is True and f.open_only is True
        other = uuid.uuid4()
        assert params.OnboardingFilterParams(stage=None, assigned_to=str(other), unassigned=False, open=False, started_from=None, started_to=None, q=None).to_filters(ctx()).assigned_to == other
        for kwargs, field in (({"stage": "nope"}, "stage"), ({"assigned_to": "nope"}, "assigned_to")):
            with pytest.raises(HTTPException) as exc:
                params.OnboardingFilterParams(**{"stage": None, "assigned_to": None, "unassigned": False, "open": False, "started_from": None, "started_to": None, "q": None, **kwargs}).to_filters(ctx())
            assert exc.value.status_code == 400 and exc.value.detail["field"] == field


# =============================================================================
class TestHelpers:
    def lead(self, **kw):
        return SimpleNamespace(**{"archived_at": None, "pipeline_stage": "won", "business_name": "Lead Co", "contact_name": "Contact", "email": "lead@x.com", "phone": "+9647700000000", "address": "Addr", **kw})

    def test_setup_lead_blocker(self):
        owner = uuid.uuid4()
        assert conversion.setup_lead_blocker(self.lead(assigned_to=owner)) is None                                  # won and owned
        assert conversion.setup_lead_blocker(self.lead(assigned_to=owner, pipeline_stage="negotiation")) is None    # open and owned: "Agreed" wins it
        assert conversion.setup_lead_blocker(self.lead(assigned_to=owner, pipeline_stage="lost")) == "lead_lost"
        assert conversion.setup_lead_blocker(self.lead(assigned_to=None)) == "lead_unassigned"
        assert conversion.setup_lead_blocker(self.lead(assigned_to=owner, archived_at=datetime.now(timezone.utc))) == "lead_archived"
        assert conversion.setup_lead_blocker(self.lead(assigned_to=None, pipeline_stage="lost", archived_at=datetime.now(timezone.utc))) == "lead_archived"   # archived first
        assert set(conversion.SETUP_LEAD_BLOCKERS) == {"lead_archived", "lead_lost", "lead_unassigned"}

    def test_the_retired_conversion_leaves_no_way_to_run_it(self):
        for gone in ("convert_lead", "preflight", "lead_blocker", "_LEAD_BLOCKERS"):
            assert not hasattr(conversion, gone), gone

    def test_the_retired_answer_is_a_410_with_a_stable_code_and_the_replacement(self):
        e = conversion.conversion_retired()
        assert isinstance(e, HTTPException) and e.status_code == 410
        assert e.detail["code"] == "conversion_retired" and e.detail["field"] == "lead"
        assert e.detail["replacement"] == conversion.CONVERSION_REPLACEMENT == "POST /api/sales/leads/{lead_id}/setup"
        assert "Customer Setup" in e.detail["message"]

    def test_effective_values_prefer_the_callers_and_fall_back_to_the_lead(self):
        lead = self.lead()
        assert conversion._effective_values(lead, S.ConversionValues()) == {"business_name": "Lead Co", "owner_name": "Contact", "owner_email": "lead@x.com", "owner_phone": "+9647700000000", "address": "Addr"}
        given = S.ConversionValues(business_name="New", owner_email="new@x.com", owner_name="N", owner_phone="+1", address="A2")
        assert conversion._effective_values(lead, given) == {"business_name": "New", "owner_name": "N", "owner_email": "new@x.com", "owner_phone": "+1", "address": "A2"}
        assert conversion._effective_values(self.lead(contact_name=None, email=None, phone=None, address=None), S.ConversionValues())["owner_email"] is None

    def test_core_service_errors_are_turned_into_field_errors(self):
        e = conversion._from_core_error(HTTPException(status_code=400, detail="Email already registered"))
        assert e.detail == {"field": "owner_email", "message": "This email is already registered to a JAZ account", "code": "owner_email_registered"}
        e = conversion._from_core_error(HTTPException(status_code=400, detail={"field": "owner_phone", "message": "Phone number already registered"}))
        assert e.detail["code"] == "owner_phone_registered" and e.detail["field"] == "owner_phone"
        other = HTTPException(status_code=400, detail="Something else")
        assert conversion._from_core_error(other) is other                                            # anything else passes through untouched

    def test_the_duplicates_shape(self):
        company = SimpleNamespace(company_id=uuid.uuid4(), company_name="Co")
        exact = dup.CompanyMatch(company, [dup.Match("email", dup.EXACT, "owner_email")])
        possible = dup.CompanyMatch(company, [dup.Match("business_name", dup.POSSIBLE, "company_name")])
        assert conversion._duplicates_out([]) == {"has_exact": False, "has_possible": False, "companies": []}
        out = conversion._duplicates_out([exact, possible])
        assert out["has_exact"] and out["has_possible"] and out["companies"][0] == {
            "id": str(company.company_id), "name": "Co", "severity": "exact", "matches": [{"field": "email", "kind": "exact", "matched_on": "owner_email"}]}
        assert conversion._exact_blocker([possible]) is None and conversion._exact_blocker([exact])["code"] == "company_exists"

    def test_the_onboarding_scope_truth_table(self):
        me, other = uuid.uuid4(), uuid.uuid4()
        table = {
            (): (False, False, False),
            (P.PERM_ONBOARDING_SCOPE_ALL,): (True, True, True),
            (P.PERM_ONBOARDING_SCOPE_ASSIGNED,): (False, True, False),
            (P.PERM_ONBOARDING_SCOPE_ALL, P.PERM_ONBOARDING_SCOPE_ASSIGNED): (True, True, True),
            (P.PERM_LEADS_SCOPE_ALL, P.PERM_LEADS_SCOPE_ASSIGNED, P.PERM_ONBOARDING_VIEW): (False, False, False),     # the LEAD scope grants nothing here
        }
        for scopes, (unowned, mine, theirs) in table.items():
            c = ctx(*scopes, user_id=me)
            assert (can_see_onboarding(c, assigned_to=None), can_see_onboarding(c, assigned_to=me), can_see_onboarding(c, assigned_to=other)) == (unowned, mine, theirs), scopes

    def _row(self, stage="assigned", owner=None, **kw):
        oid, cid, lid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        now = datetime.now(timezone.utc)
        onboarding = SimpleNamespace(id=oid, customer_id=cid, stage=stage, assigned_to=owner, notes=None, started_at=now, completed_at=None, created_at=now, updated_at=now)
        values = dict(onboarding=onboarding, customer_status="onboarding", company_id=uuid.uuid4(), company_name="Co", converted_at=now, lead_id=lid,
                      lead_created_by=uuid.uuid4(), lead_assigned_to=uuid.uuid4(), business_name="Biz", contact_name="C", contact_position=None, phone="1", whatsapp=None,
                      email="e@x.com", city="City", country="Iraq", assignee_name="W", assignee_status="active")
        values.update(kw)
        return OnboardingRow(**values)

    def test_an_onboarding_employee_gets_the_contact_details_and_nothing_behind_them(self):
        me = uuid.uuid4()
        c = ctx(P.PERM_ONBOARDING_VIEW, P.PERM_ONBOARDING_MANAGE, P.PERM_ONBOARDING_SCOPE_ASSIGNED, user_id=me)
        out = onboarding_service.onboarding_out(self._row(owner=me), c)
        assert out["company"] is None and out["lead_id"] is None
        assert out["can"] == {"update": True, "change_stage": True, "assign": False, "view_customer": False, "view_lead": False}
        assert out["allowed_stages"] == list(C.onboarding_allowed_stages("assigned", has_assignee=True))
        assert out["customer"]["business_name"] == "Biz" and out["customer"]["phone"] == "1" and out["customer"]["email"] == "e@x.com"

    def test_a_reader_without_manage_gets_no_allowed_stages(self):
        me = uuid.uuid4()
        out = onboarding_service.onboarding_out(self._row(owner=me), ctx(P.PERM_ONBOARDING_VIEW, P.PERM_ONBOARDING_SCOPE_ALL))
        assert out["allowed_stages"] == [] and out["can"]["change_stage"] is False and out["can"]["assign"] is False

    def test_the_company_needs_both_the_customer_permission_and_the_leads_scope(self):
        creator = uuid.uuid4()
        row = self._row(lead_created_by=creator, lead_assigned_to=None)                                   # an unassigned lead: in the intake scope
        base = (P.PERM_ONBOARDING_VIEW, P.PERM_ONBOARDING_SCOPE_ALL)
        assert onboarding_service.onboarding_out(row, ctx(*base, P.PERM_CUSTOMERS_VIEW))["company"] is None                                        # no lead scope
        assert onboarding_service.onboarding_out(row, ctx(*base, P.PERM_LEADS_SCOPE_ALL))["company"] is None                                        # no customers.view
        both = onboarding_service.onboarding_out(row, ctx(*base, P.PERM_CUSTOMERS_VIEW, P.PERM_LEADS_SCOPE_ALL))
        assert both["company"]["name"] == "Co" and both["lead_id"] is None                                                                          # lead link needs leads.view too
        linked = onboarding_service.onboarding_out(row, ctx(*base, P.PERM_CUSTOMERS_VIEW, P.PERM_LEADS_SCOPE_ALL, P.PERM_LEADS_VIEW))
        assert linked["lead_id"] == str(row.lead_id)


# =============================================================================
class TestMigrationDriftGuard:
    def test_the_migration_is_chained_after_the_activities_revision(self):
        mig = load_migration()
        assert mig.revision == "f4c8a1d92b63" and mig.down_revision == "e3a9c5b07d12"
        assert mig.branch_labels is None and mig.depends_on is None

    def test_the_migration_history_has_a_single_linear_head(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
        heads = script.get_heads()
        assert heads == ["f2b6d8a1c4e9"], heads                                                            # no branch: exactly one head (the batches)
        chain = [rev.revision for rev in script.walk_revisions()]
        assert chain.index("f2b6d8a1c4e9") < chain.index("e7a2d4c9b1f3") < chain.index("c5e1b9a4d2f7") < chain.index("a3f8c2d7e915") < chain.index("b6d1f3a8c294") < chain.index("f4c8a1d92b63") < chain.index("e3a9c5b07d12") < chain.index("d764d5f44e91")   # head -> base
        for rev in script.walk_revisions():
            assert not isinstance(rev.down_revision, tuple), rev.revision                                 # nothing merges two branches either

    def test_the_frozen_seed_equals_the_code(self):
        mig = load_migration()
        seeded = dict(mig._PERMISSIONS)
        assert set(seeded) == PHASE4
        assert seeded == {k: P.ALL_PERMISSIONS[k] for k in seeded}                                         # keys AND descriptions
        granted = {}
        for role, key in mig._GRANTS:
            granted.setdefault(role, []).append(key)
        assert set(granted) == {"sales_manager", "sales_employee", "onboarding_employee"}                   # Lead Data Entry: nothing
        for role, keys in granted.items():
            assert sorted(keys) == sorted(set(keys)), role                                                   # no grant twice
            assert set(keys) == granted_before_workflow(role) & PHASE4, role                                # a3f8c2d7e915 added convert for the employee
        phase1 = load_migration(BACKEND / "migrations" / "versions" / "bb595f6d8dae_sales_rbac_foundation.py", "mig_bb595_for_roles")
        ids = {key: rid for rid, key, *_ in phase1._ROLES}
        assert mig._ROLE_IDS == {r: ids[r] for r in ("sales_manager", "sales_employee", "onboarding_employee")}   # the same seeded roles

    def test_the_frozen_check_literals_equal_the_code(self):
        mig = load_migration()
        assert mig._CUSTOMER_STATUSES == "(" + ",".join(f"'{s}'" for s in C.CUSTOMER_STATUSES) + ")"
        assert mig._ONBOARDING_STAGES == "(" + ",".join(f"'{s}'" for s in C.ONBOARDING_STAGES) + ")"

    def test_the_downgrade_refuses_while_customers_exist_and_never_drops_leads_or_companies(self):
        source = MIGRATION.read_text()
        down = source[source.index("def downgrade()"):]
        assert "Refusing to downgrade" in down
        both = {"sales_customers", "sales_onboarding"}
        checked = set(re.findall(r"'(sales_[a-z]+)'", re.search(r"for table in \(([^)]*)\)", down).group(1)))
        assert checked == both                                                                              # both are checked for rows
        assert set(re.findall(r"op\.drop_table\('([a-z_]+)'\)", down)) == both                              # ... and exactly those are dropped
        assert not re.search(r"sales_leads|sales_lead_activities|companies|users", down.split("in_list")[0])  # leads, their timeline, companies and users are never touched

    def test_the_migration_touches_only_its_own_tables_and_the_rbac_catalog(self):
        source = MIGRATION.read_text()
        assert set(re.findall(r"op\.create_table\(\s*'([a-z_]+)'", source)) == {"sales_customers", "sales_onboarding"}
        assert not re.findall(r"op\.(alter_column|add_column|drop_column|drop_index|drop_constraint)\(", source)     # no existing table is altered
        assert set(re.findall(r"sa\.table\(\s*'([a-z_]+)'", source)) == {"staff_permissions", "staff_role_permissions"}
        assert not re.search(r"INSERT INTO (users|companies|sales_)|op\.bulk_insert\((?!perms|role_perms)", source)   # no user, company, lead or customer is seeded
        assert "Hand-written" in source.split('"""')[1] and "autogenerate" not in source.split('"""')[2]

    def test_every_check_and_foreign_key_of_the_migration_is_declared_on_the_models(self):
        from sales.models import SalesCustomer, SalesOnboarding
        source = MIGRATION.read_text()
        # the simplified workflow (a3f8c2d7e915) later added one check to sales_customers - declared on the model too
        later = {"ck_sales_customers_subscription_type"}
        workflow = (BACKEND / "migrations" / "versions" / "a3f8c2d7e915_sales_simplified_workflow.py").read_text()
        assert all(f"'{name}'" in workflow for name in later)
        declared = {c.name for m in (SalesCustomer, SalesOnboarding) for c in m.__table__.constraints if c.name and c.name.startswith("ck_")} - later
        assert set(re.findall(r"name='(ck_[a-z_]+)'", source)) == declared
        indexes = {i.name for m in (SalesCustomer, SalesOnboarding) for i in m.__table__.indexes}
        assert set(re.findall(r"op\.create_index\('([a-z_]+)'", source)) == indexes


# =============================================================================
class TestSourceGuarantees:
    """Static checks over the Phase-4 source: things that must be true however the code is refactored."""

    SERVICES = [BACKEND / "sales" / "services" / n for n in ("conversion.py", "customers.py", "onboarding.py", "onboarding_access.py")]
    REPOS = [BACKEND / "sales" / "repositories" / n for n in ("customers.py", "onboarding.py")]
    ALL = SERVICES + REPOS

    def test_the_customer_setup_reuses_the_platforms_company_service_and_reimplements_none_of_it(self):
        source = (BACKEND / "sales" / "services" / "customer_setup.py").read_text()
        assert source.count("admin_service.create_company(") == 1                                          # the ONE place a company is made
        assert source.count("admin_service.activate_subscription(") == 1                                   # ... and given its dated period
        for reimplemented in ("hash_password", "generate_qr_code", "qr_token", "companies_repo", "users_repo", "Company(", "plan_config_for_company", "uuid.uuid4().hex"):
            assert reimplemented not in source, reimplemented
        imports = "\n".join(re.findall(r"^(?:from|import) .*$", source, re.M))
        assert "import services.admin as admin_service" in imports
        assert "Company" not in imports                                                                   # nothing to construct

    def test_the_retired_conversion_neither_hashes_nor_creates_anything(self):
        tree = ast.parse((BACKEND / "sales" / "services" / "conversion.py").read_text())           # code only: its docstring names what was retired
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        used |= {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        for gone in ("create_company", "activate_subscription", "hash_password", "get_secret_value", "activity_service", "audit_service", "SalesCustomer", "SalesOnboarding"):
            assert gone not in used, gone

    def test_a_password_is_unwrapped_only_where_it_is_hashed_and_never_logged_or_recorded(self):
        # The Owner's and employees' passwords in customer_setup.py, a staff member's password (create / reset) in team.py: the
        # only services that hash one. No route, schema or repository ever holds the plain text - and the retired conversion,
        # which used to be one of them, no longer sees a password at all.
        unwrapped = sorted(p.relative_to(BACKEND) for p in (BACKEND / "sales").rglob("*.py") if "get_secret_value" in p.read_text())
        assert unwrapped == [Path("sales/services/customer_setup.py"), Path("sales/services/team.py")]
        team = (BACKEND / "sales" / "services" / "team.py").read_text()
        assert team.count("get_secret_value") == 2                                                         # create + reset
        for line in team.splitlines():
            if re.search(r"logger\.|activity_service\.record|audit_service\.record", line):
                assert not re.search(r"\b(password|new_password|plain)\b", line), line
        source = (BACKEND / "sales" / "services" / "customer_setup.py").read_text()
        assert source.count("get_secret_value") == 3                                                       # the owner's, a candidate employee's, a created employee's
        for line in source.splitlines():
            if re.search(r"logger\.|activity_service\.record|audit_service\.record", line):
                assert "password" not in line.lower(), line
        for match in re.finditer(r"(activity_service|audit_service)\.record\((.*?)\n    \)", source, re.S):
            assert "password" not in match.group(2).lower(), match.group(2)                                # nothing secret reaches a trail

    def test_nothing_is_ever_hard_deleted(self):
        for path in self.ALL:
            assert not re.search(r"\bdelete\(|\.delete\(|DELETE\s+FROM|TRUNCATE", path.read_text(), re.I), path.name

    def test_there_is_no_delete_route_for_conversion_customers_or_onboarding(self):
        from server import app
        for route in app.routes:
            path = getattr(route, "path", "")
            if path.startswith("/api/sales/") and (path.split("/")[3] in ("customers", "onboarding") or "/convert" in path):
                assert "DELETE" not in route.methods, path

    def test_permissions_never_role_names(self):
        for path in self.ALL:
            source = path.read_text()
            assert not re.search(r"sales_manager|sales_employee|lead_data_entry|onboarding_employee|super_admin", source), path.name

    def test_onboarding_never_reaches_through_the_lead_scope(self):
        """An Onboarding Employee must not reach the lead: onboarding lists are narrowed by the ONBOARDING scope alone."""
        for path in (BACKEND / "sales" / "services" / "onboarding.py", BACKEND / "sales" / "repositories" / "onboarding.py", BACKEND / "sales" / "services" / "onboarding_access.py"):
            assert "lead_visibility" not in path.read_text(), path.name

    def test_customers_and_onboarding_never_write_the_lead_or_the_company(self):
        for path in self.ALL:
            source = path.read_text()
            assert not re.search(r"\blead\.(pipeline_stage|assigned_to|assigned_at|lost_reason|closed_at|archived_at|updated_at|business_name)\s*=(?!=)", source), path.name
            assert not re.search(r"\bcompany\.\w+\s*=(?!=)", source), path.name

    def test_the_retired_conversion_does_not_touch_the_pipeline(self):
        source = (BACKEND / "sales" / "services" / "conversion.py").read_text()
        assert "transition_error" not in source and "AUTO_STAGE" not in source and "STAGE_WON" not in source

    def test_every_function_that_writes_also_records_its_timeline_event(self):
        for name, at_least in (("conversion", 0), ("onboarding", 3), ("customer_setup", 2)):
            source = (BACKEND / "sales" / "services" / f"{name}.py").read_text()
            writers = 0
            for fn in ast.walk(ast.parse(source)):
                if isinstance(fn, ast.AsyncFunctionDef):
                    text = ast.get_source_segment(source, fn)
                    if "db.flush()" in text:
                        writers += 1
                        assert "activity_service.record(" in text, (name, fn.name)                          # a change can never be saved without its event
            assert writers >= at_least, name
            if name == "conversion":
                assert writers == 0                                                                        # the retired module can not write at all

    def test_every_write_takes_a_row_lock_first(self):
        source = (BACKEND / "sales" / "services" / "onboarding.py").read_text()
        for fn in ("assign_onboarding", "change_stage", "update_onboarding"):
            body = source[source.index(f"async def {fn}("):]
            assert "_load_for_update(" in body.split("\nasync def ")[0], fn
        setup = (BACKEND / "sales" / "services" / "customer_setup.py").read_text()
        assert "for_update=True" in setup[setup.index("async def complete_setup("):].split("\nasync def ")[0]

    def test_the_sales_models_of_phase_4_point_only_where_they_should(self):
        from sales.models import SalesCustomer, SalesOnboarding
        assert {fk.column.table.name for fk in SalesCustomer.__table__.foreign_keys} == {"sales_leads", "companies", "users"}
        assert {fk.column.table.name for fk in SalesOnboarding.__table__.foreign_keys} == {"sales_customers", "users"}
