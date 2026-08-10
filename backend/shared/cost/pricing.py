"""Per-model USD price table + cost computation (REQ-M9-06).

_MODEL_PRICES maps each known model to USD-per-million-token input/output rates
(published Anthropic pricing). compute_cost_usd quantizes to 6 decimal places to
match the agent_call_logs.cost_usd Numeric(10,6) column.

Decimal arithmetic only — Float rounding is forbidden for financial aggregates
(same rationale as the cost_usd column comment in shared/models/orm.py).
"""
from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

_SIX_DP = Decimal("0.000001")

# USD per 1,000,000 tokens. Source: published Anthropic model pricing.
_MODEL_PRICES: dict[str, dict[str, Decimal]] = {
    "claude-sonnet-4-6": {
        "input_per_mtok": Decimal("3.00"),
        "output_per_mtok": Decimal("15.00"),
    },
    "claude-haiku-4-5-20251001": {
        "input_per_mtok": Decimal("1.00"),
        "output_per_mtok": Decimal("5.00"),
    },
    "claude-opus-4-1": {
        "input_per_mtok": Decimal("15.00"),
        "output_per_mtok": Decimal("75.00"),
    },
    "claude-opus-4-5": {
        "input_per_mtok": Decimal("15.00"),
        "output_per_mtok": Decimal("75.00"),
    },
    "claude-3-5-sonnet-20241022": {
        "input_per_mtok": Decimal("3.00"),
        "output_per_mtok": Decimal("15.00"),
    },
    "claude-3-5-haiku-20241022": {
        "input_per_mtok": Decimal("0.80"),
        "output_per_mtok": Decimal("4.00"),
    },
}


def compute_cost_usd(model: str | None, input_tokens: int | None, output_tokens: int | None) -> Decimal:
    """Compute the USD cost of one LLM call, quantized to 6 decimal places.

    Unknown models return Decimal("0") — never raises, so an unmapped model
    never crashes an agent run; cost just under-reports until the price table
    is updated (T-9.2-04, accepted).
    """
    prices = _MODEL_PRICES.get(model or "")
    if prices is None:
        logger.debug("compute_cost_usd: unknown model %r — recording zero cost", model)
        return Decimal("0").quantize(_SIX_DP)

    in_tok = Decimal(input_tokens or 0)
    out_tok = Decimal(output_tokens or 0)

    cost = (in_tok / Decimal(1_000_000)) * prices["input_per_mtok"] + (
        out_tok / Decimal(1_000_000)
    ) * prices["output_per_mtok"]

    return cost.quantize(_SIX_DP)
