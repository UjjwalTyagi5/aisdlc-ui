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
