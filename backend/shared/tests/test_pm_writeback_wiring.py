from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_development_prompt_requires_jira_writeback():
    source = _read("agents_orchestrator/development_agent/prompts/dev_agent_prompt.py")

    assert "PM work-item write-back rules" in source
    assert "do not skip Jira write-back" in source
    assert "Jira status and PR comments are updated" in source
    assert "no extra API call needed" not in source


def test_development_tools_are_provider_neutral():
    source = _read("agents_orchestrator/development_agent/tools/git_tools.py")

    assert "Move PM work items to a new state through the active provider" in source
    assert "Supports Azure Boards and Jira" in source
    assert "Post a comment with the PR URL on each linked PM work item" in source


def test_deployment_state_carries_structured_pm_artifacts():
    source = _read("agents_orchestrator/deployment_agent/deployment_agent_api.py")

    assert '"requirements_payload": rest_requirements_payload' in source
    assert '"development_artifacts": rest_development_artifacts' in source
    assert '"board_provider": rest_pipeline_context.get("board_provider")' in source
    assert '"requirements_payload": ws_requirements_payload' in source


def test_deployment_uses_state_board_provider_for_status_updates():
    source = _read("agents_orchestrator/deployment_agent/agents/pipeline_app.py")

    assert 'state.get("board_provider")' in source
    assert 'req_payload.get("provider_kind")' in source
    assert "provider = await get_pm_provider(provider_kind)" in source
    assert 'provider.move_item_state(project, wid, "Done")' in source
