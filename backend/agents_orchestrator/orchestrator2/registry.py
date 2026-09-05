"""Capability registry — the single table that says which agents exist and how to
reach them.

The old engine's dispatch table silently omitted `plan` (the Project Manager agent):
routing could select it, nothing ran, and a `logger.warning` swallowed the gap. This
registry makes that failure mode structurally impossible:

  - `AGENT_IDS` is DERIVED from `STAGE_ORDER`, not hand-typed, so it cannot drift from
    the canonical nine.
  - `REGISTRY` is asserted at import time to cover exactly `STAGE_ORDER` — a hand-edited
    table that adds, drops, or misspells an id fails at import, not at request time.
  - `get_capability` RAISES for an unmapped id instead of returning `None`. The old
    engine's `None`-and-continue is exactly how six agents ran with no system prompt
    without anyone noticing.

`plan` is the Project Manager agent in user-facing text; the id stays `plan` (matches
`STAGE_ORDER` / `AGENT_REGISTRY` verbatim).

Every `load_graph` / `load_prompt` callable below imports LAZILY, inside the callable.
Importing this module must not drag in all nine agents' heavy dependencies (LLM
clients, LangGraph builders, tool wiring, ...) — same rationale as
`agents_orchestrator/orchestrator/copilot_api.py::_graph_for` /
`_system_prompt_for` / `_base_prompt_for`, which this module mirrors for the new
engine.

This package never imports a `*_agent_api.py` module. Those wrappers carry per-role
permission and session machinery the Orchestrator deliberately does not inherit —
only each agent's `agents/` graph module and its `prompts/` package are fair game.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from shared.services.orchestrator.progression import STAGE_ORDER

# The canonical nine, in the canonical order. Never hand-typed elsewhere in this
# package — everything else is keyed off this tuple or off STAGE_ORDER directly.
AGENT_IDS: tuple[str, ...] = tuple(STAGE_ORDER)


class UnknownAgentError(Exception):
    """Raised by `get_capability` for an id that is not one of the nine agents.

    The old engine returned `None` for an unmapped stage and carried on. Absence
    must raise, not be swallowed by a caller that forgets to check.
    """


class RegistryValidationError(Exception):
    """Raised by a `load_prompt` callable whose prompt symbol is not yet known.

    Five of the nine agents' system-prompt symbols have not been located yet
    (that is the next task's work). Guessing a plausible-but-wrong symbol here
    would be silently worse than this explicit failure — do not add one without
    having actually found and verified it.
    """


@dataclass(frozen=True)
class AgentCapability:
    agent_id: str
    load_graph: Callable[[], Any]
    load_prompt: Callable[[], str]
    mode: Literal["stream", "invoke"]


def _no_prompt(agent_id: str) -> Callable[[], str]:
    """Build a `load_prompt` that raises, naming the agent, for a not-yet-located
    prompt symbol. Never invent a plausible symbol here — an honest failure beats a
    wrong prompt silently shaping an agent's behavior."""

    def _raise() -> str:
        raise RegistryValidationError(
            f"agent '{agent_id}' has no known prompt symbol yet — "
            "locating it is Task 2's work, not a guess to make here."
        )

    return _raise


# ── requirements ──────────────────────────────────────────────────────────────
def _load_graph_requirements() -> Any:
    from agents_orchestrator.requirements_agent.agents.planning import app

    return app


def _load_prompt_requirements() -> str:
    from agents_orchestrator.requirements_agent.agents.planning import (
        INGESTION_SYS_MESSAGE,
    )

    return INGESTION_SYS_MESSAGE


# ── design ────────────────────────────────────────────────────────────────────
def _load_graph_design() -> Any:
    from agents_orchestrator.design_architecture_agent.agents.architecture import app

    return app


def _load_prompt_design() -> str:
    from agents_orchestrator.design_architecture_agent.agents.architecture import (
        DESIGN_SYS_MESSAGE,
    )

    return DESIGN_SYS_MESSAGE


# ── plan (Project Manager agent; id stays `plan`) ─────────────────────────────
def _load_graph_plan() -> Any:
    from agents_orchestrator.pm_agent.agents.schedule import app

    return app


def _load_prompt_plan() -> str:
    from agents_orchestrator.pm_agent.agents.schedule import PM_SYS_MESSAGE

    return PM_SYS_MESSAGE


# ── development ───────────────────────────────────────────────────────────────
def _load_graph_development() -> Any:
    from agents_orchestrator.development_agent.agents.dev_agent import app

    return app


def _load_prompt_development() -> str:
    from agents_orchestrator.development_agent.prompts.dev_agent_prompt import (
        DEV_SYS_MESSAGE,
    )

    return DEV_SYS_MESSAGE


# ── code_review ───────────────────────────────────────────────────────────────
def _load_graph_code_review() -> Any:
    from agents_orchestrator.code_review_agent.agents.reviewer import app

    return app


# ── security ──────────────────────────────────────────────────────────────────
def _load_graph_security() -> Any:
    from agents_orchestrator.security_agent.agents.scanner import app

    return app


# ── testing (the only invoke-mode / state-machine agent) ─────────────────────
def _load_graph_testing() -> Any:
    """`testing`'s `app` is compiled WITHOUT a checkpointer. Recompile
    `graph_builder` with a `MemorySaver` and cache the result, exactly as
    `copilot_api._graph_for` does for this stage."""
    from agents_orchestrator.testing_agent.agents.testing_agent import graph_builder
    from langgraph.checkpoint.memory import MemorySaver

    if not hasattr(_load_graph_testing, "_cached_app"):
        _load_graph_testing._cached_app = graph_builder.compile(
            checkpointer=MemorySaver()
        )
    return _load_graph_testing._cached_app


# ── deployment ────────────────────────────────────────────────────────────────
def _load_graph_deployment() -> Any:
    from agents_orchestrator.deployment_agent.agents.deployer import app

    return app


# ── documentation ─────────────────────────────────────────────────────────────
def _load_graph_documentation() -> Any:
    from agents_orchestrator.documentation_agent.agents.compiler import app

    return app


REGISTRY: dict[str, AgentCapability] = {
    "requirements": AgentCapability(
        agent_id="requirements",
        load_graph=_load_graph_requirements,
        load_prompt=_load_prompt_requirements,
        mode="stream",
    ),
    "design": AgentCapability(
        agent_id="design",
        load_graph=_load_graph_design,
        load_prompt=_load_prompt_design,
        mode="stream",
    ),
    "plan": AgentCapability(
        agent_id="plan",
        load_graph=_load_graph_plan,
        load_prompt=_load_prompt_plan,
        mode="stream",
    ),
    "development": AgentCapability(
        agent_id="development",
        load_graph=_load_graph_development,
        load_prompt=_load_prompt_development,
        mode="stream",
    ),
    "code_review": AgentCapability(
        agent_id="code_review",
        load_graph=_load_graph_code_review,
        load_prompt=_no_prompt("code_review"),
        mode="stream",
    ),
    "security": AgentCapability(
        agent_id="security",
        load_graph=_load_graph_security,
        load_prompt=_no_prompt("security"),
        mode="stream",
    ),
    # The only invoke-mode / state-machine agent (copilot_api.STATE_MACHINE_STAGES ==
    # {"testing"}).
    "testing": AgentCapability(
        agent_id="testing",
        load_graph=_load_graph_testing,
        load_prompt=_no_prompt("testing"),
        mode="invoke",
    ),
    "deployment": AgentCapability(
        agent_id="deployment",
        load_graph=_load_graph_deployment,
        load_prompt=_no_prompt("deployment"),
        mode="stream",
    ),
    "documentation": AgentCapability(
        agent_id="documentation",
        load_graph=_load_graph_documentation,
        load_prompt=_no_prompt("documentation"),
        mode="stream",
    ),
}


# A hand-edited REGISTRY table must never drift from the canonical stage list — fail
# at import time, not at request time, if it ever does.
assert set(REGISTRY) == set(STAGE_ORDER), (
    f"REGISTRY {sorted(REGISTRY)} does not match STAGE_ORDER {sorted(STAGE_ORDER)}"
)


def get_capability(agent_id: str) -> AgentCapability:
    """Return the `AgentCapability` for `agent_id`.

    Raises `UnknownAgentError` rather than returning `None` — the old engine's
    None-and-continue is how six agents ran with no prompt without anyone noticing.
    """
    try:
        return REGISTRY[agent_id]
    except KeyError:
        raise UnknownAgentError(
            f"'{agent_id}' is not a known agent id. Known ids: {sorted(REGISTRY)}"
        ) from None
