"""RED tests for webhook router — REQ-M6-05.

Asserts that the FastAPI webhook router:
- Exposes POST /webhooks/{connector}/{tenant_id} route
- Route is reachable (no 404/405)
- Webhook endpoint is JWT-exempt (no 401 when no Bearer token)
- Unknown connector name → 400

Uses FastAPI TestClient (sync WSGI wrapper). Tests skip at collection time when
the webhooks.router module or process_api registration does not yet exist.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from fastapi.testclient import TestClient
    from webhooks.router import webhooks_router
    _SKIP = False
except (ImportError, ModuleNotFoundError) as _exc:
    _SKIP = True
    _SKIP_REASON = f"webhooks.router not yet implemented: {_exc}"

if _SKIP:
    pytest.skip(_SKIP_REASON, allow_module_level=True)

try:
    from fastapi import FastAPI
    _app = FastAPI()
    _app.include_router(webhooks_router)
    _client = TestClient(_app, raise_server_exceptions=False)
except Exception as _app_exc:
    pytest.skip(f"Could not build test app: {_app_exc}", allow_module_level=True)


@pytest.mark.unit
def test_webhook_route_exists_and_is_reachable():
    """POST /webhooks/{connector}/{tenant_id} route is mounted and reachable (not 404/405)."""
    resp = _client.post(
        "/webhooks/github/tenant-001",
        content=b'{"action":"opened"}',
        headers={"Content-Type": "application/json"},
    )
    # Acceptable responses: 200 (processed), 400 (bad sig), 422 (validation)
    # Not acceptable: 404 (route not found) or 405 (method not allowed)
    assert resp.status_code not in (404, 405), (
        f"Webhook route not mounted: got {resp.status_code} {resp.text}"
    )


@pytest.mark.unit
def test_webhook_route_is_jwt_exempt():
    """POST /webhooks/{connector}/{tenant_id} does not return 401 without a Bearer token.

    Signature verification IS the authentication mechanism; JWT is not required
    for webhook endpoints.
    """
    resp = _client.post(
        "/webhooks/github/tenant-001",
        content=b'{"action":"opened"}',
        headers={"Content-Type": "application/json"},
        # Explicitly: no Authorization: Bearer header
    )
    assert resp.status_code != 401, (
        f"Webhook endpoint must not require JWT auth; got 401"
    )


@pytest.mark.unit
def test_unknown_connector_returns_400():
    """POST /webhooks/{connector}/{tenant_id} with unknown connector returns 400."""
    resp = _client.post(
        "/webhooks/unknown_provider_xyz/tenant-001",
        content=b'{"action":"test"}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400, (
        f"Expected 400 for unknown connector, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.unit
def test_get_method_not_allowed_on_webhook_route():
    """GET /webhooks/{connector}/{tenant_id} is not a valid method (POST only)."""
    resp = _client.get("/webhooks/github/tenant-001")
    assert resp.status_code in (404, 405), (
        f"Expected 404/405 for GET on webhook route, got {resp.status_code}"
    )
