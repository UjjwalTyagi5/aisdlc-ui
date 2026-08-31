"""Dev-workspace cascade endpoints — ADO project / repo / branch picker,
persistent workspace management, and PR listing for the Development agent page.

Routes (mounted under the '/dev' prefix in process_api):
  GET  /dev/{project_id}/ado/projects
  GET  /dev/{project_id}/ado/projects/{ado_project}/repos
  GET  /dev/{project_id}/ado/repos/{ado_project}/{repo}/branches
  POST /dev/{project_id}/workspace/pull
  GET  /dev/{project_id}/workspace
  GET  /dev/{project_id}/prs

{project_id} is the platform project UUID.
Credentials resolve from the Azure DevOps connector (Integrations page) per tenant,
resolved per tenant (secret store "ado-org-url" / "ado-pat"); no env fallback.
All routes are gated by the artifact:view floor applied at include_router() time.
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.project_scope import require_project_access
from shared.authz.agent_access import require_agent_access
from shared.db import get_db_session
from shared.models.orm import Run
from shared.services import ado_repos
from shared.services import dev_workspace_store
from shared.services import workspace_fs

# EVERY ROUTE HERE IS SCOPED TO ITS {project_id}. Until 2026-08-17 these handlers
# took the project id from the path and filtered on tenant_id alone, so the only gate
# was the artifact:view floor applied at include time — a permission `contributor`
# holds. The router-level dependency covers all of them at once, and covers whatever
# route is added next. See docs/rbac-audit-2026-08-17.md finding 3.
dev_workspace_router = APIRouter(
    dependencies=[
        Depends(require_project_access()),
        Depends(require_agent_access("development")),
    ]
)


class PullRequest(BaseModel):
    ado_project: str
    repo_name: str
    branch: str


@dev_workspace_router.get("/{project_id}/ado/projects")
async def get_ado_projects(project_id: str, request: Request) -> list[dict]:
    return await ado_repos.list_projects(tenant_id=request.state.tenant_id)


@dev_workspace_router.get("/{project_id}/ado/projects/{ado_project}/repos")
async def get_ado_repos(project_id: str, ado_project: str, request: Request) -> list[dict]:
    return await ado_repos.list_repos(ado_project, tenant_id=request.state.tenant_id)


@dev_workspace_router.get("/{project_id}/ado/repos/{ado_project}/{repo}/branches")
async def get_ado_branches(
    project_id: str, ado_project: str, repo: str, request: Request
) -> list[dict]:
    return await ado_repos.list_branches(
        ado_project, repo, tenant_id=request.state.tenant_id
    )


@dev_workspace_router.post("/{project_id}/workspace/pull")
async def pull_workspace(project_id: str, body: PullRequest, request: Request) -> dict:
    tenant_id: str = request.state.tenant_id
    pulled_by = str(uid) if (uid := getattr(request.state, "user_id", None)) else None

    org_url, pat = await ado_repos.resolve_auth(tenant_id)
    remote_url = await ado_repos.resolve_clone_url(
        body.ado_project, body.repo_name, pat=pat, org_url=org_url
    )
    if remote_url is None:
        return {
            "status": "error",
            "error": f"Repo '{body.repo_name}' not found in ADO project '{body.ado_project}'",
        }

    work_dir = str(
        ado_repos.WORKSPACE_ROOT / tenant_id / project_id / "repo"
    )

    # Every upsert carries the full NOT-NULL field set: the atomic
    # INSERT ... ON CONFLICT validates NOT NULL on the proposed row before the
    # conflict redirects to UPDATE, so a partial dict would fail on re-pull.
    base_fields = {
        "ado_project": body.ado_project,
        "repo_name": body.repo_name,
        "branch": body.branch,
        "remote_url": remote_url,
        "work_dir": work_dir,
        "pulled_by": pulled_by,
    }

    workspace = await dev_workspace_store.upsert(
        tenant_id, project_id, {**base_fields, "status": "pulling"}
    )

    try:
        result = await asyncio.to_thread(
            ado_repos.clone_into, work_dir, remote_url, body.branch, pat
        )
        workspace = await dev_workspace_store.upsert(
            tenant_id,
            project_id,
            {**base_fields, "status": "ready", "commit_sha": result["commit_sha"]},
        )
    except RuntimeError as exc:
        workspace = await dev_workspace_store.upsert(
            tenant_id, project_id, {**base_fields, "status": "error"}
        )
        workspace = {**workspace, "error": str(exc)}

    return workspace


@dev_workspace_router.get("/{project_id}/workspace")
async def get_workspace(project_id: str, request: Request) -> dict | None:
    tenant_id: str = request.state.tenant_id
    return await dev_workspace_store.get_for_project(tenant_id, project_id)


@dev_workspace_router.get("/{project_id}/workspace/tree")
async def get_workspace_tree(project_id: str, request: Request) -> dict:
    """Flat, sorted list of repo-relative file paths for the pulled workspace.

    Returns {ready: false, paths: []} when no ready workspace exists, so the UI
    can render an empty/guard state without erroring.
    """
    tenant_id: str = request.state.tenant_id
    ws = await dev_workspace_store.get_for_project(tenant_id, project_id)
    if not ws or ws.get("status") != "ready":
        return {"ready": False, "paths": [], "truncated": False}

    tree = await asyncio.to_thread(workspace_fs.list_tree, ws["work_dir"])
    return {
        "ready": True,
        "repo_name": ws.get("repo_name"),
        "branch": ws.get("branch"),
        **tree,
    }


@dev_workspace_router.get("/{project_id}/workspace/file")
async def get_workspace_file(
    project_id: str, request: Request, path: str = Query(..., min_length=1)
) -> dict:
    """Read one repo-relative file from the pulled workspace (traversal-guarded)."""
    tenant_id: str = request.state.tenant_id
    ws = await dev_workspace_store.get_for_project(tenant_id, project_id)
    if not ws or ws.get("status") != "ready":
        raise HTTPException(status_code=404, detail="No pulled workspace for this project")
    try:
        return await asyncio.to_thread(workspace_fs.read_file, ws["work_dir"], path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@dev_workspace_router.get("/{project_id}/workspace/changes")
async def get_workspace_changes(project_id: str, request: Request) -> dict:
    """Files the agent changed since the pulled commit (for explorer decorations)."""
    tenant_id: str = request.state.tenant_id
    ws = await dev_workspace_store.get_for_project(tenant_id, project_id)
    if not ws or ws.get("status") != "ready":
        return {"base": None, "files": []}
    return await asyncio.to_thread(
        workspace_fs.list_changes, ws["work_dir"], ws.get("commit_sha")
    )


@dev_workspace_router.get("/{project_id}/workspace/file/changed-lines")
async def get_workspace_file_changed_lines(
    project_id: str, request: Request, path: str = Query(..., min_length=1)
) -> dict:
    """New-file line numbers changed for *path* since the pulled commit."""
    tenant_id: str = request.state.tenant_id
    ws = await dev_workspace_store.get_for_project(tenant_id, project_id)
    if not ws or ws.get("status") != "ready":
        return {"added_lines": []}
    try:
        return await asyncio.to_thread(
            workspace_fs.changed_lines, ws["work_dir"], ws.get("commit_sha"), path
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _pr_status_bucket(dev_artifacts: dict) -> str:
    """Map a DevelopmentArtifacts dict to one of open|review|merged for the PR tabs.

    # NOTE: Live ADO PR state (merged/review) is a post-v1 enhancement; all PRs
    # are currently bucketed as 'open' until the ADO PR status API is wired in.
    """
    return "open"


@dev_workspace_router.get("/{project_id}/prs")
async def list_prs(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Return all PRs produced for a project, sourced from runs.development_artifacts.

    Tenant-scoped: filters by both project_id and tenant_id so cross-tenant reads
    are impossible. Only runs that have a non-empty pr_url in development_artifacts
    are included. Results are ordered by created_at DESC.
    """
    tenant_id: str = request.state.tenant_id

    stmt = (
        select(Run)
        .where(
            Run.project_id == uuid.UUID(project_id),
            Run.tenant_id == uuid.UUID(tenant_id),
            Run.development_artifacts.isnot(None),
            Run.development_artifacts["pr_url"].astext.isnot(None),
            Run.development_artifacts["pr_url"].astext != "",
        )
        .order_by(Run.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()

    return [
        {
            "id": str(run.id),
            "title": (
                run.development_artifacts.get("pr_title")
                or run.development_artifacts.get("branch_name")
                or "Pull request"
            ),
            "branch": run.development_artifacts.get("branch_name", ""),
            "status": _pr_status_bucket(run.development_artifacts),
            "url": run.development_artifacts.get("pr_url", ""),
            "created_at": run.created_at.isoformat(),
        }
        for run in rows
        if run.development_artifacts.get("pr_url")
    ]
