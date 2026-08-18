"""Local email+password auth — login, logout, the password lifecycle, and /auth/me.

Mints our HS256 JWT (config.auth.jwt.create_access_token) with tenant_id + permissions
baked in, exactly as the local middleware path expects. Every account is org tier and
resolves its permissions through the normal RBAC path. Uniform 401 on any LOGIN failure
(no account enumeration).

THERE IS NO SELF-SERVE REGISTRATION. `POST /auth/register` was removed: accounts come only
from an Organization Admin onboarding somebody (`shared/routers/onboarding.py`). The old
endpoint created an account with no bindings — establishing an identity an admin could
then bind — which was defensible but left a public account-creating route on a product
whose whole access model is "an admin admits you". Removing it is one fewer thing to
misconfigure. The frontend's "Create account" tab went with it.

HOW SOMEBODY FIRST GETS IN. Onboarding creates the account with `password_hash` NULL and
emails a single-use link; `POST /auth/reset-password` is what sets the first password. So
`/auth/forgot-password` and `/auth/reset-password` are not only a recovery path, they are
the ONLY path to a first password — which is why they are treated as carefully as login.
No password is ever emailed.

Every route here is public()-marked for the D-05 boot scan. `/auth/login`,
`/auth/forgot-password` and `/auth/reset-password` are additionally in `_EXEMPT_PATHS`,
because a caller presenting a reset link is by definition not authenticated. `/auth/me`,
`/auth/change-password` and `/auth/logout` are NOT exempt: they need an identity, and
`public()` there means "no permission required", not "no authentication".
"""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from config.auth.jwt import create_access_token
from config.env import RESET_TOKEN_TTL_HOURS
from shared.auth.denylist import add_jti_to_user_denylist
from shared.auth.passwords import hash_password, verify_password
from shared.authz.dependency import public
from shared.authz.effective_role import resolve_platform_role_for_user
from shared.authz.resolver import resolve_permissions_for_user
from shared.authz.token_epoch import bump_user_epoch
from shared.db import get_db_session_superuser
from shared.services import email_templates, password_setup
from shared.services.email import send_email

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
    # Which catalogued role this person acts as. The frontend cannot work this out
    # from `permissions` — `contributor` and `custom` are the same set — so the
    # server states it. None for an account with no bindings yet.
    platform_role: str | None = None


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
    platform_role = await resolve_platform_role_for_user(user_id, tenant_id, perms)
    token = create_access_token(user_id=user_id, tenant_id=tenant_id, permissions=perms,
                                platform_role=platform_role)
    return LoginOut(token=token, tier="org", user_id=user_id, tenant_id=tenant_id or None,
                    permissions=perms, tenant_name=tenant_name, platform_role=platform_role)


@auth_local_router.post("/auth/login", response_model=LoginOut, dependencies=[Depends(public())])
async def login_endpoint(body: LoginIn) -> LoginOut:
    return await login(body)


@auth_local_router.get("/auth/me", response_model=LoginOut, dependencies=[Depends(public())])
async def me(request: Request) -> LoginOut:
    perms = getattr(request.state, "permissions", []) or []
    tid = getattr(request.state, "tenant_id", "") or ""
    uid = getattr(request.state, "user_id", "")
    # Re-resolved rather than read off the token: this endpoint exists to tell a
    # client what is true NOW, and a role assigned since the token was minted is
    # exactly the case it is asked about.
    return LoginOut(token="", tier="org", user_id=uid, tenant_id=tid or None,
                    permissions=perms,
                    platform_role=await resolve_platform_role_for_user(uid, tid, perms))


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


@auth_local_router.post("/auth/logout", dependencies=[Depends(public())])
async def logout(request: Request) -> dict:
    """Revoke the presented token so it dies with the session.

    Deleting the cookie was previously the whole of logout, which meant a token
    copied off the wire stayed valid for its full lifetime after the user
    believed they had signed out. That was tolerable only while the BFF minted a
    fresh short-lived token per request; now that the browser holds one
    backend-issued token for the session, an unrevokable logout is a real gap.

    `public()` because the caller is signing OUT — requiring a permission to stop
    holding one is the wrong shape, and the JWT middleware has already
    established identity by the time this runs (this path is not in
    _EXEMPT_PATHS).

    Always 200. Logout must not fail: Redis being down, or a legacy token with no
    jti, leaves the token alive until expiry, but reporting an error would leave
    the user unable to sign out at all and is not something they can act on.
    """
    jti = getattr(request.state, "jti", "") or ""
    sub = getattr(request.state, "user_id", "") or ""
    tenant_id = getattr(request.state, "tenant_id", "") or ""
    redis_client = getattr(request.app.state, "redis_denylist", None)

    if not (jti and sub and redis_client):
        logger.info("logout: token not revocable (jti=%s redis=%s)", bool(jti), bool(redis_client))
        return {"ok": True, "revoked": False}

    # Only the token's REMAINING life needs denying — after that its own expiry
    # refuses it. Clamped at 0 so a just-expired token cannot set a past EXPIREAT.
    import time as _time  # noqa: PLC0415 - local, keeps module import cheap

    remaining = max(int(getattr(request.state, "token_exp", 0) or 0) - int(_time.time()), 0)
    await add_jti_to_user_denylist(redis_client, tenant_id, sub, jti, remaining)
    logger.info("logout: revoked jti for user=%s tenant=%s", sub, tenant_id)
    return {"ok": True, "revoked": True}


# ── first password, and forgotten ones ───────────────────────────────────────
#
# One mechanism serving two moments. An onboarded account has `password_hash` NULL and
# cannot be signed into at all, so the emailed link is not merely a recovery path — it is
# how anybody ever gets in. That is why these two endpoints are held to the same standard
# as login rather than treated as a convenience.


class ForgotPasswordIn(BaseModel):
    email: EmailStr


@auth_local_router.post("/auth/forgot-password", dependencies=[Depends(public())])
async def forgot_password(body: ForgotPasswordIn) -> dict:
    """Email a single-use reset link, if that address has an account.

    ALWAYS 200, ALWAYS THE SAME BODY. Whether the address exists, is deactivated, or has
    never been seen, the answer is identical — otherwise this endpoint is an account
    enumerator, and a more sensitive one than login, because it needs no password to
    probe with. The uniform response is the whole point of the design and must not be
    "improved" into a helpful "no such account" message.

    A deactivated account gets no email either. It has been switched off deliberately,
    and a working reset link would be a way back in.
    """
    email = str(body.email).strip().lower()
    generic = {"ok": True}

    async with get_db_session_superuser() as s:
        row = (
            await s.execute(
                text("SELECT id, active FROM users WHERE lower(email) = :e"), {"e": email}
            )
        ).first()
        if row is None or not row.active:
            logger.info("forgot-password for unknown or inactive address (no email sent)")
            return generic

        token = await password_setup.issue(
            s, user_id=row.id, purpose="reset", ttl_hours=RESET_TOKEN_TTL_HOURS
        )

    subject, text_body, html_body = email_templates.reset_email(
        token, RESET_TOKEN_TTL_HOURS
    )
    await send_email(email, subject, text_body, html_body)
    return generic


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=200)


@auth_local_router.post("/auth/reset-password", dependencies=[Depends(public())])
async def reset_password(body: ResetPasswordIn) -> dict:
    """Spend a link and set the password on the account it belongs to.

    The token is the only credential presented, and consuming it is atomic — see
    `password_setup.consume` — so a link cannot be redeemed twice.

    400 with a single code on any bad token. The set-password page distinguishes expired
    from already-used through `GET /auth/reset-password/validate` BEFORE the user types,
    which is where that distinction is useful; here, having already accepted a password,
    the only thing left to say is that it could not be applied.

    Setting a password also DEACTIVATES OUTSTANDING TOKENS for that user, which
    `consume` handles for the one presented — the extra sweep below covers the case of an
    invite and a reset both being live at once, so completing either retires the other.
    """
    async with get_db_session_superuser() as s:
        user_id = await password_setup.consume(s, body.token)
        if user_id is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_token",
                    "message": "This link is no longer valid. Request a new one.",
                },
            )

        active = (
            await s.execute(text("SELECT active FROM users WHERE id = :i"), {"i": user_id})
        ).scalar()
        if not active:
            # The account was deactivated between issuing the link and using it.
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_token",
                    "message": "This link is no longer valid. Request a new one.",
                },
            )

        await s.execute(
            text("UPDATE users SET password_hash = :ph WHERE id = :i"),
            {"ph": hash_password(body.new_password), "i": user_id},
        )
        await s.execute(
            text(
                "UPDATE password_reset_tokens SET used_at = now() "
                "WHERE user_id = :u AND used_at IS NULL"
            ),
            {"u": user_id},
        )

    # Every token this account holds is now dead, and so is every session: a password
    # change is the clearest possible statement that older tokens should stop working,
    # and this is the same mechanism a revocation uses.
    tenant_id = None
    async with get_db_session_superuser() as s:
        tenant_id = (
            await s.execute(text("SELECT tenant_id FROM users WHERE id = :i"), {"i": user_id})
        ).scalar()
    if tenant_id:
        # exact=True: the credential itself changed, so a token minted in this same
        # second necessarily came from a login with the NEW password. Rounding up here
        # refused the user's own fresh session — setting a password and signing straight
        # in is the ordinary path, and it was 401ing.
        await bump_user_epoch(str(tenant_id), user_id, exact=True)

    logger.info("password set via link for user=%s", user_id)
    return {"ok": True}


@auth_local_router.get("/auth/reset-password/validate", dependencies=[Depends(public())])
async def validate_reset_token(token: str = "") -> dict:
    """Report whether a link is still usable, without spending it.

    Read-only deliberately: a page load must not consume the token, or a mail client that
    pre-fetches links would burn every invite before its recipient clicked.

    Returns a status the page can render — `ok`, `expired`, `used`, `unknown`. Naming
    which is safe: whoever holds the link already holds it, so "this one is spent" tells
    them nothing new, and the alternative is a dead end that becomes a support ticket.
    """
    async with get_db_session_superuser() as s:
        status = await password_setup.inspect(s, token)
    return {"status": status}
