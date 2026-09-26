"""JAZ Sales - the RETIRED lead conversion (HTTP integration tier + source guards).

POST /leads/{id}/convert and POST /leads/{id}/convert/preflight are RETIRED: the Customer Setup (POST /leads/{id}/setup - the
salesperson's "Agreed") replaces them. The old flow made the JAZ company with an ACTIVE subscription that had NO start and NO end
date, i.e. a subscription that never expires, so nothing may reach it any more. This module pins that:

  * both endpoints answer a controlled 410 `conversion_retired` (with where to go instead) to everybody who may reach them, for
    every lead and every body - and 403 / 401 exactly as before to everybody else;
  * an attempt writes NOTHING (no company, account, customer, timeline event or audit event; the lead is untouched) and its
    answer never depends on the lead, so it can not be used to probe which leads or owner emails exist;
  * the replacement (the real Customer Setup) still produces the customer, and only with a DATED subscription;
  * the source guarantees: the Customer Setup is the only caller of the platform's company service in Sales, it activates the
    dated period in the same transaction, and the retired service module can no longer write anything;
  * GET /leads/{id}/conversion (the status read) no longer offers the conversion.

The customers / onboarding records the old flow created are HISTORICAL DATA the dormant architecture, the customer views and
the reports still read: the other Phase-4 modules seed them with `convert()` (a legacy data fixture, never the API).
Who may reach the retired endpoints is covered per role in test_sales_customers_rbac.py.
"""
import ast
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from models import Company
from sales.models import SalesCustomer, SalesLead, SalesOnboarding

from sales_customer_test_utils import (
    OWNER_PASSWORD,
    PHANTOM_ID,
    api,
    assign,
    audit_events,
    convert,
    convert_body,
    count_companies,
    count_customers,
    count_users,
    create_lead,
    create_staff,
    db_row,
    errors_of,
    make_custom_role,
    make_personas,
    make_plan,
    phase4_events,
    retired_conversion,
    retire_roles,
    run_db,
    set_stage,
    unique_lead_email,
    unique_lead_phone,
    won_lead,
)
from sales_activity_test_utils import activities
from sales_test_utils import admin_token, assert_scratch_target, auth, owner_token, owner_user  # noqa: F401

BACKEND = Path(__file__).resolve().parent.parent
SALES = BACKEND / "sales"

REPLACEMENT = "POST /api/sales/leads/{lead_id}/setup"
PATHS = ["convert", "convert/preflight"]


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "cv")


@pytest.fixture(scope="module")
def mgr(p):
    return p["manager"]["headers"]


@pytest.fixture()
def lead(p, mgr):
    return won_lead(mgr, p["employee"]["id"])


def assert_retired(response):
    """The one controlled answer: 410 Gone, a stable code, a pointer to the replacement, and nothing else of substance."""
    assert response.status_code == 410, f"expected the retired-conversion 410, got {response.status_code} {response.text[:300]}"
    detail = errors_of(response)
    assert detail["code"] == "conversion_retired" and detail["field"] == "lead"
    assert detail["replacement"] == REPLACEMENT
    assert "Customer Setup" in detail["message"]
    assert set(detail) == {"field", "message", "code", "replacement"}
    return detail


# =============================================================================
class TestTheEndpointsAreRetired:
    @pytest.mark.parametrize("path", PATHS)
    def test_a_manager_gets_a_controlled_410(self, lead, mgr, path):
        assert_retired(retired_conversion(mgr, lead["id"], path, convert_body(lead)))

    @pytest.mark.parametrize("path", PATHS)
    def test_the_super_admin_gets_the_same_answer(self, lead, admin_h, path):
        assert_retired(retired_conversion(admin_h, lead["id"], path, convert_body(lead)))

    @pytest.mark.parametrize("path", PATHS)
    @pytest.mark.parametrize("body", [None, {}, {"nope": 1}, {"business_name": ""}, {"owner_password": "x"}, {"subscription_plan_id": "not-a-uuid"}],
                             ids=["no-body", "empty", "unknown-field", "blank-name", "weak-password", "bad-plan-id"])
    def test_whatever_is_sent_the_answer_is_the_same_410(self, lead, mgr, path, body):
        # no request validation any more: a retired endpoint never answers 400 / 422 about a body it will not read
        assert_retired(retired_conversion(mgr, lead["id"], path, body))

    @pytest.mark.parametrize("path", PATHS)
    def test_the_answer_does_not_depend_on_the_lead(self, p, mgr, path):
        """Won, open, lost, archived, unknown, malformed: the same 410 - the endpoint never looks the lead up, so it can not be
        used to find out which leads exist, where they stand, or which owner emails are taken."""
        assigned = create_lead(mgr)
        assign(mgr, assigned["id"], p["employee"]["id"])
        lost = create_lead(mgr)
        assign(mgr, lost["id"], p["employee"]["id"])
        set_stage(mgr, lost["id"], "lost", lost_reason="not_interested")
        archived = won_lead(mgr, p["employee"]["id"])
        assert api("DELETE", f"/api/sales/leads/{archived['id']}", mgr).status_code in (200, 204)
        converted = won_lead(mgr, p["employee"]["id"])
        convert(mgr, converted)                                    # a lead the (legacy) conversion already turned into a customer
        answers = [
            assert_retired(retired_conversion(mgr, lead_id, path, {}))
            for lead_id in (assigned["id"], lost["id"], archived["id"], converted["id"], PHANTOM_ID, "not-a-uuid")
        ]
        assert all(answer == answers[0] for answer in answers)

    def test_the_preflight_no_longer_reports_taken_owner_emails(self, lead, mgr, owner_user):
        taken = retired_conversion(mgr, lead["id"], "convert/preflight", {"owner_email": owner_user["email"]})
        free = retired_conversion(mgr, lead["id"], "convert/preflight", {"owner_email": unique_lead_email("owner")})
        assert taken.status_code == free.status_code == 410 and taken.json() == free.json()

    @pytest.mark.parametrize("path", PATHS)
    def test_the_owning_sales_employee_still_may_not_use_it(self, p, lead, path):
        # they hold sales.customers.setup, not sales.customers.convert: a 403 as ever, not the 410
        r = retired_conversion(p["employee"]["headers"], lead["id"], path, convert_body(lead))
        assert r.status_code == 403

    @pytest.mark.parametrize("path", PATHS)
    @pytest.mark.parametrize("who", ["employee2", "data_entry", "onboarding", "no_roles"])
    def test_nobody_else_gets_past_the_permission_guard(self, p, lead, path, who):
        assert retired_conversion(p[who]["headers"], lead["id"], path, convert_body(lead)).status_code == 403

    @pytest.mark.parametrize("path", PATHS)
    def test_without_a_token_it_is_refused_before_anything_else(self, lead, path):
        assert retired_conversion(None, lead["id"], path, convert_body(lead)).status_code in (401, 403)

    @pytest.mark.parametrize("path", PATHS)
    def test_a_holder_of_only_the_view_permission_cannot_reach_it(self, lead, admin_h, path):
        role = make_custom_role(["sales.access", "sales.customers.view", "sales.leads.view", "sales.leads.scope_all"])
        try:
            viewer = create_staff(admin_h, [role], "conv-viewer")
            assert retired_conversion(viewer["headers"], lead["id"], path, convert_body(lead)).status_code == 403
        finally:
            retire_roles([role])

    @pytest.mark.parametrize("path", PATHS)
    def test_the_answer_never_echoes_the_owner_password(self, lead, mgr, path):
        r = retired_conversion(mgr, lead["id"], path, convert_body(lead))
        assert OWNER_PASSWORD not in r.text and "password" not in r.text.lower()

    def test_both_routes_are_documented_as_deprecated_with_the_410(self):
        from server import app

        schema = app.openapi()
        for path in PATHS:
            operation = schema["paths"][f"/api/sales/leads/{{lead_id}}/{path}"]["post"]
            assert operation["deprecated"] is True
            assert "410" in operation["responses"] and "Customer Setup" in operation["responses"]["410"]["description"]
            assert "requestBody" not in operation                                  # no body model: nothing to validate, nothing to read


# =============================================================================
class TestAnAttemptWritesNothing:
    @pytest.mark.parametrize("path", PATHS)
    @pytest.mark.parametrize("who", ["manager", "super_admin"])
    def test_nothing_is_created_and_the_lead_is_untouched(self, p, mgr, admin_h, lead, path, who):
        headers = mgr if who == "manager" else admin_h
        body = convert_body(lead)
        before = self._state(mgr, lead, body)
        for _ in range(2):
            assert_retired(retired_conversion(headers, lead["id"], path, body))
        after = self._state(mgr, lead, body)
        assert after == before
        assert after["companies"] == 0 and after["accounts"] == 0 and after["customers"] == 0 and after["onboarding"] == 0

    @staticmethod
    def _state(mgr, lead, body):
        row = db_row(SalesLead, id=uuid.UUID(lead["id"]))
        return {
            "companies": count_companies(body["business_name"]),
            "accounts": count_users(body["owner_email"]),
            "customers": count_customers(lead["id"]),
            "onboarding": run_db(lambda db: _count_onboarding_of_lead(db, lead["id"])),
            "lead": {k: row[k] for k in ("pipeline_stage", "updated_at", "assigned_to", "archived_at", "closed_at", "lost_reason")},
            "timeline": [(e["event_type"], e["seq"]) for e in activities(mgr, lead["id"])],
            "phase4_events": phase4_events(mgr, lead["id"]),
            "audit": audit_events(lead["id"]),
        }

    def test_no_customer_appears_in_the_customer_list(self, p, mgr, lead):
        # (narrowed to this lead's own business name: other test workers make customers at the same time)
        url = f"/api/sales/customers?limit=1&q={lead['business_name']}"
        assert api("GET", url, mgr).json()["total"] == 0
        retired_conversion(mgr, lead["id"], "convert", convert_body(lead))
        assert api("GET", url, mgr).json()["total"] == 0

    def test_a_lead_can_still_be_set_up_after_an_attempt(self, p, mgr, lead):
        """The refused attempt does not lock or mark the lead: the real Customer Setup completes as if nothing happened."""
        body = convert_body(lead)
        assert_retired(retired_conversion(mgr, lead["id"], "convert", body))
        r = _setup(p["employee"]["headers"], lead, make_plan())
        assert r.status_code == 201, r.text[:400]
        assert count_customers(lead["id"]) == 1


async def _count_onboarding_of_lead(db, lead_id):
    return (await db.execute(
        select(func.count()).select_from(SalesOnboarding).join(SalesCustomer, SalesCustomer.id == SalesOnboarding.customer_id)
        .where(SalesCustomer.lead_id == uuid.UUID(lead_id))
    )).scalar_one()


def _setup(headers, lead, plan_id, **overrides):
    body = {
        "business_name": lead["business_name"], "owner_name": "Owner Person", "owner_email": unique_lead_email("owner"),
        "owner_phone": unique_lead_phone(), "owner_password": OWNER_PASSWORD, "subscription_plan_id": plan_id, "subscription_type": "trial",
    }
    body.update(overrides)
    return api("POST", f"/api/sales/leads/{lead['id']}/setup", headers, body)


# =============================================================================
class TestTheCustomerSetupIsTheOnlyWay:
    """What replaced the conversion still works - and it is the only thing that makes a customer and a company."""

    def test_the_customer_setup_creates_the_customer_with_a_dated_subscription(self, p, mgr, lead):
        r = _setup(p["employee"]["headers"], lead, make_plan())
        assert r.status_code == 201, r.text[:400]
        customer = r.json()["customer"]
        company = db_row(Company, id=uuid.UUID(customer["company"]["id"]))
        assert company["subscription_status"] == "active"
        assert company["subscription_start_date"] is not None and company["subscription_end_date"] is not None
        assert company["subscription_end_date"] > company["subscription_start_date"]
        assert company["subscription_ends_exactly"] is True                        # the trial's exact 7 x 24h end

    def test_a_paid_setup_is_dated_too(self, p, mgr, lead):
        r = _setup(p["employee"]["headers"], lead, make_plan(), subscription_type="paid")
        assert r.status_code == 201, r.text[:400]
        company = db_row(Company, id=uuid.UUID(r.json()["customer"]["company"]["id"]))
        assert company["subscription_start_date"] is not None and company["subscription_end_date"] is not None

    def test_after_a_setup_the_retired_endpoints_still_answer_410_and_the_lead_is_converted_once(self, p, mgr, lead):
        assert _setup(p["employee"]["headers"], lead, make_plan()).status_code == 201
        for path in PATHS:
            assert_retired(retired_conversion(mgr, lead["id"], path, convert_body(lead)))
        assert count_customers(lead["id"]) == 1

    def test_no_customer_made_by_a_customer_setup_has_an_undated_subscription(self):
        """The DB-wide invariant behind the retirement. Only a Customer Setup records the subscription type on its customer (the
        retired conversion never did), so every customer that has one was made by a setup - and its company has a start AND an
        end date. (The undated companies a database can hold are the historical conversions', whose customers have none.)"""
        async def _undated(db):
            return (await db.execute(
                select(func.count()).select_from(SalesCustomer).join(Company, Company.id == SalesCustomer.company_id)
                .where(SalesCustomer.subscription_type.is_not(None), (Company.subscription_start_date.is_(None) | Company.subscription_end_date.is_(None)))
            )).scalar_one()

        assert run_db(_undated) == 0

    def test_the_legacy_fixture_is_the_only_undated_kind_and_a_setup_customer_records_its_type(self, p, mgr):
        made = _setup(p["employee"]["headers"], won_lead(mgr, p["employee"]["id"]), make_plan(), subscription_type="paid")
        assert made.status_code == 201
        customer = db_row(SalesCustomer, id=uuid.UUID(made.json()["customer"]["id"]))
        assert customer["subscription_type"] == "paid" and customer["status"] == "activated"
        assert run_db(lambda db: _count_onboarding_of_customer(db, customer["id"])) == 0      # the setup IS the onboarding


async def _count_onboarding_of_customer(db, customer_id):
    return (await db.execute(select(func.count()).select_from(SalesOnboarding).where(SalesOnboarding.customer_id == customer_id))).scalar_one()


# =============================================================================
class TestTheStatusRead:
    def test_before_and_after(self, p, mgr, lead):
        before = api("GET", f"/api/sales/leads/{lead['id']}/conversion", mgr).json()
        assert before == {"lead_stage": "won", "archived": False, "converted": False, "customer": None, "can_convert": False,
                          "blocker": "conversion_retired", "can_setup": True, "setup_blocker": None}
        c = convert(mgr, lead)["customer"]                                       # legacy data: a lead an old conversion already handled
        after = api("GET", f"/api/sales/leads/{lead['id']}/conversion", mgr).json()
        assert after["converted"] is True and after["customer"]["id"] == c["id"]
        assert after["can_convert"] is False and after["blocker"] is None
        assert after["can_setup"] is False and after["setup_blocker"] is None

    def test_conversion_is_never_offered_to_anybody(self, p, mgr, admin_h, lead):
        for headers in (mgr, admin_h, p["employee"]["headers"]):
            s = api("GET", f"/api/sales/leads/{lead['id']}/conversion", headers).json()
            assert s["can_convert"] is False and s["blocker"] == "conversion_retired"

    def test_why_the_setup_cannot_start(self, p, mgr):
        lead = create_lead(mgr)
        s = api("GET", f"/api/sales/leads/{lead['id']}/conversion", mgr).json()
        assert (s["can_convert"], s["blocker"], s["can_setup"], s["setup_blocker"]) == (False, "conversion_retired", False, "lead_unassigned")
        assign(mgr, lead["id"], p["employee"]["id"])
        s = api("GET", f"/api/sales/leads/{lead['id']}/conversion", mgr).json()
        assert (s["lead_stage"], s["can_convert"], s["can_setup"], s["setup_blocker"]) == ("assigned", False, True, None)
        won = won_lead(mgr, p["employee"]["id"])
        api("DELETE", f"/api/sales/leads/{won['id']}", mgr)
        s = api("GET", f"/api/sales/leads/{won['id']}/conversion", mgr).json()
        assert (s["archived"], s["can_convert"], s["can_setup"], s["setup_blocker"]) == (True, False, False, "lead_archived")

    def test_a_caller_who_may_only_view_customers_cannot_set_up(self, p, mgr, lead, admin_h):
        # the lead's owner, a Sales Employee, may complete the Customer Setup...
        s = api("GET", f"/api/sales/leads/{lead['id']}/conversion", p["employee"]["headers"]).json()
        assert s["lead_stage"] == "won" and s["converted"] is False and s["can_convert"] is False and s["can_setup"] is True
        # ...while a caller who may only VIEW customers cannot
        role = make_custom_role(["sales.access", "sales.customers.view", "sales.leads.view", "sales.leads.scope_all"])
        try:
            viewer = create_staff(admin_h, [role], "conv-viewer")
            s = api("GET", f"/api/sales/leads/{lead['id']}/conversion", viewer["headers"]).json()
            assert s["converted"] is False and s["can_convert"] is False and s["can_setup"] is False
        finally:
            retire_roles([role])

    def test_unknown_and_malformed_ids_are_404(self, mgr):
        for lead_id in (PHANTOM_ID, "not-a-uuid"):
            assert api("GET", f"/api/sales/leads/{lead_id}/conversion", mgr).status_code == 404


# =============================================================================
class TestSourceGuarantees:
    """Whatever is refactored later: the old flow's unlimited subscription can not come back unnoticed."""

    @staticmethod
    def _sales_sources():
        return {path: path.read_text() for path in SALES.rglob("*.py")}

    def test_the_customer_setup_is_the_only_caller_of_the_companies_service_in_sales(self):
        callers = sorted(str(path.relative_to(BACKEND)) for path, source in self._sales_sources().items() if re.search(r"\bcreate_company\(", source))
        assert callers == ["sales/services/customer_setup.py"]

    def test_the_setup_activates_a_dated_period_right_after_it_creates_the_company(self):
        source = (SALES / "services" / "customer_setup.py").read_text()
        created, activated = source.index("admin_service.create_company("), source.index("admin_service.activate_subscription(")
        assert created < activated
        between = source[activated:activated + 400]
        assert "subscription_start_date" in between and "subscription_end_date" in between   # a start AND an end, never neither
        assert source.count("admin_service.create_company(") == 1 and source.count("admin_service.activate_subscription(") == 1

    def test_no_other_sales_module_writes_a_companys_subscription(self):
        """Reads (the customer views) are everywhere; writes - the platform's Activate, an assignment to a subscription column,
        an UPDATE / INSERT of companies, a Company() - only in the Customer Setup."""
        writes = re.compile(r"activate_subscription\(|\.subscription_(?:status|start_date|end_date|ends_exactly)\s*=(?!=)|\b(?:update|insert)\(\s*Company\b|\bCompany\(")
        for path, source in self._sales_sources().items():
            if path.name != "customer_setup.py":
                assert not writes.search(source), path.name

    def test_the_retired_service_module_can_no_longer_write_anything(self):
        source = (SALES / "services" / "conversion.py").read_text()
        tree = ast.parse(source)
        forbidden_calls = {"create_company", "activate_subscription", "add", "add_all", "flush", "commit", "begin_nested", "execute", "record", "hash_password"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                assert name not in forbidden_calls, f"conversion.py calls {name}()"
        assert "get_secret_value" not in source                                   # it never even sees a password
        for retired in ("def convert_lead", "def preflight", "SalesCustomer(", "SalesOnboarding(", "activity_service", "audit_service", "subscription_status"):
            assert retired not in source, retired
        imports = "\n".join(re.findall(r"^(?:from|import) .*$", source, re.M))
        assert "services.admin import" in imports and "parse_uuid" in imports and "create_company" not in imports

    def test_the_two_routes_only_raise_the_retired_answer(self):
        tree = ast.parse((SALES / "router.py").read_text())
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name in ("convert_lead", "preflight_lead_conversion"):
                found[node.name] = node
        assert set(found) == {"convert_lead", "preflight_lead_conversion"}
        for name, node in found.items():
            body = [n for n in node.body if not (isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant))]   # minus the docstring
            assert len(body) == 1 and isinstance(body[0], ast.Raise), name
            assert ast.unparse(body[0].exc) == "conversion_service.conversion_retired()", name
            assert [a.arg for a in node.args.args] == ["lead_id", "ctx"], name        # no body model, no db session

    def test_nothing_imports_the_retired_request_models_into_a_route(self):
        router = (SALES / "router.py").read_text()
        for name in ("ConvertLead", "ConvertResult", "ConversionPreflightOut"):
            assert name not in router                                                # kept in customer_schemas.py for the record only

    def test_the_retirement_left_the_historical_records_and_reads_in_place(self):
        from server import app
        from sales.models import SalesCustomer as _Customer, SalesOnboarding as _Onboarding

        assert _Customer.__tablename__ == "sales_customers" and _Onboarding.__tablename__ == "sales_onboarding"
        paths = {getattr(route, "path", "") for route in app.routes}
        for kept in ("/api/sales/customers", "/api/sales/customers/{customer_id}", "/api/sales/leads/{lead_id}/conversion",
                     "/api/sales/leads/{lead_id}/setup", "/api/sales/leads/{lead_id}/setup/preflight"):
            assert kept in paths, kept
