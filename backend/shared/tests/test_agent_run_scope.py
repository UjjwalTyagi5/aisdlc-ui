"""Unit tests for the shared agent_run_scope context manager (spec Part B).

Mirrors the credential-hygiene contextvar pattern in
shared/tests/test_m3_credential_hygiene.py: a connector is live inside the scope
and released (get_connector raises) after it, even on error, and the scope never
raises when tenant_id is absent.
"""
from unittest.mock import MagicMock

import pytest

from config.connectors.context import get_connector
from shared.services.agent_run import agent_run_scope, AgentRunScope


@pytest.mark.asyncio
async def test_scope_sets_and_clears_connector(monkeypatch):
    mock_connector = MagicMock()

    async def _fake_kind(tenant_id, project_id, agent_id):
        return "azure_devops"

    async def _fake_get(kind="azure_devops", tenant_id=""):
        assert kind == "azure_devops"
        assert tenant_id == "tenant-123"
        return mock_connector

    monkeypatch.setattr("shared.services.agent_run._stage_board_kind", _fake_kind)
    monkeypatch.setattr("shared.services.agent_run.get_connector_for_session", _fake_get)

    async with agent_run_scope(
        agent_id="requirements", tenant_id="tenant-123", session_id="sess-1",
        project_id="proj-1",
    ) as scope:
        assert isinstance(scope, AgentRunScope)
        assert scope.connector_injected is True
        assert get_connector() is mock_connector

    # Released after the scope (REQ-M3-10).
    with pytest.raises(RuntimeError):
        get_connector()


@pytest.mark.asyncio
async def test_scope_no_tenant_is_noop(monkeypatch):
    called = {"n": 0}

    async def _fake_get(kind="azure_devops", tenant_id=""):
        called["n"] += 1
        return MagicMock()

    monkeypatch.setattr("shared.services.agent_run.get_connector_for_session", _fake_get)

    async with agent_run_scope(
        agent_id="requirements", tenant_id=None, session_id=None
    ) as scope:
        assert scope.connector_injected is False
        # No connector set → get_connector still raises inside the scope.
        with pytest.raises(RuntimeError):
            get_connector()

    assert called["n"] == 0  # never tried to resolve a connector without a tenant


@pytest.mark.asyncio
async def test_scope_empty_tenant_string_is_noop(monkeypatch):
    async def _boom(kind="azure_devops", tenant_id=""):
        raise AssertionError("should not be called for empty tenant_id")

    monkeypatch.setattr("shared.services.agent_run.get_connector_for_session", _boom)

    async with agent_run_scope(
        agent_id="requirements", tenant_id="", session_id="sess-1"
    ) as scope:
        assert scope.connector_injected is False


@pytest.mark.asyncio
async def test_scope_resolution_failure_is_failsoft(monkeypatch):
    async def _fake_kind(tenant_id, project_id, agent_id):
        return "azure_devops"

    async def _raise(kind="azure_devops", tenant_id=""):
        raise RuntimeError("secret store down")

    monkeypatch.setattr("shared.services.agent_run._stage_board_kind", _fake_kind)
    monkeypatch.setattr("shared.services.agent_run.get_connector_for_session", _raise)

    # Must NOT propagate — board tools fail closed individually instead.
    async with agent_run_scope(
        agent_id="requirements", tenant_id="tenant-123", session_id="sess-1",
        project_id="proj-1",
    ) as scope:
        assert scope.connector_injected is False
        with pytest.raises(RuntimeError):
            get_connector()


@pytest.mark.asyncio
async def test_scope_clears_connector_on_error(monkeypatch):
    mock_connector = MagicMock()

    async def _fake_kind(tenant_id, project_id, agent_id):
        return "azure_devops"

    async def _fake_get(kind="azure_devops", tenant_id=""):
        return mock_connector

    monkeypatch.setattr("shared.services.agent_run._stage_board_kind", _fake_kind)
    monkeypatch.setattr("shared.services.agent_run.get_connector_for_session", _fake_get)

    with pytest.raises(ValueError):
        async with agent_run_scope(
            agent_id="requirements", tenant_id="tenant-123", session_id="sess-1",
            project_id="proj-1",
        ):
            assert get_connector() is mock_connector
            raise ValueError("boom inside scope")

    with pytest.raises(RuntimeError):
        get_connector()


def test_pick_board_kind_prefers_non_ado():
    from shared.services.agent_run import _pick_board_kind

    assert _pick_board_kind([]) is None
    assert _pick_board_kind(["azure_devops"]) == "azure_devops"  # sole board → used
    assert _pick_board_kind(["jira"]) == "jira"
    # ADO is lowest priority when another provider is also assigned (either order).
    assert _pick_board_kind(["azure_devops", "jira"]) == "jira"
    assert _pick_board_kind(["jira", "azure_devops"]) == "jira"
    assert _pick_board_kind(["azure_devops", "github_issues"]) == "github_issues"


@pytest.mark.asyncio
async def test_scope_no_board_assigned_injects_nothing(monkeypatch):
    """A stage with no assigned board must NOT silently fall back to azure_devops."""
    async def _no_board(tenant_id, project_id, agent_id):
        return None

    async def _boom(kind="azure_devops", tenant_id=""):
        raise AssertionError("must not resolve a connector when no board is assigned")

    monkeypatch.setattr("shared.services.agent_run._stage_board_kind", _no_board)
    monkeypatch.setattr("shared.services.agent_run.get_connector_for_session", _boom)

    async with agent_run_scope(
        agent_id="requirements", tenant_id="tenant-123", session_id="sess-1",
        project_id="proj-1",
    ) as scope:
        assert scope.connector_injected is False
        with pytest.raises(RuntimeError):
            get_connector()  # board tools fail closed — no ADO fallback


@pytest.mark.asyncio
async def test_scope_context_block_empty_for_requirements(monkeypatch):
    async def _fake_get(kind="azure_devops", tenant_id=""):
        return MagicMock()

    monkeypatch.setattr("shared.services.agent_run.get_connector_for_session", _fake_get)

    async with agent_run_scope(
        agent_id="requirements", tenant_id="tenant-123", session_id="sess-1"
    ) as scope:
        # Requirements declares no input_artifacts → build_context returns "".
        assert scope.context_block == ""
