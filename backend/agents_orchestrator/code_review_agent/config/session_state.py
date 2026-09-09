"""Per-session runtime state for the Code Review agent (ephemeral, in-memory).

Mirrors the Development agent's session_state but read-only: it holds the
prepared review target (the cloned repo + computed diff) plus the last produced
artifact. The durable record is the persisted `code_review_artifacts` Run.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ReviewSessionState:
    work_dir: str = ""
    repo_url: str = ""
    pat: str = ""
    project_id: str = ""
    tenant_id: str = ""
    # Prepared target
    mode: str = ""                       # "branch" | "pr"
    ado_project: str = ""
    repo_name: str = ""
    source_branch: str = ""
    base_branch: str = ""
    pr_id: str = ""
    pr_title: str = ""
    head_sha: str = ""
    base_sha: str = ""
    diff_text: str = ""
    changed_files: List[dict] = field(default_factory=list)
    target_bound: bool = False           # diff injected into the system context yet?
    # Output
    last_artifact: Optional[dict] = None
    # Plumbing
    system_injected: bool = False
    mcp_tools: list = field(default_factory=list)
    mcp_loaded: bool = False


_registry: Dict[str, ReviewSessionState] = {}
_lock = threading.Lock()


def get_session(session_id: str) -> ReviewSessionState:
    with _lock:
        if session_id not in _registry:
            _registry[session_id] = ReviewSessionState()
        return _registry[session_id]


def clear_session(session_id: str) -> None:
    with _lock:
        _registry.pop(session_id, None)


# ── Prepared-target store (project-keyed, like the dev workspace) ───────────────
# The REST `prepare` endpoint clones + diffs and stashes the result here keyed by
# (tenant, project); the WS chat binds it on the first message regardless of which
# chat session id the frontend generated.

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

    restored = prepared_targets.load("code_review", str(tenant_id), str(project_id))
    if restored is None:
        return None
    # Cached, so the next turn on this session does not read the file again.
    with _lock:
        _prepared.setdefault(key, restored)
        return _prepared[key]
