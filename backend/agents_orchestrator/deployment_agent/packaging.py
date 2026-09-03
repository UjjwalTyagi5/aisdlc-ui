"""Deciding what a deployment package should contain — deterministic, not inferred.

Deployment agent phase 2.

WHY THIS IS CODE AND NOT PROMPT. `inspect_repo` returns a bag of file lists and leaves
the agent to work out the stack, which CI file belongs to the connector, and whether
each artifact is new or a refresh. Every one of those is a rule with a right answer, and
a model asked to hold them all in its head will eventually emit Kubernetes manifests
next to a Helm chart that already renders them, or a Dockerfile whose base image it
guessed from nothing.

THREE THINGS IT REFUSES TO GUESS, each returned in `undecided` with a reason rather than
filled in with something plausible:

  an unknown stack        No base image can be chosen for a repo with no recognised
                          project file. A Dockerfile built on a guessed runtime is a
                          build failure at best and the wrong runtime at worst.
  an unknown CI target    With no deploy connector bound there is no way to know whether
                          the answer is azure-pipelines.yml, a GitHub workflow, or a
                          Jenkinsfile. Emitting all three is not a package, it is a mess
                          somebody has to clean up.
  a chart that owns it    A repo with a Helm chart already has manifests; generating raw
                          ones beside it creates two sources of truth that drift.

`not_applicable` is the other half of the same honesty: a file deliberately not in the
package, and why. Silence would read as an oversight.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

#: Project file → (stack id, human name, the marker key inspect_repo reports it under).
#: Ordered: the first match wins, so a .NET service with a package.json for its frontend
#: assets is still a .NET service.
_STACK_MARKERS: Sequence[tuple[str, str, str]] = (
    ("csproj", "dotnet", ".NET"),
    ("pom_xml", "java_maven", "Java (Maven)"),
    ("build_gradle", "java_gradle", "Java (Gradle)"),
    ("go_mod", "go", "Go"),
    ("requirements_txt", "python", "Python"),
    ("package_json", "node", "Node.js"),
)

#: deploy_via → the CI file that connector actually reads.
_CI_FILE: Dict[str, str] = {
    "azure_pipelines": "azure-pipelines.yml",
    "github_actions": ".github/workflows/deploy.yml",
    "jenkins": "Jenkinsfile",
}

_MANIFEST_FILES = ("deploy/deployment.yaml", "deploy/service.yaml")
_RUNBOOKS = ("deploy/deploy-runbook.md", "deploy/rollback-runbook.md")


def detect_stack(markers: Dict[str, List[str]]) -> Dict[str, Any]:
    """Identify the stack from the project files present.

    Returns `kind: None` when nothing is recognised. That is an answer — the caller
    must not build a Dockerfile on top of a guess.
    """
    for key, kind, label in _STACK_MARKERS:
        found = markers.get(key) or []
        if found:
            return {"kind": kind, "label": label, "evidence": list(found)[:5]}
    return {
        "kind": None,
        "label": None,
        "evidence": [],
        "reason": (
            "No .csproj, pom.xml, build.gradle, go.mod, requirements.txt or "
            "package.json found, so the runtime is unknown."
        ),
    }


def _existing(markers: Dict[str, List[str]], key: str) -> Optional[str]:
    found = markers.get(key) or []
    return found[0] if found else None


def plan_package(
    markers: Dict[str, List[str]],
    deploy_via: str = "",
    *,
    environment: str = "",
    also: Sequence[str] = (),
) -> Dict[str, Any]:
    """Decide the deployment package for this repo.

    `also` names extras the user explicitly asked for ("helm", "compose", "jenkins").
    Nothing optional is generated without being asked: a docker-compose file nobody
    wanted is noise in a review, and a Helm chart imposed on a repo that manages
    manifests directly is a migration nobody agreed to.

    Every entry carries an `action`:
      create  — not present, generate it
      refresh — present, update in place rather than adding a second copy
    and everything excluded carries a reason, in `not_applicable` or `undecided`.
    """
    stack = detect_stack(markers)
    also = {a.lower() for a in also}
    files: List[Dict[str, Any]] = []
    not_applicable: List[Dict[str, str]] = []
    undecided: List[Dict[str, str]] = []

    # ── container image ───────────────────────────────────────────────────
    existing_dockerfile = _existing(markers, "dockerfile")
    if existing_dockerfile:
        not_applicable.append({
            "path": existing_dockerfile,
            "reason": "A Dockerfile already exists. Reuse its image name and exposed "
                      "port rather than replacing a file the team maintains.",
        })
    elif stack["kind"] is None:
        undecided.append({
            "path": "Dockerfile",
            "reason": stack["reason"] + " A base image cannot be chosen without it — "
                      "ask which runtime and version this service targets.",
        })
    else:
        files.append({
            "path": "Dockerfile", "action": "create", "kind": "container",
            "reason": f"{stack['label']} detected and no Dockerfile present.",
        })

    # ── kubernetes ────────────────────────────────────────────────────────
    helm_chart = _existing(markers, "helm")
    existing_manifests = markers.get("k8s_manifests") or []
    if helm_chart:
        if "helm" in also:
            files.append({
                "path": helm_chart, "action": "refresh", "kind": "kubernetes",
                "reason": "Helm chart already present; update it in place.",
            })
        not_applicable.append({
            "path": "deploy/deployment.yaml",
            "reason": f"A Helm chart already renders the manifests ({helm_chart}). "
                      "Generating raw manifests beside it creates two sources of "
                      "truth that drift apart.",
        })
    elif "helm" in also:
        for path in ("deploy/chart/Chart.yaml", "deploy/chart/values.yaml",
                     "deploy/chart/templates/deployment.yaml",
                     "deploy/chart/templates/service.yaml"):
            files.append({
                "path": path, "action": "create", "kind": "kubernetes",
                "reason": "Helm chart requested.",
            })
    elif existing_manifests:
        for path in existing_manifests[:6]:
            files.append({
                "path": path, "action": "refresh", "kind": "kubernetes",
                "reason": "Existing manifest — refresh rather than add a second copy.",
            })
    else:
        for path in _MANIFEST_FILES:
            files.append({
                "path": path, "action": "create", "kind": "kubernetes",
                "reason": "No Kubernetes manifests found.",
            })

    # ── CI / CD ───────────────────────────────────────────────────────────
    via = (deploy_via or "").strip().lower()
    if "jenkins" in also and via != "jenkins":
        # Asked for explicitly. A Jenkinsfile is generated for the repo; the platform
        # drives no Jenkins server, and the prompt says so rather than implying it will
        # run.
        files.append({
            "path": "Jenkinsfile",
            "action": "refresh" if _existing(markers, "jenkinsfile") else "create",
            "kind": "ci", "reason": "Jenkinsfile requested.",
        })
    if via == "argocd":
        argo_dir = _existing(markers, "argocd")
        files.append({
            "path": f"{argo_dir}/application.yaml" if argo_dir
                    else "deploy/argocd/application.yaml",
            "action": "refresh" if argo_dir else "create", "kind": "ci",
            "reason": "Argo CD is the bound deploy connector.",
        })
    elif via in _CI_FILE:
        marker_key = {"azure_pipelines": "azure_pipelines",
                      "github_actions": "github_actions",
                      "jenkins": "jenkinsfile"}[via]
        existing = _existing(markers, marker_key)
        files.append({
            "path": existing or _CI_FILE[via],
            "action": "refresh" if existing else "create", "kind": "ci",
            "reason": f"{via} is the bound deploy connector.",
        })
    else:
        undecided.append({
            "path": "the CI pipeline file",
            "reason": (
                "No deploy connector is bound, so there is no way to tell whether this "
                "should be azure-pipelines.yml, a GitHub workflow, or a Jenkinsfile. "
                "Emitting all three is not a package. Ask which one this project uses."
            ),
        })

    # ── local compose, only on request ────────────────────────────────────
    existing_compose = _existing(markers, "docker_compose")
    if "compose" in also:
        files.append({
            "path": existing_compose or "docker-compose.yml",
            "action": "refresh" if existing_compose else "create", "kind": "local",
            "reason": "docker-compose requested.",
        })
    elif existing_compose:
        not_applicable.append({
            "path": existing_compose,
            "reason": "A compose file exists and is for local development; it is not "
                      "part of the deployment package unless asked for.",
        })

    # ── runbooks, always ──────────────────────────────────────────────────
    for path in _RUNBOOKS:
        files.append({
            "path": path, "action": "create", "kind": "runbook",
            "reason": "Every deployment needs a way forward and a way back.",
        })

    # ── migrations are a risk flag, not a file ────────────────────────────
    warnings: List[str] = []
    if markers.get("migrations"):
        warnings.append(
            "This repo has database migrations. The deploy runbook must state whether "
            "they are backward-compatible and how to roll back if they are not — a "
            "rollback that leaves a migrated schema behind is not a rollback."
        )
    if environment and environment.lower() in ("prod", "production"):
        warnings.append(
            "Target is production. The rollback runbook is the part that matters here."
        )

    return {
        "stack": stack,
        "deploy_via": via or None,
        "environment": environment or None,
        "files": files,
        "not_applicable": not_applicable,
        "undecided": undecided,
        "warnings": warnings,
        "summary": (
            f"{len(files)} file(s) to stage, {len(not_applicable)} deliberately "
            f"excluded, {len(undecided)} that cannot be decided without an answer."
        ),
    }
