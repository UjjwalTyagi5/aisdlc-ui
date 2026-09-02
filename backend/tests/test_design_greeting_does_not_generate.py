"""A greeting must not produce an architecture document.

From a live session: the user opened the standalone Design page, typed "hi", and
received a complete eight-section architecture document — executive summary, problem
statement, HLD, Mermaid diagrams — for work they had never asked for.

The cause was one clause in DESIGN_SYS_MESSAGE:

    If the structured pipeline context already contains requirements / user stories,
    treat them as the source material and call generate_architecture_from_context
    IMMEDIATELY

The presence of context WAS the instruction. What the user actually said never entered
into it. The Design page threads the project's stories into pipeline_context by default
("From requirements" mode), so on any project with requirements, every first message
triggered a full generation: minutes of work, real tokens, and a design resting on
assumptions nobody had confirmed.

The anti-narration rule this clause belonged to is still worth having — a model that
says "I'll generate that now" and then stops is a genuine failure mode. The fix is to
put the trigger back on the USER ASKING.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _prompt() -> str:
    from agents_orchestrator.design_architecture_agent.agents.architecture import (
        DESIGN_SYS_MESSAGE,
    )

    return DESIGN_SYS_MESSAGE


@pytest.mark.unit
def test_context_alone_no_longer_triggers_generation():
    """The exact clause that caused it must be gone."""
    flat = " ".join(_prompt().split())
    assert "call generate_architecture_from_context immediately" not in flat


@pytest.mark.unit
def test_the_prompt_says_the_user_asking_is_the_trigger():
    flat = " ".join(_prompt().split())
    assert "THE USER ASKING IS THE TRIGGER" in flat
    assert "NOT THE PRESENCE OF CONTEXT" in flat


@pytest.mark.unit
def test_the_prompt_tells_it_what_to_do_with_a_greeting():
    """Removing the bad instruction is not enough — without a positive one the model
    is left guessing, and its prior is to be maximally helpful, which is how this
    happened in the first place."""
    flat = " ".join(_prompt().split())
    assert "REPLY TO WHAT THEY SAID" in flat
    assert "greets you" in flat
    assert "ask what they want built" in flat


@pytest.mark.unit
def test_context_is_still_described_as_source_material():
    """The context must still be USED when a design IS requested — the failure to
    avoid is asking the user to re-paste requirements already in front of the agent."""
    flat = " ".join(_prompt().split())
    assert "SOURCE MATERIAL" in flat
    assert "never ask the user to re-supply requirements" in flat


@pytest.mark.unit
def test_the_anti_narration_rule_survives():
    """The clause was part of a real rule: announcing an action without calling the
    tool is a failure. Fixing the trigger must not delete the rule."""
    flat = " ".join(_prompt().split())
    assert "you MUST emit the tool call" in flat
    assert "announces an action without calling the tool is a failure" in flat


@pytest.mark.unit
def test_no_other_directive_fires_generation_on_context_presence():
    """A second 'do it immediately' anywhere in the prompt would reintroduce this."""
    import re

    flat = " ".join(_prompt().split())
    # Any remaining "immediately" must not be attached to a generate/save tool call.
    for m in re.finditer(r"immediately", flat):
        window = flat[max(0, m.start() - 160): m.start() + 40]
        assert "generate_architecture" not in window, window
        assert "save_architecture" not in window, window
