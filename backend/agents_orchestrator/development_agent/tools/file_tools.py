"""File tools for the Development Agent.

All paths are relative to the session working directory.
Path traversal and symlink escapes are rejected by resolve_safe_path().
"""
from __future__ import annotations

import fnmatch
import os
import pathlib
import re

from langchain_core.tools import tool

from agents_orchestrator.development_agent.config.session_state import get_session
from agents_orchestrator.development_agent.tools.path_guard import (
    PathTraversalError,
    resolve_safe_path,
)
from config.connection_manager import manager
from config.ws_helper import broadcast_log, get_session_id, get_user_id

# file_tools.py → tools/ → development_agent/ → agents_orchestrator/ → agentic_app/
_FILES_DIR = str(pathlib.Path(__file__).resolve().parents[3] / "files")

_READ_LIMIT_BYTES = 200_000   # 200 KB — keep LLM context manageable
_WRITE_LIMIT_BYTES = 500_000  # 500 KB


def _get_work_dir() -> str:
    session_id = get_session_id()
    s = get_session(session_id)
    if s.work_dir:
        return s.work_dir
    user_id = get_user_id()
    work_dir = os.path.join(_FILES_DIR, str(user_id), "orchestrator", str(session_id), "project")
    os.makedirs(work_dir, exist_ok=True)
    return work_dir


@tool
def read_file(relative_path: str) -> str:
    """Read a file from the repository working directory and return its contents.

    Args:
        relative_path: Path relative to the repository root (e.g. 'src/App.js').
    """
    work_dir = _get_work_dir()
    try:
        full_path = resolve_safe_path(work_dir, relative_path)
    except PathTraversalError as e:
        return f"Error: {e}"

    if not full_path.exists():
        return f"Error: file not found: {relative_path}"

    size = full_path.stat().st_size
    if size > _READ_LIMIT_BYTES:
        return (
            f"Error: file is too large to read ({size:,} bytes). "
            f"Limit is {_READ_LIMIT_BYTES:,} bytes. Read a specific section instead."
        )
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        broadcast_log(manager, f"Read: {relative_path} ({len(content)} chars)", level="INFO")
        return content
    except Exception as e:
        return f"Error reading {relative_path}: {e}"


@tool
def write_file(relative_path: str, content: str) -> str:
    """Write or overwrite a file in the repository working directory.
    Creates parent directories automatically.

    Args:
        relative_path: Path relative to the repository root (e.g. 'src/components/Login.jsx').
        content: Full file content to write (must be complete, not a stub).
    """
    if not content or not content.strip():
        return (
            "ERROR: content is required but was empty or missing. "
            "Call write_file again and pass the complete source code as content=<the full code>. "
            "Never call this tool with an empty content field."
        )
    if len(content) > _WRITE_LIMIT_BYTES:
        return (
            f"Error: content is too large ({len(content):,} bytes). "
            f"Limit is {_WRITE_LIMIT_BYTES:,} bytes. Split into multiple files."
        )

    work_dir = _get_work_dir()
    try:
        full_path = resolve_safe_path(work_dir, relative_path)
    except PathTraversalError as e:
        return f"Error: {e}"

    full_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        full_path.write_text(content, encoding="utf-8")
        s = get_session(get_session_id())
        if relative_path not in s.dev_artifacts.generated_files:
            s.dev_artifacts.generated_files.append(relative_path)
        broadcast_log(manager, f"Wrote: {relative_path} ({len(content)} chars)", level="INFO")
        return f"Successfully wrote: {relative_path}"
    except Exception as e:
        return f"Error writing {relative_path}: {e}"


@tool
def list_directory(relative_path: str = ".") -> str:
    """List files and directories recursively from the repository root.
    Skips .git, node_modules, __pycache__, and build output folders.

    Args:
        relative_path: Subdirectory to list from, relative to the repo root (default '.').
    """
    work_dir = _get_work_dir()
    if relative_path in (".", ""):
        target = work_dir
    else:
        try:
            target = str(resolve_safe_path(work_dir, relative_path))
        except PathTraversalError as e:
            return f"Error: {e}"

    if not os.path.exists(target):
        return f"Error: path not found: {relative_path}"

    SKIP = {".git", "node_modules", "__pycache__", ".venv", "bin", "obj", ".vs", "dist", "build"}
    lines: list[str] = []
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in SKIP]
        rel_root = os.path.relpath(root, work_dir)
        level = rel_root.count(os.sep) if rel_root != "." else 0
        indent = "  " * level
        folder_name = os.path.basename(root) if rel_root != "." else "."
        lines.append(f"{indent}{folder_name}/")
        sub_indent = "  " * (level + 1)
        for f in sorted(files):
            lines.append(f"{sub_indent}{f}")

    return "\n".join(lines) if lines else "Empty directory"


@tool
def delete_file(relative_path: str) -> str:
    """Delete a file from the repository working directory.

    Args:
        relative_path: Path relative to the repository root.
    """
    work_dir = _get_work_dir()
    try:
        full_path = resolve_safe_path(work_dir, relative_path)
    except PathTraversalError as e:
        return f"Error: {e}"

    if not full_path.exists():
        return f"Error: file not found: {relative_path}"
    try:
        full_path.unlink()
        s = get_session(get_session_id())
        if relative_path not in s.dev_artifacts.changed_files:
            s.dev_artifacts.changed_files.append(relative_path)
        broadcast_log(manager, f"Deleted: {relative_path}", level="INFO")
        return f"Successfully deleted: {relative_path}"
    except Exception as e:
        return f"Error deleting {relative_path}: {e}"


@tool
def edit_file(relative_path: str, old_string: str, new_string: str) -> str:
    """Make a targeted edit to an existing file — replaces old_string with new_string.
    Does NOT overwrite the whole file; only the matched section changes.

    Safety rules (same as Claude Code):
    - Fails if old_string is not found → re-read the file and try again.
    - Fails if old_string appears more than once → add more surrounding context to make it unique.

    Use write_file only for brand-new files. Use edit_file for all modifications to existing files.

    Args:
        relative_path: Path relative to the repository root.
        old_string: The exact text to find (must be unique in the file).
        new_string: The text to replace it with.
    """
    work_dir = _get_work_dir()
    try:
        full_path = resolve_safe_path(work_dir, relative_path)
    except PathTraversalError as e:
        return f"Error: {e}"

    if not full_path.exists():
        return f"Error: file not found — {relative_path}. Use write_file to create it."

    size = full_path.stat().st_size
    if size > _READ_LIMIT_BYTES:
        return (
            f"Error: file is too large to edit inline ({size:,} bytes). "
            "Use write_file to replace it entirely."
        )

    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        count = content.count(old_string)
        if count == 0:
            return (
                f"Error: old_string not found in {relative_path}. "
                "The file may have changed — call read_file to get the current content."
            )
        if count > 1:
            return (
                f"Error: old_string matches {count} times in {relative_path}. "
                "Add more surrounding lines to old_string so it matches exactly once."
            )
        new_content = content.replace(old_string, new_string, 1)
        full_path.write_text(new_content, encoding="utf-8")
        s = get_session(get_session_id())
        if relative_path not in s.dev_artifacts.changed_files:
            s.dev_artifacts.changed_files.append(relative_path)
        broadcast_log(manager, f"Edited: {relative_path}", level="INFO")
        return f"Successfully edited: {relative_path}"
    except Exception as e:
        return f"Error editing {relative_path}: {e}"


@tool
def search_codebase(pattern: str, file_glob: str = "*") -> str:
    """Search all project files for a regex pattern (like grep).
    Returns matching lines as 'relative/path:line_number: content'.
    Capped at 200 results. Skips binary files and build/dependency folders.

    Use this to:
    - Find where a function, class, or variable is defined
    - Discover naming conventions before writing new code
    - Check all usages of an import or interface
    - Locate the right file to edit

    Args:
        pattern: Regular expression (or plain text) to search for.
        file_glob: Optional filename glob filter, e.g. '*.py', '*.ts', '*.jsx'.
                   Default '*' searches all text files.
    """
    work_dir = _get_work_dir()
    SKIP_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv",
        "bin", "obj", ".vs", "dist", "build", "out", ".next",
    }
    results: list[str] = []

    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return f"Error: invalid regex pattern — {e}"

    for root, dirs, files in os.walk(work_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for filename in files:
            if not fnmatch.fnmatch(filename, file_glob):
                continue
            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(full_path, work_dir).replace("\\", "/")
            try:
                with open(full_path, "rb") as fb:
                    chunk = fb.read(8192)
                    if b"\x00" in chunk:
                        continue
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if compiled.search(line):
                            results.append(f"{rel_path}:{lineno}: {line.rstrip()}")
                            if len(results) >= 200:
                                results.append("... (results capped at 200)")
                                return "\n".join(results)
            except Exception:
                continue

    return "\n".join(results) if results else "No matches found."
