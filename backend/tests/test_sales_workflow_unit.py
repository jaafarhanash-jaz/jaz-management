"""JAZ Sales - simplified workflow: unit tier - no HTTP, no database writes.

The pure rules and builders behind the simplified workflow: the Lead Data Entry edit window (every scope combination - their own
latest ten AND only while unassigned), the rule that NOBODY moves a lead to `won` by hand (a lead is won only through the Customer
Setup), the subscription period arithmetic of the Customer Setup, the request schemas (unknown fields refused, passwords never printed,
bounds), the permission catalog / navigation drift guards (onboarding is no longer a section), the export builders (Excel
typed cells, no formula injection, RTL sheet; PDF with the embedded Arabic font, shaped Arabic, wrapped in reading order),
and static guarantees over the source (the Customer Setup reuses the platform's services instead of reimplementing them).
"""
from sales_test_utils import ALL_MODULES, ALL_PERMISSION_KEYS, SYSTEM_ROLE_PERMISSIONS, assert_scratch_target

assert_scratch_target(need_http=False)  # must run BEFORE importing database (which loads .env)

import io
import itertools
import re
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from sales import constants as C
from sales import permissions as P
from sales import setup_schemas as S
from sales.services import customer_setup
from sales.services import export
from sales.services.access import StaffContext
from sales.services import lead_access
from sales.services.lead_access import can_see, may_modify, needs_recent_leads

BACKEND = Path(__file__).resolve().parent.parent
SCOPES = (P.PERM_LEADS_SCOPE_ALL, P.PERM_LEADS_SCOPE_ASSIGNED, P.PERM_LEADS_SCOPE_INTAKE)


def ctx_with(*keys, user_id=None) -> StaffContext:
    uid = user_id or uuid.uuid4()
    return StaffContext({"id": str(uid), "name": "T", "role": "jaz_staff", "status": "active"}, uid, False, tuple(), frozenset(keys))


# =============================================================================
# the edit window
# =============================================================================
class TestEditWindow:
    def test_every_scope_combination(self):
        """may_modify never allows MORE than can_see, and differs from it exactly on intake-only leads outside the window - or
        already assigned to a salesperson."""
        me, other = uuid.uuid4(), uuid.uuid4()
        recent_lead, old_lead = uuid.uuid4(), uuid.uuid4()
        for n in range(len(SCOPES) + 1):
            for held in itertools.combinations(SCOPES, n):
                ctx = ctx_with(*held, user_id=me)
                for lead_id, created_by, assigned_to in itertools.product(
                    (recent_lead, old_lead), (me, other), (None, me, other),
                ):
                    allowed = may_modify(ctx, lead_id=lead_id, created_by=created_by, assigned_to=assigned_to,
                                         recent_ids=frozenset({recent_lead}))
                    visible = can_see(ctx, created_by=created_by, assigned_to=assigned_to)
                    if allowed:
                        assert visible, (held, created_by, assigned_to)
                    expected = (
                        P.PERM_LEADS_SCOPE_ALL in held
                        or (P.PERM_LEADS_SCOPE_ASSIGNED in held and assigned_to == me)
                        or (P.PERM_LEADS_SCOPE_INTAKE in held and created_by == me and assigned_to is None and lead_id == recent_lead)
                    )
                    assert allowed == expected, (held, lead_id == recent_lead, created_by == me, assigned_to)

    def test_data_entry_cannot_change_an_unassigned_lead_somebody_else_created(self):
        me, other, lead = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ctx = ctx_with(P.PERM_LEADS_SCOPE_INTAKE, user_id=me)
        assert can_see(ctx, created_by=other, assigned_to=None)                         # still visible (intake pool)
        assert not may_modify(ctx, lead_id=lead, created_by=other, assigned_to=None, recent_ids=frozenset({lead}))

    def test_data_entry_loses_a_lead_the_moment_a_salesperson_holds_it(self):
        """Their own latest ten AND still unassigned: assigning it (to anybody) takes it out of their hands, unassigning gives it back."""
        me, other, lead = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ctx = ctx_with(P.PERM_LEADS_SCOPE_INTAKE, user_id=me)
        recent = frozenset({lead})
        assert may_modify(ctx, lead_id=lead, created_by=me, assigned_to=None, recent_ids=recent)
        assert not may_modify(ctx, lead_id=lead, created_by=me, assigned_to=other, recent_ids=recent)
        assert not may_modify(ctx, lead_id=lead, created_by=me, assigned_to=me, recent_ids=recent)             # not even to themselves
        assert can_see(ctx, created_by=me, assigned_to=other)                                                   # still visible: read-only
        assert not may_modify(ctx, lead_id=lead, created_by=me, assigned_to=None, recent_ids=frozenset())      # outside the latest ten
        assert may_modify(ctx, lead_id=lead, created_by=me, assigned_to=None, recent_ids=recent)               # unassigned again: theirs again

    def test_the_manager_and_a_salesperson_are_unchanged(self):
        me, other, lead = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        manager = ctx_with(P.PERM_LEADS_SCOPE_ALL, user_id=me)
        salesperson = ctx_with(P.PERM_LEADS_SCOPE_ASSIGNED, user_id=me)
        for assigned_to in (None, me, other):
            for recent in (frozenset(), frozenset({lead})):
                assert may_modify(manager, lead_id=lead, created_by=other, assigned_to=assigned_to, recent_ids=recent)
        assert may_modify(salesperson, lead_id=lead, created_by=other, assigned_to=me, recent_ids=frozenset())
        assert not may_modify(salesperson, lead_id=lead, created_by=me, assigned_to=other, recent_ids=frozenset({lead}))

    def test_only_the_intake_route_reads_the_recent_leads(self):
        assert needs_recent_leads(ctx_with(P.PERM_LEADS_SCOPE_INTAKE))
        assert not needs_recent_leads(ctx_with(P.PERM_LEADS_SCOPE_INTAKE, P.PERM_LEADS_SCOPE_ALL))
        assert not needs_recent_leads(ctx_with(P.PERM_LEADS_SCOPE_ASSIGNED))
        assert not needs_recent_leads(ctx_with())

    def test_the_window_is_ten(self):
        assert C.RECENT_EDIT_WINDOW == 10


# =============================================================================
# nobody moves a lead to `won` by hand
# =============================================================================
def _lead_row(*, stage="negotiation", assigned_to=None, created_by=None):
    """A transient lead row (nothing is stored) as the read model hands it to lead_out."""
    from sales.models import SalesLead
    from sales.repositories.leads import LeadRow

    lead = SalesLead(id=uuid.uuid4(), created_by=created_by or uuid.uuid4(), assigned_to=assigned_to, pipeline_stage=stage, business_name="Won Co")
    return LeadRow(lead=lead, campaign_name=None, campaign_status=None, assignee_name=None, assignee_status=None, creator_name="Creator")


def _every_kind_of_caller():
    """A caller per system role, the Super Admin (every key of the catalog) and one holding EVERY permission through a custom role."""
    for role, keys in SYSTEM_ROLE_PERMISSIONS.items():
        yield role, ctx_with(*keys)
    yield "everything", ctx_with(*ALL_PERMISSION_KEYS)
    uid = uuid.uuid4()
    yield "super_admin", StaffContext({"id": str(uid), "name": "T", "role": "jaz_staff", "status": "active"}, uid, True, tuple(), frozenset(ALL_PERMISSION_KEYS))


class TestWonByHand:
    def test_there_is_no_permission_or_role_that_allows_it(self):
        """The manual-win privilege of the previous round is gone: nothing in lead_access decides who may win a lead."""
        assert not hasattr(lead_access, "may_mark_won")
        assert P.PERM_CUSTOMERS_CONVERT in ALL_PERMISSION_KEYS                             # the key itself stays (it guards the retired endpoints)

    def test_no_caller_is_ever_offered_won_on_any_lead(self):
        from sales.services.leads import lead_out

        for who, ctx in _every_kind_of_caller():
            for stage in C.PIPELINE_STAGES:
                for owner in (None, uuid.uuid4()):
                    offered = lead_out(_lead_row(stage=stage, assigned_to=owner), ctx, detail=False)["allowed_stages"]
                    assert C.STAGE_WON not in offered, (who, stage, owner)
                    assert offered == ([s for s in C.allowed_stages(stage, has_assignee=owner is not None)] if ctx.has(P.PERM_LEADS_CHANGE_STAGE) else []), (who, stage)

    def test_a_caller_who_can_move_stages_still_gets_every_other_stage(self):
        from sales.services.leads import lead_out

        for who, ctx in _every_kind_of_caller():
            if ctx.has(P.PERM_LEADS_CHANGE_STAGE):
                offered = lead_out(_lead_row(stage="negotiation", assigned_to=uuid.uuid4()), ctx, detail=True)["allowed_stages"]
                assert offered == ["contacted", "interested", "demo_scheduled", "demo_completed", "trial", "lost"], who

    def test_the_stage_change_refuses_won_before_anything_is_written(self):
        source = (BACKEND / "sales" / "services" / "leads.py").read_text()
        body = source[source.index("async def change_stage("):].split("\nasync def ")[0]
        gate = body.index("if body.stage == STAGE_WON:")
        assert "may_mark_won" not in body and "ctx.has(" not in body[gate:gate + 300]        # unconditional: no permission, no role
        assert gate < body.index("_ensure_active(") < body.index("lead.pipeline_stage = target") and gate < body.index("db.flush()")
        assert "won_requires_customer_setup" in body and "403" in body[gate:gate + 400]

    def test_the_stage_change_can_no_longer_write_a_win(self):
        """Nothing left in change_stage records a win: no `won` event, no accepted decision, no close time for won."""
        source = (BACKEND / "sales" / "services" / "leads.py").read_text()
        body = source[source.index("async def change_stage("):].split("\nasync def ")[0]
        assert "EVENT_LEAD_MARKED_WON" not in body and "OUTCOME_ACCEPTED" not in body
        assert body.count("STAGE_WON") == 1                                                    # ... only the refusal itself

    def test_the_only_writers_of_a_leads_stage(self):
        """Every function in Sales that assigns `pipeline_stage`. A new one has to be reviewed for a way around the won rule: the
        Customer Setup's "Agreed" (_mark_won) is the only way to `won`; the manual change (change_stage) refuses it."""
        import ast

        writers = set()
        for path in sorted((BACKEND / "sales").rglob("*.py")):
            for fn in ast.walk(ast.parse(path.read_text())):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for node in ast.walk(fn):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else []
                        if any(isinstance(t, ast.Attribute) and t.attr == "pipeline_stage" for t in targets):
                            writers.add((str(path.relative_to(BACKEND)), fn.name))
        assert writers == {
            ("sales/services/leads.py", "create_lead"),               # new / assigned at creation
            ("sales/services/leads.py", "_apply_assignment"),         # new -> assigned (AUTO_STAGE_ON_ASSIGN)
            ("sales/services/leads.py", "unassign_lead"),             # assigned -> new (AUTO_STAGE_ON_UNASSIGN)
            ("sales/services/leads.py", "change_stage"),              # the manual change: never to won
            ("sales/services/customer_setup.py", "_mark_won"),        # the salesperson's "Agreed": the ONLY way to won
            ("sales/services/batches.py", "reassign_work_batch"),     # reopens a LOST lead as assigned, never to won
        }
        assert C.STAGE_WON not in set(C.AUTO_STAGE_ON_ASSIGN.values()) | set(C.AUTO_STAGE_ON_UNASSIGN.values())
        assert all(C.STAGE_WON not in targets for targets in C.STAGE_TRANSITIONS.values())

    def test_a_win_is_recorded_only_by_the_customer_setup(self):
        """The accepted decision and the `lead_marked_won` event are written by the Customer Setup and by nothing else in Sales."""
        for path in (BACKEND / "sales").rglob("*.py"):
            source = path.read_text()
            if path.name not in ("customer_setup.py", "batches.py", "activity.py"):
                assert "OUTCOME_ACCEPTED" not in source and "EVENT_LEAD_MARKED_WON" not in source, path.name
        setup = (BACKEND / "sales" / "services" / "customer_setup.py").read_text()
        assert setup.count("OUTCOME_ACCEPTED") == 1 and setup.count("EVENT_LEAD_MARKED_WON") == 1

    def test_the_setups_win_is_private_to_the_customer_setup(self):
        for path in (BACKEND / "sales").rglob("*.py"):
            if path.name != "customer_setup.py":
                assert not re.search(r"(?<![A-Za-z0-9])_mark_won\(", path.read_text()), path.name
        source = (BACKEND / "sales" / "services" / "customer_setup.py").read_text()
        assert len(re.findall(r"(?<![A-Za-z0-9])_mark_won\(", source)) == 2                             # its definition and its one call, in complete_setup
        assert "await _mark_won(" in source[source.index("async def complete_setup("):]
        # ... and the win lives in the SAME transaction as the customer, the company and the subscription: complete_setup never commits
        # (the request's transaction does, once, after all of it) - a lead can not be committed as won without its customer
        body = source[source.index("async def complete_setup("):]
        assert ".commit(" not in source and "await _mark_won(" in body and "admin_service.create_company(" in body and "db.add(customer)" in body
        assert body.index("admin_service.activate_subscription(") < body.index("activity_service.EVENT_LEAD_CONVERTED")     # the trail closes the transaction


# =============================================================================
# customer setup: subscription period
# =============================================================================
class TestSubscriptionPeriod:
    def test_trial_is_exactly_seven_times_24_hours_from_the_creation_instant(self):
        plan = SimpleNamespace(duration_months=12)
        created = datetime(2026, 9, 25, 12, 0, 30, 123456, tzinfo=timezone.utc)     # 25/09 15:00:30 in Baghdad
        start, end, exact = customer_setup.subscription_period("trial", plan, now=created)
        assert exact is True
        assert start == datetime(2026, 9, 25, 12, 0, 30, tzinfo=timezone.utc)      # the creation instant, to the second
        assert end - start == timedelta(hours=7 * 24) == C.TRIAL_DURATION
        assert end == datetime(2026, 10, 2, 12, 0, 30, tzinfo=timezone.utc)
        # the example of the specification, in the application's own time zone: 25/09 15:00 -> 02/10 15:00
        baghdad = timezone(timedelta(hours=3))
        start, end, _ = customer_setup.subscription_period("trial", plan, now=datetime(2026, 9, 25, 15, 0, tzinfo=baghdad))
        assert (start.astimezone(baghdad), end.astimezone(baghdad)) == (
            datetime(2026, 9, 25, 15, 0, tzinfo=baghdad), datetime(2026, 10, 2, 15, 0, tzinfo=baghdad))
        assert C.TRIAL_DAYS == 7

    @pytest.mark.parametrize("clock", ["00:00:00", "12:34:56", "23:59:59"])
    def test_a_trial_is_never_rounded_to_calendar_dates(self, clock):
        h, m, sec = (int(x) for x in clock.split(":"))
        created = datetime(2026, 1, 31, h, m, sec, tzinfo=timezone.utc)
        start, end, _ = customer_setup.subscription_period("trial", SimpleNamespace(duration_months=1), now=created)
        assert start == created and end == created + timedelta(days=7)

    def test_paid_follows_the_plan_like_the_platforms_renew(self):
        for months in (1, 3, 12):
            now = datetime(2026, 1, 31, 17, 45, tzinfo=timezone.utc)
            start, end, exact = customer_setup.subscription_period("paid", SimpleNamespace(duration_months=months), now=now)
            assert exact is False
            assert start == datetime(2026, 1, 31, tzinfo=timezone.utc)             # today, calendar date
            assert end - start == timedelta(days=30 * months)

    def test_paid_uses_the_platforms_utc_date_even_when_baghdad_is_already_tomorrow(self):
        now = datetime(2026, 1, 31, 22, 30, tzinfo=timezone.utc)                 # 01/02 01:30 in Baghdad
        start, end, exact = customer_setup.subscription_period("paid", SimpleNamespace(duration_months=1), now=now)
        assert (start, end, exact) == (datetime(2026, 1, 31, tzinfo=timezone.utc), datetime(2026, 3, 2, tzinfo=timezone.utc), False)
        trial_start, trial_end, _ = customer_setup.subscription_period("trial", SimpleNamespace(duration_months=1), now=now)
        assert trial_start == now and trial_end == now + timedelta(hours=168)     # a trial is the instant, whatever the calendar

    def test_the_setup_customer_is_live_and_gets_no_onboarding(self):
        assert customer_setup.SETUP_CUSTOMER_STATUS == "activated" and customer_setup.SETUP_CUSTOMER_STATUS in C.CUSTOMER_STATUSES
        source = (BACKEND / "sales" / "services" / "customer_setup.py").read_text()
        assert "SalesOnboarding" not in source

    def test_paid_has_no_payment_step(self):
        """MVP decision: Sales creates a Paid subscription with no payment verification - nothing about a payment exists in the
        request, and the setup never touches the payment machinery."""
        assert not {f for f in S.CustomerSetup.model_fields if "pay" in f or "card" in f or "invoice" in f}
        source = (BACKEND / "sales" / "services" / "customer_setup.py").read_text()
        for payment in ("payment_transactions", "stripe", "checkout", "payments_repo"):
            assert payment not in source.lower(), payment


class TestCoreExpiryRule:
    """services/auth.py::subscription_has_ended - the ONE rule the platform blocks owners and employees on."""

    NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)

    def company(self, end, exact):
        return SimpleNamespace(subscription_end_date=end, subscription_ends_exactly=exact)

    def test_an_exact_end_is_an_instant(self):
        from services.auth import subscription_has_ended
        assert subscription_has_ended(self.company(self.NOW, True), self.NOW) is True                          # AT the end
        assert subscription_has_ended(self.company(self.NOW - timedelta(seconds=1), True), self.NOW) is True
        assert subscription_has_ended(self.company(self.NOW + timedelta(microseconds=1), True), self.NOW) is False

    def test_every_other_end_keeps_its_calendar_day_meaning(self):
        from services.auth import subscription_has_ended
        midnight = datetime(2026, 10, 2, tzinfo=timezone.utc)
        assert subscription_has_ended(self.company(midnight, False), self.NOW) is False       # ending today: active all day
        assert subscription_has_ended(self.company(self.NOW - timedelta(hours=11), False), self.NOW) is False
        assert subscription_has_ended(self.company(midnight - timedelta(seconds=1), False), self.NOW) is True   # yesterday
        # an object from before the column existed behaves like the old rule
        assert subscription_has_ended(SimpleNamespace(subscription_end_date=midnight), self.NOW) is False
        assert subscription_has_ended(self.company(None, True), self.NOW) is False               # no end: never ends

    def test_the_super_admins_actions_run_to_calendar_dates(self):
        source = (BACKEND / "services" / "admin.py").read_text()
        assert "company.subscription_ends_exactly = exact" in source                         # Activate: exact only when asked
        assert "company.subscription_ends_exactly = False" in source                         # Renew: always a calendar date
        assert "subscription_has_ended(company)" in source                                   # Reactivate: the same rule
        assert "def activate_subscription(db: AsyncSession, company_id: str, data, *, exact: bool = False)" in source


# =============================================================================
# schemas
# =============================================================================
def _setup_body(**overrides) -> dict:
    body = {
        "business_name": "Acme", "owner_name": "Owner", "owner_email": "o@example.com", "owner_phone": "+15550001111",
        "owner_password": "Owner#Pass-2026", "subscription_plan_id": str(uuid.uuid4()), "subscription_type": "trial",
    }
    body.update(overrides)
    return body


class TestSetupSchemas:
    def test_employees_and_tasks_are_optional(self):
        body = S.CustomerSetup(**_setup_body())
        assert body.employees == [] and body.tasks == [] and body.confirm_duplicates is False

    @pytest.mark.parametrize("field", ["company_id", "status", "role", "assigned_to", "customer_status", "subscription_status"])
    def test_unknown_fields_are_refused(self, field):
        with pytest.raises(ValidationError):
            S.CustomerSetup(**_setup_body(**{field: "x"}))

    def test_subscription_type_is_trial_or_paid(self):
        for ok in ("trial", "paid"):
            S.CustomerSetup(**_setup_body(subscription_type=ok))
        for bad in ("free", "", None, "TRIAL"):
            with pytest.raises(ValidationError):
                S.CustomerSetup(**_setup_body(subscription_type=bad))

    def test_passwords_never_print(self):
        secret = "Very#Secret-Pass-1"
        body = S.CustomerSetup(**_setup_body(
            owner_password=secret,
            employees=[{"name": "E", "email": "e@example.com", "phone": "+15550002222", "password": secret}],
        ))
        for text in (repr(body), str(body), body.model_dump_json()):
            assert secret not in text

    def test_employee_and_task_bounds(self):
        employee = {"name": "E", "email": "e@example.com", "phone": "+15550002222", "password": "x"}
        with pytest.raises(ValidationError):
            S.CustomerSetup(**_setup_body(employees=[employee] * (C.MAX_SETUP_EMPLOYEES + 1)))
        with pytest.raises(ValidationError):
            S.SetupEmployee(**{**employee, "name": "   "})
        with pytest.raises(ValidationError):
            S.SetupEmployee(**{**employee, "role": "company_owner"})
        task = {"title": "Do it", "assignee": 0, "due_date": "2030-01-01"}
        S.SetupTask(**task)
        for bad in ({"assignee": -1}, {"assignee": C.MAX_SETUP_EMPLOYEES}, {"priority": "urgent"}, {"title": " "}, {"due_date": None}):
            with pytest.raises(ValidationError):
                S.SetupTask(**{**task, **bad})

    def test_task_priorities_match_the_core_tasks_check(self):
        from models import Task
        check = next(c for c in Task.__table__.constraints if getattr(c, "name", "") == "ck_tasks_priority")
        assert set(re.findall(r"'([a-z]+)'", str(check.sqltext))) == set(C.TASK_PRIORITIES)

    def test_distribution_mode_is_a_closed_set(self):
        S.DistributionUpdate(auto_distribution_enabled=True)
        with pytest.raises(ValidationError):
            S.DistributionUpdate(auto_distribution_enabled=True, distribution_mode="by_region")
        with pytest.raises(ValidationError):
            S.DistributionUpdate(auto_distribution_enabled=True, last_assigned_user_id=str(uuid.uuid4()))


# =============================================================================
# catalog / navigation drift guards
# =============================================================================
class TestCatalog:
    def test_the_new_permissions_are_in_the_catalog(self):
        assert {P.PERM_SETTINGS_MANAGE, P.PERM_LEADS_EXPORT, P.PERM_CUSTOMERS_SETUP} <= set(P.ALL_PERMISSIONS)
        assert set(P.ALL_PERMISSIONS) == ALL_PERMISSION_KEYS

    def test_onboarding_is_no_longer_a_section(self):
        keys = [m["key"] for m in P.MODULES]
        assert "onboarding" not in keys and keys == ALL_MODULES

    def test_the_approved_grants(self):
        assert {P.PERM_SETTINGS_MANAGE, P.PERM_LEADS_EXPORT} <= SYSTEM_ROLE_PERMISSIONS["sales_manager"]
        # the Customer Setup has its own key; the Phase-4 conversion (a company with no subscription dates) stays the manager's
        assert P.PERM_CUSTOMERS_SETUP in SYSTEM_ROLE_PERMISSIONS["sales_employee"] and P.PERM_CUSTOMERS_SETUP in SYSTEM_ROLE_PERMISSIONS["sales_manager"]
        assert P.PERM_CUSTOMERS_CONVERT not in SYSTEM_ROLE_PERMISSIONS["sales_employee"]
        assert P.PERM_LEADS_DELETE in SYSTEM_ROLE_PERMISSIONS["lead_data_entry"]
        for role in ("sales_employee", "lead_data_entry", "onboarding_employee"):
            assert not {P.PERM_SETTINGS_MANAGE, P.PERM_LEADS_EXPORT} & SYSTEM_ROLE_PERMISSIONS[role], role
        assert not {P.PERM_CUSTOMERS_CONVERT, P.PERM_CUSTOMERS_SETUP} & SYSTEM_ROLE_PERMISSIONS["lead_data_entry"]

    def test_the_migration_seeds_exactly_the_code_catalog_additions(self):
        source = (BACKEND / "migrations" / "versions" / "a3f8c2d7e915_sales_simplified_workflow.py").read_text()
        for key in (P.PERM_SETTINGS_MANAGE, P.PERM_LEADS_EXPORT):
            assert f"'{key}'" in source
        assert "down_revision: Union[str, Sequence[str], None] = 'b6d1f3a8c294'" in source
        for literal in ("('equal')", "('trial','paid')"):
            assert literal in source
        assert C.DISTRIBUTION_MODES == ("equal",) and C.SUBSCRIPTION_TYPES == ("trial", "paid")

    def test_the_exact_trial_and_retired_role_migration(self):
        import uuid as _uuid
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from models import Company

        source = (BACKEND / "migrations" / "versions" / "c5e1b9a4d2f7_exact_trials_and_dormant_onboarding_role.py").read_text()
        assert "down_revision: Union[str, Sequence[str], None] = 'a3f8c2d7e915'" in source
        script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
        assert script.get_revision("e7a2d4c9b1f3").down_revision == "c5e1b9a4d2f7"
        # the retired role is the Phase-1 role, by its frozen uuid5 id - never deleted, only is_active
        expected = str(_uuid.uuid5(_uuid.UUID("6f0c1c1e-5a3a-4a0e-9d6a-2d0e5b7f0a10"), "jaz.staff_roles.onboarding_employee"))
        assert f"_ONBOARDING_ROLE_ID = '{expected}'" in source
        assert re.findall(r"UPDATE staff_roles SET is_active = (\w+)", source) == ["false", "true"]            # up, down
        for destructive in ("DELETE FROM", "drop_table", "DROP TABLE"):
            assert destructive not in source, destructive
        setup_mig = (BACKEND / "migrations" / "versions" / "e7a2d4c9b1f3_sales_customer_setup_permission.py").read_text()
        assert script.get_heads() == ["f2b6d8a1c4e9"]                      # (the batches' revision, chained after the setup permission)
        assert script.get_revision("f2b6d8a1c4e9").down_revision == "e7a2d4c9b1f3"
        assert "_GRANTS = [('sales_manager', 'sales.customers.setup'), ('sales_employee', 'sales.customers.setup')]" in setup_mig
        assert "_WITHDRAWN = [('sales_employee', 'sales.customers.convert')]" in setup_mig      # exactly the grant a3f8c2d7e915 added
        assert "drop_" not in setup_mig and "DELETE FROM staff_permissions WHERE key IN" in setup_mig   # downgrade removes only its own key
        column = Company.__table__.c.subscription_ends_exactly
        assert column.nullable is False and str(column.server_default.arg) == "false"
        assert P.RETIRED_SYSTEM_ROLE_KEYS == ("onboarding_employee",) and set(P.RETIRED_SYSTEM_ROLE_KEYS) <= set(P.SYSTEM_ROLE_KEYS)


# =============================================================================
# export builders
# =============================================================================
def _record(**overrides) -> dict:
    base = {c.key: None for c in export.COLUMNS}
    base.update({
        "id": str(uuid.uuid4()), "business_name": "Acme", "contact_name": "Ali", "phone": "+9647700000000", "city": "Baghdad",
        "source": "Website", "stage": "Won", "assigned_to": "Sara", "created_at": datetime(2026, 9, 25, 10, 30),
        "updated_at": datetime(2026, 9, 25, 11, 0), "estimated_value": 1250.5, "archived": "No",
    })
    base.update(overrides)
    return base


def _xlsx_parts(content: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return {name: z.read(name).decode("utf-8") for name in z.namelist() if name.endswith(".xml")}


class TestExcel:
    def test_one_typed_row_per_lead_with_a_frozen_filtered_header(self):
        records = [_record(business_name=f"Co {i}") for i in range(3)]
        parts = _xlsx_parts(export.to_xlsx(records, "en", [("Rows", "3")]))
        sheet = parts["xl/worksheets/sheet1.xml"]
        assert sheet.count("<row ") == 4                                    # header + 3
        assert "<autoFilter" in sheet and 'state="frozen"' in sheet
        assert "<f>" not in sheet                                           # not one formula
        strings = parts["xl/sharedStrings.xml"]
        for column in export.COLUMNS:
            assert column.en in strings
        assert "Co 2" in strings

    def test_text_that_looks_like_a_formula_stays_text(self):
        evil = '=HYPERLINK("http://evil.example","x")'
        parts = _xlsx_parts(export.to_xlsx([_record(business_name=evil, contact_name="+1-2", city="@SUM(A1)")], "en", []))
        sheet = parts["xl/worksheets/sheet1.xml"]
        assert "<f>" not in sheet and "HYPERLINK" not in sheet               # the value lives in the string table, as text
        assert "HYPERLINK" in parts["xl/sharedStrings.xml"]

    def test_arabic_sheet_is_right_to_left_with_arabic_headers(self):
        parts = _xlsx_parts(export.to_xlsx([_record()], "ar", [("الصفوف", "1")]))
        assert 'rightToLeft="1"' in parts["xl/worksheets/sheet1.xml"]
        assert "الشركة" in parts["xl/sharedStrings.xml"] and "معرّف العميل المحتمل" in parts["xl/sharedStrings.xml"]

    def test_the_columns_never_include_internal_fields(self):
        keys = {c.key for c in export.COLUMNS}
        for forbidden in ("notes", "description", "name_norm", "phone_norm", "email_norm", "password", "latitude", "created_by_id"):
            assert forbidden not in keys
        assert {"id", "business_name", "contact_name", "phone", "city", "source", "campaign", "assigned_to", "stage",
                "created_at", "updated_at"} <= keys


class TestPdf:
    @pytest.mark.parametrize("lang", ["ar", "en"])
    def test_a_pdf_with_the_embedded_arabic_font(self, lang):
        records = [_record(business_name="شركة الأمل للتجارة", contact_name="علي حسن", city="بغداد")] * 5
        content = export.to_pdf(records, lang, [("Rows", "5")])
        assert content.startswith(b"%PDF-") and b"%%EOF" in content[-64:]
        assert b"IBMPlexSansArabic" in content

    def test_many_rows_make_many_pages(self):
        content = export.to_pdf([_record()] * 200, "en", [])
        assert content.count(b"/Type /Page\n") + content.count(b"/Type /Page ") + content.count(b"/Type /Page>") >= 2 or content.count(b"/Page") > 3

    def test_arabic_is_shaped_and_reordered_latin_is_untouched(self):
        assert export.visual("Acme Trading") == "Acme Trading"
        shaped = export.visual("شركة")
        assert shaped != "شركة" and all("ﹰ" <= ch <= "ﻼ" or ch == " " for ch in shaped)   # presentation forms

    def test_wrapping_happens_in_reading_order(self):
        measure = lambda s: len(s) * 5.0
        lines = export.wrap("one two three four five six", 50, measure)
        assert lines == ["one two", "three four", "five six"]
        arabic = export.wrap("الأول الثاني الثالث", 60, measure)
        assert len(arabic) >= 2
        assert arabic[0] == export.visual("الأول الثاني") or arabic[0] == export.visual("الأول")    # the FIRST words stay on the FIRST line
        long_word = export.wrap("x" * 40, 50, measure)
        assert all(measure(line) <= 50 for line in long_word) and "".join(long_word).startswith("x")
        many = export.wrap(" ".join(["word"] * 50), 50, measure, max_lines=2)
        assert len(many) == 2 and many[-1].endswith("…")


# =============================================================================
# static guarantees
# =============================================================================
class TestRouteGuards:
    """Exactly the permissions each simplified-workflow route requires (a guard losing one key must fail here)."""

    EXPECTED = {
        ("POST", "/api/sales/leads/{lead_id}/setup/preflight"): ("sales.customers.setup", "sales.leads.change_stage"),
        ("POST", "/api/sales/leads/{lead_id}/setup"): ("sales.customers.setup", "sales.leads.change_stage"),
        ("GET", "/api/sales/leads/export"): ("sales.leads.view", "sales.leads.export"),
        ("GET", "/api/sales/settings/distribution"): ("sales.settings.manage",),
        ("PUT", "/api/sales/settings/distribution"): ("sales.settings.manage",),
        ("GET", "/api/sales/queue"): ("sales.leads.view",),
        ("POST", "/api/sales/leads/{lead_id}/convert"): ("sales.customers.convert",),          # the Phase-4 conversion: unchanged
        ("POST", "/api/sales/leads/{lead_id}/convert/preflight"): ("sales.customers.convert",),
    }

    def test_each_route_requires_exactly_its_permissions(self):
        from sales.router import sales_router
        walk = lambda dep: [dep] + [x for d in dep.dependencies for x in walk(d)]  # noqa: E731
        by_route = {(m, r.path): r for r in sales_router.routes for m in r.methods - {"HEAD", "OPTIONS"}}
        for key, needed in self.EXPECTED.items():
            guards = [d.call.__sales_permissions__ for d in walk(by_route[key].dependant) if getattr(d.call, "__sales_guard__", None) == "permission"]
            assert guards == [needed], (key, guards)


class TestSource:
    def test_the_setup_reuses_the_platforms_services(self):
        source = (BACKEND / "sales" / "services" / "customer_setup.py").read_text()
        assert source.count("admin_service.create_company(") == 1
        assert source.count("admin_service.activate_subscription(") == 1
        assert source.count("employees_service.create_employee(") == 1
        assert source.count("tasks_service.create_task(") == 1
        for reimplemented in ("hash_password", "generate_qr_code", "companies_repo", "users_repo", "Company(", "User(",
                              "plan_config_for_company", "tasks_repo"):
            assert reimplemented not in source, reimplemented

    def test_passwords_are_never_logged_or_recorded_by_the_setup(self):
        source = (BACKEND / "sales" / "services" / "customer_setup.py").read_text()
        for line in source.splitlines():
            if re.search(r"logger\.", line):
                assert "password" not in line.lower(), line
        for match in re.finditer(r"(activity_service|audit_service)\.record\((.*?)\n    \)", source, re.S):
            assert "password" not in match.group(2).lower(), match.group(2)[:200]

    def test_the_export_writes_text_as_text(self):
        source = (BACKEND / "sales" / "services" / "export.py").read_text()
        assert '"strings_to_formulas": False' in source and "write_string" in source
        assert ".write(" not in source                                   # never the interpreting generic writer
