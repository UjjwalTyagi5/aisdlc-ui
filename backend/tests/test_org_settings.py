"""Per-organization SSO / MFA / session settings.

The property under test throughout: the Entra client secret goes in and never comes
back out. An endpoint that can return it is an endpoint that can leak it, so there is
no read path for the value at all — only a boolean saying whether one is configured.
"""
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.services import org_settings as svc

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org():
    org_id, bu = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Settings Test')"
        ), {"i": org_id, "s": f"set-{org_id[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": bu, "o": org_id})
    yield {"org": org_id, "bu": bu}


def _hdr(user: str, org_id: str, perms: list[str]) -> dict:
    return {"Authorization": "Bearer " + create_access_token(
        user_id=user, tenant_id=org_id, permissions=perms
    )}


# ── defaults ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unconfigured_org_gets_defaults_without_writing_a_row(org):
    async with get_db_session_for_tenant(org["org"]) as s:
        settings = await svc.get_settings(s, org["org"])
    assert settings["mfaRequired"] is False
    assert settings["sessionTimeoutMinutes"] == 480
    assert settings["ssoConfigured"] is False
    assert settings["hasClientSecret"] is False

    # A read must not create the row: "has this org ever been configured?" has to stay
    # answerable, and a GET that writes makes every page load an insert.
    async with get_db_session_for_tenant(org["org"]) as s:
        count = (await s.execute(text("SELECT count(*) FROM org_settings"))).scalar()
    assert count == 0


# ── the secret ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_client_secret_is_never_returned_or_stored_in_the_row(org):
    secret = "super-secret-entra-value"
    async with get_db_session_for_tenant(org["org"]) as s:
        out = await svc.update_sso(
            s, tenant_id=org["org"], updated_by="admin",
            entra_tenant_id="contoso.onmicrosoft.com",
            entra_client_id="client-123",
            entra_client_secret=secret,
        )

    # The response says a secret exists and nothing more.
    assert out["hasClientSecret"] is True
    assert out["ssoConfigured"] is True
    assert secret not in str(out)

    # The row holds a REFERENCE, not the value.
    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(text(
            "SELECT entra_client_secret_ref FROM org_settings WHERE tenant_id = CAST(:t AS uuid)"
        ), {"t": org["org"]})).first()
    assert row.entra_client_secret_ref == svc.ENTRA_SECRET_REF
    assert secret != row.entra_client_secret_ref

    # ...and the value is retrievable only through the secret store.
    from shared.services.secret_store import get_secret
    assert await get_secret(org["org"], svc.ENTRA_SECRET_REF) == secret


@pytest.mark.asyncio
async def test_http_response_never_carries_the_secret(org):
    admin = f"admin-{_uuid.uuid4()}"
    c = TestClient(process_api.app)
    secret = "http-secret-should-not-echo"

    r = c.patch("/org/settings/sso", headers=_hdr(admin, org["org"], ["admin:*"]), json={
        "entraTenantId": "contoso.onmicrosoft.com",
        "entraClientId": "client-abc",
        "entraClientSecret": secret,
    })
    assert r.status_code == 200, r.text
    assert secret not in r.text
    assert r.json()["hasClientSecret"] is True

    g = c.get("/org/settings", headers=_hdr(admin, org["org"], ["admin:*"]))
    assert g.status_code == 200
    assert secret not in g.text
    # No reference either — a settings page needs "is it set", never "what is it".
    assert "entraClientSecretRef" not in g.text


# ── partial update ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_omitted_fields_are_left_alone(org):
    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.update_sso(
            s, tenant_id=org["org"], updated_by="admin",
            entra_tenant_id="contoso", entra_client_id="cid",
            entra_client_secret="s3cret", mfa_required=True,
            session_timeout_minutes=60,
        )
    # A second patch touching only the timeout must not blank the SSO config or MFA.
    async with get_db_session_for_tenant(org["org"]) as s:
        out = await svc.update_sso(
            s, tenant_id=org["org"], updated_by="admin2", session_timeout_minutes=120
        )
    assert out["sessionTimeoutMinutes"] == 120
    assert out["entraTenantId"] == "contoso"
    assert out["entraClientId"] == "cid"
    assert out["hasClientSecret"] is True
    assert out["mfaRequired"] is True
    assert out["updatedBy"] == "admin2"


@pytest.mark.asyncio
async def test_sso_configured_requires_all_three_parts(org):
    """A tenant id alone must not read as configured, or login fails after 'success'."""
    async with get_db_session_for_tenant(org["org"]) as s:
        out = await svc.update_sso(
            s, tenant_id=org["org"], updated_by="admin", entra_tenant_id="contoso"
        )
    assert out["ssoConfigured"] is False
    assert out["entraTenantId"] == "contoso"


# ── bounds ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_timeout_is_bounded(org):
    async with get_db_session_for_tenant(org["org"]) as s:
        # Zero would expire immediately and lock the org out of its own platform.
        with pytest.raises(svc.OrgSettingsError):
            await svc.update_sso(
                s, tenant_id=org["org"], updated_by="a", session_timeout_minutes=0
            )
        with pytest.raises(svc.OrgSettingsError):
            await svc.update_sso(
                s, tenant_id=org["org"], updated_by="a", session_timeout_minutes=99999
            )


# ── authority ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_only_an_org_admin_may_change_sso(org):
    """Redirecting the org's login to another Entra tenant is the worst single write."""
    bu_admin = f"bu-{_uuid.uuid4()}"
    c = TestClient(process_api.app)

    r = c.patch(
        "/org/settings/sso",
        headers=_hdr(bu_admin, org["org"], ["role:manage", "workspace:manage", "artifact:view"]),
        json={"entraTenantId": "attacker.onmicrosoft.com"},
    )
    assert r.status_code == 403, r.text
    assert "Organization Admin" in r.json()["detail"]


@pytest.mark.asyncio
async def test_any_member_may_read_settings(org):
    """MFA policy and session lifetime govern the reader's own login."""
    member = f"member-{_uuid.uuid4()}"
    c = TestClient(process_api.app)
    r = c.get("/org/settings", headers=_hdr(member, org["org"], ["artifact:view"]))
    assert r.status_code == 200, r.text
    assert r.json()["mfaRequired"] is False


@pytest.mark.asyncio
async def test_sso_changes_are_audited_without_values(org):
    admin = f"admin-{_uuid.uuid4()}"
    c = TestClient(process_api.app)
    secret = "audit-should-not-contain-this"
    c.patch("/org/settings/sso", headers=_hdr(admin, org["org"], ["admin:*"]), json={
        "entraTenantId": "contoso", "entraClientSecret": secret, "mfaRequired": True,
    })

    async with get_db_session_for_tenant(org["org"]) as s:
        rows = (await s.execute(text(
            "SELECT actor_id, payload FROM audit_events "
            "WHERE event_type = 'org.settings.sso.updated'"
        ))).fetchall()
    assert len(rows) == 1, rows
    assert rows[0].actor_id == admin
    payload = rows[0].payload
    # Records WHICH knobs moved, never their values.
    assert payload["client_secret_rotated"] is True
    assert secret not in str(payload)
