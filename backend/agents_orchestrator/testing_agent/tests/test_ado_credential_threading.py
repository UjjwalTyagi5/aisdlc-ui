"""Azure DevOps credentials are stored per person per project, so a clone that
resolves on the tenant alone finds nothing and tells the user the connector is
"not configured" on an organization where it plainly is. That was the whole
failure: `resolve_auth(tenant_id)` with no project and no owner.

These tests pin the three values travelling together — from the state the chat
entrypoints build, through setup_workspace, into resolve_auth. A future change
that drops project_id or owner_id anywhere on that path fails here rather than
in a live run against a real repo.
"""
import asyncio
import sys
import types

import pytest

from agents_orchestrator.testing_agent.Nodes import workspace


def _stub_resolve_auth(recorder):
    async def resolve_auth(tenant_id, project_id="", owner_id="", **kwargs):
        recorder.append({"tenant_id": tenant_id, "project_id": project_id, "owner_id": owner_id})
        return ("", "")  # not connected → graceful clone_error_message path
    return resolve_auth


@pytest.fixture
def fake_ado_repos(monkeypatch):
    """Replace shared.services.ado_repos with a recording stub.

    workspace.py imports it inside the function body, so patching the module in
    sys.modules is what the call actually sees.
    """
    calls = []
    mod = types.ModuleType("shared.services.ado_repos")
    mod.resolve_auth = _stub_resolve_auth(calls)
    monkeypatch.setitem(sys.modules, "shared.services.ado_repos", mod)

    services = sys.modules.get("shared.services")
    if services is not None:
        monkeypatch.setattr(services, "ado_repos", mod, raising=False)
    return calls


def test_clone_passes_project_and_owner_to_resolve_auth(fake_ado_repos, monkeypatch):
    # Force the remote-clone branch (skip the dev-workspace local reuse attempt).
    monkeypatch.setattr(workspace, "_local_clone_from_dev", lambda *a, **k: False)

    asyncio.run(workspace._clone_into_workspace(
        project="Company", repo="Company", branch="main",
        tenant_id="tenant-1", project_id="proj-9", owner_id="user-7",
    ))

    assert fake_ado_repos, "resolve_auth was never called"
    assert fake_ado_repos[-1] == {
        "tenant_id": "tenant-1", "project_id": "proj-9", "owner_id": "user-7",
    }


def test_setup_workspace_threads_state_identity_into_the_clone(fake_ado_repos, monkeypatch):
    monkeypatch.setattr(workspace, "_local_clone_from_dev", lambda *a, **k: False)

    state = {
        "clone_target": {"project": "Company", "repo": "Company", "branch": "main"},
        "tenant_id": "tenant-1",
        "project_id": "proj-9",
        "owner_id": "user-7",
    }
    result = asyncio.run(workspace.setup_workspace(state))

    assert fake_ado_repos[-1] == {
        "tenant_id": "tenant-1", "project_id": "proj-9", "owner_id": "user-7",
    }
    # Not-connected still degrades gracefully rather than raising.
    assert result.get("clone_error_message")


def test_missing_project_and_owner_do_not_crash_the_clone(fake_ado_repos, monkeypatch):
    """An orchestrator-driven run that has no project in context still runs; it
    just resolves nothing and reports the friendly connector message."""
    monkeypatch.setattr(workspace, "_local_clone_from_dev", lambda *a, **k: False)

    result = asyncio.run(workspace.setup_workspace({
        "clone_target": {"project": "Company", "repo": "Company", "branch": "main"},
        "tenant_id": "tenant-1",
    }))

    assert fake_ado_repos[-1] == {"tenant_id": "tenant-1", "project_id": "", "owner_id": ""}
    assert result.get("clone_error_message")
