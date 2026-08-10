"""Read-only filesystem access to a pulled Development workspace.

Backs the IDE-style repo explorer on the Development page: a file tree and
single-file content reads, with path-traversal protection and size/binary
guards. All access is scoped to a workspace's on-disk `work_dir`; callers
resolve that from the tenant-scoped dev_workspace_store first.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess

# Directories never worth showing in a code explorer (VCS internals, deps, build
# output, caches). Pruned during the walk so large repos stay responsive.
_IGNORE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".next", "dist", "build", ".turbo", ".idea",
    ".vscode", "target", ".gradle", "bin", "obj", ".terraform", "coverage",
    ".cache", ".parcel-cache", ".nuxt", "out", "vendor",
}
_MAX_FILES = 8000
_MAX_FILE_BYTES = 1_000_000  # 1 MB — larger files are truncated for the viewer
_BINARY_SNIFF_BYTES = 8000


def list_tree(work_dir: str) -> dict:
    """Return a flat, sorted list of repo-relative file paths (POSIX separators).

    Ignored directories are pruned. Capped at _MAX_FILES with a `truncated` flag.
    """
    root = pathlib.Path(work_dir)
    if not root.is_dir():
        return {"paths": [], "truncated": False}

    paths: list[str] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
            paths.append(rel)
            if len(paths) >= _MAX_FILES:
                truncated = True
                break
        if truncated:
            break

    paths.sort()
    return {"paths": paths, "truncated": truncated}


def read_file(work_dir: str, rel_path: str) -> dict:
    """Return {path, content, size, binary, truncated} for one repo-relative file.

    Raises ValueError if the resolved path escapes the workspace (traversal guard)
    and FileNotFoundError if it is not a regular file.
    """
    root = pathlib.Path(work_dir).resolve()
    target = (root / rel_path).resolve()

    if root != target and not target.is_relative_to(root):
        raise ValueError("path escapes workspace")
    if not target.is_file():
        raise FileNotFoundError(rel_path)

    size = target.stat().st_size
    raw = target.read_bytes()[: _MAX_FILE_BYTES + 1]

    if b"\x00" in raw[:_BINARY_SNIFF_BYTES]:
        return {"path": rel_path, "content": "", "size": size, "binary": True, "truncated": False}

    truncated = size > _MAX_FILE_BYTES
    text = raw[:_MAX_FILE_BYTES].decode("utf-8", errors="replace")
    return {"path": rel_path, "content": text, "size": size, "binary": False, "truncated": truncated}


# ── Change detection (agent edits since the pulled commit) ─────────────────────

_STATUS_MAP = {
    "M": "modified", "A": "added", "D": "deleted",
    "R": "renamed", "C": "copied", "T": "modified",
}


def _git(work_dir: str, args: list[str], timeout: int = 25) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=work_dir, capture_output=True, text=True, timeout=timeout
    )


def list_changes(work_dir: str, base_sha: str | None) -> dict:
    """Files changed in the working tree vs *base_sha* (the pulled commit).

    Returns {base, files:[{path, status, additions, deletions}]}. Empty when no
    base or not a git repo. Captures both committed (feature branch) and
    uncommitted edits the agent made.
    """
    if not base_sha or not pathlib.Path(work_dir, ".git").exists():
        return {"base": base_sha, "files": []}

    counts: dict[str, tuple[int, int]] = {}
    num = _git(work_dir, ["diff", "--numstat", "-M", base_sha, "--"])
    for line in num.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            add, dele, path = parts[0], parts[1], parts[-1]
            counts[path] = (0 if add == "-" else int(add), 0 if dele == "-" else int(dele))

    files: list[dict] = []
    ns = _git(work_dir, ["diff", "--name-status", "-M", base_sha, "--"])
    for line in ns.stdout.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        status = _STATUS_MAP.get(parts[0][0], "modified")
        path = parts[-1]  # for renames the new path is last
        add, dele = counts.get(path, (0, 0))
        files.append({
            "path": path.replace(os.sep, "/"),
            "status": status,
            "additions": add,
            "deletions": dele,
        })
    files.sort(key=lambda f: f["path"])
    return {"base": base_sha, "files": files}


def changed_lines(work_dir: str, base_sha: str | None, rel_path: str) -> dict:
    """New-file line numbers that were added/modified for *rel_path* vs *base_sha*.

    Drives the diff highlight in the code viewer. Traversal-guarded.
    """
    if not base_sha:
        return {"added_lines": []}

    root = pathlib.Path(work_dir).resolve()
    target = (root / rel_path).resolve()
    if root != target and not target.is_relative_to(root):
        raise ValueError("path escapes workspace")

    r = _git(work_dir, ["diff", "-U0", "--no-color", base_sha, "--", rel_path])
    added: list[int] = []
    new_line = 0
    for line in r.stdout.splitlines():
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            if m:
                new_line = int(m.group(1))
        elif line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("+"):
            added.append(new_line)
            new_line += 1
        elif line.startswith("-"):
            continue
        else:
            new_line += 1
    return {"added_lines": added}
