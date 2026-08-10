"""Single-use Redis ticket mint and redeem for WebSocket authentication.

Ticket pattern (D-03): JWT never appears in WS URLs. Client exchanges a short-lived
single-use UUID ticket that is atomically deleted on first use.

GETDEL (Redis 6.2+) provides atomic GET + DEL in one round-trip, eliminating any
TOCTOU race condition that a GET + DEL pipeline would expose.
"""
import json
import logging
import uuid

import redis.asyncio as aioredis

from config.env import REDIS_URL

logger = logging.getLogger(__name__)

_WS_TICKET_TTL_SECONDS = 20


async def mint_ws_ticket(user_id: str, tenant_id: str) -> str:
    """Store a single-use ticket in Redis and return the UUID."""
    ticket = str(uuid.uuid4())
    client = aioredis.from_url(REDIS_URL)
    try:
        await client.setex(
            f"ws_ticket:{ticket}",
            _WS_TICKET_TTL_SECONDS,
            json.dumps({"user_id": user_id, "tenant_id": tenant_id or ""}),
        )
    finally:
        await client.aclose()
    return ticket


async def redeem_ws_ticket(ticket: str) -> dict | None:
    """Atomically consume a ticket and return its claims, or None if absent/expired."""
    client = aioredis.from_url(REDIS_URL)
    try:
        raw = await client.getdel(f"ws_ticket:{ticket}")
    finally:
        await client.aclose()
    if raw is None:
        return None
    return json.loads(raw)
