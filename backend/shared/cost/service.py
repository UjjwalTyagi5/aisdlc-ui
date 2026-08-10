"""CostLogService — fire-and-forget agent_call_logs writer (REQ-M9-06).

Mirrors AuditEventService.emit's fire-and-forget pattern (shared/audit/service.py):
schedule the write via asyncio.create_task, then yield with asyncio.sleep(0) so the
task starts before emit() returns (allows unit tests to assert against a mocked
session without an extra sleep). The write coroutine swallows all exceptions — a
DB outage degrades cost capture only, never the agent run (T-9.2-03).

A blank/empty/None tenant_id (the REST-path placeholder) is skipped entirely — RLS
requires a valid tenant UUID and would reject an empty string (T-9.2-02).
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from shared.cost.pricing import compute_cost_usd
from shared.db import get_db_session_for_tenant
from shared.models.orm import AgentCallLog

logger = logging.getLogger(__name__)


class CostLogService:
    """Single write path for agent_call_logs inserts."""

    async def emit(
        self,
        *,
        tenant_id: str | None,
        run_id: str | None,
        agent_type: str | None,
        model: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
        duration_ms: int | None,
    ) -> None:
        """Fire-and-forget emit. Never raises; empty tenant_id schedules no write."""
        if tenant_id is None or str(tenant_id).strip() == "":
            return

        cost_usd = compute_cost_usd(model, input_tokens, output_tokens)

        async def _attempt() -> None:
            try:
                async with get_db_session_for_tenant(str(tenant_id)) as session:
                    session.add(
                        AgentCallLog(
                            id=uuid.uuid4(),
                            tenant_id=uuid.UUID(str(tenant_id)),
                            run_id=str(run_id) if run_id else None,
                            agent_type=agent_type or "agent",
                            model=model or "unknown",
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cost_usd=cost_usd,
                            duration_ms=duration_ms,
                        )
                    )
            except Exception:
                logger.debug("CostLogService.emit: write failed (swallowed)", exc_info=True)

        asyncio.create_task(_attempt())
        await asyncio.sleep(0)  # yield so task starts before emit() returns


# Module-level singleton — imported by AuditCallbackHandler (D-02 pattern).
cost_service = CostLogService()
