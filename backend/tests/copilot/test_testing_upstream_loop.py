"""Regression test for the testing agent's entry node needing BOTH a sync and
async execution path (C1).

Two callers drive the SAME compiled `testing_agent.app`/`graph_builder`:
  - The standalone testing agent invokes the graph SYNCHRONOUSLY
    (`run_super_agent` / `run_super_agent_async`'s
    `loop.run_in_executor(None, lambda: ctx.run(app.invoke, initial_state))`).
    LangGraph's sync `.invoke()` cannot execute an async-only node — it raises
    `TypeError: No synchronous function provided to "pull_upstream_context"`
    the instant the entry node has no `func`.
  - The Copilot drives the graph via `await graph.ainvoke(...)` on the main
    loop. `pull_upstream_context` awaits `fetch_session_artifacts`, which
    touches the shared asyncpg pool bound to whatever loop is driving it.
    Wrapping it in a sync-only node (the old `make_sync_wrapper`, which spins
    a brand-new loop via `asyncio.run` in a worker thread) raised
    `RuntimeError: got Future attached to a different loop` under `ainvoke`.

The fix: register the node as a `RunnableCallable(sync_wrapper, async_fn, ...)`
so `.invoke()` uses the sync wrapper and `.ainvoke()` uses the native coroutine
directly on the driving loop — both paths work, neither crashes.

Kept deterministic (no live DB required): asserts on the compiled graph's node
registration + a synchronous smoke invoke, rather than exercising a real
asyncpg pool.
"""
import asyncio

import pytest

from langgraph.checkpoint.memory import MemorySaver

import agents_orchestrator.testing_agent.agents.testing_agent as testing_agent_mod
from agents_orchestrator.testing_agent.Nodes.ingest_input import pull_upstream_context


def test_pull_upstream_context_is_still_a_coroutine_function():
    """The underlying node function itself remains a real coroutine function —
    only the GRAPH REGISTRATION wraps it with a sync alternative alongside it."""
    assert asyncio.iscoroutinefunction(pull_upstream_context)


def test_pull_upstream_context_registered_with_both_sync_and_async_paths():
    """Structural check on the compiled graph: the node's RunnableCallable must
    carry BOTH a sync `.func` (so `.invoke()` — the standalone path — doesn't
    raise `TypeError: No synchronous function provided`) AND an async `.afunc`
    that IS the native `pull_upstream_context` coroutine (so `.ainvoke()` — the
    Copilot path — awaits it directly on the driving loop instead of crossing
    loops via a worker-thread `asyncio.run`)."""
    node = testing_agent_mod.graph_builder.nodes["pull_upstream_context"]
    runnable = node.runnable

    assert runnable.func is not None, (
        "pull_upstream_context has no sync `func` — LangGraph's sync `.invoke()` "
        "(the standalone testing agent's entrypoint) will raise "
        "'No synchronous function provided' the moment it hits this node."
    )
    assert runnable.afunc is not None, (
        "pull_upstream_context has no async `afunc` — reintroduces the cross-loop "
        "asyncpg crash under the Copilot's `await graph.ainvoke(...)`."
    )
    assert asyncio.iscoroutinefunction(runnable.afunc)
    assert runnable.afunc is pull_upstream_context


def test_pull_upstream_context_sync_wrapper_alias_exists():
    """`pull_upstream_context_sync` is the sync wrapper registered as `func` —
    it must exist (module-level) so `.invoke()` has something to call."""
    assert hasattr(testing_agent_mod, "pull_upstream_context_sync")
    assert not asyncio.iscoroutinefunction(testing_agent_mod.pull_upstream_context_sync)


# THE NODE, NOT THE WHOLE GRAPH — and that is the fix, not a shortcut.
#
# Both tests below used to compile the graph and invoke it end to end, on the stated
# assumption that with no session_id `pull_upstream_context` early-returns and nothing
# expensive happens. True of that node, but the graph does not stop there: it runs on
# to `classify_intent`, which calls a real model. So a test named
# "does_not_cross_loops" made a live Anthropic request and failed with
# `AuthenticationError: API key is invalid` — reporting a loop-crossing regression
# that had not happened, on a machine whose ANTHROPIC_API_KEY was simply stale.
#
# The regression these guard is the node's dual sync/async REGISTRATION: `.invoke()`
# must find a sync `func` and `.ainvoke()` must await the native coroutine on the
# driving loop. Driving the node's own runnable exercises exactly those two paths, on
# a real running loop for the async one, with no model and no database in the way.


@pytest.mark.asyncio
async def test_node_ainvoke_on_driving_loop_does_not_cross_loops():
    """The Copilot path: `await ...ainvoke(...)` on the current running loop.

    A cross-loop failure here is the `RuntimeError: got Future attached to a
    different loop` the dual registration exists to prevent — it would surface as an
    exception out of this await, so the assertion is that one does not.
    """
    node = testing_agent_mod.graph_builder.nodes["pull_upstream_context"].runnable
    result = await node.ainvoke({"user_prompt": "hello", "tenant_id": "t"})
    assert result is not None


def test_node_invoke_sync_on_standalone_path_does_not_raise_typeerror():
    """The standalone path: LangGraph's sync `.invoke()`.

    Without a sync `func` registered alongside the coroutine this raises
    `TypeError: No synchronous function provided to "pull_upstream_context"` — the
    exact regression C1 fixed.
    """
    node = testing_agent_mod.graph_builder.nodes["pull_upstream_context"].runnable
    result = node.invoke({"user_prompt": "hello", "tenant_id": "t"})
    assert result is not None
