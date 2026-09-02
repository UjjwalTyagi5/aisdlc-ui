"""Artifact persistence and Redis pub/sub notification for SDLC pipeline stages.

Public API:
  persist_artifact(run_id, artifact_type, artifact_data, *, tenant_id)
      -> writes model_dump() output to the matching JSONB column on runs table

  publish_artifact_ready(run_id, artifact_type, *, tenant_id)
      -> publishes {"event":"artifact_ready", "run_id":..., "artifact_type":...,
         "tenant_id":...} to Redis so _handle_artifact_ready can scope its session

  write_and_notify(run_id, artifact_type, artifact_data, *, tenant_id)
      -> convenience: persist then publish; call from stage runners
         and agent tool functions that have a tenant_id in scope
"""
import json
import logging
from typing import Optional

import redis.asyncio as aioredis
from sqlalchemy import select

from config.env import REDIS_URL
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.models.orm import Run

logger = logging.getLogger(__name__)

_ARTIFACT_CHANNEL = "artifact_events"

# Maps artifact_type string to the corresponding JSONB column on the runs table.
# Unknown artifact_type values raise ValueError — callers must use this allowlist.
_COLUMN_MAP = {
    "requirements": "requirements_payload",
    "design": "design_artifacts",
    "plan": "plan_artifacts",
    "development": "development_artifacts",
    "testing": "testing_artifacts",
    "code_review": "code_review_artifacts",
    "security": "security_artifacts",
    "deployment": "deployment_artifacts",
    "documentation": "documentation_artifacts",
}


async def persist_artifact(
    run_id: str,
    artifact_type: str,
    artifact_data: dict,
    *,
    tenant_id: Optional[str] = None,
) -> None:
    """Write typed artifact dict to runs JSONB column.

    artifact_data must be the output of model_dump() from a typed Pydantic artifact
    model (RequirementsArtifact, DesignArtifact, etc.) — unvalidated dicts must not
    be passed directly (T-M2-02-02 mitigation).

    tenant_id should always be provided by callers that have tenant context (stage
    runners, agent tool functions via SDLCWorkflowInput). When absent, falls
    back to get_db_session_superuser() — a system-scoped read-then-write that is only
    safe before RLS FORCE is active; once Plan 04 enables FORCE RLS this fallback will
    return zero rows unless the role is BYPASSRLS (D-05 backward-compat path).
    """
    column_name = _COLUMN_MAP.get(artifact_type)
    if not column_name:
        raise ValueError(
            f"Unknown artifact_type: {artifact_type!r}. Must be one of {list(_COLUMN_MAP)}"
        )

    # Prefer tenant-scoped session (D-04); fall back to superuser for callers that
    # have not yet been updated to pass tenant_id (backward-compat bridge, D-05).
    _ctx = get_db_session_for_tenant(tenant_id) if tenant_id else get_db_session_superuser()
    async with _ctx as session:
        result = await session.execute(select(Run).where(Run.id == run_id))
        run = result.scalar_one_or_none()
        if run is None:
            logger.warning("persist_artifact: run_id %s not found", run_id)
            return
        setattr(run, column_name, artifact_data)
    logger.info("persist_artifact: wrote %s for run %s", artifact_type, run_id)


async def publish_artifact_ready(
    run_id: str,
    artifact_type: str,
    *,
    tenant_id: Optional[str] = None,
) -> None:
    """Publish Redis pub/sub event after artifact is persisted to Postgres.

    Creates a short-lived client per call — artifact writes are infrequent
    (once per pipeline stage) so connection pooling is not needed here.
    tenant_id is included in the payload so _handle_artifact_ready can scope
    its DB session without a NULL-tenant query (T-M7.1-09 mitigation).
    """
    client = aioredis.from_url(REDIS_URL)
    try:
        payload = json.dumps(
            {
                "event": "artifact_ready",
                "run_id": run_id,
                "artifact_type": artifact_type,
                "tenant_id": tenant_id or "",
            }
        )
        await client.publish(_ARTIFACT_CHANNEL, payload)
    finally:
        await client.aclose()


async def write_and_notify(
    run_id: str,
    artifact_type: str,
    artifact_data: dict,
    *,
    tenant_id: Optional[str] = None,
) -> None:
    """Persist artifact to Postgres then publish Redis notification.

    This is the primary entry point for stage runners and agent
    tool functions. Pass tenant_id from the run input so both the DB write
    and the pub/sub payload are tenant-scoped (REQ-M7-07 / T-M7.1-08).
    """
    await persist_artifact(run_id, artifact_type, artifact_data, tenant_id=tenant_id)
    await publish_artifact_ready(run_id, artifact_type, tenant_id=tenant_id)
