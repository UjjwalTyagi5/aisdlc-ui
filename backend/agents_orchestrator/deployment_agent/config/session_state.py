"""Per-session runtime state for the standalone Deployment agent (in-memory).

Mirrors the Code Review / Security agents: holds the prepared target (cloned repo
+ detected deploy connector) plus the last produced artifact. Prepared target is
keyed by (tenant, project) so the WS chat binds it regardless of chat session id.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class DeploySessionState:
    work_dir: str = ""
    repo_url: str = ""
    pat: str = ""
    project_id: str = ""
    tenant_id: str = ""
    mode: str = ""                       # "branch" | "pr"
    ado_project: str = ""
    repo_name: str = ""
    source_branch: str = ""
    pr_id: str = ""
    head_sha: str = ""
    environment: str = "staging"
    deploy_via: str = "unknown"          # azure_pipelines | github_actions | argocd | unknown
    image_registry: str = ""
    image_name: str = ""
    namespace: str = ""
    target_bound: bool = False
    staged_files: List[dict] = field(default_factory=list)   # [{path, language, contents}]
    last_artifact: Optional[dict] = None
    system_injected: bool = False
    mcp_tools: list = field(default_factory=list)
    mcp_loaded: bool = False


_registry: Dict[str, DeploySessionState] = {}
_lock = threading.Lock()


def get_session(session_id: str) -> DeploySessionState:
    with _lock:
        if session_id not in _registry:
            _registry[session_id] = DeploySessionState()
        return _registry[session_id]


def clear_session(session_id: str) -> None:
    with _lock:
        _registry.pop(session_id, None)


_prepared: Dict[tuple, dict] = {}


def set_prepared(tenant_id: str, project_id: str, data: dict) -> None:
    with _lock:
        _prepared[(str(tenant_id), str(project_id))] = data


def get_prepared(tenant_id: str, project_id: str) -> Optional[dict]:
    with _lock:
        return _prepared.get((str(tenant_id), str(project_id)))
