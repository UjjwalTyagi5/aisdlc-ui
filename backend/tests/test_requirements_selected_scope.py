def test_prompt_has_selected_story_scope_rule():
    from agents_orchestrator.requirements_agent.agents.planning import INGESTION_SYS_MESSAGE
    msg = INGESTION_SYS_MESSAGE
    # Header marker the FE/BE contract depends on.
    assert "SELECTED-STORY SCOPE" in msg
    # The refs key the page nests under `requirements` must be named in the prompt.
    assert "selected_story_refs" in msg
    # Behaviour: scope to selected refs, else operate on ALL stories.
    lowered = msg.lower()
    assert "operate on all" in lowered
    assert "ado is the source of truth" in lowered
    # Create-on-ADO path uses the existing tool.
    assert "create_board_item" in msg
