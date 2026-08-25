"""`can_perform(user, permission, resource)` — the single permission resolver.

Answers one question: *may this user do this thing, to this specific resource?*

    organization  ->  business_unit  ->  project  ->  workstream

Given a resource, the chain from it up to the organization is built, and the user's
active assignments are matched against every level of it. A role held at ANY ancestor
of the resource applies to it — that is what makes an Organization Admin able to act
on a business unit's resources without a separate assignment per unit.

The reverse never happens. A role held at a project does not reach the business unit
above it, because the project is not an ancestor of the unit. There is no implicit
grant anywhere in this module: every path either finds a matching assignment that
carries the permission, or returns DENY.

DENY IS THE DEFAULT AND EVERY FAILURE RETURNS IT
------------------------------------------------
No tenant, unknown resource, resource in another tenant, no assignment, expired
assignment, deactivated assignment, assignment whose role lacks the permission — all
DENY. A resolver that raises on "resource not found" and returns False on "no
permission" invites a caller to treat the exception as an error case and fall through;
this one has a single failure value.

EXPIRY IS ENFORCED BY THE CLOCK
-------------------------------
`expires_at IS NULL` means permanent. A non-null value in the past means the
assignment is inert *immediately*, with no sweep job required — the elevation ends
whether or not anything remembered to revoke it.

RELATIONSHIP TO `require_permission`
------------------------------------
`require_permission` is the resource-LESS route gate: it asks "does this caller hold
this permission anywhere in the tenant". That is the right question for a route like
`POST /projects` where no resource exists yet, and the wrong one for
`GET /projects/{id}`. This module is the resource-aware answer; routes move onto it
one at a time, because each move is a behaviour change that needs its own test.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.permissions import has_permission
from shared.authz.read_scope import governs_unit, grants_scope
from shared.authz.role_permissions import effective_by_role

logger = logging.getLogger(__name__)

ScopeKind = Literal["organization", "business_unit", "project", "workstream"]

# Ordered widest -> narrowest. The chain for any resource is a suffix of this.
SCOPE_ORDER: tuple[ScopeKind, ...] = (
    "organization",
    "business_unit",
    "project",
    "workstream",
)


@dataclass(frozen=True)
class Decision:
    """Why the answer is what it is. Returned by `explain_can_perform`.

    Kept separate from the boolean API because a permission check that returns a
    reason invites callers to branch on the reason, and the only safe thing to branch
    on is the boolean. The detail exists for audit records and for debugging a denial,
    not for control flow.
    """

    allowed: bool
    reason: str
    matched_scope_kind: Optional[str] = None
    matched_scope_id: Optional[str] = None
    matched_role: Optional[str] = None
    chain: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def _uuid_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(_uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


async def resolve_scope_chain(
    session: AsyncSession,
    tenant_id: str,
    resource_kind: ScopeKind,
    resource_id: str,
) -> list[tuple[str, str]]:
    """Every (scope_kind, scope_id) an assignment could sit at to reach this resource.

    Returned widest first: organization, then business unit, then project, then the
    workstream itself. Returns [] when the resource does not exist or belongs to
    another tenant — the caller must treat that as DENY, not as "no constraints".

    One query per level rather than a recursive CTE: the chain is at most four deep
    and each hop is a primary-key lookup, so the join cost is trivial and the code
    stays legible.
    """
    tenant = _uuid_or_none(tenant_id)
    if tenant is None:
        return []

    if resource_kind == "organization":
        # The organization IS the tenant. A resource id naming a different org is not
        # a chain to check, it is a cross-tenant request.
        rid = _uuid_or_none(resource_id)
        return [("organization", tenant)] if rid == tenant else []

    rid = _uuid_or_none(resource_id)
    if rid is None:
        return []

    if resource_kind == "business_unit":
        row = (await session.execute(
            text("SELECT id FROM workspaces WHERE id = CAST(:i AS uuid) "
                 "AND organization_id = CAST(:t AS uuid)"),
            {"i": rid, "t": tenant},
        )).first()
        if row is None:
            return []
        return [("organization", tenant), ("business_unit", rid)]

    if resource_kind == "project":
        row = (await session.execute(
            text("SELECT workspace_id FROM projects WHERE id = CAST(:i AS uuid)"),
            {"i": rid},
        )).first()
        if row is None:
            return []
        return [
            ("organization", tenant),
            ("business_unit", str(row.workspace_id)),
            ("project", rid),
        ]

    if resource_kind == "workstream":
        row = (await session.execute(
            text(
                "SELECT w.id, w.project_id, p.workspace_id "
                "FROM workstreams w JOIN projects p ON p.id = w.project_id "
                "WHERE w.id = CAST(:i AS uuid)"
            ),
            {"i": rid},
        )).first()
        if row is None:
            return []
        return [
            ("organization", tenant),
            ("business_unit", str(row.workspace_id)),
            ("project", str(row.project_id)),
            ("workstream", rid),
        ]

    return []


async def _permissions_at(
    session: AsyncSession, user_id: str, chain: list[tuple[str, str]], tenant_id: str
) -> list[tuple[str, str, str, list[str]]]:
    """Active, unexpired assignments on the chain, with the permissions each carries.

    Returns (scope_kind, scope_id, role_label, permissions) per matching binding.
    Built-in roles resolve through role_permissions; custom roles through
    custom_role_permissions. Both are joined here so a caller cannot accidentally
    honour one kind and not the other.
    """
    if not chain:
        return []

    now = datetime.now(tz=timezone.utc)
    scope_ids = [scope_id for _, scope_id in chain]

    rows = (await session.execute(
        text(
            """
            SELECT rb.scope_kind,
                   rb.scope_id,
                   rb.role_name,
                   COALESCE(rb.role_name, cr.name)      AS role_label,
                   crp.permission_name                  AS custom_permission
            FROM role_bindings rb
            LEFT JOIN custom_roles cr            ON cr.id = rb.custom_role_id
            LEFT JOIN custom_role_permissions crp ON crp.custom_role_id = rb.custom_role_id
            WHERE rb.user_id = :u
              AND rb.scope_id = ANY(CAST(:ids AS uuid[]))
              AND rb.status = 'active'
              AND (rb.expires_at IS NULL OR rb.expires_at > :now)
              -- A `contributor` binding is a placeholder, not a grant: it says this
              -- person was put in a unit and is waiting to be told what they do
              -- there. Excluded here as well as from the scope queries, because a
              -- unit is an ANCESTOR of every project in it — so without this, the
              -- placeholder satisfied a resource-level artifact:view on each of
              -- them and the project list hid what a pasted URL still opened.
              AND rb.role_name IS DISTINCT FROM 'contributor'
              -- THE SAME TIER RULE, applied to the scope CHAIN. A unit is an ancestor
              -- of every project in it, so without this a delivery role bound at unit
              -- level carried its permissions onto each of them: the project list
              -- would hide a project that a pasted URL still opened, and acted on.
              -- Governance roles are meant to reach across a unit; that is what
              -- governing one is.
              AND (rb.scope_kind <> 'business_unit'
                   OR rb.role_name IN ('org_admin', 'bu_admin'))
            """
        ),
        {"u": user_id, "ids": scope_ids, "now": now},
    )).fetchall()

    # Built-in roles are resolved through `effective_by_role`, NOT by joining
    # role_permissions above. That table holds the SHIPPED DEFAULT; what a role grants
    # in this organization may have been retuned from the Roles page. Resolving it here
    # the same way `resolve_permissions_for_user` does is what keeps a scoped check
    # from disagreeing with the token the caller is holding — a split that presents as
    # "works on the dashboard, denied on the project page".
    #
    # tenant_id is passed explicitly rather than left to RLS alone: role_permission_overrides'
    # tenant_isolation policy only narrows a SELECT when the querying role is actually
    # subject to RLS. A superuser/table-owner connection (as local/dev commonly uses)
    # bypasses RLS outright, and an unfiltered query then merges every tenant's overrides
    # into one dict — a real cross-tenant permission leak, not just a test artifact.
    by_role = await effective_by_role(session, tenant_id)

    grouped: dict[tuple[str, str, str], list[str]] = {}
    for r in rows:
        if r.role_name:
            permissions = sorted(by_role.get(r.role_name, set()))
        elif r.custom_permission is not None:
            permissions = [r.custom_permission]
        else:
            # A binding whose role grants nothing. Kept out of the result rather than
            # recorded as an empty match, so "matched but unauthorized" and "no match"
            # cannot be confused downstream.
            continue
        if not permissions:
            continue
        grouped.setdefault((r.scope_kind, str(r.scope_id), r.role_label), []).extend(permissions)

    # Narrowest scope first, so an explanation names the most specific assignment that
    # granted access rather than an ancestor that happens to also grant it.
    order = {kind: i for i, kind in enumerate(SCOPE_ORDER)}
    return sorted(
        [(k[0], k[1], k[2], perms) for k, perms in grouped.items()],
        key=lambda t: order.get(t[0], 0),
        reverse=True,
    )


async def visible_project_ids(
    session: AsyncSession, *, user_id: str, tenant_id: str
) -> list[str] | None:
    """Projects this user may see. `None` means every project in the tenant.

    The mirror image of `resolve_scope_chain`: that walks UP from a resource to find
    an assignment, this walks DOWN from the assignments to find the resources. Same
    rule, applied in the direction a list endpoint needs — a list cannot ask
    "may I see this one?" per row without an N+1, and answering it per row after
    paging would return short pages and a total describing rows the caller cannot open.

    Reach, by scope of the binding:
      organization  -> everything (None)
      business_unit -> every project in that unit
      project       -> that project
      workstream    -> the project it belongs to, because a workstream is only
                       reachable through its project's screens

    `None` versus `[]` matters and callers must distinguish them: None is "no filter",
    [] is "this user sees nothing". Treating [] as None shows a brand-new account the
    whole organization.
    """
    tenant = _uuid_or_none(tenant_id)
    if tenant is None or not user_id:
        return []

    now = datetime.now(tz=timezone.utc)
    active = (
        "rb.user_id = :u AND rb.status = 'active' "
        "AND (rb.expires_at IS NULL OR rb.expires_at > :now)"
    )
    # `contributor` is a placeholder, not a job — the same rule `read_scope.grants_scope`
    # states, and it has to be repeated here because this module deliberately keeps its
    # own copy of the binding predicate. Without it, somebody onboarded into a unit and
    # still waiting for a role saw every project in that unit on the Projects screen,
    # which is the opposite of what the placeholder means.
    scoped = f"{active} AND {grants_scope()}"
    # A business_unit binding maps to the unit's whole project list, so it must be
    # a role that GOVERNS the unit. A delivery role bound there — a Project Admin
    # appointed at unit level, an Architect — otherwise saw every project in the
    # unit, including ones nobody had put them on.
    scoped_unit = f"{scoped} AND {governs_unit()}"
    params = {"u": user_id, "now": now, "t": tenant}

    org_scoped = (await session.execute(
        text(
            f"SELECT 1 FROM role_bindings rb WHERE {active} "
            "AND rb.scope_kind = 'organization' AND rb.scope_id = CAST(:t AS uuid) LIMIT 1"
        ),
        params,
    )).first()
    if org_scoped is not None:
        return None

    rows = (await session.execute(
        text(
            f"""
            SELECT p.id FROM projects p
              JOIN role_bindings rb ON rb.scope_id = p.workspace_id
             WHERE {scoped_unit} AND rb.scope_kind = 'business_unit'
            UNION
            SELECT p.id FROM projects p
              JOIN role_bindings rb ON rb.scope_id = p.id
             WHERE {scoped} AND rb.scope_kind = 'project'
            UNION
            SELECT w.project_id FROM workstreams w
              JOIN role_bindings rb ON rb.scope_id = w.id
             WHERE {scoped} AND rb.scope_kind = 'workstream'
            """
        ),
        params,
    )).fetchall()
    return [str(r[0]) for r in rows]


async def explain_can_perform(
    session: AsyncSession,
    *,
    user_id: str,
    permission: str,
    tenant_id: str,
    resource_kind: ScopeKind,
    resource_id: str,
) -> Decision:
    """`can_perform` with the reasoning attached — for audit records and debugging."""
    if not user_id:
        return Decision(False, "no user")
    if not permission:
        return Decision(False, "no permission requested")
    if not tenant_id:
        return Decision(False, "no tenant context")

    chain = await resolve_scope_chain(session, tenant_id, resource_kind, resource_id)
    if not chain:
        # Unknown, or in another tenant. Deliberately the same answer either way: a
        # different one would confirm the resource exists to someone who cannot see it.
        return Decision(False, f"{resource_kind} not found in this tenant")

    matches = await _permissions_at(session, user_id, chain, tenant_id)
    if not matches:
        return Decision(
            False,
            "no active assignment on the resource's scope chain",
            chain=tuple(chain),
        )

    for scope_kind, scope_id, role_label, perms in matches:
        if has_permission(perms, permission):
            return Decision(
                True,
                f"granted by '{role_label}' at {scope_kind}",
                matched_scope_kind=scope_kind,
                matched_scope_id=scope_id,
                matched_role=role_label,
                chain=tuple(chain),
            )

    return Decision(
        False,
        f"assigned on the chain, but no role there carries '{permission}'",
        chain=tuple(chain),
    )


async def can_perform(
    session: AsyncSession,
    *,
    user_id: str,
    permission: str,
    tenant_id: str,
    resource_kind: ScopeKind,
    resource_id: str,
) -> bool:
    """May `user_id` exercise `permission` against this resource? Deny by default."""
    decision = await explain_can_perform(
        session,
        user_id=user_id,
        permission=permission,
        tenant_id=tenant_id,
        resource_kind=resource_kind,
        resource_id=resource_id,
    )
    if not decision.allowed:
        logger.info(
            "can_perform DENY user=%s permission=%s %s=%s reason=%s",
            user_id, permission, resource_kind, resource_id, decision.reason,
        )
    return decision.allowed
