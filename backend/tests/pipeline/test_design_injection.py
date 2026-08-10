from workflows.activities.design_activity import _build_design_prompt


def test_design_prompt_includes_requirements():
    p = _build_design_prompt(project_id="p1", trigger="manual", work_item_id=None,
                             requirements_payload={"stories": [{"id": "S1", "title": "Login"}]})
    assert "S1" in p and "Login" in p
