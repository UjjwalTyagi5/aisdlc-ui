"""`can_perform` — the scope-chain resolver.

Builds a real org / business unit / project / workstream chain in the database and
asserts each rule against it. Deliberately not mocked: the whole point of this
resolver is the SQL walk up the chain and the expiry comparison, and a mock would
assert that the test author understood the schema rather than that the code does.
"""
import uuid as _uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from shared.authz.can_perform import can_perform, explain_can_perform, resolve_scope_chain
from shared.authz.grant import grant_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser

# The fixture below removes the org it creates, but `grant_role` also upserts a row
# into `users` for every subject it binds — global, no tenant GUC needed, and easy to
# forget. The conftest fixture sweeps users left pointing at a deleted organization.
pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def fixture_tree():
    """One org, one BU, two projects, one workstream under project A."""
    org = str(_uuid.uuid4())
    bu = str(_uuid.uuid4())
    proj_a = str(_uuid.uuid4())
    proj_b = str(_uuid.uuid4())
    ws = str(_uuid.uuid4())
    slug = org[:8]

    # organizations and workspaces are GLOBAL (no RLS) — a plain session writes them.
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'CanPerform Test')"
        ), {"i": org, "s": f"cp-{slug}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org})

    # projects and workstreams are FORCE RLS. FORCE applies to the table owner too and
    # the app role is NOBYPASSRLS, so the WITH CHECK policy rejects an insert unless
    # app.current_tenant_id is set — which only the tenant session does.
    async with get_db_session_for_tenant(org) as s:
        for pid, name in ((proj_a, "Project A"), (proj_b, "Project B")):
            await s.execute(text(
                "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
                "VALUES (:i, :w, :t, :n)"
            ), {"i": pid, "w": bu, "t": org, "n": name})
        await s.execute(text(
            "INSERT INTO workstreams (id, project_id, tenant_id, display_name, slug) "
            "VALUES (:i, :p, :t, 'Stream One', 'stream-one')"
        ), {"i": ws, "p": proj_a, "t": org})

    yield {"org": org, "bu": bu, "proj_a": proj_a, "proj_b": proj_b, "ws": ws}

    async with get_db_session_for_tenant(org) as s:
        await s.execute(text("DELETE FROM role_bindings"))
        await s.execute(text("DELETE FROM workstreams"))
        await s.execute(text("DELETE FROM projects"))
    async with get_db_session_superuser() as s:
        await s.execute(text("DELETE FROM workspaces WHERE organization_id = CAST(:t AS uuid)"), {"t": org})
        await s.execute(text("DELETE FROM organizations WHERE id = CAST(:t AS uuid)"), {"t": org})


# ── the chain itself ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chain_is_built_widest_first(fixture_tree):
    t = fixture_tree
    async with get_db_session_for_tenant(t["org"]) as s:
        chain = await resolve_scope_chain(s, t["org"], "workstream", t["ws"])
    assert chain == [
        ("organization", t["org"]),
        ("business_unit", t["bu"]),
        ("project", t["proj_a"]),
        ("workstream", t["ws"]),
    ]


@pytest.mark.asyncio
async def test_unknown_resource_yields_no_chain(fixture_tree):
    t = fixture_tree
    async with get_db_session_for_tenant(t["org"]) as s:
        assert await resolve_scope_chain(s, t["org"], "project", str(_uuid.uuid4())) == []
        assert await resolve_scope_chain(s, t["org"], "project", "not-a-uuid") == []


# ── the four cases named in the spec ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_org_admin_reaches_a_business_unit_resource(fixture_tree):
    """A role at an ANCESTOR scope applies downward to the resource."""
    t = fixture_tree
    user = f"user-{_uuid.uuid4()}"
    await grant_role(user, t["org"], "org_admin", tenant_id=t["org"], scope_kind="organization")

    async with get_db_session_for_tenant(t["org"]) as s:
        assert await can_perform(
            s, user_id=user, permission="member:manage", tenant_id=t["org"],
            resource_kind="business_unit", resource_id=t["bu"],
        ) is True


@pytest.mark.asyncio
async def test_project_developer_cannot_reach_another_project(fixture_tree):
    """A role at a project does not reach a sibling — it is not on that chain."""
    t = fixture_tree
    user = f"user-{_uuid.uuid4()}"
    await grant_role(user, t["proj_a"], "developer", tenant_id=t["org"], scope_kind="project")

    async with get_db_session_for_tenant(t["org"]) as s:
        assert await can_perform(
            s, user_id=user, permission="run:create", tenant_id=t["org"],
            resource_kind="project", resource_id=t["proj_a"],
        ) is True
        assert await can_perform(
            s, user_id=user, permission="run:create", tenant_id=t["org"],
            resource_kind="project", resource_id=t["proj_b"],
        ) is False


@pytest.mark.asyncio
async def test_elevation_within_expiry_is_allowed(fixture_tree):
    t = fixture_tree
    user = f"user-{_uuid.uuid4()}"
    await grant_role(
        user, t["proj_a"], "project_admin", tenant_id=t["org"], scope_kind="project",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
        granted_by="tester",
    )
    async with get_db_session_for_tenant(t["org"]) as s:
        assert await can_perform(
            s, user_id=user, permission="member:manage", tenant_id=t["org"],
            resource_kind="project", resource_id=t["proj_a"],
        ) is True


@pytest.mark.asyncio
async def test_expired_elevation_is_denied(fixture_tree):
    """Enforced by the clock — no sweep job runs in this test."""
    t = fixture_tree
    user = f"user-{_uuid.uuid4()}"
    await grant_role(
        user, t["proj_a"], "project_admin", tenant_id=t["org"], scope_kind="project",
        expires_at=datetime.now(tz=timezone.utc) - timedelta(seconds=1),
        granted_by="tester",
    )
    async with get_db_session_for_tenant(t["org"]) as s:
        decision = await explain_can_perform(
            s, user_id=user, permission="member:manage", tenant_id=t["org"],
            resource_kind="project", resource_id=t["proj_a"],
        )
    assert decision.allowed is False
    assert "no active assignment" in decision.reason


# ── deny-by-default in every other direction ─────────────────────────────────

@pytest.mark.asyncio
async def test_no_assignment_anywhere_is_denied(fixture_tree):
    t = fixture_tree
    async with get_db_session_for_tenant(t["org"]) as s:
        assert await can_perform(
            s, user_id=f"stranger-{_uuid.uuid4()}", permission="artifact:view",
            tenant_id=t["org"], resource_kind="project", resource_id=t["proj_a"],
        ) is False


@pytest.mark.asyncio
async def test_assigned_but_role_lacks_the_permission(fixture_tree):
    """Matched on the chain, wrong verb — a distinct reason from 'no assignment'."""
    t = fixture_tree
    user = f"user-{_uuid.uuid4()}"
    await grant_role(user, t["proj_a"], "developer", tenant_id=t["org"], scope_kind="project")

    async with get_db_session_for_tenant(t["org"]) as s:
        decision = await explain_can_perform(
            s, user_id=user, permission="member:manage", tenant_id=t["org"],
            resource_kind="project", resource_id=t["proj_a"],
        )
    assert decision.allowed is False
    assert "no role there carries" in decision.reason


@pytest.mark.asyncio
async def test_project_role_reaches_a_workstream_beneath_it(fixture_tree):
    """The chain is four deep: a project role covers that project's workstreams."""
    t = fixture_tree
    user = f"user-{_uuid.uuid4()}"
    await grant_role(user, t["proj_a"], "developer", tenant_id=t["org"], scope_kind="project")

    async with get_db_session_for_tenant(t["org"]) as s:
        assert await can_perform(
            s, user_id=user, permission="run:create", tenant_id=t["org"],
            resource_kind="workstream", resource_id=t["ws"],
        ) is True


@pytest.mark.asyncio
async def test_workstream_role_does_not_reach_its_project(fixture_tree):
    """Upward is never implied: the project is not on the workstream's descendants."""
    t = fixture_tree
    user = f"user-{_uuid.uuid4()}"
    await grant_role(user, t["ws"], "developer", tenant_id=t["org"], scope_kind="workstream")

    async with get_db_session_for_tenant(t["org"]) as s:
        assert await can_perform(
            s, user_id=user, permission="run:create", tenant_id=t["org"],
            resource_kind="workstream", resource_id=t["ws"],
        ) is True
        assert await can_perform(
            s, user_id=user, permission="run:create", tenant_id=t["org"],
            resource_kind="project", resource_id=t["proj_a"],
        ) is False


@pytest.mark.asyncio
async def test_deactivated_assignment_is_denied(fixture_tree):
    t = fixture_tree
    user = f"user-{_uuid.uuid4()}"
    await grant_role(user, t["proj_a"], "developer", tenant_id=t["org"], scope_kind="project")
    # role_bindings is FORCE RLS — the update needs the tenant GUC set.
    async with get_db_session_for_tenant(t["org"]) as s:
        await s.execute(text(
            "UPDATE role_bindings SET status='deactivated' WHERE user_id=:u"
        ), {"u": user})

    async with get_db_session_for_tenant(t["org"]) as s:
        assert await can_perform(
            s, user_id=user, permission="run:create", tenant_id=t["org"],
            resource_kind="project", resource_id=t["proj_a"],
        ) is False


@pytest.mark.asyncio
async def test_missing_tenant_or_user_is_denied(fixture_tree):
    t = fixture_tree
    async with get_db_session_for_tenant(t["org"]) as s:
        assert await can_perform(
            s, user_id="", permission="run:create", tenant_id=t["org"],
            resource_kind="project", resource_id=t["proj_a"],
        ) is False
        assert await can_perform(
            s, user_id="someone", permission="run:create", tenant_id="",
            resource_kind="project", resource_id=t["proj_a"],
        ) is False
