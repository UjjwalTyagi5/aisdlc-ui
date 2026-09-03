"""The scanner graph must not let a prose-only "review" end the turn silently.

Same guard, and same reason, as the Code Review agent's (see
tests/test_code_review_submit_enforcement.py): the Summary/Findings tabs read only the
submit_security_review artifact, and it additionally carries the mandatory
PASS/FAIL/CONDITIONAL verdict that gates deployment. A scan the model narrates in chat
persists nothing and records no verdict.
"""
from langchain_core.messages import AIMessage, HumanMessage

from agents_orchestrator.security_agent.agents.scanner import (
    _SUBMIT_NUDGE,
    _has_submitted,
    route_fn,
)


def _ai_with_tool_call(name: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": {}, "id": "call-1"}])


def test_a_pending_tool_call_still_routes_to_tools():
    state = {"messages": [HumanMessage(content="scan it"), _ai_with_tool_call("scan_secrets")]}
    assert route_fn(state) == "tools"


def test_a_prose_review_with_nothing_submitted_is_nudged():
    state = {
        "messages": [
            HumanMessage(content="Please run the security scan and submit your review."),
            AIMessage(content="Summary: no critical issues found. Verdict: PASS"),
        ]
    }
    assert route_fn(state) == "finalize"


def test_the_nudge_is_asked_only_once():
    state = {
        "messages": [
            HumanMessage(content="scan it"),
            AIMessage(content="prose review"),
            HumanMessage(content=_SUBMIT_NUDGE),
            AIMessage(content="here it is as text again"),
        ]
    }
    assert route_fn(state) == "__end__"


def test_a_submitted_review_ends_cleanly():
    state = {
        "messages": [
            HumanMessage(content="scan it"),
            _ai_with_tool_call("submit_security_review"),
            AIMessage(content="Submitted."),
        ]
    }
    assert _has_submitted(state) is True
    assert route_fn(state) == "__end__"


def test_scanning_is_not_submitting():
    """Running Trivy/Semgrep/Gitleaks is not a verdict — only the artifact call counts."""
    state = {
        "messages": [
            HumanMessage(content="scan it"),
            _ai_with_tool_call("scan_dependencies"),
            _ai_with_tool_call("scan_code"),
            _ai_with_tool_call("generate_sbom"),
            AIMessage(content="prose summary of the scan"),
        ]
    }
    assert _has_submitted(state) is False
    assert route_fn(state) == "finalize"
