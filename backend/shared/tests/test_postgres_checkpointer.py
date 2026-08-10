"""LangGraph PostgresSaver checkpointer persistence and concurrency tests.

Verifies M2-03 (D-03): agent state survives connection pool close (session
persistence across restart) and that 10 concurrent sessions do not leak state
across thread IDs.

Both tests require a live Postgres instance with POSTGRES_SYNC_CONN_STRING set.
They skip cleanly when the variable is absent so unit CI is unaffected.
"""
import asyncio
import uuid
from typing import Annotated, TypedDict

import pytest

from config.env import POSTGRES_SYNC_CONN_STRING

_SKIP_NO_PG = pytest.mark.skipif(
    not POSTGRES_SYNC_CONN_STRING,
    reason="POSTGRES_SYNC_CONN_STRING not set — skipping Postgres checkpointer tests",
)


def _build_minimal_graph(checkpointer):
    """Build a two-node (agent -> END) LangGraph for testing state persistence."""
    from langgraph.graph import StateGraph, END, START
    from langgraph.graph.message import add_messages
    from langchain_core.messages import BaseMessage

    class _State(TypedDict):
        messages: Annotated[list[BaseMessage], add_messages]

    def _agent_node(state: _State) -> dict:
        # Echo the last message back — minimal behaviour for persistence testing
        return {"messages": state["messages"]}

    builder = StateGraph(_State)
    builder.add_node("agent", _agent_node)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    return builder.compile(checkpointer=checkpointer)


@pytest.mark.integration
@_SKIP_NO_PG
def test_postgres_checkpointer_persistence():
    """Session state survives closing and re-opening the PostgresSaver connection pool.

    Flow:
      1. Create PostgresSaver, build graph, invoke with a message.
      2. Close the connection (simulates process restart).
      3. Re-open PostgresSaver with the same connection string and thread_id.
      4. Assert the last message in the retrieved state matches what was sent.
    """
    import psycopg
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres import PostgresSaver
    from langchain_core.messages import HumanMessage

    thread_id = f"requirements:{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}
    sent_content = f"hello persistence test {uuid.uuid4()}"

    # --- Write ---
    conn1 = psycopg.connect(POSTGRES_SYNC_CONN_STRING, autocommit=True, row_factory=dict_row)
    cp1 = PostgresSaver(conn1)
    cp1.setup()
    graph1 = _build_minimal_graph(cp1)
    graph1.invoke({"messages": [HumanMessage(content=sent_content)]}, config=config)
    conn1.close()

    # --- Read back after pool close ---
    conn2 = psycopg.connect(POSTGRES_SYNC_CONN_STRING, autocommit=True, row_factory=dict_row)
    cp2 = PostgresSaver(conn2)
    graph2 = _build_minimal_graph(cp2)
    state = graph2.get_state(config)
    conn2.close()

    assert state is not None, "get_state returned None — checkpoint not persisted"
    messages = state.values.get("messages", [])
    assert len(messages) > 0, "No messages found in retrieved state"
    last_content = messages[-1].content if hasattr(messages[-1], "content") else str(messages[-1])
    assert sent_content in last_content, (
        f"State content mismatch: sent '{sent_content}', "
        f"got '{last_content}'"
    )


@pytest.mark.integration
@_SKIP_NO_PG
def test_concurrent_checkpointer_sessions():
    """10 concurrent sessions must not leak state across thread IDs.

    Each asyncio task invokes the graph 3 times with unique content tagged by
    task index. After all tasks complete, each session's state must contain only
    its own messages (no cross-contamination).
    """
    import psycopg
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres import PostgresSaver
    from langchain_core.messages import HumanMessage

    NUM_TASKS = 10
    MESSAGES_PER_TASK = 3

    def _run_session(task_index: int) -> tuple[str, list]:
        """Run one session synchronously (called from asyncio.to_thread)."""
        thread_id = f"requirements:{uuid.uuid4()}"
        config = {"configurable": {"thread_id": thread_id}}

        conn = psycopg.connect(POSTGRES_SYNC_CONN_STRING, autocommit=True, row_factory=dict_row)
        cp = PostgresSaver(conn)
        cp.setup()
        graph = _build_minimal_graph(cp)

        for i in range(MESSAGES_PER_TASK):
            content = f"task-{task_index}-msg-{i}"
            graph.invoke({"messages": [HumanMessage(content=content)]}, config=config)

        state = graph.get_state(config)
        conn.close()
        messages = state.values.get("messages", []) if state else []
        return thread_id, [
            (m.content if hasattr(m, "content") else str(m)) for m in messages
        ]

    async def _run_all():
        tasks = [asyncio.to_thread(_run_session, i) for i in range(NUM_TASKS)]
        return await asyncio.gather(*tasks)

    results = asyncio.run(_run_all())

    assert len(results) == NUM_TASKS, f"Expected {NUM_TASKS} results, got {len(results)}"

    for task_idx, (thread_id, messages) in enumerate(results):
        # Each session should have received MESSAGES_PER_TASK messages
        assert len(messages) == MESSAGES_PER_TASK, (
            f"Task {task_idx} (thread={thread_id}): "
            f"expected {MESSAGES_PER_TASK} messages, got {len(messages)}: {messages}"
        )
        # Each message must belong to this task (no cross-session content)
        for msg in messages:
            assert f"task-{task_idx}-" in msg, (
                f"Task {task_idx}: found foreign message '{msg}' — cross-session leakage detected"
            )
