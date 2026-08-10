"""Deployment workspace endpoints — connector detection, scan-target preparation
(clone read-only + detect the deploy connector), and open-PR listing.

Routes (mounted under '/deployment'):
  GET  /deployment/{project_id}/connectors
  GET  /deployment/{project_id}/ado/repos/{ado_project}/{repo}/prs
  POST /deployment/{project_id}/deploy/prepare

The ADO project/repo/branch cascade is reused from the dev-workspace endpoints.
"""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agents_orchestrator.deployment_agent.config.session_state import set_prepared
from shared.services import ado_repos

deployment_workspace_router = APIRouter()


def _detect_deploy_via(work_dir: str) -> str:
    """Heuristic: inspect the clone for the deploy mechanism the repo already uses."""
    has_argocd = has_gha = has_azp = False
    for dirpath, dirs, files in os.walk(work_dir):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "bin", "obj"}]
        low = dirpath.lower()
        if "argocd" in low or os.sep + "argo" in low:
            has_argocd = True
        if os.path.join(".github", "workflows") in dirpath.replace("/", os.sep).lower():
            has_gha = True
        for fn in files:
            if "azure-pipelines" in fn.lower() and fn.lower().endswith((".yml", ".yaml")):
                has_azp = True
    if has_argocd:
        return "argocd"
    if has_azp:
        return "azure_pipelines"
    if has_gha:
        return "github_actions"
    # Cloned from Azure DevOps → default to Azure Pipelines.
    return "azure_pipelines"


@deployment_workspace_router.get("/{project_id}/connectors")
async def list_deploy_connectors(project_id: str, request: Request) -> dict:
    """Which deployment connectors are available for this tenant (best-effort)."""
    tenant_id: str = request.state.tenant_id
    azure = github = False
    try:
        org_url, pat = await ado_repos.resolve_auth(tenant_id)
        azure = bool(org_url and pat)
    except Exception:
        azure = False
    return {
        "connectors": [
            {"kind": "azure_pipelines", "label": "Azure Pipelines", "available": azure},
            {"kind": "github_actions", "label": "GitHub Actions", "available": github},
            {"kind": "argocd", "label": "Argo CD (GitOps)", "available": True},
        ]
    }


@deployment_workspace_router.get("/{project_id}/ado/repos/{ado_project}/{repo}/prs")
async def list_open_prs(project_id: str, ado_project: str, repo: str, request: Request) -> list[dict]:
    return await ado_repos.list_pull_requests(
        ado_project, repo, status="active", tenant_id=request.state.tenant_id
    )


class PrepareDeployRequest(BaseModel):
    mode: str = "branch"             # "branch" | "pr"
    ado_project: str
    repo_name: str
    branch: str | None = None
    pr_id: str | None = None
    environment: str = "staging"
    deploy_via: str | None = None    # override; else auto-detected
    image_registry: str | None = None
    image_name: str | None = None
    namespace: str | None = None


@deployment_workspace_router.post("/{project_id}/deploy/prepare")
async def prepare_deploy(project_id: str, body: PrepareDeployRequest, request: Request) -> dict:
    """Clone the branch (or PR source) read-only and detect the deploy connector."""
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

    work_dir = str(ado_repos.WORKSPACE_ROOT / tenant_id / project_id / "deployment")
    try:
        result = await asyncio.to_thread(ado_repos.clone_into, work_dir, remote_url, branch, pat)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"Could not clone the branch: {exc}")

    deploy_via = body.deploy_via or await asyncio.to_thread(_detect_deploy_via, work_dir)

    set_prepared(tenant_id, project_id, {
        "work_dir": work_dir, "repo_url": remote_url, "pat": pat,
        "mode": body.mode, "ado_project": body.ado_project, "repo_name": body.repo_name,
        "source_branch": branch, "pr_id": body.pr_id or "", "head_sha": result.get("commit_sha", ""),
        "environment": body.environment, "deploy_via": deploy_via,
        "image_registry": body.image_registry or "", "image_name": body.image_name or body.repo_name,
        "namespace": body.namespace or "",
    })

    return {
        "status": "ready", "mode": body.mode, "repo_name": body.repo_name,
        "ado_project": body.ado_project, "branch": branch, "pr_id": body.pr_id,
        "pr_title": pr_title, "head_sha": result.get("commit_sha", ""),
        "environment": body.environment, "deploy_via": deploy_via,
        "image_registry": body.image_registry or "", "image_name": body.image_name or body.repo_name,
        "namespace": body.namespace or "",
    }
