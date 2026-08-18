"""Revoking a role stops the holder's existing token, through the real middleware.

`test_token_epoch.py` covers the mechanism in isolation. This covers the wiring: that
`revoke_role` actually bumps, that the middleware actually checks, and that a token which
worked a moment ago stops working — which is the behaviour the finding is about, and the
part that a unit test of either half would miss.

See finding 6 in docs/rbac-audit-2026-08-17.md.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.authz import token_epoch
from shared.authz.grant import grant_role, revoke_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


class _FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = str(value)

    def pipeline(self):
        outer = self

        class _Pipe:
            def __init__(self):
                self.ops = []

            def set(self, key, value, ex=None):
                self.ops.append((key, value))

            async def execute(self):
                for k, v in self.ops:
                    outer.store[k] = str(v)

        return _Pipe()


@pytest.fixture(autouse=True)
def _epoch_redis(monkeypatch):
    """Point the epoch module at an in-process fake so the test needs no Redis.

    ONE instance, returned every call. `lambda: _FakeRedis()` builds a fresh store per
    call, so the bump and the staleness check would look at different dictionaries and
    every one of these tests would pass for the wrong reason.
    """
    client = _FakeRedis()
    monkeypatch.setattr(token_epoch, "_redis", lambda: client)


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org_unit():
    org, unit = str(_uuid.uuid4()), str(_uuid.uuid4())
    unit_b = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Revoke Test')"
        ), {"i": org, "s": f"rev-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit-a', 'Unit A')"
        ), {"i": unit, "o": org})
        # A second unit, because a unit has exactly ONE Business Unit Admin: the
        # bystander in the multi-user test below needs somewhere of their own to
        # administer rather than sharing this one.
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit-b', 'Unit B')"
        ), {"i": unit_b, "o": org})
    yield {"org": org, "unit": unit, "unit_b": unit_b}


@pytest.mark.asyncio
async def test_a_revoked_role_stops_the_token_the_holder_is_already_carrying(org_unit):
    """The exact scenario from the plan: 'deleting an org_admin binding left the badge
    and access intact until sign-out'."""
    t = org_unit
    user = f"u-{_uuid.uuid4()}"
    await grant_role(user, t["unit"], "bu_admin", tenant_id=t["org"],
                     scope_kind="business_unit")

    # A token minted from that grant, exactly as login would.
    token = create_access_token(
        user_id=user, tenant_id=t["org"], permissions=["artifact:view", "member:manage"]
    )
    hdr = {"Authorization": f"Bearer {token}"}
    c = TestClient(process_api.app)

    assert c.get("/admin/workspaces", headers=hdr).status_code == 200

    await revoke_role(user, t["unit"], "bu_admin", tenant_id=t["org"],
                      scope_kind="business_unit")

    r = c.get("/admin/workspaces", headers=hdr)
    assert r.status_code == 401, r.text
    # 401 and not 403: the token is out of date, the request is not forbidden.
    assert "stale" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_a_freshly_granted_role_is_usable_immediately(org_unit):
    """A grant must not lock out the person it just granted to.

    Granting deliberately does NOT bump: it only widens what the user may do, so an older
    token is stale in the harmless direction. Bumping there would also refuse a token
    minted in the same second as the grant — `iat` is second-granular and `_now_epoch`
    rounds up — which is the grant-then-sign-in sequence, and would read as "the grant
    did not work".
    """
    t = org_unit
    user = f"u-{_uuid.uuid4()}"
    await grant_role(user, t["unit"], "bu_admin", tenant_id=t["org"],
                     scope_kind="business_unit")

    token = create_access_token(
        user_id=user, tenant_id=t["org"], permissions=["artifact:view", "member:manage"]
    )
    r = TestClient(process_api.app).get(
        "/admin/workspaces", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_one_persons_revocation_does_not_sign_out_another(org_unit):
    """Per-user, not tenant-wide: a bump that signed out the whole organisation would be
    correct and unusable."""
    t = org_unit
    revoked = f"u-{_uuid.uuid4()}"
    bystander = f"u-{_uuid.uuid4()}"
    # One admin each, in their own unit — the rule is one admin PER UNIT.
    await grant_role(revoked, t["unit"], "bu_admin", tenant_id=t["org"],
                     scope_kind="business_unit")
    await grant_role(bystander, t["unit_b"], "bu_admin", tenant_id=t["org"],
                     scope_kind="business_unit")

    tokens = {
        u: create_access_token(user_id=u, tenant_id=t["org"],
                               permissions=["artifact:view", "member:manage"])
        for u in (revoked, bystander)
    }
    await revoke_role(revoked, t["unit"], "bu_admin", tenant_id=t["org"],
                      scope_kind="business_unit")

    c = TestClient(process_api.app)
    assert c.get("/admin/workspaces",
                 headers={"Authorization": f"Bearer {tokens[revoked]}"}).status_code == 401
    assert c.get("/admin/workspaces",
                 headers={"Authorization": f"Bearer {tokens[bystander]}"}).status_code == 200
