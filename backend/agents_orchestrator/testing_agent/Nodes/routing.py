"""Graph-routing predicates + sync wrapper for async nodes.

Phase 4a — extracted from `super_agent.py` lines 1003-1087.
"""
from __future__ import annotations

import asyncio

from agents_orchestrator.testing_agent.config.session_state import SuperAgentState
from agents_orchestrator.testing_agent.config.shared import blog, logger


def make_sync_wrapper(async_func):
    """Wrap an async node so LangGraph (sync invoke) can drive it.

    Mirrors `super_agent.py:1003` exactly. Uses `nest_asyncio` when an event
    loop is already running (the FastAPI request path), and falls through to a
    plain `asyncio.run` otherwise (CLI / tests).
    """
    def sync_wrapper(state):
        try:
            asyncio.get_running_loop()
            import nest_asyncio
            nest_asyncio.apply()
            return asyncio.run(async_func(state))
        except RuntimeError:
            return asyncio.run(async_func(state))
    return sync_wrapper


def route_by_intent(state: SuperAgentState):
    intent = state.get("classified_intent")
    logger.info(f"Routing based on intent: {intent}")
    blog(f"Routing to: {intent}")

    route_map = {
        "approve_generate_unit_code": "approve_generate_unit_code_path",
        "approve_run_unit_tests": "approve_run_unit_tests_path",
        # Functional-test approval resumes
        "approve_run_functional_tests": "approve_run_functional_tests_path",
        "approve_run_ui_tests": "approve_run_ui_tests_path",
        "full_test": "full_test_path",
        "single_file_test": "single_file_test_path",
        "generate_plan_only": "generate_plan_only_path",
        # ui_test now routes to plan+approval first; execution resumes via approve_run_ui_tests
        "ui_test": "ui_test_path",
        "api_ui_only": "api_ui_only_path",   # API/UI tests without code tests
        "follow_up_query": "follow_up_path",
        "greeting": "greeting_path",
        "trigger_pipeline_and_collect": "trigger_pipeline_path",
    }
    return route_map.get(intent, "greeting_path")


def _unit_staging_enabled(state: SuperAgentState) -> bool:
    selected = set(state.get("selected_test_types") or [])
    return bool(state.get("staged_testing_enabled") and (not selected or "unit" in selected))


def _has_functional_tests(state: SuperAgentState) -> bool:
    """True when dispatch_test_types produced at least one functional_api or
    functional_ui test set and the user hasn't approved them yet."""
    generated = state.get("generated_test_sets") or []
    return any(
        isinstance(s, dict) and s.get("skill_name") in ("functional_api", "functional_ui")
        for s in generated
    )


def decide_after_plan_generation(state: SuperAgentState):
    if _unit_staging_enabled(state):
        return "await_generate_unit_approval"
    return "dispatch"


def decide_after_test_code_saved(state: SuperAgentState):
    # Unit approval gate (explicit staged-testing mode)
    if _unit_staging_enabled(state) and state.get("pending_testing_approval") != "run_unit_tests":
        return "await_run_approval"
    # Functional approval gate — active whenever functional tests were generated
    # and the user has not yet approved them (covers api_ui_only, functional, and
    # full_test paths where unit staging is NOT enabled).
    if (
        not _unit_staging_enabled(state)
        and _has_functional_tests(state)
        and not state.get("functional_tests_approved")
    ):
        return "await_functional_approval"
    return "run"


def decide_ui_test_outcome(state: SuperAgentState):
    return "failure" if state.get("error_message") else "success"


def decide_code_analysis_outcome(state: SuperAgentState):
    return (
        "analysis_failed"
        if not (state.get("code_analysis") and state["code_analysis"].functions)
        else "analysis_succeeded"
    )


def decide_cleanup(state: SuperAgentState):
    # Keep workspace alive while any approval gate is still open
    if state.get("pending_testing_approval") or state.get("pending_functional_approval"):
        return "no_cleanup_needed"
    return "needs_cleanup" if state.get('work_dir') else "no_cleanup_needed"


def decide_after_aggregate(state: SuperAgentState):
    """After code tests aggregate, run UI tests if ui_scope+target_url are set
    and UI hasn't run yet. Otherwise proceed to QA report generation."""
    if (
        state.get("ui_scope")
        and state.get("target_url")
        and not state.get("ui_tests_completed")
    ):
        return "run_ui_tests"
    return "generate_report"
