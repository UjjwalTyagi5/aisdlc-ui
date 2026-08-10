"""Phase 11.1 — at-a-glance QA dashboard sections in chat reply.

Tests cover:
1. Test pyramid table renders when generated_test_sets is populated
2. Top uncovered files renders when coverage_files is populated
3. Skill failures renders when skill_failures is populated
4. None of these break the existing summary when omitted (backward compat)
5. parse_per_file_coverage helper round-trips Cobertura XML correctly
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from agents_orchestrator.testing_agent.tools.coverage_html import (
    parse_per_file_coverage,
)
from agents_orchestrator.testing_agent.tools.qa_summary import build_qa_summary


COBERTURA_FIXTURE = """<?xml version="1.0"?>
<coverage line-rate="0.65">
  <packages><package name="pkg1"><classes>
    <class filename="src/critical.py" line-rate="0.20" lines-covered="2" lines-valid="10"/>
    <class filename="src/main.py" line-rate="0.95" lines-covered="19" lines-valid="20"/>
    <class filename="src/medium.py" line-rate="0.55" lines-covered="11" lines-valid="20"/>
  </classes></package></packages>
</coverage>"""


# --- parse_per_file_coverage ----------------------------------------------

def test_parse_per_file_coverage_returns_all_classes(tmp_path):
    xml_path = tmp_path / "coverage.xml"
    xml_path.write_text(COBERTURA_FIXTURE)
    result = parse_per_file_coverage(str(xml_path))
    assert len(result) == 3
    by_name = {f["filename"]: f for f in result}
    assert by_name["src/critical.py"]["coverage_pct"] == pytest.approx(20.0)
    assert by_name["src/critical.py"]["statements"] == 10
    assert by_name["src/critical.py"]["missed"] == 8
    assert by_name["src/main.py"]["coverage_pct"] == pytest.approx(95.0)
    assert by_name["src/medium.py"]["coverage_pct"] == pytest.approx(55.0)


def test_parse_per_file_coverage_handles_missing(tmp_path):
    assert parse_per_file_coverage(str(tmp_path / "nope.xml")) == []


def test_parse_per_file_coverage_handles_malformed(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text("<not valid>")
    assert parse_per_file_coverage(str(bad)) == []


# --- test pyramid block ---------------------------------------------------

def test_pyramid_renders_with_generated_test_sets():
    summary = build_qa_summary(
        lang="python",
        runner_command="pytest",
        exec_=None,
        generated_test_sets=[
            {"skill_name": "unit", "test_framework": "pytest", "scenario_count": 12},
            {"skill_name": "negative_edge", "test_framework": "pytest", "scenario_count": 4},
            {"skill_name": "integration", "test_framework": "pytest", "scenario_count": 3},
        ],
    )
    assert "**Test pyramid:**" in summary
    assert "| Unit | pytest | 12 |" in summary
    assert "| Negative Edge | pytest | 4 |" in summary
    assert "| Integration | pytest | 3 |" in summary


def test_pyramid_skipped_when_empty():
    summary = build_qa_summary(lang="python", runner_command="pytest")
    assert "**Test pyramid:**" not in summary


# --- top uncovered files block --------------------------------------------

def test_top_uncovered_files_renders_top_3_lowest_first():
    summary = build_qa_summary(
        lang="python",
        runner_command="pytest",
        coverage_files=[
            {"filename": "main.py", "coverage_pct": 95.0, "statements": 20, "missed": 1},
            {"filename": "critical.py", "coverage_pct": 20.0, "statements": 10, "missed": 8},
            {"filename": "medium.py", "coverage_pct": 55.0, "statements": 20, "missed": 9},
            {"filename": "ok.py", "coverage_pct": 80.0, "statements": 5, "missed": 1},
        ],
    )
    assert "**Top uncovered application files:**" in summary
    # Critical should appear first (lowest coverage)
    crit_idx = summary.find("`critical.py`")
    medium_idx = summary.find("`medium.py`")
    ok_idx = summary.find("`ok.py`")
    assert crit_idx < medium_idx < ok_idx
    # main.py (95%) should NOT appear in top-3 because critical/medium/ok beat it
    assert "`main.py`" not in summary


def test_top_uncovered_files_skipped_when_empty():
    summary = build_qa_summary(lang="python", runner_command="pytest")
    assert "**Top uncovered application files:**" not in summary


# --- skill failures block --------------------------------------------------

def test_skill_failures_block_renders():
    summary = build_qa_summary(
        lang="python",
        runner_command="pytest",
        skill_failures=[
            "functional_api: TimeoutError: connection timed out after 10s",
            "smoke: ConnectionError: ECONNREFUSED",
        ],
    )
    assert "**Skill generation failures:**" in summary
    assert "TimeoutError" in summary
    assert "ConnectionError" in summary


def test_skill_failures_caps_at_5_with_overflow_line():
    summary = build_qa_summary(
        lang="python",
        runner_command="pytest",
        skill_failures=[f"skill_{i}: failed" for i in range(8)],
    )
    assert "**Skill generation failures:**" in summary
    assert "(+3 more)" in summary  # 8 failures - 5 shown = 3 overflow


# --- backward compatibility ------------------------------------------------

def test_legacy_call_without_dashboard_kwargs_still_works():
    """Pre-Phase-11 callers that don't pass the new kwargs must still get a
    valid summary string back."""
    summary = build_qa_summary(
        lang="dotnet",
        runner_command="dotnet test",
    )
    assert "## Testing completed" in summary
    # No new dashboard sections leak into legacy output
    assert "**Test pyramid:**" not in summary
    assert "**Top uncovered application files:**" not in summary
    assert "**Skill generation failures:**" not in summary


def test_full_dashboard_renders_alongside_existing_metrics():
    """All sections compose without conflict — the existing metrics block
    still shows alongside the new dashboard sections."""
    from shared.models import CoverageSummary, TestExecution

    summary = build_qa_summary(
        lang="python",
        runner_command="pytest",
        exec_=TestExecution(framework="pytest", total=15, passed=14, failed=1, duration_ms=1200),
        cov=CoverageSummary(coverage_pct=85.0, statements=100, missed=15),
        plan_test_case_count=12,
        generated_test_sets=[
            {"skill_name": "unit", "test_framework": "pytest", "scenario_count": 10},
        ],
        coverage_files=[
            {"filename": "x.py", "coverage_pct": 30.0, "statements": 20, "missed": 14},
        ],
        skill_failures=["smoke: no service URL"],
    )
    # Existing metrics
    assert "**Result:**" in summary
    assert "**Tests:** 15 total" in summary
    assert "**Application source coverage:**" in summary
    # New dashboard
    assert "**Test pyramid:**" in summary
    assert "**Top uncovered application files:**" in summary
    assert "**Skill generation failures:**" in summary


def test_summary_prefers_application_source_coverage_when_available():
    from shared.models import CoverageSummary

    summary = build_qa_summary(
        lang="dotnet",
        runner_command="dotnet test",
        cov=CoverageSummary(coverage_pct=12.7, statements=1201, missed=1048),
        coverage_files=[
            {"filename": "Program.cs", "bucket": "Application startup", "coverage_pct": 0.0, "statements": 58, "covered": 0, "missed": 58},
            {"filename": "Views/Cases/Create.cshtml", "bucket": "View / generated UI", "coverage_pct": 0.0, "statements": 38, "covered": 0, "missed": 38},
            {"filename": "Services/DuplicateCheckService.cs", "bucket": "Application source", "coverage_pct": 98.0, "statements": 100, "covered": 98, "missed": 2},
            {"filename": "Controllers/CasesController.cs", "bucket": "Application source", "coverage_pct": 0.0, "statements": 84, "covered": 0, "missed": 84},
        ],
    )

    assert "**Application source coverage:** 53.3%" in summary
    assert "**Overall coverage:** 12.7% line" in summary
    assert "`Controllers/CasesController.cs`" in summary
    assert "`Program.cs`" not in summary
    assert "`Views/Cases/Create.cshtml`" not in summary


def test_skipped_tests_render_with_reasons():
    summary = build_qa_summary(
        lang="dotnet",
        runner_command="dotnet test",
        skipped_tests=[{
            "name": "CannotBuildExternalDependencyScenario",
            "class": "GeneratedTests.Unit",
            "reason": "Skipped because dependency setup was not available.",
        }],
    )

    assert "**Skipped tests:**" in summary
    assert "GeneratedTests.Unit.CannotBuildExternalDependencyScenario" in summary
    assert "dependency setup" in summary


def test_skipped_tests_fallback_when_runner_omits_reasons():
    from shared.models import TestExecution

    summary = build_qa_summary(
        lang="dotnet",
        runner_command="dotnet test",
        exec_=TestExecution(framework="xunit", total=10, passed=7, skipped=3),
    )

    assert "**Skipped tests:**" in summary
    assert "3 test(s) were reported as skipped" in summary
    assert "did not include per-test skip reasons" in summary
