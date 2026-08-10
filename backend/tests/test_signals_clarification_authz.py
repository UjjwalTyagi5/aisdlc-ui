"""REQ-M10-05: authz proof for the within_agent_clarification signal endpoint.

Mirrors test_hitl_approval_signal.py::test_signal_role_denied_403 — the
within_agent_clarification signal name reuses the existing generic
POST /runs/{run_id}/signals/{name} dispatch path (no new route registration).

Tier: integration (requires Postgres — seed_run writes a real Run row).
Skip guard: tests skip cleanly when POSTGRES_CONN_STRING is absent, and
xfail cleanly if process_api is not importable.

Cases:
  1. 403 — developer permissions (no artifact:approve_requirements) against a
     requirements-stage run -> HTTP 403, handle.signal NOT called
     (403-before-handle, REQ-M7-11/Pitfall 4).
  2. 404 — token tenant_id differs from the seeded run's tenant -> HTTP 404
     (tenant-scoped _get_run guard, T-M5-18) — cross-tenant clarification
     injection is impossible.
  3. 200 — product_manager permissions (artifact:approve_requirements) against
     a tenant-matching requirements-stage run with a temporal_workflow_id ->
     HTTP 200, accepted=True, handle.signal awaited once with signal name
     "within_agent_clarification" and a HITLSignal payload carrying the
     clarification_id + answer.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.env import POSTGRES_CONN_STRING
from tests.temporal.conftest import make_run_id, seed_run

_skip_no_db = pytest.mark.skipif(
    not POSTGRES_CONN_STRING,
    reason="POSTGRES_CONN_STRING not set — skipping DB-dependent temporal tests",
)


@pytest.mark.integration
@_skip_no_db
async def test_clarification_signal_denied_403_under_privileged(mint_token):
    """Developer permissions (no artifact:approve_requirements) -> 403, handle.signal NOT called."""
    try:
        import process_api  # type: ignore[import]
    except (ImportError, Exception) as exc:
        pytest.xfail(f"process_api not importable: {exc}")

    from httpx import AsyncClient, ASGITransport

    run_id = make_run_id()
    tenant_id = "00000000-0000-0000-0000-0000000010a1"

    await seed_run(
        run_id,
        tenant_id=tenant_id,
        temporal_workflow_id=f"wf-{run_id}",
    )

    developer_token = mint_token(
        user_id="developer-001",
        tenant_id=tenant_id,
        permissions=["run:create", "artifact:view"],
    )

    mock_handle = AsyncMock()
    mock_handle.signal = AsyncMock()

    with patch.object(
        process_api.app.state,  # type: ignore[attr-defined]
        "temporal_client",
        new_callable=MagicMock,
        create=True,
    ) as mock_client:
        mock_client.get_workflow_handle.return_value = mock_handle

        async with AsyncClient(
            transport=ASGITransport(app=process_api.app),  # type: ignore[attr-defined]
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                f"/runs/{run_id}/signals/within_agent_clarification",
                json={"payload": {"clarification_id": str(uuid.uuid4()), "answer": "the scope is X"}},
                headers={"Authorization": f"Bearer {developer_token}"},
            )

        assert response.status_code == 403, (
            f"REQ-M10-05: under-privileged actor must receive 403, got "
            f"{response.status_code}. Body: {response.text}"
        )

    mock_handle.signal.assert_not_called()


@pytest.mark.integration
@_skip_no_db
async def test_clarification_signal_denied_404_cross_tenant(mint_token):
    """A token whose tenant_id differs from the seeded run's tenant -> 404."""
    try:
        import process_api  # type: ignore[import]
    except (ImportError, Exception) as exc:
        pytest.xfail(f"process_api not importable: {exc}")

    from httpx import AsyncClient, ASGITransport

    run_id = make_run_id()
    run_tenant_id = "00000000-0000-0000-0000-0000000010a2"
    other_tenant_id = "00000000-0000-0000-0000-0000000010a3"

    await seed_run(
        run_id,
        tenant_id=run_tenant_id,
        temporal_workflow_id=f"wf-{run_id}",
    )

    other_tenant_token = mint_token(
        user_id="pm-001",
        tenant_id=other_tenant_id,
        permissions=["artifact:approve_requirements"],
    )

    mock_handle = AsyncMock()
    mock_handle.signal = AsyncMock()

    with patch.object(
        process_api.app.state,  # type: ignore[attr-defined]
        "temporal_client",
        new_callable=MagicMock,
        create=True,
    ) as mock_client:
        mock_client.get_workflow_handle.return_value = mock_handle

        async with AsyncClient(
            transport=ASGITransport(app=process_api.app),  # type: ignore[attr-defined]
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                f"/runs/{run_id}/signals/within_agent_clarification",
                json={"payload": {"clarification_id": str(uuid.uuid4()), "answer": "the scope is X"}},
                headers={"Authorization": f"Bearer {other_tenant_token}"},
            )

        assert response.status_code == 404, (
            f"T-M5-18: cross-tenant clarification injection must receive 404, got "
            f"{response.status_code}. Body: {response.text}"
        )

    mock_handle.signal.assert_not_called()


@pytest.mark.integration
@_skip_no_db
async def test_clarification_signal_authorized_dispatch_200(mint_token):
    """product_manager permissions (artifact:approve_requirements) -> 200, handle.signal dispatched."""
    try:
        import process_api  # type: ignore[import]
    except (ImportError, Exception) as exc:
        pytest.xfail(f"process_api not importable: {exc}")

    from httpx import AsyncClient, ASGITransport
    from shared.models.workflow_models import HITLSignal

    run_id = make_run_id()
    tenant_id = "00000000-0000-0000-0000-0000000010a4"
    clarification_id = str(uuid.uuid4())
    answer_text = "the scope is X"

    await seed_run(
        run_id,
        tenant_id=tenant_id,
        temporal_workflow_id=f"wf-{run_id}",
    )

    pm_token = mint_token(
        user_id="pm-001",
        tenant_id=tenant_id,
        permissions=["artifact:approve_requirements"],
    )

    mock_handle = AsyncMock()
    mock_handle.signal = AsyncMock()

    with patch.object(
        process_api.app.state,  # type: ignore[attr-defined]
        "temporal_client",
        new_callable=MagicMock,
        create=True,
    ) as mock_client, patch(
        "shared.routers.signals.audit_service.emit_blocking",
        new_callable=AsyncMock,
    ) as mock_audit:
        mock_client.get_workflow_handle.return_value = mock_handle
        mock_audit.return_value = None

        async with AsyncClient(
            transport=ASGITransport(app=process_api.app),  # type: ignore[attr-defined]
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                f"/runs/{run_id}/signals/within_agent_clarification",
                json={"payload": {"clarification_id": clarification_id, "answer": answer_text}},
                headers={"Authorization": f"Bearer {pm_token}"},
            )

        assert response.status_code == 200, (
            f"REQ-M10-05: authorized actor must receive 200, got "
            f"{response.status_code}. Body: {response.text}"
        )
        body = response.json()
        assert body.get("accepted") is True

    mock_handle.signal.assert_awaited_once()
    call_args = mock_handle.signal.await_args
    assert call_args.args[0] == "within_agent_clarification"
    dispatched_signal = call_args.args[1]
    assert isinstance(dispatched_signal, HITLSignal)
    assert dispatched_signal.payload.get("clarification_id") == clarification_id
    assert dispatched_signal.payload.get("answer") == answer_text
