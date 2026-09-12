"""Dependency and Risk's deterministic analysis: inventory, manifests, EOL, graph, risk.

No model is involved anywhere in these functions — a migration-risk number has to be
reproducible and explainable, so it is computed, and the agent only narrates it.
"""
from __future__ import annotations

from datetime import date

import pytest

from agents_orchestrator.discovery_agent.analysis.assessment import (
    assess_repository,
    assessment_markdown,
)
from agents_orchestrator.discovery_agent.analysis.eol import (
    Runtime,
    deprecated_reason,
    runtime_status,
)
from agents_orchestrator.discovery_agent.analysis.graph import build_dependency_graph, fan_in
from agents_orchestrator.discovery_agent.analysis.inventory import scan_inventory
from agents_orchestrator.discovery_agent.analysis.manifests import parse_module_manifest
from agents_orchestrator.discovery_agent.analysis.risk import score_module
from tests.discovery.legacy_fixture import build_legacy_repo

AS_OF = date(2026, 9, 10)


@pytest.fixture
def repo(tmp_path):
    return build_legacy_repo(tmp_path / "legacy")


def _by_name(modules):
    return {m.name: m for m in modules}


# ── inventory ────────────────────────────────────────────────────────────────


def test_inventory_finds_one_module_per_project_manifest(repo):
    inv = scan_inventory(repo)
    mods = _by_name(inv.modules)
    assert set(mods) == {
        "Billing.Web", "Billing.Core", "Billing.Core.Tests", "billing-notifier", "billing-batch",
    }
    assert mods["Billing.Web"].ecosystem == ".NET"
    assert mods["billing-notifier"].ecosystem == "Node.js"
    assert mods["billing-batch"].ecosystem == "Java"
    assert mods["Billing.Web"].path == "src/Billing.Web"


def test_inventory_skips_vendored_and_build_output(repo):
    inv = scan_inventory(repo)
    mods = _by_name(inv.modules)
    # bin/ and node_modules/ are not the application's code.
    assert mods["Billing.Web"].files == 5
    assert mods["billing-notifier"].files == 3
    assert inv.file_count == sum(m.files for m in inv.modules) + 1  # + README.md at root


def test_inventory_counts_loc_by_language(repo):
    web = _by_name(scan_inventory(repo).modules)["Billing.Web"]
    assert web.languages.get("C#", 0) > 400
    assert web.loc >= web.languages["C#"]


def test_inventory_detects_platform_blockers(repo):
    web = _by_name(scan_inventory(repo).modules)["Billing.Web"]
    assert "webforms" in web.blockers
    assert "wcf_server" in web.blockers
    assert _by_name(scan_inventory(repo).modules)["Billing.Core"].blockers == []


def test_inventory_detects_tests(repo):
    mods = _by_name(scan_inventory(repo).modules)
    assert mods["Billing.Core.Tests"].has_tests is True
    assert mods["billing-notifier"].has_tests is True
    assert mods["Billing.Web"].has_tests is False


# ── manifests ────────────────────────────────────────────────────────────────


def test_legacy_csproj_and_packages_config(repo):
    inv = scan_inventory(repo)
    web = _by_name(inv.modules)["Billing.Web"]
    facts = parse_module_manifest(repo, web)
    assert facts.runtime == Runtime(name=".NET Framework", version="4.5.2")
    packages = {d.name: d.version for d in facts.dependencies if d.kind == "package"}
    assert packages["Newtonsoft.Json"] == "9.0.1"
    assert packages["WindowsAzure.Storage"] == "8.1.4"
    assemblies = {d.name for d in facts.dependencies if d.kind == "framework-assembly"}
    assert {"System.Web", "System.ServiceModel"} <= assemblies
    assert facts.project_references == ["src/Billing.Core"]


def test_sdk_style_csproj(repo):
    core = _by_name(scan_inventory(repo).modules)["Billing.Core"]
    facts = parse_module_manifest(repo, core)
    assert facts.runtime == Runtime(name=".NET", version="6.0")
    assert {d.name: d.version for d in facts.dependencies} == {
        "Dapper": "2.0.123", "Serilog": "2.12.0",
    }


def test_package_json_and_pom(repo):
    mods = _by_name(scan_inventory(repo).modules)
    node = parse_module_manifest(repo, mods["billing-notifier"])
    assert node.runtime == Runtime(name="Node.js", version="14")
    assert {"express", "request", "jest"} <= {d.name for d in node.dependencies}
    java = parse_module_manifest(repo, mods["billing-batch"])
    assert java.runtime == Runtime(name="Java", version="8")
    assert [d.name for d in java.dependencies] == ["org.springframework:spring-core"]


def test_a_malformed_manifest_does_not_raise(tmp_path):
    (tmp_path / "Broken").mkdir()
    (tmp_path / "Broken" / "Broken.csproj").write_text("<Project><not closed", encoding="utf-8")
    mod = scan_inventory(tmp_path).modules[0]
    facts = parse_module_manifest(tmp_path, mod)
    assert facts.runtime is None
    assert facts.dependencies == []
    assert facts.parse_error


# ── EOL ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "runtime,expected",
    [
        (Runtime(".NET Framework", "4.5.2"), "eol"),
        (Runtime(".NET Framework", "4.8"), "legacy"),
        (Runtime(".NET", "6.0"), "eol"),
        (Runtime(".NET", "8.0"), "approaching"),
        (Runtime(".NET", "10.0"), "supported"),
        (Runtime("Node.js", "14"), "eol"),
        (Runtime("Java", "8"), "legacy"),
        (Runtime("Java", "7"), "eol"),
        (Runtime("Python", "2.7"), "eol"),
        (Runtime("Python", "3.12"), "supported"),
        (Runtime("Cobol", "85"), "unknown"),
    ],
)
def test_runtime_status(runtime, expected):
    assert runtime_status(runtime, AS_OF).status == expected


def test_runtime_status_carries_the_date_it_ended():
    status = runtime_status(Runtime(".NET Framework", "4.5.2"), AS_OF)
    assert status.eol_date == date(2022, 4, 26)


def test_deprecated_packages_are_named_with_a_reason():
    assert deprecated_reason(".NET", "WindowsAzure.Storage")
    assert deprecated_reason("Node.js", "request")
    assert deprecated_reason(".NET", "Dapper") is None


# ── graph ────────────────────────────────────────────────────────────────────


def test_dependency_graph_has_internal_and_external_edges(repo):
    inv = scan_inventory(repo)
    manifests = {m.name: parse_module_manifest(repo, m) for m in inv.modules}
    graph = build_dependency_graph(inv.modules, manifests)
    edges = {(e["from"], e["to"], e["type"]) for e in graph["edges"]}
    assert ("module:Billing.Web", "module:Billing.Core", "project") in edges
    assert ("module:Billing.Core.Tests", "module:Billing.Core", "project") in edges
    assert ("module:Billing.Web", "package:.NET:WindowsAzure.Storage", "package") in edges
    assert fan_in(inv.modules, manifests)["Billing.Core"] == 2


# ── risk ─────────────────────────────────────────────────────────────────────


def test_webforms_module_is_manual_and_says_why(repo):
    artifacts = assess_repository(repo, as_of=AS_OF)
    web = next(m for m in artifacts["modules"] if m["name"] == "Billing.Web")
    assert web["risk"]["tier"] == "manual"
    factors = {f["factor"] for f in web["risk"]["factors"]}
    assert {"platform", "runtime", "deprecated_dependencies", "tests"} <= factors
    assert 0 <= web["risk"]["score"] <= 100


def test_small_modern_library_scores_lower_than_the_legacy_web_app(repo):
    artifacts = assess_repository(repo, as_of=AS_OF)
    mods = {m["name"]: m for m in artifacts["modules"]}
    assert mods["Billing.Core"]["risk"]["score"] < mods["Billing.Web"]["risk"]["score"]


def test_cross_language_target_is_never_mechanical(repo):
    same = {m["name"]: m for m in assess_repository(repo, as_of=AS_OF, target_stack=".NET 8")["modules"]}
    cross = {m["name"]: m for m in assess_repository(repo, as_of=AS_OF, target_stack="Java 21 / Spring Boot")["modules"]}
    assert same["Billing.Core"]["risk"]["tier"] == "mechanical"
    assert cross["Billing.Core"]["risk"]["tier"] != "mechanical"


def test_score_module_is_bounded():
    from agents_orchestrator.discovery_agent.analysis.eol import RuntimeStatus
    from agents_orchestrator.discovery_agent.analysis.inventory import ModuleFacts

    huge = ModuleFacts(
        name="x", path="x", ecosystem=".NET", manifest="x.csproj", files=9999, loc=900_000,
        languages={"C#": 900_000}, has_tests=False,
        blockers=["webforms", "wcf_server", "winforms", "remoting"],
    )
    risk = score_module(
        huge, runtime_status=RuntimeStatus("eol", None, ""), dependency_count=500,
        deprecated=20, vulnerable_high=20, vulnerable_other=0, fan_in=40, cross_language=True,
    )
    assert risk.score == 100
    assert risk.tier == "manual"


# ── the assessment ───────────────────────────────────────────────────────────


def test_assessment_summary_agrees_with_its_modules(repo):
    artifacts = assess_repository(repo, as_of=AS_OF)
    summary = artifacts["summary"]
    assert summary["module_count"] == len(artifacts["modules"]) == 5
    tiers = summary["tier_counts"]
    assert sum(tiers.values()) == 5
    assert summary["flag_counts"]["eol"] == len(artifacts["flags"]["eol"])
    assert artifacts["schema_version"] == 1
    assert artifacts["golden_master"]["status"] == "not_captured"


def test_eol_and_deprecated_flags_name_the_module(repo):
    flags = assess_repository(repo, as_of=AS_OF)["flags"]
    eol_modules = {f["module"] for f in flags["eol"]}
    assert "Billing.Web" in eol_modules
    assert "billing-notifier" in eol_modules
    deprecated = {(f["module"], f["package"]) for f in flags["deprecated"]}
    assert ("Billing.Web", "WindowsAzure.Storage") in deprecated
    assert ("billing-notifier", "request") in deprecated


def test_vulnerabilities_are_attributed_to_the_module_that_declares_the_package(repo):
    vulns = [{
        "package": "Newtonsoft.Json", "installed_version": "9.0.1", "severity": "high",
        "cve": "CVE-2024-21907", "fixed_version": "13.0.1",
        "target": "src/Billing.Web/packages.config", "title": "DoS",
    }]
    artifacts = assess_repository(repo, as_of=AS_OF, vulnerabilities=vulns)
    web = next(m for m in artifacts["modules"] if m["name"] == "Billing.Web")
    dep = next(d for d in web["dependencies"] if d["name"] == "Newtonsoft.Json")
    assert dep["status"] == "vulnerable"
    assert dep["vulnerabilities"][0]["cve"] == "CVE-2024-21907"
    assert artifacts["flags"]["vulnerable"][0]["module"] == "Billing.Web"


def test_markdown_is_a_document(repo):
    md = assessment_markdown(assess_repository(repo, as_of=AS_OF))
    assert md.startswith("# ")
    assert md.count("\n## ") >= 4
    assert "Billing.Web" in md


def test_vendored_front_end_libraries_do_not_count_as_code(tmp_path):
    """Found on a real legacy repository (eShopModernizing): every ASP.NET app carried
    ~40k lines of jQuery, jQuery-slim and Bootstrap under Scripts/, so each web module
    maxed out the size factor on code nobody migrates. Vendored files still count as
    files — they are there — but not as lines of code."""
    web = tmp_path / "Web"
    (web / "Scripts" / "WebForms" / "MSAjax").mkdir(parents=True)
    (web / "wwwroot" / "lib" / "chart").mkdir(parents=True)
    (web / "Web.csproj").write_text("<Project><PropertyGroup><TargetFrameworkVersion>v4.7.2</TargetFrameworkVersion></PropertyGroup></Project>", encoding="utf-8")
    (web / "Home.cs").write_text("class Home {}\nclass B {}\n", encoding="utf-8")
    big = "var x = 1;\n" * 5000
    for name in ("jquery-3.3.1.js", "jquery-3.3.1.slim.js", "bootstrap.bundle.js", "modernizr-2.8.3.js",
                 "app.min.js", "_references.js"):
        (web / "Scripts" / name).write_text(big, encoding="utf-8")
    (web / "Scripts" / "WebForms" / "MSAjax" / "MicrosoftAjax.js").write_text(big, encoding="utf-8")
    (web / "wwwroot" / "lib" / "chart" / "chart.js").write_text(big, encoding="utf-8")
    (web / "Scripts" / "catalog.js").write_text("function load() {}\nload();\n", encoding="utf-8")

    module = scan_inventory(tmp_path).modules[0]
    assert module.files == 11
    assert module.vendored_files == 8
    assert module.languages.get("JavaScript") == 2   # only the app's own catalog.js
    assert module.loc == 4                            # Home.cs (2) + catalog.js (2)


def test_the_report_groups_vulnerabilities_by_package_worst_first():
    """A real legacy system has hundreds of findings. The report names each vulnerable
    package once — counts by severity and the worst CVEs — rather than one line per CVE;
    the stored assessment keeps every finding."""
    from agents_orchestrator.discovery_agent.analysis.assessment import _vulnerability_lines

    findings = [
        {"module": "web", "package": "jackson-databind", "version": "2.9.10.4", "cve": f"CVE-2020-{i}",
         "severity": "HIGH"} for i in range(5)
    ] + [
        {"module": "web", "package": "jackson-databind", "version": "2.9.10.4", "cve": "CVE-2020-9999",
         "severity": "CRITICAL"},
        {"module": "core", "package": "junit", "version": "4.12", "cve": "CVE-2020-15250", "severity": "MEDIUM"},
    ]
    lines = _vulnerability_lines(findings)
    assert len(lines) == 2
    assert lines[0].startswith("- **web** — `jackson-databind` 2.9.10.4: 6 known vulnerabilities (1 critical, 5 high)")
    assert "CVE-2020-9999 (CRITICAL)" in lines[0] and "and 3 more" in lines[0]
    assert lines[1] == "- **core** — `junit` 4.12: 1 known vulnerability (1 medium) — CVE-2020-15250 (MEDIUM)."
