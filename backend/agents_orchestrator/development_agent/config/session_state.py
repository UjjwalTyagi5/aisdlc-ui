"""Per-session runtime state for the Development Agent.

Replaces the module-level shared.py globals so that concurrent sessions
cannot bleed state into each other.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict

from shared.models.development import DevelopmentArtifacts


@dataclass
class DevSessionState:
    work_dir: str = ""
    repo_url: str = ""
    repo_type: str = ""          # "ado" | "github" | "local"
    branch_name: str = ""
    pat: str = ""
    pr_url: str = ""
    pr_title: str = ""
    build_attempts: int = 0
    system_injected: bool = False
    dev_artifacts: DevelopmentArtifacts = field(default_factory=DevelopmentArtifacts)
    ado_repos: Dict[str, str] = field(default_factory=dict)  # repo_name → clone_url
    ado_org_url: str = ""    # resolved from the tenant connector, not env
    ado_project: str = ""
    ado_repo_id: str = ""
    ado_repo_name: str = ""
    jira_base_url: str = ""  # set when provider_kind=="jira"; used by PR helpers to inject browse link
    provider_kind: str = ""  # persisted so follow-up WS messages can restore the ContextVar
    mcp_tools: list = field(default_factory=list)  # BYO MCP tools, loaded once per chat session
    mcp_loaded: bool = False
    # HITL push gate — STANDALONE dev agent only. push_gate_enabled is set True by
    # development_agent_api (the standalone WS/REST path) and left False everywhere
    # else, so the copilot orchestrator and the pipeline (which share these
    # git tools) are unaffected. When enabled, push_branch/create_pr refuse (and show
    # the diff + ask) unless push_approved is set for the current turn — which the API
    # sets only when the user's message is an explicit approval ("push" / "yes").
    push_gate_enabled: bool = False
    push_approved: bool = False


_registry: Dict[str, DevSessionState] = {}
_lock = threading.Lock()


def get_session(session_id: str) -> DevSessionState:
    """Return (creating if necessary) the DevSessionState for session_id."""
    with _lock:
        if session_id not in _registry:
            _registry[session_id] = DevSessionState()
        return _registry[session_id]


def reset_session(session_id: str) -> None:
    """Replace the session state with a fresh DevSessionState."""
    with _lock:
        _registry[session_id] = DevSessionState()


def clear_session(session_id: str) -> None:
    """Remove the session from the registry entirely (on explicit cleanup)."""
    with _lock:
        _registry.pop(session_id, None)
