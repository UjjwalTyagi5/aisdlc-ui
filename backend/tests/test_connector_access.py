"""Read, write, or both — who may do what with a connector.

The interesting assertions are the negative ones. A connector granted for reading
that can still be written to is not a partial feature, it is the absence of the
feature: the grant said one thing and the runtime did another.

Layered deliberately:
  * the lattice, exhaustively, with no database
  * the cascade (unit grant ∩ project narrowing) against real rows
  * the runtime gate, which is what an agent actually hits
"""
import json as _json
import uuid as _uuid

import pytest
from sqlalchemy import text

from config.connectors.models import CapabilityEntry, CapabilityManifest
from config.connectors.scoped import ConnectorAccessDenied, ScopedConnector
from shared.authz import connector_access as ca
from shared.authz.connector_grants import effective_access
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


# ── the lattice, with no database ────────────────────────────────────────────

def test_read_and_write_are_incomparable():
    """The property the whole design rests on. Ranking the levels 1/2/3 would make
    `write` imply `read`, which is the escalation the level exists to prevent."""
    # Incomparability is still the property that matters — it is why `permits()` is
    # a subset test and not a `>=` on a rank. Asserted through `modes()` now that the
    # `narrow()` / `contains()` pair has gone with the ceiling they served.
    assert not (ca.modes("read") & ca.modes("write"))
    assert not ca.modes("read") >= ca.modes("write")
    assert not ca.modes("write") >= ca.modes("read")


def test_read_write_admits_both_halves():
    """The top of the lattice admits everything the two below it do."""
    for level in ("read", "write", "read_write"):
        assert ca.modes("read_write") >= ca.modes(level)
    assert not ca.modes("read") >= ca.modes("read_write")
    assert not ca.modes("write") >= ca.modes("read_write")


def test_nothing_permits_anything():
    """None is 'no grant'. It must behave exactly like an unrecognised level."""
    for level in (None, "", "admin", "READ", "read-write"):
        assert not ca.permits(level, "read")
        assert not ca.permits(level, "write")


def test_the_fallback_default_is_read_write():
    """DELIBERATELY NOT least privilege, and the reversal is the point of the test.

    This asserted `read` until it was changed on purpose: a grant defaulting to read
    produced connectors that looked wired and silently refused every write, and
    `DEFAULT_TOOL_MODE` had already been "both" on the stage runtime, so the two
    defaults disagreed about the same question. If somebody "restores least privilege"
    here without also moving DEFAULT_TOOL_MODE, they reintroduce that split.
    """
    assert ca.DEFAULT_ACCESS == "read_write"
    assert ca.DEFAULT_TOOL_MODE == "both"
    # The two constants are the same decision expressed in the two vocabularies —
    # `level_from_mode` is what maps between them, so they must agree through it.
    assert ca.level_from_mode(ca.DEFAULT_TOOL_MODE) == ca.DEFAULT_ACCESS


def test_the_default_never_exceeds_what_a_connector_supports():
    """The guarantee that makes a wider default safe to have at all.

    A default is only useful if the connector can honour it — the capability check at
    decide time refuses anything else, which is how a flat `read` once made Slack and
    MS Teams permanently un-grantable. Widening the fallback must not reintroduce that
    failure from the other side.
    """
    from shared.authz.connector_capabilities import default_access_for, supported_level

    for kind in ("azure_devops", "jira", "slack", "ms_teams", "figma", "github_actions"):
        got = default_access_for(kind)
        supported = supported_level(kind)
        if supported is not None:
            assert got == supported, f"{kind}: defaulted to {got}, supports {supported}"
        # Whatever it picked must be a real level, never None or a made-up string.
        assert got in ca.ACCESS_LEVELS, f"{kind}: {got!r} is not a valid level"


def test_an_unintrospectable_kind_falls_back_to_the_flat_default():
    """MCP servers come through the same grant table and have no manifest at all."""
    from shared.authz.connector_capabilities import default_access_for

    assert default_access_for("not-a-connector-we-ship") == ca.DEFAULT_ACCESS


# ── the runtime gate ─────────────────────────────────────────────────────────

class _Recorder:
    """Records what reached it, so a denial is distinguishable from a call that
    went through and happened to do nothing."""

    connector_name = "recorder"
    display_name = "Recorder"

    def __init__(self):
        self.calls = []

    async def read_adapter(self, operation, **kw):
        self.calls.append(("read", operation))
        return "read-result"

    async def write_adapter(self, operation, **kw):
        self.calls.append(("write", operation))
        return "write-result"

    def capability_manifest(self):
        return CapabilityManifest(
            connector_name="recorder",
            read_capabilities={"list": CapabilityEntry(status="implemented")},
            write_capabilities={"create": CapabilityEntry(status="implemented")},
            listen_capabilities={},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "level,read_ok,write_ok",
    [
        ("read", True, False),
        ("write", False, True),
        ("read_write", True, True),
        (None, False, False),
    ],
)
async def test_the_gate_admits_exactly_its_level(level, read_ok, write_ok):
    inner = _Recorder()
    c = ScopedConnector(inner, level)

    if read_ok:
        assert await c.read_adapter("list") == "read-result"
    else:
        with pytest.raises(ConnectorAccessDenied):
            await c.read_adapter("list")

    if write_ok:
        assert await c.write_adapter("create") == "write-result"
    else:
        with pytest.raises(ConnectorAccessDenied):
            await c.write_adapter("create")

    # A denied call must not have reached the connector at all.
    expected = []
    if read_ok:
        expected.append(("read", "list"))
    if write_ok:
        expected.append(("write", "create"))
    assert inner.calls == expected


@pytest.mark.asyncio
async def test_a_denial_raises_rather_than_returning_empty():
    """A write that silently does nothing looks to an agent like a write that
    worked, and it goes on to report progress it did not make."""
    c = ScopedConnector(_Recorder(), "read")
    with pytest.raises(ConnectorAccessDenied) as exc:
        await c.write_adapter("create")
    assert "read-only" in str(exc.value)
    assert exc.value.mode == "write"


@pytest.mark.asyncio
async def test_the_manifest_narrows_to_the_level():
    """Anything asking a connector what it can do is told the truth for THIS
    project, not the connector's theoretical maximum."""
    read_only = ScopedConnector(_Recorder(), "read").capability_manifest()
    assert read_only.write_capabilities == {}
    assert read_only.read_capabilities

    write_only = ScopedConnector(_Recorder(), "write").capability_manifest()
    assert write_only.read_capabilities == {}
    assert write_only.write_capabilities


# ── the cascade, against real rows ───────────────────────────────────────────

@pytest.fixture
async def tree():
    org, unit, other_unit = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    proj, sibling = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Conn Test')"
        ), {"i": org, "s": "conn-" + org[:8]})
        for wid, slug in ((unit, "payments"), (other_unit, "lending")):
            await s.execute(text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name) "
                "VALUES (:i, :o, :s, :s)"
            ), {"i": wid, "o": org, "s": slug})
    # projects is FORCE RLS — needs the tenant GUC.
    async with get_db_session_for_tenant(org) as s:
        for pid, wid, name in ((proj, unit, "Ledger"), (sibling, unit, "Cards")):
            # Both stages wire jira and slack. A stage that wired nothing has no
            # access to anything since migration 0024, so the wiring has to be real
            # for these tests to be about the LEVEL rather than about step 2.
            await s.execute(text(
                "INSERT INTO projects (id, workspace_id, tenant_id, display_name, connectors) "
                "VALUES (:i, :w, :t, :n, CAST(:c AS jsonb))"
            ), {"i": pid, "w": wid, "t": org, "n": name,
                "c": _json.dumps({"development": ["jira", "slack"],
                                  "testing": ["jira"]})})
    yield {"org": org, "unit": unit, "other_unit": other_unit,
           "project": proj, "sibling": sibling}


async def _grant(org, unit, ref, kind="connector"):
    """Give the unit REACH. No level — migration 0024 removed it from the grant."""
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO integration_grants (tenant_id, kind, target_ref, workspace_id) "
            "VALUES (CAST(:t AS uuid), :k, :r, CAST(:w AS uuid)) ON CONFLICT DO NOTHING"
        ), {"t": org, "k": kind, "r": ref, "w": unit})


async def _project_default(org, project, ref, access, kind="connector"):
    """The project-wide default a stage falls through to when its chip is unset."""
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO project_connector_access "
            "  (tenant_id, project_id, kind, target_ref, access) "
            "VALUES (CAST(:t AS uuid), CAST(:p AS uuid), :k, :r, :a) "
            "ON CONFLICT (tenant_id, project_id, kind, target_ref) "
            "  DO UPDATE SET access = EXCLUDED.access"
        ), {"t": org, "p": project, "k": kind, "r": ref, "a": access})


async def _stage_mode(org, project, agent_id, ref, mode, kind="connector"):
    """The stage's own chip — the most specific answer, and the usual one."""
    import json
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "UPDATE projects SET tool_access_modes = "
            "  COALESCE(tool_access_modes, '{}'::jsonb) || CAST(:m AS jsonb) "
            "WHERE id = CAST(:p AS uuid)"
        ), {"p": project, "m": json.dumps({f"{agent_id}::{kind}::{ref}": mode})})


async def _effective(org, project, ref="jira", agent_id="development"):
    async with get_db_session_for_tenant(org) as s:
        return await effective_access(
            s, tenant_id=org, project_id=project, target_ref=ref, agent_id=agent_id
        )


@pytest.mark.asyncio
async def test_no_grant_means_no_access(tree):
    """Access denied at BU level — the absence of a row IS the denial, and since
    migration 0024 it is the ONLY thing the organisation can still deny."""
    assert await _effective(tree["org"], tree["project"]) is None


@pytest.mark.asyncio
async def test_a_wired_stage_with_nothing_set_gets_read_and_write(tree):
    """What used to be "inherit the unit's level" is now the picker's default.

    There is no unit level left to inherit, and no ceiling over this — the trade
    migration 0024 made deliberately.
    """
    await _grant(tree["org"], tree["unit"], "jira")
    assert await _effective(tree["org"], tree["project"]) == "read_write"
    assert await _effective(tree["org"], tree["sibling"]) == "read_write"


@pytest.mark.asyncio
async def test_a_stage_mode_beats_the_project_default(tree):
    await _grant(tree["org"], tree["unit"], "jira")
    await _project_default(tree["org"], tree["project"], "jira", "read")
    assert await _effective(tree["org"], tree["project"]) == "read"

    await _stage_mode(tree["org"], tree["project"], "development", "jira", "write")
    assert await _effective(tree["org"], tree["project"]) == "write"
    # Its sibling project is untouched — both levels are per project.
    assert await _effective(tree["org"], tree["sibling"]) == "read_write"


@pytest.mark.asyncio
async def test_one_connector_two_stages_two_levels(tree):
    """The reason the decision moved down here in the first place."""
    await _grant(tree["org"], tree["unit"], "jira")
    await _stage_mode(tree["org"], tree["project"], "development", "jira", "both")
    await _stage_mode(tree["org"], tree["project"], "testing", "jira", "read")
    assert await _effective(tree["org"], tree["project"], agent_id="development") == "read_write"
    assert await _effective(tree["org"], tree["project"], agent_id="testing") == "read"


@pytest.mark.asyncio
async def test_a_stage_that_never_wired_it_gets_nothing(tree):
    """With no ceiling left, the stage wiring is what stops one grant reaching all."""
    await _grant(tree["org"], tree["unit"], "jira")
    assert await _effective(tree["org"], tree["project"], agent_id="security") is None


@pytest.mark.asyncio
async def test_another_units_grant_does_not_reach_this_project(tree):
    """Granting Lending does nothing for a Payments project."""
    await _grant(tree["org"], tree["other_unit"], "jira")
    assert await _effective(tree["org"], tree["project"]) is None


@pytest.mark.asyncio
async def test_grants_are_per_connector(tree):
    await _grant(tree["org"], tree["unit"], "jira")
    assert await _effective(tree["org"], tree["project"], "jira") == "read_write"
    assert await _effective(tree["org"], tree["project"], "slack") is None


@pytest.mark.asyncio
async def test_revoking_the_unit_grant_stops_a_configured_stage(tree):
    """Permission change after a connector was already configured. Revoking is the
    whole of the organisation's remaining power, so it must reach a live stage."""
    await _grant(tree["org"], tree["unit"], "jira")
    await _stage_mode(tree["org"], tree["project"], "development", "jira", "both")
    assert await _effective(tree["org"], tree["project"]) == "read_write"

    async with get_db_session_for_tenant(tree["org"]) as s:
        await s.execute(text(
            "DELETE FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
            "  AND target_ref = 'jira'"
        ), {"t": tree["org"]})
    assert await _effective(tree["org"], tree["project"]) is None


@pytest.mark.asyncio
async def test_an_unknown_project_gets_nothing(tree):
    assert await _effective(tree["org"], str(_uuid.uuid4())) is None


# ── unit reach is governance-only ────────────────────────────────────────────
#
# Not a connector rule, but it lives on the same predicates (`read_scope`,
# `can_perform`) and breaking it would silently widen every connector decision
# above, since a project's access is resolved from the project it is asked about.

@pytest.mark.asyncio
async def test_a_delivery_role_bound_to_a_unit_reaches_none_of_its_projects(tree):
    """GOVERNING a unit is what reaches across its projects — not being bound at
    unit level. A Project Admin appointed at unit level could otherwise open, and
    act on, every project in it, including ones nobody had put them on."""
    from shared.authz.can_perform import visible_project_ids

    async with get_db_session_for_tenant(tree["org"]) as s:
        await s.execute(text(
            "INSERT INTO role_bindings (id, user_id, scope_kind, scope_id, role_name, "
            "  status, tenant_id) "
            "VALUES (CAST(:i AS uuid), :u, 'business_unit', CAST(:w AS uuid), "
            "        'project_admin', 'active', CAST(:t AS uuid))"
        ), {"i": str(_uuid.uuid4()), "u": "pa", "w": tree["unit"], "t": tree["org"]})

    async with get_db_session_for_tenant(tree["org"]) as s:
        assert await visible_project_ids(s, user_id="pa", tenant_id=tree["org"]) == []


@pytest.mark.asyncio
async def test_a_governance_role_bound_to_a_unit_reaches_its_projects(tree):
    """The other half — and the reason this is a tier rule rather than a blanket
    narrowing. Governing a unit means seeing what is in it."""
    from shared.authz.can_perform import visible_project_ids

    async with get_db_session_for_tenant(tree["org"]) as s:
        await s.execute(text(
            "INSERT INTO role_bindings (id, user_id, scope_kind, scope_id, role_name, "
            "  status, tenant_id) "
            "VALUES (CAST(:i AS uuid), :u, 'business_unit', CAST(:w AS uuid), "
            "        'bu_admin', 'active', CAST(:t AS uuid))"
        ), {"i": str(_uuid.uuid4()), "u": "bua", "w": tree["unit"], "t": tree["org"]})

    async with get_db_session_for_tenant(tree["org"]) as s:
        seen = await visible_project_ids(s, user_id="bua", tenant_id=tree["org"])
    assert set(seen or []) == {tree["project"], tree["sibling"]}


@pytest.mark.asyncio
async def test_a_delivery_role_reaches_the_project_it_is_bound_to(tree):
    """What a delivery role DOES get: the projects somebody put them on, and no
    sibling of those."""
    from shared.authz.can_perform import visible_project_ids

    async with get_db_session_for_tenant(tree["org"]) as s:
        await s.execute(text(
            "INSERT INTO role_bindings (id, user_id, scope_kind, scope_id, role_name, "
            "  status, tenant_id) "
            "VALUES (CAST(:i AS uuid), :u, 'project', CAST(:p AS uuid), "
            "        'architect', 'active', CAST(:t AS uuid))"
        ), {"i": str(_uuid.uuid4()), "u": "arch", "p": tree["project"], "t": tree["org"]})

    async with get_db_session_for_tenant(tree["org"]) as s:
        seen = await visible_project_ids(s, user_id="arch", tenant_id=tree["org"])
    assert seen == [tree["project"]]
