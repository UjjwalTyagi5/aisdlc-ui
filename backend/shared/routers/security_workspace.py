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

from agents_orchestrator.security_agent.config.session_state import set_prepared
from shared.db import get_db_session
from shared.models.orm import Run
from shared.services import ado_repos

security_workspace_router = APIRouter()


@security_workspace_router.get("/{project_id}/ado/repos/{ado_project}/{repo}/prs")
async def list_open_prs(
    project_id: str, ado_project: str, repo: str, request: Request
) -> list[dict]:
    return await ado_repos.list_pull_requests(
        ado_project, repo, status="active", tenant_id=request.state.tenant_id
    )


class PrepareScanRequest(BaseModel):
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
    org_url, pat = await ado_repos.resolve_auth(tenant_id)

    branch = (body.branch or "").strip()
    pr_title = ""
    if body.mode == "pr":
        if not body.pr_id:
            raise HTTPException(status_code=400, detail="pr_id required for mode=pr")
        pr = await ado_repos.get_pull_request(
            body.ado_project, body.repo_name, body.pr_id, pat=pat, org_url=org_url
        )
        if not pr:
            raise HTTPException(status_code=404, detail="PR not found")
        branch, pr_title = pr["source_branch"], pr["title"]
    if not branch:
        raise HTTPException(status_code=400, detail="branch is required")

    remote_url = await ado_repos.resolve_clone_url(
        body.ado_project, body.repo_name, pat=pat, org_url=org_url
    )
    if remote_url is None:
        raise HTTPException(
            status_code=404,
            detail=f"Repo '{body.repo_name}' not found in ADO project '{body.ado_project}'",
        )

    work_dir = str(ado_repos.WORKSPACE_ROOT / tenant_id / project_id / "security")
    try:
        result = await asyncio.to_thread(ado_repos.clone_into, work_dir, remote_url, branch, pat)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"Could not clone the branch: {exc}")

    set_prepared(tenant_id, project_id, {
        "work_dir": work_dir,
        "repo_url": remote_url,
        "pat": pat,
        "mode": body.mode,
        "ado_project": body.ado_project,
        "repo_name": body.repo_name,
        "branch": branch,
        "pr_id": body.pr_id or "",
        "pr_title": pr_title,
        "head_sha": result.get("commit_sha", ""),
    })

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
