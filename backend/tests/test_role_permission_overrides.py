"""Retuning a built-in role: does it stick, and does it apply everywhere?

THE TEST THAT MATTERS MOST is `test_both_permission_paths_agree`. Permissions are read
on two independent paths — the login resolver that bakes them into the JWT, and
can_perform's scoped check — and an override that applied on only one would present as
"works on the dashboard, denied on the project page". That is a day of debugging, so it
gets an assertion.

The second is `test_the_boot_reconcile_does_not_wipe_an_override`. The whole reason
overrides are a separate table is that `seed_rbac_catalog` deletes anything in
`role_permissions` that the code matrix does not declare. If that ever reaches the
override table, every customisation silently reverts on the next restart — the exact
failure this design exists to prevent.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz.catalog import seed_rbac_catalog
from shared.authz.resolver import resolve_permissions_for_user
from shared.authz.role_permissions import effective_by_role, overrides, shipped_defaults
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org():
    org_id, bu, project = str(_uuid.uuid4()), str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Roles Test')"
        ), {"i": org_id, "s": f"roles-{org_id[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org_id})
    async with get_db_session_for_tenant(org_id) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name, provider_kind) "
            "VALUES (:i, :w, :t, 'Proj', 'github')"
        ), {"i": project, "w": bu, "t": org_id})
    yield {"org": org_id, "bu": bu, "project": project}


async def _set_override(org: dict, role: str, permissions: list[str]) -> None:
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "DELETE FROM role_permission_overrides "
            "WHERE tenant_id = CAST(:t AS uuid) AND role_name = :r"
        ), {"t": org["org"], "r": role})
        for p in permissions:
            await s.execute(text(
                "INSERT INTO role_permission_overrides "
                "  (tenant_id, role_name, permission_name) "
                "VALUES (CAST(:t AS uuid), :r, :p)"
            ), {"t": org["org"], "r": role, "p": p})


async def _bind(org: dict, user_id: str, role: str, scope_kind: str, scope_id: str) -> None:
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO users (id, email, password_hash, tenant_id, active) "
            "VALUES (:i, :e, 'x', :t, true) ON CONFLICT (id) DO NOTHING"
        ), {"i": user_id, "e": f"{user_id}@abcbank.com", "t": org["org"]})
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO role_bindings (id, user_id, scope_kind, scope_id, role_name, tenant_id) "
            "VALUES (CAST(:i AS uuid), :u, :sk, CAST(:si AS uuid), :r, CAST(:t AS uuid))"
        ), {
            "i": str(_uuid.uuid4()), "u": user_id, "sk": scope_kind,
            "si": scope_id, "r": role, "t": org["org"],
        })


# ── the merge ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_role_with_no_override_holds_what_it_ships_with(org):
    async with get_db_session_for_tenant(org["org"]) as s:
        defaults = await shipped_defaults(s)
        effective = await effective_by_role(s, org["org"])
    assert effective["developer"] == defaults["developer"]
    assert "approve" not in effective["developer"]


@pytest.mark.asyncio
async def test_an_override_replaces_the_whole_set_not_a_delta(org):
    """Taking ownership of a role is explicit — you hold exactly what you chose."""
    await _set_override(org, "developer", ["artifact:view", "approve"])
    async with get_db_session_for_tenant(org["org"]) as s:
        effective = await effective_by_role(s, org["org"])
    assert effective["developer"] == {"artifact:view", "approve"}
    # run:create shipped with the role and is NOT retained — the override is the
    # complete answer, not an addition to the default.
    assert "run:create" not in effective["developer"]


@pytest.mark.asyncio
async def test_an_override_is_scoped_to_the_organization_that_made_it(org):
    """role_permissions is global; overrides are not. One org retuning Developer
    must not change another's, which is the second reason this is its own table."""
    await _set_override(org, "developer", ["artifact:view"])

    other = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Other')"
        ), {"i": other, "s": f"other-{other[:8]}"})

    async with get_db_session_for_tenant(other) as s:
        effective = await effective_by_role(s, other)
        defaults = await shipped_defaults(s)
    assert effective["developer"] == defaults["developer"]


# ── the two failure modes this design exists to prevent ──────────────────────

@pytest.mark.asyncio
async def test_both_permission_paths_agree(org):
    """The login resolver and can_perform must read the same effective set.

    If only one applied overrides, a retuned role would work on one surface and be
    refused on another with the same token.
    """
    from shared.authz.can_perform import can_perform

    dev = f"dev-{_uuid.uuid4()}"
    await _bind(org, dev, "developer", "project", org["project"])
    await _set_override(org, "developer", ["artifact:view", "approve"])

    # Path 1: what login bakes into the session and the JWT.
    from_login = set(await resolve_permissions_for_user(dev, org["org"]))
    assert "approve" in from_login
    assert "run:create" not in from_login

    # Path 2: the scoped check.
    async with get_db_session_for_tenant(org["org"]) as s:
        allowed = await can_perform(
            s, user_id=dev, permission="approve", tenant_id=org["org"],
            resource_kind="project", resource_id=org["project"],
        )
        revoked = await can_perform(
            s, user_id=dev, permission="run:create", tenant_id=org["org"],
            resource_kind="project", resource_id=org["project"],
        )
    assert bool(allowed) is True, "override granted it at login but not on the scoped check"
    assert bool(revoked) is False, "the scoped check still honoured a revoked default"


@pytest.mark.asyncio
async def test_the_boot_reconcile_does_not_wipe_an_override(org):
    """seed_rbac_catalog deletes role_permissions edges the code does not declare.

    It must never reach the override table — if it did, every customisation would
    silently revert on the next restart, which is the failure this whole design
    exists to prevent.
    """
    await _set_override(org, "developer", ["artifact:view", "approve"])

    async with get_db_session_superuser() as s:
        await seed_rbac_catalog(s)

    async with get_db_session_for_tenant(org["org"]) as s:
        assert (await overrides(s, org["org"]))["developer"] == {"artifact:view", "approve"}


# ── over HTTP ────────────────────────────────────────────────────────────────

def _client_headers(user_id: str, org_id: str, permissions: list[str]) -> dict:
    return {"Authorization": "Bearer " + create_access_token(
        user_id=user_id, tenant_id=org_id, permissions=permissions
    )}


@pytest.mark.asyncio
async def test_reading_is_open_and_writing_is_the_org_admins(org):
    c = TestClient(process_api.app)
    dev = f"dev-{_uuid.uuid4()}"
    await _bind(org, dev, "developer", "project", org["project"])

    # Anyone signed in may READ — "what can my own role do" is a fair question.
    r = c.get("/admin/role-permissions", headers=_client_headers(dev, org["org"], ["artifact:view"]))
    assert r.status_code == 200, r.text
    rows = {row["role"]: row for row in r.json()}
    assert rows["developer"]["overridden"] is False
    assert rows["developer"]["editable"] is True

    # Writing is not theirs. role:manage would not be enough either — that is a
    # Business Unit Admin, who has no standing to redefine a role org-wide.
    denied = c.put(
        "/admin/role-permissions",
        headers=_client_headers(dev, org["org"], ["artifact:view", "role:manage"]),
        json={"role": "developer", "permissions": ["artifact:view"]},
    )
    assert denied.status_code == 403, denied.text


@pytest.mark.asyncio
async def test_an_org_admin_updates_and_resets_a_role(org):
    c = TestClient(process_api.app)
    admin = f"admin-{_uuid.uuid4()}"
    headers = _client_headers(admin, org["org"], ["admin:*"])

    updated = c.put("/admin/role-permissions", headers=headers,
                    json={"role": "developer", "permissions": ["artifact:view", "approve"]})
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["overridden"] is True
    assert body["effective"] == ["approve", "artifact:view"]
    # The shipped default is still reported, so the panel can show what changed and
    # offer a way back.
    assert "run:create" in body["defaults"]

    back = c.put("/admin/role-permissions", headers=headers,
                 json={"role": "developer", "reset": True})
    assert back.status_code == 200, back.text
    assert back.json()["overridden"] is False
    assert back.json()["effective"] == back.json()["defaults"]


@pytest.mark.asyncio
async def test_the_org_admin_role_itself_cannot_be_edited(org):
    """Removing admin:* would lock every administrator out through the very UI
    that removed it."""
    c = TestClient(process_api.app)
    admin = f"admin-{_uuid.uuid4()}"
    headers = _client_headers(admin, org["org"], ["admin:*"])

    listed = {row["role"]: row for row in c.get("/admin/role-permissions", headers=headers).json()}
    assert listed["org_admin"]["editable"] is False
    assert listed["org_admin"]["lockedReason"]

    r = c.put("/admin/role-permissions", headers=headers,
              json={"role": "org_admin", "permissions": ["artifact:view"]})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "role_not_editable"


@pytest.mark.asyncio
async def test_a_role_cannot_be_stripped_to_nothing(org):
    c = TestClient(process_api.app)
    headers = _client_headers(f"admin-{_uuid.uuid4()}", org["org"], ["admin:*"])
    r = c.put("/admin/role-permissions", headers=headers,
              json={"role": "developer", "permissions": []})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_permission_set"


@pytest.mark.asyncio
async def test_an_unknown_permission_is_named_rather_than_hitting_the_foreign_key(org):
    c = TestClient(process_api.app)
    headers = _client_headers(f"admin-{_uuid.uuid4()}", org["org"], ["admin:*"])
    r = c.put("/admin/role-permissions", headers=headers,
              json={"role": "developer", "permissions": ["artifact:view", "nope:invent"]})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "unknown_permission"
    assert "nope:invent" in r.json()["detail"]["message"]


@pytest.mark.asyncio
async def test_the_permission_catalogue_comes_from_the_database(org):
    """Adding a permission is a data change, not a release — that is the point.

    A row inserted here is immediately selectable on the Roles page and grantable to
    a custom role, with no frontend deploy.
    """
    c = TestClient(process_api.app)
    headers = _client_headers(f"admin-{_uuid.uuid4()}", org["org"], ["admin:*"])

    before = {p["id"] for p in c.get("/admin/permissions", headers=headers).json()}
    assert "artifact:view" in before
    # Wildcards are excluded: they satisfy every check, so offering one in a picker
    # is offering "make this role an administrator" disguised as a checkbox.
    assert "admin:*" not in before

    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO permissions (name) VALUES ('sandbox:invent') ON CONFLICT DO NOTHING"
        ))
    try:
        after = {p["id"] for p in c.get("/admin/permissions", headers=headers).json()}
        assert "sandbox:invent" in after

        # And it is immediately grantable, with no code change anywhere.
        r = c.put("/admin/role-permissions", headers=headers,
                  json={"role": "developer", "permissions": ["artifact:view", "sandbox:invent"]})
        assert r.status_code == 200, r.text
        assert "sandbox:invent" in r.json()["effective"]
    finally:
        async with get_db_session_for_tenant(org["org"]) as s:
            await s.execute(text(
                "DELETE FROM role_permission_overrides WHERE permission_name = 'sandbox:invent'"
            ))
        async with get_db_session_superuser() as s:
            await s.execute(text("DELETE FROM permissions WHERE name = 'sandbox:invent'"))
