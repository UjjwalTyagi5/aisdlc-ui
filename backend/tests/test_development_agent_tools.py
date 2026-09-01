"""Direct tool-level tests for the Development agent — no LLM/graph involved.
Proves the HITL approval gate (spec 3.2) actually covers all 5 Consequential-tier
tools PRD §21.3 groups under one Architect approver, not just push_branch/create_pr."""
import pytest

from agents_orchestrator.development_agent.config.session_state import get_session, clear_session
from config.ws_helper import set_session_id

pytestmark = pytest.mark.asyncio


@pytest.fixture
def gated_session():
    session_id = "dev-tools-gate-test"
    set_session_id(session_id)
    s = get_session(session_id)
    s.push_gate_enabled = True
    s.push_approved = False
    s.ado_org_url = "https://dev.azure.com/fake-org"
    s.pat = "fake-pat"
    yield s
    clear_session(session_id)


async def test_create_ado_repo_refuses_without_approval(gated_session):
    from agents_orchestrator.development_agent.tools.git_tools import create_ado_repo

    result = await create_ado_repo.ainvoke({"project": "FakeProject", "repo_name": "fake-repo"})
    assert "NOT CREATED" in result or "awaiting" in result.lower()


async def test_update_work_item_state_refuses_without_approval(gated_session):
    from agents_orchestrator.development_agent.tools.git_tools import update_work_item_state

    result = await update_work_item_state.ainvoke(
        {"project": "FakeProject", "work_item_ids": [123], "target_state": "Done"}
    )
    assert "NOT UPDATED" in result or "awaiting" in result.lower()


async def test_add_pr_comment_to_work_items_refuses_without_approval(gated_session):
    from agents_orchestrator.development_agent.tools.git_tools import add_pr_comment_to_work_items

    result = await add_pr_comment_to_work_items.ainvoke(
        {"project": "FakeProject", "work_item_ids": [123], "pr_url": "https://example.com/pr/1"}
    )
    assert "NOT ADDED" in result or "awaiting" in result.lower()


async def test_update_work_item_state_succeeds_once_approved(monkeypatch, gated_session):
    """Once approved, the tool must still run its real logic (reach the connector) —
    proves the gate is additive, not a replacement for the tool's own behavior."""
    from agents_orchestrator.development_agent.tools import git_tools

    gated_session.push_approved = True

    class _FakeConnector:
        async def write_adapter(self, action, **kwargs):
            assert action == "move_item_state"
            return {"new_state": "Done"}

    monkeypatch.setattr(git_tools, "get_active_connector", lambda: _FakeConnector())

    result = await git_tools.update_work_item_state.ainvoke(
        {"project": "FakeProject", "work_item_ids": [123], "target_state": "Done"}
    )
    assert "123" in result and "Done" in result
