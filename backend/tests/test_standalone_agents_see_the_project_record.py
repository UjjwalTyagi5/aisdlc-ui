"""Every standalone agent sees the project's approved documents — by construction.

THE LIVE FAILURE (15 Sep 2026). A developer opened the Development agent from inside
TEST Project's own page and asked whether the project had approved artifacts. The
agent answered "No approved artifacts in this session" and quoted its tool:
"This conversation is not attached to a project". The project had three.

Two causes, one for each half of "attached":

  * the tools read the turn's project from `config.ws_helper` contextvars, and six of
    the standalone handlers (development, testing, code review, security, deployment,
    monitoring) never set them — only design, requirements and PM did, each by hand;
  * nothing in the agent's prompt said the record existed, so even with the tools
    working the model had no reason to look.

The fix binds the turn's project in the one place every handler already passes
through — `assert_agent_access_for_chat`, the per-turn access gate — and lists the
approved documents in the standalone prompt layer for every agent that binds the
reading tools. What an agent knows about the project's record no longer depends on
which handler the user came through, and a handler written next month inherits both.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from config import ws_helper
from shared.authz import agent_access
from shared.services import standalone_prompt as sp
from shared.tools import project_documents as pd

TENANT = "dfee0d2f-345e-430e-8084-7ab7276cc5b8"
PROJECT = "ed20b947-360d-4881-9a83-52decc68210a"


@pytest.fixture(autouse=True)
def _clear_turn_context():
    ws_helper.set_tenant_id(None)
    ws_helper.set_project_id(None)
    yield
    ws_helper.set_tenant_id(None)
    ws_helper.set_project_id(None)


# ── the gate binds the turn ──────────────────────────────────────────────────


async def test_the_access_gate_binds_the_resolved_project_for_the_turns_tools():
    project = SimpleNamespace(id=uuid.UUID(PROJECT))
    with patch.object(agent_access, "_resolve_member_project", AsyncMock(return_value=project)), \
            patch.object(agent_access, "platform_role_for", AsyncMock(return_value="developer")), \
            patch.object(agent_access, "assert_agent_access", AsyncMock()):
        resolved = await agent_access.assert_agent_access_for_chat(
            db=object(), tenant_id=TENANT, project_id="test-project", user_id="dev", agent_id="development",
        )

    assert resolved == PROJECT
    assert ws_helper.get_project_id() == PROJECT, "the tools read the project from here"
    assert ws_helper.get_tenant_id() == TENANT


async def test_a_denied_turn_binds_nothing():
    from fastapi import HTTPException

    with patch.object(agent_access, "_resolve_member_project", AsyncMock(side_effect=HTTPException(404))):
        with pytest.raises(HTTPException):
            await agent_access.assert_agent_access_for_chat(
                db=object(), tenant_id=TENANT, project_id="other", user_id="dev", agent_id="development",
            )
    assert ws_helper.get_project_id() is None


async def test_the_track_gate_binds_it_too():
    project = SimpleNamespace(id=uuid.UUID(PROJECT))
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: "code_modernization")))
    with patch.object(agent_access, "_resolve_member_project", AsyncMock(return_value=project)), \
            patch.object(agent_access, "platform_role_for", AsyncMock(return_value="business_analyst")), \
            patch.object(agent_access, "assert_agent_access", AsyncMock()), \
            patch("config.agent_registry.TRACK_PORTFOLIOS", {"code_modernization": ("requirements_modernization",)}):
        await agent_access.assert_agent_access_for_chat_on_track(
            db=db, tenant_id=TENANT, project_id=PROJECT, user_id="ba", agent_id="requirements_modernization",
        )
    assert ws_helper.get_project_id() == PROJECT


async def test_the_document_tools_answer_for_the_bound_project(monkeypatch):
    """End to end through the tool: once the gate has bound the turn, the tool that
    said "not attached to a project" lists the project's approved documents."""
    docs = [{"id": "d1", "title": "QuickLink_BRD_new.docx", "stage": "requirements", "approvedBy": "Sarthak"}]

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("shared.db.get_db_session_for_tenant", lambda tenant: _Session())
    monkeypatch.setattr("shared.services.artifact_versions.readable_documents", AsyncMock(return_value=docs))
    list_tool = {t.name: t for t in pd.make_document_tools("development")}["list_project_documents"]

    before = await list_tool.ainvoke({})
    assert "not attached to a project" in before

    ws_helper.bind_turn_project(TENANT, PROJECT)
    after = await list_tool.ainvoke({})
    assert "QuickLink_BRD_new.docx" in after


# ── the prompt names the record ──────────────────────────────────────────────


BLOCK = "--- APPROVED DOCUMENTS IN THIS PROJECT ---\n- **QuickLink_BRD_new.docx** — id `d1`\n--- END APPROVED DOCUMENTS IN THIS PROJECT ---"


@pytest.fixture
def documents_block():
    with patch("agents_orchestrator.orchestrator2.project_documents.approved_documents_context",
               AsyncMock(return_value=BLOCK)) as m:
        yield m


async def test_an_agent_with_the_reading_tools_is_told_what_the_project_holds(documents_block):
    pd.make_document_tools("development")  # registers, as the agent module does at import
    out = await sp.with_approved_documents("development", "You are the Development agent.", TENANT, PROJECT)
    assert out.startswith("You are the Development agent.")
    assert BLOCK in out
    documents_block.assert_awaited_once_with(PROJECT, TENANT)


async def test_an_agent_without_the_tools_is_not_sent_after_a_tool_it_lacks(documents_block):
    out = await sp.with_approved_documents("no_such_agent", "Base prompt.", TENANT, PROJECT)
    assert out == "Base prompt."
    documents_block.assert_not_awaited()


async def test_no_project_means_no_block(documents_block):
    pd.make_document_tools("development")
    assert await sp.with_approved_documents("development", "Base.", TENANT, None) == "Base."
    assert await sp.with_approved_documents("development", "Base.", None, PROJECT) == "Base."


async def test_an_empty_record_adds_nothing():
    pd.make_document_tools("development")
    with patch("agents_orchestrator.orchestrator2.project_documents.approved_documents_context",
               AsyncMock(return_value="")):
        assert await sp.with_approved_documents("development", "Base.", TENANT, PROJECT) == "Base."


async def test_a_failed_read_is_said_not_hidden_as_none():
    pd.make_document_tools("development")
    with patch("agents_orchestrator.orchestrator2.project_documents.approved_documents_context",
               AsyncMock(side_effect=RuntimeError("db down"))):
        out = await sp.with_approved_documents("development", "Base.", TENANT, PROJECT)
    assert "could not be listed" in out
    assert "list_project_documents" in out


async def test_resolve_agent_turn_carries_the_block_for_every_standalone_surface(documents_block):
    """The one hook every standalone handler calls — message-prompt agents on their
    first turn, self-inject agents on every turn — is where the block rides."""
    pd.make_document_tools("code_review")
    with patch.object(sp, "prepare_agent_turn", AsyncMock(return_value=("Reviewer prompt.", [], None))):
        injected, skills = await sp.resolve_agent_turn("code_review", "Reviewer prompt.", TENANT, PROJECT)
    assert injected.startswith("Reviewer prompt.")
    assert BLOCK in injected
    assert skills == []


def test_every_agent_that_registers_the_tools_is_known_to_the_prompt_layer():
    """Import the agent modules the standalone pages serve; each must have registered
    its stage, or its prompt will never mention the record."""
    import agents_orchestrator.code_review_agent.agents.reviewer  # noqa: F401
    import agents_orchestrator.deployment_agent.agents.deployer  # noqa: F401
    import agents_orchestrator.development_agent.agents.dev_agent  # noqa: F401
    import agents_orchestrator.documentation_agent.agents.compiler  # noqa: F401
    import agents_orchestrator.security_agent.agents.scanner  # noqa: F401
    import agents_orchestrator.testing_agent.Nodes.ingest_input  # noqa: F401
    import agents_orchestrator.design_architecture_agent.agents.architecture  # noqa: F401

    for stage in ("development", "code_review", "deployment", "documentation", "security", "testing", "design"):
        assert pd.has_document_tools(stage), stage
