"""Phase 0 of Track 3 (Code Modernization): the orchestrator becomes track-aware.

Before this, `orchestrator2.registry.AGENT_IDS`/`REGISTRY` and
`orchestrator2.router`'s roster were a single global set with no notion of
track — see `help/track3-implementation-plan.md` §1-2. That is safe only because
`config.agent_registry.TRACK_PORTFOLIOS["modernization"]` (and `rpa_infra`,
`data_engineering`) are still empty today: the moment a real agent is added to one
of them, every project on every OTHER track would start seeing it offered too,
unless something enforces the boundary.

These tests prove the boundary exists and holds, using the tools this module
provides for testing it — a monkeypatched `AGENT_REGISTRY`/`TRACK_PORTFOLIOS`
entry standing in for a Track 3 agent that does not exist yet, since a real one
cannot be built until this phase lands. They also pin that every EXISTING call
site (nothing here names a track) is untouched — the whole point of defaulting to
`"greenfield"` everywhere.
"""
from __future__ import annotations

import pytest

from config import agent_registry as agent_registry_module
from config.agent_registry import (
    AgentDefinition,
    TRACK_PORTFOLIOS,
    UnknownTrackError,
    agents_for_track,
    stage_order_for_track,
)


# ── config.agent_registry: the portfolio filter ────────────────────────────────


def test_greenfield_and_enhancement_portfolios_are_the_full_registry_today():
    """Nothing changes for the two tracks that share Portfolio 1 — this is the
    regression the whole phase must not break."""
    from config.agent_registry import AGENT_REGISTRY

    assert agents_for_track("greenfield") == AGENT_REGISTRY
    assert agents_for_track("enhancement") == AGENT_REGISTRY


@pytest.mark.parametrize("track", ["modernization", "rpa_infra", "data_engineering"])
def test_unbuilt_tracks_have_empty_portfolios(track):
    """Matches `TRACK_PORTFOLIOS[track] == []` — "target design, not yet built"
    (multi-track-agent-access-design.md §Portfolio 2-4) is a real, empty state,
    not an oversight."""
    assert agents_for_track(track) == {}
    assert stage_order_for_track(track) == []


def test_unknown_track_raises_rather_than_returning_empty():
    """An empty portfolio is meaningful (a track with nothing built yet). A typo'd
    track name must not be indistinguishable from one — it must fail loudly."""
    with pytest.raises(UnknownTrackError):
        agents_for_track("modernizaton")  # typo, deliberately
    with pytest.raises(UnknownTrackError):
        stage_order_for_track("")


def test_stage_order_for_track_matches_the_global_stage_order_for_portfolio_1():
    from shared.services.orchestrator.progression import STAGE_ORDER

    assert stage_order_for_track("greenfield") == list(STAGE_ORDER)


def test_a_track3_agent_added_to_agent_registry_does_not_leak_into_other_tracks(
    monkeypatch,
):
    """Simulates the exact moment this phase exists to make safe: a Discovery &
    Assessment-shaped entry lands in `AGENT_REGISTRY` (built, mounted) before or
    without being added to `TRACK_PORTFOLIOS["modernization"]`. It must not appear
    in ANY track's portfolio until it is explicitly added to that track's list —
    membership in `AGENT_REGISTRY` alone must never be enough.
    """
    patched_registry = dict(agent_registry_module.AGENT_REGISTRY)
    patched_registry["discovery_assessment"] = AgentDefinition(
        id="discovery_assessment",
        name="Discovery & Assessment Agent",
        pipeline_position=1,
        input_artifacts=[],
        output_artifact="discovery_artifacts",
        route_path="/chat-discovery-assessment-agent",
    )
    monkeypatch.setattr(agent_registry_module, "AGENT_REGISTRY", patched_registry)

    # Not in TRACK_PORTFOLIOS["modernization"] (unpatched) -> not offered anywhere.
    assert "discovery_assessment" not in agents_for_track("greenfield")
    assert "discovery_assessment" not in agents_for_track("modernization")


def test_a_track_portfolio_naming_an_unregistered_agent_raises(monkeypatch):
    """The reverse inconsistency: `TRACK_PORTFOLIOS` promises (per its own module
    comment) that an id is added there "only once it's actually built and
    mounted" — i.e. only once it is already in `AGENT_REGISTRY`. If that promise
    is ever broken, `agents_for_track` must raise, not silently drop the id."""
    patched_portfolios = {**TRACK_PORTFOLIOS, "modernization": ["not_a_real_agent_id"]}
    monkeypatch.setattr(agent_registry_module, "TRACK_PORTFOLIOS", patched_portfolios)

    with pytest.raises(KeyError):
        agent_registry_module.agents_for_track("modernization")


# ── orchestrator2.registry: the router/dispatch-facing layer ──────────────────


def test_agent_ids_for_track_matches_config_agent_registry():
    from agents_orchestrator.orchestrator2.registry import (
        AGENT_IDS,
        agent_ids_for_track,
    )

    assert agent_ids_for_track("greenfield") == AGENT_IDS
    assert agent_ids_for_track("enhancement") == AGENT_IDS
    assert agent_ids_for_track("modernization") == ()


def test_registry_for_track_matches_the_global_registry_for_portfolio_1():
    from agents_orchestrator.orchestrator2.registry import REGISTRY, registry_for_track

    assert registry_for_track("greenfield") == REGISTRY
    assert registry_for_track("modernization") == {}


def test_registry_for_track_raises_for_a_portfolio_naming_an_unmounted_capability(
    monkeypatch,
):
    """The build-order guard: an id in a track's portfolio with no `AgentCapability`
    registered in `REGISTRY` yet must raise, not silently vanish from the roster —
    that combination means someone added the agent to `TRACK_PORTFOLIOS` before
    wiring its graph/prompt here, and the router offering nothing instead of
    erroring would look identical to the track legitimately having no agents."""
    from agents_orchestrator.orchestrator2 import registry as reg

    monkeypatch.setattr(
        agent_registry_module,
        "TRACK_PORTFOLIOS",
        {**agent_registry_module.TRACK_PORTFOLIOS, "modernization": ["requirements"]},
    )
    # "requirements" IS in config.agent_registry.AGENT_REGISTRY (so
    # agents_for_track succeeds) but this simulates it NOT yet being in
    # orchestrator2.registry.REGISTRY by patching REGISTRY to omit it.
    patched = dict(reg.REGISTRY)
    del patched["requirements"]
    monkeypatch.setattr(reg, "REGISTRY", patched)

    with pytest.raises(reg.UnknownAgentError):
        reg.registry_for_track("modernization")


# ── orchestrator2.router: what the model is offered and what it may pick ──────


@pytest.mark.asyncio
async def test_route_defaults_to_greenfield_and_is_unchanged(monkeypatch):
    """No test in test_router.py names a track. This proves the default reproduces
    the exact prior behaviour: routing an unambiguous command still resolves the
    same way with no `track` argument at all."""
    from agents_orchestrator.orchestrator2 import router

    decision = await router.route(
        "run the testing agent",
        history=[],
        run_id="run-1",
        tenant_id="t1",
        project_id="proj-1",
        model_id=None,
        offering_id=None,
    )
    assert decision.agent_id == "testing"


@pytest.mark.asyncio
async def test_a_track_with_no_agents_answers_directly_without_a_model_call(
    monkeypatch,
):
    """`modernization` has an empty portfolio today. Routing on it must not attempt
    a `bind_tools([])` model call — it must short-circuit to a direct reply saying
    so, and must not call the model at all (proven by never installing a fake
    resolver: if this reached `_ask_model`, resolution would raise `ImportError`
    or similar on an unconfigured path, not the direct-reply RoutingDecision)."""
    from agents_orchestrator.orchestrator2 import router

    decision = await router.route(
        "migrate this legacy service",
        history=[],
        run_id="run-1",
        tenant_id="t1",
        project_id="proj-1",
        model_id=None,
        offering_id=None,
        track="modernization",
    )
    assert decision.agent_id is None
    assert decision.direct_reply
    assert "not" in decision.direct_reply.lower() or "no" in decision.direct_reply.lower()


@pytest.mark.asyncio
async def test_prefilter_naming_an_agent_outside_the_track_falls_through(monkeypatch):
    """`prefilter` itself is track-agnostic (pure, by design — see its own
    docstring), so `route` must not trust a match outside the scoped track. On an
    empty-portfolio track this means falling all the way through to the
    no-agents-yet direct reply, never to the named (but out-of-track) agent."""
    from agents_orchestrator.orchestrator2 import router

    # "run the testing agent" matches `prefilter` unconditionally — "testing" is
    # not in modernization's (currently empty) portfolio, so it must not be
    # trusted here.
    decision = await router.route(
        "run the testing agent",
        history=[],
        run_id="run-1",
        tenant_id="t1",
        project_id="proj-1",
        model_id=None,
        offering_id=None,
        track="modernization",
    )
    assert decision.agent_id is None


def test_validated_rejects_an_id_outside_the_given_track(monkeypatch):
    from agents_orchestrator.orchestrator2 import router

    decision = router.RoutingDecision(
        agent_id="testing", reason="picked", direct_reply=None
    )
    result = router._validated(decision, valid_ids=("requirements", "design"))
    assert result.agent_id is None
    assert result.direct_reply


def test_system_prompt_none_default_matches_full_registry():
    """`_system_prompt(None)` (every existing call site) must render byte-identical
    to explicitly passing the full `REGISTRY` — this is what makes the change
    invisible to `test_router.py`'s pinned prompt-string assertions."""
    from agents_orchestrator.orchestrator2.registry import REGISTRY
    from agents_orchestrator.orchestrator2 import router

    assert router._system_prompt() == router._system_prompt(REGISTRY)


def test_system_prompt_scoped_to_a_track_names_only_that_tracks_agents():
    from agents_orchestrator.orchestrator2 import registry as reg
    from agents_orchestrator.orchestrator2 import router

    subset = {"requirements": reg.REGISTRY["requirements"]}
    prompt = router._system_prompt(subset)
    assert "route_to_requirements" in prompt
    assert "route_to_testing" not in prompt
    assert "route_to_deployment" not in prompt


# ── orchestrator2.dispatch: enforcement, not just the router's offer ──────────


@pytest.mark.asyncio
async def test_run_agent_refuses_an_id_outside_the_given_track():
    """The override_agent bypass: a client can name `agent_id` directly over the
    wire, skipping `router.route` entirely. `run_agent` must refuse an id outside
    `track`'s portfolio itself — the router being scoped correctly is not enough,
    because this path never goes through the router at all."""
    from agents_orchestrator.orchestrator2 import dispatch

    events = [
        e
        async for e in dispatch.run_agent(
            "testing",
            text="hello",
            run_id="run-1",
            tenant_id="t1",
            model_id=None,
            offering_id=None,
            project_id="proj-1",
            user_id="user-1",
            context="",
            reason="forced",
            track="modernization",
        )
    ]
    assert events[0]["type"] == "error"
    assert "testing" in events[0]["message"]
    assert "modernization" in events[0]["message"]
    assert events[-1]["type"] == "stream_end"
    # Never got as far as announcing the agent — it was refused before dispatch.
    assert not any(e["type"] == "agent.selected" for e in events)


@pytest.mark.asyncio
async def test_run_agent_defaults_to_greenfield_unchanged(monkeypatch):
    """No existing caller of `run_agent` names a track. An unknown id must still
    refuse exactly as before (`get_capability`'s old behaviour), reachable now
    through the default `track="greenfield"` scoping instead."""
    from agents_orchestrator.orchestrator2 import dispatch

    events = [
        e
        async for e in dispatch.run_agent(
            "not_a_real_agent",
            text="hello",
            run_id="run-1",
            tenant_id="t1",
            model_id=None,
            offering_id=None,
            project_id="proj-1",
            user_id="user-1",
            context="",
            reason="forced",
        )
    ]
    assert events[0]["type"] == "error"
    assert "not_a_real_agent" in events[0]["message"]
    assert events[-1]["type"] == "stream_end"
