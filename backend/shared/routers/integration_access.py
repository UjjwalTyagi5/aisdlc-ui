"""Who may use which integration, and who actually does.

THREE LEVELS, AND THIS ROUTER IS ABOUT THE MIDDLE ONE:

  onboarded   the organisation has a connection      `connectors` / `mcp_servers`
  GRANTED     a Business Unit is permitted to use it `integration_grants`   <- here
  used        a project has it wired to its stages   `projects.connectors` / `.mcp_servers`

Only the middle level is a decision anybody makes about somebody else, which is why
it is the only one with an authorisation story: GRANTING is the Organization Admin's,
because a unit that could grant itself an integration is a unit with no grant at all.
REVOKING AT PROJECT LEVEL is either admin tier's — an admin taking something away has
to be able to stop one team without punishing the rest of the unit.

A GRANTED INTEGRATION WITH NOTHING CONNECTED IS A REAL STATE, not an error: it is a
permission waiting on a credential, and `onboarded` reports it so the hub can say
which of the two is missing.

EVERY UNIT THE VIEWER MAY SEE IS LISTED, granted or not, with `via` saying which.
Returning only the granted ones would leave the UI unable to offer the grant — you
cannot give a unit something it is not on the list for.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.dependency import require_permission
from shared.authz.read_scope import administered_workspace_ids, allowed_workspace_ids, is_org_wide
from shared.db import get_db_session
from shared.routers.connectors import _CATALOG_KINDS

logger = logging.getLogger(__name__)

integration_access_router = APIRouter(
    dependencies=[Depends(require_permission("connector:view"))],
)

_CONNECTOR_LABEL = {
    "azure_devops": "Azure DevOps",
    "jira": "Jira",
    "github": "GitHub",
    "azure_repos": "Azure Repos",
    "github_actions": "GitHub Actions",
    "slack": "Slack",
    "sso_okta": "Okta SSO",
    "sso_entra": "Microsoft Entra SSO",
}


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


def _require_org_admin(request: Request) -> None:
    """Granting is the Organization Admin's alone.

    Not `connector:manage`: a Business Unit Admin holds that for their own unit's
    connections, and a unit that can grant itself an integration has no grant.
    """
    if not is_org_wide(request):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "forbidden",
                "message": (
                    "Only an Organization Admin decides which business units may use "
                    "an integration."
                ),
            },
        )


async def _visible_units(db: AsyncSession, request: Request) -> list[tuple[str, str]]:
    allowed = await allowed_workspace_ids(db, request)
    sql = "SELECT id, display_name FROM workspaces WHERE organization_id = CAST(:t AS uuid)"
    params: dict[str, Any] = {"t": _tenant_id(request)}
    if allowed is not None:
        if not allowed:
            return []
        binds = []
        for i, w in enumerate(allowed):
            params[f"w{i}"] = w
            binds.append(f"CAST(:w{i} AS uuid)")
        sql += f" AND id IN ({', '.join(binds)})"
    rows = (await db.execute(text(sql + " ORDER BY display_name"), params)).fetchall()
    return [(str(r[0]), r[1]) for r in rows]


@integration_access_router.get("/integrations/access")
async def list_integration_access(
    request: Request, db: AsyncSession = Depends(get_db_session)
) -> list[dict[str, Any]]:
    """Every integration, with the units that may use it and the projects that do."""
    tenant_id = _tenant_id(request)
    units = await _visible_units(db, request)
    unit_ids = {u[0] for u in units}

    installed_kinds = {
        r[0]
        for r in (
            await db.execute(
                text("SELECT DISTINCT kind FROM workspace_connectors")
            )
        ).fetchall()
    }

    # `tools_snapshot` rides along because this is the only grant-aware read of an
    # MCP server anyone but its creator can make: GET /mcp/registry/{id} is
    # creator-scoped, so a Business Unit Admin holding a server an Org Admin
    # registered can never fetch it there. Without this, "what does this server
    # actually give my agents" was answerable only by the person who typed the URL.
    servers = (
        await db.execute(
            text(
                "SELECT id, server_name, description, tools_snapshot FROM mcp_servers "
                "WHERE tenant_id = CAST(:t AS uuid) ORDER BY server_name"
            ),
            {"t": tenant_id},
        )
    ).fetchall()

    # Which units hold which integration. A set of ids and nothing more: since
    # migration 0024 a grant has no level, so "granted or not" is the whole answer
    # this endpoint has to give about a unit.
    grants: dict[tuple[str, str], set[str]] = {}
    for kind, target, ws in (
        await db.execute(
            text(
                "SELECT kind, target_ref, workspace_id FROM integration_grants "
                "WHERE tenant_id = CAST(:t AS uuid)"
            ),
            {"t": tenant_id},
        )
    ).fetchall():
        grants.setdefault((kind, target), set()).add(str(ws))

    # Which projects wired which integration, and to which stages. Read from the
    # project's own columns — the third level of the cascade, and the only one that
    # says a thing is actually in use rather than merely permitted.
    project_rows = (
        await db.execute(
            text(
                "SELECT id, workspace_id, display_name, connectors, mcp_servers "
                "FROM projects WHERE tenant_id = CAST(:t AS uuid) AND archived = false"
            ),
            {"t": tenant_id},
        )
    ).fetchall()

    def _usage(kind: str, target: str) -> dict[str, list[dict[str, Any]]]:
        by_unit: dict[str, list[dict[str, Any]]] = {}
        for p in project_rows:
            wired = (p.connectors if kind == "connector" else p.mcp_servers) or {}
            # Two shapes are accepted because both are written today: a list of ids,
            # or a {stage: [ids]} map. A list means "wired, stage unknown", which is
            # still worth showing.
            stages: list[str] = []
            if isinstance(wired, dict):
                stages = [st for st, ids in wired.items() if target in (ids or [])]
                if not stages:
                    continue
            elif isinstance(wired, list):
                if target not in wired:
                    continue
            else:
                continue
            by_unit.setdefault(str(p.workspace_id), []).append(
                {"id": str(p.id), "name": p.display_name, "stages": stages}
            )
        return by_unit

    def _row(
        kind: str,
        target: str,
        name: str,
        description: Optional[str],
        onboarded: bool,
        tools: Optional[list] = None,
    ):
        granted = grants.get((kind, target), set())
        usage = _usage(kind, target)
        entries = [
            {
                "id": uid,
                "name": uname,
                "via": "granted" if uid in granted else "none",
                "projects": usage.get(uid, []),
            }
            for uid, uname in units
        ]
        return {
            "kind": kind,
            "id": target,
            "name": name,
            "description": description,
            "origin": "organization",
            "onboarded": onboarded,
            # No `supportedAccess` here since migration 0024. It existed so the unit
            # grant control never offered a level the connector could not honour;
            # that control is gone, and the check moved to where the level is now
            # chosen — the project's stage picker, validated in routers/projects.py.
            "units": entries,
            # Counts what the VIEWER can see, so a Business Unit Admin's "1 unit"
            # is their own rather than a number they cannot account for.
            "grantedUnitCount": len(granted & unit_ids),
            "projectCount": sum(len(v) for k, v in usage.items() if k in unit_ids),
            # The tools the server answered with at its last probe. Empty until
            # one has run — which is "not asked yet", not "offers nothing", and
            # the UI must say so rather than reporting zero tools.
            "tools": tools or [],
        }

    out = [
        _row("connector", k, _CONNECTOR_LABEL.get(k, k), None, k in installed_kinds)
        # The presented catalog, not the accept-set: a row here is something an
        # Org Admin can grant a unit, and granting a kind with no tile hands out
        # access to a connector nobody can reach.
        for k in sorted(_CATALOG_KINDS)
    ]
    out += [
        _row("mcp", str(s.id), s.server_name, s.description, True, s.tools_snapshot)
        for s in servers
    ]
    return out


# The three routes below GRANT and REVOKE integration access across business units.
# The router-level dependency is `connector:view` — a READ permission — which stays for
# the reads. A write announcing itself as a read is how the next reader concludes these
# are safe. `_require_org_admin()` in each body remains the operative check.
# The per-stage move (migration 0024) took `_reconcile_project_overrides` with it.
# It re-narrowed a project's rows whenever a unit's LEVEL changed; a grant no longer
# has a level to change, so there is nothing left to reconcile. Revoking still needs
# no cleanup either — `effective_access()` checks the grant first and returns None
# without ever reaching a project row.

@integration_access_router.post(
    "/integrations/access",
    dependencies=[Depends(require_permission("connector:manage"))],
)
async def grant_integration_access(
    request: Request,
    kind: str,
    id: str,
    workspaceId: Optional[str] = None,
    projectId: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Give a Business Unit reach to an integration.

    NO LEVEL — see the note in the body. This says the unit may use the thing; each
    project stage says what may be done with it.

    BUSINESS UNIT LEVEL ONLY. Granting is how far the organisation's reach decision
    goes; whether one of that unit's projects switches the integration on is the
    project's own wiring, done on its Settings. Revoking DOES reach a project — see
    the DELETE below — because taking something away has to be able to stop one team.
    """
    tenant_id = _tenant_id(request)
    _require_org_admin(request)
    if kind not in ("connector", "mcp", "model_provider"):
        raise HTTPException(status_code=422, detail="kind must be 'connector', 'mcp', or 'model_provider'")
    # NO ACCESS LEVEL HERE ANY MORE (migration 0024). A grant is a reach decision:
    # this unit may use Jira, or it may not. What its agents may DO with Jira is
    # chosen per stage on the project, so the capability check that used to live here
    # — refusing `read` on a notify-only connector — moved with it. Re-adding a level
    # to this endpoint reintroduces the ceiling the migration removed.
    if projectId and not workspaceId:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unit_level_only",
                "message": (
                    "Grants are made to a business unit. A project switches an "
                    "integration on from its own settings."
                ),
            },
        )
    if not workspaceId:
        raise HTTPException(status_code=422, detail="workspaceId is required")

    try:
        _uuid.UUID(workspaceId)
    except ValueError:
        raise HTTPException(status_code=422, detail="workspaceId must be a UUID")

    await db.execute(
        text(
            "INSERT INTO integration_grants "
            "  (tenant_id, kind, target_ref, workspace_id, granted_by) "
            "VALUES (CAST(:t AS uuid), :k, :r, CAST(:w AS uuid), :by) "
            # DO UPDATE rather than DO NOTHING so the row records who granted it most
            # recently. With the level gone there is nothing else to change, but a
            # re-grant is still a real act somebody performed.
            "ON CONFLICT (tenant_id, kind, target_ref, workspace_id) DO UPDATE "
            "  SET granted_by = EXCLUDED.granted_by"
        ),
        {
            "t": tenant_id, "k": kind, "r": id, "w": workspaceId,
            "by": getattr(request.state, "user_id", None),
        },
    )
    await db.flush()
    logger.info("integration granted: %s %s -> unit %s", kind, id, workspaceId)
    return {"ok": True, "changed": True}


@integration_access_router.delete(
    "/integrations/access",
    dependencies=[Depends(require_permission("connector:manage"))],
)
async def revoke_integration_access(
    request: Request,
    kind: str,
    id: str,
    level: str,
    workspaceId: Optional[str] = None,
    projectId: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Take access away, at one of two levels.

      level=unit     the unit loses the integration entirely. Organization Admin
                     only — a unit admin who could revoke their own grant could also
                     restore it, which makes the grant theirs.
      level=project  the project stops using it, the unit keeps the grant. Either
                     admin tier, bounded to units they administer.

    Both are idempotent: revoking what is already gone returns ok with changed=false,
    because the caller's intent is satisfied either way and a 404 here reads as
    "wrong id".
    """
    tenant_id = _tenant_id(request)
    if kind not in ("connector", "mcp", "model_provider"):
        raise HTTPException(status_code=422, detail="kind must be 'connector', 'mcp', or 'model_provider'")

    if level == "unit":
        _require_org_admin(request)
        if not workspaceId:
            raise HTTPException(status_code=422, detail="workspaceId is required")
        result = await db.execute(
            text(
                "DELETE FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
                "  AND kind = :k AND target_ref = :r AND workspace_id = CAST(:w AS uuid)"
            ),
            {"t": tenant_id, "k": kind, "r": id, "w": workspaceId},
        )
        await db.flush()
        return {"ok": True, "changed": bool(result.rowcount)}

    if level == "project":
        if not projectId:
            raise HTTPException(status_code=422, detail="projectId is required")
        project = (
            await db.execute(
                text(
                    "SELECT id, workspace_id, connectors, mcp_servers FROM projects "
                    "WHERE id = CAST(:p AS uuid) AND tenant_id = CAST(:t AS uuid)"
                ),
                {"p": projectId, "t": tenant_id},
            )
        ).first()
        if project is None:
            raise HTTPException(status_code=404, detail="not found")

        # Bounded to the units they administer — a BU Admin must not reach into a
        # sibling unit's project.
        administered = await administered_workspace_ids(db, request)
        if administered is not None and str(project.workspace_id) not in administered:
            raise HTTPException(status_code=404, detail="not found")

        column = "connectors" if kind == "connector" else "mcp_servers"
        wired = (project.connectors if kind == "connector" else project.mcp_servers) or {}
        changed = False
        if isinstance(wired, dict):
            updated = {
                st: [x for x in (ids or []) if x != id] for st, ids in wired.items()
            }
            changed = updated != wired
        elif isinstance(wired, list):
            updated = [x for x in wired if x != id]
            changed = updated != wired
        else:
            updated = wired

        if changed:
            import json  # noqa: PLC0415

            await db.execute(
                text(
                    f"UPDATE projects SET {column} = CAST(:v AS jsonb), updated_at = now() "
                    "WHERE id = CAST(:p AS uuid)"
                ),
                {"v": json.dumps(updated), "p": projectId},
            )
            await db.flush()
        return {"ok": True, "changed": changed}

    raise HTTPException(status_code=422, detail="level must be 'unit' or 'project'")


@integration_access_router.get("/connectors/grants")
async def list_connector_grants(
    request: Request,
    workspaceId: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Which connector kinds are permitted, as {kind, businessUnitIds[]}.

    Kept as grants rather than bare kinds so the UI can distinguish "every unit has
    this" from "you were given this". A bounded viewer sees the union across their
    own units with the unit lists intact for those units only — they should not learn
    which OTHER units a grant reaches.
    """
    tenant_id = _tenant_id(request)
    allowed = await allowed_workspace_ids(db, request)

    rows = (
        await db.execute(
            text(
                "SELECT target_ref, workspace_id FROM integration_grants "
                "WHERE tenant_id = CAST(:t AS uuid) AND kind = 'connector'"
            ),
            {"t": tenant_id},
        )
    ).fetchall()

    by_kind: dict[str, list[str]] = {}
    for target, ws in rows:
        ws = str(ws)
        if allowed is not None and ws not in allowed:
            continue
        if workspaceId and ws != workspaceId:
            continue
        by_kind.setdefault(target, []).append(ws)

    return [{"kind": k, "businessUnitIds": sorted(v)} for k, v in sorted(by_kind.items())]


@integration_access_router.put(
    "/connectors/grants",
    dependencies=[Depends(require_permission("connector:manage"))],
)
async def set_connector_grants(
    request: Request,
    body: dict,
    workspaceId: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Replace the grant policy — for one unit, or wholesale.

    With `workspaceId`, `kinds` is the complete set that unit may use. Without it,
    `grants` is the whole policy: every {kind, businessUnitIds} pair. Both are
    Organization Admin only.
    """
    tenant_id = _tenant_id(request)
    _require_org_admin(request)
    actor = getattr(request.state, "user_id", None)

    # NO LEVELS TO PRESERVE ANY MORE. This used to read every existing grant's
    # access level before the DELETE and reuse it on re-insert, because the replace is
    # DELETE-then-INSERT and a survivor would otherwise have come back at the default
    # — silently stripping write from every unit that had it. Migration 0024 removed
    # the level from the grant entirely, so a grant row is now just its own existence
    # and the replace has nothing to lose.
    if workspaceId:
        # Filtered against the presented catalog, matching the list read above —
        # the grantable universe and the listed one must be the same set.
        kinds = [k for k in (body.get("kinds") or []) if k in _CATALOG_KINDS]
        await db.execute(
            text(
                "DELETE FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
                "  AND kind = 'connector' AND workspace_id = CAST(:w AS uuid)"
            ),
            {"t": tenant_id, "w": workspaceId},
        )
        for k in kinds:
            await db.execute(
                text(
                    "INSERT INTO integration_grants "
                    "  (tenant_id, kind, target_ref, workspace_id, granted_by) "
                    "VALUES (CAST(:t AS uuid), 'connector', :r, CAST(:w AS uuid), :by)"
                ),
                {"t": tenant_id, "r": k, "w": workspaceId, "by": actor},
            )
    else:
        await db.execute(
            text(
                "DELETE FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
                "  AND kind = 'connector'"
            ),
            {"t": tenant_id},
        )
        for grant in body.get("grants") or []:
            kind = grant.get("kind")
            if kind not in _CATALOG_KINDS:
                continue
            for ws in grant.get("businessUnitIds") or []:
                await db.execute(
                    text(
                        "INSERT INTO integration_grants "
                        "  (tenant_id, kind, target_ref, workspace_id, granted_by) "
                        "VALUES (CAST(:t AS uuid), 'connector', :r, CAST(:w AS uuid), :by) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"t": tenant_id, "r": kind, "w": ws, "by": actor},
                )

    await db.flush()
    return await list_connector_grants(request, db=db)
