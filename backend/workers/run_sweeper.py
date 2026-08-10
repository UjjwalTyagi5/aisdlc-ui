"""RunSweeper — expires runs stuck in "running" with no activity.

Conversational (Copilot) runs skip Temporal, so nothing owns their terminal
transition: a run abandoned mid-conversation stays status="running" forever and
the Runs page fills with phantom in-flight runs. This sweeper marks any
non-gate-pending run that has been idle (updated_at) longer than
RUN_STALE_HOURS as "cancelled" — the closest terminal status the frontend
vocabulary has for "abandoned".

Scope guards:
  - Only raw status "running"/"in_progress" rows are swept. Runs parked at a
    human gate (awaiting_*) are excluded — an approval can legitimately wait
    days, and Temporal owns its own workflow timeouts.
  - Idle means the row itself hasn't been touched: every Copilot turn persists
    artifacts/stage onto the run row (updated_at onupdate), so an active
    conversation keeps its run out of scope.

Runs cross-tenant (superuser session, same pattern as the Copilot's own
persistence) and emits a run.expired audit event per swept run.

Usage (from process_api lifespan):
    task = asyncio.create_task(RunSweeper().run())
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

logger = logging.getLogger(__name__)

# Hours a "running" run may sit without any row update before it is expired.
RUN_STALE_HOURS = float(os.environ.get("RUN_STALE_HOURS", "24"))
# Sweep cadence — hourly is plenty for a 24h threshold.
SWEEP_INTERVAL_SECONDS = float(os.environ.get("RUN_SWEEP_INTERVAL_SECONDS", "3600"))

_SWEEPABLE_RAW_STATUSES = ("running", "in_progress")


class RunSweeper:
    """Periodically cancels runs that have been idle in "running" for too long."""

    def __init__(self, stale_hours: float = RUN_STALE_HOURS) -> None:
        self._stale_hours = stale_hours

    async def sweep_once(self) -> int:
        """Expire idle running runs. Returns the number of runs swept."""
        from shared.db import get_db_session_superuser  # noqa: PLC0415
        from shared.models.orm import Run  # noqa: PLC0415

        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._stale_hours)
        # Snapshot audit fields before commit — instances expire on session close.
        expired: list[dict] = []
        async with get_db_session_superuser() as s:
            rows = (
                await s.execute(
                    select(Run).where(
                        Run.status.in_(_SWEEPABLE_RAW_STATUSES),
                        Run.gate_pending.is_(False),
                        Run.updated_at < cutoff,
                    )
                )
            ).scalars().all()
            for run in rows:
                run.status = "cancelled"
                expired.append({
                    "tenant_id": str(run.tenant_id),
                    "run_id": str(run.id),
                    "stage": run.current_stage or run.stage,
                })
            if expired:
                await s.commit()

        if expired:
            logger.info(
                "RunSweeper: cancelled %d run(s) idle > %.0fh", len(expired), self._stale_hours
            )
            for info in expired:
                await self._emit_audit(info)
        return len(expired)

    async def _emit_audit(self, info: dict) -> None:
        """Fire-and-forget run.expired audit event — failure never breaks the sweep."""
        try:
            from shared.audit.models import AuditEventPayload  # noqa: PLC0415
            from shared.audit.service import audit_service  # noqa: PLC0415

            await audit_service.emit(
                AuditEventPayload(
                    tenant_id=info["tenant_id"],
                    run_id=info["run_id"],
                    event_type="run.expired",
                    actor_id="run-sweeper",
                    resource_type="run",
                    resource_id=info["run_id"],
                    payload={
                        "reason": f"idle in 'running' > {self._stale_hours:.0f}h",
                        "stage": info["stage"],
                    },
                )
            )
        except Exception:  # noqa: BLE001
            logger.debug("RunSweeper: audit emit failed (swallowed)", exc_info=True)

    async def run(self) -> None:
        """Blocking loop: sweep immediately at startup, then every interval."""
        while True:
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("RunSweeper sweep failed; retrying next interval", exc_info=True)
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


if __name__ == "__main__":  # manual one-shot: python -m workers.run_sweeper
    logging.basicConfig(level=logging.INFO)
    print("swept:", asyncio.run(RunSweeper().sweep_once()))
