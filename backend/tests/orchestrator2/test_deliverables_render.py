"""Turning an agent's turn into deliverable rows. Pure — no database here.

The threshold exists because an agent's clarifying question is not a document.
"Which service did you mean?" must stay in chat; a PRD must not.
"""
import inspect

from agents_orchestrator.orchestrator2.deliverables import (
    MIN_DELIVERABLE_CHARS,
    derive_title,
    render,
)

_LONG = "x" * (MIN_DELIVERABLE_CHARS + 10)


def test_a_short_reply_is_not_a_deliverable():
    assert render("security", "Which service did you mean?") == []


def test_a_substantive_reply_becomes_one_markdown_row():
    rows = render("security", _LONG)
    assert len(rows) == 1
    assert rows[0]["agent"] == "security"
    assert rows[0]["kind"] == "markdown"
    assert rows[0]["content"] == _LONG


def test_the_project_manager_agent_produces_a_deliverable_like_any_other():
    """`plan` is the Project Manager agent. `sections_from_run` never had a branch
    for it, so `plan_artifacts` was written by nothing and read by nothing and its
    output rendered nowhere. Nothing about this agent is special; that was the bug."""
    rows = render("plan", _LONG)
    assert len(rows) == 1 and rows[0]["agent"] == "plan"


def test_every_registry_agent_can_produce_a_deliverable():
    from agents_orchestrator.orchestrator2.registry import AGENT_IDS
    for agent_id in AGENT_IDS:
        assert render(agent_id, _LONG), f"{agent_id} produced nothing"


def test_design_is_split_into_its_sections():
    md = (
        "## High-Level Design\n" + "a" * 120 + "\n\n"
        "## Database Schema\n" + "b" * 120 + "\n"
    )
    rows = render("design", md)
    titles = [r["title"] for r in rows]
    assert "High-Level Design (HLD)" in titles
    assert "Database Schema" in titles
    assert all(r["agent"] == "design" for r in rows)


def test_design_that_does_not_parse_still_yields_one_row():
    """Falling through to nothing would lose the document entirely — the failure
    mode is invisible, because an empty panel looks like an agent that said little."""
    rows = render("design", _LONG)
    assert len(rows) == 1 and rows[0]["kind"] == "markdown"


def test_title_comes_from_the_first_heading_when_there_is_one():
    assert derive_title("requirements", "# Billing Rework PRD\nbody") == "Billing Rework PRD"


def test_title_falls_back_to_the_agent_name():
    assert derive_title("code_review", "no heading here") == "Code Review Report"


def test_the_plan_agent_is_named_project_manager_in_titles():
    """User-facing text says Project Manager agent, never 'Plan agent'."""
    assert derive_title("plan", "no heading here") == "Project Manager Report"


def test_every_agent_has_a_display_name():
    """A deliverable under a blank heading is unattributable in the panel."""
    from agents_orchestrator.orchestrator2.deliverables import DISPLAY_NAME
    from agents_orchestrator.orchestrator2.registry import AGENT_IDS
    assert set(AGENT_IDS) <= set(DISPLAY_NAME)


def test_render_is_pure_and_touches_no_database():
    from agents_orchestrator.orchestrator2 import deliverables
    src = inspect.getsource(deliverables.render)
    for forbidden in ("session", "select(", "await ", "commit"):
        assert forbidden not in src, f"render() must stay pure; found {forbidden!r}"
