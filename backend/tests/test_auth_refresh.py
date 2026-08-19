"""Picking up a role granted after you signed in.

Permissions and `platform_role` are baked into the token at login, so a grant made
afterwards does not reach a session already holding one. That is not merely a delay:
project lists resolve live from bindings, so the dashboard shows the project somebody
was just given while the navigation still reads the stale claim and offers nothing to
do with it.

The assertions that matter are that the new token is built from the DATABASE and not
from the token presented, and that this is a widening path only.
"""
import base64
import json
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.db import get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


def _claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "==" * 3).decode())


@pytest.fixture
async def person():
    org, unit = str(_uuid.uuid4()), str(_uuid.uuid4())
    uid = f"user-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Refresh Test')"
        ), {"i": org, "s": "rfr-" + org[:8]})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'payments', 'Payments')"
        ), {"i": unit, "o": org})
        await s.execute(text(
            "INSERT INTO users (id, email, tenant_id) VALUES (:i, :e, CAST(:t AS uuid))"
        ), {"i": uid, "e": f"{uid}@refresh.test", "t": org})
    yield {"org": org, "unit": unit, "user": uid}


def _client():
    return TestClient(process_api.app)


def _stale_header(user_id: str, org: str) -> dict:
    """A token minted BEFORE any grant — what a signed-in session actually holds."""
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=["artifact:view"])
    }


@pytest.mark.asyncio
async def test_refresh_reports_a_role_granted_after_sign_in(person):
    """The reported bug: an admin assigns a role and the person still sees the old
    one, because their token was minted before the grant."""
    c = _client()
    hdr = _stale_header(person["user"], person["org"])

    # Before the grant, they hold nothing.
    before = c.post("/auth/refresh", headers=hdr)
    assert before.status_code == 200, before.text
    assert before.json()["platform_role"] is None

    from shared.authz.grant import grant_role
    await grant_role(person["user"], person["unit"], "project_admin",
                     tenant_id=person["org"], scope_kind="business_unit")

    # The SAME stale token now buys a fresh one that knows about the grant.
    after = c.post("/auth/refresh", headers=hdr)
    assert after.status_code == 200, after.text
    body = after.json()
    assert body["platform_role"] == "project_admin"
    assert "run:create" in body["permissions"]


@pytest.mark.asyncio
async def test_the_new_token_carries_the_new_claims(person):
    """It is the TOKEN that gates everything downstream, so reporting the role in the
    body without minting it into the claim would fix the label and nothing else."""
    from shared.authz.grant import grant_role
    await grant_role(person["user"], person["unit"], "bu_admin",
                     tenant_id=person["org"], scope_kind="business_unit")

    r = _client().post("/auth/refresh", headers=_stale_header(person["user"], person["org"]))
    claims = _claims(r.json()["token"])
    assert claims["platform_role"] == "bu_admin"
    assert "role:manage" in claims["permissions"]
    assert claims["sub"] == person["user"]


@pytest.mark.asyncio
async def test_it_resolves_from_the_database_not_from_the_presented_token(person):
    """A token claiming admin:* must not be able to renew itself into one. The whole
    point is that the presented claim is not trusted."""
    forged = {
        "Authorization": "Bearer "
        + create_access_token(
            user_id=person["user"], tenant_id=person["org"],
            permissions=["admin:*"], platform_role="org_admin",
        )
    }
    r = _client().post("/auth/refresh", headers=forged)
    assert r.status_code == 200, r.text
    body = r.json()
    # They hold no bindings, so the refresh hands back nothing regardless of what the
    # presented token said.
    assert body["platform_role"] is None
    assert body["permissions"] == []
    assert "admin:*" not in _claims(body["token"])["permissions"]


def test_an_unauthenticated_caller_gets_nothing():
    """`public()` means no permission gate, not no authentication. Without an
    established identity there is nobody to mint for."""
    r = _client().post("/auth/refresh")
    assert r.status_code in (401, 403), r.text
