"""Native tools for the standalone Deployment agent.

The branch under assessment is cloned by the API and bound to the session. These
tools let the agent inspect the repo, detect the connected deploy connector, stage
connector-appropriate deployment files, open a gated deployment PR, and submit the
structured release artifact. Read-only on the repo except the explicit open_deploy_pr.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import uuid

from langchain_core.tools import tool

from agents_orchestrator.deployment_agent.config.session_state import get_session
from config.connection_manager import manager
from config.ws_helper import broadcast_log, get_session_id

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "bin", "obj", "dist", "build"}
_MAX_FILE_BYTES = 200_000


def _work_dir() -> pathlib.Path | None:
    s = get_session(get_session_id())
    return pathlib.Path(s.work_dir) if s.work_dir else None


@tool
async def read_repo_file(path: str) -> str:
    """Read one repo-relative file from the cloned repo (e.g. an existing Dockerfile,
    manifest, or pipeline yaml) for deployment context."""
    root = _work_dir()
    if root is None or not root.exists():
        return "ERROR: no workspace prepared. Ask the user to select a branch/PR first."
    try:
        target = (root / path).resolve()
        target.relative_to(root.resolve())
    except ValueError:
        return "ERROR: path traversal denied."
    if not target.exists() or not target.is_file():
        return f"ERROR: file not found: {path}"
    return target.read_bytes()[:_MAX_FILE_BYTES].decode("utf-8", errors="replace")


@tool
async def search_repo(query: str, max_results: int = 40) -> str:
    """Grep the cloned repo for a literal string (find existing manifests, image
    names, ports, env usage, migrations)."""
    root = _work_dir()
    if root is None or not root.exists():
        return "ERROR: no workspace prepared."
    hits: list[dict] = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if len(hits) >= max_results:
                break
            fp = pathlib.Path(dirpath) / fn
            try:
                if fp.stat().st_size > _MAX_FILE_BYTES:
                    continue
                for i, line in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if query in line:
                        rel = str(fp.relative_to(root)).replace("\\", "/")
                        hits.append({"file": rel, "line": i, "text": line.strip()[:200]})
                        if len(hits) >= max_results:
                            break
            except Exception:
                continue
    return json.dumps({"matches": hits, "count": len(hits)})


@tool
async def inspect_repo() -> str:
    """Summarize the repo for deployment planning: detected stack, existing deploy
    assets (Dockerfile/manifests/pipelines/argocd/helm), and the bound deploy
    connector. Call this first."""
    s = get_session(get_session_id())
    root = _work_dir()
    if root is None or not root.exists():
        return "ERROR: no workspace prepared."
    markers = {
        "dockerfile": [], "k8s_manifests": [], "helm": [], "argocd": [],
        "azure_pipelines": [], "github_actions": [], "csproj": [], "package_json": [],
        "requirements_txt": [], "go_mod": [], "migrations": [],
        # Java and Jenkins were not detected at all, so a Maven or Gradle repo
        # reported an unknown stack and a Jenkins repo looked like it had no CI.
        "pom_xml": [], "build_gradle": [], "jenkinsfile": [], "docker_compose": [],
    }
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        rel_dir = str(pathlib.Path(dirpath).relative_to(root)).replace("\\", "/")
        low_dir = rel_dir.lower()
        if "argocd" in low_dir or "argo" in low_dir:
            markers["argocd"].append(rel_dir)
        if "migration" in low_dir:
            markers["migrations"].append(rel_dir)
        for fn in files:
            rel = (f"{rel_dir}/{fn}" if rel_dir != "." else fn)
            l = fn.lower()
            if l == "dockerfile":
                markers["dockerfile"].append(rel)
            elif l.endswith(".csproj"):
                markers["csproj"].append(rel)
            elif l == "package.json":
                markers["package_json"].append(rel)
            elif l == "requirements.txt":
                markers["requirements_txt"].append(rel)
            elif l == "go.mod":
                markers["go_mod"].append(rel)
            elif l == "pom.xml":
                markers["pom_xml"].append(rel)
            elif l in ("build.gradle", "build.gradle.kts"):
                markers["build_gradle"].append(rel)
            elif l == "jenkinsfile" or l.startswith("jenkinsfile."):
                markers["jenkinsfile"].append(rel)
            elif l in ("docker-compose.yml", "docker-compose.yaml", "compose.yml",
                       "compose.yaml"):
                markers["docker_compose"].append(rel)
            elif l in ("chart.yaml", "values.yaml"):
                markers["helm"].append(rel)
            elif "azure-pipelines" in l and l.endswith((".yml", ".yaml")):
                markers["azure_pipelines"].append(rel)
            elif ".github/workflows" in rel.lower() and l.endswith((".yml", ".yaml")):
                markers["github_actions"].append(rel)
            elif l.endswith((".yml", ".yaml")) and ("deploy" in l or "manifest" in low_dir or "k8s" in low_dir):
                markers["k8s_manifests"].append(rel)
    markers = {k: v[:20] for k, v in markers.items()}
    return json.dumps({
        "deploy_via": s.deploy_via,
        "environment": s.environment,
        "image_registry": s.image_registry,
        "image_name": s.image_name or s.repo_name,
        "namespace": s.namespace,
        "markers": markers,
    })


@tool
async def read_upstream_artifacts() -> str:
    """Read this project's latest testing + security artifacts (gate evidence), if any."""
    s = get_session(get_session_id())
    if not s.tenant_id or not s.project_id:
        return json.dumps({"testing": None, "security": None})
    from sqlalchemy import select
    from shared.db import get_db_session_for_tenant
    from shared.models.orm import Run

    out = {"testing": None, "security": None}
    try:
        async with get_db_session_for_tenant(s.tenant_id) as db:
            for col, key in (("testing_artifacts", "testing"), ("security_artifacts", "security")):
                row = (
                    await db.execute(
                        select(getattr(Run, col))
                        .where(Run.project_id == uuid.UUID(s.project_id), getattr(Run, col).isnot(None))
                        .order_by(Run.created_at.desc()).limit(1)
                    )
                ).scalars().first()
                if row:
                    out[key] = row
    except Exception:
        pass
    return json.dumps(out, default=str)[:12000]


@tool
async def stage_deploy_file(path: str, contents: str, language: str = "yaml") -> str:
    """Stage a generated deployment file (repo-relative path) for the deployment PR.
    Call once per file (Dockerfile, k8s manifests, azure-pipelines.yml or
    .github/workflows/deploy.yml, runbooks). Files are written only when the user
    opens the PR — nothing is pushed here."""
    s = get_session(get_session_id())
    if not path or ".." in path.replace("\\", "/").split("/"):
        return "ERROR: invalid path."
    s.staged_files = [f for f in s.staged_files if f.get("path") != path]
    s.staged_files.append({"path": path, "contents": contents, "language": language})
    return f"Staged {path} ({len(contents)} bytes). {len(s.staged_files)} file(s) staged for the deployment PR."


@tool
async def open_deploy_pr(title: str = "", description: str = "") -> str:
    """Open the deployment PR with all staged files (GATED — only call when the user
    explicitly asks to open/create the PR). Pushes a new branch and opens a PR."""
    from shared.services import ado_repos

    s = get_session(get_session_id())
    if not s.staged_files:
        return "ERROR: no files staged. Stage deployment files first (stage_deploy_file)."
    if not (s.work_dir and s.repo_name and s.source_branch):
        return "ERROR: no prepared target. Select a branch first."
    new_branch = f"deploy/{s.environment}-{uuid.uuid4().hex[:8]}"
    files = [(f["path"], f["contents"]) for f in s.staged_files]
    pr_title = title or f"Deployment package for {s.repo_name} ({s.environment})"
    try:
        sha = await asyncio.to_thread(
            ado_repos.commit_and_push_files, s.work_dir, s.source_branch, new_branch,
            files, s.pat, pr_title,
        )
        pr_url = await ado_repos.create_pull_request(
            s.ado_project or s.repo_name, s.repo_name, new_branch, s.source_branch,
            pr_title, description or "Generated deployment artifacts.", pat=s.pat,
            tenant_id=s.tenant_id,
        )
    except Exception as exc:
        return f"ERROR opening deployment PR: {str(exc)[:400]}"
    if not pr_url:
        return "ERROR: pushed the branch but could not open the PR (repo not found?)."
    if s.last_artifact:
        s.last_artifact["pr_url"] = pr_url
        s.last_artifact["pr_title"] = pr_title
        s.last_artifact["status"] = "pr_opened"
    broadcast_log(manager, f"Opened deployment PR: {pr_url}", level="INFO")
    return f"Deployment PR opened: {pr_url} (branch {new_branch} → {s.source_branch}, {len(files)} files)."


@tool
async def submit_release(release_json: str) -> str:
    """Submit the final structured deployment assessment. Call ONCE after assessing
    readiness + staging the deployment files.

    Args:
        release_json: a JSON object with keys:
          summary (markdown), readiness ("ready"|"blocked"|"conditional"),
          risk_score ("critical".."none"), risk_rationale,
          gate_summary: [{name, status, note}],
          deploy_runbook (md), rollback_runbook (md),
          iac_findings: [{file, severity, rule, description, remediation}],
          compliance_evidence: {gate_approvals[], test_summary, security_summary, sbom_present, notes},
          release_decision ("go"|"no_go"|"conditional"), release_justification
    """
    from shared.models.deployment import (
        DeploymentArtifact, DeployContext, ComplianceEvidence,
    )

    s = get_session(get_session_id())
    if not s.staged_files:
        return (
            "ERROR: no deployment files staged yet. Generating the deployment package "
            "(Dockerfile, k8s manifests, pipeline yaml, deploy + rollback runbooks) via "
            "stage_deploy_file is this agent's primary deliverable — call stage_deploy_file "
            "for each package file first, then call submit_release again."
        )
    try:
        payload = json.loads(release_json) if isinstance(release_json, str) else dict(release_json)
    except Exception as exc:
        return f"ERROR: release_json not valid JSON ({exc})."

    ctx = DeployContext(
        repo_name=s.repo_name, ado_project=s.ado_project, mode=s.mode or "branch",
        source_branch=s.source_branch, pr_id=s.pr_id or None, head_sha=s.head_sha,
        environment=s.environment, deploy_via=s.deploy_via or "unknown",
    )
    ce = payload.get("compliance_evidence") or {}
    from datetime import datetime, timezone
    compliance = ComplianceEvidence(
        captured_at=datetime.now(timezone.utc).isoformat(),
        gate_approvals=ce.get("gate_approvals", []),
        test_summary=ce.get("test_summary", ""),
        security_summary=ce.get("security_summary", ""),
        sbom_present=bool(ce.get("sbom_present", False)),
        notes=ce.get("notes", ""),
    )
    try:
        artifact = DeploymentArtifact(
            context=ctx,
            summary=payload.get("summary", ""),
            readiness=payload.get("readiness", "conditional"),
            risk_score=payload.get("risk_score", "medium"),
            risk_rationale=payload.get("risk_rationale", ""),
            gate_summary=payload.get("gate_summary", []),
            generated_files=[
                {"path": f["path"], "language": f.get("language", "yaml"), "contents": f.get("contents", "")}
                for f in s.staged_files
            ],
            deploy_runbook=payload.get("deploy_runbook", ""),
            rollback_runbook=payload.get("rollback_runbook", ""),
            iac_findings=payload.get("iac_findings", []),
            compliance_evidence=compliance,
            release_decision=payload.get("release_decision", "conditional"),
            release_justification=payload.get("release_justification", ""),
            status="assessed",
        )
    except Exception as exc:
        return f"ERROR: release did not match the required shape: {exc}"

    s.last_artifact = artifact.model_dump()
    broadcast_log(
        manager,
        f"Deployment assessment complete: risk={artifact.risk_score}, "
        f"decision={artifact.release_decision}, {len(s.staged_files)} files staged",
        level="INFO",
    )
    return (
        f"Release assessment submitted: readiness={artifact.readiness}, risk={artifact.risk_score}, "
        f"decision={artifact.release_decision}. {len(s.staged_files)} deployment files staged — "
        f"the user can now review and open the deployment PR."
    )


@tool
async def plan_deploy_package(also: str = "") -> str:
    """Decide WHICH files this repo's deployment package needs. Call after inspect_repo
    and before staging anything.

    `also` is a comma-separated list of extras the USER explicitly asked for:
    "helm", "compose", "jenkins". Do not pass extras nobody requested — a Helm chart
    imposed on a repo that manages manifests directly is a migration nobody agreed to.

    Returns `files` (each with create-or-refresh and why), `not_applicable` (deliberately
    excluded, with the reason), and `undecided` — things that CANNOT be chosen from the
    repo alone, such as a base image for an unrecognised stack or which CI file to write
    when no deploy connector is bound.

    RELAY `undecided` AND ASK. Do not fill those gaps in yourself: a Dockerfile built on
    a guessed runtime and a pipeline written for the wrong CI system both look like work
    and are worse than nothing.
    """
    from agents_orchestrator.deployment_agent.packaging import plan_package

    s = get_session(get_session_id())
    root = _work_dir()
    if root is None or not root.exists():
        return "ERROR: no workspace prepared. Ask the user to select a branch/PR first."

    raw = json.loads(await inspect_repo.ainvoke({}))
    if not isinstance(raw, dict) or "markers" not in raw:
        return "ERROR: could not inspect the repo."

    extras = [x.strip().lower() for x in (also or "").split(",") if x.strip()]
    return json.dumps(
        plan_package(
            raw["markers"], s.deploy_via or "", environment=s.environment or "",
            also=extras,
        ),
        indent=2, default=str,
    )
