"""You may not grant a role that holds more than you do.

Every grant endpoint validated the requested role against `ALL_ROLES` and nothing
else, and `ALL_ROLES` contains `org_admin` (`["admin:*"]`). Because
`resolve_permissions_for_user` unions across bindings ignoring scope_kind, an
`org_admin` binding written at PROJECT scope still puts `admin:*` in that person's
token. So anyone holding `member:manage` — a Business Unit Admin, a Project Admin —
could add themselves as `org_admin` on a project they legitimately administered and
be organization admin at the next login.

`custom_roles.py` had answered this question correctly for tenant-defined roles
since task 7. These tests pin the same rule over built-in role grants.

See finding 2 in docs/rbac-audit-2026-08-17.md.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz.grant import grant_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org_tree():
    org, unit = str(_uuid.uuid4()), str(_uuid.uuid4())
    proj = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Rank Test')"
        ), {"i": org, "s": f"rank-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, :s, :s)"
        ), {"i": unit, "o": org, "s": "unit-a"})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'P')"
        ), {"i": proj, "w": unit, "t": org})
        # The person a roster write can target has to exist first — add_project_member
        # deliberately refuses to create accounts.
        await s.execute(text(
            "INSERT INTO users (id, email, tenant_id) VALUES (:i, :e, :t) "
            "ON CONFLICT (id) DO NOTHING"
        ), {"i": "victim", "e": f"victim-{org[:8]}@rankaudit.example.org", "t": org})
    yield {"org": org, "unit": unit, "proj": proj,
           "victim_email": f"victim-{org[:8]}@rankaudit.example.org"}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=perms)
    }


# ── the escalation itself ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_project_admin_cannot_grant_org_admin_on_their_project(org_tree):
    """The headline escalation, at the scope that made it reachable.

    A Project Admin genuinely administers this project, so `_assert_can_write_project`
    passes and `member:manage` passes. Only the rank check stands between them and an
    org_admin binding whose `admin:*` resolves organization-wide.
    """
    t = org_tree
    admin_user = f"pa-{_uuid.uuid4()}"
    await grant_role(admin_user, t["proj"], "project_admin",
                     tenant_id=t["org"], scope_kind="project")

    r = _client().post(
        f"/projects/{t['proj']}/members",
        headers=_hdr(admin_user, t["org"], ["member:manage", "artifact:view"]),
        json={"email": t["victim_email"], "roleName": "org_admin"},
    )
    assert r.status_code == 403, r.text
    assert "admin:*" in str(r.json()["detail"]), r.text


@pytest.mark.asyncio
async def test_bu_admin_cannot_grant_org_admin_in_their_own_unit(org_tree):
    """The same escalation one level up.

    `assert_can_write_workspace` already stopped a Business Unit Admin granting into a
    SIBLING unit. It never stopped them granting org_admin inside their OWN — which
    the admin.py comment acknowledged and did not fix.
    """
    t = org_tree
    bu_user = f"bu-{_uuid.uuid4()}"
    await grant_role(bu_user, t["unit"], "bu_admin",
                     tenant_id=t["org"], scope_kind="business_unit")

    r = _client().post(
        "/admin/assignments",
        headers=_hdr(bu_user, t["org"], ["member:manage", "role:manage"]),
        json={"user_id": "victim", "workspace_id": t["unit"], "role_name": "org_admin"},
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_a_role_change_cannot_escalate_either(org_tree):
    """PATCH was the way around the POST check.

    The roster edit used to rewrite `role_name` with a direct UPDATE, so even once the
    create path was guarded, adding someone as a developer and then editing them to
    org_admin would have reached the same place.
    """
    t = org_tree
    admin_user = f"pa-{_uuid.uuid4()}"
    await grant_role(admin_user, t["proj"], "project_admin",
                     tenant_id=t["org"], scope_kind="project")
    hdr = _hdr(admin_user, t["org"], ["member:manage", "artifact:view"])

    created = _client().post(
        f"/projects/{t['proj']}/members", headers=hdr,
        json={"email": t["victim_email"], "roleName": "developer"},
    )
    assert created.status_code == 201, created.text
    membership_id = created.json()["membershipId"]

    r = _client().patch(
        f"/projects/{t['proj']}/members/{membership_id}", headers=hdr,
        json={"roleName": "org_admin"},
    )
    assert r.status_code == 403, r.text

    # And the refusal left the original role intact — a failed edit must not strip
    # someone of the role they already had.
    async with get_db_session_for_tenant(t["org"]) as s:
        still = (await s.execute(text(
            "SELECT role_name FROM role_bindings WHERE user_id = 'victim' "
            "  AND scope_kind = 'project' AND scope_id = CAST(:p AS uuid)"
        ), {"p": t["proj"]})).scalar()
    assert still == "developer"


# ── what must still work ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_project_admin_can_grant_a_role_within_their_own_authority(org_tree):
    """The rule bites only on excess. A Project Admin holds run:create, artifact:view,
    agent:invoke and the rest of what `developer` carries, so staffing a developer is
    exactly the routine act this endpoint exists for and must not be collateral."""
    t = org_tree
    admin_user = f"pa-{_uuid.uuid4()}"
    await grant_role(admin_user, t["proj"], "project_admin",
                     tenant_id=t["org"], scope_kind="project")

    r = _client().post(
        f"/projects/{t['proj']}/members",
        headers=_hdr(admin_user, t["org"], ["member:manage", "artifact:view"]),
        json={"email": t["victim_email"], "roleName": "developer"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "developer"


@pytest.mark.asyncio
async def test_a_specialist_permission_the_admin_lacks_is_not_an_escalation(org_tree):
    """The case that ruled out a full subset rule, pinned so it cannot come back.

    `developer` carries `skill:edit`; `project_admin` does not. Under a strict
    "grant only what you hold" rule a Project Admin could not staff a developer,
    which is the most ordinary act on this endpoint. Conferring a sandbox-editing
    permission gives the recipient no authority over the person conferring it, so
    it is not what the rank check is for — only ESCALATING_PERMISSIONS are compared.
    """
    from shared.authz.grant_guard import ESCALATING_PERMISSIONS
    from shared.authz.permissions import _ROLE_PERMISSIONS

    developer = set(_ROLE_PERMISSIONS["developer"])
    project_admin = set(_ROLE_PERMISSIONS["project_admin"])
    # The premise: developer really does hold something project_admin does not.
    assert developer - project_admin, "fixture assumption broken"
    # And none of it is access-management authority.
    assert not (developer & ESCALATING_PERMISSIONS) - project_admin

    t = org_tree
    admin_user = f"pa-{_uuid.uuid4()}"
    await grant_role(admin_user, t["proj"], "project_admin",
                     tenant_id=t["org"], scope_kind="project")
    r = _client().post(
        f"/projects/{t['proj']}/members",
        headers=_hdr(admin_user, t["org"], ["member:manage", "artifact:view"]),
        json={"email": t["victim_email"], "roleName": "developer"},
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_org_admin_may_grant_anything(org_tree):
    """`admin:*` passes everything, by the same reasoning as custom roles: the
    wildcard IS the full catalogue, so an Organization Admin assigning any role is
    not an escalation. Without this branch the check would lock out the one person
    who is supposed to be able to appoint their own successor."""
    t = org_tree
    owner = f"oa-{_uuid.uuid4()}"
    await grant_role(owner, t["org"], "org_admin",
                     tenant_id=t["org"], scope_kind="organization")

    r = _client().post(
        "/admin/assignments",
        headers=_hdr(owner, t["org"], ["admin:*"]),
        json={"user_id": "victim", "workspace_id": t["unit"], "role_name": "org_admin"},
    )
    assert r.status_code == 200, r.text


# ── the property that makes the check trustworthy ────────────────────────────


@pytest.mark.asyncio
async def test_the_check_reads_the_database_not_the_token(org_tree):
    """A token claiming more than the bindings back must not widen a grant.

    Same reasoning as `custom_roles.py`: a binding outlives the session that created
    it, so a token issued before a demotion would otherwise mint a durable grant from
    a stale claim. This is why the guard calls `resolve_permissions_for_user` rather
    than reading `request.state.permissions`.
    """
    t = org_tree
    bu_user = f"bu-{_uuid.uuid4()}"
    await grant_role(bu_user, t["unit"], "bu_admin",
                     tenant_id=t["org"], scope_kind="business_unit")

    # The JWT asserts the wildcard; the binding grants only bu_admin's set.
    r = _client().post(
        "/admin/assignments",
        headers=_hdr(bu_user, t["org"], ["admin:*"]),
        json={"user_id": "victim", "workspace_id": t["unit"], "role_name": "org_admin"},
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_revocation_is_rank_checked_too(org_tree):
    """Symmetric with the grant. Someone who may not confer a role has no standing to
    take it away either — otherwise a Business Unit Admin could strip the Organization
    Admin's binding, which is the same authority pointed the other way."""
    t = org_tree
    bu_user = f"bu-{_uuid.uuid4()}"
    await grant_role(bu_user, t["unit"], "bu_admin",
                     tenant_id=t["org"], scope_kind="business_unit")
    await grant_role("victim", t["unit"], "org_admin",
                     tenant_id=t["org"], scope_kind="business_unit")

    r = _client().request(
        "DELETE", "/admin/assignments",
        headers=_hdr(bu_user, t["org"], ["member:manage"]),
        json={"user_id": "victim", "workspace_id": t["unit"], "role_name": "org_admin"},
    )
    assert r.status_code == 403, r.text
