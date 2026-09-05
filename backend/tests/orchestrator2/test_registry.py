import pytest

from agents_orchestrator.orchestrator2.registry import (
    AGENT_IDS,
    REGISTRY,
    UnknownAgentError,
    get_capability,
)
from shared.services.orchestrator.progression import STAGE_ORDER


def test_registry_covers_every_stage_in_order():
    """The registry IS the stage list — it cannot silently omit one.

    The old engine's dispatch table omitted `plan`, so the Project Manager agent
    could be routed to but never run, and the failure was swallowed by a
    logger.warning. Deriving the id list from STAGE_ORDER makes that impossible.
    """
    assert list(AGENT_IDS) == list(STAGE_ORDER)
    assert len(AGENT_IDS) == 9
    assert "plan" in AGENT_IDS
    assert set(REGISTRY) == set(AGENT_IDS)


@pytest.mark.parametrize("agent_id", list(STAGE_ORDER))
def test_every_agent_declares_a_mode(agent_id):
    assert REGISTRY[agent_id].mode in ("stream", "invoke")


def test_testing_is_the_only_state_machine_agent():
    """Mirrors copilot_api.STATE_MACHINE_STAGES = {"testing"}."""
    invoke_agents = {a for a, c in REGISTRY.items() if c.mode == "invoke"}
    assert invoke_agents == {"testing"}


def test_unknown_agent_raises_rather_than_returning_none():
    """The old engine returned None for an unmapped stage and carried on, which is
    how six agents ran with no prompt without anyone noticing. Absence must raise."""
    with pytest.raises(UnknownAgentError):
        get_capability("not_an_agent")
