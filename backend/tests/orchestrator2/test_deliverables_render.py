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

#: The OLD rule was "longer than 200 characters", so a structureless blob
#: qualified. That is exactly what filled the Deliverables tab with chat. These
#: fixtures are real documents now, because that is what a deliverable is.
_OLD_RULE_CHARS = 200

_LONG = """# Report

## Findings
Something was examined and here is what came of it, at enough length that the old
length-based rule would have accepted it on its own, which is the point.

## Next steps
The change lands in the stylesheet and the contrast ratios were checked against
the existing palette before anything was written. The header background moves to
a light purple and the header text to a dark purple, which keeps the contrast
ratio above the threshold the rest of the table already meets.
"""


def test_a_short_reply_is_not_a_deliverable():
    assert render("security", "Which service did you mean?") == []


def test_a_substantive_reply_becomes_one_markdown_row():
    rows = render("security", _LONG)
    assert len(rows) == 1
    assert rows[0]["agent"] == "security"
    assert rows[0]["kind"] == "markdown"
    assert rows[0]["content"] == _LONG.strip()


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
        "## High-Level Design\n" + "a" * 260 + "\n\n"
        "## Database Schema\n" + "b" * 260 + "\n"
    )
    rows = render("design", md)
    titles = [r["title"] for r in rows]
    assert "High-Level Design (HLD)" in titles
    assert "Database Schema" in titles
    assert all(r["agent"] == "design" for r in rows)


def test_design_that_does_not_parse_still_yields_one_row():
    """Falling through to nothing would lose the document entirely — the failure mode
    is invisible, because an empty panel looks like an agent that said little.

    `parse_design_markdown` splits on `##`, so this uses `#` headings: a real design
    document that the section parser does not recognise. That is the path the
    fallthrough exists for, and it stayed reachable when the document rule replaced
    the length rule.
    """
    doc = ("# Overview" + chr(10) + "a" * 260 + chr(10) * 2
           + "# Approach" + chr(10) + "b" * 260)
    rows = render("design", doc)
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


# ── what is actually a deliverable ───────────────────────────────────────────
#
# REPORTED, from a real session: eight "Development Report" rows in the tab, every one
# of them an ordinary chat message. Listing the branches in a repo is over 200
# characters, so under the old rule it became a document.
#
# "a deliverable is the document created — a proper document created and saved, for eg
#  design docs for design agent — not normal chats."
#
# So length stopped being the test. A document has STRUCTURE (markdown headings) and is
# not a question. The old 200-character rule was the Copilot's, carried over without
# being re-examined, and it was wrong the moment agents started holding conversations.


def _reply(text):
    return render("development", text)


def test_listing_branches_is_not_a_deliverable():
    """The exact reply that produced a spurious 'Development Report'."""
    branches = (
        "Cloned Company successfully. Here are the branches in Company:\n\n"
        "1. feature/create-branch-and-pr\n"
        "2. feature/dup-banner-purple\n"
        "3. feature/duplicate-table-brown-color\n"
        "4. main\n\n"
        "Which branch should I base my work on? Or give me a new branch name "
        "(e.g. feature/duplicate-table-purple) and I'll create it from main."
    )
    assert len(branches) > _OLD_RULE_CHARS, "long enough to pass the old rule"
    assert _reply(branches) == []


def test_a_question_is_never_a_deliverable():
    long_question = (
        "I can see the current theme uses amber colors throughout the table headers "
        "and the borders, and I have read the full stylesheet to understand how it is "
        "put together before changing anything at all here. Shall I go ahead?"
    )
    assert len(long_question) > _OLD_RULE_CHARS
    assert _reply(long_question) == []


def test_a_plain_status_update_is_not_a_deliverable():
    status = (
        "Created and switched to feature/duplicate-table-purple. Now let me explore "
        "the codebase to find the duplicate table and understand how its styling is "
        "implemented across the stylesheet, so the change stays consistent."
    )
    assert len(status) > _OLD_RULE_CHARS
    assert _reply(status) == []


def test_a_real_document_still_is_one():
    """The other half. A rule that admits nothing is not this rule."""
    doc = (
        "# Duplicate Table — Purple Theme\n\n"
        "## Summary\n" + ("Changing the duplicate banner table to a purple theme. " * 6) +
        "\n\n## Changes\n" + ("The th background becomes #f3e8ff and the text #581c87. " * 6) +
        "\n\n## Verification\n" + ("Checked against the existing contrast ratios. " * 6)
    )
    rows = _reply(doc)
    assert len(rows) == 1
    assert rows[0]["title"] == "Duplicate Table — Purple Theme"


def test_a_document_that_merely_ends_politely_still_counts():
    """A trailing 'Let me know if you want changes' must not disqualify a real
    document — otherwise agents are punished for being conversational about their own
    output, and the tab goes empty again for the opposite reason."""
    doc = (
        "# Security Review\n\n"
        "## Findings\n" + ("No injection paths were found in the handler. " * 8) +
        "\n\n## Recommendations\n" + ("Pin the dependency and re-run the scan. " * 8) +
        "\n\nLet me know if you'd like me to go deeper on any of these?"
    )
    assert len(_reply(doc)) == 1


def test_a_long_reply_with_no_structure_is_not_a_deliverable():
    """Length alone was the old rule, and it is what produced eight chat transcripts in
    the Deliverables tab."""
    rambling = "I looked at the file and then I looked at the other file. " * 20
    assert len(rambling) > 1000
    assert _reply(rambling) == []


def test_a_single_heading_is_not_enough():
    """Pins `_MIN_HEADINGS`. Found by mutation: lowering it to 1 changed no test, which
    made the constant a number nobody was defending.

    Two headings is the line because one is what a chatty reply produces — an agent
    titling its summary — while a real document has sections."""
    titled_chat = (
        "# Summary\n\n"
        + "I looked at the stylesheet and found the table styling in site.css. " * 8
    )
    assert len(titled_chat) > MIN_DELIVERABLE_CHARS
    assert render("development", titled_chat) == []


def test_a_structured_but_tiny_reply_is_not_a_deliverable():
    """Pins `MIN_DELIVERABLE_CHARS`, which the structure rule otherwise makes look
    redundant. Headings alone are cheap: an agent can produce two and thirty characters
    of prose, and that is a note, not a document."""
    tiny = "# Findings\n\n# Next steps\n\nAll clear."
    from agents_orchestrator.orchestrator2.deliverables import _HEADING_LINE_RE
    assert len(_HEADING_LINE_RE.findall(tiny)) >= 2, "structure alone would pass it"
    assert render("security", tiny) == []
