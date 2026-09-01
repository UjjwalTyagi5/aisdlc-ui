"""The shared tool_use/tool_result repair, and that both agents actually use it.

WHY THIS MATTERS MORE THAN IT LOOKS. The damage from an unpaired tool call is not to
the turn that broke — the user already saw that fail. It is that the broken history is
CHECKPOINTED, so every later turn on the same session_id replays it and is rejected by
the provider, with nothing in the error mentioning the original interruption. The
session is dead until somebody starts a new one, and nobody knows why.

Three agents each grew their own version of this. Design wrote the complete
both-directions repair after hitting the failure in its chat; Development shipped only
the orphan-ToolMessage half and needed a follow-up once its in-flight guard started
cancelling turns mid-graph; Requirements — equally checkpointed, equally tool-bound —
had none. `test_both_agents_repair_the_history_before_replaying_it` is what stops that
drifting apart again.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def _ai(content="", calls=()):
    return AIMessage(
        content=content,
        tool_calls=[
            {"id": cid, "name": "some_tool", "args": {}} for cid in calls
        ],
    )


def _ids(messages):
    out = []
    for m in messages:
        if getattr(m, "tool_calls", None):
            out.extend(tc["id"] for tc in m.tool_calls)
    return out


# ── a well-formed history is left alone ──────────────────────────────────────


@pytest.mark.unit
def test_a_paired_history_passes_through_unchanged():
    from shared.services.message_pairing import sanitize_tool_call_pairing

    msgs = [
        HumanMessage(content="list the stories"),
        _ai(calls=["call-1"]),
        ToolMessage(content="ok", tool_call_id="call-1"),
        AIMessage(content="here they are"),
    ]
    assert sanitize_tool_call_pairing(msgs) == msgs


@pytest.mark.unit
def test_messages_with_no_tool_traffic_are_untouched():
    from shared.services.message_pairing import sanitize_tool_call_pairing

    msgs = [HumanMessage(content="hello"), AIMessage(content="hi")]
    assert sanitize_tool_call_pairing(msgs) == msgs


# ── direction 1: a tool call nobody answered ─────────────────────────────────


@pytest.mark.unit
def test_a_dangling_tool_call_is_stripped():
    """The cancelled-mid-graph case: the AIMessage was checkpointed, the ToolMessage
    never was."""
    from shared.services.message_pairing import sanitize_tool_call_pairing

    out = sanitize_tool_call_pairing([
        HumanMessage(content="create the epic"),
        _ai(content="Creating it now.", calls=["never-answered"]),
    ])
    assert _ids(out) == []
    # The assistant's text survives — only the unanswered call is removed.
    assert out[-1].content == "Creating it now."


@pytest.mark.unit
def test_an_answered_call_survives_alongside_a_dangling_one():
    """A single AIMessage can carry several calls and be interrupted partway."""
    from shared.services.message_pairing import sanitize_tool_call_pairing

    out = sanitize_tool_call_pairing([
        _ai(content="working", calls=["answered", "dangling"]),
        ToolMessage(content="done", tool_call_id="answered"),
    ])
    assert _ids(out) == ["answered"]
    assert any(isinstance(m, ToolMessage) for m in out)


@pytest.mark.unit
def test_an_assistant_turn_left_empty_is_dropped_entirely():
    """Stripping the calls off a message with no text leaves an empty assistant turn,
    which is itself a 400 — so the message goes rather than the calls alone."""
    from shared.services.message_pairing import sanitize_tool_call_pairing

    out = sanitize_tool_call_pairing([
        HumanMessage(content="go"),
        _ai(content="", calls=["never-answered"]),
    ])
    assert len(out) == 1
    assert isinstance(out[0], HumanMessage)


@pytest.mark.unit
def test_no_fabricated_tool_result_is_invented():
    """Answering a dangling call with a synthetic ToolMessage would put a fact the
    system made up into the model's context. The call is removed instead."""
    from shared.services.message_pairing import sanitize_tool_call_pairing

    out = sanitize_tool_call_pairing([_ai(content="x", calls=["never-answered"])])
    assert not any(isinstance(m, ToolMessage) for m in out)


# ── direction 2: a tool result with nothing to answer ────────────────────────


@pytest.mark.unit
def test_an_orphan_tool_message_is_dropped():
    from shared.services.message_pairing import sanitize_tool_call_pairing

    out = sanitize_tool_call_pairing([
        HumanMessage(content="go"),
        ToolMessage(content="stale", tool_call_id="no-such-call"),
    ])
    assert not any(isinstance(m, ToolMessage) for m in out)


@pytest.mark.unit
def test_a_result_that_precedes_its_own_call_is_dropped():
    """ORDER IS PART OF THE INVARIANT, not just presence. A `tool_result` before its
    `tool_use` is rejected exactly as an unpaired one is, so the second pass admits a
    ToolMessage only once the call it answers has already been seen.

    Note what this rules out as a test: a ToolMessage can never have its call stripped
    by pass 1, because pass 1 derives the surviving calls FROM the ToolMessages. The
    only way a result is left pointing at nothing is this ordering case."""
    from shared.services.message_pairing import sanitize_tool_call_pairing

    out = sanitize_tool_call_pairing([
        ToolMessage(content="result", tool_call_id="call-1"),   # before its call
        _ai(content="calling", calls=["call-1"]),
    ])
    assert not any(isinstance(m, ToolMessage) for m in out)
    # The call itself is kept — the repair drops the misplaced result, not the turn.
    assert _ids(out) == ["call-1"]


@pytest.mark.unit
def test_the_result_is_always_a_replayable_history():
    """The property that actually matters, asserted directly: after the repair, every
    surviving tool call has a result and every surviving result has a call."""
    from shared.services.message_pairing import sanitize_tool_call_pairing

    out = sanitize_tool_call_pairing([
        HumanMessage(content="go"),
        _ai(content="a", calls=["ok", "dangling"]),
        ToolMessage(content="r", tool_call_id="ok"),
        ToolMessage(content="orphan", tool_call_id="unknown"),
        _ai(content="b", calls=["also-dangling"]),
    ])
    call_ids = set(_ids(out))
    result_ids = {m.tool_call_id for m in out if isinstance(m, ToolMessage)}
    assert call_ids == result_ids


# ── both agents are wired to it ──────────────────────────────────────────────


@pytest.mark.unit
def test_both_agents_repair_the_history_before_replaying_it():
    """Requirements had no repair at all before this, which is the drift these tests
    exist to prevent recurring."""
    import inspect

    from agents_orchestrator.design_architecture_agent.agents import architecture
    from agents_orchestrator.requirements_agent.agents import planning

    for mod in (planning, architecture):
        src = inspect.getsource(mod)
        assert "sanitize_tool_call_pairing" in src, mod.__name__


@pytest.mark.unit
def test_design_delegates_rather_than_keeping_its_own_copy():
    """Design's `_sanitize_messages` is kept as the name its graph calls, but it must
    not carry a second implementation that can drift from the shared one."""
    from shared.services.message_pairing import sanitize_tool_call_pairing
    from agents_orchestrator.design_architecture_agent.agents import architecture

    msgs = [_ai(content="x", calls=["dangling"])]
    assert architecture._sanitize_messages(msgs) == sanitize_tool_call_pairing(msgs)
