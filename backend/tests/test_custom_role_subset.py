"""Custom roles cannot grant more than their creator holds.

Without this rule the endpoint was a direct privilege escalation: `role:manage` is
held by a Business Unit Admin, and any catalogue permission could be packaged into a
role and then assigned — including to themselves. The pre-existing check only asked
whether a permission EXISTS, never whether the caller had it.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz.grant import grant_custom_role, grant_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org_tree():
    org, bu_a, bu_b = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    proj = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Subset Test')"
        ), {"i": org, "s": f"subset-{org[:8]}"})
        for wid, slug in ((bu_a, "unit-a"), (bu_b, "unit-b")):
            await s.execute(text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name) "
                "VALUES (:i, :o, :s, :s)"
            ), {"i": wid, "o": org, "s": slug})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'P')"
        ), {"i": proj, "w": bu_a, "t": org})
    yield {"org": org, "bu_a": bu_a, "bu_b": bu_b, "proj": proj}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id=user_id, tenant_id=org, permissions=perms)}"}


@pytest.mark.asyncio
async def test_creator_cannot_grant_a_permission_they_lack(org_tree):
    """The escalation.

    bu_admin is a GOVERNANCE role: it holds cost:view and audit:view but no delivery
    permissions at all, so run:create is something it can never hand out. That the
    tier split makes this case real rather than contrived is the point — a unit admin
    packaging delivery access into a role would be manufacturing authority they were
    deliberately not given.
    """
    t = org_tree
    user = f"bu-{_uuid.uuid4()}"
    await grant_role(user, t["bu_a"], "bu_admin", tenant_id=t["org"], scope_kind="business_unit")

    r = _client().post(
        f"/admin/custom-roles/business-unit/{t['bu_a']}",
        headers=_hdr(user, t["org"], ["role:manage", "cost:view"]),
        json={"name": "Sneaky", "permissions": ["cost:view", "run:create"]},
    )
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert "run:create" in detail, detail
    # Only the excess is named — the permission they DO hold is not.
    assert "cost:view" not in detail, detail


@pytest.mark.asyncio
async def test_creator_can_grant_a_subset_of_what_they_hold(org_tree):
    t = org_tree
    user = f"bu-{_uuid.uuid4()}"
    await grant_role(user, t["bu_a"], "bu_admin", tenant_id=t["org"], scope_kind="business_unit")

    r = _client().post(
        f"/admin/custom-roles/business-unit/{t['bu_a']}",
        headers=_hdr(user, t["org"], ["role:manage", "cost:view"]),
        json={"name": "Reviewer", "permissions": ["cost:view", "audit:view"]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scopeKind"] == "business_unit"
    assert body["scopeId"] == t["bu_a"]
    assert body["createdBy"] == user


@pytest.mark.asyncio
async def test_the_check_reads_the_database_not_the_token(org_tree):
    """A token claiming permissions the bindings do not back must not widen the role.

    Permissions are re-resolved here rather than read from request.state, because a
    custom role outlives the session that created it: a token issued before a demotion
    would otherwise mint a durable grant from a stale claim.
    """
    t = org_tree
    user = f"bu-{_uuid.uuid4()}"
    await grant_role(user, t["bu_a"], "bu_admin", tenant_id=t["org"], scope_kind="business_unit")

    # The JWT asserts run:create; the binding does not grant it.
    r = _client().post(
        f"/admin/custom-roles/business-unit/{t['bu_a']}",
        headers=_hdr(user, t["org"], ["role:manage", "run:create"]),
        json={"name": "StaleClaim", "permissions": ["run:create"]},
    )
    assert r.status_code == 403, r.text
    assert "run:create" in r.json()["detail"]


@pytest.mark.asyncio
async def test_org_admin_wildcard_may_compose_anything(org_tree):
    """admin:* IS the full catalogue, so composing any role is not an escalation."""
    t = org_tree
    admin = f"admin-{_uuid.uuid4()}"
    await grant_role(admin, t["org"], "org_admin", tenant_id=t["org"], scope_kind="organization")

    r = _client().post(
        "/admin/custom-roles",
        headers=_hdr(admin, t["org"], ["admin:*"]),
        json={"name": "Anything", "permissions": ["cost:view", "audit:view", "run:create"]},
    )
    assert r.status_code == 201, r.text
    assert r.json()["scopeKind"] == "organization"


@pytest.mark.asyncio
async def test_bu_admin_cannot_create_an_organization_wide_role(org_tree):
    """role:manage is unit authority — an org-wide role is not theirs to define."""
    t = org_tree
    user = f"bu-{_uuid.uuid4()}"
    await grant_role(user, t["bu_a"], "bu_admin", tenant_id=t["org"], scope_kind="business_unit")

    r = _client().post(
        "/admin/custom-roles",
        headers=_hdr(user, t["org"], ["role:manage", "artifact:view"]),
        json={"name": "OrgWide", "permissions": ["artifact:view"]},
    )
    assert r.status_code == 403, r.text
    assert "Organization Admin" in r.json()["detail"]


@pytest.mark.asyncio
async def test_bu_admin_cannot_create_a_role_in_another_unit(org_tree):
    t = org_tree
    user = f"bu-{_uuid.uuid4()}"
    await grant_role(user, t["bu_a"], "bu_admin", tenant_id=t["org"], scope_kind="business_unit")

    r = _client().post(
        f"/admin/custom-roles/business-unit/{t['bu_b']}",
        headers=_hdr(user, t["org"], ["role:manage", "artifact:view"]),
        json={"name": "Elsewhere", "permissions": ["artifact:view"]},
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_wildcards_are_still_rejected_outright(org_tree):
    t = org_tree
    admin = f"admin-{_uuid.uuid4()}"
    await grant_role(admin, t["org"], "org_admin", tenant_id=t["org"], scope_kind="organization")

    r = _client().post(
        "/admin/custom-roles",
        headers=_hdr(admin, t["org"], ["admin:*"]),
        json={"name": "Wild", "permissions": ["admin:*"]},
    )
    assert r.status_code == 422, r.text


# ── owner scope constrains where the role may be ASSIGNED ────────────────────

@pytest.mark.asyncio
async def test_unit_owned_role_cannot_be_assigned_outside_its_unit(org_tree):
    """Otherwise the owner scope is decoration: define narrow, bind wide."""
    t = org_tree
    role_id = str(_uuid.uuid4())
    async with get_db_session_for_tenant(t["org"]) as s:
        await s.execute(text(
            "INSERT INTO custom_roles (id, tenant_id, name, description, scope_kind, scope_id) "
            "VALUES (:i, :t, 'UnitRole', NULL, 'business_unit', :sid)"
        ), {"i": role_id, "t": t["org"], "sid": t["bu_a"]})

    subject = f"subject-{_uuid.uuid4()}"

    # Inside the owning unit: allowed.
    await grant_custom_role(
        subject, t["bu_a"], role_id, tenant_id=t["org"], scope_kind="business_unit"
    )
    # A project inside the owning unit: allowed, it is part of what that unit runs.
    await grant_custom_role(
        subject, t["proj"], role_id, tenant_id=t["org"], scope_kind="project"
    )

    # A sibling unit: refused.
    with pytest.raises(ValueError, match="cannot be assigned"):
        await grant_custom_role(
            subject, t["bu_b"], role_id, tenant_id=t["org"], scope_kind="business_unit"
        )
    # Organization scope: refused — that is the widening this prevents.
    with pytest.raises(ValueError, match="cannot be assigned"):
        await grant_custom_role(
            subject, t["org"], role_id, tenant_id=t["org"], scope_kind="organization"
        )


@pytest.mark.asyncio
async def test_org_owned_role_is_assignable_anywhere(org_tree):
    t = org_tree
    role_id = str(_uuid.uuid4())
    async with get_db_session_for_tenant(t["org"]) as s:
        await s.execute(text(
            "INSERT INTO custom_roles (id, tenant_id, name, description, scope_kind, scope_id) "
            "VALUES (:i, :t, 'OrgRole', NULL, 'organization', :sid)"
        ), {"i": role_id, "t": t["org"], "sid": t["org"]})

    subject = f"subject-{_uuid.uuid4()}"
    await grant_custom_role(subject, t["bu_a"], role_id, tenant_id=t["org"], scope_kind="business_unit")
    await grant_custom_role(subject, t["bu_b"], role_id, tenant_id=t["org"], scope_kind="business_unit")


@pytest.mark.asyncio
async def test_unknown_custom_role_is_rejected(org_tree):
    t = org_tree
    with pytest.raises(ValueError, match="unknown custom role"):
        await grant_custom_role(
            "someone", t["bu_a"], str(_uuid.uuid4()),
            tenant_id=t["org"], scope_kind="business_unit",
        )
