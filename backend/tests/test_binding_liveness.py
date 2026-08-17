"""An expired or deactivated binding grants nothing, on every path.

`can_perform` always filtered `status = 'active'` AND the expiry. Nothing else did.
`resolve_permissions_for_user` — which produces the JWT claim every `require_permission`
reads, i.e. the gate on almost every route — filtered neither, and could not: the ORM
model never got migration 0003's `expires_at` column, so the query it builds had no
column to filter on. `read_scope` filtered `status <> 'deactivated'` and no expiry.

The result was worse than a uniform miss: the two permission readers disagreed about the
same binding. A lapsed elevation was refused by a scoped check and honoured by every
resource-less one, which presents as "denied on the project page, works everywhere else".

See finding 5 in docs/rbac-audit-2026-08-17.md.
"""
import uuid as _uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from shared.authz.can_perform import can_perform
from shared.authz.effective_role import roles_held
from shared.authz.grant import grant_role
from shared.authz.read_scope import allowed_workspace_ids, administered_workspace_ids
from shared.authz.resolver import resolve_permissions_for_user
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
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Live Test')"
        ), {"i": org, "s": f"live-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit-a', 'Unit A')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'P')"
        ), {"i": proj, "w": unit, "t": org})
    yield {"org": org, "unit": unit, "proj": proj}


class _Req:
    """The two read_scope helpers read permissions and identity off request.state."""

    def __init__(self, user_id: str, perms: list[str]):
        self.state = type("S", (), {"user_id": user_id, "permissions": perms})()


# ── expiry ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_expired_binding_grants_no_permissions(org_tree):
    """The claim the JWT is minted from. This was the whole hole: `expires_at` is set,
    the clock has passed it, and the login resolver handed out the role's permissions
    anyway because its query had no expiry predicate."""
    t = org_tree
    user = f"u-{_uuid.uuid4()}"
    past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    await grant_role(user, t["unit"], "bu_admin", tenant_id=t["org"],
                     scope_kind="business_unit", expires_at=past)

    assert await resolve_permissions_for_user(user, t["org"]) == []


@pytest.mark.asyncio
async def test_an_unexpired_elevation_still_grants(org_tree):
    """The check must bite on lapsed bindings only — a live elevation is the feature."""
    t = org_tree
    user = f"u-{_uuid.uuid4()}"
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    await grant_role(user, t["unit"], "bu_admin", tenant_id=t["org"],
                     scope_kind="business_unit", expires_at=future)

    perms = await resolve_permissions_for_user(user, t["org"])
    assert "role:manage" in perms


@pytest.mark.asyncio
async def test_a_permanent_binding_is_unaffected(org_tree):
    """`expires_at IS NULL` means permanent, and must not be swept up by the filter."""
    t = org_tree
    user = f"u-{_uuid.uuid4()}"
    await grant_role(user, t["unit"], "bu_admin", tenant_id=t["org"],
                     scope_kind="business_unit")

    assert "role:manage" in await resolve_permissions_for_user(user, t["org"])


@pytest.mark.asyncio
async def test_an_expired_binding_grants_no_scope(org_tree):
    """Scope is the other half. Before this, a lapsed elevation stopped granting
    permissions and went on granting SCOPE — the person could still see the unit's
    figures, just not act in it, which is a disclosure rather than a refusal."""
    t = org_tree
    user = f"u-{_uuid.uuid4()}"
    past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    await grant_role(user, t["unit"], "bu_admin", tenant_id=t["org"],
                     scope_kind="business_unit", expires_at=past)
    req = _Req(user, ["artifact:view"])

    async with get_db_session_for_tenant(t["org"]) as s:
        assert await allowed_workspace_ids(s, req) == []
        assert await administered_workspace_ids(s, req) == []


@pytest.mark.asyncio
async def test_an_expired_binding_confers_no_standing(org_tree):
    """Standing decides who a governance request routes to, so a lapsed elevation must
    stop making someone a Business Unit Admin for routing as well as for permissions."""
    t = org_tree
    user = f"u-{_uuid.uuid4()}"
    past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    await grant_role(user, t["unit"], "bu_admin", tenant_id=t["org"],
                     scope_kind="business_unit", expires_at=past)

    async with get_db_session_for_tenant(t["org"]) as s:
        assert await roles_held(s, user) == []


# ── deactivation ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_deactivated_binding_grants_no_permissions(org_tree):
    t = org_tree
    user = f"u-{_uuid.uuid4()}"
    await grant_role(user, t["unit"], "bu_admin", tenant_id=t["org"],
                     scope_kind="business_unit")
    async with get_db_session_for_tenant(t["org"]) as s:
        await s.execute(text(
            "UPDATE role_bindings SET status = 'deactivated' WHERE user_id = :u"
        ), {"u": user})

    assert await resolve_permissions_for_user(user, t["org"]) == []


# ── the property that made this a bug rather than a gap ──────────────────────


@pytest.mark.asyncio
async def test_both_permission_readers_agree_about_an_expired_binding(org_tree):
    """The two readers must give the same answer about the same binding.

    `resolve_permissions_for_user` runs at login and fills the token; `can_perform` runs
    per scoped check. When only the second honoured expiry, a lapsed elevation was
    refused on a project page and accepted on every other route with the same token —
    which reads as a flaky page, not as an authorization bug, and costs a day to find.
    """
    t = org_tree
    user = f"u-{_uuid.uuid4()}"
    past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    await grant_role(user, t["proj"], "project_admin", tenant_id=t["org"],
                     scope_kind="project", expires_at=past)

    from_login = await resolve_permissions_for_user(user, t["org"])
    async with get_db_session_for_tenant(t["org"]) as s:
        scoped = await can_perform(
            s, user_id=user, permission="member:manage", tenant_id=t["org"],
            resource_kind="project", resource_id=t["proj"],
        )

    assert from_login == []
    assert scoped is False


@pytest.mark.asyncio
async def test_a_lapsed_governance_elevation_stops_blocking_a_delivery_grant(org_tree):
    """The tier-conflict guard reads bindings too.

    Nobody may hold both tiers within one scope. If the guard counted lapsed bindings, a
    temporary governance elevation would leave a permanent footprint: the person could
    never afterwards be given a delivery role in that scope.
    """
    t = org_tree
    user = f"u-{_uuid.uuid4()}"
    past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    await grant_role(user, t["proj"], "bu_admin", tenant_id=t["org"],
                     scope_kind="project", expires_at=past)

    # Would raise TierConflictError if the expired governance binding still counted.
    await grant_role(user, t["proj"], "developer", tenant_id=t["org"],
                     scope_kind="project")
    assert "run:create" in await resolve_permissions_for_user(user, t["org"])
