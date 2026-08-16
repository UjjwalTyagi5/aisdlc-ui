"""Which business units a caller may READ, for endpoints that aggregate.

Permissions answer *what* someone may do; this answers *which* units and projects
their answer may be computed from. RLS already confines every query to the
tenant — this narrows it further, inside the tenant, to the caller's own units.

Aggregating endpoints need it because a count discloses as much as a row: telling
a Business Unit Admin that "the organization has 47 people" is a fact about units
they cannot open. Anything returning totals must therefore compute them from the
allowed set rather than filter rows after the fact.
"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.permissions import has_permission

# Holding either means the caller reads the whole organization. Mirrors the
# frontend's resolveSessionScope(): org_admin holds admin:*, and settings:manage
# is org-admin-only, so no other role reaches this branch.
ORG_WIDE_PERMISSIONS = ("admin:*", "settings:manage")


def is_org_wide(request: Request) -> bool:
    perms = getattr(request.state, "permissions", []) or []
    return any(has_permission(perms, p) for p in ORG_WIDE_PERMISSIONS)


async def allowed_workspace_ids(db: AsyncSession, request: Request) -> list[str] | None:
    """Workspace ids this caller may aggregate over, or None meaning 'the whole org'.

    An empty list is a real answer — a signed-in user with no bindings sees
    zeroes — so callers must distinguish it from None rather than treating both
    as "no filter". Getting that backwards shows a brand-new account the entire
    organization's figures.

    A project-scoped binding pulls in its parent unit: you cannot show someone
    their project's spend without naming the unit it rolls up to.
    """
    if is_org_wide(request):
        return None

    user_id = getattr(request.state, "user_id", "") or ""
    if not user_id:
        return []

    rows = (await db.execute(
        text(
            "SELECT DISTINCT w.id FROM workspaces w "
            "JOIN role_bindings rb ON rb.scope_id = w.id "
            "WHERE rb.user_id = :u AND rb.scope_kind = 'business_unit' "
            "  AND rb.status <> 'deactivated' "
            "UNION "
            "SELECT DISTINCT p.workspace_id FROM projects p "
            "JOIN role_bindings rb ON rb.scope_id = p.id "
            "WHERE rb.user_id = :u AND rb.scope_kind = 'project' "
            "  AND rb.status <> 'deactivated'"
        ),
        {"u": user_id},
    )).fetchall()
    return [str(r[0]) for r in rows]


async def administered_workspace_ids(db: AsyncSession, request: Request) -> list[str] | None:
    """Units the caller may WRITE into, or None meaning 'the whole organization'.

    Strictly narrower than allowed_workspace_ids: only bindings AT the unit count.
    A project-scoped binding lets you read the unit your project rolls up to; it
    does not make you an administrator of that unit, so it must not let you add
    members or grant roles there.
    """
    if is_org_wide(request):
        return None

    user_id = getattr(request.state, "user_id", "") or ""
    if not user_id:
        return []

    rows = (await db.execute(
        text(
            "SELECT DISTINCT rb.scope_id FROM role_bindings rb "
            "WHERE rb.user_id = :u AND rb.scope_kind = 'business_unit' "
            "  AND rb.status <> 'deactivated'"
        ),
        {"u": user_id},
    )).fetchall()
    return [str(r[0]) for r in rows]


async def assert_can_write_workspace(
    db: AsyncSession, request: Request, workspace_id: str
) -> None:
    """Refuse a write aimed at a unit the caller does not administer.

    `member:manage` says the caller may manage members *somewhere* — a Business
    Unit Admin and a Project Admin both hold it. It does not say WHICH unit, and
    every /admin write took the unit id straight from the request body without
    ever asking. This is the missing half.

    404 rather than 403: a unit the caller does not administer should not be
    confirmed to exist by the error code.
    """
    from fastapi import HTTPException  # noqa: PLC0415 - avoids a circular import at module load

    administered = await administered_workspace_ids(db, request)
    if administered is None:
        return
    if workspace_id not in administered:
        raise HTTPException(status_code=404, detail="not found")
