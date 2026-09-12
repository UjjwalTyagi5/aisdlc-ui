"""Read Track 3's recorded work for a project: the migration-intent brief and the
Dependency and Risk.

What the two agents record lands on a run's own column (`migration_intent_payload`,
`discovery_artifacts` — migration 0057), on whichever run the conversation belonged
to: the project's standalone chat run for that stage, or an Orchestrator run. The
project's pages want THE CURRENT one, so each endpoint returns the newest run that
holds a value — newest by `updated_at`, because a chat run is created once and reused,
so its creation time says when the chat was first opened, not when this was written.

Also here, for the two pages:

  legacy-code    the project's pulled legacy code (`modernization_common.legacy_code`):
                 its status, starting a pull, and the repositories the project's
                 connection can see, for the picker.
  export         any frozen version of the brief or the assessment as .docx or .pdf,
                 rendered from the version's own payload when it is asked for.

Guarded exactly like the agents themselves: the caller must be a member of the
project, the agent must belong to the project's track, and the caller's role must
reach the agent (`assert_agent_access_for_chat_on_track`). Reading an agent's output
is using the agent's output, and pulling its code is feeding it.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from shared.authz.agent_access import assert_agent_access_for_chat_on_track
from shared.db import get_db_session
from shared.models.orm import Run

modernization_router = APIRouter()

#: Page kind (URL segment) -> backend stage.
_KINDS = {"migration-intent": "requirements_modernization", "discovery": "discovery"}
_MEDIA = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


async def _latest(db: AsyncSession, request: Request, project_id: str, agent_id: str, column: str) -> dict:
    tenant_id = str(getattr(request.state, "tenant_id", "") or "")
    user_id = str(getattr(request.state, "user_id", "") or "")
    resolved = await assert_agent_access_for_chat_on_track(
        db, tenant_id=tenant_id, project_id=project_id, user_id=user_id, agent_id=agent_id,
    )
    field = getattr(Run, column)
    row = (
        await db.execute(
            select(Run.id, Run.updated_at, field)
            .where(
                Run.project_id == uuid.UUID(resolved),
                Run.tenant_id == uuid.UUID(tenant_id),
                field.is_not(None),
            )
            .order_by(Run.updated_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return {"projectId": resolved, "runId": None, "updatedAt": None, "payload": None}
    return {
        "projectId": resolved,
        "runId": str(row[0]),
        "updatedAt": row[1].isoformat() if row[1] else None,
        "payload": row[2],
    }


@modernization_router.get("/projects/{project_id}/modernization/migration-intent")
async def latest_migration_intent(
    project_id: str, request: Request, db: AsyncSession = Depends(get_db_session),
) -> dict:
    """The project's current migration-intent brief, or `payload: null`."""
    return await _latest(db, request, project_id, "requirements_modernization", "migration_intent_payload")


@modernization_router.get("/projects/{project_id}/modernization/discovery")
async def latest_discovery_assessment(
    project_id: str, request: Request, db: AsyncSession = Depends(get_db_session),
) -> dict:
    """The project's current Dependency and Risk assessment, or `payload: null`."""
    return await _latest(db, request, project_id, "discovery", "discovery_artifacts")


# ── legacy code ──────────────────────────────────────────────────────────────


class PullBody(BaseModel):
    url: str
    branch: str = ""
    #: The page it was pulled from — whose access is checked, and whose connection is
    #: tried first for a private repository's credential.
    stage: str = "requirements_modernization"


async def _guard(db: AsyncSession, request: Request, project_id: str, stage: str) -> tuple[str, str, str]:
    """(project_id, tenant_id, user_id) once the caller may use `stage` on this project."""
    from agents_orchestrator.modernization_common.legacy_code import TRACK3_STAGES  # noqa: PLC0415

    if stage not in TRACK3_STAGES:
        raise HTTPException(status_code=404, detail=f"Unknown Code Modernization stage {stage!r}.")
    tenant_id = str(getattr(request.state, "tenant_id", "") or "")
    user_id = str(getattr(request.state, "user_id", "") or "")
    resolved = await assert_agent_access_for_chat_on_track(
        db, tenant_id=tenant_id, project_id=project_id, user_id=user_id, agent_id=stage,
    )
    return resolved, tenant_id, user_id


@modernization_router.get("/projects/{project_id}/modernization/legacy-code")
async def get_legacy_code(
    project_id: str, request: Request, stage: str = "requirements_modernization",
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """The project's pulled legacy code: the latest attempt's status and the last good
    pull (repository, commit, profile). `status: none` before anything was pulled."""
    from agents_orchestrator.modernization_common import legacy_code  # noqa: PLC0415

    resolved, _tenant, _user = await _guard(db, request, project_id, stage)
    return {"projectId": resolved, **legacy_code.public_record(resolved)}


@modernization_router.post("/projects/{project_id}/modernization/legacy-code")
async def pull_legacy_code(
    project_id: str, body: PullBody, request: Request, db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Start pulling the legacy repository (read-only, shallow). Returns at once with
    `status: pulling`; the page polls the GET until it is `ready` or `failed`."""
    from agents_orchestrator.modernization_common import legacy_code  # noqa: PLC0415

    resolved, tenant_id, user_id = await _guard(db, request, project_id, body.stage)
    try:
        record = await legacy_code.start_pull(
            tenant_id=tenant_id, project_id=resolved, user_id=user_id,
            url=body.url, branch=body.branch.strip(), stage=body.stage,
        )
    except legacy_code.PullRefused as exc:
        status = 409 if "already running" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"projectId": resolved, **record}


@modernization_router.get("/projects/{project_id}/modernization/legacy-code/repositories")
async def list_legacy_code_repositories(
    project_id: str, request: Request, stage: str = "requirements_modernization",
    ado_project: str = "", db: AsyncSession = Depends(get_db_session),
) -> dict:
    """The repositories the connection wired to THIS page's stage can see, for the Pull
    dialog's picker. Only that stage's connection, at its effective access — the same
    three checks every connector use passes (Business Unit grant, stage wiring, a level
    that admits read); never the other Code Modernization stage's. `problem` explains an
    empty answer."""
    from agents_orchestrator.discovery_agent.tools.repo_tools import repositories_data  # noqa: PLC0415
    from agents_orchestrator.modernization_common.legacy_code import (  # noqa: PLC0415
        connection_refusal,
        stage_may_read,
    )
    from agents_orchestrator.orchestrator2.connectors import bound_connector  # noqa: PLC0415

    resolved, tenant_id, user_id = await _guard(db, request, project_id, stage)
    async with bound_connector(stage, tenant_id=tenant_id, project_id=resolved, owner_id=user_id) as bound:
        if not bound or not stage_may_read():
            return {"provider": "", "problem": connection_refusal(stage)}
        data = await repositories_data(ado_project.strip())
    if data.get("repositories") is None and data.get("projects") is None and not data.get("problem"):
        return {"provider": "", "problem": connection_refusal(stage)}
    return data  # the list, or what actually went wrong reaching it


# ── exports ──────────────────────────────────────────────────────────────────


def _version_markdown(stage: str, payload: dict) -> tuple[str, str]:
    """(markdown, file stem) for a frozen version's payload."""
    if stage == "requirements_modernization":
        from agents_orchestrator.requirements_modernization_agent.brief import brief_markdown  # noqa: PLC0415
        from shared.models.artifacts import MigrationIntentArtifact  # noqa: PLC0415

        return brief_markdown(MigrationIntentArtifact(**payload)), "migration-intent-brief"
    from agents_orchestrator.discovery_agent.analysis.assessment import assessment_markdown  # noqa: PLC0415

    return assessment_markdown(payload), "discovery-assessment"


@modernization_router.get("/projects/{project_id}/modernization/{kind}/versions/{version}/export")
async def export_version(
    project_id: str, kind: str, version: int, request: Request, format: str = "docx",
    db: AsyncSession = Depends(get_db_session),
):
    """One frozen version of the brief or the assessment as a .docx or .pdf file,
    rendered from that version's payload — never from whatever is newest."""
    from shared.services import artifact_versions as svc  # noqa: PLC0415
    from shared.tools.doc_export import render_document  # noqa: PLC0415

    stage = _KINDS.get(kind)
    if stage is None:
        raise HTTPException(status_code=404, detail=f"Unknown kind {kind!r}.")
    fmt = (format or "").lower().lstrip(".")
    if fmt not in _MEDIA:
        raise HTTPException(status_code=400, detail="format must be docx or pdf")
    resolved, _tenant, _user = await _guard(db, request, project_id, stage)
    row = await svc.get_version(db, resolved, stage, version)
    if row is None or row.payload is None:
        raise HTTPException(status_code=404, detail=f"v{version} not found")
    try:
        markdown, stem = _version_markdown(stage, row.payload)
    except Exception as exc:  # noqa: BLE001 — an old payload the renderer cannot read
        raise HTTPException(status_code=422, detail=f"This version cannot be rendered ({type(exc).__name__}).") from exc

    workdir = tempfile.mkdtemp(prefix="t3-export-")
    filename = f"{stem}-v{version}.{fmt}"
    path = os.path.join(workdir, filename)
    try:
        if stage == "requirements_modernization":
            # The designed brief — title band, change table, timeline — not generic Markdown.
            import asyncio  # noqa: PLC0415

            from agents_orchestrator.requirements_modernization_agent.brief_document import (  # noqa: PLC0415
                render_brief,
            )
            from shared.models.artifacts import MigrationIntentArtifact  # noqa: PLC0415

            await asyncio.to_thread(render_brief, MigrationIntentArtifact(**row.payload), path,
                                    {"version": version, "status": {"published": "approved"}.get(row.status, row.status)})
        else:
            await render_document(markdown, path, title=f"{stem.replace('-', ' ').title()} v{version}")
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Rendering the {fmt} failed ({type(exc).__name__}).") from exc
    return FileResponse(
        path, media_type=_MEDIA[fmt], filename=filename,
        background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
    )
