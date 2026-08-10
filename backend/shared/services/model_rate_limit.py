"""Per-model RPM limiter — live enforcement at resolve_model_for_run.

A fixed-window (per-minute) counter in Redis, keyed by tenant + offering so it
inherits the org/workspace scoping of the offering's parent provider row. Each
model resolution (one per agent turn / pipeline activity) increments the window;
crossing the offering's rpm_limit raises ModelRateLimitError so the run fails
closed with a clear, actionable message.

Design choices (mirroring the connector rate-limit + fire-and-forget audit style):
  - rpm_limit None/0 -> no-op (no limit configured).
  - Redis unavailable -> FAIL OPEN (log once, allow the call). Rate limiting is
    best-effort protection; a Redis outage must not block every legitimate run.
  - Fixed 60s window (INCR + EXPIRE) — cheap, adequate for an RPM guard. A sliding
    window is a future refinement if burst-at-boundary becomes a concern.
"""
from __future__ import annotations

import logging
import time

from config.env import REDIS_URL

logger = logging.getLogger(__name__)


class ModelRateLimitError(Exception):
    """The offering's requests-per-minute limit is exhausted for this window."""


class ModelTokenLimitError(Exception):
    """The offering's tokens-per-minute limit is exhausted for this window."""


class ModelCostLimitError(Exception):
    """The offering's monthly cost budget is exhausted."""


def _minute() -> int:
    return int(time.time() // 60)


def _month() -> str:
    t = time.gmtime()
    return f"{t.tm_year}{t.tm_mon:02d}"


async def record_usage(
    tenant_id: str, offering_id: str, tokens: int, cost_usd: float
) -> None:
    """Increment the per-minute token window + monthly cost counter for an offering.

    Called from the usage-meter callback on every LLM completion. Best-effort:
    swallows Redis errors (metering degrades, runs never break). No-op without an
    offering or Redis.
    """
    if not offering_id:
        return
    client = _client()
    if client is None:
        return
    try:
        if tokens and tokens > 0:
            tkey = f"llm_tpm:{tenant_id}:{offering_id}:{_minute()}"
            await client.incrby(tkey, int(tokens))
            await client.expire(tkey, 70)
        if cost_usd and cost_usd > 0:
            ckey = f"llm_cost:{tenant_id}:{offering_id}:{_month()}"
            await client.incrbyfloat(ckey, float(cost_usd))
            await client.expire(ckey, 40 * 24 * 3600)  # ~40 days, covers the month
    except Exception:  # pragma: no cover - metering must never break a run
        logger.debug("usage meter: Redis record failed (swallowed)", exc_info=True)


async def record_budget_cost(
    scopes: list[tuple[str, str]], cost_usd: float, month: str
) -> None:
    """Increment the fast Redis monthly cost counter for each (scope, scope_id).

    Best-effort accelerator alongside the durable usage_monthly rollup (the
    authoritative source enforcement reads). No-op without Redis. Key shape:
    ``budget:{scope}:{scope_id}:{yyyymm}``.
    """
    if not cost_usd or cost_usd <= 0:
        return
    client = _client()
    if client is None:
        return
    try:
        for scope, scope_id in scopes:
            if not scope_id:
                continue
            key = f"budget:{scope}:{scope_id}:{month}"
            await client.incrbyfloat(key, float(cost_usd))
            await client.expire(key, 40 * 24 * 3600)  # ~40 days, covers the month
    except Exception:  # pragma: no cover - metering must never break a run
        logger.debug("budget meter: Redis record failed (swallowed)", exc_info=True)


async def enforce_tpm(tenant_id: str, offering_id: str, tpm_limit: int | None) -> None:
    """Block if the current minute's accumulated tokens already meet/exceed tpm_limit.

    Reactive: the check runs at resolve (pre-call) against tokens recorded by prior
    completions this minute. No-op when unset or Redis is down (fail open).
    """
    if not tpm_limit or tpm_limit <= 0 or not offering_id:
        return
    client = _client()
    if client is None:
        return
    try:
        raw = await client.get(f"llm_tpm:{tenant_id}:{offering_id}:{_minute()}")
        used = int(raw) if raw else 0
    except Exception:  # pragma: no cover - fail open
        logger.debug("usage meter: TPM read failed; allowing", exc_info=True)
        return
    if used >= tpm_limit:
        raise ModelTokenLimitError(
            f"Token rate limit reached for this model ({tpm_limit} tokens/min). "
            "Please retry in a moment."
        )


async def enforce_cost(tenant_id: str, offering_id: str, cost_limit_usd: float | None) -> None:
    """Block if this month's accumulated cost for the offering meets/exceeds the budget.

    No-op when unset or Redis is down (fail open).
    """
    if not cost_limit_usd or cost_limit_usd <= 0 or not offering_id:
        return
    client = _client()
    if client is None:
        return
    try:
        raw = await client.get(f"llm_cost:{tenant_id}:{offering_id}:{_month()}")
        spent = float(raw) if raw else 0.0
    except Exception:  # pragma: no cover - fail open
        logger.debug("usage meter: cost read failed; allowing", exc_info=True)
        return
    if spent >= float(cost_limit_usd):
        raise ModelCostLimitError(
            f"Monthly cost budget reached for this model (${cost_limit_usd:.2f}). "
            "Raise the limit in Model Providers to continue."
        )


_redis = None
_redis_failed = False


def _client():
    """Lazily build a module-level async Redis client; None if Redis is unconfigured."""
    global _redis, _redis_failed
    if _redis is not None or _redis_failed:
        return _redis
    if not REDIS_URL:
        _redis_failed = True
        return None
    try:
        import redis.asyncio as aioredis  # noqa: PLC0415

        _redis = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    except Exception:  # pragma: no cover - defensive
        logger.warning("model RPM limiter: Redis init failed; enforcement disabled", exc_info=True)
        _redis_failed = True
    return _redis


async def enforce_rpm(tenant_id: str, offering_id: str, rpm_limit: int | None) -> None:
    """Increment the per-minute window; raise ModelRateLimitError if over the cap.

    No-op when rpm_limit is falsy or Redis is unavailable (fail open).
    """
    if not rpm_limit or rpm_limit <= 0 or not offering_id:
        return
    client = _client()
    if client is None:
        return
    minute = int(time.time() // 60)
    key = f"llm_rpm:{tenant_id}:{offering_id}:{minute}"
    try:
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, 70)  # window + small grace
    except Exception:  # pragma: no cover - fail open on Redis error
        logger.debug("model RPM limiter: Redis op failed; allowing call", exc_info=True)
        return
    if count > rpm_limit:
        raise ModelRateLimitError(
            f"Rate limit reached for this model ({rpm_limit} requests/min). "
            "Please retry in a moment."
        )
