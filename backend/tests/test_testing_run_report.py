"""The test run report: the run's numbers, shaped for the page, with the truth in it.

The Testing page rendered the agent's prose as raw text. The run had written every
number to disk; this reads them into one JSON the page renders as a report. The
report also names what the prose hid: a generated suite that jest could not parse
counts as a high defect and turns a "passed" verdict into "partial".
"""
from __future__ import annotations

import json

from agents_orchestrator.testing_agent import run_report as rr

JUNIT = """<?xml version="1.0"?>
<testsuites name="jest tests" tests="3" failures="1">
  <testsuite name="LinkService" tests="3" failures="1" time="0.12">
    <testcase classname="LinkService slug" name="generates a slug" time="0.005"/>
    <testcase classname="LinkService slug" name="rejects duplicates" time="0.001">
      <failure message="expected 409 got 200">AssertionError</failure>
    </testcase>
    <testcase classname="LinkService slug" name="skipped one"><skipped/></testcase>
  </testsuite>
</testsuites>
"""

COBERTURA = """<?xml version="1.0"?>
<coverage line-rate="0.45">
  <packages><package name="src">
    <classes>
      <class name="LinkService.js" filename="src/services/LinkService.js" line-rate="0.3">
        <lines><line number="1" hits="1"/><line number="2" hits="0"/><line number="3" hits="0"/></lines>
      </class>
      <class name="index.ejs" filename="src/views/index.ejs" line-rate="0">
        <lines><line number="1" hits="0"/></lines>
      </class>
    </classes>
  </package></packages>
</coverage>
"""

ARTIFACT = {
    "test_execution": {"framework": "pytest", "total": 3, "passed": 1, "failed": 1, "skipped": 1, "errors": 0, "duration_ms": 120},
    "coverage": {"statements": 4, "missed": 3, "coverage_pct": 25.0, "branch_coverage_pct": 10.0},
    "test_cases": [{"test_case_id": "TC-01", "feature_or_function_tested": "createLink", "test_summary": "slug", "scenario_type": "Happy Path", "test_steps": "1. call", "test_data": "url", "expected_result": "slug"}],
    "defect_log": [{"defect_id": "DEF-001", "severity": "high", "summary": "dup check", "stack_trace": "trace"}],
    "language": "react", "runner_command": "npx jest", "status": "executed",
    "artifact_files": ["results.xml"], "summary_md": "## Testing completed",
}

STDERR = """PASS tests/link.test.js
FAIL src/generated_unit.test.js
  ● Test suite failed to run

    Jest encountered an unexpected token

    Jest failed to parse a file.
"""


def _run_dir(tmp_path, *, artifact=ARTIFACT, junit=JUNIT, cov=COBERTURA):
    (tmp_path / "testing_artifact.json").write_text(json.dumps(artifact), encoding="utf-8")
    (tmp_path / "results.xml").write_text(junit, encoding="utf-8")
    (tmp_path / "coverage_report.xml").write_text(cov, encoding="utf-8")
    (tmp_path / "qa_report.html").write_text("<html/>", encoding="utf-8")
    return str(tmp_path)


def test_the_report_carries_the_numbers_each_test_and_each_file(tmp_path):
    r = rr.build_run_report(_run_dir(tmp_path), session_id="s1", state={
        "clone_target": {"project": "QuickLink", "repo": "QuickLink", "branch": "main"},
        "selected_test_types": ["unit"], "test_config": {"coverage_threshold": 80},
    })
    assert r["verdict"] == "failed"
    assert r["framework"] == "jest", "the artifact's 'pytest' label is wrong for a react run"
    assert r["execution"] == {"total": 3, "passed": 1, "failed": 1, "skipped": 1, "errors": 0, "durationMs": 120}
    assert [t["status"] for t in r["tests"]] == ["passed", "failed", "skipped"]
    assert r["tests"][1]["message"] == "expected 409 got 200"
    assert r["coverage"]["linePct"] == 25.0 and r["coverage"]["thresholdPct"] == 80.0
    assert [f["path"] for f in r["coverage"]["files"]] == ["src/views/index.ejs", "src/services/LinkService.js"], "worst first"
    assert r["coverage"]["applicationPct"] == 33.3, "views are not application source"
    assert r["target"] == {"project": "QuickLink", "repo": "QuickLink", "branch": "main"}
    assert r["testCases"][0]["id"] == "TC-01" and r["testCases"][0]["scenarioType"] == "Happy Path"
    assert r["defects"][0] == {"id": "DEF-001", "severity": "high", "summary": "dup check", "detail": "trace"}
    assert r["qaReportAvailable"] is True


def test_a_generated_suite_that_could_not_run_is_a_defect_and_no_longer_a_pass(tmp_path):
    artifact = {**ARTIFACT, "test_execution": {**ARTIFACT["test_execution"], "failed": 0, "skipped": 0, "passed": 3}}
    r = rr.build_run_report(_run_dir(tmp_path, artifact=artifact, junit=JUNIT.replace("<failure", "<!--").replace("</failure>", "-->").replace("<skipped/>", "")),
                            session_id="s1", state={
        "test_runner_stderr": STDERR,
        "generated_test_sets": [{"test_file_path": "C:\\\\work\\\\src\\\\generated_unit.test.js"}],
        "work_dir": "C:\\\\work",
    })
    assert r["verdict"] == "partial"
    assert r["unrunnableSuites"] == [{"file": "src/generated_unit.test.js", "reason": "Jest encountered an unexpected token"}]
    assert any(d["id"].startswith("DEF-SUITE") and d["severity"] == "high" for d in r["defects"])
    assert r["generatedFiles"] == ["src/generated_unit.test.js"]


def test_a_repo_suite_failing_to_run_is_not_blamed_on_the_agent():
    assert rr.unrunnable_suites(STDERR.replace("generated_unit", "legacy"), ["src/generated_unit.test.js"]) == []


def test_persisted_report_is_read_back_after_the_state_is_gone(tmp_path):
    out = _run_dir(tmp_path)
    written = rr.write_run_report(out, session_id="s1", state={"clone_target": {"repo": "R", "branch": "b", "project": "P"}})
    assert written["target"]["repo"] == "R"
    read = rr.read_run_report(out, session_id="s1", state=None)
    assert read["target"]["repo"] == "R", "the clone target came from the state, kept by the file"


def test_no_run_means_no_report(tmp_path):
    assert rr.build_run_report(str(tmp_path / "missing"), session_id="s") is None
    (tmp_path / "empty").mkdir()
    r = rr.build_run_report(str(tmp_path / "empty"), session_id="s")
    assert r["verdict"] == "no_tests" and r["tests"] == []
