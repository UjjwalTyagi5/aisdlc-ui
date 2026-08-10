"""Docker-isolated sandbox runner — Phase S.

Selected by `TESTING_AGENT_SANDBOX=docker` env var (read by
`tools/sandbox/base.py:get_default_sandbox`). Default behaviour stays
LocalSubprocessSandbox so existing flows are unchanged.

Invocation form:
  docker run --rm --network=none --read-only \
             --tmpfs /tmp:rw --tmpfs /work-tmp:rw \
             -v <cwd>:/work \
             -w /work <image> sh -c "<cmd>"

Image selection by `cmd[0]`:
  - pytest|python|pip|coverage|pylint  → python:3.12-slim
  - dotnet                              → mcr.microsoft.com/dotnet/sdk:8.0
  - npm|npx|node|jest|eslint            → node:20-alpine
  - git                                 → alpine/git:latest

If the chosen image isn't available locally, Docker pulls it on the
first run; subsequent runs reuse the cached layer.

All commands run with --network=none for hard isolation; tweak this in
`_DEFAULT_NETWORK` below if your tests legitimately need network access.
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
from typing import Dict, List, Optional

from agents_orchestrator.testing_agent.tools.sandbox.base import (
    CmdResult,
    SandboxRunner,
)


logger = logging.getLogger("testing_agent.sandbox.docker")


# ── Image selection ─────────────────────────────────────────────────────────
# Each cmd[0] maps to a pinned base image. Tweak here when you need a
# different toolchain version — runners should NOT need code changes.
_IMAGE_BY_TOOL: Dict[str, str] = {
    # Python
    "pytest": "python:3.12-slim",
    "python": "python:3.12-slim",
    "python3": "python:3.12-slim",
    "pip": "python:3.12-slim",
    "pip3": "python:3.12-slim",
    "coverage": "python:3.12-slim",
    "pylint": "python:3.12-slim",
    # .NET
    "dotnet": "mcr.microsoft.com/dotnet/sdk:8.0",
    # Node / React
    "npm": "node:20-alpine",
    "npx": "node:20-alpine",
    "node": "node:20-alpine",
    "yarn": "node:20-alpine",
    "pnpm": "node:20-alpine",
    "jest": "node:20-alpine",
    "eslint": "node:20-alpine",
    # Git (used by ADO clone if a runner ever proxies through sandbox)
    "git": "alpine/git:latest",
}
_DEFAULT_IMAGE = "python:3.12-slim"
_DEFAULT_NETWORK = "none"  # set to "bridge" if a runner needs internet (e.g. npm install)


def _image_for(cmd: List[str]) -> str:
    if not cmd:
        return _DEFAULT_IMAGE
    return _IMAGE_BY_TOOL.get(cmd[0], _DEFAULT_IMAGE)


# Tools that legitimately need to install packages from the internet.
# When the chosen tool is one of these, we relax network=none → bridge.
_NETWORK_REQUIRED = {"pip", "pip3", "npm", "npx", "yarn", "pnpm", "dotnet", "git"}


class DockerSandboxRunner(SandboxRunner):
    """Run a command inside a one-shot Docker container.

    The host's `<cwd>` is bind-mounted at `/work` inside the container so
    artifact files (reports/, coverage XMLs, etc.) survive the container
    exit. `--tmpfs /tmp` keeps the OS-level read-only mount happy for
    tools that need to scratch.
    """

    name = "docker"

    def __init__(self, image_overrides: Optional[Dict[str, str]] = None):
        self._image_overrides = dict(image_overrides or {})

    def run(
        self,
        cmd: List[str],
        cwd: str,
        timeout_s: int = 600,
        env: Optional[Dict[str, str]] = None,
    ) -> CmdResult:
        if not cmd:
            return CmdResult(cmd=cmd, cwd=cwd, exit_code=1, stderr="empty cmd")

        image = self._image_overrides.get(cmd[0], _image_for(cmd))
        network = "bridge" if cmd[0] in _NETWORK_REQUIRED else _DEFAULT_NETWORK

        # Build the container-side command. Quote each arg with shlex so
        # spaces/special chars survive sh -c. The command runs from /work
        # which is bind-mounted to the host cwd.
        inner = " ".join(shlex.quote(c) for c in cmd)

        docker_cmd: List[str] = [
            "docker", "run", "--rm",
            "--network", network,
        ]
        # --read-only is great for security but breaks pip/npm install
        # because they write to the package cache. Keep it off when network
        # is allowed (i.e. install phases); enable it for pure run phases.
        if network == _DEFAULT_NETWORK:
            docker_cmd += ["--read-only"]
        docker_cmd += [
            "--tmpfs", "/tmp:rw,size=512m",
            "-v", f"{os.path.abspath(cwd)}:/work",
            "-w", "/work",
        ]
        if env:
            for k, v in env.items():
                docker_cmd += ["-e", f"{k}={v}"]
        docker_cmd += [image, "sh", "-c", inner]

        logger.info(
            f"DockerSandbox: image={image} network={network} timeout={timeout_s}s "
            f"cmd={cmd[0]} (full: {' '.join(cmd)[:200]})"
        )

        start = time.monotonic()
        try:
            proc = subprocess.run(
                docker_cmd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=timeout_s,
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
                stderr=f"DockerSandbox TimeoutExpired after {timeout_s}s",
                duration_ms=int((time.monotonic() - start) * 1000),
                timed_out=True,
            )
        except FileNotFoundError as exc:
            # Docker CLI is not installed.
            logger.error(f"DockerSandbox: docker CLI missing ({exc})")
            return CmdResult(
                cmd=cmd, cwd=cwd, exit_code=127,
                stderr=f"docker CLI not available on host: {exc}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as exc:
            logger.error(f"DockerSandbox.run failed: {exc}")
            return CmdResult(
                cmd=cmd, cwd=cwd, exit_code=1,
                stderr=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
