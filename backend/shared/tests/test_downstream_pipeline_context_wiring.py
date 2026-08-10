from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_downstream_orchestrator_apis_accept_pipeline_context():
    files = [
        "agents_orchestrator/requirements_agent/requirements_agent_api.py",
        "agents_orchestrator/design_architecture_agent/design_architecture_agent_api.py",
        "agents_orchestrator/development_agent/development_agent_api.py",
        "agents_orchestrator/testing_agent/testing_agent_api.py",
        "agents_orchestrator/deployment_agent/deployment_agent_api.py",
        "agents_orchestrator/monitoring_feedback_agent/monitoring_feedback_agent_api.py",
    ]

    for relative_path in files:
        source = _read(relative_path)
        assert "pipeline_context" in source, relative_path
        assert "build_agent_input_text" in source, relative_path


def test_stateful_agents_store_parsed_pipeline_context():
    testing_source = _read("agents_orchestrator/testing_agent/testing_agent_api.py")
    deployment_source = _read("agents_orchestrator/deployment_agent/deployment_agent_api.py")

    assert 'previous_state["pipeline_context"] = parsed_pipeline_context' in testing_source
    assert "rest_pipeline_context = parse_pipeline_context(pipeline_context)" in deployment_source
    assert "ws_pipeline_context = parse_pipeline_context(pipeline_context)" in deployment_source
    assert '"pipeline_context": rest_pipeline_context' in deployment_source
    assert '"pipeline_context": ws_pipeline_context' in deployment_source
