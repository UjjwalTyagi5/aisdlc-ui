"""Diff analysis tool for the Code Review Agent.

Accepts a unified diff string and returns structured analysis:
files changed, lines added/removed, and per-file summaries.
"""
from __future__ import annotations

import json
import re

from langchain_core.tools import tool


@tool
def analyze_diff(diff_text: str) -> str:
    """Analyze a unified diff and return structured file-change information.

    Args:
        diff_text: A unified diff string (output of `git diff`).

    Returns:
        JSON string with files changed, lines added/removed per file.
    """
    if not diff_text or not diff_text.strip():
        return json.dumps({"status": "empty", "files": [], "total_added": 0, "total_removed": 0})

    files = []
    current_file = None
    added = 0
    removed = 0

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            if current_file:
                files.append(current_file)
            match = re.search(r"b/(.+)$", line)
            fname = match.group(1) if match else "unknown"
            current_file = {"file": fname, "added": 0, "removed": 0}
        elif line.startswith("+") and not line.startswith("+++"):
            if current_file:
                current_file["added"] += 1
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            if current_file:
                current_file["removed"] += 1
            removed += 1

    if current_file:
        files.append(current_file)

    return json.dumps({
        "status": "ok",
        "files_changed": len(files),
        "total_added": added,
        "total_removed": removed,
        "files": files,
    })
