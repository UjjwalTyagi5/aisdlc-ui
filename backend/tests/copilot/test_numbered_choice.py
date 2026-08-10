"""Unit tests for the generic numbered-choice detector that turns an agent's
plain-text "1. … 2. … reply with the number" question into a clickable card."""
from agents_orchestrator.orchestrator.copilot_api import _parse_numbered_options


def test_detects_consecutive_numbered_question():
    reply = (
        "Here are the available projects:\n"
        "1. Project 2\n"
        "2. Carelon\n"
        "Which project would you like to work in? Reply with the number or name!"
    )
    opts = _parse_numbered_options(reply)
    assert opts == [("1", "Project 2"), ("2", "Carelon")]


def test_strips_markdown_and_handles_paren_style():
    reply = "Pick one?\n1) **Unit tests**\n2) *Functional*\n3) API"
    assert _parse_numbered_options(reply) == [
        ("1", "Unit tests"), ("2", "Functional"), ("3", "API")
    ]


def test_no_question_mark_no_card():
    assert _parse_numbered_options("1. foo\n2. bar\nHere is a list.") == []


def test_single_option_ignored():
    # A single-item confirm ("Should I clone? 1. Carelon") is not a multi-choice.
    assert _parse_numbered_options("Should I clone this repo?\n1. Carelon") == []


def test_non_consecutive_or_report_findings_ignored():
    # A report's numbered findings that don't start at 1/aren't consecutive must not fire.
    reply = "Findings?\n2. High severity\n5. Low severity"
    assert _parse_numbered_options(reply) == []


def test_confirmation_question_after_numbered_list_does_not_fire():
    # Enumerated acceptance-criteria scenarios followed by a yes/no confirm must NOT
    # become a forced pick-a-scenario card (no selection cue → no card).
    reply = (
        "1. Scenario 1 — Exam Not Completed\n"
        "2. Scenario 2a — Exam Completed\n"
        "3. Scenario 2b — Unknown\n"
        "4. Scenario 2c — Other\n"
        "Does this look correct? Any changes needed before I check for gaps?"
    )
    assert _parse_numbered_options(reply) == []


def test_numbered_list_without_selection_cue_does_not_fire():
    assert _parse_numbered_options("Here is the plan.\n1. Step one\n2. Step two\nOk?") == []


def test_empty_reply():
    assert _parse_numbered_options("") == []
    assert _parse_numbered_options("Just a sentence with no options?") == []
