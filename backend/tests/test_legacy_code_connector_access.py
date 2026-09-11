"""Pulling legacy code uses a connection only where the platform's connector RBAC allows.

The Pull legacy code button (both Track 3 pages), the repository picker behind it, and
Discovery's own clone all get their credential from a connector resolved by
`get_connector_for_session` — so the three checks every connector use passes must hold
here too, against REAL rows (only the Azure DevOps client itself is faked, so no
network is touched):

  1. the Business Unit holds the integration grant (the Integrations page);
  2. the project wired that connection to THIS stage;
  3. the stage's level admits `read`.

And one this feature adds: a pull from one Track 3 page never borrows the connection
the project wired to the OTHER Track 3 stage.

No credential means the clone still runs WITHOUT one, which reaches a public
repository and nothing else — the tests assert the secret is withheld, which is the
whole difference.
"""
import json as _json
import uuid as _uuid

import pytest
from sqlalchemy import text

from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")

SECRET = "PAT-SECRET-THAT-MUST-NOT-LEAK"
ADO_URL = "https://dev.azure.com/acme/Billing/_git/legacy-billing"
REQ, DISC = "requirements_modernization", "discovery"


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


class _FakeAzureDevOps:
    """The raw connector `_build_connector` would return — minus the network."""
    connector_name = "azure_devops"
    display_name = "Azure DevOps"

    async def auth_adapter(self, tenant_id: str = "") -> dict:
        return {"pat": SECRET, "org_url": "https://dev.azure.com/acme"}


class _FakeGitHub(_FakeAzureDevOps):
    connector_name = "github"
    display_name = "GitHub"

    async def auth_adapter(self, tenant_id: str = "") -> dict:
        return {"token": SECRET}


@pytest.fixture
def fake_client(monkeypatch):
    """Replace only the client construction; resolution and scoping stay real."""
    built: dict = {"cls": _FakeAzureDevOps}

    async def _build(*, kind, tenant_id):
        return built["cls"]()

    import config.connector_factory as factory
    monkeypatch.setattr(factory, "_build_connector", _build)
    return built


@pytest.fixture
async def tree():
    org, unit, other_unit = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    proj = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Legacy Pull Test')"
        ), {"i": org, "s": "lpull-" + org[:8]})
        for wid, slug in ((unit, "payments"), (other_unit, "lending")):
            await s.execute(text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name) "
                "VALUES (:i, :o, :s, :s)"
            ), {"i": wid, "o": org, "s": slug})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name, track, connectors) "
            "VALUES (:i, :w, :t, 'Billing Modernization', 'modernization', CAST('{}' AS jsonb))"
        ), {"i": proj, "w": unit, "t": org})
    yield {"org": org, "unit": unit, "other_unit": other_unit, "project": proj}


async def _grant(org, unit, ref="azure_devops"):
    """The Integrations page: this Business Unit may use the integration."""
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO integration_grants (tenant_id, kind, target_ref, workspace_id) "
            "VALUES (CAST(:t AS uuid), 'connector', :r, CAST(:w AS uuid)) ON CONFLICT DO NOTHING"
        ), {"t": org, "r": ref, "w": unit})


async def _wire(org, project, wiring: dict):
    """Project settings: which connection each stage uses."""
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "UPDATE projects SET connectors = CAST(:c AS jsonb) WHERE id = CAST(:p AS uuid)"
        ), {"p": project, "c": _json.dumps(wiring)})


async def _stage_mode(org, project, stage, mode, ref="azure_devops"):
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "UPDATE projects SET tool_access_modes = "
            "  COALESCE(tool_access_modes, '{}'::jsonb) || CAST(:m AS jsonb) "
            "WHERE id = CAST(:p AS uuid)"
        ), {"p": project, "m": _json.dumps({f"{stage}::connector::{ref}": mode})})


async def _secret_for(tree, stage, url=ADO_URL):
    from agents_orchestrator.modernization_common.legacy_code import _connection_secret

    return await _connection_secret(
        url, tenant_id=tree["org"], project_id=tree["project"], user_id="ba-user", stage=stage)


# ── the Pull button ──────────────────────────────────────────────────────────


async def test_no_business_unit_grant_means_no_credential(tree, fake_client):
    await _wire(tree["org"], tree["project"], {REQ: ["azure_devops"]})
    secret, problem = await _secret_for(tree, REQ)
    assert secret == ""
    assert "Integrations page" in problem and "Requirements (migration intent)" in problem


async def test_granted_and_wired_to_the_stage_gets_the_credential(tree, fake_client):
    await _grant(tree["org"], tree["unit"])
    await _wire(tree["org"], tree["project"], {REQ: ["azure_devops"]})
    assert await _secret_for(tree, REQ) == (SECRET, "")


async def test_a_connection_wired_only_to_discovery_is_not_lent_to_requirements(tree, fake_client):
    await _grant(tree["org"], tree["unit"])
    await _wire(tree["org"], tree["project"], {DISC: ["azure_devops"]})
    secret, problem = await _secret_for(tree, REQ)
    assert secret == "" and "Requirements (migration intent)" in problem
    # ...while Discovery, which it IS wired to, gets it.
    assert await _secret_for(tree, DISC) == (SECRET, "")


async def test_another_business_units_grant_does_not_reach_this_project(tree, fake_client):
    await _grant(tree["org"], tree["other_unit"])
    await _wire(tree["org"], tree["project"], {REQ: ["azure_devops"]})
    assert (await _secret_for(tree, REQ))[0] == ""


async def test_a_write_only_stage_cannot_read_code(tree, fake_client):
    await _grant(tree["org"], tree["unit"])
    await _wire(tree["org"], tree["project"], {REQ: ["azure_devops"]})
    await _stage_mode(tree["org"], tree["project"], REQ, "write")
    assert (await _secret_for(tree, REQ))[0] == ""
    await _stage_mode(tree["org"], tree["project"], REQ, "read")
    assert (await _secret_for(tree, REQ))[0] == SECRET


async def test_revoking_the_unit_grant_stops_the_pull(tree, fake_client):
    await _grant(tree["org"], tree["unit"])
    await _wire(tree["org"], tree["project"], {REQ: ["azure_devops"]})
    assert (await _secret_for(tree, REQ))[0] == SECRET
    async with get_db_session_for_tenant(tree["org"]) as s:
        await s.execute(text(
            "DELETE FROM integration_grants WHERE workspace_id = CAST(:w AS uuid)"), {"w": tree["unit"]})
    assert (await _secret_for(tree, REQ))[0] == ""


async def test_a_github_token_is_never_sent_to_azure(tree, fake_client):
    fake_client["cls"] = _FakeGitHub
    await _grant(tree["org"], tree["unit"], ref="github")
    await _wire(tree["org"], tree["project"], {REQ: ["github"]})
    secret, problem = await _secret_for(tree, REQ, url=ADO_URL)
    assert secret == "" and "GitHub" in problem
    assert (await _secret_for(tree, REQ, url="https://github.com/acme/legacy"))[0] == SECRET


# ── the repository picker ────────────────────────────────────────────────────


async def test_the_picker_lists_nothing_without_access_and_never_asks_the_connection(
    tree, fake_client, monkeypatch,
):
    import agents_orchestrator.discovery_agent.tools.repo_tools as repo_tools
    import shared.routers.modernization as router

    calls: list[str] = []

    async def _repos(ado_project=""):
        calls.append(ado_project)
        return {"provider": "ado", "projects": ["Billing"]}

    async def _guard(db, request, project_id, stage):
        return tree["project"], tree["org"], "ba-user"

    monkeypatch.setattr(repo_tools, "repositories_data", _repos)
    monkeypatch.setattr(router, "_guard", _guard)

    await _wire(tree["org"], tree["project"], {DISC: ["azure_devops"]})
    await _grant(tree["org"], tree["unit"])
    out = await router.list_legacy_code_repositories(tree["project"], None, stage=REQ, ado_project="", db=None)
    assert calls == [] and "Requirements (migration intent)" in out["problem"]

    out = await router.list_legacy_code_repositories(tree["project"], None, stage=DISC, ado_project="", db=None)
    assert out == {"provider": "ado", "projects": ["Billing"]} and calls == [""]


# ── Discovery's own clone, inside a turn ─────────────────────────────────────


async def test_discoverys_clone_reads_the_same_gate(tree, fake_client):
    """In a turn the connector is already bound for the stage; `_stage_credentials`
    must withhold the secret at no access exactly as the Pull button does."""
    from agents_orchestrator.discovery_agent.tools.repo_tools import _stage_credentials
    from agents_orchestrator.orchestrator2.connectors import bound_connector

    await _wire(tree["org"], tree["project"], {DISC: ["azure_devops"]})
    async with bound_connector(DISC, tenant_id=tree["org"], project_id=tree["project"], owner_id="ba-user"):
        _provider, _org, secret, refusal = await _stage_credentials()
    assert secret == "" and refusal  # no Business Unit grant yet

    await _grant(tree["org"], tree["unit"])
    async with bound_connector(DISC, tenant_id=tree["org"], project_id=tree["project"], owner_id="ba-user"):
        _provider, _org, secret, refusal = await _stage_credentials()
    assert secret == SECRET and not refusal
