"""Tests for /artifacts, /connectors, /runs/{id}/signals routers (milestone-4, plan 04).

Covers:
  (a) 401 without a Bearer token on /artifacts/{id}, /connectors, /runs/{id}/signals/{name}
      (MUST pass — no DB required, mirrors Plan 02 harness). The signals path stays
      covered here even though no route now matches it (see note below): the JWT
      middleware gates every path before routing, so this still exercises real
      behaviour rather than testing a route that no longer exists.
  (b) With a valid HS256 token, GET /connectors returns a list with Connector
      contract fields (id, tenantId, kind, name, installed, health, capabilities,
      lastCheckedAt).

Tests use httpx AsyncClient + ASGITransport (same pattern as Plan 02 test harness).

Threat mitigations verified:
  T-M4-04: GET /connectors requires auth (test_connectors_requires_auth)
  T-M4-05: GET /artifacts/{id} requires auth (test_artifacts_requires_auth)

NOTE — signal-dispatch tests removed: POST /runs/{id}/signals/{name} (T-M4-06's
SignalAck-returning stub) is gone from process_api.py — no signals_router is
included anywhere, and shared.routers._schemas.SignalAck has no other reader.
Gate/stage progression moved to the Copilot-driven flow in shared/routers/runs.py
("Chat-driven progression: for a Copilot-driven run there is no workflow, so the
gate decision mutates run state directly here") — the same architectural move that
retired WebhookConsumer (see shared/tests/test_m6_verification.py). The two tests
that posted to the dead route and asserted a SignalAck body were removed rather than
left red against functionality that no longer exists.
"""
import time
import uuid

import pytest

from config.env import JWT_SECRET_KEY, JWT_ALGORITHM

# Guard: process_api imports the full agents_orchestrator tree which requires
# langgraph, aiofiles, python-docx, and other heavy deps not installed in
# every dev environment.  Skip the entire module gracefully when any of those
# are absent rather than exploding with ModuleNotFoundError/SyntaxError.
try:
    import process_api as _process_api_mod  # noqa: F401 — import probe only
    _PROCESS_API_IMPORTABLE = True
except (ImportError, SyntaxError) as _e:
    _PROCESS_API_IMPORTABLE = False
    _PROCESS_API_IMPORT_ERROR = str(_e)

_skip_no_process_api = pytest.mark.skipif(
    not _PROCESS_API_IMPORTABLE,
    reason=(
        "process_api not importable (missing agent deps in this environment) — "
        "install all requirements from agentic_app/requirements.txt to run these tests"
    ),
)

_skip_no_jwt = pytest.mark.skipif(
    not JWT_SECRET_KEY,
    reason="JWT_SECRET_KEY not set — skipping resource router auth tests",
)

def _mint_token(tenant_id: str | None = None, exp_offset: int = 3600) -> str:
    """Mint a signed HS256 token using the configured secret.

    tenant_id defaults to a fresh UUID, not a literal string: require_permission's
    workspace-resolution fallback (active_workspace_for_request) now runs a real
    uuid.UUID(str(tenant_id)) against Postgres, and a non-UUID tenant_id 500s instead
    of the clean 404-then-fall-through-to-permission-check it's designed to produce
    for a tenant with no seeded workspace. permissions defaults to the wildcard so
    these tests exercise the routes' shape, not RBAC — narrower grants belong in
    RBAC-specific tests.
    """
    try:
        import jwt
        now = int(time.time())
        payload = {
            "sub": "test-user-001",
            "tenant_id": tenant_id or str(uuid.uuid4()),
            "iat": now,
            "exp": now + exp_offset,
            "permissions": ["admin:*"],
        }
        return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM or "HS256")
    except ImportError:
        pytest.skip("PyJWT not installed — cannot mint token")


# ── 401 tests (no DB required) ────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
@_skip_no_jwt
@_skip_no_process_api
async def test_artifacts_requires_auth():
    """GET /artifacts/{id} without a Bearer token must return 401 (T-M4-05)."""
    from httpx import ASGITransport, AsyncClient
    from process_api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/artifacts/{uuid.uuid4()}")
    assert resp.status_code == 401, (
        f"Expected 401 without JWT on /artifacts/{{id}}, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
@_skip_no_jwt
@_skip_no_process_api
async def test_connectors_requires_auth():
    """GET /connectors without a Bearer token must return 401 (T-M4-04)."""
    from httpx import ASGITransport, AsyncClient
    from process_api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/connectors")
    assert resp.status_code == 401, (
        f"Expected 401 without JWT on /connectors, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
@_skip_no_jwt
@_skip_no_process_api
async def test_signals_requires_auth():
    """POST /runs/{id}/signals/{name} without a Bearer token must return 401."""
    from httpx import ASGITransport, AsyncClient
    from process_api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/runs/{uuid.uuid4()}/signals/hitl.decision",
            json={"idempotencyKey": "test-key-001"},
        )
    assert resp.status_code == 401, (
        f"Expected 401 without JWT on /runs/{{id}}/signals/{{name}}, got {resp.status_code}: {resp.text}"
    )


# ── Connector list shape tests (auth only — no DB required) ──────────────────

@pytest.mark.unit
@pytest.mark.asyncio
@_skip_no_jwt
@_skip_no_process_api
async def test_connectors_returns_list():
    """GET /connectors with a valid JWT returns 200 + a list (may be empty in test env).

    Validates that the response is a JSON array matching the Connector contract
    fields when entries are present: id, tenantId, kind, name, installed, health,
    capabilities, lastCheckedAt.  An empty list is valid when no connectors are
    installed in the test environment.
    """
    from httpx import ASGITransport, AsyncClient
    from process_api import app

    token = _mint_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/connectors",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, (
        f"Expected 200 on GET /connectors with JWT, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert isinstance(body, list), f"GET /connectors must return a list, got: {type(body)}"

    # Validate contract fields if there are any entries
    _REQUIRED_CONNECTOR_FIELDS = {
        "id", "tenantId", "kind", "name", "installed", "health",
        "capabilities", "lastCheckedAt",
    }
    for item in body:
        missing = _REQUIRED_CONNECTOR_FIELDS - item.keys()
        assert not missing, (
            f"Connector item missing required fields {missing}: {item}"
        )
        assert isinstance(item["capabilities"], list), (
            f"connector.capabilities must be a list: {item}"
        )
        assert isinstance(item["installed"], bool), (
            f"connector.installed must be a bool: {item}"
        )
        assert item["health"] in ("healthy", "degraded", "disconnected"), (
            f"connector.health must be a valid ConnectorHealth enum value: {item}"
        )

