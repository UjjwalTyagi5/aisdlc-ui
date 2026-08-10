"""Fan-out node: render every applicable Skill's prompt, call Claude, write
each result to disk. Replaces the direct generate_test_plan → generate_test_code
edge from the pre-Phase-10 graph.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import traceback
from typing import Any, Dict

from langchain_core.output_parsers import StrOutputParser

from agents_orchestrator.testing_agent.config.session_state import SuperAgentState
from agents_orchestrator.testing_agent.config.shared import blog, get_llm
from agents_orchestrator.testing_agent.hooks import on_failure, pre_test_run
from agents_orchestrator.testing_agent.tools.skill_loader import (
    Skill,
    load_all_skills,
)

logger = logging.getLogger("testing_agent.dispatch")


def _strip_code_fences(raw: str) -> str:
    """Mirror Nodes/generate.py:_clean_llm_code_output. Kept inline so dispatch
    has no cross-node import."""
    raw = raw.strip()
    if raw.startswith("```"):
        first_nl = raw.find("\n")
        raw = raw[first_nl + 1:] if first_nl != -1 else raw
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")].strip()
    return raw


def _find_dotnet_test_project_dir(work_dir: str) -> str:
    """Return the directory containing the test .csproj so generated .cs files
    are compiled by the same project. Falls back to work_dir/tests/ if none found."""
    for root, _dirs, files in os.walk(work_dir):
        if any(skip in root for skip in (os.sep + "bin", os.sep + "obj", os.sep + ".git")):
            continue
        for f in files:
            if f.endswith(".csproj") and "test" in f.lower():
                return root
    fallback = os.path.join(work_dir, "tests")
    os.makedirs(fallback, exist_ok=True)
    _ensure_dotnet_generated_test_project(work_dir, fallback)
    return fallback


def _find_dotnet_test_project(work_dir: str) -> str | None:
    target_dir = _find_dotnet_test_project_dir(work_dir)
    for f in os.listdir(target_dir):
        if f.endswith(".csproj") and "test" in f.lower():
            return os.path.relpath(os.path.join(target_dir, f), work_dir)
    return None


def _find_first_dotnet_project(work_dir: str) -> str | None:
    for root, _dirs, files in os.walk(work_dir):
        if any(skip in root for skip in (os.sep + "bin", os.sep + "obj", os.sep + ".git", os.sep + "tests")):
            continue
        for f in files:
            if f.endswith(".csproj") and "test" not in f.lower():
                return os.path.join(root, f)
    return None


def _read_dotnet_target_framework(project_path: str) -> str:
    """Read the app target framework so a generated test project is compatible."""
    try:
        with open(project_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return "net8.0"

    match = re.search(r"<TargetFramework>\s*([^<;\s]+)\s*</TargetFramework>", content, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    match = re.search(r"<TargetFrameworks>\s*([^<]+)\s*</TargetFrameworks>", content, re.IGNORECASE)
    if match:
        frameworks = [value.strip() for value in match.group(1).split(";") if value.strip()]
        if frameworks:
            return frameworks[0]

    return "net8.0"


def _read_dotnet_package_version(project_path: str, package_names: tuple[str, ...]) -> str | None:
    try:
        with open(project_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None

    names = "|".join(re.escape(name) for name in package_names)
    patterns = [
        rf'<PackageReference\s+[^>]*Include="(?:{names})"[^>]*Version="([^"]+)"',
        rf'<PackageReference\s+[^>]*Version="([^"]+)"[^>]*Include="(?:{names})"',
        rf'<PackageReference\s+[^>]*Include="(?:{names})"[^>]*>\s*<Version>\s*([^<]+)\s*</Version>',
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    return None


def _ensure_dotnet_generated_test_project(work_dir: str, test_dir: str) -> str | None:
    """Create a minimal xUnit project when the repo has no existing test project.

    Generated C# files are placed in `test_dir`; without a .csproj there,
    validation ran plain `dotnet build` from the repo root and failed with
    MSB1003. This project gives both validation and final `dotnet test` a
    concrete target.
    """
    project_path = os.path.join(test_dir, "Generated.Tests.csproj")
    if os.path.exists(project_path):
        return project_path

    app_project = _find_first_dotnet_project(work_dir)
    if not app_project:
        return None

    rel_app_project = os.path.relpath(app_project, test_dir)
    target_framework = _read_dotnet_target_framework(app_project)
    ef_inmemory_version = _read_dotnet_package_version(
        app_project,
        (
            "Microsoft.EntityFrameworkCore",
            "Microsoft.EntityFrameworkCore.SqlServer",
            "Microsoft.EntityFrameworkCore.Sqlite",
            "Microsoft.EntityFrameworkCore.Design",
        ),
    )
    ef_inmemory_reference = (
        f'    <PackageReference Include="Microsoft.EntityFrameworkCore.InMemory" Version="{ef_inmemory_version}" />\n'
        if ef_inmemory_version
        else ""
    )
    with open(project_path, "w", encoding="utf-8") as f:
        f.write(
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            '  <PropertyGroup>\n'
            f'    <TargetFramework>{target_framework}</TargetFramework>\n'
            '    <ImplicitUsings>enable</ImplicitUsings>\n'
            '    <Nullable>enable</Nullable>\n'
            '    <IsPackable>false</IsPackable>\n'
            '  </PropertyGroup>\n'
            '  <ItemGroup>\n'
            '    <PackageReference Include="coverlet.collector" Version="6.0.4" />\n'
            '    <PackageReference Include="JunitXml.TestLogger" Version="3.0.124" />\n'
            '    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.12.0" />\n'
            f'{ef_inmemory_reference}'
            '    <PackageReference Include="Moq" Version="4.20.72" />\n'
            '    <PackageReference Include="xunit" Version="2.9.3" />\n'
            '    <PackageReference Include="xunit.runner.visualstudio" Version="2.8.2">\n'
            '      <IncludeAssets>runtime; build; native; contentfiles; analyzers; buildtransitive</IncludeAssets>\n'
            '      <PrivateAssets>all</PrivateAssets>\n'
            '    </PackageReference>\n'
            '  </ItemGroup>\n'
            '  <ItemGroup>\n'
            f'    <ProjectReference Include="{rel_app_project}" />\n'
            '  </ItemGroup>\n'
            '</Project>\n'
        )
    return project_path


def _output_path_for(work_dir: str, skill_name: str, language: str) -> str:
    """Per-skill, per-language file path that the language runner picks up."""
    if language == "dotnet":
        # Place generated files inside the real test .csproj directory so they
        # are compiled when `dotnet test` runs — work_dir/tests/ is NOT the
        # test project directory in a cloned repo.
        target_dir = _find_dotnet_test_project_dir(work_dir)
        return os.path.join(target_dir, f"GeneratedTests_{skill_name.title()}.cs")
    if language == "react":
        target_dir = os.path.join(work_dir, "src")
        os.makedirs(target_dir, exist_ok=True)
        return os.path.join(target_dir, f"generated_{skill_name}.test.jsx")
    # python (default)
    return os.path.join(work_dir, f"test_generated_{skill_name}.py")


def _csharp_looks_complete(code: str) -> bool:
    stripped = code.strip()
    if not stripped:
        return False
    if stripped.endswith(("=", ",", ".", "(", "{", "[", "=>")):
        return False
    pairs = {"{": "}", "(": ")", "[": "]"}
    stack: list[str] = []
    in_string = False
    escape = False
    for ch in stripped:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in pairs.values():
            if not stack or stack.pop() != ch:
                return False
    return not stack and not in_string


def _existing_dotnet_test_classes(work_dir: str) -> set[str]:
    names: set[str] = set()
    for root, _, files in os.walk(work_dir):
        root_parts = {part.lower() for part in os.path.normpath(root).split(os.sep)}
        if "bin" in root_parts or "obj" in root_parts or ".git" in root_parts:
            continue
        for f in files:
            if not (f.endswith(".cs") and "test" in f.lower()):
                continue
            try:
                with open(os.path.join(root, f), "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            names.update(re.findall(r"\bpublic\s+class\s+(\w+)", text))
    return names


def _make_dotnet_class_names_unique(code: str, work_dir: str) -> str:
    existing = _existing_dotnet_test_classes(work_dir)
    if not existing:
        return code

    def repl(match: re.Match) -> str:
        name = match.group(1)
        if name not in existing:
            return match.group(0)
        return f"public class Generated{name}"

    return re.sub(r"\bpublic\s+class\s+(\w+)", repl, code)


def _dotnet_should_skip_source_root(root: str) -> bool:
    root_parts = {part.lower() for part in os.path.normpath(root).split(os.sep)}
    return (
        "bin" in root_parts
        or "obj" in root_parts
        or ".git" in root_parts
        or "migrations" in root_parts
        or any(part in {"tests", "test"} or part.endswith(".tests") for part in root_parts)
    )


def _sanitize_csharp_identifier(value: str, fallback: str = "Generated") -> str:
    parts = re.findall(r"[A-Za-z0-9]+", value or "")
    name = "".join(part[:1].upper() + part[1:] for part in parts) or fallback
    if name[0].isdigit():
        name = fallback + name
    return name


def _dotnet_known_type_names(work_dir: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for root, _, files in os.walk(work_dir):
        if _dotnet_should_skip_source_root(root):
            continue
        for f in files:
            if not f.endswith(".cs"):
                continue
            try:
                with open(os.path.join(root, f), "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            for match in re.finditer(r"\b(?:class|record|struct|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)", text):
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    names.append(name)
    return names


def _dotnet_dbcontext_types(work_dir: str) -> list[str]:
    dbcontexts: list[str] = []
    seen: set[str] = set()
    for root, _, files in os.walk(work_dir):
        if _dotnet_should_skip_source_root(root):
            continue
        for f in files:
            if not f.endswith(".cs"):
                continue
            try:
                with open(os.path.join(root, f), "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            class_matches = re.finditer(
                r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^{\n]+)",
                text,
            )
            for match in class_matches:
                name, bases = match.group(1), match.group(2)
                if "DbContext" in bases and name not in seen:
                    seen.add(name)
                    dbcontexts.append(name)
    return dbcontexts


def _dotnet_type_member_summaries(work_dir: str) -> list[str]:
    summaries: list[str] = []
    for root, _, files in os.walk(work_dir):
        if _dotnet_should_skip_source_root(root):
            continue
        for f in files:
            if not f.endswith(".cs"):
                continue
            try:
                with open(os.path.join(root, f), "r", encoding="utf-8") as fh:
                    lines = fh.readlines()
            except Exception:
                continue

            current: str | None = None
            props: list[str] = []
            methods: list[str] = []
            ctors: list[str] = []

            def flush() -> None:
                nonlocal current, props, methods, ctors
                if current:
                    parts = []
                    if props:
                        parts.append("properties: " + ", ".join(props[:18]))
                    if ctors:
                        parts.append("constructors: " + "; ".join(ctors[:4]))
                    if methods:
                        parts.append("methods: " + ", ".join(methods[:12]))
                    if parts:
                        summaries.append(f"{current} - " + " | ".join(parts))
                current, props, methods, ctors = None, [], [], []

            for line in lines:
                stripped = line.strip()
                class_match = re.search(r"\b(?:class|record|struct)\s+([A-Za-z_][A-Za-z0-9_]*)", stripped)
                enum_match = re.search(r"\benum\s+([A-Za-z_][A-Za-z0-9_]*)", stripped)
                if class_match or enum_match:
                    flush()
                    current = (class_match or enum_match).group(1)
                    if enum_match:
                        enum_text = "".join(lines)
                        body_match = re.search(rf"\benum\s+{re.escape(current)}\s*\{{([^}}]+)\}}", enum_text, re.DOTALL)
                        if body_match:
                            values = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", body_match.group(1))
                            props.extend(values[:18])
                    continue
                if not current:
                    continue
                prop_match = re.search(
                    r"\bpublic\s+([A-Za-z0-9_<>,?\[\].]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{\s*get;",
                    stripped,
                )
                if prop_match:
                    props.append(f"{prop_match.group(2)}:{prop_match.group(1)}")
                    continue
                ctor_match = re.search(rf"\bpublic\s+{re.escape(current)}\s*\(([^)]*)\)", stripped)
                if ctor_match:
                    ctors.append(f"{current}({ctor_match.group(1).strip()})")
                    continue
                method_match = re.search(
                    r"\bpublic\s+(?:async\s+)?([A-Za-z0-9_<>,?\[\].]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)",
                    stripped,
                )
                if method_match and method_match.group(2) != current:
                    methods.append(
                        f"{method_match.group(1)} {method_match.group(2)}({method_match.group(3).strip()})"
                    )
            flush()
    return summaries[:80]


def _dotnet_primitive_return_methods(work_dir: str) -> dict[str, str]:
    primitive_types = {
        "long", "int", "short", "byte", "bool", "string", "decimal", "double", "float", "Guid", "DateTime", "DateOnly",
    }
    methods: dict[str, str] = {}
    return_pattern = "|".join(re.escape(t) for t in sorted(primitive_types, key=len, reverse=True))
    pattern = re.compile(
        rf"\b(?:public\s+)?(?:async\s+)?(?:(?:Task|ValueTask)<\s*(?P<async>{return_pattern})\s*>|(?P<sync>{return_pattern}))\s+"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
        re.MULTILINE,
    )
    for root, _, files in os.walk(work_dir):
        if _dotnet_should_skip_source_root(root):
            continue
        for f in files:
            if not f.endswith(".cs"):
                continue
            try:
                with open(os.path.join(root, f), "r", encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            for match in pattern.finditer(text):
                methods[match.group("name")] = match.group("async") or match.group("sync")
    return methods


def _repair_common_dotnet_type_hallucinations(code: str, work_dir: str) -> str:
    dbcontexts = _dotnet_dbcontext_types(work_dir)
    if len(dbcontexts) == 1 and dbcontexts[0] != "ApplicationDbContext":
        code = re.sub(r"\bApplicationDbContext\b", dbcontexts[0], code)
    code = re.sub(r"^\s*using\s+[A-Za-z_][A-Za-z0-9_.]*\.Tests\s*;\s*\r?\n", "", code, flags=re.MULTILINE)
    return code


def _repair_dotnet_primitive_return_assumptions(code: str, work_dir: str) -> str:
    primitive_methods = _dotnet_primitive_return_methods(work_dir)
    if not primitive_methods:
        return code

    for method_name, return_type in primitive_methods.items():
        if method_name not in code:
            continue

        suffix = "L" if return_type == "long" else ""

        def replace_object_return(match: re.Match) -> str:
            body = match.group("body")
            id_match = re.search(r"\bId\s*=\s*([0-9]+)", body)
            value = id_match.group(1) if id_match else "1"
            if return_type == "string":
                replacement = f'"{value}"'
            elif return_type == "bool":
                replacement = "true"
            else:
                replacement = f"{value}{suffix}"
            return f"{match.group('prefix')}.ReturnsAsync({replacement});"

        code = re.sub(
            rf"(?P<prefix>\.Setup\s*\([^;]*\b{re.escape(method_name)}\s*\([^;]*\)\s*)"
            r"\.ReturnsAsync\s*\(\s*new\s+[A-Za-z_][A-Za-z0-9_.]*\s*\{(?P<body>.*?)\}\s*\)\s*;",
            replace_object_return,
            code,
            flags=re.DOTALL,
        )

        assigned_vars = set(
            re.findall(
                rf"\b(?:var|{re.escape(return_type)})\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:await\s+)?[^;]*\b{re.escape(method_name)}\s*\(",
                code,
            )
        )
        for var_name in assigned_vars:
            code = re.sub(rf"^\s*Assert\.NotNull\(\s*{re.escape(var_name)}\s*\)\s*;\s*\r?\n", "", code, flags=re.MULTILINE)
            code = re.sub(rf"\b{re.escape(var_name)}\.Id\b", var_name, code)
            code = re.sub(
                rf"^\s*Assert\.[A-Za-z0-9_]+\s*\([^;\n]*\b{re.escape(var_name)}\.(?:Status|State|Name|Description)\b[^;\n]*\)\s*;\s*\r?\n",
                "",
                code,
                flags=re.MULTILINE,
            )

    return code


def _remove_brittle_dotnet_default_value_tests(code: str) -> str:
    """Remove generated xUnit methods that only assert model default values."""
    method_pattern = re.compile(
        r"(?P<method>\s*(?:\[[^\]]+\]\s*)+public\s+(?:async\s+)?(?:Task|void)\s+"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*\{(?:[^{}]|\{[^{}]*\})*\})",
        re.DOTALL,
    )

    def repl(match: re.Match) -> str:
        method = match.group("method")
        name = match.group("name").lower()
        lowered = method.lower()
        defaultish_name = any(token in name for token in ("default", "initialvalue", "initial_values"))
        defaultish_assertion = (
            "assert.null" in lowered
            or "assert.empty" in lowered
            or "assert.equal(string.empty" in lowered
            or 'assert.equal("",' in lowered
        )
        if defaultish_name and defaultish_assertion:
            return "\n"
        return method

    return method_pattern.sub(repl, code)


def _dotnet_build_generated_tests(work_dir: str, timeout_s: int = 300):
    from agents_orchestrator.testing_agent.tools.sandbox.base import (
        get_default_sandbox,
        resolve_executable,
    )

    # Fail loudly if the toolchain is genuinely missing — otherwise a [WinError 2]
    # from a bare `dotnet build` is silently treated as "generated tests did not
    # compile", which misattributes an environment gap to the LLM's output.
    if not resolve_executable("dotnet"):
        raise RuntimeError(
            "dotnet SDK not found on PATH or in a well-known install dir — install "
            "the .NET SDK (or add it to PATH) to run .NET unit test generation."
        )

    target = _find_dotnet_test_project(work_dir)
    cmd = ["dotnet", "build"]
    if target:
        cmd.append(target)
    sandbox = get_default_sandbox()
    return sandbox.run(cmd, cwd=work_dir, timeout_s=timeout_s)


def _write_dotnet_generation_log(work_dir: str, label: str, result) -> str:
    reports_dir = os.path.join(work_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, f"{label}.log")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"Command: {' '.join(getattr(result, 'cmd', []) or [])}\n")
        f.write(f"Exit code: {getattr(result, 'exit_code', '')}\n")
        f.write(f"Duration ms: {getattr(result, 'duration_ms', '')}\n")
        f.write("\n--- STDOUT ---\n")
        f.write(getattr(result, "stdout", "") or "")
        f.write("\n--- STDERR ---\n")
        f.write(getattr(result, "stderr", "") or "")
    return path


def _dotnet_namespaces(work_dir: str) -> list[str]:
    namespaces: list[str] = []
    for root, _, files in os.walk(work_dir):
        if _dotnet_should_skip_source_root(root):
            continue
        for f in files:
            if not f.endswith(".cs"):
                continue
            try:
                text = open(os.path.join(root, f), "r", encoding="utf-8").read()
            except Exception:
                continue
            for ns in re.findall(r"^\s*namespace\s+([A-Za-z0-9_.]+)", text, re.MULTILINE):
                if ns not in namespaces:
                    namespaces.append(ns)
    return namespaces[:12]


def _chunked(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _dotnet_unit_prompt(work_dir: str, functions: list, chunk_index: int) -> str:
    namespaces = _dotnet_namespaces(work_dir)
    known_types = _dotnet_known_type_names(work_dir)
    dbcontexts = _dotnet_dbcontext_types(work_dir)
    member_summaries = _dotnet_type_member_summaries(work_dir)
    using_lines = "\n".join(f"using {ns};" for ns in namespaces)
    class_name = f"GeneratedUnitTestsChunk{chunk_index:02d}"
    fn_lines = []
    for fn in functions:
        fn_lines.append(
            "\n".join([
                f"- File: {getattr(fn, 'file_path', '')}",
                f"  Function: {getattr(fn, 'function_name', '')}",
                f"  Summary: {getattr(fn, 'summary', '')}",
                f"  Inputs: {getattr(fn, 'input_format', '')}",
                f"  Output: {getattr(fn, 'output_format', '')}",
            ])
        )
    return (
        "Generate a COMPLETE, COMPILABLE C# xUnit test file for this .NET project.\n"
        "Output raw C# only. No markdown fences. No explanations.\n\n"
        "Hard requirements:\n"
        f"- Use exactly one public class named `{class_name}`.\n"
        "- Do not use class names from existing tests.\n"
        "- Generate at most 2 focused tests per listed function.\n"
        "- Prefer service/model/data tests that can compile with public constructors.\n"
        "- Do not generate tests that only assert model/DTO/default constructor default values.\n"
        "- Do not assert that string properties default to null, empty string, or string.Empty unless that initializer is explicitly visible in the provided member signatures.\n"
        "- Focus assertions on behavior, persistence, filtering, formatting, validation, and returned results.\n"
        "- If a controller or method needs dependencies you cannot construct, skip that method instead of writing uncompilable code.\n"
        "- Do not invent project types. Use only project type names listed below, plus .NET, Microsoft, Moq, and xUnit types.\n"
        "- Do not invent properties, enum values, interface methods, or constructor overloads. If exact member names are not visible, skip that test.\n"
        "- Respect method return types exactly. If a method returns Task<long>, long, int, string, bool, Guid, DateTime, or DateOnly, assert the primitive value directly and never access .Id, .Status, or other object properties on the result.\n"
        "- For Moq setups, ReturnsAsync must return the exact async result type shown in the known member signatures.\n"
        "- Avoid controller tests unless the constructor signature and all dependency methods are known from the function context.\n"
        "- Prefer simple public service methods and model/database behavior over broad controller orchestration tests.\n"
        "- If an EF Core DbContext is needed, use exactly one of the DbContext type names listed below.\n"
        "- The file must be complete: all braces, parentheses, strings, and object initializers must be closed.\n"
        "- Use ASCII comments only.\n\n"
        f"Known project types: {', '.join(known_types[:160]) or '(none discovered)'}\n"
        f"DbContext type names: {', '.join(dbcontexts) or '(none discovered)'}\n\n"
        "Known member signatures. Use these exact property, constructor, method, and enum names only:\n"
        + ("\n".join(f"- {item}" for item in member_summaries) or "- (none discovered)")
        + "\n\n"
        "Start with these using directives, adding only standard Microsoft/xUnit usings if needed:\n"
        "using System;\n"
        "using System.Linq;\n"
        "using System.Threading.Tasks;\n"
        "using Microsoft.EntityFrameworkCore;\n"
        "using Xunit;\n"
        f"{using_lines}\n\n"
        "Functions to cover:\n"
        + "\n\n".join(fn_lines)
    )


async def _run_dotnet_unit_skill(state: SuperAgentState, skill: Skill) -> Dict[str, Any]:
    code_analysis = state.get("code_analysis")
    functions = list(getattr(code_analysis, "functions", None) or [])
    if not functions:
        raise RuntimeError("unit: no production functions available for .NET unit generation")

    work_dir = state["work_dir"]
    target_dir = _find_dotnet_test_project_dir(work_dir)
    reports_dir = os.path.join(work_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    chain = get_llm() | StrOutputParser()
    loop = asyncio.get_running_loop()
    written: list[str] = []
    scenario_count = 0

    # Small chunks avoid truncation and keep each generated C# file readable.
    for idx, chunk in enumerate(_chunked(functions, 4), start=1):
        prompt = _dotnet_unit_prompt(work_dir, chunk, idx)
        code = ""
        for attempt in range(2):
            retry_suffix = ""
            if attempt:
                retry_suffix = (
                    "\n\nThe previous response was incomplete or syntactically unbalanced. "
                    "Regenerate the same single C# file from scratch. Keep it shorter. "
                    "Return only complete raw C# code."
                )
            blog(
                f"Skill {skill.name}: prompting LLM "
                f"(dotnet chunk {idx}, attempt {attempt + 1}, {len(prompt)} chars)"
            )
            raw = await loop.run_in_executor(None, chain.invoke, prompt + retry_suffix)
            code = _strip_code_fences(raw)
            code = _make_dotnet_class_names_unique(code, work_dir)
            code = _repair_common_dotnet_type_hallucinations(code, work_dir)
            code = _repair_dotnet_primitive_return_assumptions(code, work_dir)
            code = _remove_brittle_dotnet_default_value_tests(code)
            if _csharp_looks_complete(code):
                break
        if not _csharp_looks_complete(code):
            diagnostic_path = os.path.join(reports_dir, f"invalid_generated_{skill.name}_chunk{idx:02d}.cs")
            with open(diagnostic_path, "w", encoding="utf-8") as f:
                f.write(code)
            raise RuntimeError(
                f"{skill.name}: generated C# chunk {idx} was incomplete or syntactically unbalanced; "
                f"saved diagnostic copy to {diagnostic_path}"
            )

        out_path = os.path.join(target_dir, f"GeneratedTests_{skill.name.title()}_{idx:02d}.cs")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(code)

        build_res = await loop.run_in_executor(None, _dotnet_build_generated_tests, work_dir)
        if not build_res.ok:
            diagnostic_name = f"rejected_{os.path.basename(out_path)}"
            diagnostic_path = os.path.join(reports_dir, diagnostic_name)
            shutil.copy(out_path, diagnostic_path)
            log_path = _write_dotnet_generation_log(work_dir, f"rejected_{skill.name}_chunk{idx:02d}", build_res)
            try:
                os.remove(out_path)
            except OSError:
                pass
            blog(
                f"Skill {skill.name}: rejected {os.path.basename(out_path)} because it did not compile; "
                f"diagnostics saved to {os.path.basename(log_path)}",
                level="WARNING",
            )
            continue

        shutil.copy(out_path, os.path.join(reports_dir, os.path.basename(out_path)))
        written.append(out_path)
        scenario_count += code.count("[Fact]") + code.count("[Theory]")
        blog(f"Skill {skill.name}: wrote {os.path.basename(out_path)} ({len(code)} bytes)")

    if scenario_count == 0:
        raise RuntimeError("unit: generated .NET unit files contained no compilable xUnit test methods")

    return {
        "skill_name": skill.name,
        "test_file_path": written[0],
        "test_framework": "xunit",
        "scenario_count": scenario_count,
        "test_file_paths": written,
    }


async def _run_shell_skill(state: SuperAgentState, skill: Skill) -> Dict[str, Any]:
    """Phase 11.4 — shell-runtime skill execution path.

    Runs `skill.shell_command` via the existing sandbox, writes its raw stdout
    to `reports/<skill>.json`, parses via the registered output_parser, and
    returns a dict the dispatcher's collect-loop merges into state. The
    parsed artifact lands in state[`skill.output_artifact_field`] so the
    aggregator picks it up.
    """
    import shlex
    work_dir = state.get("work_dir")
    if not work_dir:
        raise RuntimeError(f"shell skill {skill.name!r}: no work_dir in state")
    if not skill.shell_command:
        raise RuntimeError(f"shell skill {skill.name!r}: missing shell_command in SKILL.md frontmatter")

    from agents_orchestrator.testing_agent.tools.sandbox.base import (
        get_default_sandbox,
        resolve_executable,
    )
    from agents_orchestrator.testing_agent.tools.skill_parsers import get_parser

    sandbox = get_default_sandbox()
    cmd_parts = shlex.split(skill.shell_command)
    # Pre-flight: check that the executable exists before handing to the sandbox.
    # On Windows, missing tools produce [WinError 2] which surfaces as an unhelpful
    # crash. resolve_executable() checks PATH then well-known install dirs (e.g.
    # dotnet), giving a clear "tool not installed" message only when truly absent.
    executable = cmd_parts[0]
    if not resolve_executable(executable):
        msg = (
            f"Skill {skill.name} (shell): '{executable}' not found on PATH — "
            f"skipping. Install it to enable this skill."
        )
        logger.warning(msg)
        blog(msg, level="WARNING")
        return {"skill_failures": [{
            "skill": skill.name,
            "error": f"'{executable}' not installed",
        }]}
    blog(f"Skill {skill.name} (shell): running {skill.shell_command[:80]}…")
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(
        None,
        lambda: sandbox.run(cmd_parts, cwd=work_dir, timeout_s=skill.shell_timeout_s),
    )

    parsed_artifact = None
    parser_name = skill.output_parser
    if parser_name:
        parser = get_parser(parser_name)
        if parser is None:
            blog(f"Skill {skill.name}: parser {parser_name!r} not registered; raw output preserved", level="WARNING")
        else:
            # Convention: parser reads from work_dir/reports/<skill>.json by default
            # but skills can override via "{work_dir}" placeholder in shell_command;
            # caller can put report wherever and parsers must accept whatever path.
            default_report = os.path.join(work_dir, "reports", f"{skill.name}.json")
            try:
                parsed_artifact = parser(default_report)
            except Exception as exc:
                logger.warning(f"Skill {skill.name}: parser {parser_name} failed: {exc}")
                parsed_artifact = None

    if res.exit_code == 0:
        blog(f"Skill {skill.name} (shell): exit=0, parsed_artifact={parsed_artifact is not None}")
    else:
        blog(f"Skill {skill.name} (shell): exit={res.exit_code} — output captured", level="WARNING")

    return {
        "skill_name": skill.name,
        "test_file_path": "",                                 # shell skills don't generate test code
        "test_framework": skill.name,                         # display label in pyramid: skill name itself
        "scenario_count": 0,
        "runtime": "shell",
        "shell_exit_code": res.exit_code,
        "parsed_artifact": parsed_artifact,
        "output_artifact_field": skill.output_artifact_field,
    }


async def _run_skill(state: SuperAgentState, skill: Skill) -> Dict[str, Any]:
    # Phase 11.4 — shell-runtime branch. When a skill declares runtime: shell,
    # bypass the LLM entirely and dispatch through the sandbox + registered parser.
    # Defensive getattr — a stub Skill (test fixture, future remote skill) may
    # not declare `runtime`; default to llm to preserve pre-Phase-11.4 behaviour.
    if getattr(skill, "runtime", "llm") == "shell":
        return await _run_shell_skill(state, skill)

    import json as _json
    language = state.get("language") or "python"
    code_analysis = state.get("code_analysis")
    test_plan = state.get("test_plan")

    if language == "dotnet" and skill.name == "unit":
        return await _run_dotnet_unit_skill(state, skill)

    # Phase 11.2 — make upstream Requirements + Design context available to
    # any skill that declares them as inputs. This lets contract/accessibility/
    # property_based / future skills generate tests directly from the design
    # agent's API contracts + DB schema + ADRs without re-deriving them from
    # code. Skills that don't declare these inputs see no change.
    upstream_requirements = state.get("upstream_requirements") or {}
    upstream_design = state.get("upstream_design") or {}
    upstream_development = state.get("upstream_development") or {}
    openapi_spec_json = state.get("openapi_spec_json") or "{}"
    if skill.name == "functional_api" and openapi_spec_json == "{}" and state.get("target_url"):
        try:
            import httpx

            base_url = str(state.get("target_url") or "").rstrip("/")
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(f"{base_url}/openapi.json")
                if resp.status_code == 200:
                    openapi_spec_json = resp.text
                    blog("Functional API: loaded OpenAPI spec from /openapi.json")
        except Exception as exc:
            logger.info("Functional API: OpenAPI discovery skipped: %s", exc)

    render_kwargs = {
        "language": language,
        "code_analysis": code_analysis.model_dump_json() if code_analysis else "{}",
        "test_plan": test_plan.model_dump_json() if test_plan else "{}",
        "target_url": state.get("target_url") or "",
        "openapi_spec_json": openapi_spec_json,
        # Phase 11.2 — upstream context propagation
        "upstream_requirements": _json.dumps(upstream_requirements, default=str)
            if upstream_requirements else "{}",
        "upstream_design": _json.dumps(upstream_design, default=str)
            if upstream_design else "{}",
        "upstream_development": _json.dumps(upstream_development, default=str)
            if upstream_development else "{}",
        # Lifted convenience: when api_contracts is the only piece a skill
        # needs (the contract skill, Phase 3), it can ask for this directly
        # rather than parsing the full design blob.
        "api_contracts": _json.dumps(
            (upstream_design or {}).get("api_contracts") or {}, default=str,
        ),
        "db_schema": _json.dumps(
            (upstream_design or {}).get("db_schema") or {}, default=str,
        ),
    }
    # render() will raise KeyError if a declared input is missing — pass only
    # what the skill actually declares to keep prompts tight.
    declared = {k: v for k, v in render_kwargs.items() if k in skill.inputs}
    prompt = skill.render(**declared)

    blog(f"Skill {skill.name}: prompting LLM ({language}, {len(prompt)} chars)")
    chain = get_llm() | StrOutputParser()
    loop = asyncio.get_running_loop()
    raw = await loop.run_in_executor(None, chain.invoke, prompt)
    code = _strip_code_fences(raw)

    # functional_ui passthrough: do not write a file. Just signal that the
    # existing UI agent should run during the unit-test phase.
    if skill.name == "functional_ui" and code.strip().startswith("UI_TEST_TRIGGER:"):
        return {
            "skill_name": skill.name,
            "test_file_path": "",
            "test_framework": "ui_browser",
            "scenario_count": 0,
        }

    work_dir = state["work_dir"]
    # functional_api is always Python httpx — pin path to a .py file even if
    # the dev's project is .NET / React.
    if skill.name == "functional_api":
        out_path = os.path.join(work_dir, "test_generated_functional_api.py")
    else:
        out_path = _output_path_for(work_dir, skill.name, language)

    if language == "dotnet":
        code = _make_dotnet_class_names_unique(code, work_dir)
        code = _repair_common_dotnet_type_hallucinations(code, work_dir)
        code = _repair_dotnet_primitive_return_assumptions(code, work_dir)
        code = _remove_brittle_dotnet_default_value_tests(code)
        if not _csharp_looks_complete(code):
            reports_dir = os.path.join(work_dir, "reports")
            os.makedirs(reports_dir, exist_ok=True)
            diagnostic_path = os.path.join(reports_dir, f"invalid_generated_{skill.name}.cs")
            with open(diagnostic_path, "w", encoding="utf-8") as f:
                f.write(code)
            raise RuntimeError(
                f"{skill.name}: generated C# was incomplete or syntactically unbalanced; "
                f"saved diagnostic copy to {diagnostic_path}"
            )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(code)
    if language == "dotnet":
        reports_dir = os.path.join(work_dir, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        shutil.copy(out_path, os.path.join(reports_dir, os.path.basename(out_path)))

    framework = {"python": "pytest", "dotnet": "xunit", "react": "jest"}.get(language, language)
    if skill.name == "functional_api":
        framework = "pytest"
    blog(f"Skill {skill.name}: wrote {os.path.basename(out_path)} ({len(code)} bytes)")
    return {
        "skill_name": skill.name,
        "test_file_path": out_path,
        "test_framework": framework,
        "scenario_count": code.count("def test_") + code.count("[Fact]") + code.count("test("),
    }


async def dispatch_test_types(state: SuperAgentState) -> Dict[str, Any]:
    """Fan out to every applicable Skill in parallel. Each Skill's per-call
    failure is captured into skill_failures; other Skills proceed."""
    pre_test_run(state)
    skills = load_all_skills()
    applicable = [s for s in skills.values() if s.applies(state)]
    selected_types = set(state.get("selected_test_types") or [])
    # Map a user-facing test type → the skill(s) that implement it. Accepts both
    # friendly aliases and raw skill names so every test-type button routes to its
    # skill (not just unit/functional/api).
    _TYPE_TO_SKILLS = {
        "unit": {"unit"},
        "functional": {"functional_ui"}, "functional_ui": {"functional_ui"}, "ui": {"functional_ui"},
        "api": {"functional_api"}, "functional_api": {"functional_api"},
        "contract": {"contract"},
        "integration": {"integration"},
        "smoke": {"smoke"},
        "accessibility": {"accessibility"}, "a11y": {"accessibility"},
        "mutation": {"mutation_testing"}, "mutation_testing": {"mutation_testing"},
        "property": {"property_based"}, "property_based": {"property_based"},
        "negative_edge": {"negative_edge"}, "negative": {"negative_edge"}, "edge": {"negative_edge"},
        "security_static": {"security_static"}, "security": {"security_static"},
        "dependency_scan": {"dependency_scan"}, "dependencies": {"dependency_scan"},
    }
    if selected_types:
        selected_skill_names: set[str] = set()
        for t in selected_types:
            selected_skill_names |= _TYPE_TO_SKILLS.get(str(t).strip().lower(), {str(t).strip().lower()})
        applicable = [s for s in applicable if s.name in selected_skill_names]
        # Explicit selection is authoritative — bypass the api_scope gate below
        # so a directly-selected contract/functional_api skill still runs.
        state = {**state, "_explicit_skill_selection": selected_skill_names}

    # Apply api_scope filter — only run API skills the user explicitly requested.
    # When the user picked the skill directly (test-type button), selection wins.
    api_scope = state.get("api_scope")  # "contract_only" | "functional" | "both" | None
    explicit = state.get("_explicit_skill_selection") or set()
    filtered = []
    for s in applicable:
        if s.name == "contract":
            if s.name in explicit or api_scope in ("contract_only", "both"):
                filtered.append(s)
            else:
                logger.info("Skipping 'contract' skill — api_scope=%s", api_scope)
        elif s.name == "functional_api":
            if s.name in explicit or (api_scope in ("functional", "both") and state.get("target_url")):
                filtered.append(s)
            else:
                logger.info("Skipping 'functional_api' skill — api_scope=%s, target_url=%s",
                            api_scope, state.get("target_url"))
        else:
            filtered.append(s)
    applicable = filtered

    blog(f"Test-type fan-out: {[s.name for s in applicable]}")

    if not applicable:
        return {"generated_test_sets": [], "skill_failures": ["no applicable skills"]}

    # Throttle LLM skill calls to avoid 529 Overloaded — run at most 3 at once.
    _skill_sem = asyncio.Semaphore(3)

    async def _run_skill_throttled(s):
        async with _skill_sem:
            return await _run_skill(state, s)

    results = await asyncio.gather(
        *[_run_skill_throttled(skill) for skill in applicable],
        return_exceptions=True,
    )

    generated: list = []
    failures: list = []
    for skill, r in zip(applicable, results):
        if isinstance(r, Exception):
            tb = "".join(traceback.format_exception(type(r), r, r.__traceback__))
            failures.append(f"{skill.name}: {type(r).__name__}: {r}")
            on_failure(state, r, ctx=f"skill:{skill.name}")
            logger.warning(f"Skill {skill.name} failed:\n{tb}")
        else:
            generated.append(r)

    return {"generated_test_sets": generated, "skill_failures": failures}
