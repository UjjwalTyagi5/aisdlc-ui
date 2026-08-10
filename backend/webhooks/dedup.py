"""Webhook event deduplication via atomic Redis SET NX EX — REQ-M6-07.

Uses a single atomic command (SET key value NX EX ttl) to prevent the
SETNX + EXPIRE race condition (RESEARCH.md Pitfall 5 avoidance).

Call AFTER signature verification, BEFORE XADD (Pitfall 5 ordering rule).
"""
from __future__ import annotations

import redis.asyncio as aioredis


async def is_duplicate_event(
    client: aioredis.Redis,
    provider: str,
    event_id: str,
    ttl_seconds: int = 86400,
) -> bool:
    """Return True if this event_id has already been seen (duplicate).

    Uses SET NX EX — single atomic command (NOT two-step SETNX + EXPIRE).
    Returns False on first occurrence (key was set); True on duplicate
    (key already existed — SET returned None).

    Key format: webhooks:dedup:{provider}:{event_id}
    TTL: 86400 seconds (24h) per CONTEXT.md decision.
    """
    key = f"webhooks:dedup:{provider}:{event_id}"
    result = await client.set(key, "1", nx=True, ex=ttl_seconds)
    return result is None  # None = key already existed → duplicate
