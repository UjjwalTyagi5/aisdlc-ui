"""The standalone Development agent finds the user's Azure DevOps credential.

THE LIVE FAILURE (15 Sep 2026). A developer, in the Development agent on a project
with Azure DevOps connected read & write, asked to push the scaffold to ADO. The agent
called `get_ado_context` and got "No ADO credentials configured", then asked the user
to paste a PAT into the chat. `_active_ado_creds` looked in one place only: the
connector the Orchestrator binds for a pipeline run — which the standalone chat never
binds. The same bug the Orchestrator had from the other side
(tests/orchestrator2/test_connector_binding.py), now closed on this side too: the chat
gate binds the turn's tenant and project, and the credential resolves for THAT project
and THIS user, as every other personal credential does.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents_orchestrator.development_agent.tools import git_tools as gt
from config import ws_helper

TENANT = "dfee0d2f-345e-430e-8084-7ab7276cc5b8"
PROJECT = "ed20b947-360d-4881-9a83-52decc68210a"
USER = "fed0c8d7-2335-4d06-b48e-7f035954582e"


@pytest.fixture(autouse=True)
def _turn(monkeypatch):
    ws_helper.set_tenant_id(None)
    ws_helper.set_project_id(None)
    monkeypatch.setattr(gt, "get_user_id", lambda: USER)
    monkeypatch.setattr(gt, "get_session_id", lambda: "sess-1")
    monkeypatch.setattr(gt, "get_active_connector", lambda: (_ for _ in ()).throw(LookupError("no connector bound")))
    yield
    ws_helper.set_tenant_id(None)
    ws_helper.set_project_id(None)


async def test_a_standalone_turn_resolves_the_users_credential_on_the_bound_project():
    ws_helper.bind_turn_project(TENANT, PROJECT)
    resolve = AsyncMock(return_value=("https://dev.azure.com/srk02804", "pat-xyz"))
    with patch("shared.services.ado_repos.resolve_auth", resolve):
        org, pat = await gt._active_ado_creds()

    assert (org, pat) == ("https://dev.azure.com/srk02804", "pat-xyz")
    resolve.assert_awaited_once_with(TENANT, project_id=PROJECT, owner_id=USER)


async def test_get_ado_context_stores_it_in_the_session():
    import json

    ws_helper.bind_turn_project(TENANT, PROJECT)
    with patch("shared.services.ado_repos.resolve_auth", AsyncMock(return_value=("https://dev.azure.com/o", "pat-1"))):
        out = json.loads(await gt.get_ado_context.ainvoke({}))

    assert out["credentials_stored"] is True
    assert out["org_url"] == "https://dev.azure.com/o"
    assert gt.get_session("sess-1").pat == "pat-1"


async def test_the_session_credential_from_a_pulled_workspace_still_counts():
    ws_helper.bind_turn_project(TENANT, PROJECT)
    s = gt.get_session("sess-1")
    s.pat, s.ado_org_url = "pat-from-workspace", "https://dev.azure.com/o/"
    with patch("shared.services.ado_repos.resolve_auth", AsyncMock(return_value=("", ""))):
        assert await gt._active_ado_creds() == ("https://dev.azure.com/o", "pat-from-workspace")
    s.pat, s.ado_org_url = "", ""


async def test_no_credential_anywhere_is_named_as_a_personal_credential():
    import json

    ws_helper.bind_turn_project(TENANT, PROJECT)
    s = gt.get_session("sess-1")
    s.pat = ""
    with patch("shared.services.ado_repos.resolve_auth", AsyncMock(return_value=("", ""))):
        out = json.loads(await gt.get_ado_context.ainvoke({}))
        listed = await gt.list_ado_projects.ainvoke({})

    assert "not connected for you" in out["error"]
    assert "Integrations page" in out["error"]
    assert "paste" not in out["error"].lower(), "never ask for a PAT in the chat"
    assert "not connected for you" in listed


async def test_the_bound_pipeline_connector_still_wins(monkeypatch):
    class _Conn:
        async def auth_adapter(self):
            return {"org_url": "https://dev.azure.com/run/", "pat": "run-pat"}

    monkeypatch.setattr(gt, "get_active_connector", lambda: _Conn())
    with patch("shared.services.ado_repos.resolve_auth", AsyncMock(side_effect=AssertionError("not consulted"))):
        assert await gt._active_ado_creds() == ("https://dev.azure.com/run", "run-pat")
