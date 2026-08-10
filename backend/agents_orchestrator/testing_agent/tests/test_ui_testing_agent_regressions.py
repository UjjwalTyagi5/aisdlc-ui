from __future__ import annotations

import pytest

from agents_orchestrator.testing_agent.tools.ui_testing_agent import (
    _is_form_flow_goal,
    _goal_keywords,
    _normalize_heuristic_result,
    _normalize_target_url,
    ask_gemini_for_testcases,
)
from agents_orchestrator.testing_agent.Nodes.ingest_input import _extract_url


def test_form_flow_goal_detection_is_generic():
    assert _is_form_flow_goal("create a new case")
    assert _is_form_flow_goal("add an employee")
    assert _is_form_flow_goal("submit an insurance request")
    assert not _is_form_flow_goal("verify dashboard chart loads")


def test_goal_keywords_uses_regex_without_runtime_name_error():
    assert _goal_keywords("do functional testing for create employee flow") == ["employee"]


def test_heuristic_result_normalization_accepts_two_and_three_tuple_shapes():
    assert _normalize_heuristic_result((True, "ok")) == (True, "ok", {"method": "heuristic"})
    assert _normalize_heuristic_result((False, "bad", {"method": "generic_form_flow"})) == (
        False,
        "bad",
        {"method": "generic_form_flow"},
    )


def test_ui_target_url_trims_sentence_punctuation():
    assert _extract_url("Use http://localhost:5077.") == "http://localhost:5077"
    assert _normalize_target_url(" http://localhost:5077. ") == "http://localhost:5077"


@pytest.mark.asyncio
async def test_form_flow_prompt_uses_generic_action_without_carelon_fields():
    cases = await ask_gemini_for_testcases(
        "<body><a>Add employee</a><form></form></body>",
        "http://localhost",
        user_goal="add employee end to end",
    )

    assert cases[0]["steps"][0]["action"] == "complete_requested_form_flow"
    serialized = str(cases)
    assert "MemberId" not in serialized
    assert "ProviderId" not in serialized
    assert "Exams[0].CptCode" not in serialized
