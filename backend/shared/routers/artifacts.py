"""Artifacts resource router.

Exposes read operations for the Artifact model plus an accepted-but-no-op PATCH
that honours the immutability decision.  All routes are JWT-protected (NOT in
_EXEMPT_PATHS) and scope every query by request.state.tenant_id.

Routes (absolute paths — registered without a router prefix):
  GET   /projects/{project_id}/artifacts  — list artifacts for a project
  GET   /artifacts/{id}                   — artifact detail
  PATCH /artifacts/{id}                   — immutability stub (no mutation)

Threat mitigations (T-M4-05):
  - Artifact query joined to Run.tenant_id == request.state.tenant_id so
    cross-tenant reads are impossible: a valid JWT for tenant B cannot see
    tenant A's artifacts.
  - Routes NOT in _EXEMPT_PATHS (JWT middleware enforces 401 without token).
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.can_perform import visible_project_ids
from shared.authz.dependency import require_permission
from shared.authz.read_scope import is_org_wide
from shared.db import get_db_session
from shared.models.orm import Artifact, Run
from shared.routers._schemas import ArtifactOut, story_artifacts_from_run
from shared.routers.projects import _get_or_404

logger = logging.getLogger(__name__)

artifacts_router = APIRouter()


class ArtifactPatchIn(BaseModel):
    """Accepted fields for PATCH — all are silently ignored (immutability decision).

    ORM-gap decision 2: The ORM Artifact is blob-backed and immutable in M4.
    Title/status edits sent by the UI are accepted and logged but NOT persisted.
    A mutable metadata table is deferred to a future milestone.
    """
    title: Optional[str] = None
    status: Optional[str] = None


async def _assert_project_visible(db: AsyncSession, request: Request, project_id) -> None:
    """Refuse an artifact read for a project the caller cannot see.

    The tenant join stops a cross-TENANT read; it does nothing about a cross-PROJECT one
    inside the same organisation, and artifacts are the requirements, designs and code
    the pipeline produces. `runs.py` guards its equivalents through `_get_run_or_404`;
    these two were missed. See finding 4 in docs/rbac-audit-2026-08-17.md.

    404, matching the sibling guards: a project the caller cannot reach is not confirmed
    to exist by the error code.
    """
    if is_org_wide(request):
        return
    visible = await visible_project_ids(
        db,
        user_id=getattr(request.state, "user_id", "") or "",
        tenant_id=str(request.state.tenant_id),
    )
    if visible is not None and str(project_id) not in visible:
        raise HTTPException(status_code=404, detail="not found")


@artifacts_router.get("/projects/{project_id}/artifacts", response_model=List[ArtifactOut])
async def list_artifacts_for_project(
    project_id: str,
    request: Request,
    phase: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
):
    """Return all artifacts for a project, optionally filtered by phase.

    Tenant-scoped: joins Artifact -> Run and filters by Run.tenant_id ==
    request.state.tenant_id so cross-tenant reads are rejected (T-M4-05).
    The phase filter maps to the owning Run's stage column.
    """
    tenant_id = request.state.tenant_id

    # Resolve UUID-or-slug to the real project (frontend phase routes are
    # slug-based; slug is a derived, non-queryable field — Plan 02). 404 on miss.
    project = await _get_or_404(db, project_id, tenant_id)
    await _assert_project_visible(db, request, project.id)

    stmt = (
        select(Artifact, Run)
        .join(Run, Artifact.run_id == Run.id)
        .where(Run.project_id == project.id)
        .where(Run.tenant_id == tenant_id)
    )
    if phase:
        stmt = stmt.where(Run.stage == phase)

    rows = (await db.execute(stmt)).all()
    result = [
        ArtifactOut.from_orm_artifact(artifact, run.stage, str(run.project_id))
        for artifact, run in rows
    ]

    # Materialize board-ingested stories (no structured-story table — they live in
    # Run.requirements_payload). Use the most recent requirements run that has
    # stories so re-ingests don't duplicate. Only for the requirements phase.
    if phase in (None, "requirements"):
        req_runs = (
            await db.execute(
                select(Run)
                .where(Run.project_id == project.id)
                .where(Run.tenant_id == tenant_id)
                .where(Run.stage == "requirements")
                .order_by(Run.created_at.desc())
            )
        ).scalars().all()
        for r in req_runs:
            synth = story_artifacts_from_run(r, str(project.id))
            if synth:
                result.extend(synth)
                break

    return result


@artifacts_router.get("/artifacts/{artifact_id}", response_model=ArtifactOut)
async def get_artifact(
    artifact_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Return a single artifact by ID.

    Tenant-scoped via Run join: returns 404 if the artifact does not belong to
    the requesting tenant's runs, preventing cross-tenant information disclosure
    (T-M4-05).
    """
    tenant_id = request.state.tenant_id
    artifact, run = await _get_artifact_or_404(db, artifact_id, tenant_id)
    await _assert_project_visible(db, request, run.project_id)
    return ArtifactOut.from_orm_artifact(artifact, run.stage, str(run.project_id))


@artifacts_router.patch("/artifacts/{artifact_id}", response_model=ArtifactOut)
async def patch_artifact(
    artifact_id: str,
    body: ArtifactPatchIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Accepted-but-no-op PATCH — artifacts are immutable in M4.

    ORM-gap decision 2: The ORM Artifact table has no updated_at column and
    blobs are immutable after creation.  This endpoint accepts the request (200),
    logs any field values the client sent, and returns the unchanged ArtifactOut.
    A mutable metadata layer (title/status overrides) is deferred to a future
    milestone.
    """
    tenant_id = request.state.tenant_id
    artifact, run = await _get_artifact_or_404(db, artifact_id, tenant_id)
    if body.title is not None or body.status is not None:
        logger.info(
            "PATCH /artifacts/%s: title/status update accepted but NOT persisted — "
            "artifacts are immutable in M4 (ORM-gap decision 2). "
            "Received: title=%r status=%r",
            artifact_id,
            body.title,
            body.status,
        )
    return ArtifactOut.from_orm_artifact(artifact, run.stage, str(run.project_id))


class ExportDocxIn(BaseModel):
    """Body for POST /artifacts/export-docx — the artifact's rendered markdown."""
    title: str = "Document"
    markdown: str = ""


@artifacts_router.post(
    "/artifacts/export-docx",
    # Data leaving the platform is what `artifact:export` names, and it was enforced
    # nowhere while being granted to all nine delivery roles. Reading an artifact and
    # exporting a copy of it are different acts; only the first is the view floor.
    dependencies=[Depends(require_permission("artifact:export"))],
)
async def export_artifact_docx(body: ExportDocxIn, request: Request):
    """Render an artifact's markdown to a Word (.docx) file and stream it back.

    The client posts the artifact's already-visible title + markdown (the panel
    already holds it); we reuse the shared markdown→docx converter so the result
    is a properly formatted Word document (headings, tables, code, lists). No DB
    lookup needed — the caller is JWT-protected (router-level view dependency) and
    is only converting content it can already see. Fully self-contained (images /
    mermaid are left as-is; no network fetch)."""
    import os
    import re
    import tempfile
    from io import BytesIO

    from fastapi.responses import StreamingResponse
    from shared.tools.docx_tools import markdown_to_docx

    md = (body.markdown or "").strip()
    if not md:
        raise HTTPException(status_code=400, detail="Nothing to export — the document is empty.")

    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    try:
        await markdown_to_docx(md, path)
        with open(path, "rb") as fh:
            data = fh.read()
    except Exception as exc:  # noqa: BLE001 — surface a clean 500, never a stack trace
        logger.warning("export_artifact_docx failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not generate the Word document.")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    safe = re.sub(r"[^A-Za-z0-9._ -]+", "", body.title or "document").strip()[:80] or "document"
    return StreamingResponse(
        BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe}.docx"'},
    )


async def _get_artifact_or_404(
    db: AsyncSession, artifact_id: str, tenant_id: str
) -> tuple[Artifact, Run]:
    """Fetch an Artifact + its owning Run, scoped to tenant_id.

    Returns (Artifact, Run) tuple; raises 404 if not found or cross-tenant
    access is attempted (T-M4-05).
    """
    result = await db.execute(
        select(Artifact, Run)
        .join(Run, Artifact.run_id == Run.id)
        .where(Artifact.id == artifact_id)
        .where(Run.tenant_id == tenant_id)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return row[0], row[1]
