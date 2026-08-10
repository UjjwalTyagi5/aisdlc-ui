"""Per-tenant rate-limit manager for connector outbound calls (REQ-M6-10).

State is keyed strictly by tenant_id so one tenant's HTTP 429 backoff window
never blocks another tenant's calls (T-m6-02-EoP mitigation).

Usage (inside a connector method):
    retry_ref = [0]
    await rate_limit_manager_with_backoff(_tenant_states, tenant_id, retry_ref)
    # ... make HTTP call ...
    # On 429:
    retry_after = float(response.headers.get("Retry-After", 0))
    record_rate_limit_hit(_tenant_states, tenant_id, retry_after_seconds=retry_after)
"""
from __future__ import annotations

import asyncio
import time


class _TenantRateLimitState:
    """Per-tenant backoff state — retry count and the wall-clock expiry of the current window."""

    __slots__ = ("retry_count", "backoff_until")

    def __init__(self) -> None:
        self.retry_count: int = 0
        self.backoff_until: float = 0.0


async def rate_limit_manager_with_backoff(
    tenant_states: dict,
    tenant_id: str,
    retry_count_ref: list,
) -> None:
    """Wait out any active backoff window for this tenant; write current retry_count into the ref.

    tenant_states is a mutable dict keyed by tenant_id.  Each entry is a
    _TenantRateLimitState.  Callers keep a class-level dict per connector class so
    that state is isolated per connector type *and* per tenant.

    retry_count_ref is a single-element list used as a mutable ref so callers can
    read the retry count after the await without an additional dict lookup.
    """
    state = tenant_states.setdefault(tenant_id, _TenantRateLimitState())
    now = time.time()
    if state.backoff_until > now:
        wait = state.backoff_until - now
        await asyncio.sleep(wait)
    retry_count_ref[0] = state.retry_count


def record_rate_limit_hit(
    tenant_states: dict,
    tenant_id: str,
    retry_after_seconds: float | None = None,
) -> None:
    """Record a 429 response; compute next backoff window using exponential backoff.

    If retry_after_seconds is provided and positive, honor the Retry-After header.
    Otherwise use exponential backoff: min(2 ** retry_count, 60) seconds.
    """
    state = tenant_states.setdefault(tenant_id, _TenantRateLimitState())
    state.retry_count += 1
    if retry_after_seconds and retry_after_seconds > 0:
        state.backoff_until = time.time() + retry_after_seconds
    else:
        # Exponential: 2^retry_count seconds, capped at 60 s
        state.backoff_until = time.time() + min(2 ** state.retry_count, 60)
