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
            "ON CONFLICT (project_id, role, phase) WHERE role IS NOT NULL DO UPDATE "
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


# Mirrors frontend/lib/connectors.ts::CONNECTOR_KIND_LABEL — this project screen
# names connectors independently of the org-level Integrations hub's own copy
# (shared/routers/integration_access.py::_CONNECTOR_LABEL, which only covers the 8
# kinds that page happens to render and would silently show a raw slug for the rest).
_CONNECTOR_KIND_LABEL: dict[str, str] = {
    "jira": "Jira",
    "azure_devops": "Azure DevOps",
    "github": "GitHub",
    "azure_repos": "Azure Repos",
    "github_actions": "GitHub Actions",
    "slack": "Slack",
    "ms_teams": "Microsoft Teams",
    "sharepoint": "SharePoint",
    "figma": "Figma",
    "confluence": "Confluence",
    "sonarqube": "SonarQube",
    "sso_okta": "Okta SSO",
    "sso_entra": "Microsoft Entra SSO",
}


class CredentialIn(BaseModel):
    kind: str
    targetId: str = Field(min_length=1, max_length=255)
    label: str = Field(min_length=1, max_length=120)
    account: Optional[str] = Field(default=None, max_length=255)
    # Accepted and NEVER stored — see the handler.
    secret: Optional[str] = None


def _credential_out(row: Any, project_id: Any) -> dict[str, Any]:
    """One project_integration_credentials row -> the ProjectIntegrationCredential
    shape (frontend/lib/schemas/project-integration.ts). `label` reads '' for a
    row written before migration 0030 added the column, rather than null — the
    frontend field is a required string, not a nullable one."""
    return {
        "id": str(row.id),
        "projectId": str(project_id),
        "ownerId": row.owner_id,
        "kind": row.kind,
        "targetId": row.target_id,
        "label": row.label or "",
        "account": row.account,
        # The only part of a secret a UI should ever see.
        "hasSecret": row.secret_ref is not None,
        "updatedBy": row.owner_id,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


@project_scoped_router.get("/projects/{project_id}/integrations")
async def list_project_integrations(
    project_id: str, request: Request, db: AsyncSession = Depends(get_db_session)
) -> list[dict[str, Any]]:
    """What this project may use, and the CALLER's own credential behind each one.

    The permitted set comes from the GRANT to the project's unit, not from what the
    organisation onboarded: a project can only use what its unit was given. Wiring
    (`projects.connectors`) says which stages it reached.

    `credential` IS SCOPED TO THE VIEWER, not the project. A credential is keyed on
    (project, OWNER, kind, target) — see migration 0016's docstring, "the second
    contributor to configure Jira silently replaced the first" is exactly the bug
    this key exists to prevent — and this page's own copy says the same thing back:
    "You never see theirs." Returning every owner's row here would say it and then
    not do it.
    """
    tenant_id = _tenant_id(request)
    project = await _project_or_404(db, tenant_id, project_id)
    viewer_id = getattr(request.state, "user_id", "") or ""

    granted = (
        await db.execute(
            text(
                "SELECT kind, target_ref FROM integration_grants "
                "WHERE tenant_id = CAST(:t AS uuid) AND workspace_id = :w"
            ),
            {"t": tenant_id, "w": project.workspace_id},
        )
    ).fetchall()

    mine: dict[tuple[str, str], Any] = {}
    for r in (
        await db.execute(
            text(
                "SELECT id, owner_id, kind, target_id, label, account, secret_ref, updated_at "
                "FROM project_integration_credentials "
                "WHERE project_id = :p AND owner_id = :o"
            ),
            {"p": project.id, "o": viewer_id},
        )
    ).fetchall():
        mine[(r.kind, r.target_id)] = r

    # MCP server names/descriptions live on mcp_servers, not the grant row —
    # one lookup for every mcp target this project may use, rather than one
    # query per row.
    mcp_targets = [t for k, t in granted if k == "mcp"]
    mcp_info: dict[str, tuple[str, Optional[str]]] = {}
    if mcp_targets:
        try:
            mcp_ids = [_uuid.UUID(t) for t in mcp_targets]
        except ValueError:
            mcp_ids = []
        if mcp_ids:
            for r in (
                await db.execute(
                    text(
                        "SELECT id, server_name, description FROM mcp_servers "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": mcp_ids},
                )
            ).fetchall():
                mcp_info[str(r.id)] = (r.server_name, r.description)

    out = []
    for kind, target in granted:
        wired = (project.connectors if kind == "connector" else project.mcp_servers) or {}
        stages: list[str] = []
        if isinstance(wired, dict):
            stages = [st for st, ids in wired.items() if target in (ids or [])]

        if kind == "mcp":
            name, description = mcp_info.get(target, (target, None))
        else:
            name, description = _CONNECTOR_KIND_LABEL.get(target, target), None

        cred_row = mine.get((kind, target))
        out.append(
            {
                "kind": kind,
                "id": target,
                "name": name,
                "description": description,
                "stages": stages,
                # Every connector authenticates as somebody; no MCP server in this
                # catalogue does (they're service-to-service, not a personal login).
                "needsProjectCredential": kind == "connector",
                "credential": _credential_out(cred_row, project.id) if cred_row else None,
            }
        )
    return out


@project_scoped_router.put(
    "/projects/{project_id}/integrations",
    # NOT connector:manage. That permission means "may onboard/disconnect a
    # tenant-wide connection, edit the MCP catalog, or grant a Business Unit
    # reach" (see connectors.py's install/credentials/disconnect routes,
    # mcp_registry.py's catalog CRUD, integration_access.py's grant/revoke) — a
    # scope decision only org_admin/bu_admin/project_admin make, and
    # test_enterprise_rbac_catalog.py pins every other role OUT of it on purpose.
    # This route is a different act entirely: recording THIS caller's own
    # identity against something the project may already use (see the
    # docstring below) — every delivery role that can see the page should be
    # able to do it, which is exactly what connector:view already means.
    dependencies=[Depends(require_permission("connector:view"))],
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

    THE SECRET IS ACTUALLY STORED, and actually used: `shared.authz.project_credential.resolve_project_secret`
    is checked by every connector's `auth_adapter()` (via
    `BaseConnector._resolve_credential_override`) ahead of the tenant-wide
    credential, whenever the connector factory was given this project+owner (see
    `config/connector_factory.py::get_connector_for_session`'s `owner_id` param).
    A blank `secret` on an update leaves the existing one in place — the field is
    write-only and never round-trips, so "didn't touch it" and "want it gone" must
    stay distinguishable, and this endpoint offers no way to express the second.
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

    secret_ref = None
    if body.secret:
        from shared.authz.project_credential import project_credential_ref  # noqa: PLC0415
        from shared.services import secret_store  # noqa: PLC0415

        secret_ref = project_credential_ref(project_id, owner, body.kind, body.targetId)
        await secret_store.put_secret(tenant_id, secret_ref, body.secret)

    row = (
        await db.execute(
            text(
                "INSERT INTO project_integration_credentials "
                "  (id, tenant_id, project_id, owner_id, kind, target_id, label, account, secret_ref) "
                "VALUES (CAST(:i AS uuid), CAST(:t AS uuid), :p, :o, :k, :r, :l, :a, :sref) "
                "ON CONFLICT (project_id, owner_id, kind, target_id) DO UPDATE "
                "  SET label = EXCLUDED.label, account = EXCLUDED.account, updated_at = now(), "
                # A blank submission (secret_ref NULL here) must not erase an
                # existing one — only a real new secret replaces the old ref.
                "      secret_ref = COALESCE(EXCLUDED.secret_ref, project_integration_credentials.secret_ref) "
                "RETURNING id, owner_id, kind, target_id, label, account, secret_ref, updated_at"
            ),
            {
                "i": str(_uuid.uuid4()), "t": tenant_id, "p": project.id, "o": owner,
                "k": body.kind, "r": body.targetId, "l": body.label, "a": body.account,
                "sref": secret_ref,
            },
        )
    ).one()
    await db.flush()
    # ProjectIntegrationCredential (frontend/lib/schemas/project-integration.ts) —
    # the credential itself, not the wrapping ProjectIntegration row. The two used
    # to be conflated: this endpoint returned a row shaped like the GET list's
    # entries (kind/targetId/stages/credentials), which the PUT response schema
    # (a single credential) could never have matched.
    return _credential_out(row, project.id)


# Connector kinds whose auth_adapter() checks BaseConnector._resolve_credential_override
# (config/connectors/base.py) — see each connector's own "Project-scoped personal
# override" comment. The rest (github, slack, ms_teams, sharepoint, figma) are
# OAuth-first or share a single org app registration with no simple "one string is
# the whole credential" shape to override per person; testing them here would
# silently test the tenant-wide credential instead and call that a personal test.
_PROJECT_CREDENTIAL_TESTABLE_KINDS = frozenset(
    {"jira", "azure_devops", "confluence", "sonarqube", "github_actions"}
)


class CredentialTestIn(BaseModel):
    kind: str
    targetId: str = Field(min_length=1, max_length=255)
    secret: str = Field(min_length=1, max_length=4000)


@project_scoped_router.post(
    "/projects/{project_id}/integrations/test-connection",
    dependencies=[Depends(require_permission("connector:view"))],
)
async def test_project_credential(
    project_id: str,
    body: CredentialTestIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Try a credential BEFORE saving it — never written to secret_store.

    Same shape as MCP's existing POST /mcp/registry/test-connection: the value
    under test lives only in this request and this one connector instance's
    `_credential_override` (config/connectors/base.py), which
    `BaseConnector._resolve_credential_override` reads ahead of everything else.
    A failed attempt leaves nothing behind to clean up.
    """
    tenant_id = _tenant_id(request)
    await _project_or_404(db, tenant_id, project_id)

    if body.kind == "mcp":
        from shared.services import mcp_registry, mcp_client  # noqa: PLC0415

        server = await mcp_registry.get_server(tenant_id, body.targetId)
        if server is None:
            raise HTTPException(status_code=404, detail="MCP server not found")
        cfg = {
            "name": server["server_name"],
            "transport": server["transport"],
            "url": server.get("url"),
            "command": server.get("command"),
            "args": server.get("args") or [],
            "headers": {"Authorization": f"Bearer {body.secret}"},
            "env": None,
        }
        result = await mcp_client.test_connection(cfg)  # type: ignore[arg-type]
        return {
            "ok": bool(result.get("ok")),
            "message": "Connected." if result.get("ok") else "Couldn't connect with this credential.",
        }

    if body.kind != "connector":
        raise HTTPException(status_code=422, detail="kind must be 'connector' or 'mcp'")
    if body.targetId not in _PROJECT_CREDENTIAL_TESTABLE_KINDS:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "not_testable",
                "message": (
                    f"{body.targetId} doesn't support testing a personal credential here yet — "
                    "it authenticates a different way (OAuth or a shared org connection)."
                ),
            },
        )

    from config.connector_factory import get_connector_for_session  # noqa: PLC0415

    connector = await get_connector_for_session(
        kind=body.targetId, tenant_id=tenant_id, unrestricted=True,
    )
    connector._credential_override = body.secret  # noqa: SLF001 — this call only, never persisted

    try:
        health = await connector.health_check()
        ok = getattr(health, "status", "") in ("healthy", "ok")
        return {
            "ok": ok,
            # type(exc).__name__ only if the health check itself failed closed rather
            # than raised — NEVER the raw exception text (credential leakage risk,
            # the same rule every connector's own auth_adapter follows).
            "message": "Connected." if ok else f"Couldn't connect ({getattr(health, 'status', 'unknown')}).",
        }
    except Exception as exc:  # noqa: BLE001 — a bad credential must read as "failed", not 500
        return {"ok": False, "message": f"Couldn't connect ({type(exc).__name__})."}


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
