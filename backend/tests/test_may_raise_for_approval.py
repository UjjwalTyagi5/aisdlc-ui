"""Who may send a document for approval — one rule for the Documents button and the chat.

Product decision (2026-09-16): whoever holds `run:create` on the project, OR whoever may use
the agent the document belongs to (its owning role, or an extra-agent grant). QA could
approve Testing documents but not send one, and security@gmail.com — a QA with Code Review
granted — could run a review but not raise its report.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

T, P, U = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", "u1"


async def _may(*, permissions, stage, uses_agent):
    from shared.services.artifact_approval import may_raise_for_approval

    access = AsyncMock(return_value=uses_agent)
    with patch("shared.authz.effective_role.platform_role_for", AsyncMock(return_value="qa")), \
         patch("shared.authz.agent_access.check_agent_access", access):
        allowed = await may_raise_for_approval(
            None, tenant_id=T, project_id=P, user_id=U, stage=stage, permissions=permissions,
        )
    return allowed, access


@pytest.mark.asyncio
async def test_run_create_raises_anything_without_consulting_agent_access():
    allowed, access = await _may(permissions=["run:create"], stage="code_review", uses_agent=False)
    assert allowed is True
    access.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_user_of_the_documents_agent_may_raise_it():
    allowed, access = await _may(permissions=["artifact:view"], stage="code_review", uses_agent=True)
    assert allowed is True
    assert access.await_args.kwargs["agent_id"] == "code_review"


@pytest.mark.asyncio
async def test_neither_run_create_nor_the_agent_is_refused():
    allowed, _ = await _may(permissions=["artifact:view"], stage="testing", uses_agent=False)
    assert allowed is False


@pytest.mark.asyncio
async def test_a_project_wide_document_belongs_to_no_agent():
    allowed, access = await _may(permissions=["artifact:view"], stage=None, uses_agent=True)
    assert allowed is False
    access.assert_not_awaited()


def test_the_route_decides_in_the_body_with_the_shared_rule():
    from shared.routers import artifacts

    src = inspect.getsource(artifacts.submit_artifact)
    assert "may_raise_for_approval(" in src
    decorator = inspect.getsource(artifacts).split("async def submit_artifact")[0].rsplit("@artifacts_router.post", 1)[1]
    assert 'require_permission("run:create")' not in decorator
