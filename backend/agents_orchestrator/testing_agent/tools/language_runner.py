"""LanguageRunner abstraction — Phase M.1.

Each language (Python, .NET, React) implements this interface so the
testing-agent graph can dispatch through `state["runner"]` without
hard-coding pytest / dotnet / jest in the Node modules.

Phase M.5 wires the dispatch (via `Nodes/detect_language.py`); the
runners themselves can be unit-tested in isolation before then.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from shared.models.testing import CoverageSummary, TestExecution

from agents_orchestrator.testing_agent.tools.sandbox.base import (
    CmdResult,
    SandboxRunner,
)


logger = logging.getLogger("testing_agent.runners")


@dataclass
class LintReport:
    """Free-form lint output the runner exposes; no scoring required for MVP."""
    tool: str = ""
    output: str = ""
    exit_code: int = 0


@dataclass
class ScannedFunction:
    """One function/class/component the planner LLM should generate tests for.

    `code_block` is the raw source segment so prompts can include it inline;
    keep it bounded by truncating at the runner level if necessary.
    """
    file_path: str        # repo-relative
    function_name: str
    code_block: str


class LanguageRunner(ABC):
    """One implementation per supported language.

    Phase M.2 — PythonRunner (this file's first concrete sibling)
    Phase M.3 — DotnetRunner
    Phase M.4 — ReactRunner

    Method contract for downstream nodes:
      * `detect(work_dir)` is called by `Nodes/detect_language.py` to choose
        which runner to instantiate for a given workspace.
      * `setup_single_file_workspace(src, work_dir)` mirrors the Phase 2
        single-file path — generate any project files needed to run tests.
      * `scan_files(work_dir)` returns the list of analysable units the
        planner should target.
      * `install + run_tests + parse_coverage + parse_lint` are the
        subprocess phases — every shell-out goes through the supplied
        SandboxRunner so Phase S can wrap everything in Docker without
        changing the runners.
      * `code_gen_prompt()` returns the language-specific test-generation
        prompt (Python: pytest+CRITICAL IMPORT RULES; .NET: xUnit;
        React: jest+RTL).
    """

    name: str = "abstract"
    framework: str = "abstract"  # "pytest" | "xunit" | "jest"
    runner_command: str = ""     # e.g. "pytest --junitxml=… --cov-report=xml"

    @abstractmethod
    def detect(self, work_dir: str) -> bool:
        ...

    @abstractmethod
    def setup_single_file_workspace(self, src_file: str, work_dir: str) -> None:
        ...

    @abstractmethod
    def scan_files(self, work_dir: str) -> List[ScannedFunction]:
        ...

    @abstractmethod
    def install(self, work_dir: str, sandbox: SandboxRunner) -> CmdResult:
        ...

    @abstractmethod
    def run_tests(self, work_dir: str, sandbox: SandboxRunner) -> CmdResult:
        ...

    @abstractmethod
    def parse_coverage(self, work_dir: str) -> Optional[CoverageSummary]:
        ...

    @abstractmethod
    def parse_test_results(self, work_dir: str) -> Optional[TestExecution]:
        ...

    @abstractmethod
    def parse_lint(self, work_dir: str, sandbox: SandboxRunner) -> LintReport:
        ...

    @abstractmethod
    def code_gen_prompt(self, scanned: List[ScannedFunction]) -> str:
        ...
