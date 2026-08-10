from agents_orchestrator.orchestrator import copilot_api, copilot_cards


def test_testing_type_uncommitted_decision():
    assert copilot_api._testing_type_uncommitted({}) is True
    assert copilot_api._testing_type_uncommitted({"classified_intent": "greeting"}) is True
    assert copilot_api._testing_type_uncommitted({"classified_intent": None}) is True
    assert copilot_api._testing_type_uncommitted({"selected_test_types": ["unit"]}) is False
    assert copilot_api._testing_type_uncommitted({"classified_intent": "full_test"}) is False
    assert copilot_api._testing_type_uncommitted({"classified_intent": "ui_test"}) is False


def test_testing_type_card_has_three_types():
    card = copilot_cards.testing_type_card("run-9")
    assert card.kind == "custom"
    ids = {o.id for o in card.options}
    assert ids == {"unit", "functional", "api"}
    assert card.min_select == 1 and card.max_select == 3


def test_testing_url_card_is_free_text():
    card = copilot_cards.testing_url_card("run-9", "functional")
    assert card.kind == "custom"
    assert card.options == []       # free-text only
    assert "url" in (card.prompt or card.title or "").lower()


def test_testing_card_kind_decision():
    assert copilot_api._testing_card_kind({}) == "type"
    assert copilot_api._testing_card_kind({"selected_test_types": ["unit"]}) is None
    assert copilot_api._testing_card_kind(
        {"selected_test_types": ["functional"], "awaiting_scope": True}) == "url"
    assert copilot_api._testing_card_kind(
        {"classified_intent": "full_test"}) is None
