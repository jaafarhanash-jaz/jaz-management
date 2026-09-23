"""Direct (non-HTTP) tests for FCM delivery-count semantics.

The rest of this test suite runs live HTTP requests against a separately-
running backend process (see conftest.py) - that architecture can't mock
anything inside that other process, so it can't exercise real FCM
success/partial-failure/failure paths at all. These tests instead import
the backend's own async service functions directly and run them with
asyncio.run() against the same local Postgres database, with
firebase_admin.messaging mocked at the Python level (no real, local, or
production Firebase credentials used or required anywhere here).

Each test does all of its async work - setup, the call under test,
verification, cleanup - inside exactly one asyncio.run() call. Splitting
it across multiple asyncio.run() calls breaks: SQLAlchemy's async engine
pools real asyncpg connections that are bound to the event loop they were
opened under, and asyncio.run() tears that loop down when it returns -
reusing the pool from a second, later asyncio.run() call hands out a
connection tied to an already-closed loop and asyncpg raises "cannot
perform operation: another operation is in progress" (confirmed by
hitting exactly this while first writing these tests as separate
setup/act/cleanup asyncio.run() calls).
"""
import asyncio
import uuid
from unittest.mock import Mock, patch

from sqlalchemy import select

from database import SessionLocal, engine
from models import Company, User
import repositories.device_tokens as device_tokens_repo
import services.company_notifications as company_notifications_service
import services.push as push_service
from server import CompanyNotificationCreate, CompanyNotificationTargetConfig
from tests.conftest import BASE_URL


CONTENT = dict(title_ar="ت", title_en="t", message_ar="ر", message_en="m")


def _batch_response(results):
    """results: list where True=success, or an exception instance for a
    failed send (e.g. messaging.UnregisteredError-shaped for dead-token
    cleanup tests)."""
    return Mock(responses=[
        Mock(success=True, exception=None) if r is True else Mock(success=False, exception=r)
        for r in results
    ])


async def _get_user(db, email):
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one()


async def _admin_current_user_dict(db):
    admin = await _get_user(db, "admin@jaz.com")
    return {"id": str(admin.id), "name": admin.name}


def _create_body(recipient_mode, target_config=None, **overrides):
    kwargs = dict(recipient_mode=recipient_mode, target_config=target_config, **CONTENT)
    kwargs.update(overrides)
    return CompanyNotificationCreate(**kwargs)


def run(coro):
    """asyncio.run() creates and tears down a fresh event loop per call,
    but SQLAlchemy's async engine pools real asyncpg connections bound to
    the loop that opened them - reusing the pool from a later,
    independent asyncio.run() call (as happens across separate test
    functions in the same pytest process, all sharing database.engine)
    hands out a connection tied to an already-closed loop and asyncpg
    raises "another operation is in progress". Disposing the pool inside
    the same run, before the loop closes, avoids that - each test starts
    the next one with a clean pool instead of a poisoned one."""
    async def _wrapped():
        try:
            return await coro
        finally:
            await engine.dispose()
    return asyncio.run(_wrapped())


class TestDeliveryCountSemantics:
    def test_case1_no_device_token_reflects_reality_not_delivered(self, api_client, admin_headers):
        """A recipient with zero registered devices: the Notification row
        is still created (in-app history works), but nothing was ever
        attempted with FCM - must show as neither delivered nor failed,
        never silently counted as "delivered" just because a DB row
        exists (the exact misleading behavior the audit flagged)."""
        async def _run():
            async with SessionLocal() as db:
                owner = await _get_user(db, "owner@demo.com")
                company = await db.get(Company, owner.company_id)
                current_user = await _admin_current_user_dict(db)
                body = _create_body(
                    "custom",
                    CompanyNotificationTargetConfig(companies=[{"company_id": str(company.id), "mode": "managers"}]),
                )
                with patch.object(push_service, "_firebase_app", return_value=None):
                    result = await company_notifications_service.create_and_send(db, current_user, body)
                await db.commit()
                return result

        result = run(_run())
        assert result["recipient_count"] == 1
        assert result["notification_created_count"] == 1
        assert result["push_delivered_count"] == 0
        assert result["push_failed_count"] == 0
        assert result["status"] == "sent"

    def test_case2_one_valid_device_token_counts_as_push_delivered(self, api_client, admin_headers):
        token = f"test-push-{uuid.uuid4().hex}"

        async def _run():
            async with SessionLocal() as db:
                try:
                    employee = await _get_user(db, "employee1@demo.com")
                    await device_tokens_repo.upsert(db, user_id=employee.id, token=token, platform="android", app_version=None)
                    await db.flush()

                    current_user = await _admin_current_user_dict(db)
                    body = _create_body(
                        "custom",
                        CompanyNotificationTargetConfig(companies=[{"company_id": str(employee.company_id), "mode": "employees"}]),
                    )
                    fake_app = Mock()
                    with patch.object(push_service, "_firebase_app", return_value=fake_app), \
                         patch("services.push.messaging.send_each_for_multicast_async", return_value=_batch_response([True])):
                        result = await company_notifications_service.create_and_send(db, current_user, body)
                    return result
                finally:
                    await device_tokens_repo.delete_tokens(db, [token])
                    await db.commit()

        result = run(_run())
        assert result["push_delivered_count"] >= 1
        assert result["push_failed_count"] == 0

    def test_case3_multiple_devices_same_recipient_counted_once_not_per_device(self, api_client, admin_headers):
        """recipient_count and push_delivered_count both count unique
        recipient users, never devices - a user with 3 devices must
        contribute at most 1 to either, even though FCM genuinely sends
        to all 3 of their devices in one multicast call."""
        tokens = [f"test-push-{uuid.uuid4().hex}" for _ in range(3)]

        async def _run():
            async with SessionLocal() as db:
                try:
                    employee = await _get_user(db, "employee1@demo.com")
                    for t in tokens:
                        await device_tokens_repo.upsert(db, user_id=employee.id, token=t, platform="android", app_version=None)
                    await db.flush()

                    current_user = await _admin_current_user_dict(db)
                    body = _create_body(
                        "custom",
                        CompanyNotificationTargetConfig(companies=[{"company_id": str(employee.company_id), "mode": "employees"}]),
                    )
                    fake_app = Mock()
                    # All 3 of this recipient's devices succeed - the real
                    # per-device BatchResponse would have 3 entries, but
                    # the broadcast-level count must still show 1
                    # recipient, not 3.
                    with patch.object(push_service, "_firebase_app", return_value=fake_app), \
                         patch("services.push.messaging.send_each_for_multicast_async", return_value=_batch_response([True, True, True])):
                        result = await company_notifications_service.create_and_send(db, current_user, body)
                    return result
                finally:
                    await device_tokens_repo.delete_tokens(db, tokens)
                    await db.commit()

        result = run(_run())
        # Not asserting recipient_count == 1: this targets the demo
        # employee's whole company (mode="employees"), which has several
        # seeded employees, not just this one - the actual property under
        # test is that THIS recipient's 3 devices contribute exactly 1 to
        # push_delivered_count, not 3. Every other employee in the same
        # company has zero registered devices, so send_push_to_user's own
        # early-return (see services/push.py) means the mock is only ever
        # actually invoked once - for this recipient's 3-token call.
        assert result["push_delivered_count"] == 1
        assert result["push_failed_count"] == 0

    def test_case4_one_invalid_token_does_not_fail_other_recipients(self, api_client, admin_headers):
        """Two recipients, each with one device: one token FCM reports as
        dead, the other succeeds. The broadcast must not fail entirely,
        and the dead token must be cleaned up (reusing the pre-existing,
        untouched dead-token cleanup in services/push.py)."""
        from firebase_admin import messaging as real_messaging

        dead_token = f"test-push-dead-{uuid.uuid4().hex}"
        live_token = f"test-push-live-{uuid.uuid4().hex}"

        async def _run():
            async with SessionLocal() as db:
                try:
                    owner = await _get_user(db, "owner@demo.com")
                    employee = await _get_user(db, "employee1@demo.com")
                    await device_tokens_repo.upsert(db, user_id=owner.id, token=dead_token, platform="android", app_version=None)
                    await device_tokens_repo.upsert(db, user_id=employee.id, token=live_token, platform="android", app_version=None)
                    await db.flush()

                    current_user = await _admin_current_user_dict(db)
                    body = _create_body("everyone")
                    fake_app = Mock()

                    # Each recipient gets its own independent multicast
                    # call (send_push_to_user is called once per
                    # recipient) - the owner's call reports its one token
                    # dead, the employee's call reports success.
                    def _send_each(message, app=None):
                        if dead_token in message.tokens:
                            return _batch_response([real_messaging.UnregisteredError("gone")])
                        return _batch_response([True])

                    with patch.object(push_service, "_firebase_app", return_value=fake_app), \
                         patch("services.push.messaging.send_each_for_multicast_async", side_effect=_send_each):
                        result = await company_notifications_service.create_and_send(db, current_user, body)

                    remaining_owner_tokens = await device_tokens_repo.list_tokens_for_user(db, owner.id)
                    remaining_employee_tokens = await device_tokens_repo.list_tokens_for_user(db, employee.id)
                    return result, remaining_owner_tokens, remaining_employee_tokens
                finally:
                    await device_tokens_repo.delete_tokens(db, [dead_token, live_token])
                    await db.commit()

        result, remaining_owner_tokens, remaining_employee_tokens = run(_run())
        assert result["status"] in ("sent", "partial_failure")
        assert result["push_failed_count"] >= 1
        assert result["push_delivered_count"] >= 1
        assert dead_token not in remaining_owner_tokens, \
            "the dead token must be deleted by the pre-existing cleanup in services/push.py"
        assert live_token in remaining_employee_tokens


class TestExpiredCompanyExcluded:
    def test_expired_company_owner_is_excluded(self, api_client, admin_headers):
        """Complements test_company_notifications_remediation.py's
        soft-deleted/suspended cases - 'expired' has no direct admin
        endpoint to set it (renew/reactivate only ever move a company
        TOWARD active), so this is the one case that needs a direct DB
        write to construct at all, isolated to this file which already
        needs real DB access for the mocked-FCM tests above."""
        plans = api_client.get(f"{BASE_URL}/api/admin/subscription-plans", headers=admin_headers).json()
        suffix = uuid.uuid4().hex[:8]
        company = api_client.post(
            f"{BASE_URL}/api/admin/companies",
            json={
                "name": f"TEST_ExpiredCo_{suffix}",
                "owner_email": f"expired_owner_{suffix}@example.com",
                "owner_name": "Expired Test Owner",
                "owner_password": "TestPass123!",
                "owner_phone": f"+1555{suffix[:7]}",
                "subscription_plan_id": plans[0]["id"],
            },
            headers=admin_headers,
        )
        assert company.status_code == 200, company.text
        company_id = company.json()["id"]

        async def _expire():
            async with SessionLocal() as db:
                row = await db.get(Company, uuid.UUID(company_id))
                row.subscription_status = "expired"
                await db.commit()

        run(_expire())

        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications/preview",
            json={"recipient_mode": "custom", "target_config": {"companies": [{"company_id": company_id, "mode": "managers"}]}},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["recipient_count"] == 0
