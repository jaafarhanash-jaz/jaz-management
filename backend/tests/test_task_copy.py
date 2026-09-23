"""Live HTTP integration tests for the employee task-copy workflow
(Part 21) - same style as tests/test_employee_groups.py: real requests
against a running backend, using the session-scoped owner fixture plus
freshly-created old/new employees per test.
"""
import re
import subprocess
import uuid
from pathlib import Path

import pytest

from tests.conftest import BASE_URL, auth_headers


def _env_database_url() -> str:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    match = re.search(r"^DATABASE_URL=(.+)$", env_path.read_text(), re.MULTILINE)
    return match.group(1).strip()


def _psql_scalar(query: str) -> str:
    """Direct read-only DB access for assertions only (never for setup) -
    the shared GET /owner/tasks listing endpoint has a pre-existing,
    unrelated issue (it eagerly resolves every task's attachments,
    including stale ones left by other test suites sharing this demo
    company against local object storage) that has nothing to do with
    task-copy; querying the table directly sidesteps that entirely,
    without adding a new Python DB-driver dependency just for this."""
    url = re.sub(r"^postgresql\+asyncpg://", "postgresql://", _env_database_url())
    result = subprocess.run(
        ["psql", url, "-t", "-A", "-c", query], capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _create_employee(api_client, owner_headers, label):
    unique = uuid.uuid4().hex[:8]
    r = api_client.post(
        f"{BASE_URL}/api/owner/employees",
        json={
            "email": f"TEST_copy_{label}_{unique}@example.com", "phone": f"059{unique[:7]}",
            "password": "testpass123", "name": f"TEST_Copy_{label}_{unique}", "role": "employee",
        },
        headers=owner_headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _create_task(api_client, owner_headers, employee_id, *, priority="medium"):
    r = api_client.post(
        f"{BASE_URL}/api/owner/tasks",
        json={"title": "TEST_ copy task", "description": "desc", "priority": priority,
              "assigned_to": employee_id, "due_date": "2026-12-31"},
        headers=owner_headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _create_daily_template(api_client, owner_headers, employee_id):
    r = api_client.post(
        f"{BASE_URL}/api/owner/daily-tasks",
        json={"title": "TEST_ daily template", "description": "desc", "assigned_to": [employee_id]},
        headers=owner_headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def other_company_owner_headers(api_client, admin_token):
    admin_hdrs = auth_headers(admin_token)
    unique = uuid.uuid4().hex[:8]
    plan = api_client.post(
        f"{BASE_URL}/api/admin/subscription-plans",
        json={"name": f"TEST_Plan_{unique}", "max_employees": 5, "price": 1, "duration_months": 1},
        headers=admin_hdrs,
    ).json()
    owner_email = f"TEST_copyowner_{unique}@example.com"
    company = api_client.post(
        f"{BASE_URL}/api/admin/companies",
        json={
            "name": f"TEST_CopyOtherCo_{unique}", "owner_email": owner_email,
            "owner_name": "Other Owner", "owner_password": "pass1234", "owner_phone": f"055{unique[:7]}",
            "subscription_plan_id": plan["id"],
        },
        headers=admin_hdrs,
    )
    assert company.status_code == 200, company.text
    login = api_client.post(f"{BASE_URL}/api/auth/login", json={"email_or_phone": owner_email, "password": "pass1234"})
    assert login.status_code == 200, login.text
    return auth_headers(login.json()["token"])


class TestPreview:
    def test_preview_counts(self, api_client, owner_headers):
        old = _create_employee(api_client, owner_headers, "old")
        new = _create_employee(api_client, owner_headers, "new")
        _create_task(api_client, owner_headers, old["id"])
        _create_task(api_client, owner_headers, old["id"])
        _create_task(api_client, owner_headers, old["id"], priority="critical")
        _create_daily_template(api_client, owner_headers, old["id"])

        r = api_client.get(
            f"{BASE_URL}/api/owner/employees/{old['id']}/copy-tasks-preview/{new['id']}", headers=owner_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["task_count"] == 2
        assert body["daily_task_count"] == 1
        assert body["critical_task_count_not_copied"] == 1
        assert body["old_employee_id"] == old["id"]
        assert body["new_employee_id"] == new["id"]

    def test_same_employee_rejected(self, api_client, owner_headers):
        emp = _create_employee(api_client, owner_headers, "same")
        r = api_client.get(
            f"{BASE_URL}/api/owner/employees/{emp['id']}/copy-tasks-preview/{emp['id']}", headers=owner_headers,
        )
        assert r.status_code == 400

    def test_cross_tenant_old_employee_rejected(self, api_client, owner_headers, other_company_owner_headers):
        old = _create_employee(api_client, owner_headers, "xold")
        new = _create_employee(api_client, owner_headers, "xnew")
        r = api_client.get(
            f"{BASE_URL}/api/owner/employees/{old['id']}/copy-tasks-preview/{new['id']}",
            headers=other_company_owner_headers,
        )
        assert r.status_code == 404


class TestConfirm:
    def test_copy_creates_independent_task_and_clones_template_not_critical(self, api_client, owner_headers):
        old = _create_employee(api_client, owner_headers, "cold")
        new = _create_employee(api_client, owner_headers, "cnew")
        task = _create_task(api_client, owner_headers, old["id"])
        _create_task(api_client, owner_headers, old["id"], priority="critical")
        template = _create_daily_template(api_client, owner_headers, old["id"])

        r = api_client.post(
            f"{BASE_URL}/api/owner/employees/{old['id']}/copy-tasks-to/{new['id']}",
            json={}, headers=owner_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tasks_copied"] == 1
        assert body["daily_tasks_cloned"] == 1
        assert body["critical_tasks_not_copied"] == 1

        # New employee has an independent, fresh-status task - verified
        # directly against the DB (see _psql_scalar's docstring for why).
        new_task_row = _psql_scalar(
            f"SELECT id || '|' || status FROM tasks WHERE assigned_to='{new['id']}' "
            f"AND title='TEST_ copy task' AND deleted_at IS NULL",
        )
        assert new_task_row, "copied task not found for new employee"
        new_task_id, new_task_status = new_task_row.split("|")
        assert new_task_status == "new"
        assert new_task_id != task["id"]

        # No critical task was copied to the new employee.
        critical_count = _psql_scalar(
            f"SELECT count(*) FROM tasks WHERE assigned_to='{new['id']}' AND priority='critical' AND deleted_at IS NULL",
        )
        assert critical_count == "0"

        # Old employee's original task is completely untouched.
        old_task_still_present = _psql_scalar(f"SELECT count(*) FROM tasks WHERE id='{task['id']}'")
        assert old_task_still_present == "1"

        # Daily template was CLONED (independent row), not shared - a
        # second, different-id template with the same title now exists,
        # assigned only to the new employee; the original's own assignee
        # list (old employee) is untouched.
        clone_count = _psql_scalar(
            f"SELECT count(*) FROM daily_tasks dt "
            f"JOIN daily_task_assignees dta ON dta.daily_task_id = dt.id "
            f"WHERE dt.title='TEST_ daily template' AND dt.id != '{template['id']}' "
            f"AND dta.employee_id='{new['id']}'",
        )
        assert clone_count == "1"
        original_assignee_untouched = _psql_scalar(
            f"SELECT count(*) FROM daily_task_assignees WHERE daily_task_id='{template['id']}' AND employee_id='{old['id']}'",
        )
        assert original_assignee_untouched == "1"

    def test_idempotency_key_prevents_double_copy(self, api_client, owner_headers):
        old = _create_employee(api_client, owner_headers, "iold")
        new = _create_employee(api_client, owner_headers, "inew")
        _create_task(api_client, owner_headers, old["id"])
        key = str(uuid.uuid4())

        r1 = api_client.post(
            f"{BASE_URL}/api/owner/employees/{old['id']}/copy-tasks-to/{new['id']}",
            json={"idempotency_key": key}, headers=owner_headers,
        )
        r2 = api_client.post(
            f"{BASE_URL}/api/owner/employees/{old['id']}/copy-tasks-to/{new['id']}",
            json={"idempotency_key": key}, headers=owner_headers,
        )
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json() == r2.json()

        count = _psql_scalar(
            f"SELECT count(*) FROM tasks WHERE assigned_to='{new['id']}' AND title='TEST_ copy task' AND deleted_at IS NULL",
        )
        assert count == "1"

    def test_employee_forbidden(self, api_client, owner_headers, employee_headers):
        old = _create_employee(api_client, owner_headers, "fold")
        new = _create_employee(api_client, owner_headers, "fnew")
        r = api_client.post(
            f"{BASE_URL}/api/owner/employees/{old['id']}/copy-tasks-to/{new['id']}",
            json={}, headers=employee_headers,
        )
        assert r.status_code == 403

    def test_cross_tenant_copy_rejected(self, api_client, owner_headers, other_company_owner_headers):
        old = _create_employee(api_client, owner_headers, "cxold")
        new = _create_employee(api_client, owner_headers, "cxnew")
        r = api_client.post(
            f"{BASE_URL}/api/owner/employees/{old['id']}/copy-tasks-to/{new['id']}",
            json={}, headers=other_company_owner_headers,
        )
        assert r.status_code == 404
