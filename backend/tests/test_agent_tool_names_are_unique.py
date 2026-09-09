"""No agent may bind two tools with the same name.

The provider rejects the whole request when it does — a `BadRequestError` that reaches
the user as "the model provider rejected the request as malformed", which points at
model configuration and is nowhere near the truth. Nothing else about the run looks
wrong: the agent is selected, the first call succeeds, and the failure only appears once
tools are in play.

REPORTED: the Design agent failed on every turn while Requirements, on the same model in
the same run, worked. Design bound 24 tools under 23 names — its own
`read_document(file_path)`, which extracts text from an uploaded file, and the platform's
`read_document(document_id)` from `make_document_tools`, which reads an approved project
document. Two genuinely different tools that happened to agree on a name.

The collision is not only an API error. `fe566143` left the Documentation agent out of
the SharePoint fan-out for exactly this reason — "a second publish tool with a
near-identical name would be a coin-flip for the model" — and the same judgement applies
here: even where the API allowed it, two `read_document`s taking different arguments is
a tool the model cannot choose between.

This test exists because the fan-out that caused it is mechanical and will happen again:
a shared factory gains a tool, it is bound to every agent, and one agent already had that
name. Catching the ninth instance is cheaper here than in a demo.
"""
from __future__ import annotations

import collections
import importlib

import pytest

#: Where each registry agent's tool list lives. Kept beside the registry rather than
#: derived from it because the graphs bind their tools at import time and the registry
#: hands back a compiled graph, which no longer says which tools went into it.
AGENT_MODULES = {
    "requirements": "agents_orchestrator.requirements_agent.agents.planning",
    "design": "agents_orchestrator.design_architecture_agent.agents.architecture",
    "plan": "agents_orchestrator.pm_agent.agents.schedule",
    "development": "agents_orchestrator.development_agent.agents.dev_agent",
    "code_review": "agents_orchestrator.code_review_agent.agents.reviewer",
    "security": "agents_orchestrator.security_agent.agents.scanner",
    "deployment": "agents_orchestrator.deployment_agent.agents.deployer",
    "documentation": "agents_orchestrator.documentation_agent.agents.compiler",
}


def _tool_list(module) -> list:
    """The agent's bound tools, however that module happens to name the list."""
    for attr in ("TOOLS", "tools", "ALL_TOOLS"):
        value = getattr(module, attr, None)
        if isinstance(value, list) and value:
            return value
    candidates = [
        v for v in vars(module).values()
        if isinstance(v, list) and v and hasattr(v[0], "name")
    ]
    return max(candidates, key=len) if candidates else []


def _names(agent_id: str) -> list[str]:
    module = importlib.import_module(AGENT_MODULES[agent_id])
    return [str(getattr(t, "name", "")) for t in _tool_list(module)]


@pytest.mark.parametrize("agent_id", sorted(AGENT_MODULES))
def test_no_agent_binds_the_same_tool_name_twice(agent_id):
    """One per agent, so a failure names the agent rather than 'something'."""
    names = _names(agent_id)
    duplicates = sorted(n for n, count in collections.Counter(names).items() if count > 1)

    assert not duplicates, (
        f"the {agent_id} agent binds {duplicates} more than once. The provider refuses "
        f"the whole request, and the user is told the model is misconfigured. Rename "
        f"one of them — do not drop either, they are different tools."
    )


@pytest.mark.parametrize("agent_id", sorted(AGENT_MODULES))
def test_every_agent_binds_at_least_one_tool(agent_id):
    """Guards the guard: if `_tool_list` stops finding the list, every duplicate check
    above passes over an empty list and this file silently proves nothing."""
    assert _names(agent_id), f"no tools found for {agent_id} — the check above is vacuous"


def test_the_two_document_readers_are_still_distinguishable():
    """The specific collision, pinned by behaviour rather than by name.

    Design reads BOTH an uploaded file and an approved project document. Whatever they
    end up being called, a reader has to be able to tell which is which — so the two
    must not share a name, and both must still be there.
    """
    names = _names("design")

    from shared.tools.project_documents import make_document_tools

    platform = {str(t.name) for t in make_document_tools("design")}
    assert platform, "the platform document tools are no longer bound anywhere"

    # The platform's readers are present...
    assert platform <= set(names), (
        f"Design lost the platform document tools: {sorted(platform - set(names))}"
    )
    # ...and Design's own upload reader is too, under a name of its own.
    own = [n for n in names if "upload" in n or n == "read_document"]
    assert own, "Design can no longer read an uploaded file at all"
    assert len(names) == len(set(names)), "the two readers collided again"
