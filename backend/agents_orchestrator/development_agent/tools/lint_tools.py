"""Lint and static analysis tools for the Development Agent."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from langchain_core.tools import tool

from agents_orchestrator.development_agent.config.session_state import get_session
from agents_orchestrator.development_agent.tools.path_guard import (
    PathTraversalError,
    validate_workspace_path,
)
from config.ws_helper import get_session_id
from shared.models.development import ValidationResult


def _record_lint(s, target_path: str, linter: str, exit_code: int, output: str) -> None:
    status = "passed" if exit_code == 0 else "failed"
    vr = ValidationResult(
        name=f"{linter}: {target_path}"[:80],
        status=status,
        command=f"{linter} {target_path}",
        summary="No issues" if status == "passed" else "Issues found",
        output=output[:2_000],
    )
    s.dev_artifacts.lint_results.append(vr)


async def _run(cmd: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(), stderr.decode()


@tool
async def lint_and_validate_code(target_path: str, language: str = "auto") -> str:
    """Run a linter on the generated code and return the results.

    Runs ruff for Python, eslint for JavaScript/TypeScript.
    Always call this before git_commit to catch errors early.

    Args:
        target_path: Absolute path to a file or directory to lint.
        language: One of: python, javascript, typescript, auto.
                  With "auto", the language is inferred from file extension.

    Returns linter output. An empty result means no issues were found.
    """
    session_id = get_session_id()
    s = get_session(session_id)

    if not s.work_dir:
        return "Error: no workspace established. Call clone_repo or init_project_structure first."

    try:
        safe_path = validate_workspace_path(s.work_dir, target_path)
    except PathTraversalError as e:
        return f"Error: {e}"

    if not safe_path.exists():
        return f"Error: path not found: {target_path}"

    if language == "auto":
        if safe_path.is_dir():
            exts = {f.suffix for f in safe_path.rglob("*") if f.is_file()}
            if {".js", ".jsx", ".ts", ".tsx"} & exts:
                language = "javascript"
            else:
                language = "python"
        else:
            ext = safe_path.suffix.lower()
            if ext in (".js", ".jsx", ".ts", ".tsx"):
                language = "javascript"
            else:
                language = "python"

    if language == "python":
        code, stdout, stderr = await _run(["ruff", "check", str(safe_path)])
        output = (stdout + stderr).strip()
        _record_lint(s, target_path, "ruff", code, output)
        if code == 0 and not output:
            return f"ruff: no issues found in {target_path}"
        return f"ruff results for {target_path}:\n{output}"

    elif language in ("javascript", "typescript"):
        code, stdout, stderr = await _run(["npx", "eslint", str(safe_path), "--format", "stylish"])
        output = (stdout + stderr).strip()
        _record_lint(s, target_path, "eslint", code, output)
        if code == 0 and not output:
            return f"eslint: no issues found in {target_path}"
        return f"eslint results for {target_path}:\n{output}"

    else:
        return f"Unsupported language '{language}'. Use: python, javascript, typescript, auto."
