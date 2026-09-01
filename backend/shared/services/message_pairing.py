"""Keep `tool_use` / `tool_result` paired in a checkpointed message history.

THE INVARIANT EVERY PROVIDER ENFORCES. An assistant message carrying `tool_calls` must
be followed by a `ToolMessage` answering each one. Anthropic returns 400 for a
`tool_use` with no matching `tool_result` AND for a `tool_result` with no preceding
`tool_use`; OpenAI rejects the second direction. Neither tolerates a history that
drifted.

HOW A CHECKPOINTED AGENT DRIFTS. The graph commits the agent node's `AIMessage` (with
its `tool_calls`) and the tools node's `ToolMessage`s in separate steps. Anything that
stops the graph between those two steps — a cancelled WS turn, a disconnect, a crash in
a tool — persists a checkpoint holding a `tool_call` that was never answered. The
damage is not to that turn, which the user already saw fail: it is that EVERY LATER
TURN on the same `session_id` replays the poisoned history and is rejected by the
provider, with nothing in the error pointing back at the interruption. The session is
permanently broken until someone starts a new one.

WHY THIS IS SHARED. Three agents arrived at their own version of this. The Design agent
hit the failure in production and wrote the repair for both directions; the Development
agent had only the orphan-`ToolMessage` half and needed a follow-up fix once its
in-flight guard began cancelling turns mid-graph; Requirements — equally checkpointed
and equally tool-bound — had no repair at all. This module is the one definition, so
the next agent inherits the complete rule instead of rediscovering half of it.

NO FABRICATED RESULTS. A dangling `tool_call` is stripped, never answered with a
synthetic `ToolMessage`. What the tool would have returned is unknown, and inventing it
would put a fact the system made up into the model's context.
"""
from __future__ import annotations

from langchain_core.messages import ToolMessage


def sanitize_tool_call_pairing(messages: list) -> list:
    """Return `messages` with unpaired tool calls and tool results removed.

    Two passes, because the two directions of drift are independent:

      - an `AIMessage.tool_calls` entry with no answering `ToolMessage` is dropped from
        that message;
      - a `ToolMessage` whose `tool_call_id` no longer matches a surviving tool call is
        dropped entirely.

    An `AIMessage` left with no tool calls keeps its text and is preserved; if it has no
    text either it is dropped, because an empty assistant turn is itself a 400.
    """
    # Pass 1: which tool_call_ids actually have a ToolMessage result?
    result_ids = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}

    sanitized: list = []
    answered_ids: set = set()
    for msg in messages:
        if getattr(msg, "tool_calls", None):
            kept = [tc for tc in msg.tool_calls if tc.get("id") in result_ids]
            if kept:
                if len(kept) != len(msg.tool_calls) and hasattr(msg, "model_copy"):
                    msg = msg.model_copy(update={"tool_calls": kept})
                answered_ids.update(tc["id"] for tc in kept)
                sanitized.append(msg)
            else:
                # No result for ANY of its tool calls. Keep the text and drop the
                # calls; drop the message outright if stripping them leaves it empty.
                content = getattr(msg, "content", "")
                if content:
                    if hasattr(msg, "model_copy"):
                        msg = msg.model_copy(update={"tool_calls": []})
                    sanitized.append(msg)
        elif isinstance(msg, ToolMessage):
            if msg.tool_call_id in answered_ids:
                sanitized.append(msg)
            # else: an orphan result — drop it silently, there is nothing to repair.
        else:
            sanitized.append(msg)
    return sanitized
