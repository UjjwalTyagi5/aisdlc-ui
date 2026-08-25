"""Unit tests for `resolve_actor_tier_access` — real per-resource (owns, may_propose)
lookups scoped to actual role_bindings, not a tenant-wide "highest role" label.
Agent Studio sub-project 3 (skills promotion parity)."""
import uuid

import pytest
from sqlalchemy import text

from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.routers import agent_profiles as ap

# The workspace-scope/project-scope cases create real organizations + workspaces
# rows (projects.workspace_id FKs workspaces.id, which FKs organizations.id) —
# clean them up the way this repo's other org/workspace-creating suites do
# (e.g. test_project_business_unit.py's `two_units` fixture).
pytestmark = pytest.mark.usefixtures("purge_created_orgs")


async def _bind_role(tenant_id: str, user_id: str, role: str, scope_kind: str, scope_id: str | None) -> None:
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(text(
            "INSERT INTO users (id, email, password_hash, tenant_id, active) "
            "VALUES (:i, :e, 'x', CAST(:t AS uuid), true) ON CONFLICT (id) DO NOTHING"
        ), {"i": user_id, "e": f"{user_id}@example.com", "t": tenant_id})
        await s.execute(text(
            "INSERT INTO role_bindings (id, user_id, scope_kind, scope_id, role_name, tenant_id) "
            "VALUES (gen_random_uuid(), :u, :sk, CAST(:si AS uuid), :r, CAST(:t AS uuid))"
        ), {"u": user_id, "sk": scope_kind, "si": scope_id, "r": role, "t": tenant_id})


async def _make_project(tenant_id, workspace_id):
    # NOTE: the plan's brief assumed `status` / `created_by` columns on `projects`;
    # neither exists (see migrations/versions/0001_baseline.py + shared/models/orm.py
    # Project — the real status-like column is `approval_status`, server_default
    # 'active', and there is no `created_by` at all). Trimmed to the columns that
    # actually exist and are NOT NULL with no default: id, tenant_id, workspace_id,
    # display_name. `projects.workspace_id` also has a real FK to `workspaces.id`
    # (which in turn FKs `organizations.id`), so — matching this repo's established
    # convention (e.g. test_project_business_unit.py's `two_units` fixture) — an
    # organization (tenant) + workspace row must exist first, inserted via the
    # RLS-bypassing superuser session since callers here use a tenant id that has no
    # organizations row yet. ON CONFLICT DO NOTHING so a repeated workspace_id
    # (same test, two projects in one workspace) is a no-op the second time.
    project_id = str(uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) "
            "VALUES (CAST(:i AS uuid), :s, 'Tier Access Test') ON CONFLICT (id) DO NOTHING"
        ), {"i": tenant_id, "s": f"tier-access-{tenant_id}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (CAST(:i AS uuid), CAST(:o AS uuid), :s, 'Test Workspace') "
            "ON CONFLICT (id) DO NOTHING"
        ), {"i": workspace_id, "o": tenant_id, "s": f"ws-{workspace_id}"})
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(text(
            "INSERT INTO projects (id, tenant_id, workspace_id, display_name) "
            "VALUES (CAST(:p AS uuid), CAST(:t AS uuid), CAST(:w AS uuid), 'Test Project')"
        ), {"p": project_id, "t": tenant_id, "w": workspace_id})
    return project_id


@pytest.mark.asyncio
async def test_org_owns_via_admin_wildcard_only():
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    owns, may_propose = await ap.resolve_actor_tier_access(tenant, user_id, ["admin:*"], "org", None)
    assert owns is True
    owns, _ = await ap.resolve_actor_tier_access(tenant, user_id, [], "org", None)
    assert owns is False


@pytest.mark.asyncio
async def test_org_may_propose_for_bu_admin_anywhere():
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "bu_admin", "business_unit", str(uuid.uuid4()))
    owns, may_propose = await ap.resolve_actor_tier_access(tenant, user_id, [], "org", None)
    assert owns is False
    assert may_propose is True


@pytest.mark.asyncio
async def test_workspace_owns_requires_binding_on_this_exact_workspace():
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    other_ws_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "bu_admin", "business_unit", ws_id)

    owns, _ = await ap.resolve_actor_tier_access(tenant, user_id, [], "workspace", ws_id)
    assert owns is True
    owns, _ = await ap.resolve_actor_tier_access(tenant, user_id, [], "workspace", other_ws_id)
    assert owns is False


@pytest.mark.asyncio
async def test_workspace_may_propose_for_project_admin_on_a_project_in_this_ws():
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    other_ws_id = str(uuid.uuid4())
    project_id = await _make_project(tenant, ws_id)
    await _bind_role(tenant, user_id, "project_admin", "project", project_id)

    _, may_propose = await ap.resolve_actor_tier_access(tenant, user_id, [], "workspace", ws_id)
    assert may_propose is True
    _, may_propose = await ap.resolve_actor_tier_access(tenant, user_id, [], "workspace", other_ws_id)
    assert may_propose is False


@pytest.mark.asyncio
async def test_project_owns_requires_binding_on_this_exact_project():
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    project_id = await _make_project(tenant, ws_id)
    other_project_id = await _make_project(tenant, ws_id)
    await _bind_role(tenant, user_id, "project_admin", "project", project_id)

    owns, _ = await ap.resolve_actor_tier_access(tenant, user_id, [], "project", project_id)
    assert owns is True
    owns, _ = await ap.resolve_actor_tier_access(tenant, user_id, [], "project", other_project_id)
    assert owns is False


@pytest.mark.asyncio
async def test_project_may_propose_for_any_member_except_contributor():
    tenant = str(uuid.uuid4())
    dev_id = str(uuid.uuid4())
    contributor_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    project_id = await _make_project(tenant, ws_id)
    await _bind_role(tenant, dev_id, "developer", "project", project_id)
    await _bind_role(tenant, contributor_id, "contributor", "project", project_id)

    _, may_propose = await ap.resolve_actor_tier_access(tenant, dev_id, [], "project", project_id)
    assert may_propose is True
    _, may_propose = await ap.resolve_actor_tier_access(tenant, contributor_id, [], "project", project_id)
    assert may_propose is False


@pytest.mark.asyncio
async def test_project_may_propose_false_for_unrelated_project():
    tenant = str(uuid.uuid4())
    dev_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    project_a = await _make_project(tenant, ws_id)
    project_b = await _make_project(tenant, ws_id)
    await _bind_role(tenant, dev_id, "developer", "project", project_a)

    _, may_propose = await ap.resolve_actor_tier_access(tenant, dev_id, [], "project", project_b)
    assert may_propose is False


@pytest.mark.asyncio
async def test_owns_implies_no_need_for_may_propose_but_both_are_reported_independently():
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    project_id = await _make_project(tenant, ws_id)
    await _bind_role(tenant, user_id, "project_admin", "project", project_id)

    owns, may_propose = await ap.resolve_actor_tier_access(tenant, user_id, [], "project", project_id)
    assert owns is True
    # project_admin's own project binding also matches the "any member" propose
    # query — both booleans are independently correct, callers decide precedence.
    assert may_propose is True
