"""Evaluation gate — the durable PASS/FAIL store `propose()`/`propose_skill()` and
`governance_requests.decide()` consult before letting an Agent Studio draft advance
(sub-project 4). Distinct from shared/eval/service.py's EvalRecordService: that is
fire-and-forget telemetry for an agent RUN's output; this is a durable, queryable
gate keyed to one specific draft VERSION.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select

from shared.db import get_db_session_for_tenant
from shared.eval.agent_studio_scoring import evaluate_agent_default
from shared.models.orm import AgentDefaultEvaluation


def _as_dict(row: AgentDefaultEvaluation) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "target_type": row.target_type,
        "target_id": str(row.target_id),
        "agent_id": row.agent_id,
        "scope": row.scope,
        "result": row.result,
        "score": row.score,
        "signals": row.signals,
        "evaluator_id": row.evaluator_id,
        "evaluator_role": row.evaluator_role,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def run_evaluation(
    tenant_id: str, target_type: str, target_id: str, agent_id: str, scope: str,
    body: str, evaluator_id: str, evaluator_role: Optional[str],
) -> dict[str, Any]:
    """Score `body`, insert one append-only AgentDefaultEvaluation row, return it."""
    is_pass, signals = evaluate_agent_default(agent_id, body)
    async with get_db_session_for_tenant(str(tenant_id)) as session:
        row = AgentDefaultEvaluation(
            tenant_id=tenant_id, target_type=target_type, target_id=target_id,
            agent_id=agent_id, scope=scope,
            result="pass" if is_pass else "fail",
            score=signals.score, signals=signals.signals,
            evaluator_id=evaluator_id, evaluator_role=evaluator_role,
        )
        session.add(row)
        await session.flush()
        return _as_dict(row)


async def latest_passing_evaluation(
    tenant_id: str, target_type: str, target_id: str,
) -> Optional[dict[str, Any]]:
    """The newest PASS row for this EXACT target_id, or None. Scoped to the exact
    version — a PASS on an earlier or later version of the same draft never
    satisfies a check for a different target_id (see Global Constraints)."""
    async with get_db_session_for_tenant(str(tenant_id)) as session:
        row = (await session.execute(
            select(AgentDefaultEvaluation).where(
                AgentDefaultEvaluation.target_type == target_type,
                AgentDefaultEvaluation.target_id == target_id,
                AgentDefaultEvaluation.result == "pass",
            ).order_by(AgentDefaultEvaluation.created_at.desc())
        )).scalars().first()
        return _as_dict(row) if row else None
