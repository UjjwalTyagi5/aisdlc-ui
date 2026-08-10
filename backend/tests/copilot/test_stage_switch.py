from agents_orchestrator.orchestrator.stage_switch import (
    STAGE_ALIASES,
    STAGE_IDS,
    detect_switch,
    mentions_stage,
    rule_match,
)
from shared.services.orchestrator.progression import STAGE_ORDER


def test_stage_ids_match_real_pipeline_stages():
    assert STAGE_IDS == list(STAGE_ORDER)
    assert set(STAGE_ALIASES.keys()) == set(STAGE_IDS)


def test_rule_match_launch_documentation():
    assert rule_match("launch documentation and create a summary", "development") == "documentation"


def test_rule_match_switch_to_security():
    assert rule_match("switch to security", "development") == "security"


def test_rule_match_now_do_code_review():
    assert rule_match("now do the code review", "development") == "code_review"


def test_rule_match_bare_verb_document_is_not_a_switch():
    assert rule_match("document this function", "development") is None


def test_rule_match_target_equal_current_is_none():
    assert rule_match("run documentation", "documentation") is None


def test_rule_match_how_do_requirements_map_to_design_is_not_a_switch():
    assert rule_match("how do requirements map to design", "development") is None


def test_rule_match_redo_security_scan_is_not_a_switch():
    assert rule_match("redo the security scan", "development") is None


def test_rule_match_next_testing_milestone_is_not_a_switch():
    assert rule_match("what is the next testing milestone", "development") is None


def test_rule_match_do_the_docstrings_is_not_a_switch():
    assert rule_match("do the docstrings", "development") is None


def test_rule_match_run_documentation():
    assert rule_match("run documentation", "development") == "documentation"


def test_rule_match_switch_to_security_word_boundary():
    assert rule_match("switch to security", "development") == "security"


def test_rule_match_now_do_code_review_word_boundary():
    assert rule_match("now do the code review", "development") == "code_review"


def test_rule_match_lets_do_testing():
    assert rule_match("let's do testing", "development") == "testing"


def test_rule_match_go_to_design():
    assert rule_match("go to design", "development") == "design"


def test_rule_match_hyphenated_code_review_alias():
    assert rule_match("switch to code-review", "development") == "code_review"


def test_mentions_stage_finds_docs_alias():
    assert mentions_stage("get the docs sorted next") == "documentation"


def test_mentions_stage_no_alias_present():
    assert mentions_stage("fix the null check") is None


async def test_detect_switch_rule_path_no_llm():
    assert await detect_switch("run documentation", "development") == "documentation"


async def test_detect_switch_no_stage_mentioned_llm_not_called():
    calls = []

    async def mock_llm(text, current_stage, stage_ids):
        calls.append((text, current_stage, stage_ids))
        return {"switch": False, "target": None}

    result = await detect_switch("fix the null check", "development", llm_classify=mock_llm)

    assert result is None
    assert calls == []


async def test_detect_switch_ambiguous_mention_calls_llm_and_switches():
    calls = []

    async def mock_llm(text, current_stage, stage_ids):
        calls.append((text, current_stage, stage_ids))
        return {"switch": True, "target": "documentation"}

    result = await detect_switch(
        "can you get the docs done next", "development", llm_classify=mock_llm
    )

    assert result == "documentation"
    assert len(calls) == 1


async def test_detect_switch_llm_says_no_switch():
    async def mock_llm(text, current_stage, stage_ids):
        return {"switch": False, "target": None}

    result = await detect_switch(
        "can you get the docs done next", "development", llm_classify=mock_llm
    )

    assert result is None
