"""Phase 11.4 — shell-runtime skill dispatch + aggregator integration.

Tests cover:
1. Skill loader recognises `runtime: shell` frontmatter.
2. _run_shell_skill invokes the sandbox command, parses output, returns
   the dict shape the aggregator expects.
3. aggregate_test_results routes parsed_artifact into the right
   AggregatedResults field (mutation_results / dependency_vulns /
   security_findings) based on output_artifact_field.
4. All shell skills in disk load (mutation_testing, security_static,
   dependency_scan).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents_orchestrator.testing_agent.tools.skill_loader import (
    Skill,
    load_all_skills,
)


# --- Skill loader recognises runtime: shell -------------------------------

def test_loader_parses_runtime_shell():
    skills = load_all_skills(reload=True)
    assert "mutation_testing" in skills
    mut = skills["mutation_testing"]
    assert mut.runtime == "shell"
    assert mut.shell_command and "stryker" in mut.shell_command.lower()
    assert mut.output_parser == "stryker_json"
    assert mut.output_artifact_field == "mutation_results"


def test_loader_default_runtime_is_llm_for_existing_skills():
    """Backward compat — none of the 4 LLM-only skills declared `runtime`,
    so they must default to 'llm'."""
    skills = load_all_skills(reload=True)
    assert skills["unit"].runtime == "llm"
    assert skills["negative_edge"].runtime == "llm"
    assert skills["smoke"].runtime == "llm"
    assert skills["functional_api"].runtime == "llm"
    assert skills["functional_ui"].runtime == "llm"
    # Phase 11.3 skills also default to llm
    assert skills["integration"].runtime == "llm"
    assert skills["contract"].runtime == "llm"
    assert skills["accessibility"].runtime == "llm"
    assert skills["property_based"].runtime == "llm"


def test_all_3_shell_skills_load():
    """All 3 Phase 11.4 shell-capable skills load with the right metadata."""
    skills = load_all_skills(reload=True)
    for name in ("mutation_testing", "security_static", "dependency_scan"):
        assert name in skills, f"{name} SKILL.md did not load"
        s = skills[name]
        assert s.runtime == "shell"
        assert s.shell_command
        assert s.output_parser
        assert s.output_artifact_field


# --- _run_shell_skill -----------------------------------------------------

@pytest.mark.asyncio
async def test_run_shell_skill_invokes_sandbox_and_parser(tmp_path):
    """End-to-end: shell skill dispatched → sandbox.run called with the
    skill's command → parser invoked on the report path → returned dict
    has parsed_artifact + output_artifact_field set."""
    from agents_orchestrator.testing_agent.Nodes.dispatch_test_types import _run_shell_skill

    # Pre-create a stryker.json fixture at the expected report path
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    stryker_fixture = {
        "files": {
            "src/x.cs": {
                "mutants": [
                    {"status": "Killed"}, {"status": "Killed"},
                    {"status": "Survived"},
                ]
            }
        }
    }
    (reports_dir / "mutation_testing.json").write_text(json.dumps(stryker_fixture))

    # Stub a Skill instance (don't load from disk — we control everything)
    skill = MagicMock(spec=Skill)
    skill.name = "mutation_testing"
    skill.runtime = "shell"
    skill.shell_command = "python --version"
    skill.shell_timeout_s = 60
    skill.output_parser = "stryker_json"
    skill.output_artifact_field = "mutation_results"

    # Stub the sandbox to return a successful CmdResult
    fake_sandbox = MagicMock()
    fake_sandbox.run = MagicMock(return_value=MagicMock(exit_code=0, stdout="", stderr=""))

    state = {"work_dir": str(tmp_path)}

    with patch(
        "agents_orchestrator.testing_agent.tools.sandbox.base.get_default_sandbox",
        return_value=fake_sandbox,
    ):
        result = await _run_shell_skill(state, skill)

    assert result["skill_name"] == "mutation_testing"
    assert result["runtime"] == "shell"
    assert result["shell_exit_code"] == 0
    assert result["output_artifact_field"] == "mutation_results"
    assert result["parsed_artifact"] is not None
    # Validate the parser actually ran:
    assert result["parsed_artifact"]["mutants_total"] == 3
    assert result["parsed_artifact"]["mutants_killed"] == 2
    # 2 killed / (2+1) = 66.67%
    assert result["parsed_artifact"]["kill_rate_pct"] == pytest.approx(66.67, rel=1e-2)


@pytest.mark.asyncio
async def test_run_shell_skill_handles_missing_report(tmp_path):
    """Shell command runs but parser returns None (report missing) — must
    not crash; parsed_artifact stays None."""
    from agents_orchestrator.testing_agent.Nodes.dispatch_test_types import _run_shell_skill

    skill = MagicMock(spec=Skill)
    skill.name = "mutation_testing"
    skill.runtime = "shell"
    skill.shell_command = "python --version"
    skill.shell_timeout_s = 60
    skill.output_parser = "stryker_json"
    skill.output_artifact_field = "mutation_results"

    fake_sandbox = MagicMock()
    fake_sandbox.run = MagicMock(return_value=MagicMock(exit_code=0, stdout="", stderr=""))

    state = {"work_dir": str(tmp_path)}

    with patch(
        "agents_orchestrator.testing_agent.tools.sandbox.base.get_default_sandbox",
        return_value=fake_sandbox,
    ):
        result = await _run_shell_skill(state, skill)

    # Report file doesn't exist → parser returns None
    assert result["parsed_artifact"] is None
    assert result["shell_exit_code"] == 0  # but command ran fine


# --- Aggregator routes shell artifacts -----------------------------------

@pytest.mark.asyncio
async def test_aggregate_routes_mutation_results():
    """Shell skill with output_artifact_field=mutation_results → ends up
    in AggregatedResults.mutation_results."""
    from agents_orchestrator.testing_agent.Nodes.aggregate_test_results import aggregate_test_results

    state = {
        "generated_test_sets": [
            # Regular LLM unit skill
            {"skill_name": "unit", "test_file_path": "/x/test_unit.py",
             "test_framework": "pytest", "scenario_count": 5},
            # Shell mutation skill
            {"skill_name": "mutation_testing", "test_file_path": "",
             "test_framework": "mutation_testing", "scenario_count": 0,
             "runtime": "shell",
             "parsed_artifact": {
                 "tool": "stryker", "kill_rate_pct": 75.0,
                 "mutants_total": 20, "mutants_killed": 15, "mutants_survived": 5,
                 "mutants_timeout": 0, "mutants_no_coverage": 0,
                 "top_survivors": [],
             },
             "output_artifact_field": "mutation_results"},
        ],
        "skill_failures": [],
    }
    delta = await aggregate_test_results(state)
    agg = delta["aggregated_results"]
    assert agg["mutation_results"] is not None
    assert agg["mutation_results"]["kill_rate_pct"] == 75.0
    assert agg["mutation_results"]["mutants_killed"] == 15
    # generated_test_sets retains BOTH the LLM unit and the shell mutation entry
    skill_names = {s["skill_name"] for s in agg["generated_test_sets"]}
    assert skill_names == {"unit", "mutation_testing"}


@pytest.mark.asyncio
async def test_aggregate_routes_dependency_vulns():
    """Shell skill with output_artifact_field=dependency_vulns → list lands
    in AggregatedResults.dependency_vulns."""
    from agents_orchestrator.testing_agent.Nodes.aggregate_test_results import aggregate_test_results

    state = {
        "generated_test_sets": [
            {"skill_name": "dependency_scan", "test_file_path": "",
             "test_framework": "dependency_scan", "scenario_count": 0,
             "runtime": "shell",
             "parsed_artifact": [
                 {"source": "pip-audit", "package": "requests",
                  "installed_version": "2.20.0", "severity": "HIGH",
                  "cve": "CVE-2018-18074", "fix_versions": ["2.20.1"]},
                 {"source": "pip-audit", "package": "django",
                  "installed_version": "2.2.0", "severity": "MEDIUM",
                  "cve": "CVE-2019-1234", "fix_versions": ["2.2.1"]},
             ],
             "output_artifact_field": "dependency_vulns"},
        ],
        "skill_failures": [],
    }
    delta = await aggregate_test_results(state)
    agg = delta["aggregated_results"]
    assert len(agg["dependency_vulns"]) == 2
    pkgs = {v["package"] for v in agg["dependency_vulns"]}
    assert pkgs == {"requests", "django"}


@pytest.mark.asyncio
async def test_aggregate_routes_security_findings():
    """Shell skill with output_artifact_field=security_findings → findings
    merge with any existing pipeline-path security_findings."""
    from agents_orchestrator.testing_agent.Nodes.aggregate_test_results import aggregate_test_results

    state = {
        "generated_test_sets": [
            {"skill_name": "security_static", "test_file_path": "",
             "test_framework": "security_static", "scenario_count": 0,
             "runtime": "shell",
             "parsed_artifact": [
                 {"source": "bandit", "severity": "HIGH", "rule_id": "B105",
                  "file": "app/auth.py", "line": 42, "message": "hardcoded password"},
             ],
             "output_artifact_field": "security_findings"},
        ],
        "skill_failures": [],
        # Existing pipeline-path security findings
        "security_findings": [
            {"source": "trivy", "severity": "HIGH", "rule_id": "CVE-1",
             "file": "Dockerfile", "message": "vulnerable base image"},
        ],
    }
    delta = await aggregate_test_results(state)
    agg = delta["aggregated_results"]
    assert len(agg["security_findings"]) == 2  # 1 trivy + 1 bandit
    sources = {f["source"] for f in agg["security_findings"]}
    assert sources == {"trivy", "bandit"}


@pytest.mark.asyncio
async def test_aggregate_handles_shell_skill_with_no_parsed_artifact():
    """Shell skill ran but its parser returned None (report missing /
    malformed). The skill stays in generated_test_sets but no field
    routing happens — and no crash."""
    from agents_orchestrator.testing_agent.Nodes.aggregate_test_results import aggregate_test_results

    state = {
        "generated_test_sets": [
            {"skill_name": "mutation_testing", "test_file_path": "",
             "test_framework": "mutation_testing", "scenario_count": 0,
             "runtime": "shell",
             "parsed_artifact": None,
             "output_artifact_field": "mutation_results"},
        ],
        "skill_failures": [],
    }
    delta = await aggregate_test_results(state)
    agg = delta["aggregated_results"]
    assert agg["mutation_results"] is None
    # The skill is still represented in the test sets list
    assert any(s["skill_name"] == "mutation_testing" for s in agg["generated_test_sets"])
