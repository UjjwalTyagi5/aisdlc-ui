"""Turning an agent's turn into deliverable rows. Pure — no database here.

The threshold exists because an agent's clarifying question is not a document.
"Which service did you mean?" must stay in chat; a PRD must not.
"""
import inspect

import pytest

from agents_orchestrator.orchestrator2.deliverables import (
    MIN_DELIVERABLE_CHARS,
    _HEADING_LINE_RE,
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


# ── an agent saying it CANNOT work is not a work product ─────────────────────
#
# FOUND BY RUNNING ALL NINE AGENTS AGAINST THE REAL STACK
# (`scripts/live_all_agents_check.py`). Three of the six captured deliverables were
# refusals — the agent explaining, at length and in well-formed markdown, that it could
# not do the job:
#
#     Security    "⚠️ Security Review Not Possible"
#     Deployment  "Cannot Proceed — Missing Prerequisites"
#     Testing     "What I Can Do"
#
# Every one clears the structure rule: real headings, well over 400 characters. They
# are the same complaint as the eight chat transcripts, arriving through a door the
# structure rule cannot close, because a refusal IS structured — that is what makes it
# a good refusal.
#
# It is also the worst possible thing to file. A user opening Deliverables sees a
# Security Review sitting under Security, and it says the opposite of what its title
# promises.
#
# The rule is deliberately NARROW: a first-person statement of inability, near the
# start, or a heading that announces the work did not happen. An agent describing what
# the SYSTEM cannot do ("the API cannot validate the token") is writing a finding, and
# findings are exactly what a security review is made of.

_REFUSALS = {
    "security": (
        "I'll perform a comprehensive security review of the coffee-ordering app. Let "
        "me start by running all the security scans.I see that no repository branch "
        "has been selected for scanning.I apologize, but I cannot produce a security "
        "review at this time. Here's why:\n\n---\n\n"
        "## ⚠️ Security Review Not Possible\n\n"
        "**Reason:** No repository workspace is prepared for scanning.\n\n"
        "The security scanning infrastructure requires a specific branch or Pull "
        "Request to be selected before I can:\n\n"
        "1. Run dependency scans (SCA/Trivy) to identify vulnerable packages\n"
        "2. Run static analysis (SAST/Semgrep) to find code-level vulnerabilities\n"
        "3. Scan for hardcoded secrets (Gitleaks)\n\n"
        "## What I Need\n\nSelect a branch and I will run the full review."
    ),
    "deployment": (
        "I'll start by inspecting the repository to understand the stack.I need to "
        "stop here and be direct with you.\n\n---\n\n"
        "## Cannot Proceed — Missing Prerequisites\n\n"
        "I cannot produce the deployment package you've requested because:\n\n"
        "### 1. No Repository Workspace\n"
        "There is no repository checked out for me to analyze. I need access to the "
        "actual source code to detect the stack, find existing Dockerfiles, and "
        "identify the correct ports and image names.\n\n"
        "### 2. No Deploy Connector Bound\n"
        "There is no deploy connector configured for this project, which tells me "
        "which CI/CD system to write for."
    ),
    "testing": (
        "I appreciate you sharing that context, but I need to clarify something "
        "important:\n\n**I don't have access to any Excel file.** I cannot:\n"
        "- View files on your computer\n- Access previously uploaded documents\n"
        "- See attachments unless they're shared directly in this conversation\n\n"
        "---\n\n## What I Can Do\n\nIf you'd like me to review, validate, or "
        "enhance your test plan, please either:\n\n"
        "1. Paste the content directly into the chat\n"
        "2. Share it as a table in text format\n"
        "3. Describe the test cases you've created\n\n"
        "## Or, I Can Generate a Fresh Test Plan\n\nSay the word and I will."
    ),
}


@pytest.mark.parametrize("agent_id", sorted(_REFUSALS))
def test_a_refusal_is_not_a_deliverable(agent_id):
    """Verbatim from the live run, shortened only in the middle of lists."""
    body = _REFUSALS[agent_id]
    assert len(body) > MIN_DELIVERABLE_CHARS, "long enough to reach the rule"
    assert len(_HEADING_LINE_RE.findall(body)) >= 2, "structured enough to pass it"
    assert render(agent_id, body) == [], (
        f"the {agent_id} agent's refusal was filed as a document"
    )


def test_a_security_review_that_reports_what_the_system_cannot_do_is_a_deliverable():
    """The false positive that would make this rule worse than the bug.

    A security review is MADE of sentences about what cannot be done. The rule reads
    the agent's own statements about ITSELF, not the subject matter — get this wrong
    and Security can never file a review again.
    """
    review = (
        "# Security Review — Coffee Ordering App\n\n"
        "## Findings\n\n"
        "**F-001 (High).** The checkout endpoint cannot validate that the cart total "
        "matches the server-side price, so a client is able to submit any amount. The "
        "handler cannot detect this today because it trusts the posted body. "
        "Sessions are unable to survive a token refresh, which masks the problem in "
        "testing. " * 3 +
        "\n\n## Mitigations\n\n"
        "Recompute the total server-side and reject a mismatch. Pin the payment "
        "intent to the recomputed amount before confirming it with the provider. " * 3
    )
    assert len(render("security", review)) == 1


def test_a_document_that_notes_a_gap_in_passing_is_still_a_deliverable():
    """A real document is allowed to admit a limit without becoming a refusal — the
    difference is whether the limit is the SUBJECT or an aside."""
    doc = (
        "# Deployment Plan — Coffee Ordering App\n\n"
        "## Environments\n"
        + "Dev, staging and production each get their own resource group and their "
          "own key vault, with promotion gated on the smoke suite. " * 4 +
        "\n\n## Rollback\n"
        + "Swap the deployment slot back and re-point the traffic manager. I could "
          "not verify the database migration path, so that step is manual for now. " * 4
    )
    assert len(render("deployment", doc)) == 1


def test_a_refusal_announced_only_in_a_heading_is_still_caught():
    """The window has an edge, and this is what sits past it.

    Found by mutation: removing the heading check broke no test, because all three
    refusals observed live happened to say "I cannot" within the first 250 characters.
    That is a property of how much narration those particular turns produced before
    giving up — the Security agent ran fourteen tools first — not a property of
    refusals. One more tool call and the declaration lands past the window.

    So the heading is the second, independent signal: a section title that says the
    work did not happen, wherever in the reply it appears.
    """
    preamble = (
        "I'll perform a comprehensive review of the application. Let me start by "
        "running the scans and gathering what I need before I write anything. " * 12
    )
    assert len(preamble) > 700, "the point of this fixture is to overrun the window"
    body = (
        preamble
        + "\n\n## Cannot Proceed — Missing Prerequisites\n\n"
        + ("There is no repository workspace prepared, and no connector is bound to "
           "this project, so none of the scans above can run. " * 4)
        + "\n\n## Next Steps\n\nSelect a branch and bind a connector."
    )
    from agents_orchestrator.orchestrator2.deliverables import (
        _REFUSAL_OPENING_RE, _REFUSAL_WINDOW_CHARS,
    )
    assert not _REFUSAL_OPENING_RE.search(body[:_REFUSAL_WINDOW_CHARS]), (
        "this fixture must exercise the HEADING signal, not the opening one"
    )
    assert render("security", body) == []


def test_the_refusal_check_reads_the_opening_not_the_whole_document():
    """Pins WHY the check is anchored near the start.

    A long report that happens to contain "I cannot" in its ninth paragraph is a
    report. Scanning the whole body would reject it, and rejecting real documents is
    the failure this whole area keeps producing in one direction or the other.
    """
    from agents_orchestrator.orchestrator2.deliverables import looks_like_a_refusal
    body = (
        "# Test Plan\n\n## Cases\n"
        + ("Each case names its inputs and its expected result, and the suite runs "
           "against the seeded fixtures. " * 40)
        + "\n\n## Not covered\nI cannot cover the payment provider's sandbox here."
    )
    assert not looks_like_a_refusal(body)


# ── a message ABOUT a document is not the document ───────────────────────────
#
# FOUND BY RUNNING ALL NINE AGENTS AGAINST THE REAL STACK. Of the six deliverables
# captured, three were refusals (above) and the other three were the agent's chat
# summary of a document it had just SAVED TO DISK:
#
#     Requirements  "📄 Download Your PRD"   -> coffee_ordering_app_prd.docx
#     Project Mgr   "Plan Summary"           -> Coffee_Ordering_App_Delivery_Plan.pdf
#     Development   "Quick Explanation"      -> src/services/order_calculator.py
#
# So NOT ONE of the nine agents filed an actual document. The real PRD was a .docx the
# tab never showed, and what the tab did show was the chat blurb linking to it.
#
# "a deliverable is the document created — a proper document created and saved, for eg
#  design docs for design agent — not normal chats."
#
# The signal is unambiguous and cannot false-positive: the reply LINKS to the
# platform's own `/generated/...` artifact mount. A document does not link to itself.
# The file is surfaced by its own file-tree pointer (`pointers_for_run`), so nothing is
# lost by declining to file the announcement beside it — and the link stays in the
# chat, where a link belongs.

_PRD_ANNOUNCEMENT = (
    "I've generated the complete **Product Requirements Document (PRD)** for your "
    "coffee ordering web application.\n\n"
    "## 📄 Download Your PRD\n\n"
    "**[Download coffee_ordering_app_prd.docx](http://localhost:8004/generated/"
    "af57932d-f4f2-4673-aef7-d33b75c022f4/requirements_agent/2c85f3eb-cc34-4ae6-8193-"
    "93715b106e85/output/coffee_ordering_app_prd.docx)**\n\n---\n\n"
    "## Document Summary\n\nThe PRD includes:\n\n"
    "### Goals & Objectives\n"
    "- 5 business goals with measurable success metrics\n"
    "- 5 user goals focused on speed, convenience and transparency\n"
    "- 6 technical goals including performance, accessibility and compliance\n\n"
    "### User Personas\n"
    "- Busy Professional — values speed and saved favourites\n"
    "- Coffee Enthusiast — values customisation and exploration\n"
    "- Casual Customer — values simplicity and guest checkout\n"
)


def test_an_announcement_of_a_saved_document_is_not_a_deliverable():
    """Verbatim shape from the live run. The PRD is the .docx at the other end of
    that link; this is the message telling you where it went."""
    assert len(_PRD_ANNOUNCEMENT) > MIN_DELIVERABLE_CHARS
    assert len(_HEADING_LINE_RE.findall(_PRD_ANNOUNCEMENT)) >= 2
    assert render("requirements", _PRD_ANNOUNCEMENT) == []


def test_the_plan_agents_announcement_is_not_a_deliverable():
    body = (
        "I've created the **Coffee Ordering App Delivery Plan**. You can download it "
        "here:\n\n"
        "📄 **[Coffee_Ordering_App_Delivery_Plan.pdf](http://localhost:8004/generated/"
        "af57932d-f4f2-4673-aef7-d33b75c022f4/orchestrator/4d0fa0d6-d38d-4b84-a743-"
        "51f27cfdb415/output/Coffee_Ordering_App_Delivery_Plan.pdf)**\n\n---\n\n"
        "### Plan Summary\n\n"
        "| Metric | Value |\n|--------|-------|\n| Total Effort | 326 hours |\n"
        "| Duration | 5 sprints (10 weeks) |\n| Milestones | 7 |\n"
        "| Work Items | 37 |\n\n"
        "### Milestone Sequence\n\n"
        "Sprint 1 delivers the auth system and the catalog APIs; Sprint 2 the menu UI "
        "and the cart and order APIs; Sprint 3 the full ordering flow and payments.\n"
    )
    assert render("plan", body) == []


def test_a_document_that_merely_cites_a_source_url_is_still_a_deliverable():
    """The false positive to avoid: a real document is allowed to contain links. Only
    a link into the platform's OWN generated-artifact mount means "the document is
    over there"."""
    doc = (
        "# Security Review\n\n## Findings\n"
        + ("The dependency is pinned to a version with a published advisory, see "
           "https://nvd.nist.gov/vuln/detail/CVE-2024-0001 for the details. " * 6)
        + "\n\n## Recommendations\n"
        + ("Upgrade and re-run the scan against the release branch. " * 8)
    )
    assert len(render("security", doc)) == 1


def test_the_announcement_check_wants_the_generated_mount_not_any_path():
    """Pins the narrowness. A design document discussing a `/generated/` directory in
    prose is not announcing its own location."""
    from agents_orchestrator.orchestrator2.deliverables import announces_a_saved_file
    assert not announces_a_saved_file(
        "The build writes into /generated/ and the pipeline uploads it afterwards."
    )
    assert announces_a_saved_file(
        "[the doc](http://localhost:8004/generated/u1/orchestrator/r1/output/a.docx)"
    )
