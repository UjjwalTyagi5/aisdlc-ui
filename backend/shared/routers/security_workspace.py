"""Security workspace endpoints — open-PR listing, scan-target preparation (clone
the branch read-only), and persisted-scan listing.

Routes (mounted under '/security'):
  GET  /security/{project_id}/ado/repos/{ado_project}/{repo}/prs
  POST /security/{project_id}/scan/prepare
  GET  /security/{project_id}/scans
  GET  /security/{project_id}/scans/{run_id}

The ADO project/repo/branch cascade is reused from the dev-workspace endpoints.
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.project_scope import require_project_access
from shared.authz.agent_access import require_agent_access
from agents_orchestrator.security_agent.config.session_state import set_prepared
from shared.db import get_db_session
from shared.models.orm import Run
from shared.services import ado_repos
from shared.services import prepared_targets

# EVERY ROUTE HERE IS SCOPED TO ITS {project_id}. Until 2026-08-17 these handlers
# took the project id from the path and filtered on tenant_id alone, so the only gate
# was the artifact:view floor applied at include time — a permission `contributor`
# holds. The router-level dependency covers all of them at once, and covers whatever
# route is added next. See docs/rbac-audit-2026-08-17.md finding 3.
security_workspace_router = APIRouter(
    dependencies=[
        Depends(require_project_access()),
        Depends(require_agent_access("security")),
    ]
)


@security_workspace_router.get("/{project_id}/ado/repos/{ado_project}/{repo}/prs")
async def list_open_prs(
    project_id: str, ado_project: str, repo: str, request: Request,
    provider: str | None = None,
) -> list[dict]:
    """Open pull requests, from whichever host this project's source lives on.

    THE PATH STILL SAYS `ado` and that is now only a name — this serves GitHub too.
    Before it, a GitHub project's PR picker returned an Azure DevOps error.
    """
    from shared.services import repo_source  # noqa: PLC0415

    # project_id + owner_id, like every dev-workspace picker route: the credential may
    # be a project-scoped personal one, which resolves to nothing on tenant_id alone.
    owner_id = str(uid) if (uid := getattr(request.state, "user_id", None)) else ""
    try:
        _chosen, prs = await repo_source.list_pull_requests(
            request.state.tenant_id, ado_project, repo, status="active",
            project_id=project_id, owner_id=owner_id, provider=provider,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return prs


class PrepareScanRequest(BaseModel):
    # Where the code lives. Absent means "the project's single configured source", so a
    # one-source project behaves exactly as it did before the picker existed.
    provider: str | None = None
    mode: str                       # "branch" | "pr"
    ado_project: str
    repo_name: str
    branch: str | None = None
    pr_id: str | None = None


@security_workspace_router.post("/{project_id}/scan/prepare")
async def prepare_scan(project_id: str, body: PrepareScanRequest, request: Request) -> dict:
    """Clone the branch (or PR's source branch) read-only and bind it to the chat
    session so the agent can scan it."""
    tenant_id: str = request.state.tenant_id
    # project_id + owner_id, matching dev_workspace.pull_workspace. Passing only the
    # tenant looks at tenant-wide connectors alone, so a project-scoped PERSONAL Azure
    # DevOps credential -- what the Integrations page lets a Project Admin save for
    # just their own project -- was never found here, and this route 500'd while the
    # repo/branch pickers in the very same dialog resolved it and worked.
    owner_id = str(uid) if (uid := getattr(request.state, "user_id", None)) else ""
    from shared.services import repo_source  # noqa: PLC0415

    # WHICH HOST, decided once and carried through every call below. Naming a provider
    # you hold no credential for is an error rather than a quiet fall-through to the
    # other one — scanning the wrong repository is worse than scanning none.
    try:
        chosen, _base, pat = await repo_source.resolve(
            tenant_id, project_id=project_id, owner_id=owner_id, provider=body.provider,
        )
    except RuntimeError as exc:
        # "Not configured" unhandled reached the browser as a bare 500, which reads as a
        # broken agent rather than as a connector nobody has set up. The resolver's own
        # sentence names the fix and the page that performs it.
        raise HTTPException(status_code=424, detail=str(exc)) from exc

    branch = (body.branch or "").strip()
    pr_title = ""
    if body.mode == "pr":
        if not body.pr_id:
            raise HTTPException(status_code=400, detail="pr_id required for mode=pr")
        try:
            _p, pr = await repo_source.get_pull_request(
                tenant_id, body.ado_project, body.repo_name, body.pr_id,
                project_id=project_id, owner_id=owner_id, provider=chosen,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=424, detail=str(exc)) from exc
        if not pr:
            raise HTTPException(status_code=404, detail="PR not found")
        branch, pr_title = pr["source_branch"], pr["title"]
    if not branch:
        raise HTTPException(status_code=400, detail="branch is required")

    work_dir = str(ado_repos.WORKSPACE_ROOT / tenant_id / project_id / "security")
    try:
        result = await repo_source.clone(
            tenant_id, body.ado_project, body.repo_name, branch, work_dir,
            project_id=project_id, owner_id=owner_id, provider=chosen,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"Could not clone the branch: {exc}")
    remote_url = result.get("remote_url", "")

    _prepared_record = {
        "work_dir": work_dir,
        "repo_url": remote_url,
        "provider": chosen,
        "pat": pat,
        "mode": body.mode,
        "ado_project": body.ado_project,
        "repo_name": body.repo_name,
        "branch": branch,
        "pr_id": body.pr_id or "",
        "pr_title": pr_title,
        "head_sha": result.get("commit_sha", ""),
    }
    set_prepared(tenant_id, project_id, _prepared_record)
    # AND ON DISK, so the record outlives this process — see prepared_targets
    # for what a restart used to do to a perfectly good checkout. Never the
    # credential: that is re-resolved as this person when the agent binds.
    prepared_targets.save(
        "security", tenant_id, project_id, {**_prepared_record}, owner_id=owner_id,
    )

    return {
        "status": "ready",
        "mode": body.mode,
        "repo_name": body.repo_name,
        "ado_project": body.ado_project,
        "branch": branch,
        "pr_id": body.pr_id,
        "pr_title": pr_title,
        "head_sha": result.get("commit_sha", ""),
    }


def _scan_summary_row(run: Run) -> dict:
    art = run.security_artifacts or {}
    ctx = art.get("context") or {}
    m = art.get("metrics") or {}
    label = (
        f"PR #{ctx.get('pr_id')}" if ctx.get("mode") == "pr" and ctx.get("pr_id")
        else ctx.get("branch", "")
    )
    return {
        "id": str(run.id),
        "label": label,
        "repo_name": ctx.get("repo_name", ""),
        "risk_score": art.get("risk_score", "none"),
        "signoff": (art.get("signoff") or {}).get("decision", "conditional"),
        "findings_count": m.get("total", len(art.get("findings") or [])),
        "critical": m.get("critical", 0),
        "created_at": run.created_at.isoformat(),
    }


@security_workspace_router.get("/{project_id}/scans")
async def list_scans(
    project_id: str, request: Request, db: AsyncSession = Depends(get_db_session)
) -> list[dict]:
    tenant_id: str = request.state.tenant_id
    stmt = (
        select(Run)
        .where(
            Run.project_id == uuid.UUID(project_id),
            Run.tenant_id == uuid.UUID(tenant_id),
            Run.security_artifacts.isnot(None),
        )
        .order_by(Run.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_scan_summary_row(r) for r in rows]


@security_workspace_router.get("/{project_id}/scans/{run_id}")
async def get_scan(
    project_id: str, run_id: str, request: Request, db: AsyncSession = Depends(get_db_session)
) -> dict:
    tenant_id: str = request.state.tenant_id
    run = (
        await db.execute(
            select(Run).where(
                Run.id == uuid.UUID(run_id),
                Run.project_id == uuid.UUID(project_id),
                Run.tenant_id == uuid.UUID(tenant_id),
            )
        )
    ).scalar_one_or_none()
    if run is None or not run.security_artifacts:
        raise HTTPException(status_code=404, detail="Scan not found")
    return {"id": str(run.id), "created_at": run.created_at.isoformat(), **run.security_artifacts}
