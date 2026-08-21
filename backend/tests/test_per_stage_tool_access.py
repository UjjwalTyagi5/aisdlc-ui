"""The read/write decision lives on the project's stage, not the unit's grant.

Migration 0024 moved it. What these pin is the three-step resolution in
`shared/authz/connector_grants.effective_access` and, just as importantly, the
things that must still fail closed after a ceiling was deliberately removed:

  reach       an ungranted unit stops everything, which is the ONLY thing an Org
              Admin can still do — so it had better work
  wiring      a connector the stage never wired is not available to it, which is
              what keeps one grant from reaching all eight agents
  level       the stage's own mode, then the project-wide default, then "both"

The last of those is the trade the migration made: a stage that wires a connector
and touches nothing gets read AND write, with nothing above it to narrow that.
"""
import uuid as _uuid

import pytest
from sqlalchemy import text

from shared.authz.connector_grants import effective_access, unit_is_granted
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org():
    org_id = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Stage Access')"
        ), {"i": org_id, "s": f"sta-{org_id[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, :s, 'Payments')"
        ), {"i": unit, "o": org_id, "s": f"pay-{unit[:8]}"})
    yield {"org": org_id, "unit": unit, "project": project}


async def _project(org, connectors: dict, modes: dict | None = None):
    """Create the project with a stage→connector wiring and per-stage modes."""
    import json
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO projects "
            "  (id, workspace_id, tenant_id, display_name, provider_kind, "
            "   connectors, tool_access_modes) "
            "VALUES (CAST(:i AS uuid), CAST(:w AS uuid), CAST(:t AS uuid), 'Core', 'github', "
            "        CAST(:c AS jsonb), CAST(:m AS jsonb))"
        ), {"i": org["project"], "w": org["unit"], "t": org["org"],
            "c": json.dumps(connectors), "m": json.dumps(modes) if modes else None})


async def _grant(org, ref="jira", kind="connector"):
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO integration_grants (tenant_id, kind, target_ref, workspace_id) "
            "VALUES (CAST(:t AS uuid), :k, :r, CAST(:w AS uuid)) ON CONFLICT DO NOTHING"
        ), {"t": org["org"], "k": kind, "r": ref, "w": org["unit"]})


async def _resolve(org, agent_id, ref="jira", kind="connector"):
    async with get_db_session_for_tenant(org["org"]) as s:
        return await effective_access(
            s, tenant_id=org["org"], project_id=org["project"],
            target_ref=ref, kind=kind, agent_id=agent_id,
        )


# ── 1. reach ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_ungranted_unit_stops_every_stage(org):
    """The one power an Org Admin has left. It must not be bypassable by wiring."""
    await _project(org, {"development": ["jira"]}, {"development::connector::jira": "both"})
    assert await _resolve(org, "development") is None


@pytest.mark.asyncio
async def test_revoking_the_grant_takes_it_back_from_a_configured_stage(org):
    await _grant(org)
    await _project(org, {"development": ["jira"]}, {"development::connector::jira": "both"})
    assert await _resolve(org, "development") == "read_write"

    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "DELETE FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
            "  AND target_ref = 'jira'"
        ), {"t": org["org"]})
    assert await _resolve(org, "development") is None


# ── 2. wiring ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_stage_that_never_wired_it_has_no_access(org):
    """One grant must not reach every agent — the stage assignment is the boundary."""
    await _grant(org)
    await _project(org, {"development": ["jira"]})
    assert await _resolve(org, "development") == "read_write"
    assert await _resolve(org, "security") is None


@pytest.mark.asyncio
async def test_a_caller_that_names_no_stage_gets_nothing(org):
    """The runtime must pass agent_id. Failing closed makes the omission visible."""
    await _grant(org)
    await _project(org, {"development": ["jira"]})
    assert await _resolve(org, "") is None


# ── 3. level ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_each_stage_gets_its_own_level(org):
    """The whole point: read-only for QA, read-write for Development, one connector."""
    await _grant(org)
    await _project(
        org,
        {"development": ["jira"], "testing": ["jira"], "code_review": ["jira"]},
        {
            "development::connector::jira": "both",
            "testing::connector::jira": "read",
            "code_review::connector::jira": "write",
        },
    )
    assert await _resolve(org, "development") == "read_write"
    assert await _resolve(org, "testing") == "read"
    assert await _resolve(org, "code_review") == "write"


@pytest.mark.asyncio
async def test_an_unset_stage_falls_back_to_the_project_default(org):
    await _grant(org)
    await _project(org, {"development": ["jira"], "testing": ["jira"]},
                   {"development::connector::jira": "write"})
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO project_connector_access "
            "  (tenant_id, project_id, kind, target_ref, access) "
            "VALUES (CAST(:t AS uuid), CAST(:p AS uuid), 'connector', 'jira', 'read')"
        ), {"t": org["org"], "p": org["project"]})

    # The stage that chose wins; the one that did not takes the project's default.
    assert await _resolve(org, "development") == "write"
    assert await _resolve(org, "testing") == "read"


@pytest.mark.asyncio
async def test_with_nothing_set_anywhere_a_wired_stage_gets_read_and_write(org):
    """THE TRADE. No ceiling remains, so an untouched chip is the final answer."""
    await _grant(org)
    await _project(org, {"development": ["jira"]})
    assert await _resolve(org, "development") == "read_write"


@pytest.mark.asyncio
async def test_a_mode_nobody_recognises_denies_rather_than_guesses(org):
    """JSONB has no CHECK behind it, so the resolver must fail closed on junk."""
    await _grant(org)
    await _project(org, {"development": ["jira"]},
                   {"development::connector::jira": "sideways"})
    assert await _resolve(org, "development") is None


# ── the grant is a boolean now ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unit_is_granted_answers_yes_or_no(org):
    async with get_db_session_for_tenant(org["org"]) as s:
        assert await unit_is_granted(
            s, tenant_id=org["org"], workspace_id=org["unit"], target_ref="jira"
        ) is False
    await _grant(org)
    async with get_db_session_for_tenant(org["org"]) as s:
        assert await unit_is_granted(
            s, tenant_id=org["org"], workspace_id=org["unit"], target_ref="jira"
        ) is True
