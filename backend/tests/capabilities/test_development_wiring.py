"""Contract tests for Development agent capability-spine wiring + mode resolution.

Pins two contracts:
1. Preflight contract — a run with a missing required capability must be blocked
   with a gap message naming the agent and the missing cap; a run with all required
   capabilities must proceed (gap == []).
2. Mode-resolution contract — _build_dev_prompt returns the right intent prompt
   for each of the four branches: design-driven, change-request-driven,
   work-item-only, and no-intent (user clarification).
"""
import pytest
from shared.capabilities.resolution import ResolvedToolset
from shared.capabilities.providers import CapabilityProvider
from shared.capabilities.preflight import preflight_agent, gap_message
from shared.models.workflow_models import ChangeRequest
from workflows.activities.development_activity import _build_dev_prompt


# ── Preflight / spine-wiring tests ────────────────────────────────────────────

def test_development_run_blocks_when_required_capability_missing():
    """A toolset missing 'repo.write' (a required Development cap) must surface a gap."""
    resolved = ResolvedToolset()
    resolved.active = {"artifact.write": CapabilityProvider("native", "artifact.write", "x")}
    gap = preflight_agent("development", resolved)
    assert "repo.write" in gap
    msg = gap_message("development", gap)
    assert "Development Agent" in msg and "repo.write" in msg


def test_development_run_proceeds_when_all_required_present():
    """A toolset covering all required Development caps must produce an empty gap."""
    resolved = ResolvedToolset()
    from config.agent_registry import AGENT_REGISTRY
    resolved.active = {
        c: CapabilityProvider("native", c, c)
        for c in AGENT_REGISTRY["development"].required_capabilities
    }
    assert preflight_agent("development", resolved) == []


# ── Mode-resolution unit tests ─────────────────────────────────────────────────

def test_mode_design_driven_mentions_design():
    """When has_design=True the prompt should direct the agent to read the design."""
    prompt = _build_dev_prompt(
        has_design=True,
        change_request=None,
        work_item_id=None,
        project_id="proj-123",
    )
    assert "design" in prompt.lower()
    assert "proj-123" in prompt


def test_mode_design_driven_includes_work_item_when_present():
    prompt = _build_dev_prompt(
        has_design=True,
        change_request=None,
        work_item_id="WI-99",
        project_id="proj-1",
    )
    assert "WI-99" in prompt


def test_mode_change_request_includes_text_and_existing():
    """Change-request mode must include the CR text and signal 'existing' codebase."""
    cr = ChangeRequest(text="Fix NPE in checkout", kind="bugfix")
    prompt = _build_dev_prompt(
        has_design=False,
        change_request=cr,
        work_item_id=None,
        project_id="proj-2",
    )
    assert "Fix NPE in checkout" in prompt
    assert "existing" in prompt.lower()


def test_mode_change_request_includes_kind_and_paths():
    cr = ChangeRequest(
        text="Add POST /orders",
        kind="feature",
        target_paths=["src/orders.py", "tests/test_orders.py"],
    )
    prompt = _build_dev_prompt(
        has_design=False,
        change_request=cr,
        work_item_id=None,
        project_id="proj-3",
    )
    assert "feature" in prompt
    assert "src/orders.py" in prompt


def test_mode_work_item_only_mentions_work_item():
    """With no design or CR but a work_item_id, the prompt must reference that item."""
    prompt = _build_dev_prompt(
        has_design=False,
        change_request=None,
        work_item_id="ADO-42",
        project_id="proj-4",
    )
    assert "ADO-42" in prompt


def test_mode_no_intent_asks_user():
    """With no design, no CR, and no work item, the prompt must ask the user."""
    prompt = _build_dev_prompt(
        has_design=False,
        change_request=None,
        work_item_id=None,
        project_id="proj-5",
    )
    # The prompt should tell the agent to ask the user, not start coding.
    assert "ask" in prompt.lower() or "no design" in prompt.lower()


def test_design_driven_takes_precedence_over_change_request():
    """design-driven (Mode 1) wins even when a change_request is also provided."""
    cr = ChangeRequest(text="do something else")
    prompt = _build_dev_prompt(
        has_design=True,
        change_request=cr,
        work_item_id=None,
        project_id="proj-6",
    )
    # Should follow the design path, not embed the CR text.
    assert "design" in prompt.lower()
    assert "do something else" not in prompt
