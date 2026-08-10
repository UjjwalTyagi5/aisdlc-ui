"""REQ-M10-02: ClarificationRequest/Answer round-trip + checkpoint-resume proof.

Two guarantees locked in here, both fully autonomous (no Postgres / Temporal /
LLM — live worker-restart durability is deferred to DLT-10):

1. JSON round-trip losslessness (Task 1) — ClarificationRequest and
   ClarificationAnswer survive model_dump_json -> model_validate_json AND
   model_dump -> model_validate (the dict path Temporal's DataConverter
   exercises across the workflow<->activity boundary) with byte-for-byte
   field identity, including unicode/multi-line question text. The
   ClarificationAnswer.answer max_length=4096 bound is enforced.

2. In-memory checkpointer resume-from-pre-question-state (Task 2) — a
   minimal LangGraph StateGraph compiled with MemorySaver, invoked under a
   thread_id, then re-invoked with the SAME thread_id and only the answer
   HumanMessage, resumes from the prior checkpoint (pre-question messages
   are still present) with the answer appended last. A different thread_id
   does not see the pipeline thread's messages (RESEARCH Pitfall 7 / A3
   namespace isolation).
"""
from __future__ import annotations

from typing import Annotated, TypedDict

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import ValidationError

from shared.models.workflow_models import ClarificationAnswer, ClarificationRequest


# ---------------------------------------------------------------------------
# Task 1: JSON / dict round-trip losslessness
# ---------------------------------------------------------------------------


def _sample_request(**overrides) -> ClarificationRequest:
    fields = dict(
        questions=["What is the scope of the rate-limiting feature?"],
        thread_id="run-1234",
        agent_type="requirements",
        phase="requirements",
        clarification_id="11111111-1111-1111-1111-111111111111",
    )
    fields.update(overrides)
    return ClarificationRequest(**fields)


def _sample_answer(**overrides) -> ClarificationAnswer:
    fields = dict(
        clarification_id="11111111-1111-1111-1111-111111111111",
        answer="Apply rate limiting only to the public API.",
        actor_id="user-42",
        idempotency_key="22222222-2222-2222-2222-222222222222",
    )
    fields.update(overrides)
    return ClarificationAnswer(**fields)


def test_clarification_request_json_roundtrip():
    original = _sample_request()

    restored = ClarificationRequest.model_validate_json(original.model_dump_json())

    assert restored == original
    assert restored.clarification_id == original.clarification_id
    assert restored.questions == original.questions
    assert restored.thread_id == original.thread_id
    assert restored.agent_type == original.agent_type
    assert restored.phase == original.phase


def test_clarification_answer_json_roundtrip():
    original = _sample_answer()

    restored = ClarificationAnswer.model_validate_json(original.model_dump_json())

    assert restored == original
    assert restored.clarification_id == original.clarification_id
    assert restored.answer == original.answer
    assert restored.actor_id == original.actor_id
    assert restored.idempotency_key == original.idempotency_key


def test_clarification_request_unicode_multiline_question_roundtrip():
    tricky_question = (
        "Should the “clarification” step support multi-line\n"
        "questions, e.g.:\n"
        "1. What about émigré data?\n"
        "2. Is 日本語 (Japanese) input allowed???\n"
        "   - and trailing whitespace?   "
    )
    original = _sample_request(questions=[tricky_question])

    restored_json = ClarificationRequest.model_validate_json(original.model_dump_json())
    restored_dict = ClarificationRequest.model_validate(original.model_dump())

    assert restored_json == original
    assert restored_dict == original
    assert restored_json.questions[0] == tricky_question
    assert restored_dict.questions[0] == tricky_question


def test_clarification_request_dict_roundtrip_dataconverter_path():
    original = _sample_request()

    restored = ClarificationRequest.model_validate(original.model_dump())

    assert restored == original


def test_clarification_answer_dict_roundtrip_dataconverter_path():
    original = _sample_answer()

    restored = ClarificationAnswer.model_validate(original.model_dump())

    assert restored == original


def test_clarification_answer_max_length_bound():
    # Exactly 4096 chars is valid.
    at_limit = _sample_answer(answer="x" * 4096)
    assert len(at_limit.answer) == 4096

    restored = ClarificationAnswer.model_validate_json(at_limit.model_dump_json())
    assert restored.answer == at_limit.answer

    # One char over the limit raises ValidationError.
    with pytest.raises(ValidationError):
        _sample_answer(answer="x" * 4097)


# ---------------------------------------------------------------------------
# Task 2: In-memory checkpointer resume-from-pre-question-state
# ---------------------------------------------------------------------------


class _ChatState(TypedDict):
    messages: Annotated[list, add_messages]


def _noop_node(state: _ChatState) -> dict:
    """Trivial node that simply passes the state through unchanged."""
    return {"messages": []}


def _build_graph():
    builder = StateGraph(_ChatState)
    builder.add_node("noop", _noop_node)
    builder.add_edge(START, "noop")
    builder.add_edge("noop", END)
    return builder.compile(checkpointer=MemorySaver())


@pytest.mark.asyncio
async def test_checkpoint_resume_from_pre_question_state():
    graph = _build_graph()
    pipeline_config = {"configurable": {"thread_id": "pipeline-run-1"}}

    # First turn: user starts, agent ends with a clarification question.
    await graph.ainvoke(
        {
            "messages": [
                HumanMessage(content="start"),
                AIMessage(content="What is the scope?"),
            ]
        },
        config=pipeline_config,
    )

    # Resume turn: re-invoke with ONLY the answer, same thread_id.
    resumed_state = await graph.ainvoke(
        {"messages": [HumanMessage(content="the scope is X")]},
        config=pipeline_config,
    )

    contents = [m.content for m in resumed_state["messages"]]

    # Pre-question messages are still present (resume from checkpoint).
    assert "start" in contents
    assert "What is the scope?" in contents

    # The answer was appended as a new HumanMessage and is last.
    assert contents[-1] == "the scope is X"
    last_message = resumed_state["messages"][-1]
    assert isinstance(last_message, HumanMessage)


@pytest.mark.asyncio
async def test_checkpoint_resume_thread_id_namespace_isolation():
    graph = _build_graph()
    pipeline_config = {"configurable": {"thread_id": "pipeline-run-1"}}
    chat_config = {"configurable": {"thread_id": "chat:session-xyz"}}

    # Seed the pipeline thread with its own conversation.
    await graph.ainvoke(
        {
            "messages": [
                HumanMessage(content="start"),
                AIMessage(content="What is the scope?"),
            ]
        },
        config=pipeline_config,
    )

    # A different thread_id (chat session) sees only its own message.
    chat_state = await graph.ainvoke(
        {"messages": [HumanMessage(content="hello from chat")]},
        config=chat_config,
    )

    chat_contents = [m.content for m in chat_state["messages"]]

    assert chat_contents == ["hello from chat"]
    assert "start" not in chat_contents
    assert "What is the scope?" not in chat_contents
