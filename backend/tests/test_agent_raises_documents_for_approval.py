"""The agent can raise a draft for approval — what the Documents button does, nothing more.

"Okay, can you send it for approval?" was answered "I cannot — that step must be performed
by an owner or project admin". Raising is `run:create`, the permission of whoever ran the
agent — and, since 2026-09-16, of whoever may use the agent the document belongs to;
approving is the approver's. These pin the tool to the button's behaviour: the same
shared implementation, the user's own permission, this stage's documents only, and a
decided document never reopened.
"""
from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit

PROJECT = str(uuid4())
TENANT = str(uuid4())
USER = str(uuid4())


def _doc(name: str, status: str = "draft", stage: str | None = "requirements"):
    return SimpleNamespace(
        id=uuid4(), project_id=PROJECT, stage=stage, artifact_type="document",
        approval_status=status, blob_path=f"{TENANT}/bu/{PROJECT}/{stage}/run/document/{name}",
        created_at=datetime.now(timezone.utc),
    )


def _fake_db():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.fixture
def turn():
    from config.ws_helper import set_project_id, set_tenant_id, set_user_id

    set_project_id(PROJECT)
    set_tenant_id(TENANT)
    set_user_id(USER)
    db = _fake_db()

    @asynccontextmanager
    async def _session(tenant_id):
        yield db

    with patch("shared.db.get_db_session_for_tenant", _session), \
         patch("shared.authz.audit.capture_names", AsyncMock(side_effect=lambda db, payload, **k: payload)):
        yield db


async def _raise(name, docs, *, allowed=True, uses_agent=False, raisable=()):
    from shared.tools import document_approval as da

    with patch("shared.authz.can_perform.can_perform", AsyncMock(return_value=allowed)) as perm, \
         patch("shared.authz.effective_role.platform_role_for", AsyncMock(return_value="qa")), \
         patch("shared.authz.agent_access.check_agent_access", AsyncMock(return_value=uses_agent)), \
         patch.object(da, "_raisable_names", AsyncMock(return_value=list(raisable))), \
         patch.object(da, "_documents_named", AsyncMock(return_value=docs)):
        out = await da.raise_for_approval(name, stage="requirements")
    return out, perm


# ── the shared act ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_draft_moves_to_pending_and_is_audited(turn):
    from shared.services.artifact_approval import submit_for_approval

    doc = _doc("TEST_Project_BRD.docx")
    assert await submit_for_approval(turn, doc, tenant_id=TENANT, actor_id=USER) is True
    assert doc.approval_status == "pending"
    event = turn.add.call_args.args[0]
    assert event.event_type == "artifact_submit"
    assert event.payload["before"] == "draft" and event.payload["after"] == "pending"


@pytest.mark.asyncio
async def test_pending_is_a_no_op_and_a_decision_is_never_reopened(turn):
    from shared.services.artifact_approval import AlreadyDecided, submit_for_approval

    pending = _doc("a.docx", "pending")
    assert await submit_for_approval(turn, pending, tenant_id=TENANT, actor_id=USER) is False
    turn.add.assert_not_called()
    for status in ("approved", "rejected"):
        with pytest.raises(AlreadyDecided):
            await submit_for_approval(turn, _doc("b.docx", status), tenant_id=TENANT, actor_id=USER)


def test_the_button_and_the_tool_share_one_implementation():
    from shared.routers import artifacts
    from shared.tools import document_approval

    assert "submit_for_approval(" in inspect.getsource(artifacts.submit_artifact)
    assert "submit_for_approval(" in inspect.getsource(document_approval.raise_for_approval)


# ── the tool ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_tool_raises_the_draft_with_the_users_permission(turn):
    doc = _doc("TEST_Project_BRD.docx")
    out, perm = await _raise("TEST_Project_BRD.docx", [doc])

    assert out.startswith("Raised 'TEST_Project_BRD.docx' for approval")
    assert "not approved yet" in out
    assert doc.approval_status == "pending"
    kwargs = perm.await_args.kwargs
    assert (kwargs["user_id"], kwargs["permission"], kwargs["resource_kind"], kwargs["resource_id"]) == (
        USER, "run:create", "project", PROJECT,
    )


@pytest.mark.asyncio
async def test_without_permission_nothing_changes(turn):
    doc = _doc("TEST_Project_BRD.docx")
    out, _ = await _raise("TEST_Project_BRD.docx", [doc], allowed=False, uses_agent=False)
    assert out.startswith("Error:") and "access to the requirements agent" in out
    assert doc.approval_status == "draft"


@pytest.mark.asyncio
async def test_whoever_may_use_the_agent_may_send_its_document(turn):
    """QA could approve Testing documents but not send one; a Security Engineer given Code
    Review could not raise its report. Using the agent now carries putting its output
    forward (product decision, 2026-09-16)."""
    doc = _doc("TEST_Project_BRD.docx")
    out, _ = await _raise("TEST_Project_BRD.docx", [doc], allowed=False, uses_agent=True)
    assert out.startswith("Raised 'TEST_Project_BRD.docx' for approval")
    assert doc.approval_status == "pending"


@pytest.mark.asyncio
async def test_a_project_wide_document_still_needs_run_create(turn):
    doc = _doc("Security_Policy.pdf", stage=None)
    out, _ = await _raise("Security_Policy.pdf", [doc], allowed=False, uses_agent=True)
    assert out.startswith("Error:") and "project-wide" in out
    assert doc.approval_status == "draft"


@pytest.mark.asyncio
async def test_an_unknown_name_changes_nothing(turn):
    out, _ = await _raise("nope.docx", [])
    assert out.startswith("Error:") and "nope.docx" in out


@pytest.mark.asyncio
async def test_a_wrong_name_is_answered_with_the_stages_raisable_documents(turn):
    """LIVE: asked to raise "the readiness report", the Deployment agent guessed
    'Deployment Readiness Report.docx' twice — the lookup tool it tried lists approved
    documents only, where a draft never appears."""
    out, _ = await _raise("Deployment Readiness Report.docx", [],
                          raisable=["QuickLink_Deployment_Readiness_staging_main_082f91e.docx"])
    assert "Nothing was changed" in out
    assert "can be raised, newest first: QuickLink_Deployment_Readiness_staging_main_082f91e.docx" in out
    out, _ = await _raise("nope.docx", [])
    assert "no draft document to raise" in out


@pytest.mark.asyncio
async def test_another_stages_document_is_raised_from_its_own_screen(turn):
    doc = _doc("architecture.docx", stage="design")
    out, _ = await _raise("architecture.docx", [doc])
    assert out.startswith("Error:") and "design" in out
    assert doc.approval_status == "draft"


@pytest.mark.asyncio
async def test_an_approved_document_is_not_reopened(turn):
    doc = _doc("QuickLink_BRD_new.docx", "approved")
    out, _ = await _raise("QuickLink_BRD_new.docx", [doc])
    assert out.startswith("Error:") and "already approved" in out
    assert doc.approval_status == "approved"


@pytest.mark.asyncio
async def test_the_draft_is_chosen_over_an_older_decided_copy_of_the_same_name(turn):
    approved = _doc("brief.docx", "approved")
    draft = _doc("brief.docx", "draft")
    out, _ = await _raise("brief.docx", [approved, draft])
    assert out.startswith("Raised")
    assert draft.approval_status == "pending" and approved.approval_status == "approved"


@pytest.mark.asyncio
async def test_a_path_is_reduced_to_the_file_name(turn):
    doc = _doc("TEST_Project_BRD.docx")
    out, _ = await _raise("some/dir/TEST_Project_BRD.docx", [doc])
    assert out.startswith("Raised")


# ── the Requirements agent has it and is told the truth ──────────────────────


def test_the_requirements_agent_can_raise_and_says_so():
    from agents_orchestrator.requirements_agent.agents import planning

    assert "raise_document_for_approval" in {t.name for t in planning.tools}
    flat = " ".join(planning.INGESTION_SYS_MESSAGE.split())
    assert "call raise_document_for_approval" in flat
    assert "never say only an owner or admin can raise it" in flat
    assert "You can NOT approve anything" in flat
