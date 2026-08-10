from agents_orchestrator.orchestrator.copilot_cards import project_choice_card, story_choice_card


def test_project_card_from_list_board_projects_json():
    raw = '[{"id":"p1","name":"Payments"},{"id":"p2","name":"Identity"}]'
    card = project_choice_card("run1", raw)
    assert card.kind == "ado_project" and card.min_select == 1 and card.max_select == 1
    assert [o.label for o in card.options] == ["Payments", "Identity"]


def test_story_card_is_multiselect():
    raw = '[{"id":"1","title":"Login","state":"Active"},{"id":"2","title":"Logout","state":"Active"}]'
    card = story_choice_card("run1", raw)
    assert card.kind == "story_multiselect" and card.max_select >= 2
    assert card.options[0].sublabel == "Active"
