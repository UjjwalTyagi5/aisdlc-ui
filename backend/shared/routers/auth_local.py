"""Local email+password auth — POST /auth/register, POST /auth/login, GET /auth/me.

Mints our HS256 JWT (config.auth.jwt.create_access_token) with tenant_id + permissions
baked in, exactly as the local middleware path expects. Every account is org tier and
resolves its permissions through the normal RBAC path. Uniform 401 on any LOGIN failure
(no account enumeration).

Self-serve registration joins the ONE organization seeded at startup (shared/auth/
bootstrap.py) and grants NOTHING: the account is created with no role bindings, so it
resolves to an empty permission set until an admin binds it to a role. Signing up is
therefore requesting access, not obtaining it — and nobody can create an organization,
which is why there is no organization field on the form.

Registration deliberately DOES return 409 on a duplicate email, unlike login's uniform
401. Login must not leak which accounts exist; a signup form has to tell you your email
is already taken or it is unusable.

All three routes are public()-marked for the D-05 boot scan, and /auth/register and
/auth/login are in _EXEMPT_PATHS so the JWT middleware lets them through unauthenticated.
"""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from config.auth.jwt import create_access_token
from shared.auth.bootstrap import get_default_org_id
from shared.auth.passwords import hash_password, verify_password
from shared.authz.dependency import public
from shared.authz.resolver import resolve_permissions_for_user
from shared.db import get_db_session_superuser

logger = logging.getLogger(__name__)
auth_local_router = APIRouter()


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class LoginOut(BaseModel):
    # `tier` is always "org" now that the platform tier is gone. It is kept in the
    # contract because the frontend session type and its stored cookies still carry it;
    # dropping the field would invalidate every session issued before this change.
    token: str
    tier: str
    user_id: str
    tenant_id: str | None
    permissions: list[str]
    # Organization display name — the app chrome names the org, so the session needs
    # it. None only for an account not yet attached to one.
    tenant_name: str | None = None


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
    tenant_id = str(row.tenant_id) if row.tenant_id else ""
    tenant_name = None
    if tenant_id:
        async with get_db_session_superuser() as s:
            org = (await s.execute(
                text("SELECT suspended, display_name FROM organizations WHERE id = :id"),
                {"id": tenant_id},
            )).first()
        if org is not None and org.suspended:
            raise HTTPException(status_code=403, detail="Organization suspended")
        tenant_name = org.display_name if org is not None else None
    perms = await resolve_permissions_for_user(user_id, tenant_id) if tenant_id else []
    token = create_access_token(user_id=user_id, tenant_id=tenant_id, permissions=perms)
    return LoginOut(token=token, tier="org", user_id=user_id, tenant_id=tenant_id or None,
                    permissions=perms, tenant_name=tenant_name)


@auth_local_router.post("/auth/login", response_model=LoginOut, dependencies=[Depends(public())])
async def login_endpoint(body: LoginIn) -> LoginOut:
    return await login(body)


class RegisterIn(BaseModel):
    email: EmailStr
    # Stating the 8-char floor here turns what would be a 500 into a 422 with a
    # field-level message the signup form can render.
    password: str = Field(min_length=8, max_length=200)


async def register(body: RegisterIn) -> LoginOut:
    """Create an unprivileged account in the one organization, then sign them in.

    The new user gets NO role bindings, so resolve_permissions_for_user returns an
    empty list and every permission-gated route refuses them until an admin grants a
    role. That is the intent: signing up establishes an identity an admin can then
    bind, it does not hand out access.

    Returns the same shape as login so the caller has one response contract to handle
    and the user is not bounced to a login form immediately after signing up.
    """
    email = body.email.strip().lower()

    org_id = await get_default_org_id()
    if org_id is None:
        # Only reachable if the server booted without ORG_ADMIN_EMAILS/PASSWORD set, so
        # no organization was ever seeded. Signing someone into a tenant-less account
        # would strand them, so refuse plainly instead.
        logger.error("register: no organization has been seeded — check ORG_ADMIN_* env")
        raise HTTPException(status_code=503, detail="Sign-up is not available yet")

    async with get_db_session_superuser() as s:
        dup = (await s.execute(
            text("SELECT 1 FROM users WHERE lower(email) = :e"), {"e": email}
        )).first()
        if dup:
            raise HTTPException(
                status_code=409, detail="An account with that email already exists"
            )

        user_id = str(_uuid.uuid4())
        await s.execute(
            text(
                "INSERT INTO users (id, email, password_hash, tenant_id, active) "
                "VALUES (:i, :e, :p, :t, true)"
            ),
            {"i": user_id, "e": email, "p": hash_password(body.password), "t": org_id},
        )
        tenant_name = (await s.execute(
            text("SELECT display_name FROM organizations WHERE id = :id"), {"id": org_id}
        )).scalar()

    # Resolved rather than hardcoded to []: this stays correct if a future default
    # binding is ever introduced, and it exercises the same path as login.
    perms = await resolve_permissions_for_user(user_id, org_id)
    token = create_access_token(user_id=user_id, tenant_id=org_id, permissions=perms)
    logger.info("registered user=%s in org=%s with %d permission(s)", email, org_id, len(perms))
    return LoginOut(
        token=token, tier="org", user_id=user_id, tenant_id=org_id, permissions=perms,
        tenant_name=tenant_name,
    )


@auth_local_router.post(
    "/auth/register", response_model=LoginOut, status_code=201,
    dependencies=[Depends(public())],
)
async def register_endpoint(body: RegisterIn) -> LoginOut:
    return await register(body)


@auth_local_router.get("/auth/me", response_model=LoginOut, dependencies=[Depends(public())])
async def me(request: Request) -> LoginOut:
    perms = getattr(request.state, "permissions", []) or []
    tid = getattr(request.state, "tenant_id", "") or ""
    return LoginOut(token="", tier="org", user_id=getattr(request.state, "user_id", ""),
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
