"""A tenant's MCP servers must reach that tenant's agents and nobody else's.

WHY THIS NEEDS A REAL DATABASE. Nothing in `resolve_server_configs` filters by tenant —
read it and you will find `select(McpServer).where(McpServer.id.in_(ids))` with no
tenant predicate at all. The isolation is entirely in the FORCE RLS policy on
`mcp_servers` (migration 0023), applied through `get_db_session_for_tenant`'s GUC. A
test with a fake session would exercise the half that does nothing and pass while the
policy was disabled.

The threat is concrete: `server_ids` come from `projects.mcp_servers[stage]`, a JSONB
map. An id copied from another tenant into that map is the whole attack, and the only
thing stopping it is the policy this test exercises.

MCP tools are also third-party code reached by a model that has read repository and
board text (§Phase 4 step 13), so "which servers can this run reach" is a security
boundary rather than a configuration detail.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytestmark = pytest.mark.integration


@pytest.fixture
async def two_tenants():
    """Two orgs, each with a project; org A also registers an MCP server."""
    from sqlalchemy import text

    from shared.db import get_db_session_for_tenant, get_db_session_superuser

    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    ws_a, ws_b = str(uuid.uuid4()), str(uuid.uuid4())
    proj_a, proj_b = str(uuid.uuid4()), str(uuid.uuid4())
    server_id = str(uuid.uuid4())

    async with get_db_session_superuser() as s:
        for org, ws, slug in ((a, ws_a, "acme"), (b, ws_b, "globex")):
            await s.execute(
                text("INSERT INTO organizations (id, slug, display_name) "
                     "VALUES (:i, :sl, :d)"),
                {"i": org, "sl": f"{slug}-{org[:8]}", "d": slug.title()},
            )
            await s.execute(
                text("INSERT INTO workspaces (id, organization_id, slug, display_name) "
                     "VALUES (:i, :o, 'unit', 'Unit')"),
                {"i": ws, "o": org},
            )

    for org, ws, proj in ((a, ws_a, proj_a), (b, ws_b, proj_b)):
        async with get_db_session_for_tenant(org) as s:
            await s.execute(
                text("INSERT INTO projects (id, workspace_id, tenant_id, display_name, "
                     "provider_kind, track, mcp_servers) "
                     "VALUES (:i, :w, :t, 'P', 'azure_devops', 'greenfield', "
                     "CAST(:m AS jsonb))"),
                {"i": proj, "w": ws, "t": org,
                 "m": f'{{"design": ["{server_id}"]}}'},
            )

    # Only org A registers the server. Both projects reference the id — that is the
    # point: B's project map names a server it does not own.
    async with get_db_session_for_tenant(a) as s:
        await s.execute(
            text("INSERT INTO mcp_servers (id, tenant_id, server_name, transport, url, "
                 "is_active, created_by) VALUES (:i, :t, 'acme-docs', "
                 "'streamable_http', 'https://mcp.acme.test/v1', true, 'seed-admin')"),
            {"i": server_id, "t": a},
        )

    yield {"a": a, "b": b, "project_a": proj_a, "project_b": proj_b,
           "server_id": server_id}

    async with get_db_session_superuser() as s:
        for stmt, val in (
            ("DELETE FROM mcp_servers WHERE id = CAST(:v AS uuid)", server_id),
            ("DELETE FROM projects WHERE id = CAST(:v AS uuid)", proj_a),
            ("DELETE FROM projects WHERE id = CAST(:v AS uuid)", proj_b),
            ("DELETE FROM workspaces WHERE id = CAST(:v AS uuid)", ws_a),
            ("DELETE FROM workspaces WHERE id = CAST(:v AS uuid)", ws_b),
            ("DELETE FROM organizations WHERE id = CAST(:v AS uuid)", a),
            ("DELETE FROM organizations WHERE id = CAST(:v AS uuid)", b),
        ):
            try:
                await s.execute(text(stmt), {"v": val})
            except Exception:  # noqa: BLE001 — teardown must not mask a failure
                pass


# ── isolation ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_owning_tenant_gets_its_server(two_tenants):
    from shared.services.mcp_registry import resolve_server_configs

    configs = await resolve_server_configs(
        two_tenants["a"], [two_tenants["server_id"]], agent_id="design",
    )
    assert len(configs) == 1
    # `ServerConfig` is a plain dict (mcp_client), keyed "name" — not the ORM column.
    assert configs[0]["name"] == "acme-docs"
    assert configs[0]["id"] == two_tenants["server_id"]


@pytest.mark.asyncio
async def test_another_tenant_asking_for_the_same_id_gets_nothing(two_tenants):
    """The attack this prevents: an id pasted into another tenant's
    `projects.mcp_servers` map. Nothing in the query filters by tenant — the RLS
    policy is the entire defence, so this is the test that proves it is on."""
    from shared.services.mcp_registry import resolve_server_configs

    configs = await resolve_server_configs(
        two_tenants["b"], [two_tenants["server_id"]], agent_id="design",
    )
    assert configs == []


@pytest.mark.asyncio
async def test_an_unscoped_read_sees_no_servers_either(two_tenants):
    """FORCE RLS applies to the table owner too, so there is no ambient path to the
    row — the same property that broke `_read_run_upstream` protects here."""
    from sqlalchemy import text

    from shared.db import get_db_session_superuser

    async with get_db_session_superuser() as s:
        n = (await s.execute(
            text("SELECT count(*) FROM mcp_servers WHERE id = CAST(:i AS uuid)"),
            {"i": two_tenants["server_id"]},
        )).scalar()
    assert n == 0


# ── the per-stage map that supplies the ids ──────────────────────────────────


@pytest.mark.asyncio
async def test_the_stage_map_decides_which_servers_a_stage_gets(two_tenants, monkeypatch):
    """`projects.mcp_servers` is `{stage: [server_id]}`. A server wired to `design` must
    not appear for `requirements` — otherwise one registration reaches every agent."""
    import shared.services.mcp_injection as inj

    monkeypatch.setattr(inj, "MCP_ENABLED", True)

    design = await inj.project_stage_server_ids(
        two_tenants["a"], two_tenants["project_a"], "design")
    requirements = await inj.project_stage_server_ids(
        two_tenants["a"], two_tenants["project_a"], "requirements")

    assert design == [two_tenants["server_id"]]
    assert requirements == []


@pytest.mark.asyncio
async def test_mcp_disabled_yields_no_servers_at_all(two_tenants, monkeypatch):
    """`MCP_ENABLED` is the feature's off switch and defaults to false. It has to win
    over a project map that names servers, or turning the feature off does nothing."""
    import shared.services.mcp_injection as inj

    monkeypatch.setattr(inj, "MCP_ENABLED", False)
    assert await inj.project_stage_server_ids(
        two_tenants["a"], two_tenants["project_a"], "design") == []


@pytest.mark.asyncio
async def test_a_project_from_another_tenant_yields_nothing(two_tenants, monkeypatch):
    """The lookup is `session.get(Project, id)` under the tenant GUC — asking for
    another tenant's project must return no row rather than its stage map."""
    import shared.services.mcp_injection as inj

    monkeypatch.setattr(inj, "MCP_ENABLED", True)
    assert await inj.project_stage_server_ids(
        two_tenants["b"], two_tenants["project_a"], "design") == []


@pytest.mark.asyncio
async def test_an_inactive_server_is_not_served(two_tenants):
    """`is_active` is how an admin revokes a server without deleting its history."""
    from sqlalchemy import text

    from shared.db import get_db_session_for_tenant
    from shared.services.mcp_registry import resolve_server_configs

    async with get_db_session_for_tenant(two_tenants["a"]) as s:
        await s.execute(
            text("UPDATE mcp_servers SET is_active = false WHERE id = CAST(:i AS uuid)"),
            {"i": two_tenants["server_id"]},
        )
    assert await resolve_server_configs(
        two_tenants["a"], [two_tenants["server_id"]], agent_id="design") == []
