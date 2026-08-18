"""ModelCallWrapper (task #20 remainder) — retry/backoff, timeout, no-training extras,
and the per-call cost cap. Pure unit tests: no DB, no network — a fake chat model
stands in for the LangChain client."""
from __future__ import annotations

import asyncio

import pytest

from shared.services.model_call_wrapper import (
    _MAX_ATTEMPTS,
    estimate_input_tokens,
    guarded_completion,
    no_training_kwargs,
)
from shared.services.model_rate_limit import ModelCostLimitError
from shared.services.model_resolver import ResolvedModel


def _resolved(**overrides) -> ResolvedModel:
    base = dict(
        provider="anthropic", litellm_provider="anthropic", model="claude-sonnet-4-6",
        api_key="sk-test", base_url=None, alias="tenant:t:p",
    )
    base.update(overrides)
    return ResolvedModel(**base)


class _RateLimitError(Exception):
    def __init__(self, msg="rate limited"):
        super().__init__(msg)
        self.status_code = 429


class _FakeChatModel:
    """ainvoke fails `fail_times` times (raising `exc_factory()` each time) then succeeds."""

    def __init__(self, fail_times: int, exc_factory=_RateLimitError):
        self.fail_times = fail_times
        self.exc_factory = exc_factory
        self.calls = 0

    async def ainvoke(self, messages, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc_factory()
        return {"messages": messages, "kwargs": kwargs}


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Retries in these tests must not actually wait 2s/4s — patch the module's sleep."""
    slept: list[float] = []

    async def _fast_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("shared.services.model_call_wrapper.asyncio.sleep", _fast_sleep)
    return slept


@pytest.mark.asyncio
async def test_succeeds_without_retry_when_first_call_works():
    model = _FakeChatModel(fail_times=0)
    result = await guarded_completion(_resolved(), model, ["hi"])
    assert model.calls == 1
    assert result["messages"] == ["hi"]


@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds(_no_real_sleep):
    model = _FakeChatModel(fail_times=1, exc_factory=_RateLimitError)
    result = await guarded_completion(_resolved(), model, ["hi"], tenant_id="t1")
    assert model.calls == 2
    assert result["messages"] == ["hi"]
    assert _no_real_sleep == [2.0]  # first retry backs off 2s


@pytest.mark.asyncio
async def test_retries_on_timeout_then_succeeds(_no_real_sleep):
    model = _FakeChatModel(fail_times=1, exc_factory=asyncio.TimeoutError)
    result = await guarded_completion(_resolved(), model, ["hi"])
    assert model.calls == 2
    assert result is not None


@pytest.mark.asyncio
async def test_raises_after_exhausting_all_retries(_no_real_sleep):
    model = _FakeChatModel(fail_times=99, exc_factory=_RateLimitError)
    with pytest.raises(_RateLimitError):
        await guarded_completion(_resolved(), model, ["hi"])
    assert model.calls == _MAX_ATTEMPTS == 3
    assert _no_real_sleep == [2.0, 4.0]  # exact schedule the task named


@pytest.mark.asyncio
async def test_non_retryable_exception_raises_immediately(_no_real_sleep):
    model = _FakeChatModel(fail_times=99, exc_factory=ValueError)
    with pytest.raises(ValueError):
        await guarded_completion(_resolved(), model, ["hi"])
    assert model.calls == 1
    assert _no_real_sleep == []


@pytest.mark.asyncio
async def test_extra_kwargs_from_resolved_model_reach_ainvoke():
    resolved = _resolved(extra_kwargs={"metadata": {"no_training": True}})
    model = _FakeChatModel(fail_times=0)
    result = await guarded_completion(resolved, model, ["hi"])
    assert result["kwargs"] == {"metadata": {"no_training": True}}


@pytest.mark.asyncio
async def test_per_call_cost_cap_blocks_before_any_network_call():
    # A long prompt against a tiny per-call cap and real pricing must raise BEFORE
    # ainvoke is ever called.
    resolved = _resolved(max_cost_per_call_usd=0.0001, input_price_per_million=15.0)
    model = _FakeChatModel(fail_times=0)
    long_prompt = "x" * 100_000
    with pytest.raises(ModelCostLimitError):
        await guarded_completion(resolved, model, long_prompt)
    assert model.calls == 0


@pytest.mark.asyncio
async def test_per_call_cost_cap_allows_a_cheap_call():
    resolved = _resolved(max_cost_per_call_usd=10.0, input_price_per_million=3.0)
    model = _FakeChatModel(fail_times=0)
    result = await guarded_completion(resolved, model, "short prompt")
    assert model.calls == 1
    assert result is not None


@pytest.mark.asyncio
async def test_no_cap_configured_never_blocks():
    resolved = _resolved(max_cost_per_call_usd=None, input_price_per_million=999.0)
    model = _FakeChatModel(fail_times=0)
    result = await guarded_completion(resolved, model, "x" * 1_000_000)
    assert result is not None


def test_estimate_input_tokens_is_conservative_and_nonzero():
    assert estimate_input_tokens("") == 0
    assert estimate_input_tokens("a") >= 1
    assert estimate_input_tokens("x" * 1000) > estimate_input_tokens("x" * 10)


def test_no_training_kwargs_known_providers_are_empty_documented_noop():
    assert no_training_kwargs("anthropic") == {}
    assert no_training_kwargs("openai") == {}
    assert no_training_kwargs("google") == {}


def test_no_training_kwargs_unknown_provider_is_empty_not_guessed():
    assert no_training_kwargs("some-new-provider") == {}
