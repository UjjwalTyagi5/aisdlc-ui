"""ReactRunner — Phase M.4.

Wraps `npm install` + `jest --coverage` + `eslint` to give React (JSX/TSX)
parity with PythonRunner / DotnetRunner.

Notes:
- Host needs Node + npm. Phase S Docker option removes that requirement.
- jest --coverage --coverageReporters=cobertura emits
  `coverage/cobertura-coverage.xml`; we copy it to `reports/coverage.xml`
  so artifact_builder._parse_coverage_xml works unchanged.
- jest-junit emits a JUnit XML at `junit.xml` (config below); we copy to
  `reports/results.xml`. parse_test_results sets framework="jest".
"""
from __future__ import annotations

import json
import os
import re
import shutil
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


# Pulls top-level function declarations + arrow-fn assignments + class declarations
# from JSX/TSX. Captures the identifier in group(1).
_FN_DECL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?function\s+(\w+)\s*\(",
    re.MULTILINE,
)
_ARROW_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>",
    re.MULTILINE,
)
_CLASS_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?class\s+(\w+)",
    re.MULTILINE,
)
# JSX component shorthand: `const Foo = (...) => (...)` — already covered by _ARROW_RE.


class ReactRunner(LanguageRunner):
    name = "react"
    framework = "jest"
    runner_command = (
        "npx jest --coverage --coverageReporters=cobertura "
        "--reporters=default --reporters=jest-junit"
    )

    # ── Detection ──────────────────────────────────────────────────────────
    def detect(self, work_dir: str) -> bool:
        """React wins on a package.json containing 'react' OR loose JSX/TSX files."""
        pkg = os.path.join(work_dir, "package.json")
        if os.path.exists(pkg):
            try:
                with open(pkg, "r", encoding="utf-8") as f:
                    data = json.load(f)
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                if "react" in deps or "next" in deps:
                    return True
            except Exception:
                pass
        for root, _, files in os.walk(work_dir):
            if any(skip in root for skip in ("node_modules",)):
                continue
            for f in files:
                if f.endswith((".jsx", ".tsx")):
                    return True
        return False

    # ── Single-file workspace generation ───────────────────────────────────
    def setup_single_file_workspace(self, src_file: str, work_dir: str) -> None:
        """Generate package.json + babel + jest config + put the source in src/."""
        src_dir = os.path.join(work_dir, "src")
        os.makedirs(src_dir, exist_ok=True)
        shutil.copy(src_file, os.path.join(src_dir, os.path.basename(src_file)))

        with open(os.path.join(work_dir, "package.json"), "w") as f:
            f.write(_PACKAGE_JSON)
        with open(os.path.join(work_dir, "babel.config.js"), "w") as f:
            f.write(_BABEL_CONFIG)
        with open(os.path.join(work_dir, "jest.config.js"), "w") as f:
            f.write(_JEST_CONFIG)

    # ── Regex-based scan ───────────────────────────────────────────────────
    def scan_files(self, work_dir: str) -> List[ScannedFunction]:
        scanned: List[ScannedFunction] = []
        for root, _, files in os.walk(work_dir):
            if any(skip in root for skip in ("node_modules", "coverage", "dist", "build")):
                continue
            for file in files:
                if not file.endswith((".jsx", ".tsx", ".js", ".ts")):
                    continue
                # Skip test files themselves.
                if any(file.endswith(suffix) for suffix in (".test.jsx", ".test.tsx", ".test.js", ".test.ts")):
                    continue
                if any(file.endswith(suffix) for suffix in (".spec.jsx", ".spec.tsx", ".spec.js", ".spec.ts")):
                    continue
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, work_dir)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    seen = set()
                    for pat in (_FN_DECL_RE, _ARROW_RE, _CLASS_RE):
                        for m in pat.finditer(content):
                            name = m.group(1)
                            if name in seen:
                                continue
                            seen.add(name)
                            start_line = content[: m.start()].count("\n")
                            lines = content.splitlines()
                            block = "\n".join(lines[start_line : min(start_line + 30, len(lines))])
                            scanned.append(ScannedFunction(
                                file_path=rel_path,
                                function_name=name,
                                code_block=block,
                            ))
                except Exception as exc:
                    logger.warning(f"ReactRunner.scan_files: error on {rel_path}: {exc}")
        return scanned

    # ── Subprocess phases ──────────────────────────────────────────────────
    def _is_cra_project(self, work_dir: str) -> bool:
        """Detect a Create-React-App project — its `test` script will reference
        react-scripts. CRA bundles its own jest config (testEnvironment, transformIgnorePatterns,
        setupFilesAfterEach, …) which raw `npx jest` cannot see, so we have to dispatch
        through `npm test` instead."""
        pkg = os.path.join(work_dir, "package.json")
        if not os.path.exists(pkg):
            return False
        try:
            with open(pkg, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False
        scripts = data.get("scripts") or {}
        test_script = (scripts.get("test") or "")
        return "react-scripts" in test_script

    def install(self, work_dir: str, sandbox: SandboxRunner) -> CmdResult:
        # Prefer `npm ci` if package-lock.json exists, else `npm install`.
        # Either way we ALWAYS need `jest-junit` for our reports/results.xml — it's
        # not in most repos' deps, so install it on the side after the main install.
        if os.path.exists(os.path.join(work_dir, "package-lock.json")):
            main = sandbox.run(["npm", "ci"], cwd=work_dir, timeout_s=900)
        else:
            main = sandbox.run(["npm", "install"], cwd=work_dir, timeout_s=900)
        if not main.ok:
            return main
        # ONE CALL, NOT TWO. `npm install --no-save <pkg>` reconciles node_modules
        # against package.json, which PRUNES anything previously installed --no-save:
        # a second such call silently removed the jest-junit installed by the first,
        # and the run died on "Could not resolve a module for a custom reporter:
        # jest-junit" after appearing to install it. Everything the harness adds goes
        # in together.
        self._ensure_harness_deps(work_dir, sandbox)
        return main

    # Packages the HARNESS needs that the repo under test has no reason to carry:
    #   · jest-junit — our reports/results.xml comes from this reporter
    #   · @testing-library/* — skills/unit/SKILL.md tells the model to write
    #     "jest + react-testing-library" tests, so the generated file imports it.
    #     The runner installed only jest-junit, so every generated suite died on
    #     "Cannot find module '@testing-library/react'" and the run said "No tests
    #     collected" — blaming the repo for a dependency the agent's own prompt
    #     asked for. The .NET runner already injects its equivalents.
    #   · jest-environment-jsdom — component tests need a DOM; jest 28+ unbundled it.
    _HARNESS_DEPS = (
        "jest-junit",
        "@testing-library/react",
        "@testing-library/jest-dom",
        # code_gen_prompt tells the model to drive interactions with userEvent, and
        # the single-file template lists it — but a cloned repo need not have it.
        "@testing-library/user-event",
        "jest-environment-jsdom",
    )

    def _ensure_harness_deps(self, work_dir: str, sandbox: SandboxRunner) -> None:
        """Install the packages our reporter and the generated tests import.

        --no-save so the repo's package.json is never rewritten: the agent is testing
        this checkout, not modifying it (and for a cloned repo those edits could end
        up in the tests PR). A failure is logged and swallowed — a repo that already
        has these still runs fine.
        """
        node_modules = os.path.join(work_dir, "node_modules")
        missing = [
            pkg for pkg in self._HARNESS_DEPS
            if not os.path.isdir(os.path.join(node_modules, *pkg.split("/")))
        ]
        if not missing:
            logger.info("react: harness deps already present")
            return
        logger.info("react: installing harness deps %s", ", ".join(missing))
        res = sandbox.run(
            ["npm", "install", "--no-save", "--no-audit", *missing],
            cwd=work_dir, timeout_s=600,
        )
        if not res.ok:
            logger.warning(
                "react: could not install %s (tests importing them will fail to "
                "run): %s", ", ".join(missing), (res.stderr or "")[:300],
            )

    def run_tests(self, work_dir: str, sandbox: SandboxRunner) -> CmdResult:
        os.makedirs(os.path.join(work_dir, "reports"), exist_ok=True)

        # Phase 8.10d — defensive: ensure node_modules + the runner binary
        # exist before dispatching tests. install() was supposed to populate
        # them, but the user reported "react-scripts: command not found"
        # propagating to chat — meaning either install() failed silently in a
        # different sandbox state or its output got lost. Re-check on disk
        # and re-run install with the failure surfaced cleanly so the chat
        # response shows WHY tests couldn't run instead of a cryptic shell
        # error from missing-binary land.
        is_cra = self._is_cra_project(work_dir)
        node_modules = os.path.join(work_dir, "node_modules")
        cra_bin = os.path.join(node_modules, ".bin", "react-scripts")
        jest_bin = os.path.join(node_modules, ".bin", "jest")
        needs_install = (
            not os.path.isdir(node_modules)
            or (is_cra and not os.path.isfile(cra_bin))
            or (not is_cra and not os.path.isfile(jest_bin))
        )
        if needs_install:
            install_cmd = (
                ["npm", "ci", "--prefer-offline", "--no-audit"]
                if os.path.isfile(os.path.join(work_dir, "package-lock.json"))
                else ["npm", "install", "--no-audit"]
            )
            logger.info(
                f"ReactRunner: pre-test guard found missing binary "
                f"({'react-scripts' if is_cra else 'jest'}); re-running install"
            )
            install_res = sandbox.run(install_cmd, cwd=work_dir, timeout_s=900)
            if install_res.exit_code != 0:
                # Surface the install failure as the test result so the chat
                # response carries the actionable error instead of a cryptic
                # 'react-scripts: command not found' from the test command.
                return CmdResult(
                    cmd=install_cmd,
                    cwd=work_dir,
                    exit_code=install_res.exit_code,
                    stdout=install_res.stdout,
                    stderr=(
                        "npm install failed before tests could run. "
                        "This usually means the cloned repo's package.json is "
                        "incompatible with the local Node version, or there are "
                        "peer-dependency conflicts.\n\n"
                        "--- npm stderr (last 800 chars) ---\n"
                        f"{(install_res.stderr or '')[-800:]}"
                    ),
                    duration_ms=install_res.duration_ms,
                )

        # Bug-fix: CRA projects (react-scripts) ship their own jest config that
        # raw `npx jest` can't see. Dispatch via `npm test --` for those; raw
        # jest for everything else. jest-junit output gets captured via env
        # vars (since CLI args don't always make it through CRA's wrapper).
        env = {
            "CI": "true",
            "JEST_JUNIT_OUTPUT_DIR": ".",
            "JEST_JUNIT_OUTPUT_NAME": "junit.xml",
        }
        if is_cra:
            cmd = [
                "npm", "test", "--",
                "--watchAll=false",
                "--coverage",
                "--coverageReporters=cobertura",
                "--reporters=default",
                "--reporters=jest-junit",
            ]
            self.runner_command = " ".join(cmd) + " (CRA path)"
        else:
            cmd = [
                "npx", "jest",
                "--coverage",
                "--coverageReporters=cobertura",
                "--reporters=default",
                "--reporters=jest-junit",
            ]

        result = sandbox.run(cmd, cwd=work_dir, timeout_s=900, env=env)
        # Normalise output paths so artifact_builder finds them.
        self._normalise_paths(work_dir)
        return result

    def parse_coverage(self, work_dir: str) -> Optional[CoverageSummary]:
        path = os.path.join(work_dir, "reports", "coverage.xml")
        if not os.path.exists(path):
            return None
        return _parse_coverage_xml(path)

    def parse_test_results(self, work_dir: str) -> Optional[TestExecution]:
        path = os.path.join(work_dir, "reports", "results.xml")
        if not os.path.exists(path):
            return None
        exec_ = _parse_junit_xml(path)
        if exec_ is not None:
            exec_.framework = "jest"
        return exec_

    def parse_lint(self, work_dir: str, sandbox: SandboxRunner) -> LintReport:
        # ESLint config is optional. If missing, return a soft "skipped" report.
        eslint_config = any(
            os.path.exists(os.path.join(work_dir, name))
            for name in (".eslintrc", ".eslintrc.js", ".eslintrc.json", "eslint.config.js")
        )
        if not eslint_config:
            return LintReport(tool="eslint", output="(no eslint config — skipped)", exit_code=0)
        result = sandbox.run(
            ["npx", "eslint", ".", "-f", "json"],
            cwd=work_dir,
            timeout_s=120,
        )
        return LintReport(
            tool="eslint",
            output=(result.stdout or "") + ("\n" + result.stderr if result.stderr else ""),
            exit_code=result.exit_code,
        )

    # ── Helpers ────────────────────────────────────────────────────────────
    def _normalise_paths(self, work_dir: str) -> None:
        cov = os.path.join(work_dir, "coverage", "cobertura-coverage.xml")
        if os.path.isfile(cov):
            dest = os.path.join(work_dir, "reports", "coverage.xml")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy(cov, dest)
        # jest-junit default outputs `junit.xml` in cwd
        junit = os.path.join(work_dir, "junit.xml")
        if os.path.isfile(junit):
            dest = os.path.join(work_dir, "reports", "results.xml")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy(junit, dest)

    def code_gen_prompt(self, scanned: List[ScannedFunction]) -> str:
        components_by_file = {}
        for fn in scanned:
            components_by_file.setdefault(fn.file_path, []).append(fn.function_name)
        target_summary = "\n".join(
            f"  - {path}: {', '.join(names)}"
            for path, names in components_by_file.items()
        )
        return (
            "Write a jest + @testing-library/react test file for the following component analysis.\n"
            "\n"
            "## CRITICAL CONVENTIONS — read carefully\n"
            "The test file you generate will live next to the source files in `src/`. Use direct\n"
            "imports of the components/functions exactly as exported. Do NOT use jest.mock() to\n"
            "stub the components themselves. Do NOT generate placeholder fallbacks.\n"
            "\n"
            "## Test structure rules\n"
            # Note: literal `{` and `}` MUST be doubled for langchain's f-string
            # template parser (otherwise it tries to bind `{ ... }` as a variable
            # and raises KeyError at format time).
            "- `describe('<Component>', () => {{ ... }})` blocks per component/function.\n"
            "- For React components: render with `render(<Foo .../>)`, query via `screen.getByRole`,\n"
            "  `getByText`, `getByLabelText` (semantic queries — do NOT use getByTestId unless\n"
            "  it's the only stable handle).\n"
            "- For interactions: `userEvent` from `@testing-library/user-event`.\n"
            "- For pure helper functions: call directly + assert with `expect(...).toBe()` /\n"
            "  `.toEqual()` / `.toThrow()`.\n"
            "- Cover happy paths, edge cases, AND error/disabled states.\n"
            "\n"
            "## Output\n"
            "Output ONLY raw JS/TS code (no markdown fences, no commentary). Start with imports\n"
            "from `@testing-library/react`, the components under test, and `userEvent`.\n"
            "\n"
            "## Components / functions under test\n"
            f"{target_summary}\n"
        )


# ── Templates for setup_single_file_workspace ───────────────────────────────
_PACKAGE_JSON = """{
  "name": "react-testing-workspace",
  "version": "0.0.0",
  "private": true,
  "scripts": {
    "test": "jest --coverage --coverageReporters=cobertura --reporters=default --reporters=jest-junit"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@babel/preset-env": "^7.25.4",
    "@babel/preset-react": "^7.24.7",
    "@testing-library/jest-dom": "^6.5.0",
    "@testing-library/react": "^16.0.1",
    "@testing-library/user-event": "^14.5.2",
    "babel-jest": "^29.7.0",
    "jest": "^29.7.0",
    "jest-environment-jsdom": "^29.7.0",
    "jest-junit": "^16.0.0"
  }
}
"""

_BABEL_CONFIG = """module.exports = {
  presets: [
    ['@babel/preset-env', { targets: { node: 'current' } }],
    ['@babel/preset-react', { runtime: 'automatic' }],
  ],
};
"""

_JEST_CONFIG = """module.exports = {
  testEnvironment: 'jsdom',
  testMatch: ['**/src/**/*.{test,spec}.{js,jsx,ts,tsx}'],
  setupFilesAfterEach: [],
  setupFilesAfterTests: [],
  setupFiles: [],
  setupFilesAfterSetup: [],
  // jest-junit picks up env vars or this option:
  reporters: [
    'default',
    ['jest-junit', { outputDirectory: '.', outputName: 'junit.xml' }],
  ],
};
"""
