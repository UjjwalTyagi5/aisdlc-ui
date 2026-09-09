"""The model chosen on an agent page must be the model that runs.

REPORTED: the Development page's picker was set to the Azure connection and every turn
still failed against the Anthropic one, which had exhausted its spend cap. The picker
was decorative. `agentModel` was written by the ModelSelector on every standalone agent
page and read by nothing — the useState and the selector's own `value`, and that was
all of it. Nothing posted a model, so the socket frame carried none, so every agent
resolved with `offering_id=None`.

`resolve_model_for_run` then falls to `offerings[0]`, and `_load_enabled` orders
`BY p.display_name`. Which connection runs was decided by alphabetical order — not by
the user, not by a default, and not visibly. "Anthropicnewkwy" sorts before "Azure key",
so the capped key won every turn while a working one sat in the list below it.

An OFFERING id rather than a model id throughout: two connections can expose the same
model, and only the offering says which key gets spent.

This is a plumbing defect, and plumbing is exactly what silently comes apart again —
a wrapper is added, or a state dict is rebuilt, and one field is not copied across. So
both ends are pinned: the Development node's behaviour, and every wrapper's source.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BACKEND = Path(__file__).resolve().parents[1]

#: Every standalone agent socket that builds graph state from a client frame. The path
#: is listed rather than derived because these are the files a person edits when adding
#: an agent, and the audit is only worth having if a new one has to be added here.
WRAPPERS = {
    "development": "agents_orchestrator/development_agent/development_agent_api.py",
    "requirements": "agents_orchestrator/requirements_agent/requirements_agent_api.py",
    "design": "agents_orchestrator/design_architecture_agent/design_architecture_agent_api.py",
    "plan": "agents_orchestrator/pm_agent/pm_agent_api.py",
    "code_review": "agents_orchestrator/code_review_agent/code_review_agent_api.py",
    "security": "agents_orchestrator/security_agent/security_agent_api.py",
    "deployment": "agents_orchestrator/deployment_agent/deployment_agent_api.py",
}


def _source(agent_id: str) -> str:
    return (BACKEND / WRAPPERS[agent_id]).read_text(encoding="utf-8-sig")


@pytest.mark.unit
@pytest.mark.parametrize("agent_id", sorted(WRAPPERS))
def test_the_wrapper_reads_the_offering_from_the_socket_frame(agent_id):
    src = _source(agent_id)
    assert 'get("offering_id")' in src, (
        f"the {agent_id} socket drops the page's model choice. Its graph node reads "
        f'state["offering_id"]; without this the node only ever sees None and the run '
        f"falls back to whichever provider connection sorts first by display name."
    )


@pytest.mark.unit
@pytest.mark.parametrize("agent_id", sorted(WRAPPERS))
def test_no_wrapper_hard_codes_the_offering_to_none(agent_id):
    """The Plan agent did. A frame carrying the choice would still have been discarded."""
    src = _source(agent_id)
    assert '"offering_id": None' not in src, (
        f"the {agent_id} socket overwrites the page's model choice with None"
    )


@pytest.mark.unit
@pytest.mark.parametrize("agent_id", sorted(WRAPPERS))
def test_the_offering_is_read_wherever_the_model_id_is(agent_id):
    """Guards the guard.

    Both checks above pass on a file that reads neither field — a wrapper that stopped
    building state at all, or one whose state dict was rewritten. Pin them together:
    the two are read side by side in every one of these files, so a source that reads
    `model_id` and not `offering_id` is the defect, and a source that reads neither is
    a sign this audit is now looking at the wrong place.
    """
    src = _source(agent_id)
    assert 'get("model_id")' in src, (
        f"{WRAPPERS[agent_id]} no longer reads model_id from the frame — this audit has "
        f"drifted off the code it was written against and is proving nothing"
    )


@pytest.mark.unit
async def test_the_development_node_forwards_the_offering_it_was_given():
    """The one node that never accepted an offering at all.

    Every other agent's node already passed `state["offering_id"]` to the resolver;
    this one resolved on `model_id` alone, so even once the socket carried the choice
    the Development agent would have gone on ignoring it.
    """
    from agents_orchestrator.development_agent.agents import dev_agent

    seen: dict = {}

    class _Stop(Exception):
        """Ends the turn at the resolver — this test is about the call, not the reply."""

    async def _fake(tenant_id, requested_model_id=None, **kwargs):
        seen["tenant_id"] = tenant_id
        seen["requested_model_id"] = requested_model_id
        seen.update(kwargs)
        raise _Stop()

    import shared.services.model_resolver as mr

    original = mr.resolve_model_for_run
    mr.resolve_model_for_run = _fake
    try:
        await dev_agent.agent_node({
            "messages": [],
            "tenant_id": "t-1",
            "model_id": "claude-opus-4-5",
            "offering_id": "off-azure-1",
        })
    finally:
        mr.resolve_model_for_run = original

    assert seen.get("offering_id") == "off-azure-1", (
        "the Development node resolved without the page's model choice, so the run "
        "falls back to whichever connection sorts first"
    )
    assert seen["tenant_id"] == "t-1"


@pytest.mark.unit
async def test_the_development_node_still_resolves_when_no_offering_is_chosen():
    """A page with no pick must keep falling back to the org default, not fail."""
    from agents_orchestrator.development_agent.agents import dev_agent

    seen: dict = {}

    class _Stop(Exception):
        pass

    async def _fake(tenant_id, requested_model_id=None, **kwargs):
        seen.update(kwargs)
        raise _Stop()

    import shared.services.model_resolver as mr

    original = mr.resolve_model_for_run
    mr.resolve_model_for_run = _fake
    try:
        result = await dev_agent.agent_node(
            {"messages": [], "tenant_id": "t-1", "model_id": None}
        )
    finally:
        mr.resolve_model_for_run = original

    assert seen.get("offering_id") is None
    # And the turn ends in an answer rather than an exception reaching the socket.
    assert result["messages"]
