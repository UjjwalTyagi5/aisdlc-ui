"""AbstractWorker — Redis Streams consumer-group base class.

Each agent type runs its own worker process consuming from ``tasks:{agent_type}``.
The consume loop reclaims crashed-worker messages via XAUTOCLAIM before reading
new ones via XREADGROUP. Messages that fail repeatedly are moved to a dead-letter
stream after _MAX_RECLAIM_ATTEMPTS so they cannot reclaim forever.
"""
import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from config.env import REDIS_URL, WORKER_RECLAIM_TIMEOUT_MS
from shared.services.metrics import TASK_DURATION, QUEUE_DEPTH, DEAD_LETTER_DEPTH

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "workers"
_MAX_RECLAIM_ATTEMPTS = 3


class AbstractWorker(ABC):
    """Base consume loop for a single agent type's worker process."""

    def __init__(self, agent_type: str, consumer_name: str):
        self.agent_type = agent_type
        self.consumer_name = consumer_name
        self.stream_key = f"tasks:{agent_type}"
        self.dlq_key = f"dlq:{agent_type}"
        self._reclaim_counts: dict[bytes, int] = {}

    async def run(self) -> None:
        client = aioredis.from_url(REDIS_URL)
        await self._ensure_group(client)
        try:
            while True:
                # Crash recovery first: reclaim messages idle longer than the
                # reclaim timeout. XAUTOCLAIM returns a 3-tuple in redis-py 7.x —
                # (next_start_id, claimed_messages, deleted_ids) — NOT a flat list.
                next_id, claimed, _ = await client.xautoclaim(
                    name=self.stream_key,
                    groupname=CONSUMER_GROUP,
                    consumername=self.consumer_name,
                    min_idle_time=WORKER_RECLAIM_TIMEOUT_MS,
                    start_id="0-0",
                    count=10,
                )
                for msg_id, fields in claimed:
                    attempts = self._reclaim_counts.get(msg_id, 0)
                    if attempts >= _MAX_RECLAIM_ATTEMPTS:
                        await self._dead_letter(client, msg_id, fields)
                        continue
                    self._reclaim_counts[msg_id] = attempts + 1
                    await self._process_one(client, msg_id, fields)

                # New messages: blocking read for up to 5s.
                results = await client.xreadgroup(
                    groupname=CONSUMER_GROUP,
                    consumername=self.consumer_name,
                    streams={self.stream_key: ">"},
                    count=1,
                    block=5000,
                )
                if results:
                    for _stream, messages in results:
                        for msg_id, fields in messages:
                            await self._process_one(client, msg_id, fields)
        except asyncio.CancelledError:
            # Clean shutdown — let the finally block close the client.
            pass
        finally:
            await client.aclose()

    async def _ensure_group(self, client) -> None:
        try:
            await client.xgroup_create(
                name=self.stream_key,
                groupname=CONSUMER_GROUP,
                id="$",
                mkstream=True,
            )
        except ResponseError as e:
            # BUSYGROUP means the group already exists — expected on restart.
            if "BUSYGROUP" not in str(e):
                raise

    async def _dead_letter(self, client, msg_id, fields) -> None:
        await client.xadd(self.dlq_key, fields)
        await client.xack(self.stream_key, CONSUMER_GROUP, msg_id)
        self._reclaim_counts.pop(msg_id, None)
        DEAD_LETTER_DEPTH.labels(stream=self.dlq_key).inc()
        logger.error(
            "Task %s exceeded %d reclaim attempts — moved to DLQ %s",
            msg_id,
            _MAX_RECLAIM_ATTEMPTS,
            self.dlq_key,
        )

    async def _process_one(self, client, msg_id, fields) -> None:
        start = time.time()
        try:
            await self.handle_task(fields)
            await client.xack(self.stream_key, CONSUMER_GROUP, msg_id)
            self._reclaim_counts.pop(msg_id, None)
            TASK_DURATION.labels(agent_type=self.agent_type).observe(time.time() - start)
        except Exception:
            # Do NOT xack on failure — leave the message in the PEL so that
            # XAUTOCLAIM reclaims it after the reclaim timeout.
            logger.exception(
                "Task %s failed — NOT acking; XAUTOCLAIM will reclaim", msg_id
            )

    @abstractmethod
    async def handle_task(self, fields: dict) -> None:
        """Agent-specific execution logic. Receives the raw Redis stream fields."""
