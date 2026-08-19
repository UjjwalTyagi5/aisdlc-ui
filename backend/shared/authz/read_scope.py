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

from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.permissions import has_permission

def active_binding(alias: str = "rb") -> str:
    """The liveness half alone: this binding still grants, whoever holds it.

    Split out of `live_binding` for the queries that ask about a SCOPE rather than a
    person — "who administers this unit" needs the same status-and-expiry rule with no
    user filter at all. Kept as a shared fragment for the reason the whole family exists:
    the rule had been written four different ways and they disagreed.

    Callers must bind `:now`.
    """
    return (
        f"{alias}.status = 'active' "
        f"AND ({alias}.expires_at IS NULL OR {alias}.expires_at > :now)"
    )


def live_binding(alias: str = "rb", *, user_param: str = "u") -> str:
    """The canonical SQL predicate for "this binding still grants something".

    THE ONE PLACE THIS RULE IS WRITTEN, because it was written four different ways and
    they disagreed. `can_perform` checked `status = 'active'` AND the expiry; the scope
    queries here checked `status <> 'deactivated'` and no expiry; `resolve_permissions_
    for_user` — the one that produces the JWT claim every route reads — checked neither.
    An elevation that had lapsed therefore stopped granting permissions on the scoped
    path, kept granting them everywhere else, and kept granting scope in both.

    `= 'active'` rather than `<> 'deactivated'`: nothing writes 'invited' today, so the
    two are equivalent in practice, and the strict form means an unaccepted invitation
    grants nothing on the day something does.

    Callers must bind `:{user_param}` and `:now`.
    """
    return f"{alias}.user_id = :{user_param} AND " + active_binding(alias)


_LIVE = live_binding()

# `contributor` is a PLACEHOLDER, NOT A JOB. It records that the Org Admin put this
# person in a unit and that nobody has yet said what they do there — the frontend's
# `awaitsBusinessUnitRole` reads it as exactly that, and ROLE_PERMISSIONS gives it the
# read-only floor and nothing else.
#
# It must therefore grant no READ SCOPE either, and it did: the binding is written at
# business_unit level, and every scope query below matched on scope_kind alone, so a
# person onboarded ten seconds ago and waiting on a role could open every project in
# the unit. The permission floor made that look harmless — they could only view — but
# viewing every project in a unit IS the disclosure this module exists to prevent.
#
# `IS DISTINCT FROM` rather than `<>`: `role_name` is NULL for a custom-role binding,
# and `NULL <> 'contributor'` is NULL, which would silently drop every custom role
# from its own scope.
def grants_scope(alias: str = "rb") -> str:
    """The half that says this binding confers reach, not merely membership."""
    return f"{alias}.role_name IS DISTINCT FROM 'contributor'"


# GOVERNING A UNIT IS WHAT REACHES ACROSS ITS PROJECTS — not being bound at unit level.
#
# Every scope query used to map a business_unit binding to the unit's whole project
# list whatever role held it. A DELIVERY role bound there therefore saw every project
# in the unit: a Project Admin appointed at unit level could open, and act on, projects
# nobody had put them on, and an Architect the same. The tier is precisely the
# distinction the product already draws — governance roles govern a unit, delivery
# roles deliver inside a project — and it had no expression here.
#
# Delivery bindings still reach their OWN scope: a project binding grants that project,
# which is how somebody gets the projects they are actually on. What goes away is the
# leap from "bound somewhere in this unit" to "everything in this unit".
#
# Mirrors ROLE_TIER in shared/authz/permissions.py. Written as the two governance role
# names rather than joining the tier column because `role_bindings.tier` is nullable
# and advisory, while role_name is what every other authz predicate keys on.
def governs_unit(alias: str = "rb") -> str:
    """True for a binding whose role governs a whole business unit."""
    return f"{alias}.role_name IN ('org_admin', 'bu_admin')"


_SCOPED = f"{_LIVE} AND {grants_scope()}"
#: For business_unit-scoped rows: live, confers reach, AND governs the unit.
_SCOPED_UNIT = f"{_SCOPED} AND {governs_unit()}"

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
            # `_SCOPED`, not `_SCOPED_UNIT`: this answers "which units may you SEE",
            # and somebody bound to a unit is IN it whatever their tier — a Developer
            # who cannot see the name of their own unit cannot read their own /my-access
            # page. What a delivery role must NOT get from a unit binding is the unit's
            # PROJECTS, and that is `visible_project_ids`, which is governance-only.
            f"SELECT DISTINCT w.id FROM workspaces w "
            f"JOIN role_bindings rb ON rb.scope_id = w.id "
            f"WHERE {_SCOPED} AND rb.scope_kind = 'business_unit' "
            f"UNION "
            f"SELECT DISTINCT p.workspace_id FROM projects p "
            f"JOIN role_bindings rb ON rb.scope_id = p.id "
            f"WHERE {_SCOPED} AND rb.scope_kind = 'project'"
        ),
        {"u": user_id, "now": datetime.now(tz=timezone.utc)},
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
            f"SELECT DISTINCT rb.scope_id FROM role_bindings rb "
            f"WHERE {_SCOPED_UNIT} AND rb.scope_kind = 'business_unit'"
        ),
        {"u": user_id, "now": datetime.now(tz=timezone.utc)},
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
