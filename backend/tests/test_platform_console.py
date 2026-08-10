"""Phase 5 — Platform Console service tests (Tasks 2–5).

Tests cover: org-detail aggregation, suspend toggle, login guard (suspended→403),
reset-org-admin password, and platform-user CRUD (create/list/login/deactivate).

Router-level (httpx/ASGITransport) tests belong to a later unit and are not included here.
"""
import uuid

import email_validator as _ev
import pytest

# Allow .test TLD (RFC 6761 special-use) so pydantic EmailStr accepts test addresses.
_ev.SPECIAL_USE_DOMAIN_NAMES = [d for d in _ev.SPECIAL_USE_DOMAIN_NAMES if d != "test"]

from shared.routers.auth_local import login, LoginIn
from fastapi import HTTPException


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    """Dispose the shared async engine after each test so asyncpg's pool isn't
    reused across pytest-asyncio's per-function event loops (avoids the
    'Event loop is closed' teardown artifact). Mirrors test_provisioning.py."""
    yield
    from shared.db import engine
    await engine.dispose()


# ---------------------------------------------------------------------------
# Task 2 — get_org_detail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_org_detail_returns_admin_and_counts():
    from shared.services.provisioning import provision_organization
    from shared.services.platform_admin import get_org_detail

    slug = f"det-{uuid.uuid4().hex[:8]}"
    res = await provision_organization("Detail Co", slug, f"{slug}@t.test", "password123")
    org_id = res["org_id"]

    detail = await get_org_detail(org_id)

    assert detail["org_id"] == org_id
    assert detail["slug"] == slug
    assert detail["suspended"] is False
    assert any(a["email"] == f"{slug}@t.test" for a in detail["admins"])
    assert detail["run_count"] == 0
    assert detail["member_count"] == 1
    assert detail["total_cost_usd"] == 0.0
    assert any(w["slug"] == "default" for w in detail["workspaces"])
    # The first org-admin shows up in the full member list with the org_admin role.
    me = next(m for m in detail["members"] if m["email"] == f"{slug}@t.test")
    assert "org_admin" in me["roles"]


@pytest.mark.asyncio
async def test_get_org_detail_unknown_raises():
    from shared.services.platform_admin import get_org_detail, OrgNotFoundError

    with pytest.raises(OrgNotFoundError):
        await get_org_detail(str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Task 3 — set_org_suspended + login guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_org_suspended_toggles_flag():
    from shared.services.provisioning import provision_organization
    from shared.services.platform_admin import set_org_suspended, get_org_detail

    slug = f"sus-{uuid.uuid4().hex[:8]}"
    res = await provision_organization("Sus Co", slug, f"{slug}@t.test", "password123")
    org_id = res["org_id"]

    await set_org_suspended(org_id, True)
    assert (await get_org_detail(org_id))["suspended"] is True
    await set_org_suspended(org_id, False)
    assert (await get_org_detail(org_id))["suspended"] is False


@pytest.mark.asyncio
async def test_suspended_org_member_cannot_login():
    from shared.services.provisioning import provision_organization
    from shared.services.platform_admin import set_org_suspended

    slug = f"lck-{uuid.uuid4().hex[:8]}"
    email = f"{slug}@t.test"
    await provision_organization("Lock Co", slug, email, "password123")

    # Sanity: can log in before suspension.
    ok = await login(LoginIn(email=email, password="password123"))
    assert ok.tier == "org"

    await set_org_suspended(ok.tenant_id, True)
    with pytest.raises(HTTPException) as exc:
        await login(LoginIn(email=email, password="password123"))
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Task 4 — reset_org_admin_password
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reset_org_admin_password_updates_hash():
    from shared.services.provisioning import provision_organization
    from shared.services.platform_admin import reset_org_admin_password

    slug = f"rst-{uuid.uuid4().hex[:8]}"
    email = f"{slug}@t.test"
    res = await provision_organization("Reset Co", slug, email, "password123")
    org_id = res["org_id"]

    await reset_org_admin_password(org_id, email, "newpassword456")

    ok = await login(LoginIn(email=email, password="newpassword456"))
    assert ok.tier == "org"


@pytest.mark.asyncio
async def test_reset_password_rejects_non_admin_email():
    from shared.services.provisioning import provision_organization
    from shared.services.platform_admin import reset_org_admin_password, AdminNotFoundError

    slug = f"rst2-{uuid.uuid4().hex[:8]}"
    res = await provision_organization("Reset2 Co", slug, f"{slug}@t.test", "password123")
    with pytest.raises(AdminNotFoundError):
        await reset_org_admin_password(res["org_id"], "stranger@nowhere.test", "newpassword456")


# ---------------------------------------------------------------------------
# Task 5 — platform-user CRUD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_and_list_platform_user():
    from shared.services.platform_admin import (
        create_platform_user, list_platform_users, PlatformUserExistsError,
    )

    email = f"pu-{uuid.uuid4().hex[:8]}@vendor.test"
    created = await create_platform_user(email, "password123", "platform_support")
    assert created["email"] == email
    assert created["platform_role"] == "platform_support"

    users = await list_platform_users()
    assert any(u["email"] == email for u in users)

    with pytest.raises(PlatformUserExistsError):
        await create_platform_user(email, "password123", "platform_admin")


@pytest.mark.asyncio
async def test_created_platform_user_can_login_as_platform():
    from shared.services.platform_admin import create_platform_user

    email = f"pu-{uuid.uuid4().hex[:8]}@vendor.test"
    await create_platform_user(email, "password123", "platform_admin")
    ok = await login(LoginIn(email=email, password="password123"))
    assert ok.tier == "platform"
    assert "platform:*" in ok.permissions


@pytest.mark.asyncio
async def test_set_org_suspended_unknown_raises():
    from shared.services.platform_admin import set_org_suspended, OrgNotFoundError

    with pytest.raises(OrgNotFoundError):
        await set_org_suspended(str(uuid.uuid4()), True)


@pytest.mark.asyncio
async def test_set_platform_user_active_unknown_raises():
    from shared.services.platform_admin import set_platform_user_active, PlatformUserNotFoundError

    with pytest.raises(PlatformUserNotFoundError):
        await set_platform_user_active(str(uuid.uuid4()), False)


@pytest.mark.asyncio
async def test_deactivate_platform_user_blocks_login():
    from shared.services.platform_admin import create_platform_user, set_platform_user_active

    email = f"pu-{uuid.uuid4().hex[:8]}@vendor.test"
    created = await create_platform_user(email, "password123", "platform_admin")
    await set_platform_user_active(created["user_id"], False)
    with pytest.raises(HTTPException) as exc:
        await login(LoginIn(email=email, password="password123"))
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Task 6 — router-level tests (httpx/ASGITransport, gate satisfied by middleware)
# ---------------------------------------------------------------------------

import httpx


def _platform_app():
    """Minimal FastAPI app mounting only the platform router, with a middleware
    that injects request.state.permissions=["platform:*"] and a stable
    request.state.user_id so the platform-admin gate is satisfied without a
    real JWT.
    """
    from fastapi import FastAPI, Request
    from shared.routers.platform import platform_router

    _CALLER_USER_ID = "aaaaaaaa-0000-0000-0000-000000000001"

    app = FastAPI()

    @app.middleware("http")
    async def _inject_perms(request: Request, call_next):
        request.state.permissions = ["platform:*"]
        request.state.tenant_id = ""
        request.state.user_id = _CALLER_USER_ID
        return await call_next(request)

    app.include_router(platform_router)
    return app, _CALLER_USER_ID


@pytest.mark.asyncio
async def test_list_orgs_includes_suspended_and_counts():
    from shared.services.provisioning import provision_organization

    slug = f"api-{uuid.uuid4().hex[:8]}"
    await provision_organization("Api Co", slug, f"{slug}@t.test", "password123")
    app, _ = _platform_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/platform/organizations")
    assert r.status_code == 200
    row = next(o for o in r.json() if o["slug"] == slug)
    assert row["suspended"] is False
    assert row["member_count"] == 1


@pytest.mark.asyncio
async def test_org_detail_endpoint_404_on_unknown():
    app, _ = _platform_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get(f"/platform/organizations/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_post_platform_user_201_and_duplicate_409():
    app, _ = _platform_app()
    email = f"rtr-{uuid.uuid4().hex[:8]}@vendor.test"
    payload = {"email": email, "password": "password123", "platform_role": "platform_support"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        r1 = await c.post("/platform/users", json=payload)
        assert r1.status_code == 201
        assert r1.json()["email"] == email

        r2 = await c.post("/platform/users", json=payload)
        assert r2.status_code == 409


@pytest.mark.asyncio
async def test_patch_platform_user_unknown_returns_404():
    app, _ = _platform_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.patch(f"/platform/users/{uuid.uuid4()}", json={"active": False})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_platform_user_self_deactivate_returns_409():
    """Cannot deactivate your own platform-user account."""
    app, caller_id = _platform_app()
    # First create the platform user whose user_id matches the caller id injected by middleware.
    from shared.services.platform_admin import create_platform_user
    from shared.db import get_db_session_superuser
    from sqlalchemy import text as _text

    email = f"self-{uuid.uuid4().hex[:8]}@vendor.test"
    # Insert the platform_users + users row with the exact caller_id so the guard triggers.
    from shared.auth.passwords import hash_password
    async with get_db_session_superuser() as s:
        # Upsert the users row with the known caller_id
        await s.execute(
            _text(
                "INSERT INTO users (id, email, password_hash, tenant_id, active) "
                "VALUES (:id, :email, :ph, NULL, true) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": caller_id, "email": email, "ph": hash_password("password123")},
        )
        await s.execute(
            _text(
                "INSERT INTO platform_users (user_id, email, platform_role, active) "
                "VALUES (:id, :email, 'platform_admin', true) "
                "ON CONFLICT (user_id) DO NOTHING"
            ),
            {"id": caller_id, "email": email},
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.patch(f"/platform/users/{caller_id}", json={"active": False})
    assert r.status_code == 409
