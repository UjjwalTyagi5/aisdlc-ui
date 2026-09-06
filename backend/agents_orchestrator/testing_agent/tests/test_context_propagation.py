"""Phase 11.2 — cross-agent context propagation regression tests.

Covers:
1. Testing agent's _fmt_testing renders all new TestingArtifact fields
   (functional_results, defect_log, security_findings, pr_coverage,
   qa_report_*_path) without crashing on missing data.
2. Backward compat — legacy artifacts (only status + execution + coverage)
   still format correctly.
3. dispatch_test_types._run_skill threads upstream_design + upstream_requirements
   into the skill render kwargs when the skill declares them as inputs.
4. _emit_testing_handoff publishes the full set of context_keys (not just 3).
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from config.context_broker import _fmt_testing


# --- _fmt_testing — extended render --------------------------------------

def test_fmt_testing_renders_all_new_fields():
    artifact = {
        "status": "executed_with_failures",
        "language": "dotnet",
        "plan_test_case_count": 12,
        "test_execution": {
            "framework": "xunit", "total": 13, "passed": 12, "failed": 1,
            "errors": 0, "skipped": 0, "duration_ms": 2193,
        },
        "coverage": {
            "coverage_pct": 12.08, "branch_coverage_pct": None,
            "statements": 1258, "missed": 1106,
        },
        "pr_coverage": {
            "coverage_pct": 87.0, "changed_lines_covered": 15,
            "changed_lines_total": 17, "files_changed": 3, "base_branch": "main",
        },
        "functional_results": [
            {"scenario_id": "FS-001", "method": "GET", "path": "/health",
             "status_code_expected": 200, "status_code_actual": 200, "passed": True},
            {"scenario_id": "FS-002", "method": "GET", "path": "/users/1",
             "status_code_expected": 200, "status_code_actual": 404, "passed": False},
        ],
        "defect_log": [
            {"defect_id": "DEF-001", "severity": "critical", "summary": "x"},
            {"defect_id": "DEF-002", "severity": "high", "summary": "y"},
            {"defect_id": "DEF-003", "severity": "high", "summary": "z"},
            {"defect_id": "DEF-004", "severity": "medium", "summary": "w"},
        ],
        "security_findings": [
            {"source": "trivy", "severity": "HIGH", "rule_id": "CVE-1", "file": "x", "message": "y"},
            {"source": "bandit", "severity": "MEDIUM", "rule_id": "B-1", "file": "z", "message": "w"},
        ],
        "pipeline_run": {
            "run_id": 42, "state": "completed", "result": "succeeded",
            "project": "carelon", "pipeline_id": 1,
        },
        "qa_report_html_path": "/path/qa_report.html",
        "qa_report_pdf_path": "/path/qa_report.pdf",
        "summary_md": "Brief summary",
        "artifact_files": ["test_plan.xlsx", "coverage_report.xml"],
    }
    out = _fmt_testing(artifact)
    # All sections appear
    assert "STATUS: executed_with_failures" in out
    assert "LANGUAGE: dotnet" in out
    assert "EXECUTION: 13 total / 12 passed / 1 failed" in out
    assert "COVERAGE: 12.08% line" in out
    assert "PR COVERAGE: 87.0% changed lines covered" in out
    assert "FUNCTIONAL API: 1/2 scenarios passed" in out
    assert "DEFECT LOG: 4 total — 1 critical / 2 high / 1 medium / 0 low" in out
    assert "SECURITY FINDINGS: 2 total (1 critical/high)" in out
    assert "PIPELINE RUN: #42 completed / succeeded" in out
    assert "QA REPORT: available as HTML + PDF" in out


def test_fmt_testing_legacy_artifact_still_works():
    """A pre-Phase-11 artifact (no functional_results / defect_log / etc.)
    must still format cleanly. This is the deployment-formatter compat
    guarantee from the spec."""
    artifact = {
        "status": "executed",
        "plan_test_case_count": 5,
        "test_execution": {"framework": "pytest", "total": 5, "passed": 5,
                           "failed": 0, "errors": 0, "skipped": 0, "duration_ms": 240},
        "coverage": {"coverage_pct": 88.0, "statements": 100, "missed": 12},
        "summary_md": "All passed",
        "artifact_files": [],
    }
    out = _fmt_testing(artifact)
    assert "STATUS: executed" in out
    assert "EXECUTION: 5 total / 5 passed" in out
    assert "COVERAGE: 88.0% line" in out
    # New sections do NOT leak
    assert "FUNCTIONAL API:" not in out
    assert "DEFECT LOG:" not in out
    assert "PR COVERAGE:" not in out
    assert "QA REPORT:" not in out


def test_fmt_testing_handles_empty_artifact():
    """Edge case — artifact has only status. Must not crash."""
    out = _fmt_testing({"status": "failed"})
    assert "STATUS: failed" in out


# --- _run_skill upstream context threading -------------------------------

@pytest.mark.asyncio
async def test_run_skill_threads_upstream_design_into_render_kwargs():
    """Skill that declares upstream_design + api_contracts as inputs receives
    them in render kwargs. Pre-Phase-11.2 they were missing — only language /
    code_analysis / test_plan / target_url / openapi_spec_json reached skills."""
    from agents_orchestrator.testing_agent.Nodes.dispatch_test_types import _run_skill

    captured_render_kwargs = {}

    class FakeSkill:
        name = "fake_contract_skill"
        inputs = ["upstream_design", "api_contracts", "language"]

        def render(self, **kwargs):
            captured_render_kwargs.update(kwargs)
            return "rendered"

    state = {
        "work_dir": "/tmp",
        "language": "python",
        "code_analysis": MagicMock(model_dump_json=lambda: "{}"),
        "test_plan": MagicMock(model_dump_json=lambda: "{}"),
        "upstream_design": {
            "api_contracts": {"GET /users": {"response": "User[]"}},
            "db_schema": {"User": ["id", "name"]},
        },
        "upstream_requirements": {"stories": [{"id": "REQ-01"}]},
    }

    fake_chain = MagicMock()
    fake_chain.invoke = lambda prompt: "import pytest\ndef test_x(): pass"
    fake_llm = MagicMock()
    fake_llm.__or__ = lambda self, other: fake_chain

    with patch(
        "agents_orchestrator.testing_agent.Nodes.dispatch_test_types.get_llm",
        return_value=fake_llm,
    ):
        await _run_skill(state, FakeSkill())

    # api_contracts should be a JSON string of the contracts dict
    assert "api_contracts" in captured_render_kwargs
    assert "GET /users" in captured_render_kwargs["api_contracts"]
    # upstream_design should be passed (the full blob)
    assert "upstream_design" in captured_render_kwargs
    assert "db_schema" in captured_render_kwargs["upstream_design"]
    # Skills that don't declare these inputs see no change
    assert "language" in captured_render_kwargs


# --- _emit_testing_handoff context_keys ----------------------------------

@pytest.mark.asyncio
async def test_emit_testing_handoff_persists_an_artifact_the_deployment_gate_can_read():
    """The hand-off is a PERSISTED ARTIFACT, not a published context-key list.

    This pinned `_handoff_handle` and `payload.context_keys` — a context-broker
    design that has since been replaced. `_emit_testing_handoff` now writes
    `testing_artifacts` through `patch_session_artifacts`, and that row is what the
    deployment agent actually reads: `pipeline_app._testing_gate` blocks unless
    `testing_artifacts.status` is "executed" or "pipeline_completed" (and blocks
    outright when the artifact is missing). So the contract worth pinning is the
    shape of the row, not the advertisement that used to precede it.
    """
    from agents_orchestrator.testing_agent import testing_agent_api

    captured = {}

    async def fake_patch(session_id, artifacts, **kwargs):
        captured["session_id"] = session_id
        captured["artifacts"] = artifacts

    with patch.object(testing_agent_api, "patch_session_artifacts", new=fake_patch):
        await testing_agent_api._emit_testing_handoff("session-XYZ", {"final_user_message": "Test"})

    assert captured, "nothing was persisted — the deployment gate would find no artifact"
    assert captured["session_id"] == "session-XYZ"
    artifact = captured["artifacts"]["testing_artifacts"]

    # The gate reads `status` first and blocks on anything outside its pass set. A run
    # that produced nothing must still leave a row saying so, or deployment cannot tell
    # "testing failed" from "testing never ran".
    assert artifact["status"] == "failed"
    assert artifact["summary_md"] == "Test"
    assert artifact["plan_test_case_count"] == 0


@pytest.mark.asyncio
async def test_emit_testing_handoff_prefers_the_real_artifact_over_the_fallback():
    """A finished run persists what it produced; the synthesized "failed" row is only
    for the case where the graph ended without an artifact at all."""
    from agents_orchestrator.testing_agent import testing_agent_api
    from shared.models import TestingArtifact

    real = TestingArtifact(
        plan_test_case_count=3, test_cases=[], status="executed",
        language="python", summary_md="ran 3", artifact_files=[],
    )
    captured = {}

    async def fake_patch(session_id, artifacts, **kwargs):
        captured["artifacts"] = artifacts

    with patch.object(testing_agent_api, "patch_session_artifacts", new=fake_patch):
        await testing_agent_api._emit_testing_handoff(
            "s-1", {"testing_artifact_json": real.model_dump_json()},
        )

    artifact = captured["artifacts"]["testing_artifacts"]
    assert artifact["status"] == "executed", "the real artifact was replaced by the fallback"
    assert artifact["plan_test_case_count"] == 3
