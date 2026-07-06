"""Shared fixtures for JAZ Platform backend tests."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://jaz-management.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _login(api_client, email_or_phone, password):
    r = api_client.post(
        f"{BASE_URL}/api/auth/login",
        json={"email_or_phone": email_or_phone, "password": password},
    )
    assert r.status_code == 200, f"Login failed for {email_or_phone}: {r.status_code} {r.text}"
    return r.json()


@pytest.fixture(scope="session", autouse=True)
def _seed(api_client):
    # Ensure seed data exists (idempotent per implementation)
    api_client.post(f"{BASE_URL}/api/seed")
    yield


@pytest.fixture(scope="session")
def admin_token(api_client):
    data = _login(api_client, "admin@jaz.com", "admin123")
    return data["token"]


@pytest.fixture(scope="session")
def owner_token(api_client):
    data = _login(api_client, "owner@demo.com", "owner123")
    return data["token"]


@pytest.fixture(scope="session")
def owner_user(api_client):
    data = _login(api_client, "owner@demo.com", "owner123")
    return data["user"]


@pytest.fixture(scope="session")
def employee_token(api_client):
    data = _login(api_client, "employee1@demo.com", "emp123")
    return data["token"]


@pytest.fixture(scope="session")
def employee_user(api_client):
    data = _login(api_client, "employee1@demo.com", "emp123")
    return data["user"]


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(admin_token):
    return auth_headers(admin_token)


@pytest.fixture
def owner_headers(owner_token):
    return auth_headers(owner_token)


@pytest.fixture
def employee_headers(employee_token):
    return auth_headers(employee_token)
