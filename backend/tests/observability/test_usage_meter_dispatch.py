"""The usage meter has to record from the thread LangChain actually calls it on.

`on_llm_end` is a sync callback. For an async run — which is every agent on this
platform — LangChain runs sync callbacks inside `run_in_executor`
(langchain_core/callbacks/manager.py:375-387), i.e. on a worker thread with no running
event loop. The handler did `asyncio.get_running_loop().create_task(...)` and swallowed
the resulting `RuntimeError`, so it recorded NOTHING, every time, silently: on
2026-09-14 `usage_monthly` and `agent_call_logs` were empty for the whole tenant while
traces, replies and token counts all looked healthy.

These tests drive the handler the way LangChain does rather than the way it is
convenient to call it — from an executor thread, from a bare thread, and from inside a
loop — because calling it directly from a test coroutine is precisely the shape that
kept passing while production recorded nothing.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from shared.observability import usage_meter as um
from shared.observability.usage_meter import UsageMeterCallbackHandler


class _Result:
    """A non-streaming LLMResult, as langchain_litellm's `_create_chat_result` builds it."""

    def __init__(self, model="azure/gpt-5-mini", prompt=8252, completion=345):
        self.llm_output = {
            "token_usage": {"prompt_tokens": prompt, "completion_tokens": completion},
            "model": model,
        }
        self.generations = []


@pytest.fixture
def captured(monkeypatch):
    """Capture what reaches the durable rollup, without touching Redis or the DB."""
    seen: list[tuple] = []

    async def _rollup(tenant_id, project_id, cost, tokens):
        seen.append((tenant_id, project_id, cost, tokens))

    async def _record_usage(*a, **k):
        return None

    monkeypatch.setattr(
        "shared.services.budget_store.record_usage_rollup", _rollup, raising=False
    )
    monkeypatch.setattr(
        "shared.services.model_rate_limit.record_usage", _record_usage, raising=False
    )
    return seen


@pytest.mark.unit
@pytest.mark.asyncio
async def test_records_from_an_executor_thread(captured):
    """THE REGRESSION. This is exactly how LangChain invokes it on every agent run."""
    handler = UsageMeterCallbackHandler("tenant-1", "offering-1", "project-1")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, handler.on_llm_end, _Result())

    assert len(captured) == 1, "spend was dropped on the path every agent uses"
    tenant_id, project_id, cost, tokens = captured[0]
    assert tenant_id == "tenant-1"
    assert project_id == "project-1"
    assert tokens == 8252 + 345
    assert cost == pytest.approx(0.002753)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_records_when_already_inside_a_loop(captured):
    """`run_inline` handlers, and anything calling the hook from a coroutine."""
    handler = UsageMeterCallbackHandler("tenant-1", "offering-1", "project-1")

    handler.on_llm_end(_Result())
    # Scheduled as a task rather than awaited; let it run.
    for _ in range(10):
        await asyncio.sleep(0)
        if captured:
            break

    assert len(captured) == 1


@pytest.mark.unit
def test_records_on_a_fully_synchronous_run(captured):
    """`planning_app.invoke(...)` — requirements_agent_api.py:608 still does this."""
    handler = UsageMeterCallbackHandler("tenant-1", "offering-1", "project-1")

    handler.on_llm_end(_Result())

    assert len(captured) == 1


@pytest.mark.unit
def test_a_closed_loop_falls_back_rather_than_losing_the_row(captured):
    """A run whose loop has gone away still gets its spend recorded.

    The captured loop is the fast path, not the only path: if it has closed — a request
    that finished, a worker shutting down — the rollup is driven synchronously on this
    thread instead. Spend is the one thing here that must not be best-effort.
    """
    handler = UsageMeterCallbackHandler("tenant-1", "offering-1", "project-1")
    closed = asyncio.new_event_loop()
    closed.close()
    handler._loop = closed

    done = threading.Event()

    def _call():
        handler.on_llm_end(_Result())
        done.set()

    t = threading.Thread(target=_call)
    t.start()
    t.join(timeout=20)

    assert done.is_set()
    assert len(captured) == 1


class _Message:
    def __init__(self, input_tokens, output_tokens):
        self.usage_metadata = {
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }


class _Generation:
    def __init__(self, message):
        self.message = message


class _StreamingResult:
    """What a `streaming=True` run delivers: usage on the message, EMPTY llm_output.

    `_combine_llm_outputs` returns `{}` (chat_models.py:653) and langchain_litellm does
    not override it, so the model name from `_create_chat_result` never survives.
    """

    def __init__(self, prompt=8252, completion=345):
        self.llm_output = {}
        self.generations = [[_Generation(_Message(prompt, completion))]]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_streaming_run_is_costed_not_just_counted(captured, monkeypatch):
    """Tokens alone are not enough: an unnamed model prices to $0.00.

    The design agent streams, so before this its rows carried real tokens and no money.
    """
    class _Resolved:
        model = "azure/gpt-5-mini"

    monkeypatch.setattr(
        "shared.services.model_resolver.get_resolved_model", lambda: _Resolved(),
        raising=False,
    )
    handler = UsageMeterCallbackHandler("tenant-1", "offering-1", "project-1")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, handler.on_llm_end, _StreamingResult())

    assert len(captured) == 1
    _, _, cost, tokens = captured[0]
    assert tokens == 8252 + 345
    assert cost == pytest.approx(0.002753), "streaming spend recorded as $0"


@pytest.mark.unit
def test_response_model_wins_over_the_contextvar(monkeypatch):
    """The response produced these tokens; the contextvar is only the fallback."""
    class _Resolved:
        model = "claude-sonnet-4-6"

    monkeypatch.setattr(
        "shared.services.model_resolver.get_resolved_model", lambda: _Resolved(),
        raising=False,
    )

    model, _, _ = UsageMeterCallbackHandler._extract(_Result(model="azure/gpt-5-mini"))

    assert model == "azure/gpt-5-mini"


@pytest.mark.unit
def test_there_is_no_aon_llm_end():
    """LangChain resolves the hook by name; `aon_llm_end` was never called by anything.

    Keeping it around is worse than not having it: it made the async path look handled.
    """
    assert not hasattr(UsageMeterCallbackHandler, "aon_llm_end")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scheduled_tasks_are_strongly_referenced(captured):
    """asyncio holds only a weak reference; an unreferenced task can vanish mid-flight."""
    handler = UsageMeterCallbackHandler("tenant-1", "offering-1", "project-1")

    handler.on_llm_end(_Result())

    assert um._PENDING, "the rollup task is collectable"
    for _ in range(10):
        await asyncio.sleep(0)
        if captured:
            break
    assert len(captured) == 1
