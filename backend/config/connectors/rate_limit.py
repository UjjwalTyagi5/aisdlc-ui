"""Per-tenant rate-limit helpers for connector outbound calls (REQ-M6-10).

Re-exports the canonical implementation from webhooks.rate_limit_manager so that
Wave-2 connector modules can import from config.connectors.rate_limit without
depending on the webhooks package directly.

Usage inside a connector class:
    from config.connectors.rate_limit import (
        _TenantRateLimitState,
        await_backoff,
        record_rate_limit_hit,
    )

    class MyConnector(BaseConnector):
        _tenant_states: dict = {}

        async def some_method(self, tenant_id: str):
            retry_ref = [0]
            await await_backoff(self.__class__._tenant_states, tenant_id, retry_ref)
            # ... make HTTP call ...
            # On HTTP 429:
            retry_after = float(response.headers.get("Retry-After", 0))
            record_rate_limit_hit(
                self.__class__._tenant_states, tenant_id,
                retry_after_seconds=retry_after,
            )
"""
from webhooks.rate_limit_manager import (
    _TenantRateLimitState,
    rate_limit_manager_with_backoff as await_backoff,
    record_rate_limit_hit,
)

__all__ = [
    "_TenantRateLimitState",
    "await_backoff",
    "record_rate_limit_hit",
]
