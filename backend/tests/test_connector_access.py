"""Read, write, or both — who may do what with a connector.

The interesting assertions are the negative ones. A connector granted for reading
that can still be written to is not a partial feature, it is the absence of the
feature: the grant said one thing and the runtime did another.

Layered deliberately:
  * the lattice, exhaustively, with no database
  * the cascade (unit grant ∩ project narrowing) against real rows
  * the runtime gate, which is what an agent actually hits
"""
import itertools
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
    assert ca.narrow("read", "write") is None
    assert ca.narrow("write", "read") is None
    assert not ca.contains("read", "write")
    assert not ca.contains("write", "read")


def test_read_write_contains_both_halves():
    for level in ("read", "write", "read_write"):
        assert ca.contains("read_write", level)
    assert not ca.contains("read", "read_write")
    assert not ca.contains("write", "read_write")


def test_narrowing_is_intersection_for_every_pair():
    for parent, child in itertools.product(ca.ACCESS_LEVELS, repeat=2):
        got = ca.narrow(parent, child)
        assert got == ca.from_modes(ca.modes(parent) & ca.modes(child))
        # A narrowing can never yield more than its parent.
        assert got is None or ca.contains(parent, got)


def test_nothing_permits_anything():
    """None is 'no grant'. It must behave exactly like an unrecognised level."""
    for level in (None, "", "admin", "READ", "read-write"):
        assert not ca.permits(level, "read")
        assert not ca.permits(level, "write")


def test_the_default_for_a_new_grant_is_least_privilege():
    assert ca.DEFAULT_ACCESS == "read"


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
            await s.execute(text(
                "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
                "VALUES (:i, :w, :t, :n)"
            ), {"i": pid, "w": wid, "t": org, "n": name})
    yield {"org": org, "unit": unit, "other_unit": other_unit,
           "project": proj, "sibling": sibling}


async def _grant(org, unit, ref, access, kind="connector"):
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO integration_grants "
            "  (tenant_id, kind, target_ref, workspace_id, access) "
            "VALUES (CAST(:t AS uuid), :k, :r, CAST(:w AS uuid), :a) "
            "ON CONFLICT (tenant_id, kind, target_ref, workspace_id) "
            "  DO UPDATE SET access = EXCLUDED.access"
        ), {"t": org, "k": kind, "r": ref, "w": unit, "a": access})


async def _narrow_project(org, project, ref, access, kind="connector"):
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO project_connector_access "
            "  (tenant_id, project_id, kind, target_ref, access) "
            "VALUES (CAST(:t AS uuid), CAST(:p AS uuid), :k, :r, :a) "
            "ON CONFLICT (tenant_id, project_id, kind, target_ref) "
            "  DO UPDATE SET access = EXCLUDED.access"
        ), {"t": org, "p": project, "k": kind, "r": ref, "a": access})


async def _effective(org, project, ref="jira"):
    async with get_db_session_for_tenant(org) as s:
        return await effective_access(
            s, tenant_id=org, project_id=project, target_ref=ref
        )


@pytest.mark.asyncio
async def test_no_grant_means_no_access(tree):
    """Access denied at BU level — the absence of a row IS the denial."""
    assert await _effective(tree["org"], tree["project"]) is None


@pytest.mark.asyncio
async def test_a_project_inherits_its_units_grant(tree):
    """No override means inherit. Absence must not read as denial, or adding the
    override table would have revoked every project's integrations on deploy."""
    await _grant(tree["org"], tree["unit"], "jira", "read_write")
    assert await _effective(tree["org"], tree["project"]) == "read_write"
    assert await _effective(tree["org"], tree["sibling"]) == "read_write"


@pytest.mark.asyncio
async def test_a_project_may_be_narrowed_below_its_unit(tree):
    await _grant(tree["org"], tree["unit"], "jira", "read_write")
    await _narrow_project(tree["org"], tree["project"], "jira", "read")
    assert await _effective(tree["org"], tree["project"]) == "read"
    # Its sibling is untouched — narrowing is per project.
    assert await _effective(tree["org"], tree["sibling"]) == "read_write"


@pytest.mark.asyncio
async def test_a_project_cannot_exceed_its_unit(tree):
    """The hierarchy rule. A project asking for more than its unit holds gets the
    intersection, never the wider of the two."""
    await _grant(tree["org"], tree["unit"], "jira", "read")
    await _narrow_project(tree["org"], tree["project"], "jira", "read_write")
    assert await _effective(tree["org"], tree["project"]) == "read"


@pytest.mark.asyncio
async def test_incomparable_levels_yield_no_access(tree):
    """A unit granted read and a project set to write share no operation. The
    answer is nothing — not 'one of them'."""
    await _grant(tree["org"], tree["unit"], "jira", "read")
    await _narrow_project(tree["org"], tree["project"], "jira", "write")
    assert await _effective(tree["org"], tree["project"]) is None


@pytest.mark.asyncio
async def test_another_units_grant_does_not_reach_this_project(tree):
    """Granting Lending does nothing for a Payments project."""
    await _grant(tree["org"], tree["other_unit"], "jira", "read_write")
    assert await _effective(tree["org"], tree["project"]) is None


@pytest.mark.asyncio
async def test_grants_are_per_connector(tree):
    await _grant(tree["org"], tree["unit"], "jira", "read_write")
    assert await _effective(tree["org"], tree["project"], "jira") == "read_write"
    assert await _effective(tree["org"], tree["project"], "slack") is None


@pytest.mark.asyncio
async def test_narrowing_the_unit_later_narrows_the_project(tree):
    """Permission change after a connector was already configured."""
    await _grant(tree["org"], tree["unit"], "jira", "read_write")
    assert await _effective(tree["org"], tree["project"]) == "read_write"

    await _grant(tree["org"], tree["unit"], "jira", "read")
    assert await _effective(tree["org"], tree["project"]) == "read"


@pytest.mark.asyncio
async def test_an_unknown_project_gets_nothing(tree):
    assert await _effective(tree["org"], str(_uuid.uuid4())) is None
