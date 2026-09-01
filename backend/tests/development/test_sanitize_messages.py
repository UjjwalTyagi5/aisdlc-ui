"""Tests for dev_agent._sanitize_messages -- the message-history repair that
runs on every agent_node call before the LLM is invoked.

Task 4 review (development-agent-chat-overhaul) flagged an Important finding
(3.1): _sanitize_messages only repaired the orphan-ToolMessage direction (a
ToolMessage with no matching AIMessage tool_call). It did not repair the
reverse: an AIMessage whose tool_calls include an id with no ToolMessage
response at all -- exactly what a mid-graph task.cancel() (task 4's own
in-flight-guard change) can leave behind when a WS turn is cancelled between
the agent node committing its AIMessage and the tools node running. Left
unrepaired, the next turn on that session_id would send a
"tool_use with no tool_result" history to the provider and get rejected --
a confusing new failure with no obvious link back to the earlier cancellation.

This file covers both directions so a future change to either can't silently
regress the other.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agents_orchestrator.development_agent.agents.dev_agent import _sanitize_messages


def _tool_call(call_id: str, name: str = "read_file", args: dict | None = None) -> dict:
    return {"name": name, "args": args or {}, "id": call_id, "type": "tool_call"}


class TestOrphanToolMessage:
    """Pre-existing direction: a ToolMessage with no matching AIMessage tool_call."""

    def test_orphan_tool_message_is_dropped(self):
        messages = [
            HumanMessage(content="hi"),
            ToolMessage(content="stray result", tool_call_id="does-not-exist"),
        ]
        out = _sanitize_messages(messages)
        assert len(out) == 1
        assert isinstance(out[0], HumanMessage)

    def test_matched_tool_message_is_kept(self):
        ai = AIMessage(content="", tool_calls=[_tool_call("call-1")])
        tm = ToolMessage(content="file contents", tool_call_id="call-1", name="read_file")
        out = _sanitize_messages([HumanMessage(content="hi"), ai, tm])
        assert out == [HumanMessage(content="hi"), ai, tm]


class TestDanglingToolCall:
    """New direction (review finding 3.1): an AIMessage tool_call with no
    ToolMessage response anywhere in the history -- the shape a cancelled
    mid-graph WS turn leaves behind."""

    def test_dangling_tool_call_is_stripped_from_ai_message(self):
        ai = AIMessage(content="", tool_calls=[_tool_call("call-cancelled")])
        messages = [HumanMessage(content="do something"), ai]

        out = _sanitize_messages(messages)

        assert len(out) == 2  # the AIMessage is repaired, not dropped
        repaired = out[1]
        assert isinstance(repaired, AIMessage)
        assert repaired.tool_calls == []
        assert out[0] is messages[0]

    def test_partial_dangling_keeps_the_answered_call_only(self):
        ai = AIMessage(
            content="",
            tool_calls=[_tool_call("call-answered"), _tool_call("call-dangling")],
        )
        tm = ToolMessage(content="ok", tool_call_id="call-answered", name="read_file")

        out = _sanitize_messages([HumanMessage(content="go"), ai, tm])

        repaired_ai = out[1]
        assert [tc["id"] for tc in repaired_ai.tool_calls] == ["call-answered"]
        assert out[2] is tm

    def test_dangling_tool_call_also_stripped_from_additional_kwargs(self):
        """Some conversion paths (langchain_core.messages.utils.convert_to_openai_messages)
        read additional_kwargs['tool_calls'] as a fallback/alongside the structured
        field -- a message that only cleared .tool_calls but left the raw
        additional_kwargs copy in place would still leak the dangling call back to
        the provider on the very next turn."""
        ai = AIMessage(
            content="",
            tool_calls=[_tool_call("call-cancelled")],
            additional_kwargs={
                "tool_calls": [
                    {
                        "id": "call-cancelled",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ]
            },
        )

        out = _sanitize_messages([HumanMessage(content="go"), ai])

        repaired = out[1]
        assert repaired.tool_calls == []
        assert repaired.additional_kwargs.get("tool_calls") == []

    def test_untouched_when_every_tool_call_is_answered(self):
        """No dangling calls at all -- the message must come back as the exact
        same object, not merely an equivalent rebuild, so a fully-healthy
        history isn't needlessly reconstructed on every single agent turn."""
        ai = AIMessage(content="", tool_calls=[_tool_call("call-1"), _tool_call("call-2")])
        tm1 = ToolMessage(content="a", tool_call_id="call-1", name="read_file")
        tm2 = ToolMessage(content="b", tool_call_id="call-2", name="read_file")
        messages = [HumanMessage(content="go"), ai, tm1, tm2]

        out = _sanitize_messages(messages)

        assert out[1] is ai

    def test_dangling_ai_message_mid_history_does_not_affect_a_later_healthy_one(self):
        """Mirrors the real shape after a cancel-then-retry: an earlier AIMessage
        left a dangling tool_call from the cancelled turn, but a later AIMessage
        in the same checkpointed history (the next, successful turn) has its own
        tool_call properly answered -- the repair must be scoped per-message,
        not global."""
        cancelled_ai = AIMessage(content="", tool_calls=[_tool_call("call-cancelled")])
        human = HumanMessage(content="try again")
        healthy_ai = AIMessage(content="", tool_calls=[_tool_call("call-2")])
        tm2 = ToolMessage(content="ok", tool_call_id="call-2", name="read_file")

        out = _sanitize_messages([cancelled_ai, human, healthy_ai, tm2])

        assert out[0].tool_calls == []
        assert out[2] is healthy_ai
        assert out[3] is tm2
