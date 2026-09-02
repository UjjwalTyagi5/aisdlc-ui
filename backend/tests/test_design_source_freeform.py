"""Design is handed no requirements. It calls a tool when it needs them.

WHAT THIS REPLACED. The Design page had a "From requirements / Freeform" toggle. In the
first position it pushed every requirements story into the agent's context before the
user had typed anything; in the second it sent none. The toggle defaulted to ON, was not
persisted (a plain useState, so it silently reset to "From requirements" on every page
load), and demanded a decision before the user knew whether the conversation needed the
stories at all.

It was also wrong by default on a real project. Board ingestion pulls EVERY item on the
board, so what got injected here was one Epic and three project-setup Tasks — not one
user story among them — and the agent dutifully designed a system for them.

The toggle is gone. `read_project_requirements` is a tool the model calls when the
conversation warrants it, which is what `_build_session_context`'s docstring argued for
all along: "context the model chooses to load, not an injection it cannot decline."

TWO PATHS still reach the agent and this file pins both:

  1. pipeline_context.requirements — the ORCHESTRATED pipeline still uses it, so the
     mechanism must keep working. The Design PAGE never populates it any more.
  2. build_context(session_id, "design") — session-keyed. A Design chat session has no
     requirements_payload, so a standalone conversation stays blank.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.agent_context import build_agent_input_text, format_pipeline_context  # noqa: E402

PROJECT = "f45e7d23-c821-44b3-a88b-6175f67ddef0"
TENANT = "81a736f4-cd44-4f63-842c-ae57023d0346"

# What the Design page sends NOW: identity and plumbing, no requirements.
PAGE_CONTEXT = {"project_id": PROJECT, "page": "Design"}

# What the ORCHESTRATOR still sends when it drives Design as part of a pipeline.
ORCHESTRATED = {
    "project_id": PROJECT,
    "requirements": {
        "all_stories": [{"ref": "1", "title": "Project Initiation", "type": "Epic"}],
        "selected_stories": [
            {"ref": "1", "title": "Project Initiation", "description": "Capture scope",
             "acceptance_criteria": ["Given x When y Then z"]},
        ],
    },
}


def _text(pc):
    return build_agent_input_text(
        task_intent="Design a URL shortener",
        pipeline_context=pc,
        pipeline_sections=("requirements",),
    )


# -- the page injects nothing --------------------------------------------------


@pytest.mark.unit
def test_the_design_page_context_carries_no_requirements():
    text = _text(PAGE_CONTEXT)
    assert "[STRUCTURED PIPELINE CONTEXT JSON]" not in text
    assert text.strip() == "Task intent: Design a URL shortener"


@pytest.mark.unit
def test_the_project_id_does_not_leak_into_the_prompt():
    """It is in pipeline_context so the backend can resolve per-stage MCP tools. It is
    plumbing, not context, and naming the project invites the model to go hunting."""
    assert PROJECT not in _text(PAGE_CONTEXT)


@pytest.mark.unit
def test_an_empty_requirements_object_produces_no_section():
    """An empty header would invite the model to ask which stories it should be reading."""
    assert "[STRUCTURED PIPELINE CONTEXT JSON]" not in _text(
        {"project_id": PROJECT, "requirements": {}}
    )


# -- the orchestrated path still works ----------------------------------------


@pytest.mark.unit
def test_the_orchestrator_can_still_supply_requirements():
    """A suite that only proved emptiness would pass just as well if the pipeline
    hand-off were broken outright."""
    text = _text(ORCHESTRATED)
    assert "[STRUCTURED PIPELINE CONTEXT JSON]" in text
    assert "Project Initiation" in text
    assert "Given x When y Then z" in text


@pytest.mark.unit
def test_design_receives_requirements_and_not_later_stages():
    """`pipeline_sections=("requirements",)` is Design's whole filter. Passing
    development or testing output would hand it the answers to what it is designing."""
    text = format_pipeline_context(
        {
            "requirements": {"all_stories": [{"ref": "1", "title": "Wanted"}]},
            "development": {"prs": ["should not appear"]},
            "testing": {"suites": ["should not appear either"]},
        },
        sections=("requirements",),
    )
    assert "Wanted" in text
    assert "should not appear" not in text


# -- the session-keyed path ----------------------------------------------------


@pytest.mark.unit
async def test_a_design_chat_session_gets_no_context(monkeypatch):
    """It stores design_artifacts and no requirements_payload, so a standalone
    conversation starts blank however the toggle used to be set."""
    from config import context_broker

    async def _fake(_sid):
        return {"status": "ok", "design_artifacts": {"hld": "x"}, "requirements_payload": None}

    monkeypatch.setattr(context_broker, "fetch_session_artifacts", _fake)
    assert await context_broker.build_context("any-session", "design") == ""


@pytest.mark.unit
def test_the_api_does_not_inject_project_context(monkeypatch):
    """A guard on the original regression: `build_context_for_project` reads the
    project's runs, and calling it from the API is what made standalone Design inherit a
    pipeline the user never started. It is legitimate inside the TOOL, where the model
    chooses to call it — but must not be imported by the API module.

    Checked on the module NAMESPACE, not the source text: the source names the function
    in the docstring explaining why it must not be auto-injected, so a substring search
    matches the warning as readily as the mistake.
    """
    from agents_orchestrator.design_architecture_agent import (
        design_architecture_agent_api as api,
    )

    assert not hasattr(api, "build_context_for_project")


# -- the tool that replaced the toggle -----------------------------------------


@pytest.mark.unit
def test_the_agent_has_a_tool_to_read_requirements():
    from agents_orchestrator.design_architecture_agent.agents import architecture

    assert "read_project_requirements" in {t.name for t in architecture.tools}


@pytest.mark.unit
def test_the_prompt_tells_the_model_the_tool_exists():
    """An unmentioned tool is a tool that does not get called. The old behaviour was
    automatic, so nothing in the prompt had to ask for it."""
    from agents_orchestrator.design_architecture_agent.agents.architecture import (
        DESIGN_SYS_MESSAGE,
    )

    prompt = " ".join(DESIGN_SYS_MESSAGE.split())
    assert "read_project_requirements" in prompt
    assert "You do NOT automatically receive this project's stories" in prompt
    # And when NOT to call it — the failure mode is over-fetching, not under-fetching.
    assert "DO NOT CALL IT when the user describes what they want" in prompt
    assert "DO NOT CALL IT on a greeting" in prompt


@pytest.mark.unit
async def test_the_tool_says_so_plainly_when_there_is_no_project(monkeypatch):
    """Returning "" would read to the model as "this project has no requirements",
    which is a different and wrong statement."""
    from agents_orchestrator.design_architecture_agent.agents import architecture
    from config import ws_helper

    monkeypatch.setattr(ws_helper, "get_project_id", lambda: None)
    monkeypatch.setattr(ws_helper, "get_tenant_id", lambda: None)

    out = await architecture.read_project_requirements.ainvoke({})
    assert "not attached to a project" in out


@pytest.mark.unit
async def test_the_tool_distinguishes_no_project_from_no_requirements(monkeypatch):
    from agents_orchestrator.design_architecture_agent.agents import architecture
    from config import context_broker, ws_helper

    monkeypatch.setattr(ws_helper, "get_project_id", lambda: PROJECT)
    monkeypatch.setattr(ws_helper, "get_tenant_id", lambda: TENANT)

    async def _empty(*_a, **_kw):
        return ""

    monkeypatch.setattr(context_broker, "build_context_for_project", _empty)
    out = await architecture.read_project_requirements.ainvoke({})
    assert "no requirements recorded yet" in out


@pytest.mark.unit
async def test_the_tool_returns_the_requirements_when_there_are_some(monkeypatch):
    from agents_orchestrator.design_architecture_agent.agents import architecture
    from config import context_broker, ws_helper

    monkeypatch.setattr(ws_helper, "get_project_id", lambda: PROJECT)
    monkeypatch.setattr(ws_helper, "get_tenant_id", lambda: TENANT)

    async def _ctx(*_a, **_kw):
        return "[REQUIREMENTS CONTEXT]\nRequirement items (4):\n  - [Epic] Project Initiation"

    monkeypatch.setattr(context_broker, "build_context_for_project", _ctx)
    out = await architecture.read_project_requirements.ainvoke({})
    assert "Project Initiation" in out
