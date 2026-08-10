"""Eval resource router.

Exposes a read endpoint for EvalRecord data. All routes are JWT-protected
(NOT in _EXEMPT_PATHS) and scope every query by request.state.tenant_id.

Routes:
  GET  /runs/{run_id}/eval   — newest-first list of EvalRecords for a run, RBAC-gated

Threat mitigations:
  - T-9.3-09: query filters by EvalRecord.tenant_id == request.state.tenant_id
    AND EvalRecord.run_id == run_id (no cross-tenant reads; RLS is the DB backstop)
  - T-9.3-10: require_permission("artifact:view", run_param="run_id") on the route
  - Route not in _EXEMPT_PATHS (JWT middleware enforces 401 without token)

Router mounting note (REQ-M9-14):
  eval_runs_router — mounted WITHOUT a prefix in process_api.py
    so GET /runs/{run_id}/eval is the public path.
    Mirrors audit_runs_router mounting pattern exactly (milestone-8-04 decision).
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.dependency import require_permission
from shared.db import get_db_session
from shared.models.orm import EvalRecord
from shared.routers._schemas import EvalRecordOut

# Separate router — mounted WITHOUT the /runs prefix in process_api.py so the
# public path resolves to /runs/{run_id}/eval (REQ-M9-14; same pattern as
# audit_runs_router, milestone-8-04 decision).
eval_runs_router = APIRouter()


@eval_runs_router.get(
    "/runs/{run_id}/eval",
    response_model=List[EvalRecordOut],
    dependencies=[Depends(require_permission("artifact:view", run_param="run_id"))],
)
async def get_run_eval(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> List[EvalRecordOut]:
    """Return all EvalRecords for a specific run, newest-first.

    A run may have multiple EvalRecords when re-evaluated; the frontend uses
    the first (latest) record as the primary quality signal.

    Scoped to the requesting tenant (T-9.3-09: tenant_id == request.state.tenant_id
    AND run_id == path param).  Requires artifact:view permission (T-9.3-10,
    REQ-M9-14).
    """
    tenant_id = request.state.tenant_id

    stmt = (
        select(EvalRecord)
        .where(
            EvalRecord.tenant_id == tenant_id,
            EvalRecord.run_id == run_id,
        )
        .order_by(EvalRecord.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [EvalRecordOut.from_orm_eval(r) for r in rows]
