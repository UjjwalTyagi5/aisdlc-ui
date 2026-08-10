"""PythonRunner — Phase M.2.

Wraps the existing pytest + coverage + pylint flow that today lives in
`Nodes/execute.py:run_code_testing_agent` and
`tools/code_testing_agent.py`. No behaviour change for the Python path
when this is wired in by Phase M.5 — same commands, same XML outputs.
"""
from __future__ import annotations

import ast
import os
from typing import List, Optional

from shared.models.testing import CoverageSummary, TestExecution

from agents_orchestrator.testing_agent.config.shared import logger
from agents_orchestrator.testing_agent.tools.artifact_builder import (
    _parse_coverage_xml,
    _parse_junit_xml,
)
from agents_orchestrator.testing_agent.tools.language_runner import (
    LanguageRunner,
    LintReport,
    ScannedFunction,
)
from agents_orchestrator.testing_agent.tools.sandbox.base import (
    CmdResult,
    SandboxRunner,
)


class PythonRunner(LanguageRunner):
    name = "python"
    framework = "pytest"
    runner_command = "pytest --junitxml=reports/results.xml --cov=. --cov-report=xml:reports/coverage.xml"

    # ── Detection ──────────────────────────────────────────────────────────
    def detect(self, work_dir: str) -> bool:
        """Python wins on any of: pyproject.toml, requirements.txt, or *.py files."""
        if os.path.exists(os.path.join(work_dir, "pyproject.toml")):
            return True
        if os.path.exists(os.path.join(work_dir, "requirements.txt")):
            return True
        for root, _, files in os.walk(work_dir):
            if any(f.endswith(".py") for f in files):
                return True
        return False

    # ── Single-file workspace (mirrors Phase 2 setup_single_file_workspace) ─
    def setup_single_file_workspace(self, src_file: str, work_dir: str) -> None:
        """Drop a .py file in `work_dir` along with conftest.py, pytest.ini,
        and an empty requirements.txt so pytest can collect + run."""
        import shutil
        dst = os.path.join(work_dir, os.path.basename(src_file))
        shutil.copy(src_file, dst)
        with open(os.path.join(work_dir, "conftest.py"), "w") as f:
            f.write(
                "import sys, os\n"
                "_HERE = os.path.dirname(os.path.abspath(__file__))\n"
                "if _HERE not in sys.path:\n"
                "    sys.path.insert(0, _HERE)\n"
            )
        with open(os.path.join(work_dir, "pytest.ini"), "w") as f:
            f.write("[pytest]\nrootdir = .\n")
        with open(os.path.join(work_dir, "requirements.txt"), "w") as f:
            f.write("")

    # ── AST scan (mirrors Nodes/analyze.py:_scan_python_files) ──────────────
    def scan_files(self, work_dir: str) -> List[ScannedFunction]:
        scanned: List[ScannedFunction] = []
        for root, _, files in os.walk(work_dir):
            if "venv" in root:
                continue
            for file in files:
                if not file.endswith(".py"):
                    continue
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, work_dir)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    tree = ast.parse(content)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.FunctionDef):
                            code_block = ast.get_source_segment(content, node)
                            if code_block:
                                scanned.append(ScannedFunction(
                                    file_path=rel_path,
                                    function_name=node.name,
                                    code_block=code_block,
                                ))
                except SyntaxError:
                    logger.warning(f"PythonRunner: could not parse {rel_path}, skipping")
                except Exception as exc:
                    logger.warning(f"PythonRunner.scan_files: error on {rel_path}: {exc}")
        return scanned

    # ── Install + run tests + parse outputs ────────────────────────────────
    def install(self, work_dir: str, sandbox: SandboxRunner) -> CmdResult:
        # Ensure pytest+coverage tooling is present. Defensive: write a baseline
        # requirements.txt if the user's repo doesn't have one.
        req_path = os.path.join(work_dir, "requirements.txt")
        if not os.path.exists(req_path):
            with open(req_path, "w") as f:
                f.write("pytest\npylint\npytest-cov\n")
        return sandbox.run(
            ["pip", "install", "-r", "requirements.txt", "pytest", "pytest-cov", "pylint"],
            cwd=work_dir,
            timeout_s=600,
        )

    def run_tests(self, work_dir: str, sandbox: SandboxRunner) -> CmdResult:
        os.makedirs(os.path.join(work_dir, "reports"), exist_ok=True)
        return sandbox.run(
            [
                "pytest",
                "--junitxml=reports/results.xml",
                "--cov=.",
                "--cov-report=xml:reports/coverage.xml",
            ],
            cwd=work_dir,
            timeout_s=600,
        )

    def parse_coverage(self, work_dir: str) -> Optional[CoverageSummary]:
        path = os.path.join(work_dir, "reports", "coverage.xml")
        if not os.path.exists(path):
            return None
        return _parse_coverage_xml(path)

    def parse_test_results(self, work_dir: str) -> Optional[TestExecution]:
        path = os.path.join(work_dir, "reports", "results.xml")
        if not os.path.exists(path):
            return None
        return _parse_junit_xml(path)

    def parse_lint(self, work_dir: str, sandbox: SandboxRunner) -> LintReport:
        result = sandbox.run(["pylint", "."], cwd=work_dir, timeout_s=120)
        out = result.stdout if result.stdout else "Pylint ran with no output."
        if result.stderr:
            out += f"\n\nERRORS:\n{result.stderr}"
        return LintReport(tool="pylint", output=out, exit_code=result.exit_code)

    # ── Code-gen prompt (Phase 1.5 prompt verbatim) ────────────────────────
    def code_gen_prompt(self, scanned: List[ScannedFunction]) -> str:
        modules_to_funcs: dict = {}
        for fn in scanned:
            mod = os.path.splitext(os.path.basename(fn.file_path))[0]
            modules_to_funcs.setdefault(mod, []).append(fn.function_name)
        import_lines = "\n".join(
            f"from {mod} import " + ", ".join(funcs)
            for mod, funcs in modules_to_funcs.items()
        )
        return (
            "Write a pytest file for the following code analysis.\n"
            "\n"
            "## CRITICAL IMPORT RULES — read carefully\n"
            "The test file you generate will be saved as `test_generated_by_agent.py` IN THE SAME DIRECTORY\n"
            "as the source files. A `conftest.py` next to the test file already adds that directory to\n"
            "`sys.path`. So you MUST import the functions under test using DIRECT imports at the top of\n"
            "the file, exactly like this:\n"
            "\n"
            "```\n"
            f"{import_lines}\n"
            "```\n"
            "\n"
            "Do NOT use `importlib`, `__import__`, `pytest.importorskip`, or any try/except import block.\n"
            "Do NOT generate stub fallback functions like `def add(*args, **kwargs): pytest.fail(...)`.\n"
            "Do NOT use `sys.path.append(...)` — the conftest already handles paths.\n"
            "If the imports fail at collection time, that's a real bug — let pytest surface it.\n"
            "\n"
            "## Test structure rules\n"
            "- Use plain `def test_xxx():` functions (or `class TestXxx:` with methods).\n"
            "- Each test calls the imported function directly with concrete inputs and asserts the result.\n"
            "- For functions that should raise, use `with pytest.raises(ExpectedException):`.\n"
            "- Cover happy paths, edge cases, AND error cases for every function in the analysis.\n"
            "- Keep tests independent — no shared mutable state between them.\n"
            "\n"
            "## Output\n"
            "Output ONLY raw Python code (no markdown fences, no commentary). Start with `import pytest`,\n"
            "followed by the import lines above, followed by the test functions.\n"
        )
