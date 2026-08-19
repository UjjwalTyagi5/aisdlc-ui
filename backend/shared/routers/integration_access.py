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

from shared.authz.connector_access import ACCESS_LEVELS, DEFAULT_ACCESS, is_access_level, narrow
from shared.authz.connector_capabilities import unsupported_reason, warnings_for
from shared.authz.dependency import require_permission
from shared.authz.read_scope import administered_workspace_ids, allowed_workspace_ids, is_org_wide
from shared.db import get_db_session
from shared.routers.connectors import _KNOWN_KINDS

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

    servers = (
        await db.execute(
            text(
                "SELECT id, server_name, description FROM mcp_servers "
                "WHERE tenant_id = CAST(:t AS uuid) ORDER BY server_name"
            ),
            {"t": tenant_id},
        )
    ).fetchall()

    grants: dict[tuple[str, str], set[str]] = {}
    # The LEVEL each unit holds, keyed the same way. Carried alongside rather than
    # folded into `grants` because callers ask two different questions of this data —
    # "which units hold it" drives the list, "at what level" drives each row's control
    # — and a set of ids answers the first without complicating it for the second.
    grant_levels: dict[tuple[str, str, str], str] = {}
    for kind, target, ws, access in (
        await db.execute(
            text(
                "SELECT kind, target_ref, workspace_id, access FROM integration_grants "
                "WHERE tenant_id = CAST(:t AS uuid)"
            ),
            {"t": tenant_id},
        )
    ).fetchall():
        grants.setdefault((kind, target), set()).add(str(ws))
        grant_levels[(kind, target, str(ws))] = access

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

    def _row(kind: str, target: str, name: str, description: Optional[str], onboarded: bool):
        granted = grants.get((kind, target), set())
        usage = _usage(kind, target)
        entries = [
            {
                "id": uid,
                "name": uname,
                "via": "granted" if uid in granted else "none",
                # None for a unit that holds nothing — there is no level without a
                # grant, and reporting a default here would show the picker a value
                # the database does not have.
                "access": grant_levels.get((kind, target, uid)),
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
            "units": entries,
            # Counts what the VIEWER can see, so a Business Unit Admin's "1 unit"
            # is their own rather than a number they cannot account for.
            "grantedUnitCount": len(granted & unit_ids),
            "projectCount": sum(len(v) for k, v in usage.items() if k in unit_ids),
        }

    out = [
        _row("connector", k, _CONNECTOR_LABEL.get(k, k), None, k in installed_kinds)
        for k in sorted(_KNOWN_KINDS)
    ]
    out += [
        _row("mcp", str(s.id), s.server_name, s.description, True) for s in servers
    ]
    return out


# The three routes below GRANT and REVOKE integration access across business units.
# The router-level dependency is `connector:view` — a READ permission — which stays for
# the reads. A write announcing itself as a read is how the next reader concludes these
# are safe. `_require_org_admin()` in each body remains the operative check.
async def _reconcile_project_overrides(
    db: AsyncSession,
    tenant_id: str,
    kind: str,
    target_ref: str,
    workspace_id: str,
    unit_access: str,
) -> int:
    """Re-narrow every project override in this unit against the unit's new level.

    THE CASE THIS EXISTS FOR: a unit granted read_write, a project narrowed to write,
    and then the Org Admin drops the unit to read. `narrow(read, write)` is empty —
    those two share no operation — so the project must end with no access rather than
    keeping a write the organisation just withdrew. An override left untouched would
    do exactly that, because the runtime reads the override as the project's answer.

    Overrides that still fit are left alone: a project deliberately held at read under
    a read_write unit stays at read when the unit changes to read. Rows whose
    intersection is empty are DELETED rather than written as some third value, since
    "no access" is the absence of a grant, not a level.
    """
    rows = (
        await db.execute(
            text(
                "SELECT p.id AS project_id, a.access FROM project_connector_access a "
                "  JOIN projects p ON p.id = a.project_id "
                " WHERE a.tenant_id = CAST(:t AS uuid) AND a.kind = :k "
                "   AND a.target_ref = :r AND p.workspace_id = CAST(:w AS uuid)"
            ),
            {"t": tenant_id, "k": kind, "r": target_ref, "w": workspace_id},
        )
    ).fetchall()

    changed = 0
    for row in rows:
        still = narrow(unit_access, row.access)
        if still == row.access:
            continue
        if still is None:
            await db.execute(
                text(
                    "DELETE FROM project_connector_access "
                    "WHERE tenant_id = CAST(:t AS uuid) AND project_id = :p "
                    "  AND kind = :k AND target_ref = :r"
                ),
                {"t": tenant_id, "p": row.project_id, "k": kind, "r": target_ref},
            )
        else:
            await db.execute(
                text(
                    "UPDATE project_connector_access SET access = :a "
                    "WHERE tenant_id = CAST(:t AS uuid) AND project_id = :p "
                    "  AND kind = :k AND target_ref = :r"
                ),
                {"t": tenant_id, "p": row.project_id, "k": kind, "r": target_ref, "a": still},
            )
        changed += 1

    if changed:
        logger.info(
            "reconciled %d project override(s) for %s %s in unit %s -> %s",
            changed, kind, target_ref, workspace_id, unit_access,
        )
    return changed


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
    access: str = DEFAULT_ACCESS,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Give a Business Unit an integration, at a stated access level.

    BUSINESS UNIT LEVEL ONLY. Granting is how far the organisation's reach decision
    goes; whether one of that unit's projects switches the integration on is the
    project's own wiring, done on its Settings. Revoking DOES reach a project — see
    the DELETE below — because taking something away has to be able to stop one team.
    """
    tenant_id = _tenant_id(request)
    _require_org_admin(request)
    if kind not in ("connector", "mcp"):
        raise HTTPException(status_code=422, detail="kind must be 'connector' or 'mcp'")
    # Rejected loudly rather than coerced to the default: a caller who sent a level
    # meant something by it, and quietly substituting `read` would hand them a
    # narrower grant than they think they made.
    if not is_access_level(access):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "bad_access_level",
                "message": f"access must be one of {', '.join(ACCESS_LEVELS)}.",
            },
        )
    # A LEVEL THE CONNECTOR CANNOT HONOUR IS REFUSED, not stored. Slack declares no
    # read capabilities, so `read` — our least-privilege default — would grant a
    # connector that can do nothing, shown on the hub as a healthy grant. Refusing
    # only ever happens on POSITIVE knowledge: a connector that cannot be introspected
    # returns None and is granted whatever was asked for.
    if kind == "connector":
        reason = unsupported_reason(id, access)
        if reason:
            raise HTTPException(
                status_code=422,
                detail={"code": "unsupported_access_level", "message": reason},
            )
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
            "  (tenant_id, kind, target_ref, workspace_id, granted_by, access) "
            "VALUES (CAST(:t AS uuid), :k, :r, CAST(:w AS uuid), :by, :a) "
            # UPDATE, not DO NOTHING: re-granting at a different level is how an Org
            # Admin changes their mind, and DO NOTHING made that a silent no-op that
            # looked like it had worked.
            "ON CONFLICT (tenant_id, kind, target_ref, workspace_id) DO UPDATE "
            "  SET access = EXCLUDED.access, granted_by = EXCLUDED.granted_by"
        ),
        {
            "t": tenant_id, "k": kind, "r": id, "w": workspaceId,
            "by": getattr(request.state, "user_id", None), "a": access,
        },
    )
    # NARROWING THE UNIT NARROWS ITS PROJECTS. A project override that no longer
    # shares an operation with the unit's new level would otherwise sit in the table
    # granting something the organisation has just withdrawn. They are recomputed
    # rather than deleted, so a project deliberately held at read under a read_write
    # unit stays at read.
    await _reconcile_project_overrides(db, tenant_id, kind, id, workspaceId, access)
    await db.flush()
    logger.info(
        "integration granted: %s %s -> unit %s (%s)", kind, id, workspaceId, access
    )
    return {
        "ok": True,
        "changed": True,
        "access": access,
        # Permitted but partly hollow — see `warnings_for`. Returned rather than
        # logged so the admin who chose the level is the one who reads it.
        "warnings": warnings_for(id, access) if kind == "connector" else [],
    }


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
    if kind not in ("connector", "mcp"):
        raise HTTPException(status_code=422, detail="kind must be 'connector' or 'mcp'")

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

    if workspaceId:
        kinds = [k for k in (body.get("kinds") or []) if k in _KNOWN_KINDS]
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
            if kind not in _KNOWN_KINDS:
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
