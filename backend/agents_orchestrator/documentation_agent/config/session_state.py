"""Per-session runtime state for the standalone Documentation agent (in-memory).

Mirrors the Deployment / Code Review / Security agents: holds the prepared target
(cloned repo + detected stack + any upstream-artifact summary) plus the list of
documents the agent has generated this session. The prepared target is keyed by
(tenant, project) so the WS chat binds it regardless of chat session id.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class DocSessionState:
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
    languages: List[str] = field(default_factory=list)
    structure_markers: dict = field(default_factory=dict)
    upstream_summary: str = ""           # short note: which platform artifacts exist
    generated_docs: List[dict] = field(default_factory=list)  # [{id,type,title,filename,format,path,contents,bytes}]
    pr_url: Optional[str] = None
    target_bound: bool = False
    system_injected: bool = False
    mcp_tools: list = field(default_factory=list)
    mcp_loaded: bool = False


_registry: Dict[str, DocSessionState] = {}
_lock = threading.Lock()


def get_session(session_id: str) -> DocSessionState:
    with _lock:
        if session_id not in _registry:
            _registry[session_id] = DocSessionState()
        return _registry[session_id]


def clear_session(session_id: str) -> None:
    with _lock:
        _registry.pop(session_id, None)


_prepared: Dict[tuple, dict] = {}


def set_prepared(tenant_id: str, project_id: str, data: dict) -> None:
    with _lock:
        _prepared[(str(tenant_id), str(project_id))] = data


def get_prepared(tenant_id: str, project_id: str) -> Optional[dict]:
    """The prepared target, from this process or from the record on disk.

    THE DISK FALLBACK IS THE POINT. This dict is process memory: a restart emptied it
    while the checkout it described stayed exactly where it was, and both the page and
    the agent then reported that nothing had been prepared. In development the restart
    is CAUSED by the prepare — cloning into the watched tree reloads the server — so
    the target was routinely gone seconds after it was made.

    The restored record carries no credential; the caller re-resolves it as the person
    who prepared the target. See shared/services/prepared_targets.py.
    """
    key = (str(tenant_id), str(project_id))
    with _lock:
        found = _prepared.get(key)
    if found is not None:
        return found

    from shared.services import prepared_targets  # noqa: PLC0415

    restored = prepared_targets.load("documentation", str(tenant_id), str(project_id))
    if restored is None:
        return None
    # Cached, so the next turn on this session does not read the file again.
    with _lock:
        _prepared.setdefault(key, restored)
        return _prepared[key]
