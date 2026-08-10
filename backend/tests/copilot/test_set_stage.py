"""POST /runs/{run_id}/copilot/set-stage — repoint a run's active stage (E1).

Lets the Copilot left rail jump the run back to ANY prior stage (e.g. re-activate
Development after the pipeline has already completed) without a Temporal signal —
mirrors copilot_advance's conversational-mutation pattern (shared/routers/runs.py).

Calls the router coroutine directly (not via ASGITransport/httpx): a pre-existing
test-isolation hazard in this suite means running ANY async test ahead of an
ASGITransport-based request against `process_api.app` in the SAME session can break
subsequent requests through the app (reproduced independently against the
pre-existing tests/routers/test_dev_prs.py — NOT something introduced by this
endpoint). Calling the endpoint coroutine directly exercises the exact same
validation/RBAC/mutation code path without depending on the JWT/CORS middleware
stack, and sidesteps that hazard entirely.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


TENANT_ID = "00000000-0000-0000-0000-e1e100000001"
RUN_ID = "00000000-0000-0000-0000-e1e1000f0001"
PROJECT_ID = "00000000-0000-0000-0000-e1e1000f0002"


def _make_run() -> MagicMock:
    run = MagicMock()
    run.id = uuid.UUID(RUN_ID)
    run.project_id = uuid.UUID(PROJECT_ID)
    run.tenant_id = uuid.UUID(TENANT_ID)
    run.current_stage = "testing"
    run.status = "complete"
    run.gate_pending = False
    return run


def _make_db(run) -> MagicMock:
    """Mock AsyncSession: execute(...).scalar_one_or_none() -> run; commit/refresh/add no-ops."""
    session = MagicMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = run
    session.execute = AsyncMock(return_value=result_mock)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


def _make_request(permissions: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            tenant_id=TENANT_ID,
            permissions=permissions,
            user_id="test-user-e1",
        )
    )


@pytest.mark.unit
async def test_set_stage_org_admin_jumps_to_any_stage():
    """org_admin (admin:*) can set current_stage to a valid stage, incl. jumping backward."""
    from shared.routers.runs import CopilotSetStageIn, copilot_set_stage

    run = _make_run()
    db = _make_db(run)
    request = _make_request(["admin:*"])

    out = await copilot_set_stage(RUN_ID, CopilotSetStageIn(stage="development"), request, db)

    assert out.current_stage == "development"
    assert out.status == "running"
    # Mutation actually landed on the Run row, not just the response body.
    assert run.current_stage == "development"
    assert run.status == "running"
    assert run.gate_pending is False
    db.commit.assert_awaited_once()


@pytest.mark.unit
async def test_set_stage_unknown_stage_returns_400():
    """A stage not in STAGE_ORDER is rejected with 400 before any RBAC check or mutation."""
    from shared.routers.runs import CopilotSetStageIn, copilot_set_stage

    run = _make_run()
    db = _make_db(run)
    request = _make_request(["admin:*"])

    with pytest.raises(HTTPException) as exc_info:
        await copilot_set_stage(RUN_ID, CopilotSetStageIn(stage="not_a_real_stage"), request, db)

    assert exc_info.value.status_code == 400
    # No mutation happened.
    assert run.current_stage == "testing"
    db.commit.assert_not_awaited()


@pytest.mark.unit
async def test_set_stage_non_permitted_caller_gets_403():
    """A developer (lacks artifact:approve_development) is denied BEFORE any mutation."""
    from shared.routers.runs import CopilotSetStageIn, copilot_set_stage

    run = _make_run()
    db = _make_db(run)
    # developer role perms per _ROLE_PERMISSIONS: run:create/view, artifact:view/export,
    # connector:view — NOT artifact:approve_development.
    request = _make_request(["run:create", "run:view", "artifact:view", "artifact:export"])

    with pytest.raises(HTTPException) as exc_info:
        await copilot_set_stage(RUN_ID, CopilotSetStageIn(stage="development"), request, db)

    assert exc_info.value.status_code == 403
    # No mutation happened — fail-closed, before any state change.
    assert run.current_stage == "testing"
    assert run.status == "complete"
    db.commit.assert_not_awaited()
