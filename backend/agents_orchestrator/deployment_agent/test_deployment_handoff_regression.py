from __future__ import annotations

import pytest

from agents_orchestrator.deployment_agent.deployment_agent_api import (
    _deployment_completed_for_handoff,
    _pipeline_summary,
)
from agents_orchestrator.deployment_agent.agents.pipeline_app import verify_testing_gate
from agents_orchestrator.deployment_agent.agents.pipeline_app import (
    _generate_dotnet_managed_deployment_files,
    _find_dotnet_project,
)


def test_pipeline_trigger_failure_after_push_is_warning_not_failed_deployment():
    state = {
        "orchestrator_driven": True,
        "push_status": "pushed",
        "pipeline_trigger_status": "failed",
        "pipeline_run_id": "",
        "errors": ["Pipeline trigger failed: 403 forbidden"],
    }

    assert _deployment_completed_for_handoff(state) is True
    summary = _pipeline_summary(state)
    assert "Deployment failed" not in summary
    assert "Deployment assets prepared" in summary
    assert "trigger attempted" in summary


def test_pipeline_run_id_is_completed_deployment():
    state = {
        "orchestrator_driven": True,
        "push_status": "pushed",
        "pipeline_trigger_status": "triggered",
        "pipeline_run_id": "123",
        "pipeline_run_url": "https://dev.azure.com/org/project/_build/results?buildId=123",
    }

    assert _deployment_completed_for_handoff(state) is True


def test_dotnet_managed_deployment_files_match_carelon_layout(tmp_path):
    app_dir = tmp_path / ".net application" / "RadAuthPortal"
    app_dir.mkdir(parents=True)
    (app_dir / "RadAuthPortal.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>',
        encoding="utf-8",
    )
    state = {"created_files": [], "existing_skipped": []}

    deployment_root, project_rel = _find_dotnet_project(str(tmp_path))
    assert deployment_root.endswith(".net application")
    assert project_rel == "RadAuthPortal/RadAuthPortal.csproj"

    assert _generate_dotnet_managed_deployment_files(state, str(tmp_path), "feature/demo") is True

    dockerfile = tmp_path / ".net application" / "Dockerfile"
    dockerignore = tmp_path / ".net application" / ".dockerignore"
    pipeline = tmp_path / "azure-pipelines.yml"
    manifest = tmp_path / "Manifest" / "carelon-deploy.yaml"
    namespace = tmp_path / "Manifest" / "namespace.yaml"

    assert dockerfile.exists()
    assert dockerignore.exists()
    assert pipeline.exists()
    assert manifest.exists()
    assert namespace.exists()

    docker_text = dockerfile.read_text(encoding="utf-8")
    pipeline_text = pipeline.read_text(encoding="utf-8")

    assert "COPY RadAuthPortal/RadAuthPortal.csproj RadAuthPortal/" in docker_text
    assert 'ENTRYPOINT ["dotnet", "RadAuthPortal.dll"]' in docker_text
    assert 'Dockerfile: ".net application/Dockerfile"' in pipeline_text
    assert 'buildContext: ".net application"' in pipeline_text
    assert "- feature/demo" in pipeline_text
    assert ".net application/Dockerfile" in state["deployment_file_paths"]
    assert ".net application/.dockerignore" in state["deployment_file_paths"]
    assert "Manifest/carelon-deploy.yaml" in state["deployment_file_paths"]


@pytest.mark.asyncio
async def test_testing_failures_warn_but_do_not_block_deployment():
    state = {
        "orchestrator_driven": True,
        "testing_artifacts": {
            "status": "executed",
            "test_execution": {"total": 10, "passed": 7, "failed": 3, "errors": 0},
        },
    }

    result = await verify_testing_gate.ainvoke({"state": state})

    assert result["testing_gate"] == "warning_failures"
    assert result.get("status") != "blocked"
    summary = _pipeline_summary(result)
    assert "Deployment blocked" not in summary
    assert "Testing warning" in summary
