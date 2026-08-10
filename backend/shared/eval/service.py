"""EvalRecordService — single write path for all eval_records inserts (REQ-M9-11).

Mirrors shared/audit/service.py's fire-and-forget contract exactly:

emit() — fire-and-forget; failures are logged and swallowed, never raises.
         Schedules the write via asyncio.create_task + sleep(0) (same shape as
         AuditEventService.emit) so eval telemetry can never break an agent run
         (T-9.3-05).

Unlike AuditEventService, EvalRecordService does not maintain a Redis dead-letter
stream — eval_records is a best-effort quality signal (not an audit/compliance
record), so a logger.warning fallback is sufficient and avoids adding a second
Redis stream + retry worker for a non-critical telemetry path. This is a
deliberate, narrower scope than audit (documented per Task 1 instructions).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Optional

from shared.db import get_db_session_for_tenant
from shared.eval.scoring import score_output
from shared.models.orm import EvalRecord

logger = logging.getLogger(__name__)

# Maps the artifact_type strings used by write_and_notify (shared/services/
# artifact_service._COLUMN_MAP) to the agent_type strings the scoring contract
# expects (shared/eval/scoring.score_output / DESIGN_REQUIRED_SECTIONS check).
_SCORING_AGENT_TYPE_MAP = {
    "design": "design_architecture",
}


def _extract_text(artifact_data: dict[str, Any]) -> str:
    """Concatenate all string field values from an artifact dict for self-eval scoring.

    Runtime emit has no "expected" output to compare against (that's the golden
    runner's job, Task 2) — this produces the `actual` text fed to score_output()
    so intrinsic-quality signals (section coverage for design, non-empty content
    for everything else) can be computed.
    """
    parts: list[str] = []
    for value in artifact_data.values():
        if isinstance(value, str) and value:
            parts.append(value)
    return "\n".join(parts)


class EvalRecordService:
    """Single write path for all eval_records inserts (REQ-M9-11)."""

    async def _write(
        self,
        *,
        tenant_id: str,
        run_id: Optional[str],
        agent_type: str,
        score: Optional[float],
        signals: dict[str, Any],
    ) -> None:
        """DB write for a single EvalRecord.

        Uses get_db_session_for_tenant(tenant_id) — set_config('app.current_tenant_id', ...)
        ensures FORCE RLS (migration 0010, plan 01) constrains the insert to the
        correct tenant (T-9.3-06; M7.1 SET LOCAL bind-param bug avoided — db.py
        already uses set_config, not SET LOCAL with a bound parameter).
        """
        async with get_db_session_for_tenant(str(tenant_id)) as session:
            session.add(
                EvalRecord(
                    id=uuid.uuid4(),
                    tenant_id=uuid.UUID(str(tenant_id)),
                    run_id=run_id,
                    agent_type=agent_type,
                    score=score,
                    signals=signals,
                )
            )

    async def emit(
        self,
        *,
        tenant_id: Optional[str],
        run_id: Optional[str],
        agent_type: str,
        artifact_data: Optional[dict[str, Any]] = None,
        score: Optional[float] = None,
        signals: Optional[dict[str, Any]] = None,
    ) -> None:
        """Fire-and-forget emit. Failures are logged and swallowed; never raises.

        Either pass `score`/`signals` directly, or pass `artifact_data` and let
        emit() derive intrinsic-quality signals via score_output() (no golden
        "expected" at runtime — see _extract_text docstring).

        asyncio.create_task schedules _attempt on the running event loop; the
        subsequent sleep(0) yields so the task starts before emit() returns —
        identical shape to AuditEventService.emit (T-9.3-05 mitigation).

        A missing tenant_id is logged and skipped (not written) — eval_records is
        a FORCE-RLS tenant-scoped table (migration 0010, T-9.3-06) with no superuser
        bypass path defined for this insert; this never raises into the caller.
        """
        if not tenant_id:
            logger.warning(
                "EvalRecordService.emit: missing tenant_id for run_id=%s agent_type=%s — skipping",
                run_id,
                agent_type,
            )
            return

        resolved_score = score
        resolved_signals = signals if signals is not None else {}

        if resolved_score is None and artifact_data is not None:
            scoring_agent_type = _SCORING_AGENT_TYPE_MAP.get(agent_type, agent_type)
            actual_text = _extract_text(artifact_data)
            eval_signals = score_output(scoring_agent_type, actual_text, expected="")
            resolved_score = eval_signals.score
            resolved_signals = eval_signals.signals

        async def _attempt() -> None:
            try:
                await self._write(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    agent_type=agent_type,
                    score=resolved_score,
                    signals=resolved_signals,
                )
            except Exception:
                logger.warning(
                    "EvalRecordService.emit: write failed for run_id=%s agent_type=%s",
                    run_id,
                    agent_type,
                    exc_info=True,
                )

        asyncio.create_task(_attempt())
        await asyncio.sleep(0)  # yield so task starts before emit() returns


# Module-level singleton — imported by workflows/activities/_base.py (single
# integration point, REQ-M9-11).
eval_service = EvalRecordService()
