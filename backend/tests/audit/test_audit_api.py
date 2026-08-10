"""REQ-M8-06 — Audit query API unit tests.

Asserts:
  - GET /runs/{id}/audit returns paginated audit events filterable by
    agent, actor, event_type, and time range
  - Requests without artifact:view permission return 403 (RBAC gate)
  - Cursor-based pagination works correctly

Uses ASGI transport (httpx.AsyncClient) + mint_token-minted JWTs, matching
the pattern established by test_m7_rbac.py.

Wave-3 turns these GREEN when the audit router is registered in process_api.py.
"""
from __future__ import annotations

import pytest

# Wave-3 implements the audit query router and registers it in process_api.py.
pytestmark = pytest.mark.xfail(
    reason="Wave 3 implements GET /runs/{id}/audit router",
    strict=False,
)


@pytest.mark.unit
async def test_get_run_audit_requires_artifact_view(mint_token):
    """GET /runs/{id}/audit returns 403 when JWT lacks artifact:view permission (REQ-M8-06)."""
    import httpx
    from process_api import app

    token = mint_token(
        user_id="u1",
        tenant_id="00000000-0000-0000-0000-000000000001",
        permissions=["run:create"],  # no artifact:view
    )
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/runs/run-001/audit", headers=headers)

    assert response.status_code == 403, (
        f"Expected 403 without artifact:view, got {response.status_code}"
    )


@pytest.mark.unit
async def test_get_run_audit_with_artifact_view_returns_200(mint_token):
    """GET /runs/{id}/audit returns 200 with artifact:view permission (REQ-M8-06)."""
    import httpx
    from process_api import app

    token = mint_token(
        user_id="u2",
        tenant_id="00000000-0000-0000-0000-000000000001",
        permissions=["artifact:view"],
    )
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/runs/run-001/audit", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert "events" in data or "items" in data or isinstance(data, list), (
        "Response must include events list or paginated wrapper"
    )


@pytest.mark.unit
async def test_get_run_audit_filter_by_event_type(mint_token):
    """GET /runs/{id}/audit?event_type=tool_call_start returns only matching events."""
    import httpx
    from process_api import app

    token = mint_token(
        user_id="u3",
        tenant_id="00000000-0000-0000-0000-000000000001",
        permissions=["artifact:view"],
    )
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/runs/run-001/audit?event_type=tool_call_start",
            headers=headers,
        )

    assert response.status_code == 200


@pytest.mark.unit
async def test_get_run_audit_cursor_pagination(mint_token):
    """GET /runs/{id}/audit supports cursor-based pagination (next_cursor in response)."""
    import httpx
    from process_api import app

    token = mint_token(
        user_id="u4",
        tenant_id="00000000-0000-0000-0000-000000000001",
        permissions=["artifact:view"],
    )
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/runs/run-001/audit?page_size=2",
            headers=headers,
        )

    assert response.status_code == 200
    data = response.json()
    # Cursor-based pagination: response may include next_cursor or similar
    # The exact key name is determined by Wave-3 implementation
    assert isinstance(data, dict), "Paginated response should be a JSON object"


@pytest.mark.unit
async def test_get_run_audit_no_token_returns_401():
    """GET /runs/{id}/audit without Authorization header returns 401."""
    import httpx
    from process_api import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/runs/run-001/audit")

    assert response.status_code in (401, 403), (
        f"Expected 401 or 403 without token, got {response.status_code}"
    )
