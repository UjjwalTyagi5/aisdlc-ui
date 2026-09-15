"""Per-model USD price table + cost computation (REQ-M9-06).

_MODEL_PRICES maps a handful of models to USD-per-million-token input/output rates
(published Anthropic pricing); LiteLLM's own price table covers everything else.
compute_cost_usd quantizes to 6 decimal places to match the
agent_call_logs.cost_usd Numeric(10,6) column.

Decimal arithmetic only — Float rounding is forbidden for financial aggregates
(same rationale as the cost_usd column comment in shared/models/orm.py).

WHY THERE IS A SECOND SOURCE. _MODEL_PRICES held six Anthropic models and nothing
else, while `is_valid_model` lets a tenant pick any model in LiteLLM's catalog
(shared/services/model_catalog.py builds the BYOK picker straight from
`litellm.model_cost`). Every model outside those six therefore priced at exactly
$0.00 — silently, by the documented "unknown models return zero" contract. That is
not a display bug: `usage_monthly`, the Cost & Budgets page and the `monthly_budget_usd`
/ `cost_limit_usd` guards all read this number, so an OpenAI or Azure tenant could
never reach a cap however much they spent. Observed live on 2026-09-14: a design-agent
turn on `azure/gpt-5-mini` burned 8,252 in / 345 out (a real $0.002753) and recorded
zero.

LiteLLM is the right second source because it is already a hard dependency, it is
already what the model picker offers from, and its table is keyed by the same strings
the agents actually send — including the `provider/model` form (`azure/gpt-5-mini`)
that LangChain reports and that a bare-name table can never match.

THE CURATED TABLE STILL WINS. It is checked first: those rates are the ones this
platform has committed to in writing, and a dependency bump must not silently reprice
them.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

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


# LiteLLM's price map, normalised to this module's shape and built on first use.
# The import costs ~7s (see shared/services/model_catalog.py, which defers it for the
# same reason), and metering must not pay that on a module import that happens at
# process start. None means "tried and could not" — cached so a broken install degrades
# to the curated table instead of retrying the 7s import on every completion.
_LITELLM_PRICES: Optional[dict[str, dict[str, Decimal]]] = None
_LITELLM_LOADED = False


def _litellm_prices() -> dict[str, dict[str, Decimal]]:
    global _LITELLM_PRICES, _LITELLM_LOADED
    if _LITELLM_LOADED:
        return _LITELLM_PRICES or {}
    _LITELLM_LOADED = True
    try:
        import litellm  # noqa: PLC0415

        table: dict[str, dict[str, Decimal]] = {}
        for name, entry in (litellm.model_cost or {}).items():
            if not isinstance(entry, dict):
                continue
            in_cost, out_cost = entry.get("input_cost_per_token"), entry.get("output_cost_per_token")
            if in_cost is None and out_cost is None:
                continue
            # str() first: these arrive as floats, and Decimal(float) would carry the
            # binary representation error into a figure that is summed into a ledger.
            table[name] = {
                "input_per_mtok": Decimal(str(in_cost or 0)) * 1_000_000,
                "output_per_mtok": Decimal(str(out_cost or 0)) * 1_000_000,
            }
        _LITELLM_PRICES = table
    except Exception:  # pragma: no cover - pricing must never break a run
        logger.warning(
            "compute_cost_usd: LiteLLM price table unavailable; only the curated "
            "models will be priced and everything else records zero cost",
            exc_info=True,
        )
        _LITELLM_PRICES = None
    return _LITELLM_PRICES or {}


def _prices_for(model: str) -> dict[str, Decimal] | None:
    """Rates for one model name, curated table first, then LiteLLM's.

    Tries the name as given and then with any `provider/` prefix stripped, because the
    same call is named both ways depending on who is reporting it: the usage meter sees
    LangChain's `azure/gpt-5-mini` while a resolved BYOK model carries the bare
    `gpt-5-mini`. Both must price to the same number or the ledger disagrees with itself
    depending on which callback recorded the turn.
    """
    if not model:
        return None
    candidates = [model]
    if "/" in model:
        candidates.append(model.rsplit("/", 1)[1])
    for name in candidates:
        found = _MODEL_PRICES.get(name) or _litellm_prices().get(name)
        if found:
            return found
    return None


def compute_cost_usd(model: str | None, input_tokens: int | None, output_tokens: int | None) -> Decimal:
    """Compute the USD cost of one LLM call, quantized to 6 decimal places.

    Models in neither price table return Decimal("0") — never raises, so an unmapped
    model never crashes an agent run; cost just under-reports until the price table is
    updated (T-9.2-04, accepted).
    """
    prices = _prices_for(model or "")
    if prices is None:
        # INFO, not debug. This is the line that says real spend is being recorded as
        # zero, which is exactly what nobody noticed for the whole of the OpenAI and
        # Azure catalog; at debug level it never appeared in a normal deployment.
        logger.info("compute_cost_usd: unpriced model %r — recording zero cost", model)
        return Decimal("0").quantize(_SIX_DP)

    in_tok = Decimal(input_tokens or 0)
    out_tok = Decimal(output_tokens or 0)

    cost = (in_tok / Decimal(1_000_000)) * prices["input_per_mtok"] + (
        out_tok / Decimal(1_000_000)
    ) * prices["output_per_mtok"]

    return cost.quantize(_SIX_DP)
