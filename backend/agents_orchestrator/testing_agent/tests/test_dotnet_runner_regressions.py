from __future__ import annotations

from pathlib import Path

from agents_orchestrator.testing_agent.Nodes.dispatch_test_types import (
    _csharp_looks_complete,
    _chunked,
    _dotnet_dbcontext_types,
    _dotnet_type_member_summaries,
    _dotnet_unit_prompt,
    _make_dotnet_class_names_unique,
    _repair_common_dotnet_type_hallucinations,
    _remove_brittle_dotnet_default_value_tests,
)
from agents_orchestrator.testing_agent.tools.runners.dotnet import DotnetRunner


def test_dotnet_scan_skips_test_projects_and_migrations(tmp_path: Path):
    prod = tmp_path / "RadAuthPortal" / "Services"
    tests = tmp_path / "RadAuthPortal.Tests"
    migrations = tmp_path / "RadAuthPortal" / "Migrations"
    prod.mkdir(parents=True)
    tests.mkdir(parents=True)
    migrations.mkdir(parents=True)

    (prod / "CaseService.cs").write_text(
        "namespace RadAuthPortal.Services;\n"
        "public class CaseService\n"
        "{\n"
        "  public int CreateCaseAsync()\n"
        "  {\n"
        "    return 1;\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    (tests / "CaseServiceTests.cs").write_text(
        "public class CaseServiceTests\n"
        "{\n"
        "  public void CreateCaseAsync_ReturnsId() {}\n"
        "}\n",
        encoding="utf-8",
    )
    (migrations / "InitialCreate.cs").write_text(
        "public class InitialCreate\n"
        "{\n"
        "  protected void Up() {}\n"
        "}\n",
        encoding="utf-8",
    )

    scanned = DotnetRunner().scan_files(str(tmp_path))

    assert [s.function_name for s in scanned] == ["CreateCaseAsync"]


def test_generated_csharp_completeness_guard_rejects_truncated_initializer():
    assert not _csharp_looks_complete("using Xunit;\npublic class GeneratedUnitTests { void X() { var x = new CptCode { Code =")
    assert _csharp_looks_complete("using Xunit;\npublic class GeneratedUnitTests { public void X() { Assert.True(true); } }")


def test_generated_dotnet_class_names_are_made_unique(tmp_path: Path):
    test_dir = tmp_path / "RadAuthPortal.Tests"
    test_dir.mkdir()
    (test_dir / "ActionLogServiceTests.cs").write_text(
        "public class ActionLogServiceTests {}\n",
        encoding="utf-8",
    )

    code = "public class ActionLogServiceTests {}\npublic class GeneratedUnitTests {}"
    rewritten = _make_dotnet_class_names_unique(code, str(tmp_path))

    assert "public class GeneratedActionLogServiceTests" in rewritten
    assert "public class GeneratedUnitTests" in rewritten


def test_brittle_dotnet_default_value_tests_are_removed():
    code = """
using Xunit;
public class GeneratedUnitTestsChunk04
{
    [Fact]
    public void ActionLog_DefaultValuesAreExpected()
    {
        var log = new ActionLog();
        Assert.Null(log.Comment);
    }

    [Fact]
    public void LogAsync_WritesEntry()
    {
        Assert.True(true);
    }
}
"""

    cleaned = _remove_brittle_dotnet_default_value_tests(code)

    assert "ActionLog_DefaultValuesAreExpected" not in cleaned
    assert "LogAsync_WritesEntry" in cleaned


def test_dotnet_unit_prompt_is_chunked_and_unique(tmp_path: Path):
    src = tmp_path / "App" / "Services"
    migrations = tmp_path / "App" / "Migrations"
    src.mkdir(parents=True)
    migrations.mkdir(parents=True)
    (src / "OrderService.cs").write_text(
        "namespace Demo.Services;\npublic class OrderService {}\n",
        encoding="utf-8",
    )
    (migrations / "InitialCreate.cs").write_text(
        "namespace Demo.Migrations;\npublic class InitialCreate {}\n",
        encoding="utf-8",
    )

    fn = type(
        "Fn",
        (),
        {
            "file_path": "App/Services/OrderService.cs",
            "function_name": "CreateOrder",
            "summary": "Creates an order.",
            "input_format": "none",
            "output_format": "int",
        },
    )()
    prompt = _dotnet_unit_prompt(str(tmp_path), [fn], 3)

    assert "GeneratedUnitTestsChunk03" in prompt
    assert "Do not generate tests that only assert model/DTO/default constructor default values." in prompt
    assert "using Demo.Services;" in prompt
    assert "using Demo.Migrations;" not in prompt
    assert len(_chunked([1, 2, 3, 4, 5], 4)) == 2


def test_dotnet_prompt_exposes_real_dbcontext_and_repairs_applicationdbcontext(tmp_path: Path):
    data = tmp_path / "App" / "Data"
    data.mkdir(parents=True)
    (data / "AppDbContext.cs").write_text(
        "using Microsoft.EntityFrameworkCore;\n"
        "namespace Demo.Data;\n"
        "public class AppDbContext : DbContext\n"
        "{\n"
        "  public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) {}\n"
        "}\n",
        encoding="utf-8",
    )

    fn = type(
        "Fn",
        (),
        {
            "file_path": "App/Data/AppDbContext.cs",
            "function_name": "OnModelCreating",
            "summary": "Configures model mappings.",
            "input_format": "ModelBuilder",
            "output_format": "none",
        },
    )()
    prompt = _dotnet_unit_prompt(str(tmp_path), [fn], 1)
    repaired = _repair_common_dotnet_type_hallucinations(
        "private ApplicationDbContext Create() => null;",
        str(tmp_path),
    )

    assert _dotnet_dbcontext_types(str(tmp_path)) == ["AppDbContext"]
    assert "DbContext type names: AppDbContext" in prompt
    assert "ApplicationDbContext" not in repaired
    assert "AppDbContext" in repaired


def test_dotnet_prompt_includes_exact_member_signatures(tmp_path: Path):
    models = tmp_path / "App" / "Models"
    models.mkdir(parents=True)
    (models / "Case.cs").write_text(
        "namespace Demo.Models;\n"
        "public enum CaseStatus { Open, Closed }\n"
        "public class Case\n"
        "{\n"
        "  public long Id { get; set; }\n"
        "  public DateOnly CreatedOn { get; set; }\n"
        "  public Case(long id) {}\n"
        "  public void Close(CaseStatus status) {}\n"
        "}\n",
        encoding="utf-8",
    )

    fn = type(
        "Fn",
        (),
        {
            "file_path": "App/Models/Case.cs",
            "function_name": "Close",
            "summary": "Closes a case.",
            "input_format": "CaseStatus",
            "output_format": "none",
        },
    )()
    summaries = _dotnet_type_member_summaries(str(tmp_path))
    prompt = _dotnet_unit_prompt(str(tmp_path), [fn], 1)

    assert any("Id:long" in item for item in summaries)
    assert any("CreatedOn:DateOnly" in item for item in summaries)
    assert "CaseStatus" in prompt
    assert "Open" in prompt
    assert "Closed" in prompt


# --- .NET SDK detection -----------------------------------------------------
# A machine with only the .NET *runtime* has dotnet.exe on PATH, so the
# executable check passes and every `dotnet build` then fails with "No .NET SDKs
# were found". Each generated chunk gets rejected as uncompilable and the run
# blames the model for a missing toolchain. These pin the distinction.

import subprocess  # noqa: E402
import types  # noqa: E402

import pytest  # noqa: E402

from agents_orchestrator.testing_agent.Nodes import dispatch_test_types as _dtt  # noqa: E402


def _fake_run(stdout: str, returncode: int = 0):
    def run(cmd, **kwargs):
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")
    return run


def _uncached():
    """_dotnet_sdk_available is lru_cached; clear between cases.

    Tolerates the attribute being absent — one case monkeypatches the function
    itself with a plain lambda, which has no cache to clear.
    """
    clear = getattr(_dtt._dotnet_sdk_available, "cache_clear", None)
    if clear:
        clear()


def test_sdk_probe_false_when_only_runtimes_installed(monkeypatch):
    _uncached()
    monkeypatch.setattr(subprocess, "run", _fake_run(""))
    assert _dtt._dotnet_sdk_available() is False
    _uncached()


def test_sdk_probe_true_when_an_sdk_is_listed(monkeypatch):
    _uncached()
    monkeypatch.setattr(subprocess, "run", _fake_run("8.0.404 [C:\Program Files\dotnet\sdk]\n"))
    assert _dtt._dotnet_sdk_available() is True
    _uncached()


def test_sdk_probe_false_when_dotnet_cannot_be_run(monkeypatch):
    _uncached()

    def boom(cmd, **kwargs):
        raise FileNotFoundError("dotnet")
    monkeypatch.setattr(subprocess, "run", boom)
    assert _dtt._dotnet_sdk_available() is False
    _uncached()


def test_dotnet_unit_skill_names_the_missing_sdk_before_spending_model_calls(monkeypatch):
    """The failure must name the SDK and the fix, and must happen before generation."""
    import asyncio

    _uncached()
    monkeypatch.setattr(_dtt, "_dotnet_sdk_available", lambda: False)
    called = []
    monkeypatch.setattr(_dtt, "get_llm", lambda *a, **k: called.append(1))

    skill = types.SimpleNamespace(name="unit")
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(_dtt._run_dotnet_unit_skill({"work_dir": "."}, skill))

    msg = str(exc.value)
    assert ".NET SDK" in msg
    assert "aka.ms/dotnet/download" in msg
    assert not called, "the model was called before the toolchain was checked"
    _uncached()
