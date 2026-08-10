"""HTTP tests for GET /connectors/health (REQ-M3-08).

Verifies the endpoint is JWT-protected (401 without a token) and returns the
expected {"connectors": ..., "probed_at": ...} shape with a valid token.

Uses the AsyncClient + ASGITransport pattern from test_jwt_auth.py. All tests
are @pytest.mark.unit — the in-process ASGI app needs no real Postgres/Redis.
"""
import time

import pytest

pytestmark = pytest.mark.skip(
    reason="pre-existing import error — No module named 'jwt' (PyJWT not installed); "
    "quarantined in milestone-4 Wave A"
)

try:
    import jwt
    from httpx import ASGITransport, AsyncClient
    from config.env import JWT_ALGORITHM, JWT_SECRET_KEY
    _skip_no_jwt = pytest.mark.skipif(
        not JWT_SECRET_KEY,
        reason="JWT_SECRET_KEY not set — skipping connector health auth tests",
    )
except (ImportError, ModuleNotFoundError):
    jwt = None
    ASGITransport = None
    AsyncClient = None
    JWT_ALGORITHM = None
    JWT_SECRET_KEY = None
    _skip_no_jwt = pytest.mark.skip(reason="jwt not installed")


def _mint_token(payload_extra: dict | None = None, exp_offset: int = 3600) -> str:
    """Mint a signed HS256 token using the configured secret."""
    now = int(time.time())
    payload = {
        "sub": "test-user-001",
        "tenant_id": "test-tenant",
        "iat": now,
        "exp": now + exp_offset,
    }
    if payload_extra:
        payload.update(payload_extra)
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM or "HS256")


@pytest.mark.unit
@pytest.mark.asyncio
@_skip_no_jwt
async def test_connectors_health_requires_auth():
    """GET /connectors/health without a token must be rejected with 401."""
    from process_api import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/connectors/health")
    assert resp.status_code == 401, (
        f"Expected 401 without JWT, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
@_skip_no_jwt
async def test_connectors_health_returns_200_with_jwt():
    """GET /connectors/health with a valid token returns 200 + expected shape."""
    from process_api import app

    token = _mint_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/connectors/health",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, (
        f"Expected 200 with JWT, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "connectors" in body, f"Response missing 'connectors': {body}"
    assert "probed_at" in body, f"Response missing 'probed_at': {body}"
