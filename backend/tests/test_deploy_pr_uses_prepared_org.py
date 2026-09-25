"""Opening the deployment PR uses the organisation the session already cloned with.

WHAT WENT WRONG. `open_deploy_pr` handed `create_pull_request` a PAT but no org URL,
so the org was re-derived by `resolve_auth()` — which, called without project_id and
owner_id, only sees TENANT-WIDE connectors. Every Azure DevOps credential on the
project in question was the per-person, per-project kind the Integrations page saves,
so the lookup answered with nothing and the agent reported "Azure DevOps is not
configured" about a repository it had cloned, and pushed to, moments earlier with that
same credential.

AND IT REPORTED IT WRONGLY. Push and PR shared one `try`, so a failure in the second
was announced as though neither had happened — "no files have been pushed, no PR
created" — while the branch was sitting on the remote with the deployment package in
it. The two are separate steps with separate consequences and now say so.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents_orchestrator.deployment_agent.config.session_state import (  # noqa: E402
    clear_session, get_session,
)
from shared.services.ado_repos import org_base_from_repo_url  # noqa: E402

pytestmark = pytest.mark.unit

SESSION = "s-deploy-pr"


@pytest.mark.parametrize(("remote", "expected"), [
    # The shape a real clone of an ADO project produces, project segment and all.
    ("https://dev.azure.com/srk02804/Quicklink%20Project/_git/Quicklink%20Project",
     "https://dev.azure.com/srk02804"),
    # The same, with the PAT git was given embedded in it — the stored record's form.
    ("https://x:supersecretpat@dev.azure.com/acme/Payments/_git/sdlc",
     "https://dev.azure.com/acme"),
    # Organisation-level remote, no project segment.
    ("https://dev.azure.com/acme/_git/sdlc", "https://dev.azure.com/acme"),
    # The legacy host carries the organisation in the hostname instead.
    ("https://acme.visualstudio.com/Payments/_git/sdlc", "https://acme.visualstudio.com"),
    # Not Azure DevOps, and not guessable: answer nothing rather than point a write
    # at the wrong account.
    ("https://github.com/acme/sdlc.git", ""),
    ("git@ssh.dev.azure.com:v3/acme/Payments/sdlc", ""),
    ("", ""),
])
def test_org_base_comes_from_the_remote_that_was_cloned(remote, expected):
    assert org_base_from_repo_url(remote) == expected


def test_the_derived_base_never_carries_the_credential():
    base = org_base_from_repo_url("https://x:supersecretpat@dev.azure.com/acme/_git/sdlc")
    assert "supersecretpat" not in base and "@" not in base


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(
        "config.ws_helper.get_session_id", lambda: SESSION, raising=False,
    )
    monkeypatch.setattr(
        "agents_orchestrator.deployment_agent.tools.deploy_tools.get_session_id",
        lambda: SESSION,
    )
    s = get_session(SESSION)
    s.work_dir, s.repo_name, s.source_branch = "/tmp/clone", "sdlc", "main"
    s.ado_project, s.pat, s.tenant_id = "Payments", "pat-value", "t-1"
    s.repo_url = "https://x:pat-value@dev.azure.com/acme/Payments/_git/sdlc"
    s.environment = "staging"
    s.staged_files = [{"path": "Dockerfile", "contents": "FROM node:20-alpine"}]
    # A real session reaches this tool holding the assessment it just produced; an
    # empty dict is falsy and would skip the very write these tests are about.
    s.last_artifact = {"status": "assessed"}
    yield s
    clear_session(SESSION)


async def _call(**patches):
    from agents_orchestrator.deployment_agent.tools import deploy_tools

    return await deploy_tools.open_deploy_pr.ainvoke({"title": "", "description": ""})


@pytest.mark.asyncio
async def test_pr_is_opened_against_the_prepared_organisation(session, monkeypatch):
    seen: dict = {}

    def _push(*_a, **_k):
        return "abc1234567"

    async def _create(*args, **kwargs):
        seen.update(kwargs)
        return "https://dev.azure.com/acme/Payments/_git/sdlc/pullrequest/7"

    from shared.services import ado_repos

    monkeypatch.setattr(ado_repos, "commit_and_push_files", _push)
    monkeypatch.setattr(ado_repos, "create_pull_request", _create)

    out = await _call()

    assert seen["org_url"] == "https://dev.azure.com/acme"
    assert "pullrequest/7" in out
    assert session.last_artifact["status"] == "pr_opened"


@pytest.mark.asyncio
async def test_a_failed_pr_admits_the_branch_was_pushed(session, monkeypatch):
    def _push(*_a, **_k):
        return "abc1234567"

    async def _create(*_a, **_k):
        raise RuntimeError("Azure DevOps is not configured.")

    from shared.services import ado_repos

    monkeypatch.setattr(ado_repos, "commit_and_push_files", _push)
    monkeypatch.setattr(ado_repos, "create_pull_request", _create)

    out = await _call()

    assert "WAS pushed" in out
    assert "deploy/staging-" in out          # names the branch that now exists
    assert "by hand" in out                  # and what to do about it
    assert session.last_artifact.get("pr_url") is None


@pytest.mark.asyncio
async def test_a_failed_push_says_nothing_was_pushed(session, monkeypatch):
    def _push(*_a, **_k):
        raise RuntimeError("authentication failed")

    async def _create(*_a, **_k):  # pragma: no cover - must not be reached
        raise AssertionError("the PR must not be attempted after a failed push")

    from shared.services import ado_repos

    monkeypatch.setattr(ado_repos, "commit_and_push_files", _push)
    monkeypatch.setattr(ado_repos, "create_pull_request", _create)

    out = await _call()

    assert "Nothing was pushed" in out
