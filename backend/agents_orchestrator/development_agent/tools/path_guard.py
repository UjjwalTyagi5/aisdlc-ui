"""Path safety utilities for the Development Agent.

All file/lint tools must resolve paths through this module to prevent
directory traversal and symlink escape attacks.
"""
from __future__ import annotations

from pathlib import Path


class PathTraversalError(ValueError):
    """Raised when a path resolves outside the session workspace."""


def resolve_safe_path(workspace: str, relative_path: str) -> Path:
    """Resolve relative_path inside workspace, rejecting traversal and symlink escapes.

    Strips leading slashes/backslashes before joining so the agent cannot
    anchor to the filesystem root.  Uses Path.resolve() on both sides so
    symlinks cannot be used to escape the workspace boundary.
    """
    ws = Path(workspace).resolve()
    candidate = (ws / relative_path.lstrip("/\\")).resolve()
    try:
        candidate.relative_to(ws)
    except ValueError:
        raise PathTraversalError(
            f"Path '{relative_path}' escapes the session workspace — access denied."
        )
    return candidate


def validate_workspace_path(workspace: str, absolute_path: str) -> Path:
    """Assert that an already-absolute path sits inside workspace.

    Used by lint_tools which receives an absolute path from the LLM.
    """
    ws = Path(workspace).resolve()
    candidate = Path(absolute_path).resolve()
    try:
        candidate.relative_to(ws)
    except ValueError:
        raise PathTraversalError(
            f"Path '{absolute_path}' is outside the session workspace — access denied."
        )
    return candidate
