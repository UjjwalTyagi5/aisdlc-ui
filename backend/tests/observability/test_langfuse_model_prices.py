"""Langfuse cannot cost a trace for a model it has no price for.

A design-agent turn on 2026-09-14 recorded `model=azure/gpt-5-mini`, 8,252 in / 345 out,
`modelId: None`, `calculatedTotalCost: 0` — real spend, shown as $0.00, because Langfuse
prices at ingestion from its own `models` table and that table only ships a few hundred
well-known names. Nothing this platform sends can carry a cost instead: `langchain_litellm`
drops LiteLLM's `response_cost` from `llm_output`, and Langfuse's LangChain handler never
passes `cost_details` on a success path.

These cover the part that decides whether a price lands on the right call: the match
pattern, and the per-token conversion. The write itself needs the Langfuse database.
"""
from __future__ import annotations

import re
from decimal import Decimal

import pytest

from shared.observability.model_prices import (
    _bare,
    _match_pattern,
    _per_token_prices,
    schedule_model_price_sync,
)


@pytest.mark.unit
def test_pattern_matches_the_string_the_trace_actually_recorded():
    """The live failure: the offering is `azure/gpt-5-mini` and so is the trace."""
    assert re.match(_match_pattern("azure/gpt-5-mini"), "azure/gpt-5-mini")


@pytest.mark.unit
def test_pattern_matches_the_bare_name_too():
    """Direct-SDK callers report the model without its provider prefix."""
    assert re.match(_match_pattern("azure/gpt-5-mini"), "gpt-5-mini")


@pytest.mark.unit
def test_pattern_does_not_match_another_providers_copy():
    """An Azure offering's rate must not be applied to an OpenAI call.

    This is why the pattern is an alternation and not a `(?:[^/]+/)?` wildcard prefix.
    """
    assert not re.match(_match_pattern("azure/gpt-5-mini"), "openai/gpt-5-mini")


@pytest.mark.unit
@pytest.mark.parametrize("other", ["gpt-5-mini-x", "xgpt-5-mini", "gpt-5", ""])
def test_pattern_is_anchored(other):
    assert not re.match(_match_pattern("azure/gpt-5-mini") + r"\Z", other)


@pytest.mark.unit
def test_model_ids_are_regex_escaped():
    """`gpt-4.1`'s dot would otherwise match any character and misprice `gpt-4o1`."""
    pattern = _match_pattern("gpt-4.1")

    assert re.match(pattern, "gpt-4.1")
    assert not re.match(pattern, "gpt-4o1")


@pytest.mark.unit
def test_bare_strips_only_the_provider_prefix():
    assert _bare("azure/gpt-5-mini") == "gpt-5-mini"
    assert _bare("gpt-5-mini") == "gpt-5-mini"


@pytest.mark.unit
def test_prices_are_per_token_not_per_million():
    """Langfuse stores USD per token; everything else on this platform is per million.

    Getting this backwards would overstate every trace by a factor of a million, which
    is the kind of wrong that reads as a product outage on the Cost page.
    """
    in_price, out_price = _per_token_prices("azure/gpt-5-mini")

    assert in_price == Decimal("0.00000025")
    assert out_price == Decimal("0.000002")


@pytest.mark.unit
def test_prices_come_from_the_same_source_as_the_budget_ledger():
    """The Traces page and Cost & Budgets must never quote different money.

    `_per_token_prices` goes through `shared.cost.pricing`, so the curated Anthropic
    rates still override LiteLLM here exactly as they do there.
    """
    from shared.cost.pricing import _MODEL_PRICES

    in_price, _ = _per_token_prices("claude-sonnet-4-6")

    assert in_price == _MODEL_PRICES["claude-sonnet-4-6"]["input_per_mtok"] / 1_000_000


@pytest.mark.unit
def test_unpriceable_model_yields_nothing_rather_than_a_zero_price():
    """A zero row would look like a priced model that genuinely costs nothing."""
    assert _per_token_prices("not-a-real-model-xyz") is None


@pytest.mark.unit
def test_scheduling_without_a_project_is_a_no_op():
    """Standalone/chat turns have no project, so there is no binding to teach."""
    schedule_model_price_sync(tenant_id="t", project_id=None, model="azure/gpt-5-mini")


@pytest.mark.unit
def test_scheduling_outside_an_event_loop_does_not_raise():
    """Model resolution runs from sync contexts too; a missing loop must not fail a run."""
    schedule_model_price_sync(tenant_id="t", project_id="p", model="azure/gpt-5-mini")
