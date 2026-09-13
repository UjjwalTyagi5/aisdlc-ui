"""An Orchestrator conversation is self-contained; the agent pages share the project.

  * code pulled in an Orchestrator conversation lives in that conversation's own copy —
    the pages never see it, and a page's pull never reaches the conversation;
  * the chat can pull the code (Requirements' `pull_legacy_code`), with the connection
    wired to the agent's stage and only when that stage may read;
  * what an Orchestrator turn records is its Deliverables, not a version on the pages;
  * a page's Discovery chat takes its brief from the Migration Intent page's versions, never
    from a run an Orchestrator conversation wrote.

The clone is faked (the fixture repository is copied into place) so nothing touches the
network; everything else — scope, profile, record, credentials — is the real code.
"""
import uuid
from types import SimpleNamespace

import pytest

from config.ws_helper import (
    set_orchestrator_run,
    set_project_id,
    set_run_id,
    set_session_id,
    set_tenant_id,
    set_user_id,
)

PROJECT = str(uuid.uuid4())
RUN = str(uuid.uuid4())
SECRET = "PAT-FROM-THE-REQUIREMENTS-STAGE"


@pytest.fixture
def legacy(monkeypatch, tmp_path):
    """legacy_code rooted in tmp, with a clone that lays down the fixture repository."""
    import agents_orchestrator.discovery_agent.tools.repo_tools as repo_tools
    from agents_orchestrator.modernization_common import legacy_code
    from tests.discovery.legacy_fixture import build_legacy_repo

    monkeypatch.setattr(legacy_code, "_root", lambda: tmp_path / "legacy-code")
    clones: list[dict] = []

    def fake_clone(url, dest, branch, secret):
        clones.append({"url": url, "secret": secret})
        build_legacy_repo(__import__("pathlib").Path(dest))
        return {"commit": "abc123def456", "branch": branch or "main"}

    monkeypatch.setattr(repo_tools, "_clone", fake_clone)
    return SimpleNamespace(module=legacy_code, clones=clones)


def _orchestrator_turn():
    set_project_id(PROJECT)
    set_run_id(RUN)
    set_session_id(RUN)
    set_user_id("ba-user")
    set_orchestrator_run(True)


def _page_turn():
    set_project_id(PROJECT)
    set_run_id(str(uuid.uuid4()))
    set_session_id(str(uuid.uuid4()))
    set_user_id("ba-user")
    set_orchestrator_run(False)


def _tool(name):
    from agents_orchestrator.requirements_modernization_agent.tools.brief_tools import TOOLS

    return next(t for t in TOOLS if t.name == name)


# ── scope ────────────────────────────────────────────────────────────────────


async def test_code_pulled_in_the_orchestrator_stays_in_that_conversation(legacy):
    _orchestrator_turn()
    out = await _tool("pull_legacy_code").ainvoke({"repository": "https://github.com/acme/legacy-billing"})
    assert out.startswith("Pulled read-only for this conversation")
    assert "Legacy code pulled" in out  # the profile comes straight back

    assert legacy.module.current_pull(PROJECT, RUN) is not None
    assert legacy.module.current_pull(PROJECT) is None  # the pages' copy is untouched
    assert (legacy.module.project_dir(PROJECT, RUN) / "checkout").is_dir()

    profile = await _tool("get_legacy_code_profile").ainvoke({})
    assert "legacy-billing" in profile

    _page_turn()
    assert "No legacy code has been pulled for this project yet" in await _tool("get_legacy_code_profile").ainvoke({})


async def test_a_page_pull_does_not_reach_an_orchestrator_conversation(legacy):
    await legacy.module.pull_now(PROJECT, "https://github.com/acme/legacy-billing", "", user_id="ba-user")
    _page_turn()
    assert "Legacy code pulled" in await _tool("get_legacy_code_profile").ainvoke({})

    _orchestrator_turn()
    assert "No legacy code has been pulled in this conversation yet" in await _tool(
        "get_legacy_code_profile").ainvoke({})


async def test_discovery_in_the_orchestrator_assesses_the_conversations_copy(legacy):
    from agents_orchestrator.discovery_agent.tools.assessment_tools import _ensure_checkout
    from agents_orchestrator.discovery_agent.tools.repo_tools import DiscoverySession

    _orchestrator_turn()
    await _tool("pull_legacy_code").ainvoke({"repository": "https://github.com/acme/legacy-billing"})
    s = DiscoverySession()
    assert _ensure_checkout(s)
    assert s.work_dir == str(legacy.module.checkout_dir(PROJECT, RUN))


# ── credentials for a chat pull ──────────────────────────────────────────────


class _FakeAdo:
    connector_name = "azure_devops"
    display_name = "Azure DevOps"

    async def auth_adapter(self, tenant_id: str = "") -> dict:
        return {"pat": SECRET, "org_url": "https://dev.azure.com/acme"}


@pytest.fixture
def bound():
    from config.connectors.context import clear_connector, set_connector
    from config.connectors.scoped import ScopedConnector

    def bind(level):
        set_connector(ScopedConnector(_FakeAdo(), level))
    yield bind
    clear_connector()


async def test_a_chat_pull_uses_the_stages_connection_when_it_may_read(legacy, bound):
    _orchestrator_turn()
    bound("read")
    out = await _tool("pull_legacy_code").ainvoke(
        {"repository": "https://dev.azure.com/acme/Billing/_git/legacy-billing"})
    assert "Migration Intent stage's connection" in out
    assert legacy.clones[-1]["secret"] == SECRET


async def test_a_chat_pull_gets_no_credential_when_the_stage_cannot_read(legacy, bound):
    _orchestrator_turn()
    bound(None)  # no grant / not wired: the connector permits nothing
    await _tool("pull_legacy_code").ainvoke({"repository": "https://dev.azure.com/acme/Billing/_git/legacy-billing"})
    assert legacy.clones[-1]["secret"] == ""

    listing = await _tool("find_legacy_repositories").ainvoke({})
    assert "Migration Intent" in listing and "Integrations page" in listing


async def test_a_github_token_is_not_sent_to_azure_from_the_chat(legacy, bound, monkeypatch):
    class _FakeGitHub(_FakeAdo):
        connector_name = "github"

        async def auth_adapter(self, tenant_id: str = "") -> dict:
            return {"token": SECRET}

    from config.connectors.context import set_connector
    from config.connectors.scoped import ScopedConnector

    _orchestrator_turn()
    set_connector(ScopedConnector(_FakeGitHub(), "read"))
    await _tool("pull_legacy_code").ainvoke({"repository": "https://dev.azure.com/acme/Billing/_git/legacy-billing"})
    assert legacy.clones[-1]["secret"] == ""


# ── versions: the pages', not the Orchestrator's ─────────────────────────────


async def test_an_orchestrator_turn_freezes_no_page_version(monkeypatch):
    from agents_orchestrator.modernization_common import versions
    from shared.services import artifact_versions as svc

    async def must_not_run(*a, **k):
        raise AssertionError("an Orchestrator turn wrote a page version")

    monkeypatch.setattr(svc, "snapshot_stage_payload", must_not_run)
    _orchestrator_turn()
    set_tenant_id(str(uuid.uuid4()))
    assert await versions.freeze_version("requirements_modernization", {"system_name": "X"}) is None
    line = versions.saved_line("Saved to the project as the current migration-intent brief.", None, "brief")
    assert "Deliverables" in line and "agent's page" not in line


class _Session:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def versions_store(monkeypatch):
    import shared.db
    from shared.services import artifact_versions as svc

    state = {"enforced": False, "published": None, "latest": None}

    async def enforcement_enabled(db, project_id):
        return state["enforced"]

    async def latest_published(db, project_id, stage):
        return state["published"]

    async def latest_version(db, project_id, stage):
        return state["latest"]

    monkeypatch.setattr(shared.db, "get_db_session_for_tenant", lambda tenant: _Session())
    monkeypatch.setattr(svc, "enforcement_enabled", enforcement_enabled)
    monkeypatch.setattr(svc, "latest_published", latest_published)
    monkeypatch.setattr(svc, "latest_version", latest_version)
    return state


BRIEF = {"system_name": "ClaimTrack", "business_drivers": ["End of support"],
         "current_state": {"stack": "Java 7"}, "target_state": {"stack": "Java 21"},
         "in_scope": ["all"], "constraints": ["June 2027"], "success_criteria": ["parity"]}


async def test_a_page_chat_takes_its_brief_from_the_requirements_page(versions_store):
    from agents_orchestrator.modernization_common.standalone import upstream_from_pages

    versions_store["latest"] = SimpleNamespace(version=2, status="draft", payload=BRIEF)
    text = await upstream_from_pages(PROJECT, "tenant", "discovery")
    assert "Migration-intent brief v2 (draft, not yet approved)" in text and "ClaimTrack" in text

    versions_store["published"] = SimpleNamespace(version=1, status="published", payload=BRIEF)
    assert "v1 (approved)" in await upstream_from_pages(PROJECT, "tenant", "discovery")


async def test_enforced_publication_leaves_a_draft_brief_out(versions_store):
    from agents_orchestrator.modernization_common.standalone import upstream_from_pages

    versions_store.update(enforced=True, latest=SimpleNamespace(version=3, status="draft", payload=BRIEF))
    assert await upstream_from_pages(PROJECT, "tenant", "discovery") == ""


async def test_requirements_has_no_upstream(versions_store):
    from agents_orchestrator.modernization_common.standalone import upstream_from_pages

    versions_store["latest"] = SimpleNamespace(version=1, status="draft", payload=BRIEF)
    assert await upstream_from_pages(PROJECT, "tenant", "requirements_modernization") == ""
