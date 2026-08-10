"""Tests for the activity dispatch registry (Task 3).

Verifies that get_activity_fn() resolves known agent_ids to the correct
Temporal activity callable, raises KeyError for unknown agents, and that
get_all_activity_fns() returns the full list for Worker registration.
"""
from __future__ import annotations

import pytest

from workflows.activity_dispatch import get_activity_fn, get_all_activity_fns


def test_get_activity_fn_requirements():
    fn = get_activity_fn("requirements")
    assert callable(fn)
    assert fn.__name__ == "run_requirements_activity"


def test_get_activity_fn_all_known_agents():
    for agent_id in ("requirements", "design", "development", "testing"):
        fn = get_activity_fn(agent_id)
        assert callable(fn), f"No activity for {agent_id}"


def test_get_activity_fn_unknown_raises():
    with pytest.raises(KeyError, match="no_such_agent"):
        get_activity_fn("no_such_agent")


def test_get_all_activity_fns_returns_list():
    fns = get_all_activity_fns()
    assert isinstance(fns, list)
    assert len(fns) >= 4


def test_get_activity_fn_returns_correct_function_per_agent():
    """Each agent_id maps to the expected function name."""
    expected = {
        "requirements": "run_requirements_activity",
        "design": "run_design_activity",
        "development": "run_development_activity",
        "testing": "run_testing_activity",
    }
    for agent_id, expected_name in expected.items():
        fn = get_activity_fn(agent_id)
        assert fn.__name__ == expected_name, (
            f"{agent_id} -> {fn.__name__}, expected {expected_name}"
        )


def test_get_all_activity_fns_are_all_callable():
    for fn in get_all_activity_fns():
        assert callable(fn)
