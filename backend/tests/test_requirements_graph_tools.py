def test_nlp_tools_bound_to_requirements_graph():
    from agents_orchestrator.requirements_agent.agents import planning
    tool_names = {t.name for t in planning.tools}
    assert "run_nlp_quality_check" in tool_names
    assert "run_requirement_smell_check" in tool_names
    assert "run_spectral_lint" in tool_names


def test_prompt_mentions_pre_validation():
    from agents_orchestrator.requirements_agent.agents.planning import INGESTION_SYS_MESSAGE
    assert "pre-validation" in INGESTION_SYS_MESSAGE.lower()
