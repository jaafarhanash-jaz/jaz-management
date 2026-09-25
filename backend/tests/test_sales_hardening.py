"""JAZ Sales - pre-Phase-2 hardening.

  A. STAFF CONTEXT   - StaffContext.user carries only an allowlist of fields; the password hash from the core user
                       dict never reaches it (nor its repr).
  B. REVOCATION CHECK- ck_staff_user_roles_revoked_pair: a revocation always records who did it.
  C. AUDIT TRAIL     - every staff account / role change writes its staff_audit_events row(s): the right event,
                       the right allowlisted payload, one correlation id per request, no false events for no-ops,
                       no secrets, nothing for failed requests.
  D. APPEND-ONLY     - UPDATE / DELETE / TRUNCATE on the trail are rejected by the database.
  E. ATOMICITY       - the audit row and the change it records commit or roll back together: if the audit insert
                       fails, the change does not happen (proven end-to-end over HTTP with a database-side probe, and
                       in-process through the real get_db dependency).

Scratch database + scratch backend only (see sales_test_utils.assert_scratch_target). Schema-changing helpers used by
the atomicity probe are always removed in a finally block.
"""
from sales_test_utils import assert_scratch_target

assert_scratch_target(need_http=False)  # must run BEFORE importing database (which loads .env)

import asyncio
import json
import random
import uuid
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

import services.auth as auth_service
from database import SessionLocal, engine, get_db
from models import User
from sales.models import StaffAuditEvent, StaffRole, StaffUserRole
from sales.repositories import staff as staff_repo
from sales.schemas import StaffCreate, StaffUpdate
from sales.services import audit as audit_service
from sales.services import team as team_service
from sales.services.access import resolve_staff_context
from sales_test_utils import STAFF_PASSWORD, api, auth, create_staff, lookup_user, mint_token, unique_email, unique_phone
# token fixtures that mint instead of logging in (see sales_test_utils) - override conftest's for this module
from sales_test_utils import admin_token  # noqa: F401

CONTEXT_FIELDS = {"id", "name", "email", "phone", "role", "status"}
SENTINEL_HASH = "$2b$12$SENTINEL.HASH.MUST.NEVER.APPEAR.IN.A.CONTEXT.OR.ITS.REPR.xxxxxxxxxxxxxx"


def run(coro):
    async def _wrapped():
        try:
            return await coro
        finally:
            await engine.dispose()
    return asyncio.run(_wrapped())


@pytest.fixture(scope="module", autouse=True)
def _scratch_guard():
    assert_scratch_target()


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return auth(admin_token)


@pytest.fixture(scope="module")
def admin_id():
    return lookup_user("admin@jaz.com")["id"]


# ---- database helpers ---------------------------------------------------------------------------------------
def _event_dict(e: StaffAuditEvent) -> dict:
    return {
        "seq": e.seq, "action": e.action, "module": e.module, "outcome": e.outcome, "actor_type": e.actor_type,
        "actor_user_id": str(e.actor_user_id) if e.actor_user_id else None, "actor_platform_role": e.actor_platform_role,
        "target_type": e.target_type, "target_id": str(e.target_id) if e.target_id else None,
        "target_user_id": str(e.target_user_id) if e.target_user_id else None,
        "before": e.before_data, "after": e.after_data, "reason": e.reason, "metadata": e.event_metadata,
        "correlation_id": str(e.correlation_id), "ip_address": e.ip_address, "user_agent": e.user_agent, "id": str(e.id),
    }


async def _events(*conditions):
    async with SessionLocal() as db:
        rows = (await db.execute(select(StaffAuditEvent).where(*conditions).order_by(StaffAuditEvent.seq))).scalars().all()
        return [_event_dict(e) for e in rows]


def events_for(user_id: str) -> list:
    return run(_events(StaffAuditEvent.target_user_id == uuid.UUID(user_id)))


async def _scalar(sql, params):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), params)).scalar_one_or_none()


def db_scalar(sql, **params):
    return run(_scalar(sql, params))


def grant_rows(user_id: str) -> list:
    async def _q():
        async with SessionLocal() as db:
            rows = (await db.execute(select(StaffUserRole, StaffRole.key).join(StaffRole, StaffRole.id == StaffUserRole.role_id)
                                     .where(StaffUserRole.user_id == uuid.UUID(user_id)).order_by(StaffUserRole.granted_at))).all()
            return [{"id": str(g.id), "role_key": key, "granted_by": str(g.granted_by), "revoked_at": g.revoked_at,
                     "revoked_by": str(g.revoked_by) if g.revoked_by else None} for g, key in rows]
    return run(_q())


def new_staff(admin_h, roles=(), label="hard", **kw):
    return create_staff(admin_h, list(roles), label, do_login=kw.pop("do_login", False), **kw)


# =============================================================================
# A. STAFF CONTEXT: no password hash
# =============================================================================
class TestStaffContextCarriesNoPasswordHash:
    async def _ctx(self, user_id):
        """The REAL flow: token -> core get_current_user (whose dict includes the hash) -> resolve_staff_context."""
        async with SessionLocal() as db:
            core_user = await auth_service.get_current_user(db, mint_token(user_id))
            return core_user, await resolve_staff_context(db, core_user)

    def test_super_admin_context_has_no_password(self, admin_id):
        core_user, ctx = run(self._ctx(admin_id))
        assert core_user["password"], "precondition: the core dict really does carry the hash"
        assert ctx.is_super_admin and "password" not in ctx.user
        assert set(ctx.user) == CONTEXT_FIELDS
        assert core_user["password"] not in repr(ctx) and "password" not in repr(ctx)

    def test_jaz_staff_context_has_no_password(self, admin_h):
        staff = new_staff(admin_h, ["sales_manager"], "ctx")
        core_user, ctx = run(self._ctx(staff["id"]))
        assert core_user["password"] and not ctx.is_super_admin
        assert "password" not in ctx.user and set(ctx.user) == CONTEXT_FIELDS
        assert core_user["password"] not in repr(ctx) and "password" not in repr(ctx)
        assert ctx.has("sales.access", "sales.team.view")  # permissions still resolve

    def test_expected_fields_remain_available_with_the_right_values(self, admin_h):
        staff = new_staff(admin_h, ["sales_employee"], "ctxvals")
        core_user, ctx = run(self._ctx(staff["id"]))
        for field in CONTEXT_FIELDS:
            assert ctx.user[field] == core_user[field]
        assert ctx.user["email"] == staff["email"] and ctx.user["role"] == "jaz_staff" and ctx.user["status"] == "active"
        assert ctx.user_id == uuid.UUID(staff["id"]) and [r["key"] for r in ctx.roles] == ["sales_employee"]

    def test_it_is_an_allowlist_not_a_denylist(self):
        """A secret field nobody thought of yet must not flow in either."""
        async def _run():
            async with SessionLocal() as db:
                dirty = {"id": str(uuid.uuid4()), "role": "super_admin", "status": "active", "name": "n", "email": "e@x.co",
                         "phone": "1", "password": SENTINEL_HASH, "totp_secret": "S3CR3T", "refresh_token": "RT", "hash": "H"}
                return await resolve_staff_context(db, dirty)
        ctx = run(_run())
        assert set(ctx.user) == CONTEXT_FIELDS
        for leaked in (SENTINEL_HASH, "S3CR3T", "RT"):
            assert leaked not in repr(ctx)

    def test_partial_user_dicts_still_work(self):
        async def _run():
            async with SessionLocal() as db:
                return await resolve_staff_context(db, {"id": str(uuid.uuid4()), "role": "super_admin"})
        ctx = run(_run())
        assert ctx.user["id"] and ctx.user["role"] == "super_admin" and ctx.user["email"] is None

    def test_me_response_shape_is_unchanged(self, admin_h):
        body = api("GET", "/api/sales/me", admin_h).json()
        assert set(body) == {"user", "is_super_admin", "roles", "permissions", "modules"}
        assert set(body["user"]) == CONTEXT_FIELDS
        assert "password" not in json.dumps(body).lower()


# =============================================================================
# B. REVOCATION INTEGRITY CHECK
# =============================================================================
async def _new_user(db, status="active"):
    user = User(id=uuid.uuid4(), email=f"rev-{uuid.uuid4().hex[:10]}@example.com", phone=f"+1666{random.randint(1000000, 9999999)}",
                password="not-a-real-hash", name="Revocation test", role="jaz_staff", status=status)
    db.add(user)
    await db.flush()
    return user


async def _role(db, key="sales_employee"):
    return (await db.execute(select(StaffRole).where(StaffRole.key == key))).scalar_one()


async def _purge_users(user_ids):
    async with SessionLocal() as db:
        await db.execute(delete(StaffUserRole).where(StaffUserRole.user_id.in_(user_ids)))
        await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()


class TestRevocationPairCheck:
    def _with_grant(self, body):
        async def _run():
            uid = None
            try:
                async with SessionLocal() as db:
                    user = await _new_user(db); uid = user.id
                    role = await _role(db)
                    await staff_repo.grant_role(db, user.id, role.id, user.id)
                    await body(db, user)
            finally:
                if uid:
                    await _purge_users([uid])
        run(_run())

    def test_both_null_is_an_active_grant(self):
        async def body(db, user):
            row = (await db.execute(select(StaffUserRole).where(StaffUserRole.user_id == user.id))).scalar_one()
            assert row.revoked_at is None and row.revoked_by is None
        self._with_grant(body)

    def test_both_set_is_a_revoked_grant(self):
        async def body(db, user):
            role = await _role(db)
            assert await staff_repo.revoke_role(db, user.id, role.id, user.id) is True
            row = (await db.execute(select(StaffUserRole).where(StaffUserRole.user_id == user.id))).scalar_one()
            assert row.revoked_at is not None and row.revoked_by == user.id
        self._with_grant(body)

    def test_revoked_at_alone_is_rejected(self):
        async def body(db, user):
            with pytest.raises(IntegrityError) as exc:
                await db.execute(update(StaffUserRole).where(StaffUserRole.user_id == user.id).values(revoked_at=text("granted_at + interval '1 second'")))
            assert "ck_staff_user_roles_revoked_pair" in str(exc.value)
            await db.rollback()
        self._with_grant(body)

    def test_revoked_by_alone_is_rejected(self):
        async def body(db, user):
            with pytest.raises(IntegrityError) as exc:
                await db.execute(update(StaffUserRole).where(StaffUserRole.user_id == user.id).values(revoked_by=user.id))
            assert "ck_staff_user_roles_revoked_pair" in str(exc.value)
            await db.rollback()
        self._with_grant(body)

    def test_revoked_before_granted_is_still_rejected_by_the_original_check(self):
        """Both columns set, so ONLY the timestamp rule is violated - each test violates exactly one constraint."""
        async def body(db, user):
            with pytest.raises(IntegrityError) as exc:
                await db.execute(update(StaffUserRole).where(StaffUserRole.user_id == user.id)
                                 .values(revoked_at=text("granted_at - interval '1 day'"), revoked_by=user.id))
            assert "ck_staff_user_roles_revoked_after_granted" in str(exc.value)
            assert "revoked_pair" not in str(exc.value)
            await db.rollback()
        self._with_grant(body)

    def test_model_metadata_mirrors_the_database_constraint(self):
        assert "ck_staff_user_roles_revoked_pair" in {c.name for c in StaffUserRole.__table__.constraints}
        definition = db_scalar("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_staff_user_roles_revoked_pair'")
        assert definition and "revoked_at IS NULL" in definition and "revoked_by IS NOT NULL" in definition

    def test_the_repository_writes_both_columns_together(self):
        """The only production writer of revoked_*: it can never produce the half-set state the CHECK forbids."""
        async def body(db, user):
            role = await _role(db)
            assert await staff_repo.revoke_role(db, user.id, role.id, user.id) is True
            assert await staff_repo.revoke_role(db, user.id, role.id, user.id) is False   # idempotent, still consistent
            assert await staff_repo.grant_role(db, user.id, role.id, user.id) is True      # re-grant after revoke
        self._with_grant(body)


# =============================================================================
# C. AUDIT TRAIL - through the real API, read back from the database
# =============================================================================
class TestAuditEvents:
    def test_create_staff_emits_created_plus_one_grant_event_per_role_with_one_correlation_id(self, admin_h, admin_id):
        ua = f"hardening-test/{uuid.uuid4().hex[:8]}"
        payload = {"name": "Audit Create", "email": unique_email("audit-c"), "phone": unique_phone(), "password": STAFF_PASSWORD,
                   "role_keys": ["sales_manager", "sales_employee"]}
        r = api("POST", "/api/sales/team", {**admin_h, "User-Agent": ua}, payload)
        assert r.status_code == 201, r.text
        sid = r.json()["id"]

        events = events_for(sid)
        assert [e["action"] for e in events] == ["staff_created", "staff_role_granted", "staff_role_granted"]
        assert len({e["correlation_id"] for e in events}) == 1

        created = events[0]
        assert (created["module"], created["outcome"], created["actor_type"]) == ("sales", "success", "user")
        assert created["actor_user_id"] == admin_id and created["actor_platform_role"] == "super_admin"
        assert (created["target_type"], created["target_id"], created["target_user_id"]) == ("staff_user", sid, sid)
        assert created["before"] is None
        assert created["after"] == {"name": "Audit Create", "email": payload["email"], "phone": payload["phone"], "status": "active"}
        assert created["metadata"] == {"actor_roles": [], "initial_role_keys": ["sales_manager", "sales_employee"]}
        assert created["ip_address"] is None and created["user_agent"] == ua

        grants = {g["role_key"]: g for g in grant_rows(sid)}
        assert set(grants) == {"sales_manager", "sales_employee"}
        for event in events[1:]:
            role_key = event["after"]["role_key"]
            assert event["target_type"] == "staff_role_grant" and event["target_user_id"] == sid
            assert event["before"] is None and event["target_id"] == grants[role_key]["id"]
            assert event["metadata"]["grant_id"] == grants[role_key]["id"] and event["metadata"]["actor_roles"] == []
            assert event["actor_user_id"] == admin_id and event["user_agent"] == ua

    def test_create_staff_without_roles_emits_only_the_created_event(self, admin_h):
        sid = new_staff(admin_h, [], "audit-nr")["id"]
        assert [e["action"] for e in events_for(sid)] == ["staff_created"]

    def test_update_emits_staff_updated_with_only_the_fields_that_changed(self, admin_h, admin_id):
        s = new_staff(admin_h, [], "audit-u")
        new_email = unique_email("audit-u2")
        r = api("PATCH", f"/api/sales/team/{s['id']}", admin_h, {"name": "Renamed", "email": new_email, "phone": s["phone"]})  # phone unchanged
        assert r.status_code == 200, r.text
        events = events_for(s["id"])
        assert [e["action"] for e in events] == ["staff_created", "staff_updated"]
        updated = events[1]
        assert updated["before"] == {"name": "Test audit-u", "email": s["email"]}
        assert updated["after"] == {"name": "Renamed", "email": new_email}
        assert updated["metadata"] == {"actor_roles": [], "fields": ["email", "name"]}
        assert updated["actor_user_id"] == admin_id and updated["target_id"] == s["id"]

    def test_status_changes_are_their_own_events(self, admin_h):
        s = new_staff(admin_h, [], "audit-st")
        assert api("PATCH", f"/api/sales/team/{s['id']}", admin_h, {"status": "inactive"}).status_code == 200
        assert api("PATCH", f"/api/sales/team/{s['id']}", admin_h, {"status": "inactive"}).status_code == 200   # already inactive: no event
        assert api("PATCH", f"/api/sales/team/{s['id']}", admin_h, {"status": "active"}).status_code == 200
        assert api("PATCH", f"/api/sales/team/{s['id']}", admin_h, {"status": "active"}).status_code == 200     # already active: no event
        events = events_for(s["id"])
        assert [e["action"] for e in events] == ["staff_created", "staff_deactivated", "staff_reactivated"]
        deactivated, reactivated = events[1], events[2]
        assert deactivated["before"] == {"status": "active"} and deactivated["after"] == {"status": "inactive"}
        assert deactivated["metadata"] == {"actor_roles": [], "refresh_tokens_revoked": True}
        assert reactivated["before"] == {"status": "inactive"} and reactivated["after"] == {"status": "active"}
        assert "refresh_tokens_revoked" not in reactivated["metadata"]

    def test_profile_and_status_in_one_request_emit_two_events_under_one_correlation_id(self, admin_h):
        s = new_staff(admin_h, [], "audit-both")
        before = events_for(s["id"])
        r = api("PATCH", f"/api/sales/team/{s['id']}", admin_h, {"name": "Both Changed", "status": "inactive"})
        assert r.status_code == 200, r.text
        new = events_for(s["id"])[len(before):]
        assert [e["action"] for e in new] == ["staff_updated", "staff_deactivated"]
        assert len({e["correlation_id"] for e in new}) == 1 and new[0]["correlation_id"] != before[0]["correlation_id"]
        assert new[0]["after"] == {"name": "Both Changed"}  # status is not in the profile event

    def test_a_patch_that_changes_nothing_emits_nothing(self, admin_h):
        s = new_staff(admin_h, [], "audit-noop")
        r = api("PATCH", f"/api/sales/team/{s['id']}", admin_h, {"name": "Test audit-noop", "email": s["email"], "phone": s["phone"], "status": "active"})
        assert r.status_code == 200
        assert [e["action"] for e in events_for(s["id"])] == ["staff_created"]

    def test_password_reset_event_carries_no_payload_at_all(self, admin_h, admin_id):
        s = new_staff(admin_h, [], "audit-pw")
        r = api("POST", f"/api/sales/team/{s['id']}/reset-password", admin_h, {"new_password": "Reset#98765Xy"})
        assert r.status_code == 200, r.text
        event = events_for(s["id"])[-1]
        assert event["action"] == "staff_password_reset"
        assert event["before"] is None and event["after"] is None and event["reason"] is None
        assert event["metadata"] == {"actor_roles": [], "refresh_tokens_revoked": True}
        assert (event["target_type"], event["target_id"], event["target_user_id"]) == ("staff_user", s["id"], s["id"])
        assert event["actor_user_id"] == admin_id

    def test_role_grant_and_revoke_events_and_their_no_ops(self, admin_h, admin_id):
        s = new_staff(admin_h, [], "audit-role")
        base = len(events_for(s["id"]))
        url = f"/api/sales/team/{s['id']}/roles"

        assert api("POST", url, admin_h, {"role_key": "sales_manager"}).json()["changed"] is True
        assert api("POST", url, admin_h, {"role_key": "sales_manager"}).json()["changed"] is False   # no-op: no event
        first_grant = grant_rows(s["id"])[0]
        assert api("DELETE", f"{url}/sales_manager", admin_h).json()["changed"] is True
        assert api("DELETE", f"{url}/sales_manager", admin_h).json()["changed"] is False            # no-op: no event
        assert api("POST", url, admin_h, {"role_key": "sales_manager"}).json()["changed"] is True   # re-grant: a NEW grant row

        new = events_for(s["id"])[base:]
        assert [e["action"] for e in new] == ["staff_role_granted", "staff_role_revoked", "staff_role_granted"]
        granted, revoked, regranted = new
        assert granted["target_id"] == revoked["target_id"] == first_grant["id"]                    # the revoke names the grant it revoked
        assert regranted["target_id"] != first_grant["id"]
        assert granted["before"] is None and granted["after"] == {"role_key": "sales_manager"}
        assert revoked["before"] == {"role_key": "sales_manager"} and revoked["after"] is None
        assert revoked["metadata"]["grant_id"] == first_grant["id"] and revoked["metadata"]["role_id"] == granted["metadata"]["role_id"]
        assert all(e["actor_user_id"] == admin_id and e["target_user_id"] == s["id"] for e in new)
        assert len({e["correlation_id"] for e in new}) == 3                                          # three requests, three correlation ids
        rows = grant_rows(s["id"])
        assert rows[0]["revoked_by"] == admin_id and rows[0]["revoked_at"] is not None              # and the revocation row agrees

    def test_failed_requests_write_no_event(self, admin_h):
        s = new_staff(admin_h, [], "audit-fail")
        phantom = "00000000-0000-4000-8000-0000000000aa"
        dup = api("POST", "/api/sales/team", admin_h, {"name": "Dup", "email": s["email"], "phone": unique_phone(), "password": STAFF_PASSWORD, "role_keys": []})
        bad_role = api("POST", "/api/sales/team", admin_h, {"name": "X", "email": unique_email("audit-x"), "phone": unique_phone(), "password": STAFF_PASSWORD, "role_keys": ["no_such_role"]})
        weak_pw = api("POST", f"/api/sales/team/{s['id']}/reset-password", admin_h, {"new_password": "abc"})
        missing = api("PATCH", f"/api/sales/team/{phantom}", admin_h, {"name": "x"})
        unknown_role = api("POST", f"/api/sales/team/{s['id']}/roles", admin_h, {"role_key": "no_such_role"})
        assert (dup.status_code, bad_role.status_code, weak_pw.status_code, missing.status_code, unknown_role.status_code) == (400, 400, 400, 404, 400)
        assert [e["action"] for e in events_for(s["id"])] == ["staff_created"]                     # only the original creation
        assert db_scalar("SELECT count(*) FROM staff_audit_events WHERE target_user_id = :u", u=uuid.UUID(phantom)) == 0

    def test_correlation_ids_are_per_request(self, admin_h):
        a, b = new_staff(admin_h, [], "audit-corr1"), new_staff(admin_h, [], "audit-corr2")
        assert events_for(a["id"])[0]["correlation_id"] != events_for(b["id"])[0]["correlation_id"]

    def test_no_secret_reaches_the_trail(self, admin_h):
        """Full lifecycle with known passwords and real tokens; then every audit row of the account is searched."""
        s = new_staff(admin_h, ["sales_employee"], "audit-secret", do_login=True, real_login=True)
        first_password, second_password = STAFF_PASSWORD, "Reset#98765Xy"
        tokens = [s["token"], s["refresh_token"]]
        sid = s["id"]
        assert api("POST", f"/api/sales/team/{sid}/reset-password", admin_h, {"new_password": second_password}).status_code == 200
        assert api("PATCH", f"/api/sales/team/{sid}", admin_h, {"name": "Secret Test", "status": "inactive"}).status_code == 200
        assert api("PATCH", f"/api/sales/team/{sid}", admin_h, {"status": "active"}).status_code == 200
        assert api("POST", f"/api/sales/team/{sid}/roles", admin_h, {"role_key": "sales_manager"}).status_code == 200
        assert api("DELETE", f"/api/sales/team/{sid}/roles/sales_manager", admin_h).status_code == 200

        events = events_for(sid)
        assert len(events) >= 7
        blob = json.dumps(events, default=str)
        for secret in (first_password, second_password, *tokens, "$2b$", "$2a$", "$2y$"):
            assert secret not in blob, f"secret material found in the audit trail: {secret[:12]}..."

        def keys(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield str(k).lower(); yield from keys(v)
            elif isinstance(obj, list):
                for i in obj:
                    yield from keys(i)
        assert not (set(keys(events)) & audit_service._SECRET_KEYS), "a secret-looking key is stored in the trail"

    def test_the_writer_refuses_secret_looking_keys(self):
        ctx = SimpleNamespace(user_id=uuid.uuid4(), user={"role": "super_admin"}, roles=())
        audit = audit_service.new_audit_context("t")

        async def _try(**kw):
            async with SessionLocal() as db:
                await audit_service.record(db, ctx, audit, action="staff_updated", target_type="staff_user", **kw)
        for kw in ({"before": {"password": "x"}}, {"after": {"nested": {"Password_Hash": "x"}}}, {"metadata": {"list": [{"refresh_token": "t"}]}},
                   {"metadata": {"access_token": "t"}}, {"after": {"secret": "s"}}):
            with pytest.raises(ValueError, match="secret-looking key"):
                run(_try(**kw))
        assert "refresh_tokens_revoked" not in audit_service._SECRET_KEYS  # the legitimate metadata key is not blocked

    def test_user_agent_handling_and_ip_is_never_recorded(self, admin_h):
        assert audit_service.new_audit_context(None).user_agent is None
        assert audit_service.new_audit_context("").user_agent is None
        assert audit_service.new_audit_context("a\x00b").user_agent == "ab"
        assert len(audit_service.new_audit_context("u" * 5000).user_agent) == 512
        assert audit_service.new_audit_context("x").correlation_id != audit_service.new_audit_context("x").correlation_id

        long_ua = "long-agent/" + "u" * 2000
        r = api("POST", "/api/sales/team", {**admin_h, "User-Agent": long_ua},
                {"name": "UA", "email": unique_email("audit-ua"), "phone": unique_phone(), "password": STAFF_PASSWORD, "role_keys": []})
        assert r.status_code == 201
        events = events_for(r.json()["id"])
        assert events[0]["user_agent"] == long_ua[:512]
        assert all(e["ip_address"] is None for e in events)   # never populated by the app (scoped to this test's own events)

    def test_model_matches_the_migration(self):
        async def _run():
            async with SessionLocal() as db:
                cols = {r[0] for r in (await db.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'staff_audit_events'"))).all()}
                checks = {r[0] for r in (await db.execute(text("SELECT conname FROM pg_constraint WHERE conrelid = 'public.staff_audit_events'::regclass AND contype = 'c'"))).all()}
                idx = {r[0] for r in (await db.execute(text("SELECT indexname FROM pg_indexes WHERE tablename = 'staff_audit_events'"))).all()}
                return cols, checks, idx
        cols, checks, idx = run(_run())
        table = StaffAuditEvent.__table__
        assert cols == {c.name for c in table.columns}
        assert checks == {c.name for c in table.constraints if c.__class__.__name__ == "CheckConstraint"}
        assert idx == {i.name for i in table.indexes} | {"staff_audit_events_pkey", "uq_staff_audit_events_seq"}


# =============================================================================
# D. APPEND-ONLY
# =============================================================================
@pytest.fixture(scope="module")
def event(admin_h):
    """One real audit row (the creation of a throwaway staff account) for the append-only attempts."""
    s = new_staff(admin_h, [], "append-only")
    return events_for(s["id"])[0]


class TestAuditTrailIsAppendOnly:
    def _attempt(self, event, sql):
        async def _run():
            async with SessionLocal() as db:
                with pytest.raises(DBAPIError) as exc:
                    await db.execute(text(sql), {"id": uuid.UUID(event["id"])})
                assert "append-only" in str(exc.value)
                await db.rollback()
        run(_run())
        # the row is exactly as it was
        again = run(_events(StaffAuditEvent.id == uuid.UUID(event["id"])))
        assert len(again) == 1 and again[0] == event

    def test_update_is_rejected(self, event):
        self._attempt(event, "UPDATE staff_audit_events SET action = 'tampered' WHERE id = :id")

    def test_update_of_any_column_is_rejected_even_a_reason(self, event):
        self._attempt(event, "UPDATE staff_audit_events SET reason = 'edited' WHERE id = :id")

    def test_delete_is_rejected(self, event):
        self._attempt(event, "DELETE FROM staff_audit_events WHERE id = :id")

    def test_truncate_is_rejected(self, event):
        async def _run():
            async with SessionLocal() as db:
                with pytest.raises(DBAPIError) as exc:
                    await db.execute(text("TRUNCATE staff_audit_events"))
                assert "append-only" in str(exc.value)
                await db.rollback()
        run(_run())
        assert len(run(_events(StaffAuditEvent.id == uuid.UUID(event["id"])))) == 1

    def test_inserts_still_work_and_get_increasing_seq(self, admin_h):
        a, b = new_staff(admin_h, [], "seq-a"), new_staff(admin_h, [], "seq-b")
        seq_a, seq_b = events_for(a["id"])[0]["seq"], events_for(b["id"])[0]["seq"]
        assert isinstance(seq_a, int) and seq_b > seq_a


# =============================================================================
# E. ATOMICITY
# =============================================================================
class TestAuditIsAtomicWithTheChange:
    def test_http_audit_insert_failure_rolls_back_the_change(self, admin_h):
        """End to end through the real route and get_db. A database-side probe (a CHECK that rejects audit rows
        carrying ONE unique User-Agent) makes the audit insert of exactly this test's requests fail; every other
        request - including other tests running in parallel - is unaffected. The probe is always removed."""
        target = new_staff(admin_h, ["sales_employee"], "atomic", do_login=True, real_login=True)
        sid = target["id"]
        original_hash = db_scalar("SELECT password FROM users WHERE id = :id", id=uuid.UUID(sid))
        probe, cname = f"atomicity-probe-{uuid.uuid4().hex}", f"ck_probe_{uuid.uuid4().hex[:8]}"
        probe_headers = {**admin_h, "User-Agent": probe}
        new_email = unique_email("atomic-new")

        async def ddl(sql):
            async with SessionLocal() as db:
                await db.execute(text(sql)); await db.commit()
        run(ddl(f"ALTER TABLE staff_audit_events ADD CONSTRAINT {cname} CHECK (user_agent IS DISTINCT FROM '{probe}') NOT VALID"))
        try:
            base = f"/api/sales/team/{sid}"
            responses = {
                "create": api("POST", "/api/sales/team", probe_headers, {"name": "Atomic New", "email": new_email, "phone": unique_phone(), "password": STAFF_PASSWORD, "role_keys": ["sales_employee"]}),
                "update": api("PATCH", base, probe_headers, {"name": "Should Not Stick", "status": "inactive"}),
                "reset": api("POST", f"{base}/reset-password", probe_headers, {"new_password": "Reset#98765Xy"}),
                "grant": api("POST", f"{base}/roles", probe_headers, {"role_key": "sales_manager"}),
                "revoke": api("DELETE", f"{base}/roles/sales_employee", probe_headers),
            }
            assert {k: v.status_code for k, v in responses.items()} == {k: 500 for k in responses}, {k: v.status_code for k, v in responses.items()}
        finally:
            run(ddl(f"ALTER TABLE staff_audit_events DROP CONSTRAINT IF EXISTS {cname}"))

        # NOTHING of any of the five changes persisted
        assert db_scalar("SELECT count(*) FROM users WHERE email = :e", e=new_email) == 0
        assert db_scalar("SELECT name FROM users WHERE id = :id", id=uuid.UUID(sid)) == "Test atomic"
        assert db_scalar("SELECT status FROM users WHERE id = :id", id=uuid.UUID(sid)) == "active"
        assert db_scalar("SELECT password FROM users WHERE id = :id", id=uuid.UUID(sid)) == original_hash
        assert db_scalar("SELECT count(*) FROM refresh_tokens WHERE user_id = :id AND revoked_at IS NULL", id=uuid.UUID(sid)) >= 1  # sessions NOT revoked
        grants = grant_rows(sid)
        assert [(g["role_key"], g["revoked_at"]) for g in grants] == [("sales_employee", None)]   # no manager grant; employee grant not revoked
        assert db_scalar("SELECT count(*) FROM staff_audit_events WHERE user_agent = :ua", ua=probe) == 0
        # the same operations succeed once the probe is gone (so the failures above were the probe, nothing else)
        assert api("PATCH", base, admin_h, {"name": "Now It Sticks"}).status_code == 200
        assert db_scalar("SELECT name FROM users WHERE id = :id", id=uuid.UUID(sid)) == "Now It Sticks"

    @staticmethod
    async def _in_request(fn):
        """Drive the REAL get_db dependency the way FastAPI does: commit on success, rollback + re-raise on error."""
        gen = get_db()
        db = await gen.__anext__()
        try:
            result = await fn(db)
        except BaseException as exc:
            await gen.athrow(exc)  # get_db rolls the session back and re-raises
            raise
        try:
            await gen.__anext__()  # commit
        except StopAsyncIteration:
            pass
        return result

    async def _admin_ctx(self, admin_id):
        async with SessionLocal() as db:
            return await resolve_staff_context(db, await auth_service.get_current_user(db, mint_token(admin_id)))

    @pytest.mark.parametrize("operation", ["create", "update", "reset", "grant", "revoke"])
    def test_a_failing_audit_writer_rolls_back_every_operation(self, admin_h, admin_id, operation, monkeypatch):
        s = new_staff(admin_h, ["sales_employee"], f"inproc-{operation}", do_login=True, real_login=True)
        sid, uid = s["id"], uuid.UUID(s["id"])
        original_hash = db_scalar("SELECT password FROM users WHERE id = :id", id=uid)
        new_email = unique_email("inproc-new")

        async def _run():
            ctx = await self._admin_ctx(admin_id)
            audit = audit_service.new_audit_context("inproc")

            async def broken_writer(*args, **kwargs):
                raise RuntimeError("audit store unavailable")
            monkeypatch.setattr(audit_service, "record", broken_writer)

            ops = {
                "create": lambda db: team_service.create_staff(db, ctx, StaffCreate(name="In Proc", email=new_email, phone=unique_phone(), password=STAFF_PASSWORD, role_keys=["sales_employee"]), audit),
                "update": lambda db: team_service.update_staff(db, ctx, sid, StaffUpdate(name="Should Not Stick", status="inactive"), audit),
                "reset": lambda db: team_service.reset_password(db, ctx, sid, SecretStr("Reset#98765Xy"), audit),
                "grant": lambda db: team_service.assign_role(db, ctx, sid, "sales_manager", audit),
                "revoke": lambda db: team_service.revoke_role(db, ctx, sid, "sales_employee", audit),
            }
            with pytest.raises(RuntimeError, match="audit store unavailable"):
                await self._in_request(ops[operation])
        run(_run())
        monkeypatch.undo()

        assert db_scalar("SELECT count(*) FROM users WHERE email = :e", e=new_email) == 0
        assert db_scalar("SELECT name FROM users WHERE id = :id", id=uid) == f"Test inproc-{operation}"
        assert db_scalar("SELECT status FROM users WHERE id = :id", id=uid) == "active"
        assert db_scalar("SELECT password FROM users WHERE id = :id", id=uid) == original_hash
        assert db_scalar("SELECT count(*) FROM refresh_tokens WHERE user_id = :id AND revoked_at IS NULL", id=uid) >= 1
        assert [(g["role_key"], g["revoked_at"]) for g in grant_rows(sid)] == [("sales_employee", None)]
        assert [e["action"] for e in events_for(sid)] == ["staff_created", "staff_role_granted"]   # only what setup wrote

    def test_the_same_operations_commit_together_with_their_events_when_the_writer_works(self, admin_h, admin_id):
        s = new_staff(admin_h, [], "inproc-ok")

        async def _run():
            ctx = await self._admin_ctx(admin_id)
            await self._in_request(lambda db: team_service.update_staff(db, ctx, s["id"], StaffUpdate(name="Committed"), audit_service.new_audit_context("t")))
        run(_run())
        assert db_scalar("SELECT name FROM users WHERE id = :id", id=uuid.UUID(s["id"])) == "Committed"
        assert [e["action"] for e in events_for(s["id"])] == ["staff_created", "staff_updated"]
