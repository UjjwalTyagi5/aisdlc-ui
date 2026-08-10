"""Backward-compatibility test: legacy TestingArtifact JSON (without new
fan-out fields) must deserialize cleanly. New fields default to empty/None.
"""
from __future__ import annotations

import json

from shared.models.testing import (
    DefectEntry,
    FunctionalScenarioResult,
    TestingArtifact,
)
from agents_orchestrator.testing_agent.tools.artifact_builder import (
    _build_testing_artifact,
    _parse_skipped_tests,
)
from agents_orchestrator.testing_agent.Nodes.execute import run_code_testing_agent


LEGACY_ARTIFACT_JSON = json.dumps({
    "plan_test_case_count": 3,
    "test_cases": [],
    "status": "executed",
    "language": "python",
    "summary_md": "Legacy artifact",
    "artifact_files": ["test_plan.xlsx"],
})


def test_legacy_artifact_json_deserializes_with_defaults():
    artifact = TestingArtifact.model_validate_json(LEGACY_ARTIFACT_JSON)
    assert artifact.functional_results == []
    assert artifact.defect_log == []
    assert artifact.qa_report_html_path is None
    assert artifact.qa_report_pdf_path is None
    assert artifact.status == "executed"


def test_functional_scenario_result_round_trip():
    sr = FunctionalScenarioResult(
        scenario_id="FS-001",
        method="GET",
        path="/users/1",
        status_code_expected=200,
        status_code_actual=200,
        passed=True,
        response_sample='{"id": 1}',
    )
    j = sr.model_dump_json()
    restored = FunctionalScenarioResult.model_validate_json(j)
    assert restored == sr


def test_defect_entry_round_trip():
    de = DefectEntry(
        defect_id="DEF-001",
        severity="high",
        summary="Endpoint /users returned 500",
        stack_trace="Traceback...",
        reproducer='curl -X GET http://localhost/users',
    )
    restored = DefectEntry.model_validate_json(de.model_dump_json())
    assert restored == de
    assert restored.severity == "high"


def test_artifact_with_new_fields_serializes():
    artifact = TestingArtifact(
        plan_test_case_count=5,
        status="executed_with_failures",
        language="python",
        functional_results=[FunctionalScenarioResult(
            scenario_id="FS-001", method="GET", path="/health",
            status_code_expected=200, status_code_actual=200, passed=True,
        )],
        defect_log=[DefectEntry(defect_id="DEF-001", severity="critical", summary="x")],
        qa_report_html_path="/path/qa_report.html",
        qa_report_pdf_path=None,
    )
    j = artifact.model_dump_json()
    restored = TestingArtifact.model_validate_json(j)
    assert len(restored.functional_results) == 1
    assert len(restored.defect_log) == 1
    assert restored.qa_report_pdf_path is None


def test_artifact_marks_attempted_run_without_results_as_failed():
    artifact = _build_testing_artifact(
        {
            "test_run_attempted": True,
            "language": "dotnet",
            "test_plan": None,
        },
        ["test_plan.xlsx"],
    )

    assert artifact.status == "failed"


def test_ui_results_determine_status_even_with_stale_plan_cases():
    artifact = _build_testing_artifact(
        {
            "ui_test_results": [{"id": "UI-1", "status": "Pass"}],
            "language": "dotnet",
            "test_plan": type(
                "Plan",
                (),
                {
                    "test_cases": [
                        type(
                            "Case",
                            (),
                            {
                                "test_case_id": "TC-OLD",
                                "feature_or_function_tested": "old",
                                "scenario_type": "unit",
                            },
                        )()
                    ]
                },
            )(),
        },
        ["ui_test_results.html"],
    )

    assert artifact.status == "executed"
    assert artifact.test_execution is not None
    assert artifact.test_execution.framework == "ui_browser"


async def test_unit_run_skips_existing_tests_when_generated_file_missing(tmp_path):
    result = await run_code_testing_agent({
        "work_dir": str(tmp_path),
        "language": "dotnet",
        "generated_test_sets": [],
        "skill_failures": ["unit: generated C# was incomplete"],
    })

    assert result["test_runner_exit_code"] == 1
    assert "generated unit test file was not produced" in result["test_execution_summary"]


def test_artifact_failed_when_unit_generation_failed_even_if_existing_tests_ran(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "results.xml").write_text(
        '<testsuite tests="13" failures="0" errors="0" skipped="0" time="1"></testsuite>',
        encoding="utf-8",
    )

    artifact = _build_testing_artifact(
        {
            "work_dir": str(tmp_path),
            "language": "dotnet",
            "skill_failures": ["unit: generated C# was incomplete"],
            "generated_test_sets": [],
        },
        ["results.xml"],
    )

    assert artifact.status == "failed"
    assert artifact.test_execution is not None
    assert artifact.test_execution.total == 13


def test_parse_skipped_tests_recovers_dotnet_skip_reason_from_source(tmp_path):
    reports = tmp_path / "reports"
    tests = tmp_path / "RadAuthPortal.Tests"
    reports.mkdir()
    tests.mkdir()
    (reports / "results.xml").write_text(
        """
<testsuite tests="1" skipped="1">
  <testcase classname="RadAuthPortal.Tests.ActionLogServiceTests" name="LogDuplicateExamQuestion_NoAnswer_FormatsComment">
    <skipped />
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    (tests / "ActionLogServiceTests.cs").write_text(
        """
using Xunit;
public class ActionLogServiceTests
{
    [Fact(Skip = "Pending product implementation for duplicate action log capture.")]
    public void LogDuplicateExamQuestion_NoAnswer_FormatsComment() {}
}
""",
        encoding="utf-8",
    )

    skipped = _parse_skipped_tests(str(reports / "results.xml"), work_dir=str(tmp_path))

    assert skipped[0]["reason"] == "Pending product implementation for duplicate action log capture."


def test_parse_skipped_tests_uses_clear_metadata_message_when_reason_missing(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "results.xml").write_text(
        """
<testsuite tests="1" skipped="1">
  <testcase classname="Generated.Tests" name="SomeSkippedTest">
    <skipped />
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )

    skipped = _parse_skipped_tests(str(reports / "results.xml"), work_dir=str(tmp_path))

    assert skipped[0]["reason"] == "Runner did not emit a skip reason and no source-level skip annotation was found."
