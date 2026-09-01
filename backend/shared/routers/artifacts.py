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

from fastapi import APIRouter, Depends, HTTPException, Request, Response
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
from shared.services.artifact_store import is_blob_path

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


@artifacts_router.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Stream an artifact's stored bytes, scoped to the caller's tenant.

    THE ONE SAFE DOWNLOAD PATH, and it replaces an unsafe one. The Requirements agent
    used to expose `GET /sdlc/agent/requirement/download/{filename}` reading a flat,
    process-wide `outputs/` directory whose files are written under fixed names
    (`outputs/brd.docx`). Path traversal was guarded there, but nothing was
    tenant-scoped: one tenant's BRD overwrote another's, and whichever was on disk was
    served to any caller holding `artifact:view`.

    Here the identifier is an artifact id, and `_get_artifact_or_404` resolves it
    through a join on `Run.tenant_id` — so an id belonging to another tenant is a 404,
    not a download. `_assert_project_visible` then applies the same project-visibility
    rule the rest of this router uses, because being in your tenant is not the same as
    being on your project.

    404 rather than 403 for a cross-tenant id: a 403 would confirm the artifact exists.
    """
    tenant_id = request.state.tenant_id
    artifact, run = await _get_artifact_or_404(db, artifact_id, tenant_id)
    await _assert_project_visible(db, request, run.project_id)

    if not artifact.blob_path:
        # A row with no blob path predates blob storage, or was recorded by an agent
        # that only wrote the JSONB payload. Not an error worth a 500.
        raise HTTPException(status_code=404, detail="This artifact has no stored file")

    # LEGACY ROWS HOLD A LOCAL FILESYSTEM PATH, NOT A BLOB NAME. Before
    # chat_artifacts.register_generated_file went through artifact_store, it recorded
    # `blob_path` = the on-disk path and `blob_url` = a `/generated/...` static URL.
    # Passing either to download_bytes asks Azure for a blob literally named
    # `C:\pwc_work\...`, which surfaces as a 502 that reads like a storage outage.
    #
    # `is_blob_path` tests the tenant prefix, which is exactly what store_artifact
    # guarantees and what no local path satisfies. Answering 404 with a reason beats
    # both a misleading 502 and a silent empty download.
    if not is_blob_path(artifact.blob_path, str(tenant_id)):
        logger.info(
            "Artifact %s predates blob storage (path is local) — not downloadable",
            artifact_id,
        )
        raise HTTPException(
            status_code=404,
            detail="This artifact was generated before blob storage and has no "
                   "downloadable copy. Re-run the agent to produce a stored version.",
        )

    blob_client = getattr(request.app.state, "blob_client", None)
    if blob_client is None:
        # AZURE_BLOB_ACCOUNT_URL unset — the common local-dev state. Say which of the
        # two possible causes it is, rather than a bare 404 the caller must guess at.
        raise HTTPException(
            status_code=503,
            detail="Blob storage is not configured on this deployment",
        )

    try:
        data = await blob_client.download_bytes(artifact.blob_path)
    except Exception as exc:  # noqa: BLE001
        # Type name only: an Azure error can carry a SAS token or the account URL.
        logger.warning(
            "Artifact %s download failed: %s", artifact_id, type(exc).__name__
        )
        raise HTTPException(status_code=502, detail="Artifact could not be retrieved")

    # The filename shown to the browser is the LEAF of the stored path, which
    # artifact_store already sanitised on the way in — never a caller-supplied value,
    # so it cannot be used to inject a header.
    leaf = artifact.blob_path.rsplit("/", 1)[-1]
    return Response(
        content=data,
        media_type=artifact.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{leaf}"'},
    )


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
