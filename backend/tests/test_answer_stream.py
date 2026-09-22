"""The chat shows the agent's answers, never its working.

Live: the Code Review chat mixed the agent's step-by-step text into its reply, and the
Requirements agent, given an attached document, first said the document "has not been
uploaded" and only then answered. Both were text from a message that went on to call a
tool.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from shared.services.answer_stream import AnswerStream

pytestmark = pytest.mark.unit


def _run(messages) -> str:
    stream = AnswerStream(lambda c: c if isinstance(c, str) else "")
    out = "".join(stream.feed(m) for m in messages)
    return out + stream.close()


def test_text_before_a_tool_call_is_not_sent():
    assert _run([
        AIMessageChunk(content="The document has not been uploaded yet, ", id="m1"),
        AIMessageChunk(content="I'll read it first.", id="m1"),
        AIMessageChunk(content="", id="m1", tool_call_chunks=[{"name": "read_document", "args": "{}", "id": "c1", "index": 0}]),
        ToolMessage(content="# BRD ...", tool_call_id="c1"),
        AIMessageChunk(content="Here is the BRD summary.", id="m2"),
    ]) == "Here is the BRD summary."


def test_a_streamed_answer_is_sent_whole():
    assert _run([AIMessageChunk(content="Review ", id="m1"), AIMessageChunk(content="submitted.", id="m1")]) == "Review submitted."


def test_messages_without_ids_are_separated_by_the_tool_results_between_them():
    assert _run([
        AIMessageChunk(content="Let me run the security review."),
        AIMessageChunk(content="", tool_call_chunks=[{"name": "run_security_review", "args": "{}", "id": "c1", "index": 0}]),
        ToolMessage(content="13 vulnerabilities", tool_call_id="c1"),
        AIMessageChunk(content="Done: 13 vulnerabilities."),
    ]) == "Done: 13 vulnerabilities."


def test_a_complete_message_with_tool_calls_is_withheld_and_one_without_is_sent():
    assert _run([
        AIMessage(content="Reading src/index.js", id="a", tool_calls=[{"name": "read_repo_file", "args": {"path": "src/index.js"}, "id": "t1"}]),
        ToolMessage(content="...", tool_call_id="t1"),
        AIMessage(content="Two findings.", id="b"),
    ]) == "Two findings."


def test_injected_prompts_and_tool_results_are_never_sent():
    assert _run([
        HumanMessage(content="You wrote the review as prose instead of submitting it."),
        ToolMessage(content="Error: file not found", tool_call_id="x"),
    ]) == ""


def test_two_answers_in_one_turn_are_both_sent_in_order():
    assert _run([AIMessageChunk(content="First.", id="a"), AIMessageChunk(content=" Second.", id="b")]) == "First. Second."
