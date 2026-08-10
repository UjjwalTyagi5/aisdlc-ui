"""Evidence export router (REQ-M8-07, D-03).

Routes:
  POST   /runs/{run_id}/evidence                   — initiate async evidence ZIP export
  GET    /runs/{run_id}/evidence/{job_id}/status    — poll job status

Both routes are gated by require_permission("artifact:view", run_param="run_id")
(T-M8-14, D-05 boot-scan sentinel present).

Flow (D-03):
  1. POST → 202 + {"job_id": uuid4}; stores evidence:job:{job_id}={"status":"pending"}
     in Redis with 24h TTL; schedules a background task.
  2. Background task: query artifacts + audit_events → build_evidence_zip() → upload
     to Azure Blob → generate user-delegation SAS URL → update Redis job state to
     {"status":"ready","download_url":...}.  On any error → {"status":"failed"}.
  3. GET .../status → reads Redis job JSON; 404 when job unknown.

Blob path convention (T-M8-17 cross-tenant isolation):
  {tenant_id}/evidence/{run_id}/{job_id}.zip

Threat mitigations:
  T-M8-14: both routes carry require_permission("artifact:view", run_param="run_id")
  T-M8-15: SAS URL uses user-delegation key (read-only, 24h TTL)
  T-M8-16: ZIP contains manifest.json with SHA-256 per file matching contents
  T-M8-17: blob path namespaced by tenant_id
  T-M8-18: routes classified before assert_all_routes_protected (boot-scan clean)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.audit.evidence import build_evidence_zip
from shared.authz.dependency import require_permission
from shared.db import get_db_session
from shared.models.orm import Artifact, AuditEvent
from shared.services.metrics import EVIDENCE_EXPORT_DURATION
from shared.storage.azure_blob import generate_evidence_sas_url

logger = logging.getLogger(__name__)

evidence_router = APIRouter()

# Redis TTL for evidence job state keys (24 hours)
_JOB_TTL_SECONDS = 24 * 3600


class EvidenceJobResponse(BaseModel):
    """Response body for POST /runs/{run_id}/evidence."""
    job_id: str


class EvidenceStatusResponse(BaseModel):
    """Response body for GET /runs/{run_id}/evidence/{job_id}/status."""
    status: str   # pending | processing | ready | failed
    download_url: Optional[str] = None


@evidence_router.post(
    "/runs/{run_id}/evidence",
    status_code=202,
    response_model=EvidenceJobResponse,
    dependencies=[Depends(require_permission("artifact:view", run_param="run_id"))],
)
async def initiate_evidence_export(
    run_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Initiate an async evidence export for a run.

    Returns 202 + {"job_id": ...} immediately. A background task builds the ZIP,
    uploads it to Azure Blob, and stores the download URL in Redis.

    Returns 503 when Azure Blob storage is not configured (AZURE_BLOB_ACCOUNT_URL absent).
    """
    blob_client = getattr(request.app.state, "blob_client", None)
    if blob_client is None:
        raise HTTPException(
            status_code=503,
            detail="Evidence export unavailable — Azure Blob Storage not configured.",
        )

    redis_pool = getattr(request.app.state, "redis_pool", None)

    tenant_id: str = getattr(request.state, "tenant_id", "") or ""
    job_id = str(uuid.uuid4())

    # Write initial pending state to Redis (24h TTL)
    _pending = json.dumps({"status": "pending"})
    if redis_pool is not None:
        import redis.asyncio as aioredis
        redis_client = aioredis.Redis(connection_pool=redis_pool)
        await redis_client.set(
            f"evidence:job:{job_id}",
            _pending,
            ex=_JOB_TTL_SECONDS,
        )
    else:
        # Fallback: store on app.state directly for testing (no Redis available)
        if not hasattr(request.app.state, "_evidence_jobs"):
            request.app.state._evidence_jobs = {}
        request.app.state._evidence_jobs[job_id] = {"status": "pending"}

    # Schedule background task
    background_tasks.add_task(
        _run_evidence_export,
        app=request.app,
        run_id=run_id,
        tenant_id=tenant_id,
        job_id=job_id,
        blob_client=blob_client,
        redis_pool=redis_pool,
    )

    return JSONResponse(content={"job_id": job_id}, status_code=202)


@evidence_router.get(
    "/runs/{run_id}/evidence/{job_id}/status",
    response_model=EvidenceStatusResponse,
    dependencies=[Depends(require_permission("artifact:view", run_param="run_id"))],
)
async def get_evidence_status(
    run_id: str,
    job_id: str,
    request: Request,
) -> EvidenceStatusResponse:
    """Poll the status of an evidence export job.

    Returns the current job state: pending | processing | ready | failed.
    When status is "ready", the response also includes a download_url.

    Returns 404 when the job ID is unknown (expired or never existed).
    """
    redis_pool = getattr(request.app.state, "redis_pool", None)

    raw: Optional[str] = None
    if redis_pool is not None:
        import redis.asyncio as aioredis
        redis_client = aioredis.Redis(connection_pool=redis_pool)
        raw_bytes = await redis_client.get(f"evidence:job:{job_id}")
        if raw_bytes is not None:
            raw = raw_bytes.decode() if isinstance(raw_bytes, bytes) else raw_bytes
    else:
        # Fallback: read from app.state (test path)
        jobs: dict = getattr(request.app.state, "_evidence_jobs", {})
        if job_id in jobs:
            raw = json.dumps(jobs[job_id])

    if raw is None:
        raise HTTPException(status_code=404, detail="Evidence job not found or expired.")

    job_data: dict[str, Any] = json.loads(raw)
    return EvidenceStatusResponse(
        status=job_data.get("status", "unknown"),
        download_url=job_data.get("download_url"),
    )


async def _run_evidence_export(
    *,
    app: Any,
    run_id: str,
    tenant_id: str,
    job_id: str,
    blob_client: Any,
    redis_pool: Any,
) -> None:
    """Background task: build evidence ZIP, upload to Blob, generate SAS URL.

    Observes EVIDENCE_EXPORT_DURATION Prometheus histogram (REQ-M8-07).
    On success: updates Redis job → {"status":"ready","download_url":...}
    On failure: updates Redis job → {"status":"failed"}
    """
    start_time = time.monotonic()
    try:
        await _update_job_state(app, job_id, {"status": "processing"}, redis_pool)

        # Query artifacts and audit_events for this run (tenant-scoped)
        import uuid as _uuid
        try:
            run_uuid = _uuid.UUID(run_id)
        except ValueError:
            run_uuid = None

        async with _db_session(tenant_id) as db:
            # Artifacts — run_id and tenant_id are UUID columns
            artifact_stmt = select(Artifact)
            if run_uuid is not None:
                artifact_stmt = artifact_stmt.where(Artifact.run_id == run_uuid)
            else:
                artifact_stmt = artifact_stmt.where(Artifact.run_id == run_id)

            try:
                tenant_uuid = _uuid.UUID(tenant_id)
                artifact_stmt = artifact_stmt.where(Artifact.tenant_id == tenant_uuid)
            except (ValueError, AttributeError):
                pass  # non-UUID tenant_id — let RLS handle isolation

            artifact_rows = (await db.execute(artifact_stmt)).scalars().all()
            artifacts = [
                {
                    "artifact_type": a.artifact_type,
                    "blob_url": a.blob_url or "",
                    "blob_path": a.blob_path or "",
                    "created_at": str(a.created_at),
                }
                for a in artifact_rows
            ]

            # Audit events — resource_id is a plain string column storing run_id
            event_rows = (
                await db.execute(
                    select(AuditEvent).where(
                        AuditEvent.resource_id == run_id,
                    ).order_by(AuditEvent.created_at)
                )
            ).scalars().all()
            audit_events = [
                {
                    "id": str(e.id),
                    "event_type": e.event_type or "",
                    "actor_id": e.actor_id or "",
                    "resource_id": e.resource_id or "",
                    "created_at": str(e.created_at),
                    "payload": json.dumps(e.payload) if e.payload else "{}",
                }
                for e in event_rows
            ]

        # Build ZIP with SHA-256 manifest
        zip_bytes, _zip_sha256 = build_evidence_zip(artifacts, audit_events)

        # Upload to Azure Blob — namespaced by tenant_id (T-M8-17)
        blob_name = f"{tenant_id}/evidence/{run_id}/{job_id}.zip"
        await blob_client.upload_bytes(zip_bytes, blob_name, content_type="application/zip")

        # Generate user-delegation SAS URL (T-M8-15, RESEARCH Pitfall 3)
        sas_url = await generate_evidence_sas_url(blob_client, blob_name, ttl_hours=24)

        elapsed = (time.monotonic() - start_time)
        EVIDENCE_EXPORT_DURATION.observe(elapsed)
        logger.info(
            "Evidence export complete: run_id=%s job_id=%s elapsed=%.2fs",
            run_id, job_id, elapsed,
        )

        await _update_job_state(
            app,
            job_id,
            {"status": "ready", "download_url": sas_url},
            redis_pool,
        )

    except Exception as exc:
        elapsed = (time.monotonic() - start_time)
        EVIDENCE_EXPORT_DURATION.observe(elapsed)
        logger.error(
            "Evidence export failed: run_id=%s job_id=%s error=%s",
            run_id, job_id, type(exc).__name__,
        )
        await _update_job_state(app, job_id, {"status": "failed"}, redis_pool)


async def _update_job_state(
    app: Any,
    job_id: str,
    state: dict[str, Any],
    redis_pool: Any,
) -> None:
    """Write job state to Redis (or app.state fallback for tests)."""
    raw = json.dumps(state)
    if redis_pool is not None:
        import redis.asyncio as aioredis
        redis_client = aioredis.Redis(connection_pool=redis_pool)
        await redis_client.set(f"evidence:job:{job_id}", raw, ex=_JOB_TTL_SECONDS)
    else:
        jobs: dict = getattr(app.state, "_evidence_jobs", {})
        jobs[job_id] = state
        app.state._evidence_jobs = jobs


from contextlib import asynccontextmanager
from shared.db import get_db_session_for_tenant


@asynccontextmanager
async def _db_session(tenant_id: str):
    """Open a tenant-scoped DB session for the background task."""
    async with get_db_session_for_tenant(tenant_id) as session:
        yield session
