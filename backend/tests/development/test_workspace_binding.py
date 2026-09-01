"""Tests for workspace binding and pipeline_context project_id resolution.

Part 1 (TestGetWorkDirHonoursPresetWorkDir):
  Guard test for B3: _get_work_dir() honours a pre-set s.work_dir.
  No network, DB, or filesystem calls are made.

Part 2 (TestProjectIdFromMessage, test_bind_pulled_workspace_with_pipeline_context):
  Regression test for C1 fix: _project_id_from_message must resolve project_id
  from pipeline_context, which is the only key present in the live WS payload.
  Pre-fix both helpers read the wrong keys so project_id was always None.
"""
from __future__ import annotations

import json
import uuid

import pytest

from agents_orchestrator.development_agent.config.session_state import (
    clear_session,
    get_session,
)
from agents_orchestrator.development_agent.tools.git_tools import _get_work_dir
from config.ws_helper import set_session_id, set_user_id
from shared.db import RESOLVED_POSTGRES_CONN_STRING as _DB_URL


# -- Part 1: _get_work_dir pre-set path guard ---------------------------------

class TestGetWorkDirHonoursPresetWorkDir:
    def setup_method(self):
        self._sid = "test-session-workspace-binding-001"
        set_user_id("test-user-workspace-binding")
        set_session_id(self._sid)
        clear_session(self._sid)

    def teardown_method(self):
        clear_session(self._sid)

    def test_returns_preset_work_dir(self, tmp_path):
        expected = str(tmp_path / "pulled_repo")
        s = get_session(self._sid)
        s.work_dir = expected
        result = _get_work_dir()
        assert result == expected

    def test_returns_preset_work_dir_regardless_of_existence(self):
        fake_path = "/tmp/some/non/existent/workspace"
        s = get_session(self._sid)
        s.work_dir = fake_path
        result = _get_work_dir()
        assert result == fake_path

    def test_does_not_return_preset_when_empty(self, tmp_path):
        s = get_session(self._sid)
        s.work_dir = ""
        result = _get_work_dir()
        assert result != ""
        assert "project" in result


# -- Part 2: pipeline_context project_id resolution (C1 fix) ------------------

_skip_no_db = pytest.mark.skipif(
    not _DB_URL or _DB_URL.endswith("sdlc_agentic"),
    reason="Live Postgres not configured",
)

_TENANT_ID = uuid.UUID("00000000-c1c1-0000-0001-000000000001")
_ORG_ID    = uuid.UUID("00000000-c1c1-0000-0001-000000000010")
_WS_ID     = uuid.UUID("00000000-c1c1-0000-0001-000000000011")
_PROJ_ID   = uuid.UUID("00000000-c1c1-0000-0001-000000000012")


class TestProjectIdFromMessage:
    def _helper(self):
        from agents_orchestrator.development_agent.development_agent_api import _project_id_from_message
        return _project_id_from_message

    def test_reads_from_pipeline_context(self):
        fn = self._helper()
        pid = str(_PROJ_ID)
        msg = {"pipeline_context": {"page": "Development", "project_id": pid}}
        assert fn(msg) == pid

    def test_reads_from_top_level_project_id(self):
        fn = self._helper()
        pid = str(_PROJ_ID)
        msg = {"project_id": pid}
        assert fn(msg) == pid

    def test_reads_from_context_dict(self):
        fn = self._helper()
        pid = str(_PROJ_ID)
        msg = {"context": {"project_id": pid}}
        assert fn(msg) == pid

    def test_returns_none_when_absent(self):
        fn = self._helper()
        assert fn({}) is None

    def test_top_level_takes_precedence_over_pipeline_context(self):
        fn = self._helper()
        msg = {"project_id": "top-level-id", "pipeline_context": {"project_id": "pipeline-id"}}
        assert fn(msg) == "top-level-id"

    def test_pipeline_context_as_json_string(self):
        fn = self._helper()
        pid = str(_PROJ_ID)
        msg = {"pipeline_context": json.dumps({"project_id": pid})}
        assert fn(msg) == pid


@pytest.fixture(scope="module")
async def _seeded_workspace():
    from sqlalchemy import text
    from shared.db import get_db_session_for_tenant
    from shared.services import dev_workspace_store

    async with get_db_session_for_tenant(str(_TENANT_ID)) as db:
        await db.execute(
            text(
                "INSERT INTO organizations (id, slug, display_name, created_at, updated_at) "
                "VALUES (:id, :slug, :dn, now(), now()) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": str(_ORG_ID), "slug": "c1-test-org", "dn": "C1 Test Org"},
        )
        await db.execute(
            text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name, created_at, updated_at) "
                "VALUES (:id, :org_id, :slug, :dn, now(), now()) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": str(_WS_ID), "org_id": str(_ORG_ID), "slug": "c1-test-ws", "dn": "C1 Test WS"},
        )
        await db.execute(
            text(
                "INSERT INTO projects (id, workspace_id, tenant_id, display_name, provider_kind, "
                "archived, created_at, updated_at) "
                "VALUES (:id, :ws_id, :tid, :dn, 'azure_devops', false, now(), now()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": str(_PROJ_ID), "ws_id": str(_WS_ID), "tid": str(_TENANT_ID), "dn": "C1 Test Project"},
        )

    await dev_workspace_store.upsert(
        str(_TENANT_ID),
        str(_PROJ_ID),
        {
            "ado_project": "TestADOProject",
            "repo_name": "test-repo",
            "branch": "main",
            "remote_url": "https://dev.azure.com/org/TestADOProject/_git/test-repo",
            "work_dir": "/tmp/c1-test-workspace",
            "status": "ready",
        },
    )
    yield str(_PROJ_ID)

    async with get_db_session_for_tenant(str(_TENANT_ID)) as db:
        await db.execute(text("DELETE FROM dev_workspaces WHERE project_id = :pid"), {"pid": str(_PROJ_ID)})
        await db.execute(text("DELETE FROM projects WHERE id = :id"), {"id": str(_PROJ_ID)})
        await db.execute(text("DELETE FROM workspaces WHERE id = :id"), {"id": str(_WS_ID)})
        await db.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": str(_ORG_ID)})


@pytest.mark.asyncio(loop_scope="session")
@_skip_no_db
async def test_bind_pulled_workspace_with_pipeline_context(_seeded_workspace):
    """_bind_pulled_workspace must bind when project_id is ONLY in pipeline_context.

    Exact live WS payload: no top-level project_id, no context key.
    Pre-fix this returned empty string because both helpers read the wrong keys.
    """
    from agents_orchestrator.development_agent.development_agent_api import _bind_pulled_workspace

    sid = "c1-bind-test-pipeline-ctx-001"
    clear_session(sid)
    s = get_session(sid)

    message_data = {"pipeline_context": {"page": "Development", "project_id": _seeded_workspace}}

    guidance = await _bind_pulled_workspace(s, message_data, str(_TENANT_ID))

    assert guidance, "Expected non-empty guidance string -- workspace was not bound"
    assert s.work_dir == "/tmp/c1-test-workspace", f"s.work_dir not set: {s.work_dir!r}"
    assert s.branch_name == "main", f"s.branch_name not set: {s.branch_name!r}"
    assert s.ado_project == "TestADOProject", f"s.ado_project not set: {s.ado_project!r}"

    clear_session(sid)


async def test_bind_pulled_workspace_forwards_project_id_and_owner_id_to_resolve_auth(monkeypatch):
    """Regression: _bind_pulled_workspace's own resolve_auth call must pass
    project_id/owner_id, not just tenant_id.

    Found live during Task 10 verification (2026-08-31): create_pr failed on a
    real project with a real project-scoped ADO credential saved via the
    Integrations page, because resolve_auth(tenant_id) alone can't find a
    project-scoped personal credential (project_integration_credentials) --
    it silently left s.pat empty, so create_pr's Basic-auth header carried no
    real password and Azure DevOps answered with a 302 redirect to its
    sign-in page instead of a clean 401. Same root cause and same fix shape
    as ado_repos.py's dev_workspace.py callers (commit d291651c) -- this call
    site inside the chat/WS agent flow was missed in that pass.
    """
    from agents_orchestrator.development_agent import development_agent_api
    from shared.services import ado_repos

    captured: dict = {}

    async def _fake_resolve_auth(tenant_id, *, project_id="", owner_id=""):
        captured["tenant_id"] = tenant_id
        captured["project_id"] = project_id
        captured["owner_id"] = owner_id
        return "https://dev.azure.com/fake-org", "fake-real-pat"

    monkeypatch.setattr(ado_repos, "resolve_auth", _fake_resolve_auth)
    async def _fake_get_for_project(tenant_id, project_id):
        return {
            "status": "ready",
            "work_dir": "/tmp/binding-test",
            "remote_url": "https://dev.azure.com/fake-org/proj/_git/repo",
            "branch": "main",
            "ado_project": "proj",
            "repo_name": "repo",
        }

    monkeypatch.setattr(
        development_agent_api.dev_workspace_store, "get_for_project", _fake_get_for_project
    )

    sid = "bind-workspace-pat-forward-test"
    clear_session(sid)
    s = get_session(sid)

    message_data = {"pipeline_context": {"project_id": "the-project-id"}}
    await development_agent_api._bind_pulled_workspace(
        s, message_data, "the-tenant-id", "the-user-id"
    )

    assert captured["project_id"] == "the-project-id"
    assert captured["owner_id"] == "the-user-id"
    assert s.pat == "fake-real-pat"

    clear_session(sid)
