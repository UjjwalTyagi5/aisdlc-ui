"""The reviewer graph must not end a turn with the review left as chat prose.

Live failure this pins (2026-09-03, azure/gpt-5-mini): the agent produced a complete,
accurate review — summary, findings with file/line, merge recommendation — entirely as
markdown in the chat, called no tool, and the Summary/Findings tabs stayed empty. Asked
in chat to "SAVE IT", it produced a SECOND markdown review. The persisted artifact is
the only thing those tabs render, so an unsubmitted review did not happen.
"""
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END

from agents_orchestrator.code_review_agent.agents.reviewer import (
    _SUBMIT_NUDGE,
    _already_nudged,
    _has_submitted,
    _is_nudge_turn,
    route_fn,
)

_SUBMIT_CALL = {"name": "submit_code_review", "args": {"review_json": "{}"}, "id": "1"}


def _prose_review() -> AIMessage:
    return AIMessage(content="## Summary\nThe change swaps yellow for purple.\n- F-001 ...")


def test_a_prose_review_routes_to_the_nudge_instead_of_ending():
    state = {"messages": [HumanMessage(content="review it"), _prose_review()]}
    assert route_fn(state) == "finalize"


def test_a_tool_call_still_routes_to_tools():
    state = {"messages": [HumanMessage(content="review it"),
                          AIMessage(content="", tool_calls=[_SUBMIT_CALL])]}
    assert route_fn(state) == "tools"


def test_the_turn_ends_once_the_review_was_actually_submitted():
    state = {"messages": [
        HumanMessage(content="review it"),
        AIMessage(content="", tool_calls=[_SUBMIT_CALL]),
        ToolMessage(content="Review submitted", tool_call_id="1"),
        AIMessage(content="Done — 3 findings."),
    ]}
    assert _has_submitted(state) is True
    assert route_fn(state) == END


def test_the_nudge_is_spent_once_per_turn_not_once_per_session():
    """A model that will not submit even when nudged must end, not loop forever."""
    state = {"messages": [
        HumanMessage(content="review it"),
        _prose_review(),
        HumanMessage(content=_SUBMIT_NUDGE),
        _prose_review(),
    ]}
    assert _already_nudged(state) is True
    assert route_fn(state) == END


def test_a_later_review_in_the_same_chat_gets_its_own_nudge():
    """The regression the whole-transcript scan caused: one submission (or one nudge)
    anywhere in a long session used to disable the guarantee for every later review."""
    state = {"messages": [
        # Turn 1 — asked, nudged, submitted.
        HumanMessage(content="review it"),
        _prose_review(),
        HumanMessage(content=_SUBMIT_NUDGE),
        AIMessage(content="", tool_calls=[_SUBMIT_CALL]),
        ToolMessage(content="Review submitted", tool_call_id="1"),
        # Turn 2 — a fresh request that has drifted back to prose.
        HumanMessage(content="review the new commit"),
        _prose_review(),
    ]}
    assert _has_submitted(state) is False, "turn 1's submission leaked into turn 2"
    assert _already_nudged(state) is False, "turn 1's nudge leaked into turn 2"
    assert route_fn(state) == "finalize"


def test_the_nudge_turn_is_detected_so_the_call_can_be_forced():
    """agent_node binds tool_choice on exactly this turn — prose persuasion had already
    failed against the model that produced the live bug."""
    nudged = {"messages": [HumanMessage(content="review it"), _prose_review(),
                           HumanMessage(content=_SUBMIT_NUDGE)]}
    assert _is_nudge_turn(nudged) is True

    ordinary = {"messages": [HumanMessage(content="review it")]}
    assert _is_nudge_turn(ordinary) is False


def test_a_request_about_the_report_is_not_nudged_into_a_new_review():
    """Live: "Send the review report for approval." was answered, then the nudge forced
    submit_code_review and another review was filed."""
    state = {"messages": [
        HumanMessage(content="Send the review report for approval."),
        AIMessage(content="", tool_calls=[{"name": "raise_document_for_approval", "args": {"filename": "r.docx"}, "id": "9"}]),
        ToolMessage(content="Error: your role on this project cannot raise documents for approval", tool_call_id="9"),
        AIMessage(content="Your role cannot raise it; a Project Admin can."),
    ]}
    assert route_fn(state) == END

    greeting = {"messages": [HumanMessage(content="Hi"), AIMessage(content="Hello! Select a target to review.")]}
    assert route_fn(greeting) == END


def test_a_turn_that_started_the_review_workflow_is_still_nudged():
    state = {"messages": [
        HumanMessage(content="Please look at the branch and tell me what you think"),
        AIMessage(content="", tool_calls=[{"name": "run_security_review", "args": {}, "id": "5"}]),
        ToolMessage(content="Security review: 13 vulnerabilities", tool_call_id="5"),
        _prose_review(),
    ]}
    assert route_fn(state) == "finalize"


def test_the_pages_own_review_requests_count_as_review_turns():
    for asked in ("Please review the whole branch, run the security review, and submit your findings.",
                  "Review the prepared change and submit your findings.", "can you re-review this PR?"):
        assert route_fn({"messages": [HumanMessage(content=asked), _prose_review()]}) == "finalize", asked
