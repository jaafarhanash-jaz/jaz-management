"""Pre-Phase-2 hardening: the email / phone identifier namespaces.

POST /auth/login takes ONE free-text identifier. It used to be resolved with `email = X OR phone = X` and
`scalar_one_or_none()`, so one user's email equal to another user's phone made that email un-loginable
(MultipleResultsFound -> HTTP 500). Any authenticated user could cause that, through PUT /employee/profile.

What is proven here:
  * LOGIN      - an identifier is classified ('@' -> email column, else phone column) and looked up in ONE
                 column; the legacy collision no longer produces a 500; more than one live match fails closed
                 (401, generic) and is logged without the identifier.
  * PHONE RULE - the shared rule (services/identifiers.py) rejects '@' and nothing else, and is applied on every
                 phone write path (profile, owner employee update, company create/update, Sales staff
                 create/update); the two previously unchecked paths now also enforce same-column uniqueness.
  * DATABASE   - ck_users_email_has_at / ck_users_phone_no_at exist, are still NOT VALID (the migration must not
                 validate them), and reject bad writes.

Runs against the scratch database and a scratch backend only (see sales_test_utils.assert_scratch_target).
DDL used to recreate legacy data (dropping a CHECK / index) always happens inside a transaction that is rolled
back - the constraint is never left dropped.
"""
from sales_test_utils import assert_scratch_target

assert_scratch_target(need_http=False)  # must run BEFORE importing database (which loads .env)

import asyncio
import logging
import random
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

import repositories.users as users_repo
import services.auth as auth_service
from database import SessionLocal, engine
from models import User
from sales_test_utils import api, auth, create_staff, login, mint_token, unique_email
# token fixtures that mint instead of logging in (see sales_test_utils) - override conftest's for this module
from sales_test_utils import admin_token, owner_token, owner_user  # noqa: F401
from services import identifiers

PW = "Idh#12345678"


def run(coro):
    async def _wrapped():
        try:
            return await coro
        finally:
            await engine.dispose()
    return asyncio.run(_wrapped())


async def _scalar(sql, **params):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), params)).scalar_one_or_none()


def db_scalar(sql, **params):
    return run(_scalar(sql, **params))


async def _make_user(db, *, email=None, phone=None, role="jaz_staff", company_id=None, password=PW, name="IDH user"):
    user = User(
        id=uuid.uuid4(),
        email=email or f"idh-{uuid.uuid4().hex[:10]}@example.com",
        phone=phone or f"+1888{random.randint(1000000, 9999999)}",
        password=await auth_service.hash_password(password),
        name=name, role=role, company_id=company_id, status="active",
    )
    db.add(user)
    await db.flush()
    return user


async def _purge(user_ids):
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id.in_(user_ids)))  # refresh_tokens cascade
        await db.commit()


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def demo_company_id(owner_user):
    return db_scalar("SELECT company_id FROM users WHERE id = :id", id=uuid.UUID(owner_user["id"]))


@pytest.fixture(scope="module")
def owner_h(owner_token):
    return auth(owner_token)


@pytest.fixture(scope="module")
def employees(demo_company_id):
    """Two throwaway employees in the demo company (inserted directly, removed afterwards)."""
    async def _create():
        async with SessionLocal() as db:
            a = await _make_user(db, role="employee", company_id=demo_company_id, name="IDH employee A")
            b = await _make_user(db, role="employee", company_id=demo_company_id, name="IDH employee B")
            await db.commit()
            return [{"id": str(u.id), "email": u.email, "phone": u.phone, "headers": auth(mint_token(u.id))} for u in (a, b)]
    made = run(_create())
    yield made
    run(_purge([uuid.UUID(e["id"]) for e in made]))


def _phone_of(user_id):
    return db_scalar("SELECT phone FROM users WHERE id = :id", id=uuid.UUID(user_id))


def _fresh(phone):
    """Keeps the STYLE of a phone but makes it unique, so a test never trips over a leftover row in the
    (long-lived) scratch database."""
    return f"{phone}{random.randint(1000, 9999)}"


# =============================================================================
# LOGIN RESOLUTION (in-process, real database; DDL for legacy data is rolled back)
# =============================================================================
class TestLoginResolution:
    def test_email_identifier_logs_in_by_email(self):
        async def _run():
            async with SessionLocal() as db:
                try:
                    u = await _make_user(db)
                    session = await auth_service.login(db, u.email, PW)
                    assert session["user"]["id"] == str(u.id) and "password" not in session["user"]
                finally:
                    await db.rollback()
        run(_run())

    def test_phone_identifier_logs_in_by_phone(self):
        async def _run():
            async with SessionLocal() as db:
                try:
                    u = await _make_user(db, phone="+964 770 123 4567")  # spaces and '+' are ordinary phone text
                    session = await auth_service.login(db, "+964 770 123 4567", PW)
                    assert session["user"]["id"] == str(u.id)
                finally:
                    await db.rollback()
        run(_run())

    def test_wrong_password_and_unknown_identifiers_are_a_plain_401(self):
        async def _run():
            async with SessionLocal() as db:
                try:
                    u = await _make_user(db)
                    for identifier, password in [(u.email, "wrong-password"), ("nobody@example.com", PW), ("+10000000000", PW), ("@", PW), ("", PW)]:
                        with pytest.raises(HTTPException) as exc:
                            await auth_service.login(db, identifier, password)
                        assert exc.value.status_code == 401 and exc.value.detail == "Invalid credentials"
                finally:
                    await db.rollback()
        run(_run())

    def test_legacy_collision_email_of_one_user_is_the_phone_of_another_is_no_longer_a_500(self):
        """The exact pre-fix failure: user A's email == user B's phone. Recreated inside a transaction (the
        phone CHECK is dropped there, as it would be absent from legacy data) and rolled back."""
        async def _run():
            async with SessionLocal() as db:
                try:
                    await db.execute(text("ALTER TABLE users DROP CONSTRAINT ck_users_phone_no_at"))
                    a = await _make_user(db, password="A-password-#1")
                    b = await _make_user(db, phone=a.email, password="B-password-#2")  # B's phone == A's email
                    # old lookup: two rows -> MultipleResultsFound -> 500. Now: '@' means email column -> exactly A.
                    session = await auth_service.login(db, a.email, "A-password-#1")
                    assert session["user"]["id"] == str(a.id)
                    # ...and B's password with that identifier is a clean 401, never an exception
                    with pytest.raises(HTTPException) as exc:
                        await auth_service.login(db, a.email, "B-password-#2")
                    assert exc.value.status_code == 401
                    assert b.phone == a.email  # the polluted row really was in place
                finally:
                    await db.rollback()
        run(_run())

    def test_more_than_one_live_match_fails_closed_with_a_generic_401_and_a_log(self, caplog):
        """Even a single-column match can be ambiguous if a unique index is missing (or legacy data): never
        `.first()`, never a 500 - a generic 401 identical to a wrong password, logged without the identifier."""
        async def _run():
            async with SessionLocal() as db:
                try:
                    await db.execute(text("DROP INDEX uq_users_email_active"))
                    shared = f"twin-{uuid.uuid4().hex[:10]}@example.com"
                    u1 = await _make_user(db, email=shared)
                    u2 = await _make_user(db, email=shared)
                    with caplog.at_level(logging.WARNING, logger="services.auth"):
                        with pytest.raises(HTTPException) as exc:
                            await auth_service.login(db, shared, PW)  # the password is CORRECT for both rows
                    assert exc.value.status_code == 401 and exc.value.detail == "Invalid credentials"
                    logged = "\n".join(r.getMessage() for r in caplog.records)
                    assert "matches 2 live accounts" in logged and str(u1.id) in logged and str(u2.id) in logged
                    assert shared not in logged  # the identifier itself is not written to the log
                    # the response must not say which column collided: same body as any other failed login
                    assert "email" not in str(exc.value.detail).lower() and "phone" not in str(exc.value.detail).lower()
                finally:
                    await db.rollback()
        run(_run())

    def test_ambiguity_in_the_phone_column_fails_closed_too(self):
        async def _run():
            async with SessionLocal() as db:
                try:
                    await db.execute(text("DROP INDEX uq_users_phone_active"))
                    shared = f"+1999{random.randint(1000000, 9999999)}"
                    await _make_user(db, phone=shared)
                    await _make_user(db, phone=shared)
                    with pytest.raises(HTTPException) as exc:
                        await auth_service.login(db, shared, PW)
                    assert exc.value.status_code == 401
                finally:
                    await db.rollback()
        run(_run())

    def test_classification_consults_exactly_one_column(self, monkeypatch):
        calls = []

        async def spy_email(db, identifier, limit=2):
            calls.append(("email", identifier)); return []

        async def spy_phone(db, identifier, limit=2):
            calls.append(("phone", identifier)); return []

        monkeypatch.setattr(users_repo, "find_live_by_email", spy_email)
        monkeypatch.setattr(users_repo, "find_live_by_phone", spy_phone)

        async def _run():
            async with SessionLocal() as db:
                for identifier in ("someone@example.com", "0501234567", "ext-12", "a@b", "+964 770 1"):
                    with pytest.raises(HTTPException):
                        await auth_service.login(db, identifier, PW)
        run(_run())
        assert calls == [("email", "someone@example.com"), ("phone", "0501234567"), ("phone", "ext-12"), ("email", "a@b"), ("phone", "+964 770 1")]

    def test_legacy_email_without_at_is_not_a_login_identifier(self):
        """Documents the trade-off of classifying by '@': an email that has no '@' (impossible through the API,
        and what the pre-deploy detection query looks for) is looked up as a phone and therefore not found."""
        async def _run():
            async with SessionLocal() as db:
                try:
                    await db.execute(text("ALTER TABLE users DROP CONSTRAINT ck_users_email_has_at"))
                    u = await _make_user(db, email=f"noatsign{uuid.uuid4().hex[:8]}")
                    with pytest.raises(HTTPException) as exc:
                        await auth_service.login(db, u.email, PW)
                    assert exc.value.status_code == 401
                finally:
                    await db.rollback()
        run(_run())


# =============================================================================
# THE SHARED PHONE RULE (unit)
# =============================================================================
VALID_PHONES = [
    "0501234567", "+964 770 123 4567", "(555) 010-1234", "00964770123456", "+971-50-123-4567",
    "ext-77", "١٢٣٤٥٦٧٨٩٠",  # Arabic-Indic digits
    "123", "x" * 200,
]


class TestSharedPhoneRule:
    @pytest.mark.parametrize("phone", VALID_PHONES)
    def test_existing_style_values_stay_valid(self, phone):
        assert identifiers.phone_error(phone) is None
        assert identifiers.validate_phone(phone) == phone
        assert identifiers.validate_phone(phone, field="owner_phone") == phone

    def test_the_rule_is_only_about_the_at_sign(self):
        # no digits-only rule, no country format, no length rule, no trimming - exactly as before
        for phone in ("", " ", "abc", "٠٥٠", "+", "a b c", "1" * 500):
            assert identifiers.phone_error(phone) is None

    @pytest.mark.parametrize("phone", ["a@b.com", "@", "0501@234", "admin@jaz.com", " @ ", "x@"])
    def test_at_sign_is_rejected(self, phone):
        assert identifiers.phone_error(phone) == identifiers.PHONE_HAS_AT_SIGN
        with pytest.raises(HTTPException) as plain:
            identifiers.validate_phone(phone)
        assert plain.value.status_code == 400 and plain.value.detail == identifiers.PHONE_HAS_AT_SIGN  # string: what the employee/profile screens toast
        with pytest.raises(HTTPException) as shaped:
            identifiers.validate_phone(phone, field="owner_phone")
        assert shaped.value.detail == {"field": "owner_phone", "message": identifiers.PHONE_HAS_AT_SIGN}  # what Companies/Sales attach to an input

    @pytest.mark.parametrize("phone", [None, 12345, 1.5, ["0501"], {"a": 1}, True])
    def test_non_text_is_rejected_instead_of_crashing(self, phone):
        assert identifiers.phone_error(phone) == identifiers.PHONE_NOT_TEXT
        with pytest.raises(HTTPException) as exc:
            identifiers.validate_phone(phone)
        assert exc.value.status_code == 400

    def test_integrity_errors_are_translated_and_unrelated_ones_are_not(self):
        def err(msg):
            return IntegrityError("INSERT", {}, Exception(msg))
        dup = identifiers.translate_phone_integrity_error(err('duplicate key value violates unique constraint "uq_users_phone_active"'))
        assert dup.status_code == 400 and dup.detail == identifiers.PHONE_TAKEN
        at = identifiers.translate_phone_integrity_error(err('violates check constraint "ck_users_phone_no_at"'), field="phone")
        assert at.detail == {"field": "phone", "message": identifiers.PHONE_HAS_AT_SIGN}
        assert identifiers.translate_phone_integrity_error(err('violates unique constraint "uq_users_email_active"')) is None

    def test_login_classification(self):
        assert identifiers.is_email_identifier("a@b.co") and identifiers.is_email_identifier("@")
        assert not identifiers.is_email_identifier("0501234567") and not identifiers.is_email_identifier("")


# =============================================================================
# EVERY PHONE WRITE PATH, over HTTP (scratch backend)
# =============================================================================
class TestPhoneWritePathsHttp:
    # ---- PUT /employee/profile: reachable by every authenticated role; used to have NO checks ----
    def test_profile_rejects_a_phone_containing_at(self, employees):
        me = employees[0]
        r = api("PUT", "/api/employee/profile", me["headers"], {"phone": "victim@example.com"})
        assert r.status_code == 400 and r.json()["detail"] == identifiers.PHONE_HAS_AT_SIGN
        assert _phone_of(me["id"]) == me["phone"]  # unchanged

    def test_profile_cannot_be_used_to_lock_someone_out_of_email_login(self, employees, admin_h):
        """The original attack: set my phone to somebody else's EMAIL (here the Super Admin's)."""
        me = employees[0]
        r = api("PUT", "/api/employee/profile", me["headers"], {"phone": "admin@jaz.com"})
        assert r.status_code == 400
        assert db_scalar("SELECT count(*) FROM users WHERE phone = 'admin@jaz.com'") == 0

    def test_profile_duplicate_phone_is_a_clean_400_not_a_500(self, employees):
        a, b = employees
        r = api("PUT", "/api/employee/profile", a["headers"], {"phone": b["phone"]})
        assert r.status_code == 400 and r.json()["detail"] == identifiers.PHONE_TAKEN
        assert _phone_of(a["id"]) == a["phone"]

    @pytest.mark.parametrize("style", VALID_PHONES[:7])
    def test_profile_accepts_existing_style_phones(self, employees, style):
        a, phone = employees[0], _fresh(style)
        try:
            r = api("PUT", "/api/employee/profile", a["headers"], {"phone": phone})
            assert r.status_code == 200, r.text
            assert _phone_of(a["id"]) == phone
        finally:
            run(_set_phone(a["id"], a["phone"]))

    def test_profile_resaving_my_own_phone_is_not_a_duplicate(self, employees):
        a = employees[0]
        r = api("PUT", "/api/employee/profile", a["headers"], {"phone": a["phone"], "name": "IDH employee A"})
        assert r.status_code == 200, r.text

    @pytest.mark.parametrize("bad", [None, 501234567, ["0501"]])
    def test_profile_non_text_phone_is_a_400(self, employees, bad):
        r = api("PUT", "/api/employee/profile", employees[0]["headers"], {"phone": bad})
        assert r.status_code == 400 and r.json()["detail"] == identifiers.PHONE_NOT_TEXT

    def test_profile_name_only_update_is_unaffected(self, employees):
        r = api("PUT", "/api/employee/profile", employees[0]["headers"], {"name": "IDH employee A"})
        assert r.status_code == 200

    # ---- PUT /owner/employees/{id}: the company owner's edit; used to have NO checks ----
    def test_owner_employee_update_rejects_at(self, employees, owner_h):
        e = employees[1]
        r = api("PUT", f"/api/owner/employees/{e['id']}", owner_h, {"phone": "admin@jaz.com"})
        assert r.status_code == 400 and r.json()["detail"] == identifiers.PHONE_HAS_AT_SIGN
        assert _phone_of(e["id"]) == e["phone"]

    def test_owner_employee_update_duplicate_phone_is_a_clean_400(self, employees, owner_h):
        a, b = employees
        r = api("PUT", f"/api/owner/employees/{b['id']}", owner_h, {"phone": a["phone"]})
        assert r.status_code == 400 and r.json()["detail"] == identifiers.PHONE_TAKEN
        assert _phone_of(b["id"]) == b["phone"]

    @pytest.mark.parametrize("style", VALID_PHONES[:7])
    def test_owner_employee_update_accepts_existing_style_phones(self, employees, owner_h, style):
        b, phone = employees[1], _fresh(style)
        try:
            r = api("PUT", f"/api/owner/employees/{b['id']}", owner_h, {"phone": phone})
            assert r.status_code == 200, r.text
            assert _phone_of(b["id"]) == phone
        finally:
            run(_set_phone(b["id"], b["phone"]))

    def test_owner_employee_update_resaving_the_same_phone_is_fine(self, employees, owner_h):
        b = employees[1]
        r = api("PUT", f"/api/owner/employees/{b['id']}", owner_h, {"phone": b["phone"], "department": "IDH"})
        assert r.status_code == 200, r.text

    def test_owner_employee_update_non_text_phone_is_a_400(self, employees, owner_h):
        r = api("PUT", f"/api/owner/employees/{employees[1]['id']}", owner_h, {"phone": None})
        assert r.status_code == 400

    # ---- POST /owner/employees (create) ----
    def test_owner_employee_create_rejects_at_and_accepts_a_normal_phone(self, owner_h):
        body = {"email": unique_email("idh-emp"), "phone": "boss@example.com", "password": "Emp#12345678", "name": "IDH new", "role": "employee"}
        r = api("POST", "/api/owner/employees", owner_h, body)
        assert r.status_code == 400 and r.json()["detail"] == identifiers.PHONE_HAS_AT_SIGN
        assert db_scalar("SELECT count(*) FROM users WHERE email = :e", e=body["email"]) == 0  # nothing was created
        ok = api("POST", "/api/owner/employees", owner_h, {**body, "phone": _fresh("(555) 010-")})
        assert ok.status_code in (200, 201), ok.text
        run(_retire_by_email(body["email"]))

    # ---- company create / update (Super Admin) ----
    def test_company_create_and_update_apply_the_rule_in_the_field_error_shape(self, admin_h, owner_user):
        plan_id = str(db_scalar("SELECT id FROM subscription_plans WHERE is_active LIMIT 1"))
        owner_email = unique_email("idh-owner")
        body = {"name": "IDH Co", "owner_email": owner_email, "owner_name": "IDH Owner", "owner_password": "Owner#12345678",
                "owner_phone": "owner@example.com", "subscription_plan_id": plan_id}
        r = api("POST", "/api/admin/companies", admin_h, body)
        assert r.status_code == 400 and r.json()["detail"] == {"field": "owner_phone", "message": identifiers.PHONE_HAS_AT_SIGN}
        assert db_scalar("SELECT count(*) FROM users WHERE email = :e", e=owner_email) == 0

        created = api("POST", "/api/admin/companies", admin_h, {**body, "owner_phone": _fresh("(555) 010-")})
        assert created.status_code in (200, 201), created.text
        cid = created.json()["id"]
        try:
            bad = api("PUT", f"/api/admin/companies/{cid}", admin_h, {"owner_phone": "x@y.com"})
            assert bad.status_code == 400 and bad.json()["detail"] == {"field": "owner_phone", "message": identifiers.PHONE_HAS_AT_SIGN}
            good = api("PUT", f"/api/admin/companies/{cid}", admin_h, {"owner_phone": _fresh("+964 770 555 ")})
            assert good.status_code == 200, good.text
            dup = api("PUT", f"/api/admin/companies/{cid}", admin_h, {"owner_phone": owner_user["phone"]})
            assert dup.status_code == 400 and dup.json()["detail"]["field"] == "owner_phone"  # the existing same-column check still works
        finally:
            run(_retire_by_email(owner_email))

    # ---- Sales staff create / update ----
    def test_sales_staff_create_and_update_apply_the_rule(self, admin_h):
        base = {"name": "IDH staff", "email": unique_email("idh-staff"), "phone": "boss@example.com", "password": "Staff#12345", "role_keys": []}
        r = api("POST", "/api/sales/team", admin_h, base)
        assert r.status_code == 400 and r.json()["detail"] == {"field": "phone", "message": identifiers.PHONE_HAS_AT_SIGN}
        assert db_scalar("SELECT count(*) FROM users WHERE email = :e", e=base["email"]) == 0

        first, second = _fresh("(555) 010-"), _fresh("+964 770 555 ")
        ok = api("POST", "/api/sales/team", admin_h, {**base, "phone": first})
        assert ok.status_code == 201, ok.text
        sid = ok.json()["id"]
        bad = api("PATCH", f"/api/sales/team/{sid}", admin_h, {"phone": "a@b.co"})
        assert bad.status_code == 400 and bad.json()["detail"] == {"field": "phone", "message": identifiers.PHONE_HAS_AT_SIGN}
        assert _phone_of(sid) == first
        good = api("PATCH", f"/api/sales/team/{sid}", admin_h, {"phone": second})
        assert good.status_code == 200 and _phone_of(sid) == second
        other = create_staff(admin_h, [], "idh-other", do_login=False)
        dup = api("PATCH", f"/api/sales/team/{sid}", admin_h, {"phone": other["phone"]})
        assert dup.status_code == 400 and dup.json()["detail"]["field"] == "phone"

    # ---- login over real HTTP ----
    def test_http_login_by_email_and_by_phone_reach_the_same_account(self, admin_h):
        staff = create_staff(admin_h, [], "idh-login", do_login=False)
        by_email = login(staff["email"], staff["password"])
        by_phone = login(staff["phone"], staff["password"])
        assert by_email["user"]["id"] == by_phone["user"]["id"] == staff["id"]
        login(staff["email"], "not-the-password", expect=401)
        login("nobody-idh@example.com", staff["password"], expect=401)
        login("+15550000000", staff["password"], expect=401)
        login("'; DROP TABLE users; --@x", staff["password"], expect=401)  # hostile-looking identifiers are just 401s


async def _set_phone(user_id, phone):
    async with SessionLocal() as db:
        await db.execute(text("UPDATE users SET phone = :p WHERE id = :id"), {"p": phone, "id": uuid.UUID(user_id)})
        await db.commit()


async def _retire_by_email(email):
    """Soft-delete a throwaway account (and the company it owns), the platform's own way to remove things:
    it frees the email and phone for reuse and leaves no dangling FK."""
    async with SessionLocal() as db:
        await db.execute(text("UPDATE companies SET deleted_at = now() WHERE owner_id IN (SELECT id FROM users WHERE email = :e)"), {"e": email})
        await db.execute(text("UPDATE users SET deleted_at = now() WHERE email = :e"), {"e": email})
        await db.commit()


# =============================================================================
# THE APPLICATION LAYER, ON ITS OWN
# =============================================================================
class TestApplicationLayerRejectsIndependentlyOfTheDatabase:
    """Two independent layers guard the rule: the application validators and ck_users_phone_no_at. Through the HTTP
    tests above a missing validator would be masked by the CHECK (which produces the same clean 400 on the paths that
    translate it). Here the CHECK is dropped INSIDE a transaction that is rolled back, so only the application rule
    can reject - proving each write path enforces it itself (e.g. before the NOT VALID constraints exist)."""

    BAD = "someone@example.com"

    def _in_txn(self, body):
        async def _run():
            async with SessionLocal() as db:
                try:
                    await db.execute(text("ALTER TABLE users DROP CONSTRAINT ck_users_phone_no_at"))
                    await body(db)
                finally:
                    await db.rollback()
        run(_run())

    @staticmethod
    async def _company_id(db):
        return (await db.execute(text("SELECT company_id FROM users WHERE email = 'owner@demo.com'"))).scalar_one()

    @staticmethod
    def _rejected(exc, expect_detail=identifiers.PHONE_HAS_AT_SIGN):
        assert exc.value.status_code == 400 and exc.value.detail == expect_detail

    def test_profile_update(self):
        import services.employees as employees_service

        async def body(db):
            u = await _make_user(db, role="employee", company_id=await self._company_id(db))
            with pytest.raises(HTTPException) as exc:
                await employees_service.update_own_profile(db, str(u.id), {"phone": self.BAD})
            self._rejected(exc)
            assert (await db.execute(text("SELECT phone FROM users WHERE id = :id"), {"id": u.id})).scalar_one() == u.phone
            await employees_service.update_own_profile(db, str(u.id), {"phone": "+964 770 000 1234"})  # control: valid still works
        self._in_txn(body)

    def test_owner_employee_update(self):
        import services.employees as employees_service

        async def body(db):
            company_id = await self._company_id(db)
            u = await _make_user(db, role="employee", company_id=company_id)
            with pytest.raises(HTTPException) as exc:
                await employees_service.update_employee(db, company_id, str(u.id), {"phone": self.BAD})
            self._rejected(exc)
            await employees_service.update_employee(db, company_id, str(u.id), {"phone": "0501234500"})  # control
        self._in_txn(body)

    def test_owner_employee_create(self):
        import services.employees as employees_service
        from types import SimpleNamespace

        async def body(db):
            data = SimpleNamespace(email=unique_email("appl"), phone=self.BAD, password="Emp#12345678", name="x", department=None, position=None, cv=None)
            with pytest.raises(HTTPException) as exc:
                await employees_service.create_employee(db, await self._company_id(db), None, data)
            self._rejected(exc)
            assert (await db.execute(text("SELECT count(*) FROM users WHERE email = :e"), {"e": data.email})).scalar_one() == 0
        self._in_txn(body)

    def test_company_create_and_update(self):
        import services.admin as admin_service
        from types import SimpleNamespace

        async def body(db):
            field_error = {"field": "owner_phone", "message": identifiers.PHONE_HAS_AT_SIGN}
            create = SimpleNamespace(owner_phone=self.BAD, owner_email=unique_email("appl-co"), owner_name="x", owner_password="Owner#12345678",
                                     name="x", address=None, subscription_plan_id=str(uuid.uuid4()))
            with pytest.raises(HTTPException) as exc:
                await admin_service.create_company(db, create)
            self._rejected(exc, field_error)
            company_id = await self._company_id(db)
            update = SimpleNamespace(name=None, address=None, subscription_plan_id=None, owner_name=None, owner_email=None,
                                     owner_phone=self.BAD, owner_password=None, owner_password_confirm=None)
            with pytest.raises(HTTPException) as exc2:
                await admin_service.update_company(db, str(company_id), update)
            self._rejected(exc2, field_error)
        self._in_txn(body)

    def test_sales_staff_create_and_update(self):
        from sales.schemas import StaffCreate, StaffUpdate
        from sales.services import team as team_service
        from sales.services.access import resolve_staff_context
        from sales.services.audit import new_audit_context
        from sales_test_utils import lookup_user

        admin = lookup_user("admin@jaz.com")

        async def body(db):
            ctx = await resolve_staff_context(db, {"id": admin["id"], "role": "super_admin"})
            audit = new_audit_context("appl")
            field_error = {"field": "phone", "message": identifiers.PHONE_HAS_AT_SIGN}
            create = StaffCreate(name="x", email=unique_email("appl-staff"), phone=self.BAD, password="Staff#12345", role_keys=[])
            with pytest.raises(HTTPException) as exc:
                await team_service.create_staff(db, ctx, create, audit)
            self._rejected(exc, field_error)
            assert (await db.execute(text("SELECT count(*) FROM users WHERE email = :e"), {"e": create.email})).scalar_one() == 0
            staff = await _make_user(db)
            with pytest.raises(HTTPException) as exc2:
                await team_service.update_staff(db, ctx, str(staff.id), StaffUpdate(phone=self.BAD), audit)
            self._rejected(exc2, field_error)
        self._in_txn(body)


# =============================================================================
# THE DATABASE CHECKS
# =============================================================================
class TestDatabaseNamespaceChecks:
    def test_constraints_exist_and_are_still_not_valid(self):
        """The migration must create them NOT VALID and must NEVER validate them (that is a separate,
        manual step after production is audited)."""
        async def _run():
            async with SessionLocal() as db:
                rows = (await db.execute(text(
                    "SELECT conname, convalidated FROM pg_constraint WHERE conrelid = 'public.users'::regclass "
                    "AND conname IN ('ck_users_email_has_at','ck_users_phone_no_at')"))).all()
                return dict(rows)
        state = run(_run())
        assert state == {"ck_users_email_has_at": False, "ck_users_phone_no_at": False}

    def test_a_phone_containing_at_is_rejected_on_insert_and_on_update(self):
        async def _run():
            async with SessionLocal() as db:
                with pytest.raises(IntegrityError) as ins:
                    await _make_user(db, phone="someone@example.com")
                assert "ck_users_phone_no_at" in str(ins.value)
                await db.rollback()
            async with SessionLocal() as db:
                u = await _make_user(db)
                with pytest.raises(IntegrityError) as upd:
                    await db.execute(text("UPDATE users SET phone = 'x@y.com' WHERE id = :id"), {"id": u.id})
                assert "ck_users_phone_no_at" in str(upd.value)
                await db.rollback()
        run(_run())

    def test_an_email_without_at_is_rejected(self):
        async def _run():
            async with SessionLocal() as db:
                with pytest.raises(IntegrityError) as exc:
                    await _make_user(db, email="not-an-email")
                assert "ck_users_email_has_at" in str(exc.value)
                await db.rollback()
        run(_run())

    def test_valid_existing_style_rows_are_accepted(self):
        async def _run():
            async with SessionLocal() as db:
                try:
                    for style in VALID_PHONES[:7]:
                        await _make_user(db, phone=_fresh(style))
                finally:
                    await db.rollback()
        run(_run())

    def test_not_valid_still_blocks_updates_of_an_already_violating_row(self):
        """The operational warning in the migration, proven: a NOT VALID CHECK is applied to every UPDATE of a
        row - even one that touches neither column (a heartbeat's last_seen_at) - so a violating row must be
        corrected before deploying. (Recreated in a transaction that is rolled back.)"""
        async def _run():
            async with SessionLocal() as db:
                try:
                    await db.execute(text("ALTER TABLE users DROP CONSTRAINT ck_users_phone_no_at"))
                    u = await _make_user(db, phone="legacy@example.com")  # a row that violates the future rule
                    # users.company_id is a DEFERRABLE FK: the insert queued trigger events, and PostgreSQL refuses
                    # ALTER TABLE while any are pending - fire them first.
                    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                    await db.execute(text("ALTER TABLE users ADD CONSTRAINT ck_users_phone_no_at CHECK (position('@' in phone) = 0) NOT VALID"))
                    with pytest.raises(IntegrityError) as exc:
                        await db.execute(text("UPDATE users SET last_seen_at = now() WHERE id = :id"), {"id": u.id})
                    assert "ck_users_phone_no_at" in str(exc.value)
                finally:
                    await db.rollback()
        run(_run())

    def test_model_metadata_mirrors_the_constraints(self):
        names = {c.name for c in User.__table__.constraints}
        assert {"ck_users_email_has_at", "ck_users_phone_no_at"} <= names
