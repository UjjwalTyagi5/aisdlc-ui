"""Three project-scoped surfaces the API used to answer empty for.

  GET/PUT/DELETE /projects/{id}/agent-access-overrides
  GET/PUT        /projects/{id}/integrations
  GET/DELETE     /admin/cross-bu-grants

Grouped because each is one table and one screen, and three routers of forty lines
would be three places to look for the same shape of thing rather than one.

THE WRITE GUARD IS SHARED AND IT IS THE POINT. `member:manage` / `connector:manage`
say a caller may act SOMEWHERE; `_assert_can_write_project` says which project.
Without it, holding the permission anywhere would mean holding it everywhere — the
same hole `assert_can_write_workspace` closes one level up.
"""
from __future__ import annotations

import json
import logging
import uuid as _uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.audit import RBAC_ROLE_REVOKED, record_rbac_change
from shared.authz.dependency import require_permission
from shared.authz.project_scope import assert_can_administer_project
from shared.authz.token_epoch import bump_user_epoch
from shared.authz.read_scope import administered_workspace_ids, is_org_wide
from shared.db import get_db_session

logger = logging.getLogger(__name__)

project_scoped_router = APIRouter(
    dependencies=[Depends(require_permission("artifact:view"))],
)

INVOLVEMENT = ("none", "use", "primary", "owner")


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


async def _project_or_404(db: AsyncSession, tenant_id: str, project_id: str) -> Any:
    try:
        pid = str(_uuid.UUID(project_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=422, detail="project_id must be a UUID")
    row = (
        await db.execute(
            text(
                "SELECT id, workspace_id, display_name, connectors, mcp_servers "
                "FROM projects WHERE id = CAST(:p AS uuid) AND tenant_id = CAST(:t AS uuid)"
            ),
            {"p": pid, "t": tenant_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return row


async def _assert_can_write_project(db: AsyncSession, request: Request, project: Any) -> None:
    """Org-wide, the parent unit's admin, or this project's own admin.

    Delegates to `shared.authz.project_scope` — see the note on the twin in
    project_members.py for why this stopped being written out here.
    """
    await assert_can_administer_project(db, request, project)


# ── agent access overrides ───────────────────────────────────────────────────


class OverrideIn(BaseModel):
    role: str = Field(min_length=1, max_length=64)
    phase: str = Field(min_length=1, max_length=32)
    involvement: str


@project_scoped_router.get("/projects/{project_id}/agent-access-overrides")
async def list_overrides(
    project_id: str, request: Request, db: AsyncSession = Depends(get_db_session)
) -> list[dict[str, Any]]:
    """What this project changed about which roles reach which agents.

    EMPTY IS THE NORMAL ANSWER and means "this project uses the built-in matrix" —
    not that something is missing. The overrides are the exception, so a project with
    none is the common case rather than an unconfigured one.
    """
    project = await _project_or_404(db, _tenant_id(request), project_id)
    rows = (
        await db.execute(
            text(
                "SELECT id, role, phase, involvement, set_by, set_at "
                "FROM agent_access_overrides WHERE project_id = :p ORDER BY role, phase"
            ),
            {"p": project.id},
        )
    ).fetchall()
    return [
        {
            "id": str(r.id),
            "projectId": str(project.id),
            "role": r.role,
            "phase": r.phase,
            "involvement": r.involvement,
            "setBy": r.set_by or "system",
            "setAt": r.set_at.isoformat() if r.set_at else None,
        }
        for r in rows
    ]


@project_scoped_router.put(
    "/projects/{project_id}/agent-access-overrides",
    dependencies=[Depends(require_permission("member:manage"))],
)
async def set_override(
    project_id: str,
    body: OverrideIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Set one (role, phase) override. Upsert — the same pair set twice is one answer."""
    tenant_id = _tenant_id(request)
    project = await _project_or_404(db, tenant_id, project_id)
    await _assert_can_write_project(db, request, project)

    if body.involvement not in INVOLVEMENT:
        raise HTTPException(
            status_code=422, detail=f"involvement must be one of {INVOLVEMENT}"
        )

    await db.execute(
        text(
            "INSERT INTO agent_access_overrides "
            "  (id, tenant_id, project_id, role, phase, involvement, set_by) "
            "VALUES (CAST(:i AS uuid), CAST(:t AS uuid), :p, :r, :ph, :inv, :by) "
            "ON CONFLICT (project_id, role, phase) DO UPDATE "
            "  SET involvement = EXCLUDED.involvement, set_by = EXCLUDED.set_by, "
            "      set_at = now()"
        ),
        {
            "i": str(_uuid.uuid4()), "t": tenant_id, "p": project.id,
            "r": body.role, "ph": body.phase, "inv": body.involvement,
            "by": getattr(request.state, "user_id", None),
        },
    )
    await db.flush()
    rows = await list_overrides(project_id, request, db)
    return next(r for r in rows if r["role"] == body.role and r["phase"] == body.phase)


@project_scoped_router.delete(
    "/projects/{project_id}/agent-access-overrides",
    status_code=204,
    response_class=Response,
    dependencies=[Depends(require_permission("member:manage"))],
)
async def clear_override(
    project_id: str,
    role: str,
    phase: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Put one pair back to the built-in matrix. Deleting the row IS the reset."""
    project = await _project_or_404(db, _tenant_id(request), project_id)
    await _assert_can_write_project(db, request, project)
    await db.execute(
        text(
            "DELETE FROM agent_access_overrides "
            "WHERE project_id = :p AND role = :r AND phase = :ph"
        ),
        {"p": project.id, "r": role, "ph": phase},
    )
    await db.flush()
    return Response(status_code=204)


# ── project integrations ─────────────────────────────────────────────────────


class CredentialIn(BaseModel):
    kind: str
    targetId: str = Field(min_length=1, max_length=255)
    account: Optional[str] = Field(default=None, max_length=255)
    # Accepted and NEVER stored — see the handler.
    secret: Optional[str] = None


@project_scoped_router.get("/projects/{project_id}/integrations")
async def list_project_integrations(
    project_id: str, request: Request, db: AsyncSession = Depends(get_db_session)
) -> list[dict[str, Any]]:
    """What this project may use, and whose credential is behind each one.

    The permitted set comes from the GRANT to the project's unit, not from what the
    organisation onboarded: a project can only use what its unit was given. Wiring
    (`projects.connectors`) says which stages it reached, and the credentials say who
    identified themselves to it.
    """
    tenant_id = _tenant_id(request)
    project = await _project_or_404(db, tenant_id, project_id)

    granted = (
        await db.execute(
            text(
                "SELECT kind, target_ref FROM integration_grants "
                "WHERE tenant_id = CAST(:t AS uuid) AND workspace_id = :w"
            ),
            {"t": tenant_id, "w": project.workspace_id},
        )
    ).fetchall()

    creds: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in (
        await db.execute(
            text(
                "SELECT id, owner_id, kind, target_id, account, secret_ref, updated_at "
                "FROM project_integration_credentials WHERE project_id = :p"
            ),
            {"p": project.id},
        )
    ).fetchall():
        creds.setdefault((r.kind, r.target_id), []).append(
            {
                "id": str(r.id),
                "projectId": str(project.id),
                "ownerId": r.owner_id,
                "kind": r.kind,
                "targetId": r.target_id,
                "account": r.account,
                # The only part of a secret a UI should ever see.
                "hasSecret": r.secret_ref is not None,
                "updatedAt": r.updated_at.isoformat() if r.updated_at else None,
            }
        )

    out = []
    for kind, target in granted:
        wired = (project.connectors if kind == "connector" else project.mcp_servers) or {}
        stages: list[str] = []
        if isinstance(wired, dict):
            stages = [st for st, ids in wired.items() if target in (ids or [])]
        elif isinstance(wired, list) and target in wired:
            stages = []
        out.append(
            {
                "kind": kind,
                "targetId": target,
                "stages": stages,
                "credentials": creds.get((kind, target), []),
            }
        )
    return out


@project_scoped_router.put(
    "/projects/{project_id}/integrations",
    dependencies=[Depends(require_permission("connector:manage"))],
)
async def upsert_project_credential(
    project_id: str,
    body: CredentialIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Record THIS caller's credential against an integration the project may use.

    KEYED ON THE OWNER, not just the project. A credential authenticates a PERSON
    against a tool — a repo bot, a board account, a database role are each somebody's
    — and keyed on the project alone the second contributor to configure Jira would
    silently replace the first, with neither able to tell.

    THE SECRET IS READ AND DISCARDED. There is no secret store wired to this table
    yet, so `secret_ref` stays null and `hasSecret` reports false. Storing the value
    in the column instead would put it in every backup and every replica, which is
    the outcome making the column a reference was meant to prevent — so it is not
    stored at all until there is somewhere proper to put it.
    """
    tenant_id = _tenant_id(request)
    project = await _project_or_404(db, tenant_id, project_id)
    if body.kind not in ("connector", "mcp"):
        raise HTTPException(status_code=422, detail="kind must be 'connector' or 'mcp'")

    permitted = (
        await db.execute(
            text(
                "SELECT 1 FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
                "  AND workspace_id = :w AND kind = :k AND target_ref = :r"
            ),
            {"t": tenant_id, "w": project.workspace_id, "k": body.kind, "r": body.targetId},
        )
    ).first()
    if permitted is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "not_granted",
                "message": (
                    "This business unit has not been given that integration. Ask an "
                    "Organization Admin to grant it first."
                ),
            },
        )

    owner = getattr(request.state, "user_id", "") or ""
    await db.execute(
        text(
            "INSERT INTO project_integration_credentials "
            "  (id, tenant_id, project_id, owner_id, kind, target_id, account) "
            "VALUES (CAST(:i AS uuid), CAST(:t AS uuid), :p, :o, :k, :r, :a) "
            "ON CONFLICT (project_id, owner_id, kind, target_id) DO UPDATE "
            "  SET account = EXCLUDED.account, updated_at = now()"
        ),
        {
            "i": str(_uuid.uuid4()), "t": tenant_id, "p": project.id, "o": owner,
            "k": body.kind, "r": body.targetId, "a": body.account,
        },
    )
    await db.flush()
    rows = await list_project_integrations(project_id, request, db)
    return next(r for r in rows if r["kind"] == body.kind and r["targetId"] == body.targetId)


# ── cross-BU grants ──────────────────────────────────────────────────────────


@project_scoped_router.get("/admin/cross-bu-grants")
async def list_cross_bu_grants(
    request: Request, db: AsyncSession = Depends(get_db_session)
) -> list[dict[str, Any]]:
    """Live loans touching a unit the viewer administers — lent OUT and borrowed IN.

    Both directions, because an admin needs both answers and they are one fact seen
    from two sides: "who of mine is working elsewhere" and "whose people are working
    here".
    """
    tenant_id = _tenant_id(request)
    administered = await administered_workspace_ids(db, request)

    sql = (
        "SELECT g.id, g.user_id, g.role, g.approved_by, g.approved_at, "
        "       g.parent_workspace_id, pw.display_name AS parent_name, "
        "       g.project_id, p.display_name AS project_name, "
        "       p.workspace_id AS target_workspace_id, tw.display_name AS target_name, "
        "       u.email "
        "FROM cross_bu_grants g "
        "JOIN projects p ON p.id = g.project_id "
        "JOIN workspaces pw ON pw.id = g.parent_workspace_id "
        "JOIN workspaces tw ON tw.id = p.workspace_id "
        "LEFT JOIN users u ON u.id = g.user_id "
        "WHERE g.tenant_id = CAST(:t AS uuid)"
    )
    params: dict[str, Any] = {"t": tenant_id}
    rows = (await db.execute(text(sql), params)).fetchall()

    out = []
    for r in rows:
        parent, target = str(r.parent_workspace_id), str(r.target_workspace_id)
        if administered is not None and parent not in administered and target not in administered:
            continue
        local = (r.email or r.user_id).split("@", 1)[0].replace(".", " ").title()
        out.append(
            {
                "id": str(r.id),
                "identityId": r.user_id,
                "displayName": local,
                "projectId": str(r.project_id),
                "projectName": r.project_name,
                "parentWorkspaceId": parent,
                "parentWorkspaceName": r.parent_name,
                "targetWorkspaceId": target,
                "targetWorkspaceName": r.target_name,
                "role": r.role,
                "approvedBy": r.approved_by or "system",
                "approvedAt": r.approved_at.isoformat() if r.approved_at else None,
                # Which side of the loan the viewer is on.
                "lentByYou": administered is None or parent in administered,
            }
        )
    return out


class RevokeGrantIn(BaseModel):
    identityId: str
    projectId: str


@project_scoped_router.delete(
    "/admin/cross-bu-grants",
    dependencies=[Depends(require_permission("member:manage"))],
)
async def revoke_cross_bu_grant(
    body: RevokeGrantIn, request: Request, db: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    """End a loan.

    ONLY THE LENDING UNIT'S ADMIN. The borrowing unit can take the person off the
    project like any other member; ending the LOAN is the lender's, because it is
    their person and their headcount.
    """
    tenant_id = _tenant_id(request)
    grant = (
        await db.execute(
            text(
                "SELECT id, parent_workspace_id, project_id FROM cross_bu_grants "
                "WHERE tenant_id = CAST(:t AS uuid) AND user_id = :u "
                "  AND project_id = CAST(:p AS uuid)"
            ),
            {"t": tenant_id, "u": body.identityId, "p": body.projectId},
        )
    ).first()
    if grant is None:
        # Idempotent: the loan being already over satisfies the intent.
        return {"ok": True, "changed": False}

    administered = await administered_workspace_ids(db, request)
    if administered is not None and str(grant.parent_workspace_id) not in administered:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "not_the_lender",
                "message": (
                    "Only the business unit that lent this person can end the loan. "
                    "You can remove them from the project instead."
                ),
            },
        )

    await db.execute(
        text("DELETE FROM cross_bu_grants WHERE id = :i"), {"i": grant.id}
    )
    # The seat goes with the loan — leaving the binding would keep them working on a
    # project their own unit has taken them off.
    #
    # No rank check here, deliberately: this is the lending unit ending its OWN loan,
    # and its authority comes from having made the loan rather than from outranking
    # whatever role the borrowing project gave the person.
    removed = (
        await db.execute(
            text(
                "SELECT role_name FROM role_bindings WHERE user_id = :u "
                "  AND scope_kind = 'project' AND scope_id = :p"
            ),
            {"u": body.identityId, "p": grant.project_id},
        )
    ).fetchall()
    await db.execute(
        text(
            "DELETE FROM role_bindings WHERE user_id = :u AND scope_kind = 'project' "
            "  AND scope_id = :p"
        ),
        {"u": body.identityId, "p": grant.project_id},
    )
    # Audited on the route's own session, so the revocation and the record of it
    # commit together. This path removed bindings silently until now.
    for row in removed:
        await record_rbac_change(
            db,
            tenant_id=tenant_id,
            actor_id=getattr(request.state, "user_id", None),
            event_type=RBAC_ROLE_REVOKED,
            subject_id=body.identityId,
            scope_kind="project",
            scope_id=str(grant.project_id),
            role=row.role_name,
            extra={"reason": "cross_bu_loan_ended"},
        )
    await db.flush()
    # This path deletes bindings directly rather than through revoke_role, so it has to
    # invalidate the borrowed person's token itself — otherwise the loan ends and they
    # keep working on the project until their session lapses.
    await bump_user_epoch(tenant_id, body.identityId)
    logger.info("cross-bu loan ended: user=%s project=%s", body.identityId, body.projectId)
    return {"ok": True, "changed": True}
