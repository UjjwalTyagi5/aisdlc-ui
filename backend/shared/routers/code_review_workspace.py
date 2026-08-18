"""Code-Review workspace endpoints — open-PR listing, review-target preparation
(clone read-only + compute the diff), and persisted-review listing.

Routes (mounted under '/code-review'):
  GET  /code-review/{project_id}/ado/repos/{ado_project}/{repo}/prs
  POST /code-review/{project_id}/review/prepare
  GET  /code-review/{project_id}/reviews
  GET  /code-review/{project_id}/reviews/{run_id}

The ADO project/repo/branch cascade is reused from the dev-workspace endpoints
(`/dev/{project_id}/ado/...`) — same generic ADO picker. Credentials resolve from
the Azure DevOps connector (Integrations) per tenant, falling back to env.
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.project_scope import require_project_access
from agents_orchestrator.code_review_agent.config.session_state import set_prepared
from shared.db import get_db_session
from shared.models.orm import Run
from shared.services import ado_repos

# EVERY ROUTE HERE IS SCOPED TO ITS {project_id}. Until 2026-08-17 these handlers
# took the project id from the path and filtered on tenant_id alone, so the only gate
# was the artifact:view floor applied at include time — a permission `contributor`
# holds. The router-level dependency covers all of them at once, and covers whatever
# route is added next. See docs/rbac-audit-2026-08-17.md finding 3.
code_review_workspace_router = APIRouter(dependencies=[Depends(require_project_access())])


@code_review_workspace_router.get("/{project_id}/ado/repos/{ado_project}/{repo}/prs")
async def list_open_prs(
    project_id: str, ado_project: str, repo: str, request: Request
) -> list[dict]:
    return await ado_repos.list_pull_requests(
        ado_project, repo, status="active", tenant_id=request.state.tenant_id
    )


class PrepareRequest(BaseModel):
    mode: str                       # "branch" | "pr"
    ado_project: str
    repo_name: str
    source_branch: str | None = None
    base_branch: str | None = None
    pr_id: str | None = None


@code_review_workspace_router.post("/{project_id}/review/prepare")
async def prepare_review(project_id: str, body: PrepareRequest, request: Request) -> dict:
    """Clone the repo read-only and compute the diff for the selected target, binding
    it to the chat session so the agent can review it. Returns the diff + context."""
    tenant_id: str = request.state.tenant_id
    org_url, pat = await ado_repos.resolve_auth(tenant_id)

    source = (body.source_branch or "").strip()
    base = (body.base_branch or "").strip()
    pr_title = ""
    if body.mode == "pr":
        if not body.pr_id:
            raise HTTPException(status_code=400, detail="pr_id required for mode=pr")
        pr = await ado_repos.get_pull_request(
            body.ado_project, body.repo_name, body.pr_id, pat=pat, org_url=org_url
        )
        if not pr:
            raise HTTPException(status_code=404, detail="PR not found")
        source, base, pr_title = pr["source_branch"], pr["target_branch"], pr["title"]
    if not source or not base:
        raise HTTPException(status_code=400, detail="source_branch and base_branch are required")
    if source == base:
        raise HTTPException(status_code=400, detail="source and base branch must differ")

    remote_url = await ado_repos.resolve_clone_url(
        body.ado_project, body.repo_name, pat=pat, org_url=org_url
    )
    if remote_url is None:
        raise HTTPException(
            status_code=404,
            detail=f"Repo '{body.repo_name}' not found in ADO project '{body.ado_project}'",
        )

    work_dir = str(ado_repos.WORKSPACE_ROOT / tenant_id / project_id / "review")
    try:
        result = await asyncio.to_thread(
            ado_repos.clone_and_diff, work_dir, remote_url, source, base, pat
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"Could not prepare diff: {exc}")

    set_prepared(tenant_id, project_id, {
        "work_dir": work_dir,
        "repo_url": remote_url,
        "pat": pat,
        "mode": body.mode,
        "ado_project": body.ado_project,
        "repo_name": body.repo_name,
        "source_branch": source,
        "base_branch": base,
        "pr_id": body.pr_id or "",
        "pr_title": pr_title,
        "head_sha": result["head_sha"],
        "base_sha": result["base_sha"],
        "diff_text": result["diff"],
        "changed_files": result["files"],
    })

    return {
        "status": "ready",
        "mode": body.mode,
        "repo_name": body.repo_name,
        "ado_project": body.ado_project,
        "source_branch": source,
        "base_branch": base,
        "pr_id": body.pr_id,
        "pr_title": pr_title,
        "head_sha": result["head_sha"],
        "base_sha": result["base_sha"],
        "files": result["files"],
        "diff": result["diff"],
        "truncated": result["truncated"],
    }


def _review_summary_row(run: Run) -> dict:
    art = run.code_review_artifacts or {}
    ctx = art.get("context") or {}
    findings = art.get("findings") or []
    crit = sum(1 for f in findings if f.get("severity") in ("critical", "high"))
    label = (
        f"PR #{ctx.get('pr_id')}" if ctx.get("mode") == "pr" and ctx.get("pr_id")
        else f"{ctx.get('source_branch', '')} → {ctx.get('base_branch', '')}"
    )
    return {
        "id": str(run.id),
        "label": label,
        "repo_name": ctx.get("repo_name", ""),
        "merge_recommendation": art.get("merge_recommendation", "needs_discussion"),
        "findings_count": len(findings),
        "critical_high": crit,
        "created_at": run.created_at.isoformat(),
    }


@code_review_workspace_router.get("/{project_id}/reviews")
async def list_reviews(
    project_id: str, request: Request, db: AsyncSession = Depends(get_db_session)
) -> list[dict]:
    tenant_id: str = request.state.tenant_id
    stmt = (
        select(Run)
        .where(
            Run.project_id == uuid.UUID(project_id),
            Run.tenant_id == uuid.UUID(tenant_id),
            Run.code_review_artifacts.isnot(None),
        )
        .order_by(Run.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_review_summary_row(r) for r in rows]


@code_review_workspace_router.get("/{project_id}/reviews/{run_id}")
async def get_review(
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
    if run is None or not run.code_review_artifacts:
        raise HTTPException(status_code=404, detail="Review not found")
    return {"id": str(run.id), "created_at": run.created_at.isoformat(), **run.code_review_artifacts}
