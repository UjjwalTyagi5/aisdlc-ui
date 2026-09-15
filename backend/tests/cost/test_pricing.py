"""Unit tests for shared/cost/pricing.py compute_cost_usd (REQ-M9-06)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from shared.cost.pricing import _MODEL_PRICES, compute_cost_usd


@pytest.mark.unit
def test_compute_cost_usd_known_model():
    prices = _MODEL_PRICES["claude-sonnet-4-6"]
    expected = (
        (Decimal(1000) / Decimal(1_000_000)) * prices["input_per_mtok"]
        + (Decimal(500) / Decimal(1_000_000)) * prices["output_per_mtok"]
    ).quantize(Decimal("0.000001"))

    result = compute_cost_usd("claude-sonnet-4-6", 1000, 500)

    assert result == expected


@pytest.mark.unit
def test_compute_cost_usd_unknown_model_returns_zero():
    result = compute_cost_usd("some-future-model-xyz", 1000, 500)

    assert result == Decimal("0")


@pytest.mark.unit
def test_compute_cost_usd_none_tokens_treated_as_zero():
    result = compute_cost_usd("claude-sonnet-4-6", None, None)

    assert result == Decimal("0")


@pytest.mark.unit
def test_compute_cost_usd_quantized_to_six_decimal_places():
    result = compute_cost_usd("claude-haiku-4-5-20251001", 123, 456)

    assert result.as_tuple().exponent == -6


@pytest.mark.unit
def test_price_table_has_configured_models():
    for model in (
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ):
        assert model in _MODEL_PRICES
        assert "input_per_mtok" in _MODEL_PRICES[model]
        assert "output_per_mtok" in _MODEL_PRICES[model]


# ── LiteLLM fallback ──────────────────────────────────────────────────────────
# The curated table above covers six Anthropic models; the BYOK picker offers
# everything in LiteLLM's catalog. These pin the fallback that stops the rest of
# that catalog recording $0.00 into `usage_monthly` and under the budget guards.


@pytest.mark.unit
def test_model_outside_curated_table_is_priced_from_litellm():
    """The live 2026-09-14 case: a design turn on azure/gpt-5-mini recorded zero."""
    result = compute_cost_usd("azure/gpt-5-mini", 8252, 345)

    assert result > Decimal("0")
    # LiteLLM's own arithmetic for the same call, to the cent this ledger stores.
    assert result == Decimal("0.002753")


@pytest.mark.unit
def test_provider_prefixed_and_bare_names_price_identically():
    """The meter sees `azure/gpt-5-mini`, a resolved BYOK model carries `gpt-5-mini`.

    Same call, same money — otherwise the ledger disagrees with itself depending on
    which callback happened to record the turn.
    """
    assert compute_cost_usd("azure/gpt-5-mini", 1000, 500) == compute_cost_usd(
        "gpt-5-mini", 1000, 500
    )


@pytest.mark.unit
def test_curated_table_wins_over_litellm():
    """A dependency bump must not silently reprice a rate we publish."""
    from shared.cost import pricing

    model = "claude-sonnet-4-6"
    assert pricing._prices_for(model) is _MODEL_PRICES[model]


@pytest.mark.unit
def test_litellm_fallback_still_returns_zero_for_a_genuinely_unknown_model():
    assert compute_cost_usd("not-a-real-model-xyz", 1000, 500) == Decimal("0")


@pytest.mark.unit
def test_litellm_prices_are_decimal_not_float():
    """Float rounding is forbidden for anything summed into a ledger."""
    from shared.cost import pricing

    prices = pricing._prices_for("azure/gpt-5-mini")

    assert isinstance(prices["input_per_mtok"], Decimal)
    assert isinstance(prices["output_per_mtok"], Decimal)
