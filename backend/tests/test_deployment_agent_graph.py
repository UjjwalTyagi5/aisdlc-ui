"""Deployment agent — the copilot's deploy stage must GENERATE the deployment
package (Dockerfile, pipeline yaml, k8s manifests, runbooks) via `stage_deploy_file`,
not just an LLM release-decision report.

Root cause (live diagnosis): the deploy agent produced a release report but
`generated/deployment` stayed empty because it never called `stage_deploy_file`, so
`session.staged_files` was empty and `_capture_stage_files` had nothing to persist
(covered by tests/copilot/test_capture_stage_files.py). This file covers the other
half: (1) the system prompt makes staging the files the mandatory, primary task, and
(2) `submit_release` now refuses to finalize when nothing was staged, forcing the
agent back to `stage_deploy_file` instead of silently accepting a files-less report.
"""
import json

import pytest

from agents_orchestrator.deployment_agent.prompts.deploy_prompt import DEPLOY_SYSTEM_PROMPT
from agents_orchestrator.deployment_agent.tools.deploy_tools import (
    stage_deploy_file,
    submit_release,
)
from agents_orchestrator.deployment_agent.config.session_state import get_session
from config.ws_helper import set_session_id, reset_session_id


def test_deployment_graph_importable():
    from agents_orchestrator.deployment_agent.agents.deployer import app
    assert app is not None
    assert hasattr(app, "ainvoke")


def test_deploy_prompt_mandates_stage_deploy_file():
    """The prompt must make generating the package via stage_deploy_file the
    PRIMARY, mandatory deliverable — not an optional step subordinate to the
    release-decision report."""
    prompt = DEPLOY_SYSTEM_PROMPT
    assert len(prompt) > 100
    lower = prompt.lower()
    assert "mandatory" in lower
    assert prompt.count("stage_deploy_file") >= 3
    # It must explicitly say submit_release depends on files having been staged first.
    assert "submit_release" in lower and "staged" in lower
    primary_idx = lower.find("primary")
    submit_release_tool_idx = lower.find("- submit_release(")
    assert primary_idx != -1 and submit_release_tool_idx != -1
    assert primary_idx < submit_release_tool_idx


@pytest.fixture
def deploy_session():
    token = set_session_id("test-submit-release-session")
    s = get_session("test-submit-release-session")
    s.repo_name = "TestRepo"
    s.mode = "branch"
    s.source_branch = "feature/x"
    s.environment = "staging"
    s.deploy_via = "azure_pipelines"
    yield s
    reset_session_id(token)
    s.staged_files = []
    s.last_artifact = None


_RELEASE_PAYLOAD = json.dumps({
    "summary": "Deploys the API service to staging via the generated k8s manifests.",
    "readiness": "ready",
    "risk_score": "low",
    "risk_rationale": "No DB migrations; tests green.",
    "gate_summary": [{"name": "Tests", "status": "pass", "note": "83 passed"}],
    "deploy_runbook": "1. Build image\n2. kubectl apply\n3. Verify health endpoint",
    "rollback_runbook": "1. kubectl rollout undo\n2. Verify previous version healthy",
    "compliance_evidence": {"gate_approvals": ["Tests"], "sbom_present": False},
    "release_decision": "go",
    "release_justification": "All gates green, low risk change.",
})


@pytest.mark.asyncio
async def test_submit_release_rejects_when_no_files_staged(deploy_session):
    """This is the fix's core guarantee: an LLM that skips straight to a release
    report without generating any deployment file must be told to go back and
    stage the package, instead of the report being silently accepted."""
    result = await submit_release.ainvoke({"release_json": _RELEASE_PAYLOAD})

    assert "error" in result.lower()
    assert "stage_deploy_file" in result.lower()
    assert deploy_session.last_artifact is None


@pytest.mark.asyncio
async def test_submit_release_succeeds_once_files_are_staged(deploy_session):
    await stage_deploy_file.ainvoke({
        "path": "Dockerfile", "contents": "FROM python:3.12-slim\n", "language": "dockerfile",
    })
    await stage_deploy_file.ainvoke({
        "path": "deploy/deploy-runbook.md", "contents": "# Deploy runbook\n", "language": "markdown",
    })

    result = await submit_release.ainvoke({"release_json": _RELEASE_PAYLOAD})

    assert "error" not in result.lower(), f"submit_release reported an error: {result}"
    assert "submitted" in result.lower()
    assert deploy_session.last_artifact is not None
    generated = deploy_session.last_artifact["generated_files"]
    assert {f["path"] for f in generated} == {"Dockerfile", "deploy/deploy-runbook.md"}
