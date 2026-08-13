"""What a built-in role actually grants HERE — shipped default, or this org's override.

ONE ANSWER, TWO CALLERS. Permissions are read on two independent paths:

  resolve_permissions_for_user   at login, baked into the JWT and the session
  can_perform._permissions_at    per scoped check, against the live binding chain

Both must apply overrides, and they must apply them identically. If only the first did,
a retuned role would appear to work until a scoped check disagreed with the token the
caller was holding — the kind of split that presents as "it works on the dashboard but
not on the project page" and takes a day to find. So the merge lives here and both call
it, rather than each joining the tables its own way.

THE MERGE RULE IS WHOLE-SET REPLACEMENT. A role with any override rows holds exactly
those; a role with none holds its shipped default. There is no per-permission delta —
see the migration for why every answer to "what happens when the default later changes"
is a surprise.

NOT CACHED. Two small indexed reads against tables with tens of rows, on a path that
already does several. A cache here would need invalidating from the admin endpoint, and
a stale permission cache fails in the direction that matters — someone keeps an
authority that was just taken away.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def shipped_defaults(session: AsyncSession) -> dict[str, set[str]]:
    """role -> the permissions it ships with, straight from `role_permissions`.

    Read from the DATABASE rather than from `_ROLE_PERMISSIONS` in code, even though
    the seeder keeps them in step. The table is what every other query joins, so
    reading anything else here would let the UI show a default that no permission
    check would ever honour.
    """
    rows = (
        await session.execute(text("SELECT role_name, permission_name FROM role_permissions"))
    ).fetchall()
    out: dict[str, set[str]] = {}
    for role_name, permission_name in rows:
        out.setdefault(role_name, set()).add(permission_name)
    return out


async def overrides(session: AsyncSession, tenant_id: Optional[str] = None) -> dict[str, set[str]]:
    """role -> the permissions this organization gave it instead, if any.

    `tenant_id` is optional because RLS already confines the rows to the caller's
    tenant when the session was opened with the GUC set. Passing it adds an explicit
    predicate for callers on a superuser session, where RLS would not narrow anything.
    """
    sql = "SELECT role_name, permission_name FROM role_permission_overrides"
    params: dict[str, str] = {}
    if tenant_id:
        sql += " WHERE tenant_id = CAST(:t AS uuid)"
        params["t"] = tenant_id
    rows = (await session.execute(text(sql), params)).fetchall()
    out: dict[str, set[str]] = {}
    for role_name, permission_name in rows:
        out.setdefault(role_name, set()).add(permission_name)
    return out


async def effective_by_role(
    session: AsyncSession, tenant_id: Optional[str] = None
) -> dict[str, set[str]]:
    """role -> what it grants in this organization. The one answer both readers use."""
    defaults = await shipped_defaults(session)
    tuned = await overrides(session, tenant_id)
    return {**defaults, **tuned}


async def effective_for_roles(
    session: AsyncSession, role_names: list[str], tenant_id: Optional[str] = None
) -> set[str]:
    """The union of what these roles grant — a person's effective permission set."""
    if not role_names:
        return set()
    by_role = await effective_by_role(session, tenant_id)
    out: set[str] = set()
    for name in role_names:
        out |= by_role.get(name, set())
    return out
