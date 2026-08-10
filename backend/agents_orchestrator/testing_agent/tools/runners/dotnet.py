"""DotnetRunner — Phase M.3.

Wraps `dotnet restore` + `dotnet test` (coverlet → Cobertura) +
`dotnet format` to give the testing agent .NET (C#) coverage parity
with PythonRunner.

Notes:
- The host must have the `dotnet` SDK installed (Phase S Docker option
  removes this requirement).
- coverlet's default Cobertura output path is
  `<work_dir>/TestResults/<guid>/coverage.cobertura.xml`. We normalise
  it to `<work_dir>/reports/coverage.xml` after `run_tests` so the
  existing artifact_builder._parse_coverage_xml works unchanged.
- `scan_files` is regex-based for MVP (no native C# AST in Python). The
  list is good enough for the planner LLM to seed test cases; Phase M.5
  may replace with an LLM-based extractor for richer signatures.
"""
from __future__ import annotations

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


# Matches `public/internal/private [static] [async] <ReturnType> Name(...)`.
# Captures (modifiers, return_type, name).
_METHOD_RE = re.compile(
    r"^\s*(?:public|internal|private|protected)\s+(?:static\s+)?(?:async\s+)?"
    r"([\w<>\[\]\?\s,]+?)\s+"
    r"(\w+)\s*\([^)]*\)\s*\{?\s*$",
    re.MULTILINE,
)


class DotnetRunner(LanguageRunner):
    name = "dotnet"
    framework = "xunit"
    runner_command = (
        'dotnet test --collect:"XPlat Code Coverage" '
        '--logger "junit;LogFilePath=reports/results.xml" '
        '--results-directory ./TestResults'
    )

    # ── Detection ──────────────────────────────────────────────────────────
    def detect(self, work_dir: str) -> bool:
        for root, _, files in os.walk(work_dir):
            for f in files:
                if f.endswith((".csproj", ".sln", ".cs")):
                    return True
        return False

    # ── Single-file workspace generation ───────────────────────────────────
    def setup_single_file_workspace(self, src_file: str, work_dir: str) -> None:
        """Build a minimal solution: src project + test project + ProjectReference + .sln.

        Layout:
          <work_dir>/
          ├── <base>.sln                 (so `dotnet restore`/`dotnet test` work at the root)
          ├── src/<base>.csproj
          │   └── <base>.cs              (the user's source)
          ├── tests/<base>.Tests.csproj
          │   └── (test code dropped here later by save_generated_code)
          └── coverlet.runsettings
        """
        import uuid
        base = os.path.splitext(os.path.basename(src_file))[0]
        src_dir = os.path.join(work_dir, "src")
        tests_dir = os.path.join(work_dir, "tests")
        os.makedirs(src_dir, exist_ok=True)
        os.makedirs(tests_dir, exist_ok=True)

        # Copy source into src/
        shutil.copy(src_file, os.path.join(src_dir, os.path.basename(src_file)))

        # src/<base>.csproj — netstandard2.1 library
        with open(os.path.join(src_dir, f"{base}.csproj"), "w") as f:
            f.write(_SRC_CSPROJ)

        # tests/<base>.Tests.csproj — xunit + coverlet.collector + ProjectReference
        with open(os.path.join(tests_dir, f"{base}.Tests.csproj"), "w") as f:
            f.write(_TESTS_CSPROJ.format(base=base))

        # coverlet.runsettings — Cobertura format
        with open(os.path.join(work_dir, "coverlet.runsettings"), "w") as f:
            f.write(_COVERLET_RUNSETTINGS)

        # Solution file at work_dir root — required so `dotnet restore` / `dotnet test`
        # invoked from work_dir know which projects to build (otherwise MSB1003).
        # GUIDs are required by the .sln format; deterministic per-base for stability.
        src_guid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{base}-src")).upper()
        tests_guid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{base}-tests")).upper()
        with open(os.path.join(work_dir, f"{base}.sln"), "w") as f:
            f.write(_SOLUTION_TEMPLATE.format(
                base=base, src_guid=src_guid, tests_guid=tests_guid,
            ))

    # ── Regex-based scan ───────────────────────────────────────────────────
    def scan_files(self, work_dir: str) -> List[ScannedFunction]:
        scanned: List[ScannedFunction] = []
        for root, _, files in os.walk(work_dir):
            root_parts = {part.lower() for part in os.path.normpath(root).split(os.sep)}
            if (
                "bin" in root_parts
                or "obj" in root_parts
                or "migrations" in root_parts
                or any(part in {"tests", "test"} or part.endswith(".tests") for part in root_parts)
            ):
                continue
            for file in files:
                if not file.endswith(".cs"):
                    continue
                if file.lower().endswith(("test.cs", "tests.cs")):
                    continue
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, work_dir)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    # Extract the file header (using directives + namespace declaration)
                    # so each scanned function's code_block carries enough context for
                    # the LLM to generate `using <Namespace>;` correctly. Without this
                    # the prompt sees only the method body and picks the class name as
                    # the namespace, breaking the build.
                    header_lines: List[str] = []
                    for line in content.splitlines():
                        stripped = line.strip()
                        if stripped.startswith(("using ", "namespace ", "//")):
                            header_lines.append(line)
                        elif stripped.startswith(("{", "}")) and len(header_lines) > 0:
                            header_lines.append(line)
                        elif stripped.startswith("public class") or stripped.startswith("internal class"):
                            # Stop once we hit a class — header ends here
                            break
                    file_header = "\n".join(header_lines).rstrip()
                    for m in _METHOD_RE.finditer(content):
                        return_type, method_name = m.group(1).strip(), m.group(2)
                        if method_name in {"if", "while", "for", "switch", "return"}:
                            continue
                        start_line = content[: m.start()].count("\n")
                        lines = content.splitlines()
                        body = "\n".join(lines[start_line : min(start_line + 30, len(lines))])
                        # Prepend the file header so the LLM sees the real namespace + usings.
                        block = (file_header + "\n// ... (method body) ...\n" + body) if file_header else body
                        scanned.append(ScannedFunction(
                            file_path=rel_path,
                            function_name=method_name,
                            code_block=block,
                        ))
                except Exception as exc:
                    logger.warning(f"DotnetRunner.scan_files: error on {rel_path}: {exc}")
        return scanned

    # ── Subprocess phases ──────────────────────────────────────────────────
    def install(self, work_dir: str, sandbox: SandboxRunner) -> CmdResult:
        # `dotnet restore` from work_dir fails with MSB1003 if there's no
        # .sln/.csproj at the root — true for cloned repos where projects
        # live under a subdir (e.g. carelon/`.net application/RadAuthPortal`).
        # Pass the discovered targets so restore knows what to build.
        targets = self._discover_test_targets(work_dir)
        result = sandbox.run(
            ["dotnet", "restore", *targets],
            cwd=work_dir, timeout_s=600,
        )
        if not result.ok:
            return result
        # Walk for ALL test csprojs (independent of `_discover_test_targets`,
        # which prefers a .sln when present and would skip the per-project
        # enumeration we need here). For each, inject two packages if missing:
        #
        # 1. JunitXml.TestLogger — required so we can parse results into our
        #    reports/results.xml. Single-file workspaces include it in the
        #    .csproj template; cloned repos usually don't.
        #
        # 2. coverlet.collector — required so `dotnet test --collect:"XPlat
        #    Code Coverage"` produces `coverage.cobertura.xml` (which our
        #    parser handles) instead of the binary `.coverage` format.
        #    Without this, coverage stats never appear in the chat summary.
        for r, _, files in os.walk(work_dir):
            if any(skip in r for skip in (os.sep + "bin", os.sep + "obj", os.sep + ".git")):
                continue
            for f in files:
                if not f.endswith(".csproj") or "test" not in f.lower():
                    continue
                tp = os.path.relpath(os.path.join(r, f), work_dir)
                for pkg, ver in (
                    ("JunitXml.TestLogger", "3.0.124"),
                    ("coverlet.collector", "6.0.4"),
                ):
                    # Skip injection if the csproj already references the package
                    # (avoid noisy logs + wasted NuGet roundtrips on single-file
                    # workspaces and repos that already have it).
                    try:
                        with open(os.path.join(work_dir, tp), "r", encoding="utf-8") as fh:
                            content = fh.read()
                        if pkg.lower() in content.lower():
                            logger.info(f"{pkg} already in {tp} — skipping inject")
                            continue
                    except Exception:
                        pass
                    add_result = sandbox.run(
                        ["dotnet", "add", tp, "package", pkg, "--version", ver],
                        cwd=work_dir, timeout_s=180,
                    )
                    if add_result.ok:
                        logger.info(f"Injected {pkg} into {tp}")
                    else:
                        logger.warning(f"Failed to inject {pkg} into {tp}: {(add_result.stderr or '')[:200]}")
        return result

    def run_tests(self, work_dir: str, sandbox: SandboxRunner) -> CmdResult:
        os.makedirs(os.path.join(work_dir, "reports"), exist_ok=True)
        # Target priority for `dotnet test` (avoids MSB1003 in cloned repos
        # that don't have a .sln at the root):
        #   1. .sln at work_dir root      ← created by setup_single_file_workspace
        #   2. **/*.Tests.csproj          ← typical test project naming
        #   3. **/*Tests.csproj           ← also covers UnitTests, IntegrationTests, etc.
        #   4. **/*.csproj                ← last resort: every csproj
        target_args = self._discover_test_targets(work_dir)
        # --settings only if coverlet.runsettings exists at root. Single-file
        # workspaces generate it; cloned repos usually don't have one (and we
        # shouldn't override the repo's own settings if they do). Without
        # settings, coverlet still produces a default cobertura XML, just at a
        # slightly different path which _normalise_coverage_path picks up.
        runsettings_args = []
        if os.path.exists(os.path.join(work_dir, "coverlet.runsettings")):
            runsettings_args = ["--settings", "coverlet.runsettings"]
        test_cmd = [
                "dotnet", "test",
                *target_args,
                "--collect:XPlat Code Coverage",
                *runsettings_args,
                "--logger", "junit;LogFilePath=reports/results.xml",
                "--results-directory", "./TestResults",
            ]
        result = sandbox.run(
            test_cmd,
            cwd=work_dir,
            timeout_s=900,
        )
        # coverlet writes coverage.cobertura.xml under TestResults/<guid>/.
        # Normalise to reports/coverage.xml so artifact_builder finds it.
        self._normalise_coverage_path(work_dir)
        self._write_command_log(work_dir, "dotnet_test_initial", result)

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
        # Same JUnit shape pytest produces — reuse the parser. Override the
        # framework label since dotnet doesn't run pytest.
        exec_ = _parse_junit_xml(path)
        if exec_ is not None:
            exec_.framework = "xunit"
        return exec_

    def parse_lint(self, work_dir: str, sandbox: SandboxRunner) -> LintReport:
        result = sandbox.run(
            ["dotnet", "format", "--verify-no-changes"],
            cwd=work_dir,
            timeout_s=120,
        )
        return LintReport(
            tool="dotnet format",
            output=(result.stdout or "") + ("\n" + result.stderr if result.stderr else ""),
            exit_code=result.exit_code,
        )

    # ── Helpers ────────────────────────────────────────────────────────────
    def _discover_test_targets(self, work_dir: str) -> List[str]:
        """Pick what to pass to `dotnet test` so it doesn't fail with MSB1003
        in cloned repos that have no .sln at the root.

        Returns a list of args (paths relative to work_dir) to splice into the
        command. Empty list means "let dotnet auto-discover" (fine when there's
        a .sln OR a single .csproj at work_dir root)."""
        # 1. .sln at root
        for f in os.listdir(work_dir):
            if f.endswith(".sln"):
                return [f]
        # 2/3. Test-project csprojs anywhere in the tree
        test_projects: List[str] = []
        all_csprojs: List[str] = []
        for r, _, files in os.walk(work_dir):
            if any(skip in r for skip in (os.sep + "bin", os.sep + "obj", os.sep + ".git")):
                continue
            for f in files:
                if f.endswith(".csproj"):
                    rel = os.path.relpath(os.path.join(r, f), work_dir)
                    all_csprojs.append(rel)
                    if "test" in f.lower():
                        test_projects.append(rel)
        if test_projects:
            return test_projects
        # 4. Fall back to all csprojs (dotnet test will skip non-test ones)
        if all_csprojs:
            return all_csprojs
        return []

    def _normalise_coverage_path(self, work_dir: str) -> None:
        """Normalise the various dotnet output locations to where
        artifact_builder._parse_*_xml expects them:
          - coverlet → TestResults/<guid>/coverage.cobertura.xml → reports/coverage.xml
          - JunitXml.TestLogger → tests/reports/results.xml      → reports/results.xml
          - dotnet default TRX logger → TestResults/*.trx        → reports/results.xml (converted)
        Both go to <work_dir>/reports/.
        """
        reports_dir = os.path.join(work_dir, "reports")
        os.makedirs(reports_dir, exist_ok=True)

        # 1. Coverage. Real `dotnet test` layouts vary by SDK/test runner:
        # coverage may be under TestResults/<guid>/, under a project-local
        # TestResults folder, or nested deeper when multiple test projects run.
        # Walk the workspace and pick the newest cobertura file.
        coverage_candidates: list[str] = []
        for r, _, files in os.walk(work_dir):
            if any(skip in r for skip in (os.sep + "bin", os.sep + "obj", os.sep + ".git")):
                continue
            for f in files:
                if f.lower() == "coverage.cobertura.xml":
                    coverage_candidates.append(os.path.join(r, f))
        if coverage_candidates:
            coverage_candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            shutil.copy(coverage_candidates[0], os.path.join(reports_dir, "coverage.xml"))

        # 2. JUnit — the logger writes inside the test project's own dir
        # (LogFilePath is relative to each test project's bin output, not the
        # global results-directory). Walk and pick the first results.xml.
        junit_candidates: list[str] = []
        for r, _, files in os.walk(work_dir):
            if r.startswith(reports_dir):
                continue
            if "results.xml" in files:
                junit_candidates.append(os.path.join(r, "results.xml"))
        if junit_candidates:
            junit_candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            shutil.copy(junit_candidates[0], os.path.join(reports_dir, "results.xml"))
            return

        # 3. TRX fallback (always produced by dotnet test by default). Convert
        # to a tiny JUnit-compatible XML so artifact_builder._parse_junit_xml works.
        trx_files: list = []
        for r, _, files in os.walk(work_dir):
            for f in files:
                if f.endswith(".trx"):
                    trx_files.append(os.path.join(r, f))
        if trx_files:
            self._trx_to_junit(trx_files[0], os.path.join(reports_dir, "results.xml"))

    def _write_command_log(self, work_dir: str, label: str, result: CmdResult) -> None:
        reports_dir = os.path.join(work_dir, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        path = os.path.join(reports_dir, f"{label}.log")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"Command: {' '.join(result.cmd)}\n")
                f.write(f"Exit code: {result.exit_code}\n")
                f.write(f"Duration ms: {result.duration_ms}\n")
                f.write("\n--- STDOUT ---\n")
                f.write(result.stdout or "")
                f.write("\n--- STDERR ---\n")
                f.write(result.stderr or "")
        except Exception as exc:
            logger.warning("Failed to write %s: %s", path, exc)

    @staticmethod
    def _trx_to_junit(trx_path: str, junit_out: str) -> None:
        """Convert dotnet's TRX (Visual Studio Test Results) format to a minimal
        JUnit-compatible XML so artifact_builder._parse_junit_xml gets totals."""
        import xml.etree.ElementTree as ET
        try:
            tree = ET.parse(trx_path)
            root = tree.getroot()
            # TRX namespace
            ns = {"vs": "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"}
            counters = root.find(".//vs:ResultSummary/vs:Counters", ns)
            if counters is None:
                return
            total = int(counters.get("total", "0"))
            passed = int(counters.get("passed", "0"))
            failed = int(counters.get("failed", "0"))
            executed = int(counters.get("executed", "0"))
            skipped = max(total - executed, 0)
            errors = max(total - passed - failed - skipped, 0)
            # Sum durations across all UnitTestResult elements
            duration_s = 0.0
            for utr in root.findall(".//vs:UnitTestResult", ns):
                d = utr.get("duration")
                if d:
                    h, m, s = d.split(":")
                    duration_s += int(h) * 3600 + int(m) * 60 + float(s)
            with open(junit_out, "w") as f:
                f.write(
                    f'<?xml version="1.0" encoding="UTF-8"?>\n'
                    f'<testsuite tests="{total}" failures="{failed}" errors="{errors}" '
                    f'skipped="{skipped}" time="{duration_s}">\n'
                    f'</testsuite>\n'
                )
        except Exception as exc:
            logger.warning(f"_trx_to_junit failed: {exc}")

    def code_gen_prompt(self, scanned: List[ScannedFunction]) -> str:
        names_by_file = {}
        for fn in scanned:
            names_by_file.setdefault(fn.file_path, []).append(fn.function_name)
        target_summary = "\n".join(
            f"  - {path}: {', '.join(names)}"
            for path, names in names_by_file.items()
        )

        # Pull the actual namespace + class names out of the source files so we
        # can tell the LLM EXACTLY what to import. Without this the LLM picks
        # the class name as the namespace and the build fails with "type or
        # namespace not found".
        namespaces: List[str] = []
        classes: List[str] = []
        for fn in scanned:
            if not fn.code_block:
                continue
            ns_match = re.search(r"^\s*namespace\s+([\w.]+)", fn.code_block, re.MULTILINE)
            if ns_match and ns_match.group(1) not in namespaces:
                namespaces.append(ns_match.group(1))
            cls_match = re.search(r"^\s*(?:public|internal)\s+(?:static\s+)?(?:partial\s+)?class\s+(\w+)", fn.code_block, re.MULTILINE)
            if cls_match and cls_match.group(1) not in classes:
                classes.append(cls_match.group(1))
        ns_directive = "\n".join(f"using {n};" for n in namespaces) if namespaces else "// (source has no explicit namespace — call types directly)"
        ns_decl = (namespaces[0] + ".Tests") if namespaces else "GeneratedTests"
        class_list = ", ".join(classes) if classes else "(infer from analysis)"

        return (
            "Write an xUnit test class for the following C# code analysis.\n"
            "\n"
            "## CRITICAL CONVENTIONS — these are required for the test to compile\n"
            "The test file lives in `tests/GeneratedTests.cs`. The tests project has a\n"
            "ProjectReference to the source project, so the source types ARE available — but you\n"
            "MUST `using` their namespace explicitly. The source code declares the following\n"
            "namespace(s) and class(es):\n"
            "\n"
            f"  Namespace(s):  {', '.join(namespaces) if namespaces else '(none — types are global)'}\n"
            f"  Class(es):     {class_list}\n"
            "\n"
            "Your generated file MUST start with these EXACT using directives:\n"
            "\n"
            "```\n"
            "using System;\n"
            "using Xunit;\n"
            f"{ns_directive}\n"
            "```\n"
            "\n"
            f"AND your namespace declaration MUST be: `namespace {ns_decl}`\n"
            "Do NOT pick a different namespace. Do NOT skip the `using` for the source namespace.\n"
            "Do NOT generate stub fallback implementations. Do NOT use reflection or dynamic invocation.\n"
            "\n"
            "## Test structure rules\n"
            "- Use xUnit `[Fact]` (or `[Theory]` + `[InlineData]`) test methods.\n"
            "- Single public test class per source class; `Assert.Equal`/`Assert.True`/`Assert.Throws<T>`.\n"
            "- Instantiate the class under test with `new ClassName()` (or via field).\n"
            "- Cover happy paths, edge cases, AND expected-exception cases for every method below.\n"
            "- Test methods must be independent — no shared mutable static state.\n"
            "\n"
            "## Output\n"
            "Output ONLY raw C# code (no markdown fences, no commentary). Use the using directives\n"
            "and namespace declaration shown above verbatim.\n"
            "\n"
            "## Methods under test\n"
            f"{target_summary}\n"
        )


# ── Templates for setup_single_file_workspace ───────────────────────────────
_SRC_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>netstandard2.1</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>disable</ImplicitUsings>
  </PropertyGroup>
</Project>
"""

_TESTS_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <!-- RollForward=LatestMajor lets the test DLL run on a newer runtime
         (e.g. user has .NET 10 SDK but no net8.0 runtime installed). Without
         this the testhost crashes with "You must install or update .NET". -->
    <RollForward>LatestMajor</RollForward>
    <IsPackable>false</IsPackable>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.10.0" />
    <PackageReference Include="xunit" Version="2.9.0" />
    <PackageReference Include="xunit.runner.visualstudio" Version="2.8.2" />
    <PackageReference Include="coverlet.collector" Version="6.0.2" />
    <PackageReference Include="JunitXml.TestLogger" Version="3.0.124" />
  </ItemGroup>
  <ItemGroup>
    <ProjectReference Include="..\\src\\{base}.csproj" />
  </ItemGroup>
</Project>
"""

_COVERLET_RUNSETTINGS = """<?xml version="1.0" encoding="utf-8"?>
<RunSettings>
  <DataCollectionRunSettings>
    <DataCollectors>
      <DataCollector friendlyName="XPlat code coverage">
        <Configuration>
          <Format>cobertura</Format>
        </Configuration>
      </DataCollector>
    </DataCollectors>
  </DataCollectionRunSettings>
</RunSettings>
"""

# Minimal VS-format .sln referencing both projects (src + tests). The csproj
# folder GUID 9A19103F-... is the standard SDK-style C# project type. Project
# entry GUIDs are deterministic uuid5 from base name (assigned in the runner).
_SOLUTION_TEMPLATE = """Microsoft Visual Studio Solution File, Format Version 12.00
# Visual Studio Version 17
Project("{{9A19103F-16F7-4668-BE54-9A1E7A4F7556}}") = "{base}", "src\\\\{base}.csproj", "{{{src_guid}}}"
EndProject
Project("{{9A19103F-16F7-4668-BE54-9A1E7A4F7556}}") = "{base}.Tests", "tests\\\\{base}.Tests.csproj", "{{{tests_guid}}}"
EndProject
Global
\tGlobalSection(SolutionConfigurationPlatforms) = preSolution
\t\tDebug|Any CPU = Debug|Any CPU
\t\tRelease|Any CPU = Release|Any CPU
\tEndGlobalSection
\tGlobalSection(ProjectConfigurationPlatforms) = postSolution
\t\t{{{src_guid}}}.Debug|Any CPU.ActiveCfg = Debug|Any CPU
\t\t{{{src_guid}}}.Debug|Any CPU.Build.0 = Debug|Any CPU
\t\t{{{src_guid}}}.Release|Any CPU.ActiveCfg = Release|Any CPU
\t\t{{{src_guid}}}.Release|Any CPU.Build.0 = Release|Any CPU
\t\t{{{tests_guid}}}.Debug|Any CPU.ActiveCfg = Debug|Any CPU
\t\t{{{tests_guid}}}.Debug|Any CPU.Build.0 = Debug|Any CPU
\t\t{{{tests_guid}}}.Release|Any CPU.ActiveCfg = Release|Any CPU
\t\t{{{tests_guid}}}.Release|Any CPU.Build.0 = Release|Any CPU
\tEndGlobalSection
EndGlobal
"""
