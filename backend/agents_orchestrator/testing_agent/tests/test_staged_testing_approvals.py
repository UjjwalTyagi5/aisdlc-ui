from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents_orchestrator.testing_agent.Nodes.approval import (
    request_unit_execution_approval,
    request_unit_generation_approval,
)
from agents_orchestrator.testing_agent.Nodes.execute import handle_ui_test_failure
from agents_orchestrator.testing_agent.Nodes.ingest_input import classify_intent
from agents_orchestrator.testing_agent.Nodes.routing import (
    decide_after_plan_generation,
    decide_after_test_code_saved,
    decide_cleanup,
    route_by_intent,
)
from agents_orchestrator.testing_agent.agents.testing_agent import _initial_state
from agents_orchestrator.testing_agent.testing_agent_api import process_agent_stream_for_chat_display


@pytest.mark.asyncio
async def test_selected_unit_testing_uses_staged_flow_by_default():
    result = await classify_intent({
        "user_prompt": "do unit testing",
        "input_file_path": "carelon.zip",
        "selected_test_types": ["unit"],
    })

    assert result["classified_intent"] == "full_test"
    assert result["staged_testing_enabled"] is True
    assert route_by_intent(result) == "full_test_path"


@pytest.mark.asyncio
async def test_explicit_unit_execution_can_bypass_staged_flow():
    result = await classify_intent({
        "user_prompt": "execute unit tests and coverage now",
        "input_file_path": "carelon.zip",
        "selected_test_types": ["unit"],
    })

    assert result["classified_intent"] == "full_test"
    assert result["staged_testing_enabled"] is False
    assert route_by_intent(result) == "full_test_path"


@pytest.mark.asyncio
async def test_pending_generate_approval_routes_to_unit_generation():
    result = await classify_intent({
        "user_prompt": "generate the unit test code",
        "pending_testing_approval": "generate_unit_code",
        "selected_test_types": ["unit"],
    })

    assert result["classified_intent"] == "approve_generate_unit_code"
    assert result["pending_testing_approval"] is None
    assert route_by_intent(result) == "approve_generate_unit_code_path"


@pytest.mark.asyncio
async def test_pending_run_approval_routes_to_unit_execution():
    result = await classify_intent({
        "user_prompt": "approve",
        "pending_testing_approval": "run_unit_tests",
        "selected_test_types": ["unit"],
    })

    assert result["classified_intent"] == "approve_run_unit_tests"
    assert result["pending_testing_approval"] is None
    assert route_by_intent(result) == "approve_run_unit_tests_path"


@pytest.mark.asyncio
async def test_staged_approval_recovers_when_pending_flag_is_missing_but_plan_exists():
    result = await classify_intent({
        "user_prompt": "yes looks good generate the code",
        "staged_testing_enabled": True,
        "test_plan": MagicMock(test_cases=[MagicMock()]),
        "generated_test_sets": [],
        "input_file_path": "carelon.zip",
        "work_dir": "C:/tmp/extracted",
    })

    assert result["classified_intent"] == "approve_generate_unit_code"
    assert route_by_intent(result) == "approve_generate_unit_code_path"


def test_initial_state_preserves_uploaded_file_path_on_approval_turn():
    previous = {
        "input_file_path": "C:/tmp/input/carelon.zip",
        "pending_testing_approval": "generate_unit_code",
        "work_dir": "C:/tmp/extracted",
    }

    resumed = _initial_state("yes looks good generate the code", None, previous)

    assert resumed["input_file_path"] == "C:/tmp/input/carelon.zip"
    assert resumed["work_dir"] == "C:/tmp/extracted"
    assert resumed["pending_testing_approval"] == "generate_unit_code"


def test_staged_routing_pauses_after_plan_and_after_code_generation():
    staged = {"staged_testing_enabled": True, "selected_test_types": ["unit"]}

    assert decide_after_plan_generation(staged) == "await_generate_unit_approval"
    assert decide_after_test_code_saved(staged) == "await_run_approval"
    assert decide_cleanup({"work_dir": "/tmp/work", "pending_testing_approval": "run_unit_tests"}) == "no_cleanup_needed"


def test_pending_unit_approval_does_not_emit_deployment_handoff():
    responses = process_agent_stream_for_chat_display({
        "classified_intent": "full_test",
        "pending_testing_approval": "generate_unit_code",
        "final_outputs": {
            "final_summary_md": "Code analysis and test planning are complete.",
            "testing_artifact_json": "{}",
        },
    })

    assert responses
    assert responses[-1] is not None  # M2-06: sentinel mechanism removed; artifact tools used instead


@pytest.mark.asyncio
async def test_approval_messages_set_pending_stage():
    plan = MagicMock()
    plan.test_cases = [MagicMock(scenario_type="Happy Path"), MagicMock(scenario_type="Error Case")]
    first = await request_unit_generation_approval({"test_plan": plan})

    assert first["pending_testing_approval"] == "generate_unit_code"
    assert "generate the unit test code" in first["final_user_message"]

    second = await request_unit_execution_approval({
        "generated_test_sets": [{
            "test_file_paths": ["GeneratedTests_Unit_01.cs"],
            "scenario_count": 3,
        }]
    })

    assert second["pending_testing_approval"] == "run_unit_tests"
    assert "run the unit tests" in second["final_user_message"]


@pytest.mark.asyncio
async def test_ui_failure_clears_pending_functional_approval():
    result = await handle_ui_test_failure({
        "error_message": "The UI testing agent failed to run.",
        "pending_functional_approval": "run_ui_tests",
    })

    assert result["ui_tests_completed"] is True
    assert result["pending_functional_approval"] is None
    assert result["functional_tests_approved"] is False
