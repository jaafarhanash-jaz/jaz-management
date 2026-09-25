"""JAZ Sales - Phase 2: Lead CRUD, listing, search, filters, sorting, pagination, archive (HTTP integration tier).

Runs against a live backend on the scratch database only (see sales_test_utils.assert_scratch_target). Personas are
real staff accounts created through the real API; every lead is created through the API too, with unique identity
values, and each test isolates its rows with its own campaign - the scratch DB accumulates leads across runs.
"""
import random
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest

from sales.timezone import app_today

from sales_lead_test_utils import (
    PHANTOM_ID,
    activities,
    assign,
    create_campaign,
    create_lead,
    errors_of,
    event_types,
    get_lead,
    lead_body,
    make_personas,
    rand_digits,
    run_db,
    set_stage,
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
    return make_personas(admin_h, "crud")


@pytest.fixture(scope="module")
def mgr(p):
    return p["manager"]["headers"]


def _has_key(obj, needles):
    """True if any dict key anywhere in `obj` contains one of the needles."""
    if isinstance(obj, dict):
        return any(any(n in str(k).lower() for n in needles) or _has_key(v, needles) for k, v in obj.items())
    if isinstance(obj, list):
        return any(_has_key(i, needles) for i in obj)
    return False


# =============================================================================
class TestSources:
    def test_lists_the_eleven_standard_sources_in_order(self, p):
        r = api("GET", "/api/sales/sources", p["manager"]["headers"])
        assert r.status_code == 200
        rows = r.json()
        assert [s["key"] for s in rows] == ["google_maps", "website", "facebook", "instagram", "tiktok", "referral",
                                             "cold_call", "inbound", "advertisement", "manual_entry", "import"]
        assert all(s["name_en"] and s["name_ar"] for s in rows)
        assert [s["sort_order"] for s in rows] == sorted(s["sort_order"] for s in rows)

    @pytest.mark.parametrize("persona", ["manager", "employee", "data_entry"])
    def test_readable_by_every_role_that_works_leads(self, p, persona):
        assert api("GET", "/api/sales/sources", p[persona]["headers"]).status_code == 200

    def test_read_only(self, p, admin_h):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            r = api(method, "/api/sales/sources", admin_h, {"key": "x"})
            assert r.status_code in (404, 405), (method, r.status_code)


# =============================================================================
class TestCreate:
    def test_minimal_lead_gets_the_documented_defaults(self, p, mgr):
        lead = create_lead(mgr, business_name=f"Minimal {uniq()}", phone=None, email=None, city=None, country=None,
                           business_type=None, contact_name=None)
        assert lead["source"] == "manual_entry" and lead["priority"] == "medium" and lead["pipeline_stage"] == "new"
        assert lead["assigned_to"] is None and lead["campaign"] is None and lead["lost_reason"] is None
        assert lead["archived_at"] is None and lead["closed_at"] is None and lead["estimated_value"] is None
        assert lead["created_by"]["id"] == p["manager"]["id"]
        assert lead["created_at"] and lead["updated_at"]
        assert lead["allowed_stages"] == ["lost"]
        assert lead["can"] == {"update": True, "change_stage": True, "assign": True, "archive": True, "restore": False}

    def test_full_lead_round_trips_every_field(self, p, mgr):
        campaign = create_campaign(mgr)
        wa = "771" + rand_digits(7)                                            # unique: duplicates are detected across every run
        body = lead_body(
            business_type="Pharmacy", description="Chain of 3 pharmacies", contact_name="Sara Ali", contact_position="Owner",
            whatsapp=f"+964 {wa[:3]} {wa[3:6]} {wa[6:]}", website=f"https://www.{uniq()}.example.net", country="Iraq", city="Baghdad",
            address="Karrada, street 12", latitude=33.3128, longitude=44.3615, source="instagram",
            campaign_id=campaign["id"], priority="high", estimated_value=1500.5, notes="Met at the expo",
        )
        r = api("POST", "/api/sales/leads", mgr, body)
        assert r.status_code == 201, r.text
        lead = r.json()
        for key in ("business_name", "business_type", "description", "contact_name", "contact_position", "phone", "whatsapp",
                    "website", "country", "city", "address", "latitude", "longitude", "source", "priority", "notes"):
            assert lead[key] == body[key], key
        assert lead["email"] == body["email"]
        assert lead["estimated_value"] == 1500.5
        assert lead["campaign_id"] == campaign["id"] and lead["campaign"] == {"id": campaign["id"], "name": campaign["name"], "status": "active"}
        assert get_lead(mgr, lead["id"]) == lead   # what create returned is what a later read returns

    def test_whitespace_is_trimmed_and_blank_means_no_value(self, mgr):
        lead = create_lead(mgr, business_name=f"  Padded {uniq()}  ", phone="   ", email="", contact_name="  ", city=None)
        assert lead["business_name"].startswith("Padded ") and not lead["business_name"].endswith(" ")
        assert lead["phone"] is None and lead["email"] is None and lead["contact_name"] is None

    def test_normalized_columns_are_stored_and_never_returned(self, mgr):
        a, b, u = rand_digits(7), rand_digits(7), uniq()                           # unique numbers: duplicates are detected across every run
        phone, name = f"+964 (770) {a[:3]}-{a[3:]}", f"Al-Amal   Pharmacy {u}"
        whatsapp = "".join("٠١٢٣٤٥٦٧٨٩"[int(c)] for c in f"0750{b}")             # Arabic-Indic digits
        lead = create_lead(mgr, business_name=name, phone=phone, whatsapp=whatsapp, email=f"  {u}@AMAL.COM ".strip(),
                           website=f"HTTPS://www.Amal-{u}.com/Shop/", city="  Baghdad ")
        assert not _has_key(lead, ("_norm",))
        from sqlalchemy import select
        from sales.models import SalesLead

        async def _row(db):
            row = (await db.execute(select(SalesLead).where(SalesLead.id == uuid.UUID(lead["id"])))).scalar_one()
            return row.name_norm, row.city_norm, row.phone_norm, row.whatsapp_norm, row.email_norm, row.website_norm
        name_n, city_n, phone_n, wa_n, email_n, site_n = run_db(_row)
        assert name_n == f"al amal pharmacy {u}" and city_n == "baghdad"
        assert phone_n == f"964770{a}" and wa_n == f"0750{b}"
        assert email_n == lead["email"].lower() and site_n == f"amal-{u}.com/shop"

    @pytest.mark.parametrize("override,status,field", [
        ({"business_name": None}, 422, None),                                # missing
        ({"business_name": "   "}, 422, None),                               # blank
        ({"business_name": "!!! ---"}, 400, "business_name"),                # nothing alphanumeric
        ({"business_name": "x" * 201}, 422, None),
        ({"email": "not-an-email"}, 422, None),
        ({"phone": "0770@123"}, 400, "phone"),                               # the shared Phase-1 phone rule: no '@'
        ({"whatsapp": "0770@123"}, 400, "whatsapp"),
        ({"phone": "1" * 51}, 422, None),
        ({"source": "myspace"}, 400, "source"),
        ({"source": "Facebook"}, 400, "source"),                             # keys are exact
        ({"campaign_id": PHANTOM_ID}, 400, "campaign_id"),
        ({"campaign_id": "not-a-uuid"}, 422, None),
        ({"latitude": 10.0}, 400, "longitude"),                              # both or neither
        ({"longitude": 10.0}, 400, "latitude"),
        ({"latitude": 91, "longitude": 10}, 422, None),
        ({"latitude": 10, "longitude": -181}, 422, None),
        ({"estimated_value": -1}, 422, None),
        ({"estimated_value": 1.005}, 422, None),                             # more than 2 decimals
        ({"estimated_value": 10 ** 13}, 422, None),                          # beyond numeric(14,2)
        ({"priority": "urgent"}, 422, None),
        ({"notes": "n" * 5001}, 422, None),
    ])
    def test_validation(self, mgr, override, status, field):
        r = api("POST", "/api/sales/leads", mgr, lead_body(**override))   # lead_body drops keys set to None
        assert r.status_code == status, r.text
        if field:
            assert errors_of(r)["field"] == field

    @pytest.mark.parametrize("forbidden", [
        {"pipeline_stage": "won"}, {"lost_reason": "other"}, {"assigned_at": "2026-01-01T00:00:00Z"}, {"created_by": PHANTOM_ID},
        {"id": PHANTOM_ID}, {"archived_at": "2026-01-01T00:00:00Z"}, {"closed_at": "2026-01-01T00:00:00Z"},
        {"name_norm": "x"}, {"phone_norm": "123456"}, {"created_at": "2020-01-01T00:00:00Z"}, {"can": {}},
    ])
    def test_clients_cannot_smuggle_server_owned_fields(self, mgr, forbidden):
        r = api("POST", "/api/sales/leads", mgr, {**lead_body(), **forbidden})
        assert r.status_code == 422

    def test_a_rejected_create_writes_nothing(self, mgr):
        name = f"Never Created {uniq()}"
        assert api("POST", "/api/sales/leads", mgr, lead_body(business_name=name, source="nope")).status_code == 400
        assert api("GET", f"/api/sales/leads?q={quote(name)}", mgr).json()["total"] == 0


class TestCreateWithAssignee:
    def test_manager_may_create_a_lead_already_assigned(self, p, mgr):
        lead = create_lead(mgr, assigned_to=p["employee"]["id"])
        assert lead["assigned_to"]["id"] == p["employee"]["id"] and lead["pipeline_stage"] == "assigned"
        assert lead["assigned_at"] is not None
        assert event_types(mgr, lead["id"]) == ["lead_created", "lead_assigned"]

    def test_choosing_the_owner_needs_the_assign_permission(self, p, mgr):
        body = lead_body(assigned_to=p["employee"]["id"])
        assert api("POST", "/api/sales/leads", p["data_entry"]["headers"], body).status_code == 403
        assert api("GET", f"/api/sales/leads?q={quote(body['business_name'])}", mgr).json()["total"] == 0   # nothing was created

    @pytest.mark.parametrize("who", ["manager", "data_entry", "onboarding", "no_roles"])
    def test_an_ineligible_owner_is_refused_and_nothing_is_created(self, p, mgr, who):
        body = lead_body(assigned_to=p[who]["id"])
        r = api("POST", "/api/sales/leads", mgr, body)
        assert r.status_code == 400 and errors_of(r)["code"] == "assignee_not_eligible"
        assert api("GET", f"/api/sales/leads?q={quote(body['business_name'])}", mgr).json()["total"] == 0


# =============================================================================
class TestRead:
    def test_detail_and_summary_shapes(self, mgr):
        campaign = create_campaign(mgr)
        lead = create_lead(mgr, description="Long description", notes="Some notes", campaign_id=campaign["id"])
        detail = get_lead(mgr, lead["id"])
        assert detail["description"] == "Long description" and detail["notes"] == "Some notes"
        listed = api("GET", f"/api/sales/leads?campaign_id={campaign['id']}", mgr).json()["items"][0]
        assert listed["id"] == lead["id"] and "description" not in listed and "notes" not in listed   # long text stays out of lists

    def test_unknown_and_malformed_ids_are_404(self, mgr):
        assert api("GET", f"/api/sales/leads/{PHANTOM_ID}", mgr).status_code == 404
        assert api("GET", "/api/sales/leads/not-a-uuid", mgr).status_code == 404
        assert api("GET", f"/api/sales/leads/{PHANTOM_ID}/activities", mgr).status_code == 404

    def test_no_response_ever_carries_secrets_or_normalized_columns(self, mgr):
        lead = create_lead(mgr)
        for path in (f"/api/sales/leads/{lead['id']}", "/api/sales/leads?limit=5", f"/api/sales/leads/{lead['id']}/activities",
                     "/api/sales/leads/stage-counts", "/api/sales/assignees", "/api/sales/campaigns"):
            body = api("GET", path, mgr).json()
            assert not _has_key(body, ("password", "hash", "token", "secret", "_norm")), path


# =============================================================================
class TestUpdate:
    def test_partial_update_changes_only_what_was_sent(self, mgr):
        lead = create_lead(mgr, notes="keep me", priority="low")
        r = api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"priority": "high", "contact_name": "New Person"})
        assert r.status_code == 200, r.text
        updated = r.json()
        assert updated["priority"] == "high" and updated["contact_name"] == "New Person"
        for key in ("business_name", "phone", "email", "city", "notes", "source", "pipeline_stage"):
            assert updated[key] == lead[key], key
        assert updated["updated_at"] >= lead["updated_at"]

    def test_null_and_blank_clear_optional_fields(self, mgr):
        lead = create_lead(mgr, contact_name="Someone", notes="note", website=f"https://{uniq()}.example.com")
        r = api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"contact_name": None, "notes": "  ", "website": ""})
        assert r.status_code == 200
        cleared = r.json()
        assert cleared["contact_name"] is None and cleared["notes"] is None and cleared["website"] is None

    @pytest.mark.parametrize("field", ["business_name", "source", "priority"])
    def test_required_fields_cannot_be_cleared(self, mgr, field):
        lead = create_lead(mgr)
        r = api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {field: None})
        assert r.status_code == 400 and errors_of(r)["field"] == field
        assert get_lead(mgr, lead["id"])[field] == lead[field]

    def test_empty_body_is_rejected_and_unchanged_values_are_a_quiet_no_op(self, mgr):
        lead = create_lead(mgr)
        assert api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {}).status_code == 400
        before = len(activities(mgr, lead["id"]))
        r = api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"priority": lead["priority"], "city": lead["city"]})
        assert r.status_code == 200 and r.json()["updated_at"] == lead["updated_at"]
        assert len(activities(mgr, lead["id"])) == before   # no change, no event

    @pytest.mark.parametrize("forbidden", [
        {"pipeline_stage": "won"}, {"lost_reason": "other"}, {"assigned_to": PHANTOM_ID}, {"assigned_at": "2026-01-01T00:00:00Z"},
        {"created_by": PHANTOM_ID}, {"archived_at": "2026-01-01T00:00:00Z"}, {"closed_at": "2026-01-01T00:00:00Z"}, {"id": PHANTOM_ID},
        {"name_norm": "x"}, {"phone_norm": "123456"},
    ])
    def test_stage_owner_archive_and_derived_columns_cannot_be_patched(self, mgr, forbidden):
        lead = create_lead(mgr)
        r = api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, forbidden)
        assert r.status_code == 422
        assert get_lead(mgr, lead["id"]) == lead

    def test_validation_matches_create(self, mgr):
        lead = create_lead(mgr)
        url = f"/api/sales/leads/{lead['id']}"
        assert errors_of(api("PATCH", url, mgr, {"phone": "12@34"}))["field"] == "phone"
        assert errors_of(api("PATCH", url, mgr, {"source": "nope"}))["field"] == "source"
        assert errors_of(api("PATCH", url, mgr, {"campaign_id": PHANTOM_ID}))["field"] == "campaign_id"
        assert errors_of(api("PATCH", url, mgr, {"business_name": "@@@"}))["field"] == "business_name"
        assert errors_of(api("PATCH", url, mgr, {"latitude": 10.0}))["field"] == "longitude"   # lead has no coordinates yet
        assert api("PATCH", url, mgr, {"estimated_value": -5}).status_code == 422

    def test_coordinates_can_be_set_together_and_then_moved_one_at_a_time(self, mgr):
        lead = create_lead(mgr)
        url = f"/api/sales/leads/{lead['id']}"
        assert api("PATCH", url, mgr, {"latitude": 10.5, "longitude": 20.5}).status_code == 200
        r = api("PATCH", url, mgr, {"latitude": 11.5})
        assert r.status_code == 200 and (r.json()["latitude"], r.json()["longitude"]) == (11.5, 20.5)
        r = api("PATCH", url, mgr, {"latitude": None, "longitude": None})
        assert r.status_code == 200 and r.json()["latitude"] is None

    def test_source_priority_value_and_campaign_can_change(self, mgr):
        first, second = create_campaign(mgr), create_campaign(mgr)
        lead = create_lead(mgr, campaign_id=first["id"], source="website")
        r = api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"source": "referral", "campaign_id": second["id"], "estimated_value": "2500.00", "priority": "low"})
        assert r.status_code == 200
        body = r.json()
        assert (body["source"], body["priority"], body["estimated_value"]) == ("referral", "low", 2500.0)
        assert body["campaign"]["id"] == second["id"]
        r = api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"campaign_id": None})
        assert r.status_code == 200 and r.json()["campaign"] is None and r.json()["campaign_id"] is None

    def test_updating_unknown_lead(self, mgr):
        assert api("PATCH", f"/api/sales/leads/{PHANTOM_ID}", mgr, {"priority": "low"}).status_code == 404
        assert api("PATCH", "/api/sales/leads/garbage", mgr, {"priority": "low"}).status_code == 404


# =============================================================================
class TestArchive:
    def test_archive_hides_from_listings_but_keeps_everything(self, mgr):
        campaign = create_campaign(mgr)
        lead = create_lead(mgr, campaign_id=campaign["id"])
        r = api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        assert r.status_code == 200 and r.json()["changed"] is True
        archived = r.json()["lead"]
        assert archived["archived_at"] is not None and archived["can"]["restore"] is True and archived["can"]["archive"] is False
        assert archived["allowed_stages"] == [] and archived["can"]["update"] is False

        in_campaign = lambda extra="": api("GET", f"/api/sales/leads?campaign_id={campaign['id']}{extra}", mgr).json()  # noqa: E731
        assert in_campaign()["total"] == 0                                    # hidden by default
        assert in_campaign("&archived=only")["total"] == 1                     # findable
        assert in_campaign("&archived=all")["total"] == 1
        assert get_lead(mgr, lead["id"])["business_name"] == lead["business_name"]   # still readable by id
        assert api("GET", f"/api/sales/campaigns/{campaign['id']}", mgr).json()["lead_count"] == 1   # still belongs to its campaign
        assert event_types(mgr, lead["id"]) == ["lead_created", "lead_archived"]

    def test_an_archived_lead_is_read_only(self, p, mgr):
        lead = create_lead(mgr, assigned_to=p["employee"]["id"])
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        base = f"/api/sales/leads/{lead['id']}"
        for method, path, body in [
            ("PATCH", base, {"priority": "high"}),
            ("POST", f"{base}/stage", {"stage": "contacted"}),
            ("POST", f"{base}/assign", {"assigned_to": p["employee2"]["id"]}),
            ("POST", f"{base}/unassign", None),
        ]:
            r = api(method, path, mgr, body)
            assert r.status_code == 409 and errors_of(r)["code"] == "lead_archived", (path, r.text)
        assert get_lead(mgr, lead["id"])["priority"] == "medium"

    def test_archive_and_restore_are_idempotent(self, mgr):
        lead = create_lead(mgr)
        base = f"/api/sales/leads/{lead['id']}"
        assert api("DELETE", base, mgr).json()["changed"] is True
        assert api("DELETE", base, mgr).json()["changed"] is False
        assert api("POST", f"{base}/restore", mgr).json()["changed"] is True
        assert api("POST", f"{base}/restore", mgr).json()["changed"] is False
        assert event_types(mgr, lead["id"]) == ["lead_created", "lead_archived", "lead_restored"]   # only real changes are recorded

    def test_restore_brings_the_lead_back_unchanged(self, mgr):
        campaign = create_campaign(mgr)
        lead = create_lead(mgr, campaign_id=campaign["id"])
        api("DELETE", f"/api/sales/leads/{lead['id']}", mgr)
        restored = api("POST", f"/api/sales/leads/{lead['id']}/restore", mgr).json()["lead"]
        assert restored["archived_at"] is None and restored["can"]["update"] is True
        assert api("GET", f"/api/sales/leads?campaign_id={campaign['id']}", mgr).json()["total"] == 1
        assert api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"priority": "high"}).status_code == 200

    def test_unknown_ids(self, mgr):
        assert api("DELETE", f"/api/sales/leads/{PHANTOM_ID}", mgr).status_code == 404
        assert api("POST", f"/api/sales/leads/{PHANTOM_ID}/restore", mgr).status_code == 404


# =============================================================================
@pytest.fixture(scope="module")
def dataset(p, mgr):
    """12 leads in one fresh campaign with known attributes, so every listing test can filter to exactly them."""
    campaign = create_campaign(mgr)
    emp1, emp2 = p["employee"]["id"], p["employee2"]["id"]
    specs = [
        # name,        source,         priority, value,  owner, stage,        phone-tag,  contact
        ("Alpha",      "google_maps",  "high",   5000,   emp1,  "contacted",   "111",      "Nadia Karim"),
        ("Bravo",      "website",      "low",    100,    emp1,  "assigned",    "222",      "Omar Saleh"),
        ("Charlie",    "facebook",     "medium", None,   emp2,  "trial",       "333",      "Layla Hasan"),
        ("Delta",      "instagram",    "high",   250.5,  None,  "new",         "444",      None),
        ("Echo",       "tiktok",       "low",    9999,   emp2,  "negotiation", "555",      "Yusuf Adel"),
        ("Foxtrot",    "referral",     "medium", 300,    None,  "new",         "666",      "Rania Nabil"),
        ("Golf",       "cold_call",    "high",   None,   emp1,  "won",         "777",      "Kareem Jaber"),
        ("Hotel",      "inbound",      "low",    75,     emp2,  "lost",        "888",      "Dina Fouad"),
        ("India",      "advertisement", "medium", 1200,  None,  "new",         "999",      "Samir Tariq"),
        ("Juliet",     "manual_entry", "high",   40,     emp1,  "interested",  "123",      "Huda Mansour"),
        ("Kilo",       "import",       "low",    None,   None,  "new",         "234",      None),
        ("Lima",       "google_maps",  "medium", 60,     emp2,  "demo_scheduled", "345",   "Ziad Amin"),
    ]
    token = uniq(6)
    out = {}
    for name, source, priority, value, owner, stage, tag, contact in specs:
        body = lead_body(
            business_name=f"{name} {token}", source=source, priority=priority, campaign_id=campaign["id"],
            phone=f"+1555{tag}{rand_digits(6)}", contact_name=contact,
            whatsapp=f"+1666{tag}{rand_digits(6)}", email=f"{name.lower()}-{token}@lead-tests.example.com",
            estimated_value=value,
        )
        lead = api("POST", "/api/sales/leads", mgr, body)
        assert lead.status_code == 201, lead.text
        lead = lead.json()
        if owner:
            assign(mgr, lead["id"], owner)
        if stage not in ("new", "assigned"):
            if stage == "lost":
                set_stage(mgr, lead["id"], "lost", lost_reason="no_response")
            elif stage == "won":
                set_stage(mgr, lead["id"], "contacted")   # a lead must have been contacted before it can be won
                set_stage(mgr, lead["id"], "won")
            else:
                set_stage(mgr, lead["id"], stage)
        out[name] = get_lead(mgr, lead["id"])
    return {"campaign": campaign, "leads": out, "token": token, "emp1": emp1, "emp2": emp2}


def _list(headers, dataset, query="", **kw):
    r = api("GET", f"/api/sales/leads?campaign_id={dataset['campaign']['id']}{('&' + query) if query else ''}", headers)
    assert r.status_code == kw.get("expect", 200), r.text
    return r.json()


def _names(page):
    return [i["business_name"].split(" ")[0] for i in page["items"]]


class TestListPagination:
    def test_total_limit_offset_and_no_overlap(self, mgr, dataset):
        first = _list(mgr, dataset, "limit=5&offset=0")
        second = _list(mgr, dataset, "limit=5&offset=5")
        third = _list(mgr, dataset, "limit=5&offset=10")
        assert first["total"] == second["total"] == third["total"] == 12
        assert (len(first["items"]), len(second["items"]), len(third["items"])) == (5, 5, 2)
        assert (first["limit"], first["offset"], third["offset"]) == (5, 0, 10)
        ids = [i["id"] for page in (first, second, third) for i in page["items"]]
        assert len(ids) == len(set(ids)) == 12

    def test_default_page_size_and_offset_past_the_end(self, mgr, dataset):
        page = _list(mgr, dataset)
        assert page["limit"] == 25 and page["offset"] == 0 and len(page["items"]) == 12
        assert _list(mgr, dataset, "offset=100")["items"] == []

    @pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=abc", "sort=password", "order=sideways", "priority=urgent"])
    def test_bad_paging_and_sorting_parameters_are_422(self, mgr, dataset, query):
        _list(mgr, dataset, query, expect=422)


class TestListSorting:
    def test_default_is_newest_first(self, mgr, dataset):
        assert _names(_list(mgr, dataset)) == ["Lima", "Kilo", "Juliet", "India", "Hotel", "Golf", "Foxtrot", "Echo", "Delta", "Charlie", "Bravo", "Alpha"]

    def test_by_created_at_ascending(self, mgr, dataset):
        assert _names(_list(mgr, dataset, "sort=created_at&order=asc"))[:3] == ["Alpha", "Bravo", "Charlie"]

    def test_by_business_name_both_ways(self, mgr, dataset):
        assert _names(_list(mgr, dataset, "sort=business_name&order=asc")) == sorted(_names(_list(mgr, dataset)))
        assert _names(_list(mgr, dataset, "sort=business_name&order=desc"))[0] == "Lima"

    def test_by_priority_ranks_high_medium_low(self, mgr, dataset):
        order = [i["priority"] for i in _list(mgr, dataset, "sort=priority&order=desc")["items"]]
        assert order == ["high"] * 4 + ["medium"] * 4 + ["low"] * 4
        assert [i["priority"] for i in _list(mgr, dataset, "sort=priority&order=asc")["items"]][:4] == ["low"] * 4

    def test_by_estimated_value_puts_empty_values_last_either_way(self, mgr, dataset):
        for order in ("asc", "desc"):
            values = [i["estimated_value"] for i in _list(mgr, dataset, f"sort=estimated_value&order={order}")["items"]]
            assert values[-3:] == [None, None, None]
            present = values[:-3]
            assert present == sorted(present, reverse=order == "desc")

    def test_by_pipeline_stage_follows_pipeline_order(self, mgr, dataset):
        rank = {s: i for i, s in enumerate(["new", "assigned", "contacted", "interested", "demo_scheduled", "demo_completed", "trial", "negotiation", "won", "lost"])}
        stages = [i["pipeline_stage"] for i in _list(mgr, dataset, "sort=pipeline_stage&order=asc")["items"]]
        assert [rank[s] for s in stages] == sorted(rank[s] for s in stages)

    def test_by_updated_at_shows_the_most_recently_touched_first(self, mgr, dataset):
        target = dataset["leads"]["Alpha"]
        assert api("PATCH", f"/api/sales/leads/{target['id']}", mgr, {"notes": f"touched {uniq()}"}).status_code == 200
        assert _names(_list(mgr, dataset, "sort=updated_at&order=desc"))[0] == "Alpha"

    def test_sorting_is_stable_across_pages(self, mgr, dataset):
        by_pages = [i["id"] for off in (0, 4, 8) for i in _list(mgr, dataset, f"sort=priority&order=desc&limit=4&offset={off}")["items"]]
        assert len(by_pages) == len(set(by_pages)) == 12


class TestListFilters:
    def test_pipeline_stage(self, mgr, dataset):
        assert _names(_list(mgr, dataset, "pipeline_stage=new")) == ["Kilo", "India", "Foxtrot", "Delta"]
        assert set(_names(_list(mgr, dataset, "pipeline_stage=won,lost"))) == {"Golf", "Hotel"}
        assert _list(mgr, dataset, "pipeline_stage=demo_completed")["total"] == 0

    def test_unknown_stage_is_a_400_with_the_field(self, mgr, dataset):
        r = api("GET", f"/api/sales/leads?campaign_id={dataset['campaign']['id']}&pipeline_stage=bogus", mgr)
        assert r.status_code == 400 and errors_of(r)["field"] == "pipeline_stage"

    def test_assigned_employee(self, mgr, dataset):
        assert set(_names(_list(mgr, dataset, f"assigned_to={dataset['emp1']}"))) == {"Alpha", "Bravo", "Golf", "Juliet"}
        assert set(_names(_list(mgr, dataset, f"assigned_to={dataset['emp2']}"))) == {"Charlie", "Echo", "Hotel", "Lima"}
        assert set(_names(_list(mgr, dataset, "assigned_to=unassigned"))) == {"Delta", "Foxtrot", "India", "Kilo"}

    def test_me_means_the_caller(self, p, dataset):
        mine = _list(p["employee"]["headers"], dataset, "assigned_to=me")
        assert set(_names(mine)) == {"Alpha", "Bravo", "Golf", "Juliet"}

    def test_bad_assignee_value_is_a_400(self, mgr, dataset):
        r = api("GET", f"/api/sales/leads?campaign_id={dataset['campaign']['id']}&assigned_to=somebody", mgr)
        assert r.status_code == 400 and errors_of(r)["field"] == "assigned_to"

    def test_source(self, mgr, dataset):
        assert set(_names(_list(mgr, dataset, "source=google_maps"))) == {"Alpha", "Lima"}
        assert _names(_list(mgr, dataset, "source=tiktok")) == ["Echo"]
        assert _list(mgr, dataset, "source=nonexistent")["total"] == 0

    def test_priority(self, mgr, dataset):
        assert set(_names(_list(mgr, dataset, "priority=high"))) == {"Alpha", "Delta", "Golf", "Juliet"}
        assert _list(mgr, dataset, "priority=low")["total"] == 4

    def test_campaign(self, mgr, dataset):
        elsewhere = create_campaign(mgr)
        assert api("GET", f"/api/sales/leads?campaign_id={elsewhere['id']}", mgr).json()["total"] == 0
        assert api("GET", f"/api/sales/leads?campaign_id={dataset['campaign']['id']}", mgr).json()["total"] == 12
        assert api("GET", "/api/sales/leads?campaign_id=not-a-uuid", mgr).status_code == 422

    def test_created_date_range(self, mgr, dataset):
        today = app_today()
        day = lambda d: (today + timedelta(days=d)).isoformat()  # noqa: E731
        assert _list(mgr, dataset, f"created_from={day(-1)}&created_to={day(1)}")["total"] == 12
        assert _list(mgr, dataset, f"created_from={day(-1)}")["total"] == 12
        assert _list(mgr, dataset, f"created_to={day(1)}")["total"] == 12
        assert _list(mgr, dataset, f"created_to={day(-2)}")["total"] == 0
        assert _list(mgr, dataset, f"created_from={day(2)}")["total"] == 0
        assert api("GET", "/api/sales/leads?created_from=yesterday", mgr).status_code == 422

    def test_filters_combine(self, mgr, dataset):
        assert set(_names(_list(mgr, dataset, f"priority=high&assigned_to={dataset['emp1']}"))) == {"Alpha", "Golf", "Juliet"}
        assert _names(_list(mgr, dataset, f"priority=high&assigned_to={dataset['emp1']}&pipeline_stage=won")) == ["Golf"]
        assert _list(mgr, dataset, "priority=high&assigned_to=unassigned&source=cold_call")["total"] == 0

    def test_archived_leads_are_filtered_out_unless_asked_for(self, mgr, dataset):
        keep = create_campaign(mgr)
        a, b = create_lead(mgr, campaign_id=keep["id"]), create_lead(mgr, campaign_id=keep["id"])
        api("DELETE", f"/api/sales/leads/{b['id']}", mgr)
        url = f"/api/sales/leads?campaign_id={keep['id']}"
        assert [i["id"] for i in api("GET", url, mgr).json()["items"]] == [a["id"]]
        assert [i["id"] for i in api("GET", url + "&archived=only", mgr).json()["items"]] == [b["id"]]
        assert api("GET", url + "&archived=all", mgr).json()["total"] == 2
        assert api("GET", url + "&archived=maybe", mgr).status_code == 422


class TestListSearch:
    def test_business_name_case_insensitive_substring(self, mgr, dataset):
        assert _names(_list(mgr, dataset, f"q={quote('ALPHA ' + dataset['token'])}")) == ["Alpha"]
        assert _names(_list(mgr, dataset, "q=otel")) == ["Hotel"]
        assert set(_names(_list(mgr, dataset, "q=o"))) >= {"Bravo", "Echo", "Foxtrot", "Golf", "Hotel"}

    def test_phone_by_partial_digits_and_by_a_differently_formatted_number(self, mgr, dataset):
        alpha = dataset["leads"]["Alpha"]
        digits = "".join(ch for ch in alpha["phone"] if ch.isdigit())
        assert _names(_list(mgr, dataset, f"q={digits[:8]}")) == ["Alpha"]
        assert "Alpha" in _names(_list(mgr, dataset, f"q={quote(alpha['phone'])}"))
        spaced = f"+{digits[:4]} {digits[4:7]} {digits[7:]}"                      # same number, other spacing
        assert _names(_list(mgr, dataset, f"q={quote(spaced)}")) == ["Alpha"]

    def test_a_national_form_with_a_leading_zero_finds_the_stored_number(self, mgr):
        campaign = create_campaign(mgr)
        tail = "".join(random.choices("123456789", k=9))
        lead = create_lead(mgr, campaign_id=campaign["id"], phone=f"+964 {tail[:3]} {tail[3:6]} {tail[6:]}")
        national = f"0{tail[:3]} {tail[3:6]} {tail[6:]}"
        found = api("GET", f"/api/sales/leads?campaign_id={campaign['id']}", mgr, None, params={"q": national}).json()
        assert [i["id"] for i in found["items"]] == [lead["id"]]

    def test_text_that_merely_contains_digits_is_not_treated_as_a_phone_number(self, mgr):
        campaign = create_campaign(mgr)
        phone = f"+9{rand_digits(2)} 555 1234 {rand_digits(4)}"                                  # contains 555123 and 1234; unique per run
        lead = create_lead(mgr, campaign_id=campaign["id"], phone=phone, business_name=f"Corner Shop {uniq()}")
        url = f"/api/sales/leads?campaign_id={campaign['id']}"
        assert [i["id"] for i in api("GET", url, mgr, None, params={"q": "1234"}).json()["items"]] == [lead["id"]]              # looks like a number
        assert [i["id"] for i in api("GET", url, mgr, None, params={"q": "555 123"}).json()["items"]] == [lead["id"]]
        assert api("GET", url, mgr, None, params={"q": "Pharmacy 1234"}).json()["total"] == 0                                  # a name with digits in it
        assert api("GET", url, mgr, None, params={"q": "Branch 2 - 555123"}).json()["total"] == 0

    def test_whatsapp(self, mgr, dataset):
        whatsapp = dataset["leads"]["Charlie"]["whatsapp"]
        assert _names(_list(mgr, dataset, f"q={whatsapp[-9:]}")) == ["Charlie"]

    def test_email(self, mgr, dataset):
        assert _names(_list(mgr, dataset, f"q=echo-{dataset['token']}@")) == ["Echo"]

    def test_contact_name(self, mgr, dataset):
        assert _names(_list(mgr, dataset, "q=Yusuf")) == ["Echo"]
        assert _names(_list(mgr, dataset, "q=kareem%20jaber")) == ["Golf"]

    def test_no_match_and_blank_query(self, mgr, dataset):
        assert _list(mgr, dataset, "q=zzz-no-such-lead")["total"] == 0
        assert _list(mgr, dataset, "q=%20%20")["total"] == 12    # blank search = no search

    def test_like_wildcards_are_matched_literally(self, mgr):
        campaign = create_campaign(mgr)
        special = create_lead(mgr, campaign_id=campaign["id"], business_name=f"50%_off Store {uniq()}")
        create_lead(mgr, campaign_id=campaign["id"], business_name=f"Plain Store {uniq()}")
        url = f"/api/sales/leads?campaign_id={campaign['id']}"
        for q in ("50%25_off", "%25_"):
            assert [i["id"] for i in api("GET", f"{url}&q={q}", mgr).json()["items"]] == [special["id"]], q
        assert api("GET", f"{url}&q=%25", mgr).json()["total"] == 1     # '%' alone is not "match everything"
        assert api("GET", f"{url}&q=_", mgr).json()["total"] == 1

    def test_search_composes_with_filters_and_pagination(self, mgr, dataset):
        page = _list(mgr, dataset, f"q={quote(dataset['token'])}&priority=high&limit=2&offset=2")
        assert page["total"] == 4 and len(page["items"]) == 2

    def test_search_input_is_bounded(self, mgr):
        assert api("GET", f"/api/sales/leads?q={'x' * 201}", mgr).status_code == 422


class TestStageCounts:
    def test_counts_per_stage_within_the_filters(self, mgr, dataset):
        r = api("GET", f"/api/sales/leads/stage-counts?campaign_id={dataset['campaign']['id']}", mgr)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 12
        assert body["counts"] == {"new": 4, "assigned": 1, "contacted": 1, "interested": 1, "demo_scheduled": 1,
                                  "demo_completed": 0, "trial": 1, "negotiation": 1, "won": 1, "lost": 1}

    def test_every_stage_is_present_even_with_no_leads(self, mgr):
        empty = create_campaign(mgr)
        body = api("GET", f"/api/sales/leads/stage-counts?campaign_id={empty['id']}", mgr).json()
        assert body["total"] == 0 and len(body["counts"]) == 10 and set(body["counts"].values()) == {0}

    def test_the_stage_filter_itself_is_ignored_so_a_board_can_show_every_column(self, mgr, dataset):
        base = f"/api/sales/leads/stage-counts?campaign_id={dataset['campaign']['id']}"
        assert api("GET", base + "&pipeline_stage=won", mgr).json()["counts"]["new"] == 4

    def test_other_filters_apply(self, mgr, dataset):
        body = api("GET", f"/api/sales/leads/stage-counts?campaign_id={dataset['campaign']['id']}&assigned_to={dataset['emp1']}", mgr).json()
        assert body["total"] == 4 and body["counts"]["new"] == 0 and body["counts"]["won"] == 1


# =============================================================================
class TestHostileInput:
    """Text that cannot be stored, or that looks like an attack, must produce a clean answer - never a 500 - and
    ordinary Unicode must round-trip untouched."""

    @pytest.mark.parametrize("field", ["business_name", "notes", "phone", "city", "contact_name", "website", "address", "description"])
    def test_a_nul_character_is_refused_not_a_500(self, mgr, field):
        assert api("POST", "/api/sales/leads", mgr, lead_body(**{field: "a\u0000b"})).status_code == 422

    def test_nul_is_refused_on_update_stage_note_and_campaign_text_too(self, mgr):
        lead = create_lead(mgr)
        assert api("PATCH", f"/api/sales/leads/{lead['id']}", mgr, {"notes": "x\u0000"}).status_code == 422
        assert api("POST", f"/api/sales/leads/{lead['id']}/stage", mgr, {"stage": "lost", "lost_reason": "other", "note": "\u0000"}).status_code == 422
        assert api("POST", "/api/sales/campaigns", mgr, {"name": "Bad\u0000Name"}).status_code == 422
        assert api("POST", "/api/sales/leads/duplicate-check", mgr, {"phone": "07\u000070"}).status_code == 422

    def test_nul_in_search_and_filters_is_harmless(self, mgr):
        assert api("GET", "/api/sales/leads?q=abc%00def", mgr).status_code == 200
        assert api("GET", "/api/sales/leads?source=goo%00gle", mgr).status_code == 422
        assert api("GET", "/api/sales/leads?assigned_to=%00", mgr).status_code == 400
        assert api("GET", "/api/sales/campaigns?q=abc%00", mgr).status_code == 200
        assert api("GET", "/api/sales/campaigns?source=x%00", mgr).status_code == 422

    def test_unicode_round_trips_untouched(self, mgr):
        name = f"مقهى النخيل ☕ Café 😀 {uniq()}"
        lead = create_lead(mgr, business_name=name, contact_name="محمد علي", notes="ملاحظات\nسطر ثانٍ 🙂", city="بغداد")
        stored = get_lead(mgr, lead["id"])
        assert stored["business_name"] == name and stored["contact_name"] == "محمد علي" and stored["notes"] == "ملاحظات\nسطر ثانٍ 🙂"
        assert api("GET", "/api/sales/leads", mgr, None, params={"q": "النخيل"}).json()["total"] >= 1

    def test_injection_looking_text_is_just_data(self, mgr):
        name = f"Robert'); DROP TABLE sales_leads;-- {uniq()}"
        lead = create_lead(mgr, business_name=name, notes="<script>alert(1)</script>", contact_name="' OR '1'='1")
        assert get_lead(mgr, lead["id"])["business_name"] == name and get_lead(mgr, lead["id"])["notes"] == "<script>alert(1)</script>"
        found = api("GET", "/api/sales/leads", mgr, None, params={"q": "DROP TABLE"}).json()
        assert any(i["id"] == lead["id"] for i in found["items"])
        assert api("GET", "/api/sales/leads?limit=1", mgr).status_code == 200      # the table is very much still there
