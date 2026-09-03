"""A connector with no tile of its own inherits the tile it belongs to.

`azure_pipelines` is deliberately absent from the Integrations catalogue: one ado-pat
covers boards, repos and CI/CD, so it is folded into the Azure DevOps tile. The
consequence nobody notices until a pipeline call is denied is that there is then no
tile to grant it and no entry for the stage picker to wire — so it resolves to None for
every project, forever, and looks exactly like a configuration mistake somebody made.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.authz import connector_grants as cg  # noqa: E402

TENANT = "11111111-1111-1111-1111-111111111111"
PROJECT = "22222222-2222-2222-2222-222222222222"
WORKSPACE = "33333333-3333-3333-3333-333333333333"


class _Row:
    def __init__(self, connectors):
        self.workspace_id = WORKSPACE
        self.connectors = connectors
        self.mcp_servers = {}
        self.tool_access_modes = {}
        # The level lookup falls through to project_connector_access, which selects an
        # `access` column off the same fake cursor.
        self.access = "read"


class _DB:
    def __init__(self, connectors):
        self.row = _Row(connectors)

    async def execute(self, _stmt, _params=None):
        row = self.row

        class _R:
            @staticmethod
            def first():
                return row

            @staticmethod
            def scalar_one_or_none():
                return None
        return _R()


@pytest.fixture
def granted(monkeypatch):
    """The workspace grant passes; what varies is the per-stage wiring."""
    seen = {}

    async def _unit_is_granted(_db, *, tenant_id, workspace_id, target_ref, kind):
        seen["target_ref"] = target_ref
        return True

    monkeypatch.setattr(cg, "unit_is_granted", _unit_is_granted)
    return seen


@pytest.mark.unit
async def test_a_stage_wired_to_azure_devops_can_reach_pipelines(granted):
    """THE POINT. Granting the Azure DevOps tile is the only way a project can express
    "this stage may use our ADO" — pipelines have no tile of their own."""
    db = _DB({"deployment": ["azure_devops"]})
    level = await cg.effective_access(
        db, tenant_id=TENANT, project_id=PROJECT, target_ref="azure_pipelines",
        agent_id="deployment",
    )
    assert level is not None


@pytest.mark.unit
async def test_the_workspace_grant_it_checks_is_the_tile_not_the_alias(granted):
    """Looking up a grant for 'azure_pipelines' would never match: nothing writes that
    row, because the catalogue has no tile to write it from."""
    db = _DB({"deployment": ["azure_devops"]})
    await cg.effective_access(
        db, tenant_id=TENANT, project_id=PROJECT, target_ref="azure_pipelines",
        agent_id="deployment",
    )
    assert granted["target_ref"] == "azure_devops"


@pytest.mark.unit
async def test_a_stage_with_no_ado_wiring_still_cannot_reach_pipelines(granted):
    """The alias inherits a decision; it does not invent one. A stage that was never
    wired to Azure DevOps gains nothing."""
    db = _DB({"design": ["azure_devops"]})
    level = await cg.effective_access(
        db, tenant_id=TENANT, project_id=PROJECT, target_ref="azure_pipelines",
        agent_id="deployment",
    )
    assert level is None


@pytest.mark.unit
async def test_a_project_wiring_nothing_reaches_nothing(granted):
    db = _DB({})
    assert await cg.effective_access(
        db, tenant_id=TENANT, project_id=PROJECT, target_ref="azure_pipelines",
        agent_id="deployment",
    ) is None


@pytest.mark.unit
async def test_connectors_with_their_own_tile_are_untouched(granted):
    """The alias must not quietly rewrite every other connector's lookup."""
    db = _DB({"deployment": ["sonarqube"]})
    await cg.effective_access(
        db, tenant_id=TENANT, project_id=PROJECT, target_ref="sonarqube",
        agent_id="deployment",
    )
    assert granted["target_ref"] == "sonarqube"


@pytest.mark.unit
async def test_the_alias_does_not_apply_to_mcp_servers(granted):
    """`kind` distinguishes connectors from MCP servers, and an MCP server that
    happened to share the name must not inherit a connector's grant."""
    db = _DB({"deployment": ["azure_devops"]})
    await cg.effective_access(
        db, tenant_id=TENANT, project_id=PROJECT, target_ref="azure_pipelines",
        kind="mcp_server", agent_id="deployment",
    )
    assert granted["target_ref"] == "azure_pipelines"


@pytest.mark.unit
async def test_a_run_that_names_no_stage_gets_nothing(granted):
    db = _DB({"deployment": ["azure_devops"]})
    assert await cg.effective_access(
        db, tenant_id=TENANT, project_id=PROJECT, target_ref="azure_pipelines",
        agent_id="",
    ) is None
