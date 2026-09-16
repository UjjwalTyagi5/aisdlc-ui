"""Release only the model's ANSWERS from a LangGraph `stream_mode="messages"` stream.

THE CHAT WAS SHOWING THE AGENT'S WORKING. The stream yields every message a turn produces
as it is produced, and the chat handlers forwarded any text they saw. A model that writes
a sentence before calling a tool — "The document has not been uploaded yet, I'll read it
first", "Let me run the security review" — had that sentence sent to the reader as the
start of its reply, followed by the real answer once the tools came back. A text chunk
cannot say whether its message will end in a tool call; only the whole message can.

So each AI message is collected until it is complete — a different message id arrives, a
non-AI message (a tool result) arrives, or the stream ends — and its text is released only
if the message called no tool. Tool results, the reader's own messages and anything a
graph injects as a HumanMessage are never released.

The cost is honest: an answer appears when its message is complete, not word by word.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

_AI_TYPES = ("ai", "AIMessageChunk")


def _has_tool_calls(msg: Any) -> bool:
    return bool(
        getattr(msg, "tool_calls", None)
        or getattr(msg, "tool_call_chunks", None)
        or getattr(msg, "invalid_tool_calls", None)
    )


class AnswerStream:
    """Feed it each streamed message; it returns the answer text that is ready to send."""

    def __init__(self, extract_text: Callable[[Any], str]):
        self._extract = extract_text
        self._id: Optional[str] = None
        self._text = ""
        self._calls_tools = False
        self._open = False

    def _flush(self) -> str:
        released = "" if self._calls_tools else self._text
        self._id, self._text, self._calls_tools, self._open = None, "", False, False
        return released

    def feed(self, msg: Any) -> str:
        """Take one streamed message. Returns text released by a message it completed."""
        if getattr(msg, "type", None) not in _AI_TYPES:
            # A tool result (or an injected prompt) means the AI message before it is done.
            return self._flush() if self._open else ""
        released = ""
        msg_id = getattr(msg, "id", None)
        if self._open and msg_id != self._id:
            released = self._flush()
        self._open, self._id = True, msg_id
        if _has_tool_calls(msg):
            self._calls_tools = True
        content = getattr(msg, "content", None)
        if content:
            self._text += self._extract(content) or ""
        return released

    def close(self) -> str:
        """The stream ended: release the last message if it was an answer."""
        return self._flush() if self._open else ""
