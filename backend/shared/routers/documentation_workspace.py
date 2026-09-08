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

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agents_orchestrator.documentation_agent.config.session_state import set_prepared
from shared.authz.project_scope import require_project_access
from shared.services import ado_repos

# EVERY ROUTE HERE IS SCOPED TO ITS {project_id}. Was bare APIRouter() — the only
# gate was the artifact:view floor applied at include time (process_api.py), which
# contributor holds; a contributor could clone/prepare/read connector state against
# ANY project id in the tenant. Same finding, same fix, as
# shared/routers/security_workspace.py — see docs/rbac-audit-2026-08-17.md finding 3,
# which lists this file as fixed alongside dev/code_review/security_workspace.py but
# the dependency was never actually attached here (deployment_workspace.py had the
# identical gap — see that file's own note).
documentation_workspace_router = APIRouter(dependencies=[Depends(require_project_access())])

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
    # ASKED PER PROVIDER, not hard-coded to one. `repo_source.available` checks each
    # backend for a usable credential, scoped to this project and caller — the same
    # project-scoped lookup that is the ONLY place this platform stores a Project
    # Admin's source credential.
    from shared.services import repo_source  # noqa: PLC0415

    try:
        sources = await repo_source.available(
            tenant_id,
            project_id=project_id,
            owner_id=getattr(request.state, "user_id", "") or "",
        )
    except Exception:  # noqa: BLE001
        sources = []
    azure = "ado" in sources
    github = "github" in sources

    sharepoint = False
    try:
        from shared.services.notification_targets import sharepoint_target

        sharepoint = bool(await sharepoint_target(tenant_id))
    except Exception:
        sharepoint = False

    return {
        "connectors": [
            {"kind": "azure_repos", "label": "Azure Repos", "available": azure},
            {"kind": "github", "label": "GitHub", "available": github},
            {"kind": "sharepoint", "label": "SharePoint", "available": sharepoint},
        ]
    }


@documentation_workspace_router.get("/{project_id}/ado/repos/{ado_project}/{repo}/prs")
async def list_open_prs(
    project_id: str, ado_project: str, repo: str, request: Request,
    provider: str | None = None,
) -> list[dict]:
    """Open pull requests, from whichever host this project's source lives on.

    THE PATH STILL SAYS `ado` and that is now only a name — the route serves GitHub
    too. Renaming it means moving five routers, their BFF proxies and every caller in
    one go, which is churn for a cosmetic gain; the behaviour is what mattered, and a
    GitHub project's PR picker returned an Azure DevOps error before this.
    """
    from shared.services import repo_source  # noqa: PLC0415

    try:
        _chosen, prs = await repo_source.list_pull_requests(
            request.state.tenant_id, ado_project, repo,
            project_id=project_id,
            owner_id=getattr(request.state, "user_id", "") or "",
            provider=provider,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return prs


class PrepareDocRequest(BaseModel):
    #: Which host to clone from. Omitted, the project's only configured source is used;
    #: with both connected, the picker names one. Never guessed at silently — a clone
    #: from the wrong host is a confusing failure, not a graceful fallback.
    provider: str | None = None
    mode: str = "branch"             # "branch" | "pr"
    ado_project: str
    repo_name: str
    branch: str | None = None
    pr_id: str | None = None


@documentation_workspace_router.post("/{project_id}/prepare")
async def prepare_docs(project_id: str, body: PrepareDocRequest, request: Request) -> dict:
    """Clone the branch (or PR source) read-only and detect languages + upstream artifacts."""
    tenant_id: str = request.state.tenant_id
    owner_id = getattr(request.state, "user_id", "") or ""
    # `project_id` AND `owner_id`, or the credential is never found. Source credentials
    # live in `project_integration_credentials`, saved per user per project — that is
    # the only place this platform keeps them, deliberately, so nobody borrows anybody
    # else's token. The façade threads both through to whichever backend answers.
    from shared.services import repo_source  # noqa: PLC0415

    try:
        chosen, _base, _secret = await repo_source.resolve(
            tenant_id, project_id=project_id, owner_id=owner_id, provider=body.provider,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

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
        except RuntimeError as exc:  # same unconfigured-connector path as below
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not pr:
            raise HTTPException(status_code=404, detail="PR not found")
        branch, pr_title = pr["source_branch"], pr["title"]
    if not branch:
        raise HTTPException(status_code=400, detail="branch is required")

    # A MISSING OR UNREACHABLE SOURCE IS NOT A CRASH. These raise RuntimeError carrying
    # an actionable sentence, and nothing used to catch them, so FastAPI turned it into a
    # bare 500 and the dialog simply did nothing with the one message that explained why.
    work_dir = str(ado_repos.WORKSPACE_ROOT / tenant_id / project_id / "documentation")
    try:
        result = await repo_source.clone(
            tenant_id, body.ado_project, body.repo_name, branch, work_dir,
            project_id=project_id, owner_id=owner_id, provider=chosen,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"Could not clone the branch: {exc}")
    remote_url = result.get("remote_url", "")

    languages = await asyncio.to_thread(_detect_languages, work_dir)
    upstream_summary = await _upstream_summary(tenant_id, project_id)

    set_prepared(tenant_id, project_id, {
        "work_dir": work_dir, "repo_url": remote_url, "pat": _secret, "provider": chosen,
        "mode": body.mode, "ado_project": body.ado_project, "repo_name": body.repo_name,
        "source_branch": branch, "pr_id": body.pr_id or "", "head_sha": result.get("commit_sha", ""),
        "languages": languages, "upstream_summary": upstream_summary,
    })

    return {
        "status": "ready", "provider": chosen, "mode": body.mode, "repo_name": body.repo_name,
        "ado_project": body.ado_project, "branch": branch, "pr_id": body.pr_id,
        "pr_title": pr_title, "head_sha": result.get("commit_sha", ""),
        "languages": languages, "upstream_summary": upstream_summary,
    }
