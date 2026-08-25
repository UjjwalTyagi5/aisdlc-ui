"""Which of the platform roles is this caller acting as?

Permissions answer what someone may DO; `read_scope` answers WHICH units they may do it
in. This answers a third question neither covers: WHO THEY ARE on the tier ladder —
which is what request routing is expressed in. "One tier above the requester" cannot be
computed from a permission set, because two roles can hold `member:manage` and sit on
different rungs.

HIGHEST STANDING WINS. Somebody bound as `bu_admin` in one unit and `developer` on a
project in another is a Business Unit Admin for routing purposes: their request should
climb from the highest authority they actually hold, or they could file from their
lowest rung and have their own tier decide it.

Deliberately NOT derived from `request.state.permissions`. The frontend's
`effectivePlatformRole` infers a role from permission strings because that is all a
session cookie carries; here the bindings themselves are one query away, and inference
would guess `custom` and `contributor` wrong — both hold permission sets that overlap
built-in roles exactly.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.permissions import has_permission
from shared.authz.read_scope import ORG_WIDE_PERMISSIONS, live_binding

logger = logging.getLogger(__name__)

# Most authority first. Anything not listed is a delivery contributor, which the
# escalation ladder treats identically — see REQUEST_ESCALATION_CHAIN.
_STANDING: tuple[str, ...] = ("org_admin", "bu_admin", "project_admin")


async def roles_held(db: AsyncSession, user_id: str) -> list[str]:
    """Every non-deactivated role name this user holds, any scope."""
    if not user_id:
        return []
    # Liveness matters here as much as anywhere: standing decides who a governance
    # request routes to, so an elevation that has lapsed must stop making someone a
    # Business Unit Admin for routing purposes, not just for permission checks.
    rows = (
        await db.execute(
            text(
                f"SELECT DISTINCT role_name FROM role_bindings rb "
                f"WHERE {live_binding()}"
            ),
            {"u": user_id, "now": datetime.now(tz=timezone.utc)},
        )
    ).fetchall()
    return [r[0] for r in rows]


async def platform_role_for(
    db: AsyncSession, *, user_id: str, permissions: list[str]
) -> Optional[str]:
    """The rule itself, computable without a Request.

    Split out so LOGIN can answer the same question. The frontend cannot: a
    Contributor and a `custom` bundle both resolve to exactly `['artifact:view']`,
    so `effectivePlatformRole`'s permission inference had no way to tell them apart
    and showed every newly-onboarded Contributor as "Custom". The fix is for the
    server — which reads bindings, not a permission bundle — to state the role
    outright, which is what `session.platformRole` has always been the seam for.

    Org-wide standing is checked first and not merely as a shortcut: the org
    admin's binding is at ORGANIZATION scope, and a caller can also reach org-wide
    standing through `settings:manage` without an `org_admin` row. Reading only
    bindings would put such a caller on the wrong rung.
    """
    if any(has_permission(permissions, p) for p in ORG_WIDE_PERMISSIONS):
        return "org_admin"

    held = await roles_held(db, user_id)
    if not held:
        return None

    for role in _STANDING:
        if role in held:
            return role

    # A delivery role. Which one matters for agent-access stage two (the agent's
    # owner must match), so return the actual name rather than a placeholder.
    # `contributor` is the "no role yet" placeholder and loses to anything real.
    real = [r for r in held if r != "contributor"]
    return real[0] if real else "contributor"


async def effective_platform_role(db: AsyncSession, request: Request) -> Optional[str]:
    """The caller's role for routing purposes, or None when they hold nothing."""
    return await platform_role_for(
        db,
        user_id=getattr(request.state, "user_id", "") or "",
        permissions=getattr(request.state, "permissions", []) or [],
    )


async def resolve_platform_role_for_user(
    user_id: str, tenant_id: str, permissions: list[str]
) -> Optional[str]:
    """Login-time entry point: opens its own tenant-scoped session.

    Mirrors `resolve_permissions_for_user`, and for the same reason — `role_bindings`
    is FORCE RLS, so this must run under the tenant GUC or it reads nothing and every
    caller silently looks role-less.

    Originally presentation-only (a login-time label), but `agent_skills.py`'s write
    routes (sub-project 2) now also call this per-request to feed
    `assert_can_write_agent_scope`'s role check — a genuine authorization input, not
    just a label. That is safe specifically BECAUSE this function fails closed: any
    exception below returns `None`, and `assert_can_write_agent_scope` treats
    `role=None` as an automatic deny at the personal ("user") scope and as "no
    permission held" everywhere else (`has_permission` never matches a `None`/absent
    permission). If this fail-closed behavior is ever "improved" to fail open (e.g.
    returning a cached/last-known role instead of `None` on error), re-audit every
    authorization call site before doing so — the "never fail a login over a label"
    reasoning below no longer fully applies once this return value can deny or allow
    a write.
    """
    if not tenant_id:
        return None
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415 — import cycle

    try:
        async with get_db_session_for_tenant(tenant_id) as session:
            return await platform_role_for(
                session, user_id=user_id, permissions=permissions
            )
    except Exception:  # noqa: BLE001
        # Presentation only — the frontend falls back to inferring from permissions,
        # which is what it did before this existed. Never fail a login over a label.
        logger.exception("could not resolve platform role for %s", user_id)
        return None


async def actor_display_name(db: AsyncSession, request: Request) -> str:
    """A human name for this caller, for audit trails and request rows.

    Read from `users`, NOT from a token claim. The BFF mints the bearer and could
    carry a name in it, but the database is the authority on who an id belongs to,
    and a display name that arrives with the request is a display name the request
    can choose. It ends up written into an append-only trail, so it should not be
    caller-supplied.

    `users` stores no name column, so the email's local part is titled — the same
    derivation the BFF's people directory uses, which keeps one person spelled the
    same way on the Users page and in a timeline entry.
    """
    user_id = getattr(request.state, "user_id", "") or ""
    if not user_id:
        return "Unknown"

    email = (
        await db.execute(text("SELECT email FROM users WHERE id = :u"), {"u": user_id})
    ).scalar()
    if not email:
        return user_id

    local = str(email).split("@", 1)[0]
    parts = [p for p in re.split(r"[._\-+]+", local) if p]
    return " ".join(p[:1].upper() + p[1:] for p in parts) or user_id
