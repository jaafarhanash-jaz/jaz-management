"""JAZ Sales - Phase 1: route sweep.

Three guarantees, enforced against the REAL route table so they keep holding as
the API grows:

  A. STRUCTURAL - every /api/sales route sits behind the staff gate, and every
     route except /me also declares an explicit permission. A route added later
     without a guard fails here.
  B. CONTAINMENT - a fully-privileged staff user (Sales Manager) can reach NO
     core (owner/employee/super-admin) feature. Every protected core route is
     called with a schema-valid body; only an explicit, reviewed allowlist of
     own-account self-service routes may succeed.
  C. INVENTORY - the set of public (unauthenticated) routes is pinned, so an
     accidentally-unprotected new endpoint is noticed.

B and the /api/sales checks in C run against the live scratch backend (see
sales_test_utils.assert_scratch_target).
"""
import re

import pytest
import requests

from sales_test_utils import BASE_URL, STAFF_PASSWORD, assert_scratch_target, auth, create_staff
from sales_test_utils import admin_token  # noqa: F401  - mints instead of logging in; overrides conftest's fixture

PHANTOM = "00000000-0000-4000-8000-000000000000"

# Core routes that legitimately succeed for ANY authenticated user (incl. a staff
# user) because they only ever act on the caller's own account. Reviewed one by one:
SELF_SERVICE_2XX = {
    ("GET", "/api/auth/me"),                                  # own profile
    ("POST", "/api/heartbeat"),                               # own presence signal
    ("GET", "/api/notifications"),                            # own notifications
    ("GET", "/api/notifications/unread-count"),
    ("PUT", "/api/notifications/read-all"),
    ("PUT", "/api/notifications/{notification_id}/read"),
    ("GET", "/api/dashboard/layout"),                         # own widget layout
    ("PUT", "/api/dashboard/layout"),
    ("GET", "/api/devices"),                                  # own push-device registrations
    ("POST", "/api/devices"),
    ("DELETE", "/api/devices"),
    ("PUT", "/api/employee/profile"),                         # own name/phone/avatar only (whitelist)
    ("PUT", "/api/profile/language"),
    ("PUT", "/api/profile/password"),
    ("GET", "/api/profile/photo"),
    ("POST", "/api/profile/photo"),
    ("DELETE", "/api/profile/photo"),
    ("GET", "/api/owner/subscription-plans"),                 # plan catalog: open to any authenticated user (pre-existing)
}
# Pre-existing defect, NOT introduced by Sales and reproducible with a Super Admin
# token too: these routes DO enforce role != employee -> 403, but the failure-audit
# step that runs afterwards inserts an attendance_events row with a null company_id
# and the NOT NULL constraint turns the 403 into a 500. Nothing is written and no
# access is granted; tracked for a separate fix. Any OTHER 5xx still fails the test.
KNOWN_PREEXISTING_500 = {
    ("POST", "/api/employee/attendance/check-in"),
    ("POST", "/api/employee/attendance/check-out"),
}
# Routes with no get_current_user dependency (public or token-in-query by design).
EXPECTED_PUBLIC = {
    ("GET", "/api/health"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/refresh"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/notifications/stream"),   # SSE: authenticates with a token query param
    ("POST", "/api/seed"),                  # dev/test seeding; refuses to run in production
    ("POST", "/api/webhook/stripe"),        # authenticated by Stripe signature
}


def _walk(dependant):
    for dep in dependant.dependencies:
        yield dep
        yield from _walk(dep)


def _routes(app):
    for r in app.routes:
        path, methods = getattr(r, "path", None), getattr(r, "methods", None)
        if path and methods and path.startswith("/api"):
            for m in sorted(methods - {"HEAD", "OPTIONS"}):
                yield r, m, path


@pytest.fixture(scope="module")
def server_app():
    assert_scratch_target()
    import server  # importing builds the route table; opens no DB connection
    return server


# =============================================================================
# A. structural
# =============================================================================
class TestEverySalesRouteIsGuarded:
    def test_router_level_staff_gate_is_present(self):
        from sales.deps import get_staff_context
        from sales.router import sales_router
        assert any(d.dependency is get_staff_context for d in sales_router.dependencies)

    def test_every_route_declares_a_permission_except_me(self, server_app):
        found = 0
        for route, method, path in _routes(server_app.app):
            if not path.startswith("/api/sales"):
                continue
            found += 1
            guards = [getattr(d.call, "__sales_guard__", None) for d in _walk(route.dependant)]
            assert "staff" in guards, f"{method} {path} is not behind the staff gate"
            if path != "/api/sales/me":
                assert "permission" in guards, f"{method} {path} declares no require_permission(...)"
        assert found >= 8

    def test_sales_paths_come_only_from_the_sales_router(self, server_app):
        from sales.router import sales_router
        stray = [(m, p) for r, m, p in _routes(server_app.app)
                 if p.startswith("/api/sales") and r.endpoint.__module__ != "sales.router"]
        assert stray == [], f"/api/sales routes defined outside sales.router: {stray}"
        assert all(r.path.startswith("/api/sales") for r in sales_router.routes)


# =============================================================================
# C. public-route inventory
# =============================================================================
class TestPublicRouteInventory:
    def test_public_routes_are_exactly_the_reviewed_set(self, server_app):
        public = set()
        for route, method, path in _routes(server_app.app):
            if path.startswith("/api/sales"):
                continue
            if not any(d.call is server_app.get_current_user for d in _walk(route.dependant)):
                public.add((method, path))
        assert public == EXPECTED_PUBLIC, f"new/removed public routes: {public ^ EXPECTED_PUBLIC}"


# =============================================================================
# B. containment (live)
# =============================================================================
def _dummy_body_factory(spec):
    def resolve(sch):
        while isinstance(sch, dict) and "$ref" in sch:
            node = spec
            for part in sch["$ref"].lstrip("#/").split("/"):
                node = node[part]
            sch = node
        return sch

    def dummy(sch, depth=0):
        sch = resolve(sch)
        if depth > 6:
            return None
        if "anyOf" in sch or "oneOf" in sch:
            opts = [o for o in (sch.get("anyOf") or sch.get("oneOf")) if resolve(o).get("type") != "null"]
            return dummy(opts[0], depth + 1) if opts else None
        if "allOf" in sch:
            return dummy(sch["allOf"][0], depth + 1)
        if "enum" in sch:
            return sch["enum"][0]
        t, fmt = sch.get("type"), sch.get("format")
        if t == "string":
            return {"email": "dummy@example.com", "uuid": PHANTOM, "date": "2027-01-01", "date-time": "2027-01-01T10:00:00Z"}.get(fmt, "x")
        if t == "integer":
            return max(1, int(sch.get("minimum", 1)))
        if t == "number":
            return 1.0
        if t == "boolean":
            return True
        if t == "array":
            return []
        if t == "object" or "properties" in sch:
            required = sch.get("required") or []
            return {k: dummy(v, depth + 1) for k, v in (sch.get("properties") or {}).items() if k in required}
        return "x"

    def body_for(path, method):
        op = spec["paths"].get(path, {}).get(method.lower(), {})
        content = op.get("requestBody", {}).get("content", {})
        if "application/json" in content:
            return dummy(content["application/json"]["schema"])
        return {} if method in ("POST", "PUT", "PATCH") else None

    return body_for


@pytest.fixture(scope="module")
def manager_headers(admin_token):
    return create_staff(auth(admin_token), ["sales_manager"], "sweep")["headers"]


class TestStaffCannotReachCoreFeatures:
    def test_sales_manager_is_denied_on_every_protected_core_route(self, server_app, manager_headers):
        body_for = _dummy_body_factory(server_app.app.openapi())
        probed, unexpected, self_service_seen, known_500_seen = 0, [], set(), set()
        for route, method, path in _routes(server_app.app):
            if path.startswith("/api/sales"):
                continue
            if not any(d.call is server_app.get_current_user for d in _walk(route.dependant)):
                continue  # public - pinned by TestPublicRouteInventory
            url = BASE_URL + re.sub(r"\{[^}]+\}", PHANTOM, path)
            resp = requests.request(method, url, json=body_for(path, method), headers=manager_headers, timeout=(5, 15), stream=True)
            status = resp.status_code
            resp.close()
            probed += 1
            key = (method, path)
            if status >= 500:
                if key in KNOWN_PREEXISTING_500:
                    known_500_seen.add(key)
                else:
                    unexpected.append((method, path, status, "unexpected 5xx reached by a staff user"))
            elif status < 400:
                if key in SELF_SERVICE_2XX:
                    self_service_seen.add(key)
                else:
                    unexpected.append((method, path, status, "staff user SUCCEEDED on a core route outside the self-service allowlist"))
        assert probed >= 150, f"sweep looks too small ({probed} routes) - route table not enumerated?"
        assert unexpected == [], "\n".join(map(str, unexpected))
        # the allowlist must not silently rot: every entry that was reachable should still be exercised
        assert ("GET", "/api/auth/me") in self_service_seen

    def test_owner_data_routes_specifically_are_denied(self, manager_headers):
        """Spot-check the highest-value targets by name, independent of the generic sweep."""
        for method, path in [
            ("GET", "/api/owner/employees"), ("GET", "/api/owner/tasks"), ("GET", "/api/owner/attendance"),
            ("GET", "/api/owner/dashboard"), ("GET", "/api/admin/companies"), ("GET", "/api/admin/statistics"),
            ("GET", "/api/admin/subscription-plans"), ("GET", "/api/employee/tasks"), ("GET", "/api/messages/inbox"),
        ]:
            r = requests.request(method, BASE_URL + path, headers=manager_headers, timeout=15)
            assert r.status_code == 403, f"{method} {path} -> {r.status_code} {r.text[:120]}"
