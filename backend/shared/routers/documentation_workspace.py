"""Documentation workspace endpoints — repo connector listing, target preparation
(clone read-only + detect languages + summarize existing platform artifacts), and
open-PR listing.

Routes (mounted under '/documentation'):
  GET  /documentation/{project_id}/connectors
  GET  /documentation/{project_id}/ado/repos/{ado_project}/{repo}/prs
  POST /documentation/{project_id}/prepare

The ADO project/repo/branch cascade is reused from the dev-workspace endpoints.
"""
from __future__ import annotations

import asyncio
import os
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agents_orchestrator.documentation_agent.config.session_state import set_prepared
from shared.services import ado_repos

documentation_workspace_router = APIRouter()

_LANG_EXT = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
    ".jsx": "JavaScript", ".cs": "C#", ".go": "Go", ".java": "Java", ".rb": "Ruby",
    ".rs": "Rust", ".php": "PHP", ".kt": "Kotlin",
}
_SKIP = {".git", "node_modules", "__pycache__", ".venv", "bin", "obj", "dist", "build", ".next"}


def _detect_languages(work_dir: str) -> list[str]:
    counts: dict[str, int] = {}
    for dirpath, dirs, files in os.walk(work_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            lang = _LANG_EXT.get(ext)
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
    return [lang for lang, _ in sorted(counts.items(), key=lambda kv: -kv[1])][:6]


async def _upstream_summary(tenant_id: str, project_id: str) -> str:
    """One-line note on which platform artifacts already exist for this project."""
    cols = {
        "requirements": "requirements_payload", "design": "design_artifacts",
        "development": "development_artifacts", "testing": "testing_artifacts",
        "code_review": "code_review_artifacts", "security": "security_artifacts",
    }
    present: list[str] = []
    try:
        from sqlalchemy import select
        from shared.db import get_db_session_for_tenant
        from shared.models.orm import Run
        async with get_db_session_for_tenant(tenant_id) as db:
            for key, col in cols.items():
                row = (
                    await db.execute(
                        select(getattr(Run, col))
                        .where(Run.project_id == uuid.UUID(project_id), getattr(Run, col).isnot(None))
                        .limit(1)
                    )
                ).scalars().first()
                if row:
                    present.append(key)
    except Exception:
        return ""
    return ", ".join(present) if present else "none found"


@documentation_workspace_router.get("/{project_id}/connectors")
async def list_doc_connectors(project_id: str, request: Request) -> dict:
    """Where this tenant can file documentation (best-effort).

    NOTE: the frontend's listDocConnectors is currently unreferenced (unlike
    listDeployConnectors, which the deploy-target dialog uses), so the SharePoint entry
    has no UI effect today. It is here for symmetry with the deployment endpoint and so
    the data exists when a doc-target picker is built.
    """
    tenant_id: str = request.state.tenant_id
    azure = False
    try:
        org_url, pat = await ado_repos.resolve_auth(tenant_id)
        azure = bool(org_url and pat)
    except Exception:
        azure = False

    sharepoint = False
    try:
        from shared.services.notification_targets import sharepoint_target

        sharepoint = bool(await sharepoint_target(tenant_id))
    except Exception:
        sharepoint = False

    return {
        "connectors": [
            {"kind": "azure_repos", "label": "Azure Repos", "available": azure},
            {"kind": "sharepoint", "label": "SharePoint", "available": sharepoint},
        ]
    }


@documentation_workspace_router.get("/{project_id}/ado/repos/{ado_project}/{repo}/prs")
async def list_open_prs(project_id: str, ado_project: str, repo: str, request: Request) -> list[dict]:
    return await ado_repos.list_pull_requests(
        ado_project, repo, status="active", tenant_id=request.state.tenant_id
    )


class PrepareDocRequest(BaseModel):
    mode: str = "branch"             # "branch" | "pr"
    ado_project: str
    repo_name: str
    branch: str | None = None
    pr_id: str | None = None


@documentation_workspace_router.post("/{project_id}/prepare")
async def prepare_docs(project_id: str, body: PrepareDocRequest, request: Request) -> dict:
    """Clone the branch (or PR source) read-only and detect languages + upstream artifacts."""
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
        raise HTTPException(status_code=404, detail=f"Repo '{body.repo_name}' not found")

    work_dir = str(ado_repos.WORKSPACE_ROOT / tenant_id / project_id / "documentation")
    try:
        result = await asyncio.to_thread(ado_repos.clone_into, work_dir, remote_url, branch, pat)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"Could not clone the branch: {exc}")

    languages = await asyncio.to_thread(_detect_languages, work_dir)
    upstream_summary = await _upstream_summary(tenant_id, project_id)

    set_prepared(tenant_id, project_id, {
        "work_dir": work_dir, "repo_url": remote_url, "pat": pat,
        "mode": body.mode, "ado_project": body.ado_project, "repo_name": body.repo_name,
        "source_branch": branch, "pr_id": body.pr_id or "", "head_sha": result.get("commit_sha", ""),
        "languages": languages, "upstream_summary": upstream_summary,
    })

    return {
        "status": "ready", "mode": body.mode, "repo_name": body.repo_name,
        "ado_project": body.ado_project, "branch": branch, "pr_id": body.pr_id,
        "pr_title": pr_title, "head_sha": result.get("commit_sha", ""),
        "languages": languages, "upstream_summary": upstream_summary,
    }
