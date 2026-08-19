"""Which role somebody held, when it changed, and who changed it.

The trail existed and nothing read it — but worse, the one screen where roles are
actually changed did not write to it. `PATCH /workspaces/{id}/members/{user}` assigned
`role_name` on the ORM object and committed, which skipped the audit, the rank check,
the tier-conflict check and the one-admin-per-unit rule all at once.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org():
    org_id, unit = str(_uuid.uuid4()), str(_uuid.uuid4())
    admin = f"orgadmin-{_uuid.uuid4()}"
    member = f"member-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Hist Test')"
        ), {"i": org_id, "s": "hst-" + org_id[:8]})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'payments', 'Payments')"
        ), {"i": unit, "o": org_id})
        for uid in (admin, member):
            await s.execute(text(
                "INSERT INTO users (id, email, tenant_id) VALUES (:i, :e, CAST(:t AS uuid))"
            ), {"i": uid, "e": f"{uid}@hist.test", "t": org_id})

    from shared.authz.grant import grant_role
    await grant_role(admin, org_id, "org_admin", tenant_id=org_id, scope_kind="organization")
    await grant_role(member, unit, "contributor", tenant_id=org_id, scope_kind="business_unit")
    yield {"org": org_id, "unit": unit, "admin": admin, "member": member}


def _client():
    return TestClient(process_api.app)


def _hdr(uid, org, perms):
    return {"Authorization": "Bearer " + create_access_token(
        user_id=uid, tenant_id=org, permissions=perms)}


def _admin(t):
    return _hdr(t["admin"], t["org"], ["admin:*"])


def test_changing_a_role_is_recorded_as_a_change(org):
    """The reported gap: a role was changed and the history showed only the original
    onboarding grant, because the PATCH mutated the row in place."""
    c = _client()
    r = c.patch(f"/workspaces/{org['unit']}/members/{org['member']}",
                headers=_admin(org), json={"roleName": "developer"})
    assert r.status_code == 200, r.text

    history = c.get(f"/users/{org['member']}/role-history", headers=_admin(org)).json()
    change = next(e for e in history if e["kind"] == "changed")
    assert change["from"] == "contributor"
    assert change["to"] == "developer"
    assert change["at"]
    assert change["scopeName"] == "Payments"


def test_the_history_names_who_made_the_change(org):
    """"Who made me a Project Admin" is the question actually asked afterwards."""
    c = _client()
    c.patch(f"/workspaces/{org['unit']}/members/{org['member']}",
            headers=_admin(org), json={"roleName": "qa"})

    history = c.get(f"/users/{org['member']}/role-history", headers=_admin(org)).json()
    change = next(e for e in history if e["kind"] == "changed")
    assert change["actorId"] == org["admin"]
    assert change["actorEmail"] == f"{org['admin']}@hist.test"


def test_a_first_grant_has_no_from(org):
    """It is not a change — there was nothing before it. Storing a from/to pair
    instead of deriving one could not describe this at all."""
    history = _client().get(
        f"/users/{org['member']}/role-history", headers=_admin(org)
    ).json()
    first = [e for e in history if e["kind"] == "granted"]
    assert first, history
    assert first[-1]["from"] is None
    assert first[-1]["to"] == "contributor"


def test_changes_read_newest_first(org):
    c = _client()
    for role in ("developer", "qa", "architect"):
        c.patch(f"/workspaces/{org['unit']}/members/{org['member']}",
                headers=_admin(org), json={"roleName": role})

    history = c.get(f"/users/{org['member']}/role-history", headers=_admin(org)).json()
    assert [e["at"] for e in history] == sorted((e["at"] for e in history), reverse=True)
    assert history[0]["to"] == "architect"


# ── the escalation the in-place update allowed ───────────────────────────────

@pytest.mark.asyncio
async def test_a_unit_admin_cannot_patch_somebody_to_org_admin(org):
    """THE HOLE THIS PATH HAD. The route gate is `workspace:manage`, which a Business
    Unit Admin holds, and nothing checked WHAT role was being granted — so this
    endpoint was one PATCH away from minting an Organization Admin, who then holds
    admin:* organization-wide at their next login."""
    bu = f"buadmin-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO users (id, email, tenant_id) VALUES (:i, :e, CAST(:t AS uuid))"
        ), {"i": bu, "e": f"{bu}@hist.test", "t": org["org"]})

    from shared.authz.grant import grant_role
    await grant_role(bu, org["unit"], "bu_admin",
                     tenant_id=org["org"], scope_kind="business_unit")

    # Exactly what a Business Unit Admin's token carries.
    perms = ["workspace:manage", "member:manage", "role:manage", "artifact:view"]
    r = _client().patch(
        f"/workspaces/{org['unit']}/members/{org['member']}",
        headers=_hdr(bu, org["org"], perms),
        json={"roleName": "org_admin"},
    )
    assert r.status_code in (403, 404), (
        f"a unit admin minted an org_admin: {r.status_code} {r.text[:200]}"
    )

    # And the member is untouched.
    async with get_db_session_for_tenant(org["org"]) as s:
        role = (await s.execute(text(
            "SELECT role_name FROM role_bindings WHERE user_id = :u "
            "  AND scope_kind = 'business_unit'"
        ), {"u": org["member"]})).scalar()
    assert role == "contributor"


def test_history_is_refused_for_somebody_outside_your_units(org):
    """A person's role history names every unit they have been placed in, which is
    more than a project-level admin is owed about somebody passing through."""
    outsider = f"pa-{_uuid.uuid4()}"
    r = _client().get(
        f"/users/{org['member']}/role-history",
        headers=_hdr(outsider, org["org"], ["member:manage"]),
    )
    assert r.status_code == 404, r.text
