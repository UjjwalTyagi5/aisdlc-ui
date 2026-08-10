"""dispatch_test_types fan-out: filters skills by applies(), runs in parallel,
isolates per-skill failures via asyncio.gather(return_exceptions=True).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents_orchestrator.testing_agent.Nodes.dispatch_test_types import (
    dispatch_test_types,
    _find_dotnet_test_project,
    _run_skill,
)
from agents_orchestrator.testing_agent.tools.skill_loader import load_all_skills as _load_all_skills


def _has_two_applicable_skills() -> bool:
    skills = _load_all_skills(reload=True)
    sample_state = {"code_analysis": object()}  # truthy
    applicable = [s for s in skills.values() if s.applies(sample_state)]
    return len(applicable) >= 2


def test_dotnet_fallback_test_project_created_when_repo_has_no_tests(tmp_path):
    app_dir = tmp_path / "src" / "DemoApp"
    app_dir.mkdir(parents=True)
    (app_dir / "DemoApp.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>',
        encoding="utf-8",
    )

    target = _find_dotnet_test_project(str(tmp_path))

    assert target.replace("\\", "/") == "tests/Generated.Tests.csproj"
    generated = tmp_path / "tests" / "Generated.Tests.csproj"
    assert generated.exists()
    assert "..\\src\\DemoApp\\DemoApp.csproj" in generated.read_text(encoding="utf-8")


def test_dotnet_fallback_test_project_matches_app_target_framework(tmp_path):
    app_dir = tmp_path / "src" / "DemoApp"
    app_dir.mkdir(parents=True)
    (app_dir / "DemoApp.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>',
        encoding="utf-8",
    )

    _find_dotnet_test_project(str(tmp_path))

    generated = tmp_path / "tests" / "Generated.Tests.csproj"
    text = generated.read_text(encoding="utf-8")
    assert "<TargetFramework>net10.0</TargetFramework>" in text


def test_dotnet_fallback_test_project_adds_matching_ef_inmemory_package(tmp_path):
    app_dir = tmp_path / "src" / "DemoApp"
    app_dir.mkdir(parents=True)
    (app_dir / "DemoApp.csproj").write_text(
        """
<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.EntityFrameworkCore.SqlServer" Version="10.0.0" />
  </ItemGroup>
</Project>
""",
        encoding="utf-8",
    )

    _find_dotnet_test_project(str(tmp_path))

    generated = tmp_path / "tests" / "Generated.Tests.csproj"
    text = generated.read_text(encoding="utf-8")
    assert '<PackageReference Include="Microsoft.EntityFrameworkCore.InMemory" Version="10.0.0" />' in text


def test_dotnet_fallback_test_project_reads_multiline_ef_package_version(tmp_path):
    app_dir = tmp_path / "src" / "DemoApp"
    app_dir.mkdir(parents=True)
    (app_dir / "DemoApp.csproj").write_text(
        """
<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.EntityFrameworkCore.Design" Version="10.0.7">
      <PrivateAssets>all</PrivateAssets>
      <IncludeAssets>runtime; build; native; contentfiles; analyzers; buildtransitive</IncludeAssets>
    </PackageReference>
    <PackageReference Include="Microsoft.EntityFrameworkCore.Sqlite" Version="10.0.7" />
  </ItemGroup>
</Project>
""",
        encoding="utf-8",
    )

    _find_dotnet_test_project(str(tmp_path))

    generated = tmp_path / "tests" / "Generated.Tests.csproj"
    text = generated.read_text(encoding="utf-8")
    assert '<PackageReference Include="Microsoft.EntityFrameworkCore.InMemory" Version="10.0.7" />' in text


def test_dotnet_fallback_test_project_includes_moq(tmp_path):
    app_dir = tmp_path / "src" / "DemoApp"
    app_dir.mkdir(parents=True)
    (app_dir / "DemoApp.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>',
        encoding="utf-8",
    )

    _find_dotnet_test_project(str(tmp_path))

    generated = tmp_path / "tests" / "Generated.Tests.csproj"
    text = generated.read_text(encoding="utf-8")
    assert '<PackageReference Include="Moq" Version="4.20.72" />' in text
    assert '<PackageReference Include="xunit" Version="2.9.3" />' in text
    assert '<PrivateAssets>all</PrivateAssets>' in text


def test_dotnet_repair_removes_invalid_tests_namespace_import(tmp_path):
    from agents_orchestrator.testing_agent.Nodes.dispatch_test_types import _repair_common_dotnet_type_hallucinations

    code = "using System;\nusing RadAuthPortal.Tests;\nusing Xunit;\npublic class DemoTests {}\n"

    repaired = _repair_common_dotnet_type_hallucinations(code, str(tmp_path))

    assert "using RadAuthPortal.Tests;" not in repaired
    assert "using Xunit;" in repaired


def test_dotnet_repair_primitive_return_mock_and_assertions(tmp_path):
    from agents_orchestrator.testing_agent.Nodes.dispatch_test_types import _repair_dotnet_primitive_return_assumptions

    svc = tmp_path / "src" / "DemoApp"
    svc.mkdir(parents=True)
    (svc / "ICaseService.cs").write_text(
        "using System.Threading.Tasks; public interface ICaseService { Task<long> CreateCaseAsync(CreateCaseViewModel model); }",
        encoding="utf-8",
    )
    code = """
public async Task Create_ReturnsCase()
{
    caseServiceMock.Setup(s => s.CreateCaseAsync(It.IsAny<CreateCaseViewModel>()))
        .ReturnsAsync(new Case { Id = 42, Status = CaseStatus.InProgress });
    var result = await caseServiceMock.Object.CreateCaseAsync(new CreateCaseViewModel());
    Assert.NotNull(result);
    Assert.Equal(42, result.Id);
    Assert.Equal(CaseStatus.InProgress, result.Status);
}
"""

    repaired = _repair_dotnet_primitive_return_assumptions(code, str(tmp_path))

    assert ".ReturnsAsync(42L);" in repaired
    assert "Assert.Equal(42, result);" in repaired
    assert "Assert.NotNull(result)" not in repaired
    assert "result.Id" not in repaired
    assert "result.Status" not in repaired


@pytest.mark.asyncio
async def test_dispatches_only_applicable_skills():
    state = {
        "code_analysis": MagicMock(functions=[MagicMock()]),
        "test_plan": MagicMock(test_cases=[]),
        "language": "python",
        "work_dir": "/tmp",
        # No target_url → smoke / functional_api / functional_ui should NOT run
    }
    with patch(
        "agents_orchestrator.testing_agent.Nodes.dispatch_test_types._run_skill",
        new=AsyncMock(return_value={"skill_name": "unit", "test_file_path": "/tmp/t.py", "test_framework": "pytest", "scenario_count": 1}),
    ) as mock_run:
        result = await dispatch_test_types(state)
    # unit run; smoke / functional_api / functional_ui skipped
    called_names = [c.args[1].name for c in mock_run.call_args_list]
    assert "unit" in called_names
    assert "smoke" not in called_names
    assert "functional_api" not in called_names


@pytest.mark.skipif(not _has_two_applicable_skills(), reason="needs at least 2 applicable skills (negative_edge added in Task 7)")
@pytest.mark.asyncio
async def test_one_skill_failure_does_not_kill_others():
    state = {"code_analysis": MagicMock(functions=[MagicMock()]), "test_plan": MagicMock(), "language": "python", "work_dir": "/tmp"}

    async def fake_run_skill(state, skill):
        if skill.name == "unit":
            raise RuntimeError("unit gen failed")
        return {"skill_name": skill.name, "test_file_path": f"/tmp/{skill.name}.py", "test_framework": "pytest", "scenario_count": 1}

    with patch(
        "agents_orchestrator.testing_agent.Nodes.dispatch_test_types._run_skill",
        new=AsyncMock(side_effect=fake_run_skill),
    ):
        result = await dispatch_test_types(state)

    assert any("unit gen failed" in f for f in result.get("skill_failures", []))
    assert any(s["skill_name"] == "negative_edge" for s in result.get("generated_test_sets", []))


@pytest.mark.skipif(not _has_two_applicable_skills(), reason="needs at least 2 applicable skills (negative_edge added in Task 7)")
@pytest.mark.asyncio
async def test_dispatch_runs_in_parallel():
    """Skills are throttled, but should still run faster than fully sequential."""
    state = {"code_analysis": MagicMock(functions=[MagicMock()]), "test_plan": MagicMock(), "language": "python", "work_dir": "/tmp"}
    delay = 0.2

    async def slow_run(state, skill):
        await asyncio.sleep(delay)
        return {"skill_name": skill.name, "test_file_path": "/x", "test_framework": "pytest", "scenario_count": 0}

    with patch(
        "agents_orchestrator.testing_agent.Nodes.dispatch_test_types._run_skill",
        new=AsyncMock(side_effect=slow_run),
    ):
        import time
        t0 = time.monotonic()
        await dispatch_test_types(state)
        elapsed = time.monotonic() - t0
    # dispatch_test_types intentionally throttles LLM skills to 3 at a time.
    # With 5 applicable skills and 0.2s delay, throttled parallelism is about
    # 0.4s, while fully sequential execution would be about 1.0s.
    assert elapsed < 0.65, f"dispatch appears sequential (elapsed={elapsed:.2f}s)"


@pytest.mark.asyncio
async def test_functional_ui_passthrough_does_not_write_file(tmp_path):
    state = {"work_dir": str(tmp_path), "target_url": "http://x", "language": "python"}
    from agents_orchestrator.testing_agent.tools.skill_loader import load_all_skills as _laS
    skills = _laS(reload=True)
    ui_skill = skills["functional_ui"]

    # Mock the chain (get_llm() | StrOutputParser()) to return UI_TEST_TRIGGER
    fake_chain = MagicMock()
    fake_chain.invoke = lambda prompt: "UI_TEST_TRIGGER:http://x"
    fake_llm = MagicMock()
    fake_llm.__or__ = lambda self, other: fake_chain

    with patch(
        "agents_orchestrator.testing_agent.Nodes.dispatch_test_types.get_llm",
        return_value=fake_llm,
    ):
        result = await _run_skill(state, ui_skill)
    assert result["test_framework"] == "ui_browser"
    # No file written
    assert not any(p.name.startswith("generated_functional_ui") for p in tmp_path.iterdir())


@pytest.mark.asyncio
async def test_functional_api_pinned_to_python(tmp_path):
    state = {"work_dir": str(tmp_path), "target_url": "http://x", "language": "dotnet"}
    from agents_orchestrator.testing_agent.tools.skill_loader import load_all_skills as _laS
    skills = _laS(reload=True)
    fa_skill = skills["functional_api"]

    fake_chain = MagicMock()
    fake_chain.invoke = lambda prompt: "import pytest, httpx\ndef test_x(): pass"
    fake_llm = MagicMock()
    fake_llm.__or__ = lambda self, other: fake_chain

    with patch(
        "agents_orchestrator.testing_agent.Nodes.dispatch_test_types.get_llm",
        return_value=fake_llm,
    ):
        result = await _run_skill(state, fa_skill)
    assert result["test_file_path"].endswith(".py")
    assert result["test_framework"] == "pytest"
