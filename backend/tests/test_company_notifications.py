"""Live HTTP integration tests for the Super Admin "Company Notifications"
broadcast feature - same style as tests/conftest.py's other suites: real
requests against a running backend, using the session-scoped
admin/owner/employee fixtures.
"""
import uuid

import pytest

from tests.conftest import BASE_URL, auth_headers, _login


CONTENT = {
    "title_ar": "عنوان تجريبي",
    "title_en": "Test Title",
    "message_ar": "رسالة تجريبية",
    "message_en": "Test message",
}


def _company_notifications(mode, **overrides):
    body = {"recipient_mode": mode, "target_config": None, **CONTENT}
    body.update(overrides)
    return body


def _latest_company_notification(notifications):
    for n in notifications:
        if n["category"] == "company_notifications":
            return n
    return None


class TestRecipientModes:
    def test_all_managers(self, api_client, admin_headers):
        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications",
            json=_company_notifications("all_managers"),
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["recipient_mode"] == "all_managers"
        assert data["recipient_count"] >= 1
        assert data["status"] in ("sent", "partial_failure")

    def test_all_employees(self, api_client, admin_headers):
        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications",
            json=_company_notifications("all_employees"),
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["recipient_count"] >= 1

    def test_everyone_covers_managers_and_employees_but_not_super_admin(self, api_client, admin_headers):
        managers = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications/preview",
            json={"recipient_mode": "all_managers", "target_config": None},
            headers=admin_headers,
        ).json()["recipient_count"]
        employees = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications/preview",
            json={"recipient_mode": "all_employees", "target_config": None},
            headers=admin_headers,
        ).json()["recipient_count"]
        everyone = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications/preview",
            json={"recipient_mode": "everyone", "target_config": None},
            headers=admin_headers,
        ).json()["recipient_count"]
        assert everyone == managers + employees

    def test_empty_recipient_set_is_handled_safely(self, api_client, admin_headers):
        """A freshly created company has an owner but zero employees yet -
        targeting it in 'employees' mode must resolve to 0, not error."""
        plans = api_client.get(f"{BASE_URL}/api/admin/subscription-plans", headers=admin_headers).json()
        assert plans, "seed data must include at least one subscription plan"
        suffix = uuid.uuid4().hex[:8]
        company = api_client.post(
            f"{BASE_URL}/api/admin/companies",
            json={
                "name": f"TEST_EmptyCo_{suffix}",
                "owner_email": f"empty_owner_{suffix}@example.com",
                "owner_name": "Empty Co Owner",
                "owner_password": "TestPass123!",
                "owner_phone": f"+1555{suffix[:7]}",
                "subscription_plan_id": plans[0]["id"],
            },
            headers=admin_headers,
        )
        assert company.status_code == 200, company.text
        company_id = company.json()["id"]

        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications",
            json=_company_notifications(
                "custom", target_config={"companies": [{"company_id": company_id, "mode": "employees"}]}
            ),
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["recipient_count"] == 0
        assert data["notification_created_count"] == 0
        assert data["status"] == "sent"


class TestCustomTargeting:
    def test_targeted_company_receives_it_and_others_do_not(self, api_client, admin_headers, owner_headers):
        plans = api_client.get(f"{BASE_URL}/api/admin/subscription-plans", headers=admin_headers).json()
        suffix = uuid.uuid4().hex[:8]
        company = api_client.post(
            f"{BASE_URL}/api/admin/companies",
            json={
                "name": f"TEST_CustomTargetCo_{suffix}",
                "owner_email": f"custom_owner_{suffix}@example.com",
                "owner_name": "Custom Target Owner",
                "owner_password": "TestPass123!",
                "owner_phone": f"+1555{suffix[:7]}",
                "subscription_plan_id": plans[0]["id"],
            },
            headers=admin_headers,
        )
        assert company.status_code == 200, company.text
        company_id = company.json()["id"]
        target_owner_token = _login(api_client, f"custom_owner_{suffix}@example.com", "TestPass123!")["token"]
        target_owner_headers = auth_headers(target_owner_token)

        before = api_client.get(f"{BASE_URL}/api/notifications", headers=owner_headers).json()
        before_count = len([n for n in before if n["category"] == "company_notifications"])

        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications",
            json=_company_notifications(
                "custom", target_config={"companies": [{"company_id": company_id, "mode": "everyone"}]}
            ),
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["recipient_count"] == 1  # just the new company's owner - no employees yet
        assert r.json()["notification_created_count"] == 1

        targeted = api_client.get(f"{BASE_URL}/api/notifications", headers=target_owner_headers).json()
        assert _latest_company_notification(targeted) is not None

        after = api_client.get(f"{BASE_URL}/api/notifications", headers=owner_headers).json()
        after_count = len([n for n in after if n["category"] == "company_notifications"])
        assert after_count == before_count  # untargeted company's owner got nothing new


class TestLanguage:
    def test_recipient_gets_content_in_their_own_language(self, api_client, admin_headers, owner_headers):
        set_lang = api_client.put(
            f"{BASE_URL}/api/profile/language", json={"language": "en"}, headers=owner_headers
        )
        assert set_lang.status_code == 200, set_lang.text
        try:
            r = api_client.post(
                f"{BASE_URL}/api/admin/company-notifications",
                json=_company_notifications("all_managers"),
                headers=admin_headers,
            )
            assert r.status_code == 200, r.text

            notifications = api_client.get(f"{BASE_URL}/api/notifications", headers=owner_headers).json()
            latest = _latest_company_notification(notifications)
            assert latest is not None
            assert latest["title"] == CONTENT["title_en"]
            assert latest["message"] == CONTENT["message_en"]
        finally:
            api_client.put(f"{BASE_URL}/api/profile/language", json={"language": "ar"}, headers=owner_headers)

    def test_default_language_is_arabic(self, api_client, admin_headers, owner_headers):
        api_client.put(f"{BASE_URL}/api/profile/language", json={"language": "ar"}, headers=owner_headers)
        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications",
            json=_company_notifications("all_managers"),
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        notifications = api_client.get(f"{BASE_URL}/api/notifications", headers=owner_headers).json()
        latest = _latest_company_notification(notifications)
        assert latest is not None
        assert latest["title"] == CONTENT["title_ar"]
        assert latest["message"] == CONTENT["message_ar"]

    def test_invalid_language_rejected(self, api_client, owner_headers):
        r = api_client.put(f"{BASE_URL}/api/profile/language", json={"language": "fr"}, headers=owner_headers)
        assert r.status_code == 400


class TestAuthorization:
    @pytest.mark.parametrize("path,method", [
        ("/admin/company-notifications", "post"),
        ("/admin/company-notifications/preview", "post"),
        ("/admin/company-notifications", "get"),
    ])
    def test_owner_forbidden(self, api_client, owner_headers, path, method):
        r = getattr(api_client, method)(f"{BASE_URL}/api{path}", json=_company_notifications("all_managers"), headers=owner_headers)
        assert r.status_code == 403

    @pytest.mark.parametrize("path,method", [
        ("/admin/company-notifications", "post"),
        ("/admin/company-notifications/preview", "post"),
        ("/admin/company-notifications", "get"),
    ])
    def test_employee_forbidden(self, api_client, employee_headers, path, method):
        r = getattr(api_client, method)(f"{BASE_URL}/api{path}", json=_company_notifications("all_managers"), headers=employee_headers)
        assert r.status_code == 403

    @pytest.mark.parametrize("path,method", [
        ("/admin/company-notifications", "post"),
        ("/admin/company-notifications/preview", "post"),
        ("/admin/company-notifications", "get"),
    ])
    def test_unauthenticated_rejected(self, api_client, path, method):
        r = getattr(api_client, method)(f"{BASE_URL}/api{path}", json=_company_notifications("all_managers"))
        assert r.status_code in (401, 403)


class TestPreviewAndHistory:
    def test_preview_does_not_create_or_send(self, api_client, admin_headers):
        before = api_client.get(f"{BASE_URL}/api/admin/company-notifications", headers=admin_headers).json()

        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications/preview",
            json={"recipient_mode": "all_managers", "target_config": None},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert "recipient_count" in r.json()

        after = api_client.get(f"{BASE_URL}/api/admin/company-notifications", headers=admin_headers).json()
        assert len(after) == len(before)

    def test_history_lists_created_broadcasts(self, api_client, admin_headers):
        created = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications",
            json=_company_notifications("all_managers"),
            headers=admin_headers,
        ).json()

        history = api_client.get(f"{BASE_URL}/api/admin/company-notifications", headers=admin_headers).json()
        assert any(item["id"] == created["id"] for item in history)

    def test_custom_mode_requires_target_config(self, api_client, admin_headers):
        r = api_client.post(
            f"{BASE_URL}/api/admin/company-notifications",
            json=_company_notifications("custom", target_config=None),
            headers=admin_headers,
        )
        assert r.status_code == 400
