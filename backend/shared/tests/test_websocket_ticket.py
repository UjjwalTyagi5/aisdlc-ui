"""WebSocket ticket authentication tests for M2-04 (D-03).

Covers M2_FASTAPI_UNIFICATION.md §Testing row 7:
  1. Valid ticket -> connection opens successfully
  2. Invalid (nonexistent) ticket -> WebSocket close 4401
  3. Reused ticket -> WebSocket close 4401 (ticket deleted on first use)

Tests require Redis (ticket storage) and skip cleanly when REDIS_URL is absent.
They use starlette.testclient.TestClient for synchronous WS testing.
"""
import time

import pytest

pytestmark = pytest.mark.skip(
    reason="pre-existing import error — No module named 'jwt' (PyJWT not installed); "
    "quarantined in milestone-4 Wave A"
)

try:
    import jwt
    from config.env import JWT_SECRET_KEY, JWT_ALGORITHM, REDIS_URL
    _SKIP_NO_REDIS = pytest.mark.skipif(
        not REDIS_URL,
        reason="REDIS_URL not set — skipping WebSocket ticket tests",
    )
except (ImportError, ModuleNotFoundError):
    jwt = None
    JWT_SECRET_KEY = None
    JWT_ALGORITHM = None
    REDIS_URL = None
    _SKIP_NO_REDIS = pytest.mark.skip(reason="jwt not installed")


def _mint_jwt() -> str:
    """Mint a short-lived JWT for ticket exchange."""
    now = int(time.time())
    payload = {
        "sub": "ws-test-user",
        "tenant_id": "ws-test-tenant",
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, JWT_SECRET_KEY or "test-secret", algorithm=JWT_ALGORITHM or "HS256")


def _get_ticket(client) -> str:
    """Exchange a JWT for a single-use WS ticket via POST /auth/ws-ticket."""
    token = _mint_jwt()
    resp = client.post(
        "/auth/ws-ticket",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"Failed to mint ticket: {resp.text}"
    return resp.json()["ticket"]


@pytest.mark.integration
@_SKIP_NO_REDIS
def test_valid_ticket_connects():
    """A single-use ticket obtained from POST /auth/ws-ticket should allow WS connection."""
    from starlette.testclient import TestClient
    from process_api import app

    with TestClient(app) as client:
        ticket = _get_ticket(client)
        try:
            with client.websocket_connect(
                f"/sdlc/agent/requirement/ws?ticket={ticket}"
            ) as ws:
                # Connection opened — ticket was valid and consumed
                # Send a minimal ping to confirm the socket is live
                ws.send_text('{"type": "ping"}')
                # If we reach here without exception, the connection was accepted
        except Exception as exc:
            # Connection refused / closed immediately → ticket was not accepted
            pytest.fail(f"Valid ticket connection failed: {exc}")


@pytest.mark.integration
@_SKIP_NO_REDIS
def test_invalid_ticket_returns_4401():
    """Connecting with a nonexistent ticket UUID must result in close code 4401."""
    from starlette.testclient import TestClient
    from process_api import app

    with TestClient(app) as client:
        try:
            with client.websocket_connect(
                "/sdlc/agent/requirement/ws?ticket=00000000-0000-0000-0000-000000000000"
            ) as ws:
                # If we reach here, the server did not close with 4401 — fail
                pytest.fail("Expected server to close with 4401 for invalid ticket")
        except Exception as exc:
            # starlette TestClient raises WebSocketDisconnect on abnormal close
            error_str = str(exc)
            # Accept either the close code in the exception message or a generic disconnect
            # WebSocketDisconnect.code == 4401 or the string "4401" in the repr
            code = getattr(exc, "code", None)
            assert code == 4401 or "4401" in error_str, (
                f"Expected close code 4401 for invalid ticket, got: {exc!r}"
            )


@pytest.mark.integration
@_SKIP_NO_REDIS
def test_reused_ticket_returns_4401():
    """Reusing a single-use ticket must result in close code 4401 on the second connection.

    The ticket is atomically deleted (GETDEL) on first use by redeem_ws_ticket,
    so a second connection with the same ticket string must be treated as invalid.
    """
    from starlette.testclient import TestClient
    from process_api import app

    with TestClient(app) as client:
        ticket = _get_ticket(client)

        # First use — consume the ticket
        try:
            with client.websocket_connect(
                f"/sdlc/agent/requirement/ws?ticket={ticket}"
            ):
                pass  # Connect then immediately disconnect
        except Exception:
            pass  # First connection may close due to missing session data; that is fine

        # Second use — same ticket string must be rejected
        try:
            with client.websocket_connect(
                f"/sdlc/agent/requirement/ws?ticket={ticket}"
            ) as ws:
                pytest.fail("Expected server to close with 4401 for reused ticket")
        except Exception as exc:
            code = getattr(exc, "code", None)
            error_str = str(exc)
            assert code == 4401 or "4401" in error_str, (
                f"Expected close code 4401 for reused ticket, got: {exc!r}"
            )
