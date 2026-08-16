"""Local email+password auth, single-org bootstrap, and unprivileged self-signup.

The platform_users table is still asserted below: the migration still creates it, and
the retired platform tier left rows behind. Nothing reads it any more.
"""
from shared.models.orm import PlatformUser, User


def test_user_has_password_hash_column():
    cols = {c.name for c in User.__table__.columns}
    assert "password_hash" in cols


def test_platform_user_model():
    cols = {c.name for c in PlatformUser.__table__.columns}
    assert {"user_id", "email", "platform_role", "active"} <= cols


def test_baseline_provides_local_auth_shape():
    """Local email+password auth needs platform_users and users.password_hash.

    Was a shape check on migration 0013, which the squash folded into the baseline.
    The assertion that matters is unchanged: the columns must exist.
    """
    from pathlib import Path
    mig = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0001_baseline.py"
    text = mig.read_text(encoding="utf-8")
    assert "platform_users" in text
    assert "password_hash" in text
    # platform_users is GLOBAL by design — a platform admin belongs to no tenant, so
    # putting it behind a tenant policy would make it unreadable at login.
    assert "ALTER TABLE platform_users ENABLE ROW LEVEL SECURITY" not in text


def test_password_hash_roundtrip():
    from shared.auth.passwords import hash_password, verify_password
    h = hash_password("s3cret-pw")
    assert h != "s3cret-pw"
    assert verify_password("s3cret-pw", h) is True
    assert verify_password("wrong", h) is False
    assert verify_password("s3cret-pw", None) is False


import pytest


@pytest.fixture
async def cleanup_seeded_orgs():
    """Remove organizations these tests seed, so the dev database is not littered.

    The suite runs against a real database and `seed_org_admins` creates a real org.
    Without this every run leaves another one behind — which is how the neighbouring
    RBAC suites accumulated their throwaway tenants.
    """
    slugs: list[str] = []
    yield slugs
    from sqlalchemy import text
    from shared.db import get_db_session_superuser
    async with get_db_session_superuser() as s:
        for slug in slugs:
            row = (await s.execute(
                text("SELECT id FROM organizations WHERE slug = :s"), {"s": slug}
            )).first()
            if row is None:
                continue
            org_id = str(row.id)
            # role_bindings is FORCE RLS; delete it under the tenant GUC first, then
            # the global rows.
            from shared.db import get_db_session_for_tenant
            async with get_db_session_for_tenant(org_id) as ts:
                await ts.execute(text("DELETE FROM role_bindings"))
            await s.execute(
                text("DELETE FROM workspaces WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org_id},
            )
            await s.execute(
                text("DELETE FROM organizations WHERE id = CAST(:o AS uuid)"), {"o": org_id}
            )


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    """Dispose the module-level async engine after each test so a fresh
    pytest-asyncio event loop never reuses a connection bound to a closed loop
    ('Event loop is closed' on the shared engine pool). Mirrors the established
    fixture in test_custom_roles.py / test_rls_isolation.py."""
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_is_org_admin_email(monkeypatch):
    import config.env as env
    monkeypatch.setattr(env, "ORG_ADMIN_EMAILS", ["boss@co.com"])
    from shared.auth import bootstrap
    assert bootstrap.is_org_admin_email("BOSS@co.com") is True
    assert bootstrap.is_org_admin_email("nobody@co.com") is False
    assert bootstrap.is_org_admin_email("") is False


@pytest.mark.asyncio
async def test_seed_org_admins_idempotent(monkeypatch, cleanup_seeded_orgs):
    """Second run must converge, not fail — boot re-runs this on every restart."""
    import config.env as env
    monkeypatch.setattr(env, "ORG_ADMIN_EMAILS", ["seedtest@co.com"])
    monkeypatch.setattr(env, "ORG_ADMIN_PASSWORD", "seed-pw-123")
    monkeypatch.setattr(env, "DEFAULT_ORG_SLUG", "seedtest-org")
    monkeypatch.setattr(env, "DEFAULT_ORG_NAME", "Seed Test Org")
    cleanup_seeded_orgs.append("seedtest-org")
    from shared.auth.bootstrap import ensure_default_organization, seed_org_admins
    await seed_org_admins()
    first = await ensure_default_organization()
    await seed_org_admins()
    assert await ensure_default_organization() == first, "org must not be recreated"


@pytest.mark.asyncio
async def test_seed_org_admins_noop_without_env(monkeypatch):
    """An unconfigured env must not create a nameless organization."""
    import config.env as env
    monkeypatch.setattr(env, "ORG_ADMIN_EMAILS", [])
    monkeypatch.setattr(env, "ORG_ADMIN_PASSWORD", "")
    monkeypatch.setattr(env, "DEFAULT_ORG_SLUG", "never-created-org")
    from shared.auth.bootstrap import get_default_org_id, seed_org_admins
    await seed_org_admins()
    assert await get_default_org_id() is None


@pytest.mark.asyncio
async def test_login_returns_org_tier_and_admin_permissions(monkeypatch, cleanup_seeded_orgs):
    """The seeded admin is an ordinary org user holding org_admin — not a platform tier."""
    import config.env as env
    monkeypatch.setattr(env, "ORG_ADMIN_EMAILS", ["boss@co.com"])
    monkeypatch.setattr(env, "ORG_ADMIN_PASSWORD", "pw-12345")
    monkeypatch.setattr(env, "DEFAULT_ORG_SLUG", "logintest-org")
    monkeypatch.setattr(env, "DEFAULT_ORG_NAME", "Login Test Org")
    cleanup_seeded_orgs.append("logintest-org")
    from shared.auth.bootstrap import seed_org_admins
    from shared.routers.auth_local import login, LoginIn
    await seed_org_admins()
    out = await login(LoginIn(email="boss@co.com", password="pw-12345"))
    assert out.tier == "org"
    assert out.token
    assert out.tenant_id, "org admin must carry a tenant — it is not tenant-less"
    assert "platform:*" not in out.permissions
    assert out.permissions, "org_admin binding must resolve to a non-empty permission set"

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await login(LoginIn(email="boss@co.com", password="nope"))
    assert ei.value.status_code == 401
    # unknown email = same uniform 401 (no enumeration)
    with pytest.raises(HTTPException) as ei2:
        await login(LoginIn(email="ghost@co.com", password="whatever"))
    assert ei2.value.status_code == 401


@pytest.mark.asyncio
async def test_change_password_flow():
    import uuid as _uuid
    from sqlalchemy import text
    from fastapi import Request, HTTPException
    from shared.db import get_db_session_superuser
    from shared.auth.passwords import hash_password, verify_password
    from shared.routers.auth_local import change_password, ChangePasswordIn

    uid = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO users (id, email, password_hash, active) VALUES (:i,:e,:p,true)"
        ), {"i": uid, "e": f"{uid}@example.com", "p": hash_password("old-pw-123")})

    req = Request({"type": "http", "headers": []})
    req.state.user_id = uid

    with pytest.raises(HTTPException) as ei:
        await change_password(req, ChangePasswordIn(current_password="nope", new_password="new-pw-123"))
    assert ei.value.status_code == 400

    await change_password(req, ChangePasswordIn(current_password="old-pw-123", new_password="new-pw-123"))
    async with get_db_session_superuser() as s:
        row = (await s.execute(text("SELECT password_hash FROM users WHERE id=:i"), {"i": uid})).first()
    assert verify_password("new-pw-123", row.password_hash)

    # This user is deliberately TENANT-LESS, so the conftest sweep — which only
    # removes users orphaned from a deleted organization — cannot see it. Cleaned up
    # here rather than by widening that sweep to every tenant-less user, which would
    # eventually delete a real account.
    async with get_db_session_superuser() as s:
        await s.execute(text("DELETE FROM users WHERE id = :i"), {"i": uid})


def test_platform_tier_surface_is_gone():
    """The cross-tenant platform console was removed with multi-org support.

    Asserted as an import failure rather than a missing route: re-adding the module is
    how this would silently come back, and a stray import is the first symptom.
    """
    import pytest as _pytest
    with _pytest.raises(ModuleNotFoundError):
        import shared.routers.platform  # noqa: F401


def test_register_does_not_accept_an_organization():
    """Signup must not be able to name — let alone create — an organization."""
    from shared.routers.auth_local import RegisterIn
    assert set(RegisterIn.model_fields) == {"email", "password"}
