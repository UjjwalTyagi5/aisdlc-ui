"""You may not grant a role that holds more than you do.

THE HOLE THIS CLOSES. Every grant endpoint validated the requested role against
`ALL_ROLES` and nothing else. `ALL_ROLES` contains `org_admin`, whose permission
set is `["admin:*"]`. And `resolve_permissions_for_user` unions permissions across
a user's bindings *ignoring scope_kind* — so an `org_admin` binding written at
PROJECT scope still puts `admin:*` in that person's token.

Composed, those three facts meant anyone holding `member:manage` — a Business Unit
Admin, a Project Admin — could add a person, or themselves, as `org_admin` on a
project they legitimately administered, and become organization admin at the next
login. `admin.py` even carried a comment naming the risk; the fix applied there
constrained WHERE a grant could be aimed, never WHAT could be granted.

WHY THIS IS NOT THE CUSTOM-ROLE SUBSET RULE. `custom_roles.py::_assert_creator_holds`
requires the whole requested permission set to be a subset of the creator's own,
which is right when someone is COMPOSING a bundle of permissions. Applied to
assigning a built-in role it is wrong, and provably so: `developer` carries
`skill:edit` and `project_admin` does not, so a strict subset rule stops a Project
Admin staffing a developer — the single most ordinary act on the endpoint.

The escalation was never about specialist delivery permissions. Handing a QA
engineer `artifact:approve_testing` confers no power over the person who handed it
out. What made the hole an escalation was conferring **authority over access
itself**: the permissions that decide who may do what. So the rule here is a subset
check narrowed to exactly those, which refuses `org_admin` (`admin:*`) and
`bu_admin` (`role:manage`, `workspace:manage`) to a Project Admin while leaving
every delivery role grantable.

It deliberately still permits granting your own role — a Project Admin appointing a
co-admin of their project, a Business Unit Admin a co-admin of their unit. That is
delegation of authority already held, not acquisition of new authority, and
forbidding it would make every role a single point of failure.

See finding 2 in `docs/rbac-audit-2026-08-17.md`.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.permissions import has_permission
from shared.authz.resolver import resolve_permissions_for_user
from shared.authz.role_permissions import effective_by_role

logger = logging.getLogger(__name__)

# The permissions that confer authority over ACCESS rather than over work. Holding
# one of these lets you change what other people may do, so conferring one you do
# not hold is how a grant becomes an escalation — and conferring one you DO hold is
# ordinary delegation.
#
# `admin:*` is here as a literal because it is what `org_admin` carries, and the
# whole attack was granting `org_admin` at a scope nobody was checking. It is not
# reachable by the wildcard shortcut below either: a caller who holds it returns
# early, and one who does not must be refused it by name.
ESCALATING_PERMISSIONS: frozenset[str] = frozenset({
    "admin:*",
    "role:manage",
    "member:manage",
    "settings:manage",
    "workspace:manage",
})


async def assert_can_grant_role(
    db: AsyncSession, request: Request, role_name: str
) -> None:
    """Refuse a grant of `role_name` carrying access-authority the caller lacks.

    Only `ESCALATING_PERMISSIONS` are compared. A role's specialist permissions —
    `skill:edit`, `artifact:approve_testing`, `run:create` — are the point of
    assigning it and confer no power over the assigner, so they are not checked;
    see the module docstring for why a full subset rule is wrong here.

    `admin:*` passes everything, by the same reasoning as custom roles: the
    wildcard IS the full catalogue, so an Organization Admin assigning any role is
    not an escalation.

    The excess permissions ARE named in the 403, unlike the deliberately opaque
    refusal `require_permission` returns. They are the caller's own permissions
    described back to them, so there is nothing to disclose — and "forbidden" with
    no indication of why a role they can see in the picker was refused sends people
    to support rather than to a different role.

    Resolved from the DATABASE, not from `request.state.permissions`. The token is
    a snapshot from login; an override edited since then, or a binding revoked
    since then, must count against the caller here. This is the same choice
    `custom_roles.py` made, for the same reason.

    Reads the effective (override-aware) permission set for the target role, so an
    organization that has retuned what `bu_admin` grants gets the rank check
    against what it grants HERE, not against the shipped default.
    """
    caller_id = getattr(request.state, "user_id", "") or ""
    tenant_id = getattr(request.state, "tenant_id", "") or ""
    if not caller_id or not tenant_id:
        # No established identity — fail closed rather than fall through to a
        # subset check against an empty held-set, which would refuse with a
        # confusing message instead of the correct one.
        raise HTTPException(status_code=403, detail="Forbidden")

    held = await resolve_permissions_for_user(caller_id, tenant_id)
    if has_permission(held, "admin:*"):
        return

    by_role = await effective_by_role(db, tenant_id)
    target = by_role.get(role_name)
    if target is None:
        # The routes all validate against ALL_ROLES first and return 422, so an
        # unknown role does not reach here. Reaching it means the catalogue and
        # the role list disagree — a server fault, not a permission one, and it
        # must not be reported as a refusal the caller could act on.
        raise HTTPException(
            status_code=500, detail=f"role '{role_name}' has no permission set"
        )

    held_set = set(held)
    excess = sorted(
        p for p in target if p in ESCALATING_PERMISSIONS and p not in held_set
    )
    if excess:
        logger.warning(
            "grant refused: user=%s tried to grant role=%s carrying permissions "
            "they lack: %s",
            caller_id,
            role_name,
            excess,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"You cannot grant '{role_name}' — it carries access-management "
                "permissions you do not hold: " + ", ".join(excess)
            ),
        )
