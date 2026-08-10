"""LanguageRunner factory — Phase M.

Resolution order:
  1. Explicit name ("python" | "dotnet" | "react") → that runner.
  2. Auto-detect by walking work_dir (Phase M.5 calls this).

DotnetRunner (Phase M.3) and ReactRunner (Phase M.4) register here when
their files land — until then, only Python is registered.
"""
from __future__ import annotations

from typing import Dict, Optional, Type

from agents_orchestrator.testing_agent.tools.language_runner import LanguageRunner
from agents_orchestrator.testing_agent.tools.runners.python import PythonRunner

_REGISTRY: Dict[str, Type[LanguageRunner]] = {
    "python": PythonRunner,
}

# Phase M.3 — DotnetRunner
try:
    from agents_orchestrator.testing_agent.tools.runners.dotnet import DotnetRunner
    _REGISTRY["dotnet"] = DotnetRunner
except ImportError:
    pass

# Phase M.4 — ReactRunner
try:
    from agents_orchestrator.testing_agent.tools.runners.react import ReactRunner
    _REGISTRY["react"] = ReactRunner
except ImportError:
    pass


def register_runner(name: str, cls: Type[LanguageRunner]) -> None:
    """Add or replace an entry in the runner registry. Used by Phase M.3/M.4."""
    _REGISTRY[name.lower()] = cls


def get_runner(name: str) -> LanguageRunner:
    """Instantiate a runner by name. Raises KeyError if not registered."""
    cls = _REGISTRY.get(name.lower())
    if cls is None:
        raise KeyError(f"No LanguageRunner registered for '{name}' (available: {list(_REGISTRY)})")
    return cls()


def detect_runner(work_dir: str) -> Optional[LanguageRunner]:
    """Walk work_dir and return the first runner whose detect() matches.

    Priority order matches plan §M.5:
      1. dotnet (*.csproj / *.sln)
      2. react (package.json with react)
      3. python (any *.py file)

    Phase M.5 will call this from Nodes/detect_language.py.
    """
    priority = ("dotnet", "react", "python")
    for name in priority:
        cls = _REGISTRY.get(name)
        if cls is None:
            continue
        runner = cls()
        try:
            if runner.detect(work_dir):
                return runner
        except Exception:
            continue
    return None
