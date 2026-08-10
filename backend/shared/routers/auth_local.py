"""Local email+password auth — POST /auth/login, GET /auth/me (Phase 3).

Mints our HS256 JWT (config.auth.jwt.create_access_token) with tenant_id + permissions
baked in, exactly as the local middleware path expects. Platform admins get platform:*
and tier=platform; org users get their resolved permissions and tier=org. No public
registration. Uniform 401 on any failure (no account enumeration).

Both routes are public()-marked for the D-05 boot scan: /auth/login is exempt from the
JWT middleware (in _EXEMPT_PATHS) but still seen by the scan, and /auth/me reads an
already-validated request.state without a per-route permission gate.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from config.auth.jwt import create_access_token
from shared.auth.passwords import hash_password, verify_password
from shared.auth.platform_identity import PLATFORM_WILDCARD, is_platform_user
from shared.authz.dependency import public
from shared.authz.resolver import resolve_permissions_for_user
from shared.db import get_db_session_superuser

logger = logging.getLogger(__name__)
auth_local_router = APIRouter()


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class LoginOut(BaseModel):
    token: str
    tier: str
    user_id: str
    tenant_id: str | None
    permissions: list[str]


async def login(body: LoginIn) -> LoginOut:
    email = body.email.strip().lower()
    async with get_db_session_superuser() as s:
        row = (await s.execute(
            text("SELECT id, tenant_id, password_hash, active FROM users WHERE lower(email) = :e"),
            {"e": email},
        )).first()
    if not row or not row.active or not verify_password(body.password, row.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user_id = row.id
    if await is_platform_user(user_id):
        token = create_access_token(user_id=user_id, tenant_id="", permissions=[PLATFORM_WILDCARD])
        return LoginOut(token=token, tier="platform", user_id=user_id, tenant_id=None,
                        permissions=[PLATFORM_WILDCARD])

    tenant_id = str(row.tenant_id) if row.tenant_id else ""
    if tenant_id:
        async with get_db_session_superuser() as s:
            suspended = (await s.execute(
                text("SELECT suspended FROM organizations WHERE id = :id"),
                {"id": tenant_id},
            )).scalar()
        if suspended:
            raise HTTPException(status_code=403, detail="Organization suspended")
    perms = await resolve_permissions_for_user(user_id, tenant_id) if tenant_id else []
    token = create_access_token(user_id=user_id, tenant_id=tenant_id, permissions=perms)
    return LoginOut(token=token, tier="org", user_id=user_id, tenant_id=tenant_id or None,
                    permissions=perms)


@auth_local_router.post("/auth/login", response_model=LoginOut, dependencies=[Depends(public())])
async def login_endpoint(body: LoginIn) -> LoginOut:
    return await login(body)


@auth_local_router.get("/auth/me", response_model=LoginOut, dependencies=[Depends(public())])
async def me(request: Request) -> LoginOut:
    perms = getattr(request.state, "permissions", []) or []
    tid = getattr(request.state, "tenant_id", "") or ""
    tier = "platform" if PLATFORM_WILDCARD in perms else "org"
    return LoginOut(token="", tier=tier, user_id=getattr(request.state, "user_id", ""),
                    tenant_id=tid or None, permissions=perms)


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


async def change_password(request: Request, body: ChangePasswordIn) -> dict:
    user_id = getattr(request.state, "user_id", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    async with get_db_session_superuser() as s:
        row = (await s.execute(
            text("SELECT password_hash FROM users WHERE id = :i"), {"i": user_id}
        )).first()
        if not row or not verify_password(body.current_password, row.password_hash):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        await s.execute(
            text("UPDATE users SET password_hash = :ph WHERE id = :i"),
            {"ph": hash_password(body.new_password), "i": user_id},
        )
    return {"ok": True}


@auth_local_router.post("/auth/change-password", dependencies=[Depends(public())])
async def change_password_endpoint(request: Request, body: ChangePasswordIn) -> dict:
    return await change_password(request, body)
