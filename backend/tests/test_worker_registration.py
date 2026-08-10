"""Tests for Temporal worker activity registration via dispatch registry."""
from workflows.activity_dispatch import get_all_activity_fns
from workflows.activities.emit_escalation_activity import emit_escalation_activity
from workflows.activities.sync_status_activity import sync_run_status_activity


def test_dispatch_registry_covers_all_agent_activities():
    fns = get_all_activity_fns()
    names = {fn.__name__ for fn in fns}
    assert "run_requirements_activity" in names
    assert "run_design_activity" in names
    assert "run_development_activity" in names
    assert "run_testing_activity" in names


def test_infra_activities_importable():
    assert callable(emit_escalation_activity)
    assert callable(sync_run_status_activity)
