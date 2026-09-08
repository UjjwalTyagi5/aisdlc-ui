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
    """Raised by `validate_registry()`, naming every agent whose graph failed to
    load, whose prompt failed to load, or whose prompt resolved to an empty or
    whitespace-only string (the same silent failure wearing a different hat).

    Collects every failure before raising once — the old engine's None-and-continue
    is exactly how six agents ran with no system prompt without anyone noticing;
    failing on the first gap here would just make someone re-run this nine times.
    """


class PromptNotApplicableError(Exception):
    """Raised by the `load_prompt` of an invoke-mode agent (`testing`, the only one).

    Testing is a state machine that reads `state["user_prompt"]`; its nodes carry
    their own prompts, and it never receives an injected system message. This is a
    deliberate, recorded design decision — distinct from `RegistryValidationError`
    on purpose, so a missing STREAM-agent prompt (a bug) can never be confused with
    an invoke-agent's prompt being not applicable (not a bug). Do not "fix" this by
    inventing a system prompt for `testing`.
    """


@dataclass(frozen=True)
class AgentCapability:
    agent_id: str
    load_graph: Callable[[], Any]
    load_prompt: Callable[[], str]
    mode: Literal["stream", "invoke"]


def _prompt_not_applicable(agent_id: str) -> Callable[[], str]:
    """Build the `load_prompt` for an invoke-mode agent: raises
    `PromptNotApplicableError`, never `RegistryValidationError`. Invoke-mode agents
    (only `testing`, today) carry their own per-node prompts and are never given an
    injected system message — `validate_registry()` knows this from `mode` and never
    calls this at all, but if something else calls it directly the distinct
    exception type makes the "not applicable" story explicit rather than looking
    like the missing-prompt bug this task closed."""

    def _raise() -> str:
        raise PromptNotApplicableError(
            f"agent '{agent_id}' is invoke-mode — it has no system prompt by "
            "design (its graph nodes carry their own prompts). This is not a "
            "gap; do not wire one."
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


def _load_prompt_code_review() -> str:
    from agents_orchestrator.code_review_agent.prompts.review_prompt import (
        CODE_REVIEW_SYSTEM_PROMPT,
    )

    return CODE_REVIEW_SYSTEM_PROMPT


# ── security ──────────────────────────────────────────────────────────────────
def _load_graph_security() -> Any:
    from agents_orchestrator.security_agent.agents.scanner import app

    return app


def _load_prompt_security() -> str:
    from agents_orchestrator.security_agent.prompts.security_prompt import (
        SECURITY_SYSTEM_PROMPT,
    )

    return SECURITY_SYSTEM_PROMPT


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


def _load_prompt_deployment() -> str:
    from agents_orchestrator.deployment_agent.prompts.deploy_prompt import (
        DEPLOY_SYSTEM_PROMPT,
    )

    return DEPLOY_SYSTEM_PROMPT


# ── documentation ─────────────────────────────────────────────────────────────
def _load_graph_documentation() -> Any:
    from agents_orchestrator.documentation_agent.agents.compiler import app

    return app


def _load_prompt_documentation() -> str:
    from agents_orchestrator.documentation_agent.prompts.doc_prompt import (
        DOC_SYSTEM_PROMPT,
    )

    return DOC_SYSTEM_PROMPT


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
        load_prompt=_load_prompt_code_review,
        mode="stream",
    ),
    "security": AgentCapability(
        agent_id="security",
        load_graph=_load_graph_security,
        load_prompt=_load_prompt_security,
        mode="stream",
    ),
    # The only invoke-mode / state-machine agent (copilot_api.STATE_MACHINE_STAGES ==
    # {"testing"}). Prompt is deliberately NOT applicable — see PromptNotApplicableError.
    "testing": AgentCapability(
        agent_id="testing",
        load_graph=_load_graph_testing,
        load_prompt=_prompt_not_applicable("testing"),
        mode="invoke",
    ),
    "deployment": AgentCapability(
        agent_id="deployment",
        load_graph=_load_graph_deployment,
        load_prompt=_load_prompt_deployment,
        mode="stream",
    ),
    "documentation": AgentCapability(
        agent_id="documentation",
        load_graph=_load_graph_documentation,
        load_prompt=_load_prompt_documentation,
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


def validate_registry() -> None:
    """Resolve every agent's graph, and — for `stream`-mode agents only — its
    prompt, collecting every failure before raising once.

    Six of nine agents used to run with no system prompt at all (spec §11.2): the
    old engine's `_system_prompt_for` returned `None` for them and the caller
    carried on, so the symptom was an agent that churned or answered vaguely
    instead of an error anyone could act on. This function makes that state
    unshippable — call it at boot.

    `invoke`-mode agents (only `testing`, today) are graph-only by design: a state
    machine whose nodes carry their own prompts, never given an injected system
    message. `mode` decides this, not a guess made here — a `stream` agent's
    `load_prompt` is ALWAYS required to resolve, and an `invoke` agent's is never
    even called, so `testing` lacking a system prompt can never be mistaken for the
    six-agent bug this closes.

    A prompt that resolves to an empty or whitespace-only string counts as
    missing — that is the same silent failure wearing a different hat.
    """
    failures: list[str] = []

    for agent_id, capability in REGISTRY.items():
        try:
            capability.load_graph()
        except Exception as exc:  # noqa: BLE001 - collect, don't stop at the first
            failures.append(f"{agent_id}: graph failed to load ({exc!r})")

        if capability.mode != "stream":
            # invoke-mode agents (testing) are graph-only by design — never call
            # load_prompt for them; see PromptNotApplicableError.
            continue

        try:
            prompt = capability.load_prompt()
        except Exception as exc:  # noqa: BLE001 - collect, don't stop at the first
            failures.append(f"{agent_id}: prompt failed to load ({exc!r})")
            continue

        if not isinstance(prompt, str) or not prompt.strip():
            failures.append(
                f"{agent_id}: prompt resolved to empty/whitespace — treated as missing"
            )

    if failures:
        detail = "\n".join(f"  - {failure}" for failure in failures)
        raise RegistryValidationError(
            f"registry validation failed for {len(failures)} item(s):\n{detail}"
        )
