"""An agent asked for an artifact it cannot make must say so, not make a different one.

From a live session: the user opened the DESIGN agent and asked for "a BRD as PDF".
Design has no BRD tool -- generate_brd belongs to the Requirements agent -- and its
scope boundary named only Development and Testing as other people's work. With no rule
covering requirements artifacts it did the nearest thing it knew and produced a full
architecture document instead. Minutes of generation, real tokens, and the user had to
read the whole thing to discover it was not what they asked for.

The Requirements agent already had the mirror rule ("If the user asks about HLD, LLD,
API design, C4 diagrams, or wireframes: say That's handled by the Design Agent"). The
asymmetry was the bug.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _design() -> str:
    from agents_orchestrator.design_architecture_agent.agents.architecture import (
        DESIGN_SYS_MESSAGE,
    )

    return " ".join(DESIGN_SYS_MESSAGE.split())


def _requirements() -> str:
    from agents_orchestrator.requirements_agent.agents.planning import INGESTION_SYS_MESSAGE

    return " ".join(INGESTION_SYS_MESSAGE.split())


# -- design must redirect requirements work ------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "artifact", ["BRD", "PDD", "MoM", "risk register", "user stories", "acceptance criteria"]
)
def test_design_names_the_requirements_artifacts_it_cannot_make(artifact):
    assert artifact in _design(), f"{artifact} not named in the design scope boundary"


@pytest.mark.unit
def test_design_points_at_the_requirements_agent():
    d = _design()
    assert "That's handled by the Requirements Agent" in d
    assert "You have no tool that produces any of them" in d


@pytest.mark.unit
def test_design_is_told_not_to_substitute():
    """The general rule behind the specific failure -- an agent that cannot do what was
    asked should say so, not deliver the nearest thing it can build."""
    d = _design()
    assert "NEVER SUBSTITUTE A DIFFERENT ARTIFACT FOR THE ONE ASKED FOR" in d


@pytest.mark.unit
def test_design_really_has_no_requirements_tool():
    """The prompt rule matches reality: there is genuinely nothing to call."""
    from agents_orchestrator.design_architecture_agent.agents import architecture

    names = {t.name for t in architecture.tools}
    for forbidden in ("generate_brd", "generate_pdd", "generate_mom",
                      "generate_risk_register", "generate_user_stories"):
        assert forbidden not in names


# -- the mirror direction still holds ------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("topic", ["HLD", "LLD", "API design", "C4 diagrams", "wireframes"])
def test_requirements_still_redirects_design_work(topic):
    assert topic in _requirements()


@pytest.mark.unit
def test_requirements_points_at_the_design_agent():
    assert "That's handled by the Design Agent" in _requirements()


@pytest.mark.unit
def test_requirements_really_has_no_design_tool():
    from agents_orchestrator.requirements_agent.agents import planning

    names = {t.name for t in planning.tools}
    for forbidden in ("generate_architecture", "generate_architecture_from_context",
                      "save_architecture"):
        assert forbidden not in names
