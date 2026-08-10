"""Sandbox abstraction — Phase M.1.

Every `LanguageRunner.install()` / `run_tests()` / `parse_lint()` shells
out to a subprocess. To support Phase S's Docker-isolated runner without
changing the runners themselves, those subprocess calls go through a
`SandboxRunner` interface.

The default `LocalSubprocessSandbox` preserves Phase 4a behaviour exactly
(direct `subprocess.run` on the host). Phase S adds `DockerSandboxRunner`
under tools/sandbox/docker_runner.py, selected via the
`TESTING_AGENT_SANDBOX=docker` env flag.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


logger = logging.getLogger("testing_agent.sandbox")


# Toolchains whose installers frequently DON'T add themselves to PATH — most
# notably the .NET SDK on Windows. When a tool isn't on PATH, a bare
# subprocess call fails with [WinError 2] and (for the dotnet build check) is
# misreported as "generated tests did not compile". Resolving these to their
# well-known install dir makes the local sandbox work without a PATH edit.
_FALLBACK_TOOL_DIRS: Dict[str, List[str]] = {
    "dotnet": [
        r"C:\Program Files\dotnet",
        r"C:\Program Files (x86)\dotnet",
        os.path.expanduser(r"~\.dotnet"),
        "/usr/local/share/dotnet",
        "/usr/share/dotnet",
        os.path.expanduser("~/.dotnet"),
    ],
}


def resolve_executable(name: str) -> Optional[str]:
    """Full path to *name*: PATH first (shutil.which), then well-known install
    dirs for toolchains that are often missing from PATH (e.g. dotnet on Windows).
    Returns None if it can't be found anywhere."""
    found = shutil.which(name)
    if found:
        return found
    exe = name + ".exe" if os.name == "nt" else name
    for d in _FALLBACK_TOOL_DIRS.get(name, []):
        candidate = os.path.join(d, exe)
        if os.path.isfile(candidate):
            return candidate
    return None


@dataclass
class CmdResult:
    """Result of a single subprocess invocation through the sandbox."""
    cmd: List[str]
    cwd: str
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class SandboxRunner(ABC):
    """Run an arbitrary CLI command against a working directory.

    Implementations decide the isolation boundary (host process vs Docker
    container). Stdout/stderr come back as text; binary outputs should be
    written to files inside `cwd` and read out separately.
    """

    name: str = "abstract"

    @abstractmethod
    def run(
        self,
        cmd: List[str],
        cwd: str,
        timeout_s: int = 600,
        env: Optional[Dict[str, str]] = None,
    ) -> CmdResult:
        ...


class LocalSubprocessSandbox(SandboxRunner):
    """Default sandbox — runs commands directly on the host process.

    Mirrors the behaviour of the original `code_testing_agent.run_command`
    (now at `tools/code_testing_agent.py:22-37`) and dev agent's
    `git_tools._run_git`. NEVER passes `shell=True`.
    """

    name = "local"

    def run(
        self,
        cmd: List[str],
        cwd: str,
        timeout_s: int = 600,
        env: Optional[Dict[str, str]] = None,
    ) -> CmdResult:
        import time
        start = time.monotonic()
        merged_env = None
        if env:
            merged_env = {**os.environ, **env}
        # Resolve cmd[0] to a full path so a tool that's installed but not on PATH
        # (commonly the .NET SDK on Windows) runs instead of raising [WinError 2].
        run_cmd = cmd
        if cmd:
            resolved = resolve_executable(cmd[0])
            if resolved:
                run_cmd = [resolved, *cmd[1:]]
        try:
            proc = subprocess.run(
                run_cmd,
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=timeout_s,
                env=merged_env,
            )
            return CmdResult(
                cmd=cmd,
                cwd=cwd,
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return CmdResult(
                cmd=cmd,
                cwd=cwd,
                exit_code=124,
                stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                stderr=f"TimeoutExpired after {timeout_s}s",
                duration_ms=int((time.monotonic() - start) * 1000),
                timed_out=True,
            )
        except Exception as exc:
            logger.warning(f"LocalSubprocessSandbox.run failed: {exc}")
            return CmdResult(
                cmd=cmd,
                cwd=cwd,
                exit_code=1,
                stdout="",
                stderr=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )


def get_default_sandbox() -> SandboxRunner:
    """Return the sandbox selected by the env var, defaulting to local.

    Phase S registers `DockerSandboxRunner` under `TESTING_AGENT_SANDBOX=docker`.
    Anything else (or unset) → `LocalSubprocessSandbox`.
    """
    flag = os.environ.get("TESTING_AGENT_SANDBOX", "local").lower()
    if flag == "docker":
        try:
            from agents_orchestrator.testing_agent.tools.sandbox.docker_runner import DockerSandboxRunner
            return DockerSandboxRunner()
        except ImportError:
            logger.warning("TESTING_AGENT_SANDBOX=docker but docker_runner.py not available — falling back to local")
    return LocalSubprocessSandbox()
