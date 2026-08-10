"""Phase 3 — local email+password auth + platform tier."""
from shared.models.orm import PlatformUser, User


def test_user_has_password_hash_column():
    cols = {c.name for c in User.__table__.columns}
    assert "password_hash" in cols


def test_platform_user_model():
    cols = {c.name for c in PlatformUser.__table__.columns}
    assert {"user_id", "email", "platform_role", "active"} <= cols


def test_migration_0013_shape():
    from pathlib import Path
    mig = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0013_local_auth.py"
    text = mig.read_text(encoding="utf-8")
    assert 'revision = "0013"' in text
    assert 'down_revision = "0012"' in text
    assert "platform_users" in text
    assert "password_hash" in text


def test_password_hash_roundtrip():
    from shared.auth.passwords import hash_password, verify_password
    h = hash_password("s3cret-pw")
    assert h != "s3cret-pw"
    assert verify_password("s3cret-pw", h) is True
    assert verify_password("wrong", h) is False
    assert verify_password("s3cret-pw", None) is False


import pytest


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
async def test_is_platform_admin_email(monkeypatch):
    import config.env as env
    monkeypatch.setattr(env, "PLATFORM_ADMIN_EMAILS", ["boss@co.com"])
    from shared.auth import platform_identity as pi
    assert pi.is_platform_admin_email("BOSS@co.com") is True
    assert pi.is_platform_admin_email("nobody@co.com") is False
    assert pi.is_platform_admin_email("") is False


@pytest.mark.asyncio
async def test_seed_platform_admins_idempotent(monkeypatch):
    import config.env as env
    monkeypatch.setattr(env, "PLATFORM_ADMIN_EMAILS", ["seedtest@co.com"])
    monkeypatch.setattr(env, "PLATFORM_ADMIN_PASSWORD", "seed-pw-123")
    from shared.auth.platform_identity import seed_platform_admins, is_platform_user
    await seed_platform_admins()
    await seed_platform_admins()  # second run must not error (idempotent)
    assert await is_platform_user("platform|seedtest@co.com") is True
    assert await is_platform_user("platform|nobody@co.com") is False


@pytest.mark.asyncio
async def test_login_returns_token_and_tier(monkeypatch):
    import config.env as env
    monkeypatch.setattr(env, "PLATFORM_ADMIN_EMAILS", ["boss@co.com"])
    monkeypatch.setattr(env, "PLATFORM_ADMIN_PASSWORD", "pw-12345")
    from shared.auth.platform_identity import seed_platform_admins
    from shared.routers.auth_local import login, LoginIn
    await seed_platform_admins()
    out = await login(LoginIn(email="boss@co.com", password="pw-12345"))
    assert out.tier == "platform"
    assert out.token
    assert out.permissions == ["platform:*"]

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


def test_platform_router_routes_are_gated():
    from shared.routers.platform import platform_router
    routes = [r for r in platform_router.routes if hasattr(r, "dependant")]
    assert routes, "router has no routes"
    for r in routes:
        assert any(
            getattr(getattr(d, "call", None), "__rbac_require_permission__", False)
            for d in r.dependant.dependencies
        ), f"route {r.path} not gated"
