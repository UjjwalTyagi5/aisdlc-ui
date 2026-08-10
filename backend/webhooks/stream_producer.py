"""Redis Stream webhook event producer — REQ-M6-09.

Publishes normalized canonical events to webhooks:{connector_kind} Redis Streams.
Stream key naming mirrors base_worker.py convention (tasks:{agent_type} →
webhooks:{connector_kind}).

Call AFTER signature verification, dedup check, and normalization.
"""
from __future__ import annotations

import json

import redis.asyncio as aioredis


async def publish_webhook_event(
    client: aioredis.Redis,
    connector_kind: str,
    canonical_event: dict,
) -> bytes | str | None:
    """XADD the normalized canonical event to the webhooks:{connector_kind} stream.

    The canonical_event dict must carry tenant_id and event_id so that the
    consumer (Plan 07) can build the deterministic Temporal workflow_id.

    Returns the Redis stream entry ID assigned by the XADD command.
    """
    stream_key = f"webhooks:{connector_kind}"
    return await client.xadd(stream_key, {"event": json.dumps(canonical_event)})
