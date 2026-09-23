"""Live HTTP integration tests for the Company Notifications remediation
pass (audit findings: invalid recipient_mode 500s, no company-status
filtering, no idempotency, no reliable device-token logout cleanup).
Same style as tests/test_company_notifications.py.
"""
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from tests.conftest import BASE_URL, auth_headers, _login


CONTENT = {
    "title_ar": "عنوان تجريبي",
    "title_en": "Test Title",
    "message_ar": "رسالة تجريبية",
    "message_en": "Test message",
}


def _body(mode, **overrides):
    body = {"recipient_mode": mode, "target_config": None, **CONTENT}
    body.update(overrides)
    return body


class _TestCompany:
    """Small bag of everything a test needs about a just-created company's
    owner - avoids every test re-deriving email/refresh_token by hand."""
    def __init__(self, company_id, owner_id, email, token, refresh_token):
        self.company_id = company_id
        self.owner_id = owner_id
        self.email = email
        self.token = token
        self.refresh_token = refresh_token
        self.headers = auth_headers(token)


def _create_test_company(api_client, admin_headers, name_prefix="TEST_RemediationCo") -> _TestCompany:
    plans = api_client.get(f"{BASE_URL}/api/admin/subscription-plans", headers=admin_headers).json()
    suffix = uuid.uuid4().hex[:8]
    email = f"remediation_owner_{suffix}@example.com"
    company = api_client.post(
        f"{BASE_URL}/api/admin/companies",
        json={
            "name": f"{name_prefix}_{suffix}",
            "owner_email": email,
            "owner_name": "Remediation Test Owner",
            "owner_password": "TestPass123!",
            "owner_phone": f"+1555{suffix[:7]}",
            "subscription_plan_id": plans[0]["id"],
        },
        headers=admin_headers,
    )
    assert company.status_code == 200, company.text
    data = company.json()
    login = _login(api_client, email, "TestPass123!")
    return _TestCompany(data["id"], data["owner_id"], email, login["token"], login["refresh_token"])


class TestRecipientModeValidation:
    @pytest.mark.parametrize("mode", ["typo", "", "ALL_MANAGERS", "all managers", "12345", "custom "])
    def test_invalid_recipient_mode_rejected_cleanly_on_preview(self, api_client, admin_headers, mode):
        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications/preview",
            json={"recipient_mode": mode, "target_config": None},
            headers=admin_headers,
        )
        assert r.status_code in (400, 422), r.text
        assert r.status_code != 500

    def test_null_recipient_mode_rejected_cleanly(self, api_client, admin_headers):
        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications/preview",
            json={"recipient_mode": None, "target_config": None},
            headers=admin_headers,
        )
        assert r.status_code in (400, 422), r.text

    def test_missing_recipient_mode_rejected_cleanly(self, api_client, admin_headers):
        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications/preview",
            json={"target_config": None},
            headers=admin_headers,
        )
        assert r.status_code in (400, 422), r.text

    def test_invalid_recipient_mode_rejected_on_send_too(self, api_client, admin_headers):
        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications",
            json=_body("not_a_real_mode"),
            headers=admin_headers,
        )
        assert r.status_code in (400, 422), r.text
        assert r.status_code != 500

    @pytest.mark.parametrize("target_config", [
        {"companies": [{"mode": "managers"}]},  # missing company_id
        {"companies": [{"company_id": "abc", "mode": "bogus_group"}]},  # invalid nested mode
        {"companies": []},  # empty list
        {"not_companies_key": []},  # wrong shape
        "not-an-object",  # wrong type entirely
    ])
    def test_malformed_custom_target_config_rejected_cleanly(self, api_client, admin_headers, target_config):
        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications/preview",
            json={"recipient_mode": "custom", "target_config": target_config},
            headers=admin_headers,
        )
        assert r.status_code in (400, 422), r.text
        assert r.status_code != 500

    def test_all_four_valid_modes_accepted(self, api_client, admin_headers):
        for mode in ["all_managers", "all_employees", "everyone"]:
            r = api_client.post(
                f"{BASE_URL}/api/admin/company-notifications/preview",
                json={"recipient_mode": mode, "target_config": None},
                headers=admin_headers,
            )
            assert r.status_code == 200, (mode, r.text)
        co = _create_test_company(api_client, admin_headers, "TEST_ValidModeCo")
        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications/preview",
            json={"recipient_mode": "custom", "target_config": {"companies": [{"company_id": co.company_id, "mode": "everyone"}]}},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text


class TestActiveCompanyFiltering:
    def test_active_company_owner_is_included(self, api_client, admin_headers):
        co = _create_test_company(api_client, admin_headers, "TEST_ActiveCo")
        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications/preview",
            json={"recipient_mode": "custom", "target_config": {"companies": [{"company_id": co.company_id, "mode": "managers"}]}},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["recipient_count"] == 1

    def test_soft_deleted_company_owner_is_excluded(self, api_client, admin_headers):
        co = _create_test_company(api_client, admin_headers, "TEST_DeletedCo")
        deleted = api_client.delete(f"{BASE_URL}/api/admin/companies/{co.company_id}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text
        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications/preview",
            json={"recipient_mode": "custom", "target_config": {"companies": [{"company_id": co.company_id, "mode": "managers"}]}},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["recipient_count"] == 0

    def test_suspended_company_owner_is_excluded(self, api_client, admin_headers):
        """Isolates the new Company-status filter specifically: suspend
        only sets Company.subscription_status, it does NOT touch the
        owner User row's deleted_at at all - so unlike the soft-delete
        case above, exclusion here can only come from the new filter,
        not the pre-existing User.deleted_at check."""
        co = _create_test_company(api_client, admin_headers, "TEST_SuspendedCo")
        suspended = api_client.post(f"{BASE_URL}/api/admin/companies/{co.company_id}/suspend", headers=admin_headers)
        assert suspended.status_code == 200, suspended.text

        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications/preview",
            json={"recipient_mode": "custom", "target_config": {"companies": [{"company_id": co.company_id, "mode": "managers"}]}},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["recipient_count"] == 0

        # And confirmed still absent from the platform-wide "all_managers"
        # count too, not just the custom-targeted one.
        all_managers_before = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications/preview",
            json={"recipient_mode": "all_managers", "target_config": None},
            headers=admin_headers,
        ).json()["recipient_count"]
        reactivated = api_client.post(f"{BASE_URL}/api/admin/companies/{co.company_id}/reactivate", headers=admin_headers)
        assert reactivated.status_code == 200, reactivated.text
        all_managers_after = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications/preview",
            json={"recipient_mode": "all_managers", "target_config": None},
            headers=admin_headers,
        ).json()["recipient_count"]
        assert all_managers_after == all_managers_before + 1


class TestIdempotency:
    def test_same_key_twice_creates_only_one_broadcast(self, api_client, admin_headers):
        key = f"test-idem-{uuid.uuid4().hex}"
        r1 = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications",
            json=_body("all_managers", idempotency_key=key),
            headers=admin_headers,
        )
        assert r1.status_code == 200, r1.text
        r2 = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications",
            json=_body("all_managers", idempotency_key=key),
            headers=admin_headers,
        )
        assert r2.status_code == 200, r2.text
        assert r1.json()["id"] == r2.json()["id"]

        history = api_client.get(f"{BASE_URL}/api/admin/company-notifications", headers=admin_headers).json()
        matching = [b for b in history if b["id"] == r1.json()["id"]]
        assert len(matching) == 1

    def test_different_keys_create_separate_broadcasts(self, api_client, admin_headers):
        r1 = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications",
            json=_body("all_managers", idempotency_key=f"test-idem-{uuid.uuid4().hex}"),
            headers=admin_headers,
        )
        r2 = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications",
            json=_body("all_managers", idempotency_key=f"test-idem-{uuid.uuid4().hex}"),
            headers=admin_headers,
        )
        assert r1.json()["id"] != r2.json()["id"]

    def test_no_key_means_no_protection_same_as_before(self, api_client, admin_headers):
        r1 = api_client.post(f"{BASE_URL}/api/admin/company-notifications", json=_body("all_managers"), headers=admin_headers)
        r2 = api_client.post(f"{BASE_URL}/api/admin/company-notifications", json=_body("all_managers"), headers=admin_headers)
        assert r1.json()["id"] != r2.json()["id"]

    def test_concurrent_requests_with_same_key_produce_one_broadcast_and_no_duplicate_recipient_rows(
        self, api_client, admin_headers, owner_headers, owner_user
    ):
        key = f"test-idem-concurrent-{uuid.uuid4().hex}"
        admin_token = admin_headers["Authorization"].split(" ")[1]

        def _fire():
            return requests.post(
                f"{BASE_URL}/api/admin/company-notifications",
                json=_body("all_managers", idempotency_key=key),
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            r1, r2 = list(pool.map(lambda _: _fire(), range(2)))

        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        assert r1.json()["id"] == r2.json()["id"]

        notifications = api_client.get(f"{BASE_URL}/api/notifications", headers=owner_headers).json()
        matching = [n for n in notifications if n.get("entity_id") == r1.json()["id"]]
        assert len(matching) == 1, "the targeted owner must receive exactly one notification row, not one per racing request"


class TestDeviceLogoutCleanup:
    def _register(self, api_client, headers, token, platform="android"):
        r = api_client.post(f"{BASE_URL}/api/devices", json={"token": token, "platform": platform}, headers=headers)
        assert r.status_code == 200, r.text
        return r.json()

    def _device_count(self, api_client, admin_headers, user_id):
        r = api_client.get(f"{BASE_URL}/api/admin/users/{user_id}/devices", headers=admin_headers)
        assert r.status_code == 200, r.text
        return len(r.json())

    def test_logout_removes_only_targeted_device_leaves_other_devices_and_users_untouched(self, api_client, admin_headers):
        # Two independent test companies so each owner is a distinct,
        # freshly-created user with no pre-existing device rows to
        # confuse the before/after counts.
        owner_a = _create_test_company(api_client, admin_headers, "TEST_LogoutA")
        owner_b = _create_test_company(api_client, admin_headers, "TEST_LogoutB")

        token_a1 = f"test-device-{uuid.uuid4().hex}"
        token_a2 = f"test-device-{uuid.uuid4().hex}"
        self._register(api_client, owner_a.headers, token_a1)
        self._register(api_client, owner_a.headers, token_a2)
        self._register(api_client, owner_b.headers, f"test-device-{uuid.uuid4().hex}")

        assert self._device_count(api_client, admin_headers, owner_a.owner_id) == 2
        assert self._device_count(api_client, admin_headers, owner_b.owner_id) == 1

        logout_resp = api_client.post(
            f"{BASE_URL}/api/auth/logout",
            json={"refresh_token": owner_a.refresh_token, "device_token": token_a1},
        )
        assert logout_resp.status_code == 200, logout_resp.text

        # Owner A: exactly one device left (token_a2, not token_a1).
        assert self._device_count(api_client, admin_headers, owner_a.owner_id) == 1
        # Owner B: completely untouched by owner A's logout.
        assert self._device_count(api_client, admin_headers, owner_b.owner_id) == 1

        # token_a1 is now free - a different user registering it takes
        # ownership cleanly (the exact upsert-by-token mechanism that
        # prevents cross-account leakage once the new user registers).
        owner_c = _create_test_company(api_client, admin_headers, "TEST_LogoutC")
        self._register(api_client, owner_c.headers, token_a1)
        assert self._device_count(api_client, admin_headers, owner_c.owner_id) == 1
        # Owner A still shows only 1 (token_a1's row moved to C, did not
        # duplicate or linger under A).
        assert self._device_count(api_client, admin_headers, owner_a.owner_id) == 1

    def test_logout_with_someone_elses_device_token_does_not_delete_it(self, api_client, admin_headers):
        """A malicious or buggy client claiming a device_token that
        actually belongs to a different user must not be able to delete
        it - delete_by_token_and_user is scoped by (token, user_id) from
        the refresh token being revoked, never by whatever the client
        claims."""
        owner_a = _create_test_company(api_client, admin_headers, "TEST_LogoutOwnedA")
        owner_b = _create_test_company(api_client, admin_headers, "TEST_LogoutOwnedB")
        b_token = f"test-device-{uuid.uuid4().hex}"
        self._register(api_client, owner_b.headers, b_token)
        assert self._device_count(api_client, admin_headers, owner_b.owner_id) == 1

        # Owner A logs out claiming B's device token.
        r = api_client.post(
            f"{BASE_URL}/api/auth/logout",
            json={"refresh_token": owner_a.refresh_token, "device_token": b_token},
        )
        assert r.status_code == 200, r.text
        # B's device must still be there - untouched.
        assert self._device_count(api_client, admin_headers, owner_b.owner_id) == 1

    def test_logout_without_device_token_is_unaffected_backward_compatible(self, api_client, admin_headers):
        """A client that never sends device_token (older app build, or no
        push token yet) must see identical logout behavior to before this
        change - no error, refresh token still revoked, device untouched."""
        owner = _create_test_company(api_client, admin_headers, "TEST_LogoutNoTokenCo")
        self._register(api_client, owner.headers, f"test-device-{uuid.uuid4().hex}")
        assert self._device_count(api_client, admin_headers, owner.owner_id) == 1

        r = api_client.post(f"{BASE_URL}/api/auth/logout", json={"refresh_token": owner.refresh_token})
        assert r.status_code == 200, r.text
        # Device untouched - only an explicit device_token (or the
        # separate DELETE /devices endpoint) removes it.
        assert self._device_count(api_client, admin_headers, owner.owner_id) == 1
