from pathlib import Path


PLANNING_PATH = (
    Path(__file__).resolve().parents[2]
    / "agents"
    / "requirements_agent"
    / "agents"
    / "planning.py"
)


def test_requirements_agent_exposes_board_neutral_tool_names():
    text = PLANNING_PATH.read_text(encoding="utf-8")

    expected = [
        "list_board_projects",
        "list_board_groups",
        "list_board_states",
        "list_board_items",
        "list_board_items_by_state",
        "fetch_board_item_detail",
        "fetch_board_hierarchy",
        "commit_board_ingestion_batch",
        "create_board_item",
        "write_stories_to_board",
        "write_acceptance_criteria_to_board",
        "write_back_normalized_to_board",
        "move_board_item_state",
        "add_board_comment",
    ]
    for name in expected:
        assert f"async def {name}" in text
        assert f"    {name}," in text


def test_requirements_prompt_does_not_reference_legacy_ado_tools():
    text = PLANNING_PATH.read_text(encoding="utf-8")
    legacy_terms = [
        "list_ado",
        "fetch_ado",
        "create_ado",
        "write_stories_to_ado",
        "write_back_normalized_to_ado",
        "write_acceptance_criteria_to_ado",
        "move_work_item_state",
        "add_ado_comment",
        "commit_ingestion_batch",
        "Azure Boards",
    ]
    for term in legacy_terms:
        assert term not in text


def test_requirements_prompt_forbids_fake_tool_and_user_messages():
    text = PLANNING_PATH.read_text(encoding="utf-8")

    assert "Never print bracketed fake tool narration" in text
    assert "Never write simulated user replies" in text
    assert "Jira project roles" in text
    assert "permissions, not delivery teams" in text
