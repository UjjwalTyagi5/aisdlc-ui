"""A Redis outage must not stop the durable spend rollup.

`_record` writes two things: the per-offering Redis counter (hot, for rate limiting)
and the `usage_monthly` rollup (durable, what the budget guard blocks on and the Cost
page reports). The Redis write came first with no guard of its own, so when it raised
-- which it does whenever Redis is unreachable, e.g. an expired Entra token -- the whole
of `_record` unwound into the caller's blanket except and the rollup never ran.

The comment above the rollup claimed it "ALWAYS runs (independent of the per-offering
meter)". These tests make that true.
"""
from __future__ import annotations

import pytest

from shared.observability.usage_meter import UsageMeterCallbackHandler


class _Response:
    """Minimal LangChain-ish response carrying token usage."""

    llm_output = {
        "model_name": "claude-sonnet-4-6",
        "token_usage": {"input_tokens": 100, "output_tokens": 40},
    }
    generations: list = []


@pytest.mark.unit
async def test_rollup_still_records_when_the_redis_meter_raises(monkeypatch):
    recorded: list[tuple] = []

    async def _boom(*a, **kw):
        raise ConnectionError("invalid username-password pair")

    async def _rollup(tenant_id, project_id, cost, tokens):
        recorded.append((tenant_id, project_id, cost, tokens))

    monkeypatch.setattr(
        "shared.services.model_rate_limit.record_usage", _boom, raising=False
    )
    monkeypatch.setattr(
        "shared.services.budget_store.record_usage_rollup", _rollup, raising=False
    )

    h = UsageMeterCallbackHandler(
        tenant_id="t-1", offering_id="off-1", project_id="p-1"
    )
    await h._record(_Response())

    assert recorded, "Redis failing must not cost us the durable rollup"
    tenant, project, _cost, tokens = recorded[0]
    assert tenant == "t-1"
    assert project == "p-1"
    assert tokens == 140


@pytest.mark.unit
async def test_rollup_records_normally_when_redis_is_healthy(monkeypatch):
    calls: dict[str, bool] = {}

    async def _ok(*a, **kw):
        calls["redis"] = True

    async def _rollup(*a, **kw):
        calls["rollup"] = True

    monkeypatch.setattr(
        "shared.services.model_rate_limit.record_usage", _ok, raising=False
    )
    monkeypatch.setattr(
        "shared.services.budget_store.record_usage_rollup", _rollup, raising=False
    )

    h = UsageMeterCallbackHandler(tenant_id="t-1", offering_id="off-1")
    await h._record(_Response())

    assert calls == {"redis": True, "rollup": True}


@pytest.mark.unit
async def test_no_tokens_records_nothing(monkeypatch):
    """Guard the early return -- a completion with no usage is not spend."""
    called = False

    async def _rollup(*a, **kw):
        nonlocal called
        called = True

    monkeypatch.setattr(
        "shared.services.budget_store.record_usage_rollup", _rollup, raising=False
    )

    class _Empty:
        llm_output: dict = {}
        generations: list = []

    await UsageMeterCallbackHandler(tenant_id="t-1")._record(_Empty())
    assert called is False
