"""JAZ Sales - Phase 2: duplicate detection (HTTP integration tier).

Before a lead is created - and when its identity fields change - the server looks for the same phone, WhatsApp, email,
website or business name + city among other leads, and for the same company name / owner email / owner phone among
existing JAZ companies. Nothing is merged, deleted or blocked outright: duplicates are REPORTED (409), and the caller
explicitly continues. `exact` and `possible` are different grades, and only an authorized user may continue past an
exact one. Every value below is unique per test, so leftovers from earlier runs never collide by accident.
"""
import uuid
from datetime import datetime, timezone

import pytest

from sales_lead_test_utils import (
    PHANTOM_ID,
    activities,
    assign,
    create_lead,
    demo_company,
    errors_of,
    get_lead,
    lead_body,
    make_personas,
    rand_digits,
    run_db,
    uniq,
)
from sales_test_utils import admin_token, api, assert_scratch_target, auth  # noqa: F401  (admin_token: mints instead of logging in)


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def p(admin_h):
    return make_personas(admin_h, "dup")


@pytest.fixture(scope="module")
def mgr(p):
    return p["manager"]["headers"]


def check(h, **fields):
    r = api("POST", "/api/sales/leads/duplicate-check", h, fields)
    assert r.status_code == 200, r.text
    return r.json()


def try_create(h, confirm=False, expect=None, **overrides):
    body = lead_body(**overrides)
    if confirm:
        body["confirm_duplicates"] = True
    r = api("POST", "/api/sales/leads", h, body)
    if expect is not None:
        assert r.status_code == expect, f"expected {expect}, got {r.status_code} {r.text[:300]}"
    return r


def patch(h, lead_id, confirm=False, **fields):
    body = {**fields, **({"confirm_duplicates": True} if confirm else {})}
    return api("PATCH", f"/api/sales/leads/{lead_id}", h, body)


def found(report, lead_id):
    """The report entry for one specific existing lead."""
    entries = [e for e in report["leads"] if e["id"] == lead_id]
    assert len(entries) == 1, f"expected exactly one entry for {lead_id}, got {entries}"
    return entries[0]


def company_entry(report, company_id):
    entries = [c for c in report["companies"] if c["id"] == company_id]
    assert len(entries) == 1, f"expected exactly one entry for company {company_id}, got {report['companies']}"
    return entries[0]


def kinds(entry):
    return sorted((m["field"], m["kind"], m["matched_on"]) for m in entry["matches"])


def local_number():
    """A 10-digit national-style number, unique enough for a test (no leading zero: the tail logic uses 9 digits)."""
    return "7" + rand_digits(9)


# =============================================================================
class TestPhone:
    SPELLINGS = [
        lambda t: f"+964 {t[:3]} {t[3:6]} {t[6:]}",
        lambda t: f"00964{t}",
        lambda t: f"+964-{t[:3]}-{t[3:]}",
        lambda t: f"(964) {t[:3]}.{t[3:6]}.{t[6:]}",
        lambda t: "".join("٠١٢٣٤٥٦٧٨٩"[int(c)] for c in f"964{t}"),          # Arabic-Indic digits
        lambda t: f"964{t}",
    ]

    @pytest.mark.parametrize("spelling", range(len(SPELLINGS)))
    def test_the_same_number_written_any_way_is_an_exact_duplicate(self, mgr, spelling):
        t = local_number()
        existing = create_lead(mgr, phone=f"+964 {t[:3]} {t[3:6]} {t[6:]}")
        candidate = self.SPELLINGS[spelling](t)
        report = check(mgr, phone=candidate)
        assert report["has_exact"] and found(report, existing["id"])["severity"] == "exact"
        assert kinds(found(report, existing["id"])) == [("phone", "exact", "phone")]
        assert try_create(mgr, phone=candidate).status_code == 409

    def test_a_national_number_matches_its_international_form_only_as_possible(self, mgr):
        t = local_number()
        existing = create_lead(mgr, phone=f"+964 {t[:3]} {t[3:6]} {t[6:]}")
        report = check(mgr, phone=f"0{t[:3]} {t[3:6]} {t[6:]}")
        entry = found(report, existing["id"])
        assert entry["severity"] == "possible" and kinds(entry) == [("phone", "possible", "phone")]
        assert report["has_possible"] and not report["has_exact"]

    def test_a_different_operator_prefix_is_not_a_match(self, mgr):
        rest = rand_digits(7)
        existing = create_lead(mgr, phone=f"0770{rest}")
        assert all(e["id"] != existing["id"] for e in check(mgr, phone=f"0750{rest}")["leads"])   # same last 7 digits, other operator
        assert found(check(mgr, phone=f"0770{rest}"), existing["id"])["severity"] == "exact"

    def test_numbers_too_short_to_identify_anyone_never_match(self, mgr):
        create_lead(mgr, phone="12345")
        assert check(mgr, phone="12345")["leads"] == []
        try_create(mgr, phone="12345", expect=201)

    def test_short_numbers_only_match_when_identical(self, mgr):
        digits = "5" + rand_digits(6)
        existing = create_lead(mgr, phone=digits)
        assert kinds(found(check(mgr, phone=digits), existing["id"])) == [("phone", "exact", "phone")]
        assert all(e["id"] != existing["id"] for e in check(mgr, phone="0" + digits)["leads"])   # no tail logic below 9 digits

    def test_a_phone_matches_a_whatsapp_number_and_the_other_way_round(self, mgr):
        t = local_number()
        with_whatsapp = create_lead(mgr, whatsapp=f"+964 {t[:3]} {t[3:6]} {t[6:]}", phone=None)
        assert kinds(found(check(mgr, phone=f"00964{t}"), with_whatsapp["id"])) == [("phone", "exact", "whatsapp")]
        u = local_number()
        with_phone = create_lead(mgr, phone=f"+964{u}", whatsapp=None)
        assert kinds(found(check(mgr, whatsapp=f"+964 {u[:3]} {u[3:]}"), with_phone["id"])) == [("whatsapp", "exact", "phone")]

    def test_whatsapp_against_whatsapp(self, mgr):
        t = local_number()
        existing = create_lead(mgr, whatsapp=f"+964{t}", phone=None)
        assert kinds(found(check(mgr, whatsapp=f"00964 {t}"), existing["id"])) == [("whatsapp", "exact", "whatsapp")]


class TestEmailAndWebsite:
    def test_email_is_exact_and_case_insensitive(self, mgr):
        u = uniq()
        existing = create_lead(mgr, email=f"Info@Shop-{u}.com")
        assert kinds(found(check(mgr, email=f"  INFO@shop-{u}.COM "), existing["id"])) == [("email", "exact", "email")]
        assert try_create(mgr, email=f"info@shop-{u}.com").status_code == 409

    def test_a_different_email_is_not_a_match(self, mgr):
        u = uniq()
        create_lead(mgr, email=f"info@shop-{u}.com")
        assert check(mgr, email=f"sales@shop-{u}.com")["leads"] == []

    @pytest.mark.parametrize("candidate_template", [
        "http://{u}.EXAMPLE.org", "https://www.{u}.example.org/", "{u}.example.org", "www.{u}.example.org/?utm=1#top", "HTTPS://{u}.example.org///",
    ])
    def test_a_website_is_the_same_website_however_it_is_typed(self, mgr, candidate_template):
        u = uniq()
        existing = create_lead(mgr, website=f"https://www.{u}.example.org/")
        assert kinds(found(check(mgr, website=candidate_template.format(u=u)), existing["id"])) == [("website", "exact", "website")]

    def test_a_different_page_of_the_same_site_is_a_different_business(self, mgr):
        u = uniq()
        existing = create_lead(mgr, website=f"https://facebook.com/shop-{u}-a")
        assert all(e["id"] != existing["id"] for e in check(mgr, website=f"https://www.facebook.com/shop-{u}-b")["leads"])
        assert kinds(found(check(mgr, website=f"http://www.facebook.com/shop-{u}-a/"), existing["id"])) == [("website", "exact", "website")]


class TestBusinessNameAndCity:
    def test_the_same_name_in_the_same_city_is_a_possible_duplicate(self, mgr):
        u = uniq()
        existing = create_lead(mgr, business_name=f"Al-Amal  Pharmacy {u}", city="Baghdad")
        report = check(mgr, business_name=f"AL AMAL PHARMACY {u}!", city=" baghdad ")
        entry = found(report, existing["id"])
        assert entry["severity"] == "possible" and kinds(entry) == [("business_name", "possible", "business_name_city")]
        assert not report["has_exact"]

    def test_arabic_spelling_variants_match(self, mgr):
        u = uniq()
        existing = create_lead(mgr, business_name=f"صيدلية الأمل {u}", city="بغداد")
        report = check(mgr, business_name=f"صيدليه الامل {u}", city="بغداد")           # ة->ه, أ->ا
        assert kinds(found(report, existing["id"])) == [("business_name", "possible", "business_name_city")]

    def test_name_alone_or_a_different_city_is_not_a_match(self, mgr):
        u = uniq()
        existing = create_lead(mgr, business_name=f"Unique Pharmacy {u}", city="Baghdad")
        for fields in ({"business_name": f"Unique Pharmacy {u}"}, {"business_name": f"Unique Pharmacy {u}", "city": "Basra"}, {"city": "Baghdad"}):
            assert all(e["id"] != existing["id"] for e in check(mgr, **fields)["leads"]), fields

    def test_a_similar_but_different_name_is_not_a_match(self, mgr):
        u = uniq()
        existing = create_lead(mgr, business_name=f"Amal Pharmacy {u}", city="Baghdad")
        assert all(e["id"] != existing["id"] for e in check(mgr, business_name=f"Amal Pharmacies {u}", city="Baghdad")["leads"])


class TestWhichLeadsAreConsidered:
    def test_archived_and_closed_leads_are_still_reported_so_they_can_be_restored_instead(self, mgr):
        t = local_number()
        archived = create_lead(mgr, phone=f"+964{t}")
        api("DELETE", f"/api/sales/leads/{archived['id']}", mgr)
        entry = found(check(mgr, phone=f"00964{t}"), archived["id"])
        assert entry["archived"] is True and entry["severity"] == "exact"

        u = local_number()
        lost = create_lead(mgr, phone=f"+964{u}")
        api("POST", f"/api/sales/leads/{lost['id']}/stage", mgr, {"stage": "lost", "lost_reason": "not_interested"})
        assert found(check(mgr, phone=f"+964{u}"), lost["id"])["pipeline_stage"] == "lost"

    def test_everything_that_matches_is_reported_exact_first(self, mgr):
        t = local_number()
        possible_only = create_lead(mgr, phone=f"0{t}")                                 # national form of the same number
        exact = create_lead(mgr, phone=f"+964{t}", email=None, confirm_duplicates=True)  # itself a possible duplicate of the first
        report = check(mgr, phone=f"+964{t}")
        ids = [e["id"] for e in report["leads"] if e["id"] in (possible_only["id"], exact["id"])]
        assert ids == [exact["id"], possible_only["id"]]
        assert [found(report, i)["severity"] for i in ids] == ["exact", "possible"]


# =============================================================================
class TestCreateFlow:
    def test_no_duplicates_no_friction(self, mgr):
        try_create(mgr, expect=201)
        try_create(mgr, confirm=True, expect=201)         # confirming when there is nothing to confirm is harmless

    def test_a_409_carries_the_full_report_and_creates_nothing(self, mgr):
        t = local_number()
        existing = create_lead(mgr, phone=f"+964{t}")
        body = lead_body(phone=f"00964 {t}")
        r = api("POST", "/api/sales/leads", mgr, body)
        assert r.status_code == 409
        detail = errors_of(r)
        assert detail["field"] == "duplicates" and detail["code"] == "duplicates_found"
        report = detail["duplicates"]
        assert report["has_exact"] is True and report["can_override"] is True
        assert found(report, existing["id"])["severity"] == "exact"
        assert api("GET", "/api/sales/leads", mgr, None, params={"q": body["business_name"]}).json()["total"] == 0   # nothing was created

    def test_an_authorized_user_can_continue_and_the_override_is_recorded(self, mgr):
        t = local_number()
        existing = create_lead(mgr, phone=f"+964{t}")
        before = get_lead(mgr, existing["id"])
        r = try_create(mgr, confirm=True, phone=f"964{t}")
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["id"] != existing["id"]
        assert get_lead(mgr, existing["id"]) == before                                  # never merged, never modified
        both = api("GET", "/api/sales/leads", mgr, None, params={"q": t}).json()
        assert {i["id"] for i in both["items"]} >= {existing["id"], created["id"]}     # two separate records
        event = activities(mgr, created["id"])[0]
        assert event["event_type"] == "lead_created"
        assert event["metadata"]["duplicate_override"] == {"exact": 1, "possible": 0, "lead_ids": [existing["id"]], "company_ids": []}

    def test_only_an_authorized_user_may_continue_past_an_exact_duplicate(self, p, mgr):
        t = local_number()
        create_lead(mgr, phone=f"+964{t}")
        for who in ("data_entry", "data_entry2"):
            r = try_create(p[who]["headers"], confirm=True, phone=f"+964{t}")
            assert r.status_code == 403, who
            detail = errors_of(r)
            assert detail["code"] == "duplicate_override_forbidden" and detail["duplicates"]["can_override"] is False
        # the clerk is told up front that they cannot continue
        first = try_create(p["data_entry"]["headers"], phone=f"+964{t}")
        assert first.status_code == 409 and errors_of(first)["duplicates"]["can_override"] is False
        assert api("GET", "/api/sales/leads", mgr, None, params={"q": t}).json()["total"] == 1     # still only the original

    def test_a_data_entry_clerk_may_continue_past_a_possible_duplicate(self, p, mgr):
        t = local_number()
        create_lead(mgr, phone=f"+964{t}")
        national = f"0{t}"
        first = try_create(p["data_entry"]["headers"], phone=national)
        assert first.status_code == 409 and errors_of(first)["duplicates"]["can_override"] is True
        try_create(p["data_entry"]["headers"], confirm=True, phone=national, expect=201)

    def test_name_and_city_is_only_ever_possible_so_anyone_may_continue(self, p, mgr):
        u = uniq()
        create_lead(mgr, business_name=f"Twin Store {u}", city="Erbil")
        h = p["data_entry"]["headers"]
        assert try_create(h, business_name=f"Twin Store {u}", city="Erbil").status_code == 409
        try_create(h, confirm=True, business_name=f"Twin Store {u}", city="Erbil", expect=201)

    def test_a_mixed_report_is_exact_when_any_match_is_exact(self, p, mgr):
        u, t = uniq(), local_number()
        create_lead(mgr, business_name=f"Mixed Store {u}", city="Mosul")                         # possible (name + city)
        create_lead(mgr, phone=f"+964{t}", city="Duhok")                                          # exact (phone)
        overlapping = dict(business_name=f"Mixed Store {u}", city="Mosul", phone=f"+964{t}")
        assert try_create(p["data_entry"]["headers"], confirm=True, **overlapping).status_code == 403   # one exact match needs a manager
        try_create(mgr, confirm=True, expect=201, **overlapping)

    def test_the_override_flag_is_not_a_field_on_the_lead(self, mgr):
        assert "confirm_duplicates" not in try_create(mgr, confirm=True, expect=201).json()

    def test_creating_with_an_owner_still_checks_duplicates(self, p, mgr):
        t = local_number()
        create_lead(mgr, phone=f"+964{t}")
        assert try_create(mgr, phone=f"+964{t}", assigned_to=p["employee"]["id"]).status_code == 409


# =============================================================================
class TestWhatACallerLearnsAboutLeadsOutsideTheirScope:
    def test_out_of_scope_matches_are_reported_without_identity(self, p, mgr):
        t = local_number()
        owned = create_lead(mgr, phone=f"+964{t}", business_name=f"Owned Lead {uniq()}")
        assign(mgr, owned["id"], p["employee"]["id"])

        for who in ("data_entry", "employee2"):                                 # neither may see this lead
            entries = [e for e in check(p[who]["headers"], phone=f"+964{t}")["leads"] if e["business_name"] == owned["business_name"]]
            assert len(entries) == 1
            entry = entries[0]
            assert entry["in_scope"] is False and entry["id"] is None and entry["assigned_to"] is None
            assert entry["assigned"] is True and entry["severity"] == "exact" and entry["pipeline_stage"] == "assigned"
            for leaked in ("phone", "email", "notes", "contact_name"):
                assert leaked not in entry

        seen_by_manager = found(check(mgr, phone=f"+964{t}"), owned["id"])
        assert seen_by_manager["in_scope"] is True and seen_by_manager["assigned_to"]["id"] == p["employee"]["id"]
        assert found(check(p["employee"]["headers"], phone=f"+964{t}"), owned["id"])["in_scope"] is True   # the owner sees their own

    def test_a_clerk_still_sees_unassigned_leads_in_full(self, p, mgr):
        t = local_number()
        lead = create_lead(mgr, phone=f"+964{t}")
        assert found(check(p["data_entry"]["headers"], phone=f"+964{t}"), lead["id"])["in_scope"] is True


# =============================================================================
def _make_company(name, email, phone, *, deleted=False, owner_deleted=False):
    """A company + owner written straight into the scratch DB (there is no Sales API for customers)."""
    from models import Company, User

    async def _go(db):
        owner = User(id=uuid.uuid4(), email=email, phone=phone, password="not-a-real-hash", name="Owner", role="company_owner",
                     deleted_at=datetime.now(timezone.utc) if owner_deleted else None)
        db.add(owner)
        await db.flush()
        company = Company(id=uuid.uuid4(), name=name, owner_id=owner.id, qr_code="x",
                          deleted_at=datetime.now(timezone.utc) if deleted else None)
        db.add(company)
        await db.flush()
        return str(company.id)

    return run_db(_go)


class TestJazCompanies:
    def test_an_owners_email_is_an_exact_collision(self, mgr):
        company = demo_company()
        for variant in (company["owner_email"], company["owner_email"].upper(), f"  {company['owner_email']} "):
            report = check(mgr, email=variant)
            entry = company_entry(report, company["id"])
            assert report["has_exact"] and entry["name"] == company["name"] and entry["severity"] == "exact"
            assert kinds(entry) == [("email", "exact", "owner_email")]

    def test_an_owners_phone_is_exact_and_its_international_form_is_possible(self, mgr):
        company = demo_company()
        digits = "".join(c for c in company["owner_phone"] if c.isdigit())
        exact = company_entry(check(mgr, phone=f"{digits[:3]} {digits[3:6]} {digits[6:]}"), company["id"])
        assert exact["severity"] == "exact" and kinds(exact) == [("phone", "exact", "owner_phone")]
        possible = company_entry(check(mgr, phone=f"+971 {digits.lstrip('0')}"), company["id"])
        assert possible["severity"] == "possible" and kinds(possible) == [("phone", "possible", "owner_phone")]

    def test_a_whatsapp_number_is_compared_with_the_owners_phone_too(self, mgr):
        company = demo_company()
        assert kinds(company_entry(check(mgr, whatsapp=company["owner_phone"]), company["id"])) == [("whatsapp", "exact", "owner_phone")]

    def test_a_company_name_is_a_possible_collision_whatever_its_spelling(self, mgr):
        company = demo_company()
        for variant in (company["name"], company["name"].replace("ة", "ه"), f"  {company['name']}!! "):
            report = check(mgr, business_name=variant)                                  # no city needed against companies
            entry = company_entry(report, company["id"])
            assert entry["severity"] == "possible" and kinds(entry) == [("business_name", "possible", "company_name")]
        assert not check(mgr, business_name=company["name"])["has_exact"]

    def test_companies_only_expose_their_id_and_name(self, mgr):
        company = demo_company()
        assert set(company_entry(check(mgr, email=company["owner_email"]), company["id"])) == {"id", "name", "severity", "matches"}

    def test_creating_a_lead_for_an_existing_customer_needs_a_manager(self, p, mgr):
        company = demo_company()
        body = dict(business_name=f"Fresh Name {uniq()}", email=company["owner_email"])
        r = api("POST", "/api/sales/leads", p["data_entry"]["headers"], lead_body(**body))
        assert r.status_code == 409 and errors_of(r)["duplicates"]["can_override"] is False
        assert try_create(p["data_entry"]["headers"], confirm=True, **body).status_code == 403
        made = try_create(mgr, confirm=True, expect=201, **body).json()
        override = activities(mgr, made["id"])[0]["metadata"]["duplicate_override"]
        assert company["id"] in override["company_ids"] and override["exact"] >= 1

    def test_a_name_only_collision_with_a_company_can_be_confirmed_by_anyone(self, p):
        company = demo_company()
        h = p["data_entry"]["headers"]
        assert try_create(h, business_name=company["name"]).status_code == 409
        try_create(h, confirm=True, business_name=company["name"], expect=201)

    def test_deleted_companies_and_deleted_owners_are_ignored(self, mgr):
        u = uniq()
        live_email, dead_email, orphan_email = f"live-{u}@co.example.com", f"dead-{u}@co.example.com", f"orphan-{u}@co.example.com"
        live = _make_company(f"Live Co {u}", live_email, f"+1888{rand_digits(7)}")
        _make_company(f"Dead Co {u}", dead_email, f"+1888{rand_digits(7)}", deleted=True)
        _make_company(f"Orphan Co {u}", orphan_email, f"+1888{rand_digits(7)}", owner_deleted=True)
        assert [c["id"] for c in check(mgr, email=live_email)["companies"]] == [live]
        assert check(mgr, email=dead_email)["companies"] == []
        assert check(mgr, email=orphan_email)["companies"] == []
        assert check(mgr, business_name=f"Dead Co {u}")["companies"] == []

    def test_only_owners_count_not_every_user_of_a_company(self, mgr):
        from sqlalchemy import select
        from models import User

        async def _employee_email(db):
            return (await db.execute(select(User.email).where(User.email == "employee1@demo.com"))).scalar_one()

        # an employee's email belongs to a person, not to a customer record
        assert check(mgr, email=run_db(_employee_email))["companies"] == []

    def test_a_lead_and_a_company_can_collide_at_once(self, mgr):
        company = demo_company()
        existing = try_create(mgr, confirm=True, expect=201, email=company["owner_email"], business_name=f"Both {uniq()}").json()
        report = check(mgr, email=company["owner_email"])
        assert found(report, existing["id"])["severity"] == "exact"
        assert company_entry(report, company["id"])["severity"] == "exact"


# =============================================================================
class TestUpdateFlow:
    def _pair(self, mgr, **kw):
        """Two leads with nothing in common; returns (other, mine)."""
        return create_lead(mgr, **kw), create_lead(mgr)

    def test_changing_a_phone_to_someone_elses_number_is_refused_until_confirmed(self, mgr):
        t = local_number()
        other, mine = self._pair(mgr, phone=f"+964{t}")
        before = get_lead(mgr, mine["id"])
        r = patch(mgr, mine["id"], phone=f"00964 {t}")
        assert r.status_code == 409 and errors_of(r)["code"] == "duplicates_found"
        assert found(errors_of(r)["duplicates"], other["id"])["severity"] == "exact"
        assert get_lead(mgr, mine["id"]) == before                                       # refused: nothing changed, nothing recorded
        assert len(activities(mgr, mine["id"])) == 1

        r = patch(mgr, mine["id"], confirm=True, phone=f"00964 {t}")
        assert r.status_code == 200 and r.json()["phone"] == f"00964 {t}"
        event = activities(mgr, mine["id"])[-1]
        assert event["event_type"] == "lead_updated" and event["metadata"]["duplicate_override"]["lead_ids"] == [other["id"]]

    def test_only_an_authorized_user_may_confirm_an_exact_collision_when_editing(self, p, mgr):
        t = local_number()
        create_lead(mgr, phone=f"+964{t}")
        mine = create_lead(p["data_entry"]["headers"])
        r = patch(p["data_entry"]["headers"], mine["id"], confirm=True, phone=f"+964{t}")
        assert r.status_code == 403 and errors_of(r)["code"] == "duplicate_override_forbidden"
        assert get_lead(mgr, mine["id"])["phone"] == mine["phone"]

    def test_an_employee_editing_their_own_lead_is_held_to_the_same_rules(self, p, mgr):
        t = local_number()
        create_lead(mgr, phone=f"+964{t}")
        mine = create_lead(mgr)
        assign(mgr, mine["id"], p["employee"]["id"])
        h = p["employee"]["headers"]
        assert patch(h, mine["id"], phone=f"+964{t}").status_code == 409
        assert patch(h, mine["id"], confirm=True, phone=f"+964{t}").status_code == 403           # exact: needs a manager
        assert patch(h, mine["id"], confirm=True, phone=f"0{t}").status_code == 200               # possible: may continue

    @pytest.mark.parametrize("field,value_of", [
        ("email", lambda e: e["email"].upper()),
        ("website", lambda e: e["website"] + "/"),
    ])
    def test_email_and_website_collisions_are_caught_on_update(self, mgr, field, value_of):
        other, mine = self._pair(mgr, website=f"https://www.site-{uniq()}.example.org/")
        r = patch(mgr, mine["id"], **{field: value_of(other)})
        assert r.status_code == 409 and found(errors_of(r)["duplicates"], other["id"])["severity"] == "exact"

    def test_a_whatsapp_change_is_checked_against_phones_too(self, mgr):
        t = local_number()
        other, mine = self._pair(mgr, phone=f"+964{t}")
        r = patch(mgr, mine["id"], whatsapp=f"00964{t}")
        assert r.status_code == 409 and kinds(found(errors_of(r)["duplicates"], other["id"])) == [("whatsapp", "exact", "phone")]

    def test_changing_the_name_or_city_into_an_existing_pair_is_a_possible_duplicate(self, p, mgr):
        u = uniq()
        other = create_lead(mgr, business_name=f"Pair Store {u}", city="Kirkuk")
        h = p["data_entry"]["headers"]
        mine = create_lead(h, business_name=f"Different {u}", city="Kirkuk")
        r = patch(h, mine["id"], business_name=f"pair store {u}")
        assert r.status_code == 409 and found(errors_of(r)["duplicates"], other["id"])["severity"] == "possible"
        assert patch(h, mine["id"], confirm=True, business_name=f"pair store {u}").status_code == 200   # a clerk may continue past a possible one

        moving = create_lead(h, business_name=f"Pair Store {u}", city="Najaf")                          # a different city: no pair yet
        assert patch(h, moving["id"], city="Kirkuk").status_code == 409                                  # the city alone completes the pair

    def test_unrelated_edits_never_re_raise_an_accepted_duplicate(self, mgr):
        t = local_number()
        create_lead(mgr, phone=f"+964{t}")
        twin = try_create(mgr, confirm=True, expect=201, phone=f"+964{t}").json()             # knowingly created as a duplicate
        for change in ({"notes": "call back Monday"}, {"priority": "high"}, {"contact_name": "Someone"}, {"estimated_value": 100}):
            assert patch(mgr, twin["id"], **change).status_code == 200, change

    def test_reformatting_your_own_number_is_not_a_new_collision(self, mgr):
        t = local_number()
        create_lead(mgr, phone=f"+964{t}")
        twin = try_create(mgr, confirm=True, expect=201, phone=f"+964{t}").json()
        assert patch(mgr, twin["id"], phone=f"00964 {t[:3]} {t[3:]}").status_code == 200     # same normalized value: nothing new to warn about

    def test_a_lead_never_collides_with_itself(self, mgr):
        t = local_number()
        mine = create_lead(mgr, phone=f"+964{t}")
        assert patch(mgr, mine["id"], phone=f"00964{t}", priority="high").status_code == 200
        assert [e["id"] for e in check(mgr, phone=f"+964{t}")["leads"]] == [mine["id"]]            # a plain check does see it...
        assert check(mgr, phone=f"+964{t}", exclude_lead_id=mine["id"])["leads"] == []             # ...unless the editor excludes it

    def test_clearing_an_identity_field_needs_no_check(self, mgr):
        t = local_number()
        create_lead(mgr, phone=f"+964{t}")
        twin = try_create(mgr, confirm=True, expect=201, phone=f"+964{t}").json()
        r = patch(mgr, twin["id"], phone=None)
        assert r.status_code == 200 and r.json()["phone"] is None

    def test_updating_to_an_owners_email_hits_the_company_check(self, p):
        company = demo_company()
        h = p["data_entry"]["headers"]
        mine = create_lead(h)
        r = patch(h, mine["id"], email=company["owner_email"])
        assert r.status_code == 409 and company_entry(errors_of(r)["duplicates"], company["id"])["severity"] == "exact"
        assert patch(h, mine["id"], confirm=True, email=company["owner_email"]).status_code == 403


# =============================================================================
class TestDuplicateCheckEndpoint:
    def test_it_is_read_only(self, mgr):
        name = f"Preview Only {uniq()}"
        report = check(mgr, business_name=name, city="Baghdad", phone=f"+964{local_number()}")
        assert report["leads"] == [] and report["companies"] == []
        assert api("GET", "/api/sales/leads", mgr, None, params={"q": name}).json()["total"] == 0      # checking created nothing

    def test_nothing_to_compare_means_nothing_found(self, mgr):
        assert check(mgr) == {"has_exact": False, "has_possible": False, "can_override": True, "leads": [], "companies": []}
        assert check(mgr, phone="  ", email="", website=" ")["leads"] == []

    @pytest.mark.parametrize("who", ["manager", "employee", "data_entry"])
    def test_available_to_every_role_that_works_leads(self, p, who):
        assert api("POST", "/api/sales/leads/duplicate-check", p[who]["headers"], {"phone": "+15550009999"}).status_code == 200

    def test_can_override_reflects_the_callers_own_permissions(self, p, mgr):
        t = local_number()
        create_lead(mgr, phone=f"+964{t}")
        assert check(mgr, phone=f"+964{t}")["can_override"] is True
        assert check(p["data_entry"]["headers"], phone=f"+964{t}")["can_override"] is False
        assert check(p["data_entry"]["headers"], phone=f"0{t}")["can_override"] is True            # possible only

    @pytest.mark.parametrize("payload", [
        {"exclude_lead_id": "not-a-uuid"}, {"business_name": "x" * 201}, {"phone": "1" * 51}, {"email": "e" * 255},
        {"notes": "unknown field"}, {"confirm_duplicates": True},
    ])
    def test_bad_payloads_are_422(self, mgr, payload):
        assert api("POST", "/api/sales/leads/duplicate-check", mgr, payload).status_code == 422

    def test_a_half_typed_email_is_fine(self, mgr):
        assert check(mgr, email="half-typed@")["leads"] == []

    def test_an_unknown_excluded_lead_is_harmless(self, mgr):
        t = local_number()
        existing = create_lead(mgr, phone=f"+964{t}")
        assert [e["id"] for e in check(mgr, phone=f"+964{t}", exclude_lead_id=PHANTOM_ID)["leads"]] == [existing["id"]]
