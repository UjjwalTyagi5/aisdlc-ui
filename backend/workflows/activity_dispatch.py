"""Activity dispatch registry -- maps agent_id -> Temporal activity callable.

The data-driven SDLCWorkflow uses get_activity_fn(agent_id) instead of
importing each activity at the workflow module level. This keeps the
workflow body agent-agnostic: adding a new agent means adding one entry
here + the activity module. Zero workflow code changes.
"""
from __future__ import annotations

from typing import Callable, List

from workflows.activities.requirements_activity import run_requirements_activity
from workflows.activities.design_activity import run_design_activity
from workflows.activities.development_activity import run_development_activity
from workflows.activities.testing_activity import run_testing_activity
from workflows.activities.code_review_activity import run_code_review_activity
from workflows.activities.security_activity import run_security_activity
from workflows.activities.deployment_activity import run_deployment_activity
from workflows.activities.documentation_activity import run_documentation_activity


_ACTIVITY_REGISTRY: dict[str, Callable] = {
    "requirements": run_requirements_activity,
    "design": run_design_activity,
    "development": run_development_activity,
    "testing": run_testing_activity,
    "code_review": run_code_review_activity,
    "security": run_security_activity,
    "deployment": run_deployment_activity,
    "documentation": run_documentation_activity,
}


def get_activity_fn(agent_id: str) -> Callable:
    """Return the Temporal activity function for agent_id. Raises KeyError if unknown."""
    if agent_id not in _ACTIVITY_REGISTRY:
        raise KeyError(
            f"No activity registered for agent '{agent_id}'. "
            f"Known agents: {list(_ACTIVITY_REGISTRY)}"
        )
    return _ACTIVITY_REGISTRY[agent_id]


def get_all_activity_fns() -> List[Callable]:
    """Return all registered activity functions (for Worker registration)."""
    return list(_ACTIVITY_REGISTRY.values())
