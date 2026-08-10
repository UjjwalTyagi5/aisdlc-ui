"""AutoGen-backed code execution sandbox for the Development Agent.

Uses AutoGen's LocalCommandLineCodeExecutor to run generated code in an
isolated temp directory with a configurable timeout.

Falls back to a subprocess-based implementation if pyautogen is not installed.

Execution is gated by SandboxPolicy:
- bash is disabled by default (allow_bash=False)
- output is capped at 50 KB and secrets are redacted
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from langchain_core.tools import tool

from agents_orchestrator.development_agent.tools.sandbox_policy import (
    DEFAULT_POLICY,
    SandboxPolicy,
    sanitize_output,
)


def _run_with_autogen(code: str, language: str, timeout: int, work_dir: str) -> str:
    """Execute code using AutoGen's LocalCommandLineCodeExecutor."""
    from autogen.coding import LocalCommandLineCodeExecutor, CodeBlock

    executor = LocalCommandLineCodeExecutor(
        timeout=timeout,
        work_dir=work_dir,
    )
    result = executor.execute_code_blocks(
        code_blocks=[CodeBlock(code=code, language=language)]
    )
    output = result.output.strip() if result.output else ""
    exit_code = result.exit_code
    if exit_code == 0:
        return f"Execution successful (exit 0):\n{output}" if output else "Execution successful (exit 0): no output"
    return f"Execution failed (exit {exit_code}):\n{output}"


async def _run_with_subprocess(code: str, language: str, timeout: int, work_dir: str) -> str:
    """Fallback: run code via subprocess when pyautogen is unavailable."""
    ext_map = {"python": ".py", "javascript": ".js", "typescript": ".ts"}
    cmd_map = {
        "python": ["python"],
        "javascript": ["node"],
        "typescript": ["npx", "ts-node"],
    }
    ext = ext_map.get(language, ".py")
    cmd = cmd_map.get(language, ["python"])

    code_file = Path(work_dir) / f"sandbox_code{ext}"
    code_file.write_text(code, encoding="utf-8")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, str(code_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Execution timed out after {timeout}s"

        output = (stdout.decode() + stderr.decode()).strip()
        if proc.returncode == 0:
            return f"Execution successful (exit 0):\n{output}" if output else "Execution successful (exit 0): no output"
        return f"Execution failed (exit {proc.returncode}):\n{output}"
    finally:
        try:
            code_file.unlink(missing_ok=True)
        except Exception:
            pass


@tool
async def execute_code_in_sandbox(
    code: str,
    language: str = "python",
    timeout_seconds: int = 30,
) -> str:
    """Execute generated code in an isolated sandbox and return the output.

    Use this after generating a component to verify it runs without errors
    BEFORE committing. Always fix failures before proceeding to git_commit.

    Supported languages: python, javascript, typescript.
    Bash execution is disabled for security reasons.

    Args:
        code: The source code to execute. Must be complete and self-contained.
        language: One of: python, javascript, typescript.
        timeout_seconds: Max execution time in seconds (max 120).

    Returns stdout + stderr from the execution, or a timeout/error message.
    """
    policy: SandboxPolicy = DEFAULT_POLICY

    if language == "bash":
        if not policy.allow_bash:
            return (
                "Error: bash execution is disabled by sandbox policy. "
                "Use run_command for build/test/lint commands instead."
            )

    if language not in ("python", "javascript", "typescript", "bash"):
        return f"Unsupported language '{language}'. Use: python, javascript, typescript."

    timeout = min(max(timeout_seconds, 5), 120)

    with tempfile.TemporaryDirectory(prefix="dev_sandbox_") as work_dir:
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, _run_with_autogen, code, language, timeout, work_dir
            )
        except ImportError:
            result = await _run_with_subprocess(code, language, timeout, work_dir)
        except Exception as e:
            result = f"Sandbox error: {e}"

    return sanitize_output(result, policy)
