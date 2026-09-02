"""Freeform Design means the agent gets NO requirements. Not "fewer" — none.

The Design page has a source toggle: "From requirements" threads the project's stories
into the agent's context, "Freeform" sends none so it designs purely from the chat.

THIS HAS ALREADY REGRESSED ONCE. A user opened standalone Design, typed "hi", and
watched the agent start an end-to-end design of four board items it should never have
seen. The cause was a PROJECT-KEYED fallback in the context builder: it read the
project's most recent Run, so the standalone page inherited whatever Requirements last
produced. It was removed, and `_build_session_context`'s docstring now says in capitals
not to add it back — but nothing tested the behaviour, on either side.

TWO INDEPENDENT PATHS reach the agent and BOTH must stay quiet in freeform:

  1. `pipeline_context.requirements` -> build_agent_input_text(sections=("requirements",)).
     The toggle controls this one: the frontend passes `requirements: undefined`, which
     JSON omits.

  2. `build_context(session_id, "design")` -> the session's stored requirements_payload.
     The toggle cannot reach this. A Design chat session has no requirements_payload, so
     it stays empty; an ORCHESTRATED run does, which is how the pipeline legitimately
     feeds Design. Both halves are asserted here, because "freeform is empty" is only
     meaningful alongside "the pipeline still works".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.agent_context import build_agent_input_text, format_pipeline_context  # noqa: E402

PROJECT = "f45e7d23-c821-44b3-a88b-6175f67ddef0"

# The exact shape app/(app)/projects/[id]/design/page.tsx sends.
FROM_REQUIREMENTS = {
    "project_id": PROJECT,
    "page": "Design",
    "requirements": {
        "all_stories": [{"ref": "1", "title": "Project Initiation", "type": "Epic"}],
        "selected_stories": [
            {"ref": "1", "title": "Project Initiation", "description": "Capture scope",
             "acceptance_criteria": ["Given x When y Then z"]},
        ],
    },
}

# Freeform omits the key entirely — `undefined` does not survive JSON.
FREEFORM = {"project_id": PROJECT, "page": "Design"}


def _text(pc):
    return build_agent_input_text(
        task_intent="Design a URL shortener",
        pipeline_context=pc,
        pipeline_sections=("requirements",),
    )


# -- freeform sends nothing ----------------------------------------------------


@pytest.mark.unit
def test_freeform_sends_no_requirements():
    text = _text(FREEFORM)
    assert "Project Initiation" not in text
    assert "[STRUCTURED PIPELINE CONTEXT JSON]" not in text


@pytest.mark.unit
def test_freeform_sends_only_the_users_own_words():
    """THE REGRESSION. "hi" must reach the agent as "hi" — anything else and it starts
    designing a system the user never mentioned."""
    assert _text(FREEFORM).strip() == "Task intent: Design a URL shortener"


@pytest.mark.unit
def test_freeform_does_not_leak_the_project_id_either():
    """project_id is in pipeline_context so the backend can resolve per-stage MCP
    tools. It is plumbing, not context, and naming the project invites the model to go
    looking for its stories."""
    assert PROJECT not in _text(FREEFORM)


@pytest.mark.unit
def test_an_empty_requirements_object_is_still_nothing():
    """A page with no stories yet sends `requirements: {}`. An empty section header
    would invite the model to ask which stories it should be reading."""
    pc = {"project_id": PROJECT, "requirements": {}}
    assert "[STRUCTURED PIPELINE CONTEXT JSON]" not in _text(pc)


# -- from-requirements sends the stories --------------------------------------


@pytest.mark.unit
def test_from_requirements_sends_the_stories():
    text = _text(FROM_REQUIREMENTS)
    assert "[STRUCTURED PIPELINE CONTEXT JSON]" in text
    assert "Project Initiation" in text
    assert "Given x When y Then z" in text


@pytest.mark.unit
def test_the_board_work_item_type_survives():
    """Board ingestion pulls Epics and chore Tasks alongside real stories. Without the
    type they all reach the agent as things to design a system for — which is exactly
    what produced a design for four SDLC-setup board items."""
    assert '"type": "Epic"' in _text(FROM_REQUIREMENTS)


@pytest.mark.unit
def test_the_two_modes_differ_by_more_than_formatting():
    assert len(_text(FROM_REQUIREMENTS)) > len(_text(FREEFORM)) + 100


# -- only the requested section is passed through ------------------------------


@pytest.mark.unit
def test_design_receives_requirements_and_not_later_stages():
    """`pipeline_sections=("requirements",)` is the Design agent's whole filter. Passing
    development or testing output would hand it the answers to what it is designing."""
    pc = {
        "requirements": {"all_stories": [{"ref": "1", "title": "Wanted"}]},
        "development": {"prs": ["should not appear"]},
        "testing": {"suites": ["should not appear either"]},
    }
    text = format_pipeline_context(pc, sections=("requirements",))
    assert "Wanted" in text
    assert "should not appear" not in text


@pytest.mark.unit
def test_nothing_at_all_produces_no_context_block():
    assert format_pipeline_context(None, sections=("requirements",)) == ""
    assert format_pipeline_context({}, sections=("requirements",)) == ""


# -- the second path: the session-keyed lookup --------------------------------


@pytest.mark.unit
async def test_a_session_without_requirements_gives_design_no_context(monkeypatch):
    """A standalone Design chat session stores design_artifacts and no
    requirements_payload, so this path contributes nothing — which is what keeps
    freeform freeform even though the toggle cannot reach it."""
    from config import context_broker

    async def _fake(_sid):
        return {"status": "ok", "design_artifacts": {"hld": "x"}, "requirements_payload": None}

    monkeypatch.setattr(context_broker, "fetch_session_artifacts", _fake)
    assert await context_broker.build_context("any-session", "design") == ""


@pytest.mark.unit
async def test_an_orchestrated_session_does_feed_design(monkeypatch):
    """The other half of the contract: the pipeline is SUPPOSED to hand Requirements to
    Design. A test that only proved emptiness would pass just as well if this path were
    broken outright."""
    from config import context_broker

    async def _fake(_sid):
        return {
            "status": "ok",
            "requirements_payload": {
                "board_project": "sdlc",
                "work_items": [{"title": "Password reset", "type": "User Story"}],
            },
        }

    monkeypatch.setattr(context_broker, "fetch_session_artifacts", _fake)
    ctx = await context_broker.build_context("run-as-session", "design")
    assert ctx
    assert "Password reset" in ctx


@pytest.mark.unit
def test_the_project_keyed_fallback_is_not_reinstated():
    """A guard on the regression itself. `build_context_for_project` reads the project's
    most recent Run; calling it from the Design API is what made the standalone page
    inherit a pipeline the user never started.

    The check is on the MODULE NAMESPACE, not the source text — the source names the
    function in the docstring explaining why it must not be used, so a substring search
    matches the warning as readily as the mistake.
    """
    from agents_orchestrator.design_architecture_agent import (
        design_architecture_agent_api as api,
    )

    assert not hasattr(api, "build_context_for_project"), (
        "the project-keyed fallback is imported again; standalone Design will inherit "
        "the project's last Requirements run"
    )
