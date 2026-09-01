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

The first fix round (commit 2f38f01f) added that repair but introduced a new
bug of its own (re-review finding N1): emptying tool_calls to `[]` still left
`additional_kwargs["tool_calls"] = []`, and langchain_litellm's
`_convert_message_to_dict` does `elif "tool_calls" in message.additional_kwargs`
-- an `in` check, not a truthiness check -- so the empty array still went on
the wire, which OpenAI's schema rejects (min length 1) just as hard as a
dangling entry. This file's `TestDanglingToolCall` tests assert the corrected
behaviour: the additional_kwargs key must be DELETED (not emptied) when no
tool_calls survive, and a message with neither surviving tool_calls nor any
content must be dropped from the history entirely (mirroring the precedent in
design_architecture_agent/agents/architecture.py's own _sanitize_messages).
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_litellm.chat_models.litellm import _convert_message_to_dict

from agents_orchestrator.development_agent.agents.dev_agent import _sanitize_messages


def _tool_call(call_id: str, name: str = "read_file", args: dict | None = None) -> dict:
    return {"name": name, "args": args or {}, "id": call_id, "type": "tool_call"}


def _raw_tool_call_kwargs(call_id: str, name: str = "read_file") -> dict:
    """The raw provider-format mirror streaming ChatLiteLLM populates in
    additional_kwargs alongside the structured .tool_calls field."""
    return {
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}
        ]
    }


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

    def test_dangling_tool_call_is_stripped_but_message_kept_when_content_present(self):
        ai = AIMessage(
            content="Let me check that file for you.",
            tool_calls=[_tool_call("call-cancelled")],
        )
        messages = [HumanMessage(content="do something"), ai]

        out = _sanitize_messages(messages)

        assert len(out) == 2  # content survives -> message repaired, not dropped
        repaired = out[1]
        assert isinstance(repaired, AIMessage)
        assert repaired.tool_calls == []
        assert repaired.content == "Let me check that file for you."
        assert out[0] is messages[0]

    def test_dangling_tool_call_with_no_content_drops_the_message_entirely(self):
        """Re-review's explicit new case: nothing survives the strip (no
        answered tool_calls, no text either -- cancelled before the model
        produced any). A content-less, tool-call-less assistant turn is
        dropped outright rather than left in the history."""
        ai = AIMessage(content="", tool_calls=[_tool_call("call-cancelled")])
        messages = [HumanMessage(content="do something"), ai]

        out = _sanitize_messages(messages)

        assert len(out) == 1
        assert isinstance(out[0], HumanMessage)
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

    def test_dangling_tool_call_stripped_from_additional_kwargs_key_is_deleted_not_emptied(self):
        """N1 regression: additional_kwargs['tool_calls'] must be DELETED, not
        set to [], when no tool_calls survive. langchain_litellm's
        _convert_message_to_dict checks `"tool_calls" in message.additional_kwargs`
        (membership, not truthiness) -- an empty list still trips that fallback
        and puts an empty tool_calls array on the wire, which OpenAI's schema
        rejects (min length 1) just as hard as the original dangling entry."""
        ai = AIMessage(
            content="Let me check that file for you.",
            tool_calls=[_tool_call("call-cancelled")],
            additional_kwargs=_raw_tool_call_kwargs("call-cancelled"),
        )

        out = _sanitize_messages([HumanMessage(content="go"), ai])

        repaired = out[1]
        assert repaired.tool_calls == []
        assert "tool_calls" not in repaired.additional_kwargs

    def test_dangling_tool_call_repair_produces_a_wire_payload_openai_accepts(self):
        """End-to-end regression for N1, reproduced through the real conversion
        function dev_agent's ChatLiteLLM actually uses (not
        convert_to_openai_messages, which isn't on this call path): the
        repaired message's wire dict must carry no 'tool_calls' key at all,
        not an empty array -- OpenAI's schema requires tool_calls to have at
        least one element when the key is present."""
        ai = AIMessage(
            content="Let me check that file for you.",
            tool_calls=[_tool_call("call-cancelled")],
            additional_kwargs=_raw_tool_call_kwargs("call-cancelled"),
        )

        out = _sanitize_messages([HumanMessage(content="go"), ai])
        wire = _convert_message_to_dict(out[1])

        assert "tool_calls" not in wire
        assert wire["content"] == "Let me check that file for you."

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
        cancelled_ai = AIMessage(
            content="On it, pulling that file now.",
            tool_calls=[_tool_call("call-cancelled")],
        )
        human = HumanMessage(content="try again")
        healthy_ai = AIMessage(content="", tool_calls=[_tool_call("call-2")])
        tm2 = ToolMessage(content="ok", tool_call_id="call-2", name="read_file")

        out = _sanitize_messages([cancelled_ai, human, healthy_ai, tm2])

        assert out[0].tool_calls == []
        assert out[0].content == "On it, pulling that file now."
        assert out[2] is healthy_ai
        assert out[3] is tm2
