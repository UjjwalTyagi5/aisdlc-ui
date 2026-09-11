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
    # Portfolio 1's nine plus Track 3's first two. Which of them a given project sees
    # is `registry_for_track`'s job, not this table's.
    assert len(AGENT_IDS) == 11
    assert "plan" in AGENT_IDS
    assert {"requirements_modernization", "discovery"} <= set(AGENT_IDS)
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


def test_validate_registry_resolves_graph_for_all_nine_and_prompt_for_stream_agents():
    """Six of nine agents used to run with NO system prompt (spec §11.2) — the old
    engine returned None and carried on, so the symptom was an agent that answered
    vaguely rather than an error. This makes that state unshippable.

    `testing` is the one deliberate exception: the only invoke-mode agent, a state
    machine that reads state["user_prompt"] and whose nodes carry their own prompts.
    It never receives an injected system message, so validate_registry must require
    a graph for it but must NOT demand a prompt.
    """
    from agents_orchestrator.orchestrator2.registry import validate_registry

    validate_registry()  # raises RegistryValidationError listing any gap

    stream_agents = {a for a, c in REGISTRY.items() if c.mode == "stream"}
    invoke_agents = {a for a, c in REGISTRY.items() if c.mode == "invoke"}
    assert invoke_agents == {"testing"}
    assert len(stream_agents) == 10  # eight of Portfolio 1 + Track 3's two

    # Every stream agent resolves to a real, non-blank prompt.
    for agent_id in stream_agents:
        prompt = REGISTRY[agent_id].load_prompt()
        assert isinstance(prompt, str) and prompt.strip(), agent_id


def test_validate_registry_reports_every_gap_not_just_the_first(monkeypatch):
    from agents_orchestrator.orchestrator2 import registry as reg

    def _boom():
        raise ImportError("nope")

    broken = dict(reg.REGISTRY)
    for aid in ("design", "security"):
        broken[aid] = reg.AgentCapability(
            agent_id=aid, load_graph=_boom,
            load_prompt=broken[aid].load_prompt, mode=broken[aid].mode,
        )
    monkeypatch.setattr(reg, "REGISTRY", broken)
    with pytest.raises(reg.RegistryValidationError) as exc:
        reg.validate_registry()
    msg = str(exc.value)
    assert "design" in msg and "security" in msg


def test_validate_registry_treats_blank_prompt_as_missing(monkeypatch):
    """An empty or whitespace-only prompt is the same silent failure wearing a
    different hat — validate_registry must catch it, not just an import error."""
    from agents_orchestrator.orchestrator2 import registry as reg

    broken = dict(reg.REGISTRY)
    design_cap = broken["design"]
    broken["design"] = reg.AgentCapability(
        agent_id="design",
        load_graph=design_cap.load_graph,
        load_prompt=lambda: "   ",
        mode=design_cap.mode,
    )
    monkeypatch.setattr(reg, "REGISTRY", broken)
    with pytest.raises(reg.RegistryValidationError) as exc:
        reg.validate_registry()
    assert "design" in str(exc.value)


def test_validate_registry_never_calls_load_prompt_for_invoke_mode_agents(monkeypatch):
    """Prompt is not applicable for invoke-mode agents — validate_registry must not
    even attempt to resolve one, so a not-yet-wired invoke prompt can never be
    mistaken for a gap."""
    from agents_orchestrator.orchestrator2 import registry as reg

    def _must_not_be_called():
        raise AssertionError("load_prompt must not be called for an invoke-mode agent")

    broken = dict(reg.REGISTRY)
    testing_cap = broken["testing"]
    broken["testing"] = reg.AgentCapability(
        agent_id="testing",
        load_graph=testing_cap.load_graph,
        load_prompt=_must_not_be_called,
        mode="invoke",
    )
    monkeypatch.setattr(reg, "REGISTRY", broken)
    reg.validate_registry()  # must not raise, and must not call _must_not_be_called


def test_testing_prompt_is_explicitly_not_applicable_not_a_silent_gap():
    """Testing's load_prompt must raise a distinct PromptNotApplicableError — not
    RegistryValidationError — so this reads as a deliberate, recorded decision
    (invoke-mode agents carry their own per-node prompts) rather than an oversight
    someone later "fixes" by inventing a system prompt."""
    from agents_orchestrator.orchestrator2.registry import PromptNotApplicableError

    assert REGISTRY["testing"].mode == "invoke"
    with pytest.raises(PromptNotApplicableError):
        REGISTRY["testing"].load_prompt()
