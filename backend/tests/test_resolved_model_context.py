"""Proves whether a ResolvedModel stashed via set_resolved_model() in an async
context is readable from inside a function executed via loop.run_in_executor.

This is the load-bearing finding for the BYOK tool-helper cutover (tasks 7b/7c):
those helpers run inside run_in_executor and must be able to read
get_resolved_model(). This test establishes the EXACT pattern they must use.
"""
import asyncio
import contextvars

import pytest

from shared.services.model_resolver import (
    ResolvedModel,
    set_resolved_model,
    get_resolved_model,
)


def _make_rm() -> ResolvedModel:
    return ResolvedModel(
        provider="anthropic",
        litellm_provider="anthropic",
        model="claude-sonnet-4-6",
        api_key="sk-x",
        base_url=None,
        alias="tenant:t:p",
    )


@pytest.mark.asyncio
async def test_resolved_model_visible_in_executor_plain():
    """Default run_in_executor(None, fn) — does a plain ContextVar propagate?

    Python's default executor does NOT copy the calling context into the worker
    thread. The contextvar set in the async task is therefore NOT visible inside
    the plain executor call: get_resolved_model() returns None there.
    """
    set_resolved_model(_make_rm())

    def _in_executor():
        got = get_resolved_model()
        return None if got is None else got.model

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _in_executor)
    # Plain executor does NOT inherit context -> None.
    assert result is None


@pytest.mark.asyncio
async def test_resolved_model_visible_in_executor_copy_context():
    """copy_context().run(fn) snapshots the current context and runs fn inside it
    in the worker thread. THIS is the pattern the helper cutover must use."""
    set_resolved_model(_make_rm())

    def _in_executor():
        got = get_resolved_model()
        return None if got is None else got.model

    loop = asyncio.get_event_loop()
    ctx = contextvars.copy_context()
    result = await loop.run_in_executor(None, ctx.run, _in_executor)
    assert result == "claude-sonnet-4-6"
