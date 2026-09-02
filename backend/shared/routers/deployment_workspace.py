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

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agents_orchestrator.deployment_agent.config.session_state import set_prepared
from shared.authz.dependency import require_permission
from shared.authz.project_scope import require_project_access
from shared.db import get_db_session
from shared.services import ado_repos

# EVERY ROUTE HERE IS SCOPED TO ITS {project_id}. Was bare APIRouter() — the only
# gate was the artifact:view floor applied at include time (process_api.py), which
# contributor holds; a contributor could clone/prepare-deploy/read connector state
# against ANY project id in the tenant. Same finding, same fix, as
# shared/routers/security_workspace.py — see docs/rbac-audit-2026-08-17.md finding 3,
# which lists this file as fixed alongside dev/code_review/security_workspace.py but
# the dependency was never actually attached here (documentation_workspace.py has the
# identical gap — see that file's own note). Pinned by
# tests/test_project_workspace_scope.py.
deployment_workspace_router = APIRouter(dependencies=[Depends(require_project_access())])


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


async def _github_actions_available(tenant_id: str) -> bool:
    """True when this tenant has a working GitHub Actions credential.

    This used to be hardcoded False, which meant the GitHub Actions option in the
    deploy-target dialog could never be selected no matter what the tenant had
    connected. Resolves the same `gha-pat` / `gha-owner` refs the Integrations form
    writes, then verifies with the shared probe. Best-effort by contract — any failure
    reads as "unavailable" rather than raising into the endpoint.
    """
    try:
        from shared.services import secret_store
        from shared.services.deployment_probe import probe_github_actions

        pat = await secret_store.get_secret(tenant_id, "gha-pat")
        if not pat or pat == secret_store.DISCONNECTED_MARKER:
            return False
        owner = await secret_store.get_secret(tenant_id, "gha-owner")
        ok, _account, _err = await probe_github_actions(pat, owner or None)
        return bool(ok)
    except Exception:  # noqa: BLE001
        return False


@deployment_workspace_router.get("/{project_id}/connectors")
async def list_deploy_connectors(project_id: str, request: Request) -> dict:
    """Which deployment connectors are available for this tenant (best-effort)."""
    tenant_id: str = request.state.tenant_id
    azure = False
    try:
        org_url, pat = await ado_repos.resolve_auth(tenant_id)
        azure = bool(org_url and pat)
    except Exception:
        azure = False
    github = await _github_actions_available(tenant_id)
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


# ── The approval gate (deployment agent phase 1) ──────────────────────────────
#
# NOTHING REACHES AN ENVIRONMENT WITHOUT A NAMED HUMAN APPROVING IT. Generating
# deployment files needs no gate; creating a pipeline, starting a run, or applying to a
# cluster each become a `pending` deployment row that somebody has to accept.
#
# TWO CHECKS, as everywhere else in this codebase. `require_project_access()` on the
# router says the caller reaches THIS project; `require_permission` on the route says
# they take deployment decisions at all.
#
# NOT assert_can_administer_project, which the artifact gate uses. That one demands
# project administration, and `artifact:approve_deployment` is held by devops_engineer —
# the role whose job this is and which is deliberately not a project admin. Requiring
# both would leave the permission granted to nobody who could use it.


class DeploymentDecisionIn(BaseModel):
    """A rejection may carry a reason; an approval needs nothing."""

    reason: str | None = None


def _deployment_out(dep) -> dict:
    return {
        "id": str(dep.id),
        "projectId": str(dep.project_id),
        "runId": str(dep.run_id) if dep.run_id else None,
        "action": dep.action,
        "targetKind": dep.target_kind,
        "environment": dep.environment,
        "request": dep.request,
        "requestedBy": dep.requested_by,
        "requestedAt": dep.requested_at.isoformat() if dep.requested_at else None,
        "approvalStatus": dep.approval_status,
        "approvedBy": dep.approved_by,
        "approvedAt": dep.approved_at.isoformat() if dep.approved_at else None,
        "rejectionReason": dep.rejection_reason,
        "executionStatus": dep.execution_status,
        "executedAt": dep.executed_at.isoformat() if dep.executed_at else None,
        "externalId": dep.external_id,
        "externalUrl": dep.external_url,
        "outcome": dep.outcome,
    }


@deployment_workspace_router.get("/{project_id}/deployments")
async def list_project_deployments(
    project_id: str,
    request: Request,
    pending_only: bool = False,
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Deployments for this project, newest first."""
    from shared.services import deployment_gate

    rows = await deployment_gate.list_deployments(
        db, tenant_id=request.state.tenant_id, project_id=project_id,
        pending_only=pending_only,
    )
    return [_deployment_out(d) for d in rows]


@deployment_workspace_router.post(
    "/{project_id}/deployments/{deployment_id}/approve",
    dependencies=[Depends(require_permission("artifact:approve_deployment"))],
)
async def approve_deployment_route(
    project_id: str,
    deployment_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Release a pending deployment to run.

    NO COMMIT HERE. `get_db_session` sets the RLS tenant transaction-locally and owns
    the commit at request end; committing mid-request drops the tenant and the next
    statement reads an empty table.
    """
    from shared.services import deployment_gate

    try:
        dep = await deployment_gate.approve_deployment(
            db, deployment_id=deployment_id, tenant_id=request.state.tenant_id,
            approver=getattr(request.state, "user_id", "") or "",
        )
    except deployment_gate.DeploymentGateError as exc:
        raise HTTPException(
            status_code=404 if exc.code == "not_found" else 409,
            detail={"code": exc.code, "message": exc.reason},
        ) from None
    if str(dep.project_id) != str(project_id):
        raise HTTPException(status_code=404, detail="Deployment not found")
    return _deployment_out(dep)


@deployment_workspace_router.post(
    "/{project_id}/deployments/{deployment_id}/reject",
    dependencies=[Depends(require_permission("artifact:approve_deployment"))],
)
async def reject_deployment_route(
    project_id: str,
    deployment_id: str,
    request: Request,
    body: DeploymentDecisionIn | None = None,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Refuse a pending deployment."""
    from shared.services import deployment_gate

    try:
        dep = await deployment_gate.reject_deployment(
            db, deployment_id=deployment_id, tenant_id=request.state.tenant_id,
            approver=getattr(request.state, "user_id", "") or "",
            reason=(body.reason if body else "") or "",
        )
    except deployment_gate.DeploymentGateError as exc:
        raise HTTPException(
            status_code=404 if exc.code == "not_found" else 409,
            detail={"code": exc.code, "message": exc.reason},
        ) from None
    if str(dep.project_id) != str(project_id):
        raise HTTPException(status_code=404, detail="Deployment not found")
    return _deployment_out(dep)
