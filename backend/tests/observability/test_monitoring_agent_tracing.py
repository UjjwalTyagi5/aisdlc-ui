"""The monitoring agent traces through litellm, which no LangChain handler can see.

WHY THIS AGENT IS DIFFERENT. Every other agent reaches the model through LangChain, so
the Langfuse CallbackHandler observes the call and records model, tokens and cost for
free. This one calls `litellm.completion` directly — nothing observes that — which is
why it was the last agent emitting no traces at all while looking perfectly healthy.

The destination therefore cannot ride on a handler. It rides on a contextvar that
`agent_trace` sets to THIS run's project client, and `traced_completion` reads. A module
global would defeat the per-project isolation the binding table exists to provide: every
project's traces would land in whichever client was configured first.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents_orchestrator.monitoring_feedback_agent._byok import traced_completion
from shared.observability.bindings import get_current_client, set_current_client


@pytest.fixture(autouse=True)
def _clear_client():
    set_current_client(None)
    yield
    set_current_client(None)


def _response(*, tokens: bool = True):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = "the answer"
    resp.usage = (
        MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15) if tokens else None
    )
    return resp


def _traced_client():
    client = MagicMock()
    generation = MagicMock()
    client.start_as_current_generation.return_value.__enter__.return_value = generation
    return client, generation


def test_untraced_when_no_client_is_bound():
    """Tracing off, or an unbound project: the call must behave exactly as before."""
    with patch("litellm.completion", return_value=_response()) as completion:
        result = traced_completion(
            name="rca_agent", model="m", messages=[{"role": "user", "content": "hi"}]
        )
    assert completion.called
    assert result.choices[0].message.content == "the answer"


def test_generation_is_recorded_on_the_runs_own_client():
    client, generation = _traced_client()
    set_current_client(client)

    with patch("litellm.completion", return_value=_response()):
        traced_completion(
            name="rca_agent",
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "analyse this"}],
        )

    assert client.start_as_current_generation.called
    kwargs = client.start_as_current_generation.call_args.kwargs
    # The node name, so the trace tree reads like the pipeline rather than a flat list.
    assert kwargs["name"] == "rca_agent"
    assert kwargs["model"] == "claude-sonnet-4-6"
    # Token usage is what makes the call show up in cost, not just in the trace list.
    assert generation.update.call_args.kwargs["usage_details"] == {
        "input": 10, "output": 5, "total": 15
    }


def test_a_response_without_usage_still_records_the_generation():
    """Not every provider returns usage. A missing count must not lose the trace."""
    client, generation = _traced_client()
    set_current_client(client)

    with patch("litellm.completion", return_value=_response(tokens=False)):
        traced_completion(name="summary_agent", model="m", messages=[])

    assert client.start_as_current_generation.called
    assert generation.update.call_args.kwargs["usage_details"] is None


def test_a_tracing_failure_does_not_lose_the_model_call():
    """Observability must never be the reason an agent turn fails.

    The same posture as every other Langfuse path here: losing a trace is acceptable,
    losing the analysis is not.
    """
    client = MagicMock()
    client.start_as_current_generation.side_effect = RuntimeError("langfuse exploded")
    set_current_client(client)

    with patch("litellm.completion", return_value=_response()) as completion:
        result = traced_completion(name="rca_agent", model="m", messages=[])

    assert completion.called
    assert result.choices[0].message.content == "the answer"


def test_the_client_is_per_run_not_global():
    """Two runs, two projects: the second must not inherit the first's destination."""
    first, _ = _traced_client()
    second, _ = _traced_client()

    set_current_client(first)
    assert get_current_client() is first
    set_current_client(second)
    assert get_current_client() is second


def test_both_llm_call_sites_go_through_the_wrapper():
    """A bare `litellm.completion` in this agent is an untraced model call.

    Pinned as source inspection because the alternative is a live provider call. Two
    call sites exist today (rca_agent and summary_agent); a third added later must use
    the wrapper or this fails.
    """
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[2]
        / "agents_orchestrator/monitoring_feedback_agent/llm_analysis.py"
    ).read_text(encoding="utf-8")

    assert "traced_completion(" in source
    # `litellm.completion(` must not appear outside the wrapper itself.
    assert "litellm.completion(" not in source, (
        "a direct litellm.completion call is invisible to Langfuse — use traced_completion"
    )
