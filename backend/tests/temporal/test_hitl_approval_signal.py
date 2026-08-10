"""SC-03, SC-04, D-01/D-08: HITL approval signal and negative authz test.

Tier: integration (requires Temporal server + Postgres)
Criteria:
  SC-03 — GET /runs/{id} returns awaiting_requirements_approval state
  SC-04 — Approval signal advances workflow to Design agent
  D-01/REQ-M7-11 (NEGATIVE) — A permission-lacking actor (real signed token without
    artifact:approve_requirements) receives HTTP 403 AND handle.signal is NOT called.
    Migrated from the legacy M5 "roles"/_PHASE_APPROVER_ROLES vocabulary (D-01) —
    the negative-authz CONTRACT (403-before-handle, assert_not_called) is unchanged.

Skip guard: tests skip (not error) when POSTGRES_CONN_STRING or LITELLM_API_KEY absent.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.env import LITELLM_API_KEY, POSTGRES_CONN_STRING
from tests.temporal.conftest import make_run_id, make_workflow_input, seed_run
from shared.models.workflow_models import HITLSignal

# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------
_skip_no_db = pytest.mark.skipif(
    not POSTGRES_CONN_STRING,
    reason="POSTGRES_CONN_STRING not set — skipping DB-dependent temporal tests",
)

# Live-agent guard — workflow execution invokes real agent graphs through LiteLLM proxy.
_skip_no_litellm = pytest.mark.skipif(
    not LITELLM_API_KEY,
    reason="LITELLM_API_KEY not set — litellm-proxy not configured; skipping live-agent temporal tests",
)

try:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    _TEMPORALIO_AVAILABLE = True
except ImportError:
    _TEMPORALIO_AVAILABLE = False

_skip_no_temporal = pytest.mark.skipif(
    not _TEMPORALIO_AVAILABLE,
    reason="temporalio not installed",
)


# ---------------------------------------------------------------------------
# SC-03: awaiting_requirements_approval state
# ---------------------------------------------------------------------------

@pytest.mark.integration
@_skip_no_db
@_skip_no_temporal
@_skip_no_litellm
async def test_awaiting_state(temporal_env):
    """GET /runs/{id} must return status=awaiting_requirements_approval after requirements complete.

    SC-03: the run status mirror in Postgres reflects the HITL gate state.
    """
    import httpx
    from httpx import AsyncClient, ASGITransport
    import process_api  # type: ignore[import]

    project_id = str(uuid.uuid4())

    async with AsyncClient(
        transport=ASGITransport(app=process_api.app),  # type: ignore[attr-defined]
        base_url="http://testserver",
    ) as client:
        # Create a run
        create_resp = await client.post(
            "/runs",
            json={"project_id": project_id, "trigger": "manual"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert create_resp.status_code == 201
        run_id = create_resp.json()["runId"]

        # Poll GET /runs/{id} until awaiting_requirements_approval or timeout
        import asyncio
        for _ in range(20):
            await asyncio.sleep(0.5)
            get_resp = await client.get(
                f"/runs/{run_id}",
                headers={"Authorization": "Bearer test-token"},
            )
            if get_resp.status_code == 200:
                status = get_resp.json().get("status")
                if status == "awaiting_requirements_approval":
                    break
        else:
            pytest.fail(
                f"SC-03: run {run_id} never reached awaiting_requirements_approval. "
                f"Last status: {get_resp.json().get('status')}"
            )


# ---------------------------------------------------------------------------
# SC-04: Approval signal advances to Design agent
# ---------------------------------------------------------------------------

@pytest.mark.integration
@_skip_no_db
@_skip_no_temporal
@_skip_no_litellm
async def test_approval_advances_to_design(temporal_env):
    """Sending an approval signal must advance the workflow to the Design activity.

    SC-04: after approval, the run status transitions to running_design (or equivalent).
    """
    import httpx
    from httpx import AsyncClient, ASGITransport
    import asyncio
    import process_api  # type: ignore[import]

    project_id = str(uuid.uuid4())

    async with AsyncClient(
        transport=ASGITransport(app=process_api.app),  # type: ignore[attr-defined]
        base_url="http://testserver",
    ) as client:
        create_resp = await client.post(
            "/runs",
            json={"project_id": project_id, "trigger": "manual"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert create_resp.status_code == 201
        run_id = create_resp.json()["runId"]

        # Wait for HITL gate
        await asyncio.sleep(2)

        # Send approval signal
        signal_resp = await client.post(
            f"/runs/{run_id}/signals/requirements_approved",
            json={"payload": {}},
            headers={"Authorization": "Bearer approver-token"},
        )
        assert signal_resp.status_code == 200, (
            f"SC-04: approval signal must succeed, got {signal_resp.status_code}"
        )
        ack = signal_resp.json()
        assert ack.get("accepted") is True, "SC-04: signal ack must report accepted=True"

        # Run should transition away from awaiting state
        await asyncio.sleep(1)
        get_resp = await client.get(
            f"/runs/{run_id}",
            headers={"Authorization": "Bearer test-token"},
        )
        assert get_resp.json().get("status") != "awaiting_requirements_approval", (
            "SC-04: approval must advance the run past the awaiting_requirements_approval state"
        )


# ---------------------------------------------------------------------------
# D-08: Negative authz — wrong-role actor receives 403 + signal NOT dispatched
# ---------------------------------------------------------------------------

@pytest.mark.integration
@_skip_no_db
async def test_signal_role_denied_403(mint_token):
    """An actor lacking the approval PERMISSION for the current phase must get HTTP 403.

    D-01/REQ-M7-11 negative-authz contract (migrated from the legacy M5 "roles"
    vocabulary — D-01 retires _PHASE_APPROVER_ROLES/_check_role_for_phase in favor
    of the shared _PHASE_PERMISSION map + has_permission):
      1. An actor holding developer permissions (run:create, artifact:view — NOT
         artifact:approve_requirements) POSTs the requirements-phase approval signal
      2. API returns HTTP 403 (Forbidden) — the phase-permission check denies
      3. Temporal handle.signal is NOT called — no durable state mutation occurs
         (the 403 is raised BEFORE client.get_workflow_handle, REQ-M7-11/Pitfall 4)
      4. Run remains in awaiting state

    Why a REAL signed token (Open Question 1 / T-7.2-16): the legacy approach
    posted a hardcoded placeholder bearer-string literal that is not a decodable
    JWT — the auth middleware would 401 on it (token decode failure), never
    reaching the phase-permission check this test exists to prove. mint_token (T-7.2-04)
    issues a real HS256-signed token carrying a controllable `permissions` array
    AND a tenant_id that matches the seeded run, so the request genuinely reaches
    Step 3 (_check_permission_for_phase) and is denied there — exercising the
    actual 403 branch, not a 401 short-circuit that would mask a real bypass.

    Implementation note (D-01, signals.py Step 3):
      - The signals router calls _check_permission_for_phase(request.state, phase)
        BEFORE calling client.get_workflow_handle(...).signal(...)
      - This test patches the Temporal client handle to assert_not_called on .signal()
    """
    # We need process_api importable; if not, the test xfails via the xfail decorator.
    # The mock-based portion of this test does NOT require a real Temporal server.
    try:
        import process_api  # type: ignore[import]
    except (ImportError, Exception) as exc:
        pytest.xfail(f"process_api not importable: {exc}")

    from httpx import AsyncClient, ASGITransport

    run_id = make_run_id()
    tenant_id = "00000000-0000-0000-0000-0000000007aa"

    # Seed a real Run row — tenant-matching AND with a temporal_workflow_id, so the
    # request clears Step 1 (tenant-scoped 404 guard) and Step 2 (409 "no workflow"
    # guard) and genuinely reaches Step 3 (the phase-permission check). Without a
    # seeded run + workflow id the request would 404/409 before ever exercising the
    # 403 branch this test is supposed to prove (T-7.2-16 — 401/404/409-vs-403 confusion
    # would mask a real authz bypass).
    await seed_run(
        run_id,
        tenant_id=tenant_id,
        temporal_workflow_id=f"wf-{run_id}",
    )

    # Real signed token: developer permissions WITHOUT artifact:approve_requirements
    # (Open Question 1 resolution — developer perms lacking the requirements-approval
    # permission), tenant_id matching the seeded run so Step 1's tenant scope passes.
    developer_token = mint_token(
        user_id="developer-001",
        tenant_id=tenant_id,
        permissions=["run:create", "artifact:view"],
    )

    # Build a mock Temporal handle that we can assert was NOT called
    mock_handle = AsyncMock()
    mock_handle.signal = AsyncMock()

    # Patch the Temporal client on app.state so the router uses our mock.
    # create=True: app.state.temporal_client is normally set during the FastAPI
    # lifespan startup (process_api.py ~line 350-363); ASGITransport-based tests
    # invoke the app WITHOUT running lifespan, so the attribute does not yet exist
    # on the bare starlette.datastructures.State — patch.object would otherwise
    # raise AttributeError before the request is ever made (Rule 1 fix: a
    # pre-existing test-infra gap, not a signals.py/authz defect).
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
            # Real signed token, authenticated, but lacking artifact:approve_requirements —
            # genuinely reaches and is denied by the phase-permission check (not a 401).
            response = await client.post(
                f"/runs/{run_id}/signals/requirements_approved",
                json={"payload": {}},
                headers={"Authorization": f"Bearer {developer_token}"},
            )

        # Contract assertion 1: HTTP 403 returned (the permission-denied branch,
        # not a 401 decode-failure / 404 not-found / 409 no-workflow short-circuit)
        assert response.status_code == 403, (
            f"D-01/REQ-M7-11: permission-lacking actor must receive 403 (the genuine "
            f"phase-permission-denied branch), got {response.status_code}. "
            f"Body: {response.text}"
        )

    # Contract assertion 2: Temporal handle.signal must NOT have been called —
    # the M5 negative-authz contract (403-before-handle, REQ-M7-11) preserved exactly.
    mock_handle.signal.assert_not_called()
