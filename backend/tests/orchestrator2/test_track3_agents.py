"""Track 3 Phase 1 in the Orchestrator: the first two Code Modernization agents are
real, registered, offered only on a Code Modernization project, and refused
everywhere else — including when a client names them directly on the wire.

Phase 0 (`test_track_scoping.py`) proved the boundary with a monkeypatched stand-in;
these tests hold it against the REAL agents.
"""
from __future__ import annotations

import re

import pytest

from agents_orchestrator.orchestrator2 import router
from agents_orchestrator.orchestrator2.registry import (
    REGISTRY,
    agent_ids_for_track,
    registry_for_track,
)

TRACK3 = ("requirements_modernization", "discovery")


def test_the_modernization_portfolio_is_the_two_built_agents_in_hand_off_order():
    assert agent_ids_for_track("modernization") == TRACK3
    assert set(registry_for_track("modernization")) == set(TRACK3)


@pytest.mark.parametrize("track", ["greenfield", "enhancement", "rpa_infra", "data_engineering"])
def test_no_other_track_is_offered_a_track3_agent(track):
    assert not set(agent_ids_for_track(track)) & set(TRACK3)


def test_both_agents_resolve_a_graph_and_a_real_prompt():
    for agent_id in TRACK3:
        capability = REGISTRY[agent_id]
        assert capability.mode == "stream"
        assert capability.load_graph() is not None
        prompt = capability.load_prompt()
        assert isinstance(prompt, str) and len(prompt.strip()) > 500


def test_every_stage_owner_can_approve_its_own_gate():
    """The boot guard: Track 3's owners (BA, Architect) hold their stages' approve
    permissions, or the platform refuses to start."""
    from shared.authz.catalog import verify_agent_ownership

    assert verify_agent_ownership() == []


# ── dispatch: enforcement on the override path ───────────────────────────────


async def _events(agent_id: str, track: str) -> list[dict]:
    from agents_orchestrator.orchestrator2 import dispatch

    return [
        e async for e in dispatch.run_agent(
            agent_id, text="hello", run_id="run-1", tenant_id="t1", model_id=None,
            offering_id=None, project_id="proj-1", user_id="user-1", context="",
            reason="forced", track=track,
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id", TRACK3)
async def test_a_greenfield_project_cannot_run_a_track3_agent_even_by_naming_it(agent_id):
    events = await _events(agent_id, "greenfield")
    assert events[0]["type"] == "error"
    assert agent_id in events[0]["message"]
    assert not any(e["type"] == "agent.selected" for e in events)
    assert events[-1]["type"] == "stream_end"


@pytest.mark.asyncio
async def test_a_modernization_project_cannot_run_portfolio_1_requirements():
    """Each track owns its own agents: Track 3's Requirements is not Portfolio 1's."""
    events = await _events("requirements", "modernization")
    assert events[0]["type"] == "error"
    assert not any(e["type"] == "agent.selected" for e in events)


# ── router: what a Code Modernization conversation is offered ────────────────


@pytest.mark.asyncio
async def test_a_modernization_turn_offers_exactly_the_two_agents_in_track3_voice(monkeypatch):
    seen: dict = {}

    async def fake_ask(text, *, system_prompt, capabilities, **_kw):
        seen["prompt"], seen["capabilities"] = system_prompt, capabilities
        return router.RoutingDecision(agent_id="requirements_modernization",
                                      reason="You described the migration.", direct_reply=None)

    monkeypatch.setattr(router, "_ask_model", fake_ask)
    decision = await router.route(
        "We need to move our billing system off .NET Framework 4.5", history=[],
        run_id="r", tenant_id="t", project_id="p", model_id=None, offering_id=None,
        track="modernization",
    )
    assert decision.agent_id == "requirements_modernization"
    assert set(seen["capabilities"]) == set(TRACK3)
    assert "CODE MODERNIZATION" in seen["prompt"]
    assert set(re.findall(r"route_to_(\w+)", seen["prompt"])) == set(TRACK3)


@pytest.mark.asyncio
async def test_a_greenfield_turn_is_never_offered_track3(monkeypatch):
    seen: dict = {}

    async def fake_ask(text, *, system_prompt, capabilities, **_kw):
        seen["prompt"], seen["capabilities"] = system_prompt, capabilities
        return router.RoutingDecision(agent_id="design", reason="r", direct_reply=None)

    monkeypatch.setattr(router, "_ask_model", fake_ask)
    await router.route("assess the legacy repository", history=[], run_id="r", tenant_id="t",
                       project_id="p", model_id=None, offering_id=None, track="greenfield")
    assert not set(seen["capabilities"]) & set(TRACK3)
    assert "route_to_discovery" not in seen["prompt"]
    assert "CODE MODERNIZATION" not in seen["prompt"]


@pytest.mark.asyncio
async def test_a_hallucinated_greenfield_pick_on_a_modernization_turn_is_refused(monkeypatch):
    async def fake_ask(*_a, **_kw):
        return router.RoutingDecision(agent_id="design", reason="r", direct_reply=None)

    monkeypatch.setattr(router, "_ask_model", fake_ask)
    decision = await router.route("design it", history=[], run_id="r", tenant_id="t",
                                  project_id="p", model_id=None, offering_id=None,
                                  track="modernization")
    assert decision.agent_id is None
    assert decision.direct_reply


@pytest.mark.asyncio
@pytest.mark.parametrize("text,expected", [
    ("run the requirements agent", "requirements_modernization"),
    ("open the discovery agent", "discovery"),
    ("switch to the discovery and assessment agent", "discovery"),
    ("use the migration intent agent", "requirements_modernization"),
])
async def test_naming_an_agent_resolves_within_the_track_without_a_model_call(monkeypatch, text, expected):
    async def must_not_call(*_a, **_kw):
        raise AssertionError("an explicit command must not cost a model call")

    monkeypatch.setattr(router, "_ask_model", must_not_call)
    decision = await router.route(text, history=[], run_id="r", tenant_id="t", project_id="p",
                                  model_id=None, offering_id=None, track="modernization")
    assert decision.agent_id == expected


def test_the_same_name_means_portfolio_1_requirements_on_greenfield():
    assert router.prefilter("run the requirements agent") == "requirements"
    assert router.prefilter("run the requirements agent", agent_ids_for_track("greenfield")) == "requirements"


def test_the_track3_prompt_starts_with_migration_intent_and_names_the_unbuilt_agents():
    prompt = router._system_prompt(registry_for_track("modernization"), "modernization")
    assert "MIGRATION INTENT" in prompt
    assert "why the modernization is happening" in prompt
    assert "Discovery & Assessment clones and reads the legacy repository" in prompt
    assert "proceed to" in prompt
    unbuilt = prompt.split("Not built for this track yet:", 1)[1].split(".", 1)[0]
    for name in ("Design", "Strategy", "Development", "Documentation"):
        assert name in unbuilt
    assert "Discovery" not in unbuilt


def test_continuity_on_a_modernization_turn_keeps_the_track3_voice():
    prompt = router._system_prompt_with_continuity(
        "discovery", registry_for_track("modernization"), "modernization")
    assert "CODE MODERNIZATION" in prompt
    assert "THE Discovery & Assessment AGENT IS MID-CONVERSATION" in prompt


# ── deliverables: what the two agents file ───────────────────────────────────


def test_the_assessment_report_is_filed_as_a_deliverable(tmp_path):
    from agents_orchestrator.discovery_agent.analysis.assessment import (
        assess_repository,
        assessment_markdown,
    )
    from agents_orchestrator.orchestrator2.deliverables import render
    from tests.discovery.legacy_fixture import build_legacy_repo

    report = assessment_markdown(assess_repository(build_legacy_repo(tmp_path / "r")), max_modules=25)
    rows = render("discovery", report)
    assert len(rows) == 1
    assert rows[0]["title"].startswith("Discovery & Assessment")


def test_the_migration_brief_is_filed_as_a_deliverable():
    from agents_orchestrator.orchestrator2.deliverables import render
    from agents_orchestrator.requirements_modernization_agent.brief import brief_markdown
    from shared.models.artifacts import MigrationIntentArtifact

    brief = MigrationIntentArtifact(
        system_name="Billing", business_drivers=["EOL runtime"],
        current_state={"stack": ".NET Framework 4.5.2"}, target_state={"stack": ".NET 8"},
        in_scope=["Billing.Web"], constraints=["By March"], success_criteria=["Same invoices"],
    )
    rows = render("requirements_modernization", brief_markdown(brief))
    assert len(rows) == 1
    assert rows[0]["title"] == "Migration Intent Brief — Billing"
