"""Test case suites over HTTP: generate, run, follow the job, list the history, read a suite back.

Mounted at `/testing` beside the other workspace routers. Every route is scoped to its
`{project_id}` (`require_project_access`); starting work also requires use of the Testing
agent on that project — the same gate as its chat.
"""
from __future__ import annotations

import logging
import re
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from shared.authz.project_scope import require_project_access

logger = logging.getLogger(__name__)

testing_suites_router = APIRouter(dependencies=[Depends(require_project_access())])


class Target(BaseModel):
    ado_project: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    provider: Optional[str] = None


class GenerateRequest(BaseModel):
    target: Target
    kinds: list[Literal["unit", "functional", "api"]] = Field(default_factory=lambda: ["unit", "functional", "api"], min_length=1)
    offering_id: Optional[str] = None


async def _may_use_testing(request: Request, project_id: str) -> tuple[str, str, str]:
    """(tenant, user id, user's name) for a caller who may use the Testing agent here."""
    from shared.authz.agent_access import assert_agent_access_for_chat  # noqa: PLC0415
    from shared.authz.effective_role import actor_display_name  # noqa: PLC0415
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    tenant_id = str(getattr(request.state, "tenant_id", "") or "")
    user_id = str(getattr(request.state, "user_id", "") or "")
    async with get_db_session_for_tenant(tenant_id) as db:
        await assert_agent_access_for_chat(db, tenant_id=tenant_id, project_id=project_id, user_id=user_id,
                                           agent_id="testing")
        user_name = await actor_display_name(db, request)
    return tenant_id, user_id, user_name


@testing_suites_router.post("/{project_id}/suites/generate", status_code=202)
async def generate(project_id: str, body: GenerateRequest, request: Request) -> dict:
    """Start writing the suites for a branch. Returns the job to follow."""
    from agents_orchestrator.testing_agent.suites import jobs  # noqa: PLC0415
    from agents_orchestrator.testing_agent.suites.workflows import generate_workflow  # noqa: PLC0415

    tenant_id, user_id, user_name = await _may_use_testing(request, project_id)
    running = jobs.active_job(project_id, "generate")
    if running:
        raise HTTPException(status_code=409, detail="Test cases are already being generated for this project — follow that run.")
    kinds = list(dict.fromkeys(body.kinds))
    target = body.target.model_dump()
    job = jobs.start_job(
        kind="generate", project_id=project_id, tenant_id=tenant_id, user_id=user_id, user_name=user_name,
        params={"target": target, "kinds": kinds},
        work=lambda job: generate_workflow(job, target=target, kinds=kinds, offering_id=body.offering_id),
    )
    return job.public()


class RunRequest(BaseModel):
    #: The running application — required for functional and API suites.
    base_url: Optional[str] = None
    #: Functional runs open a visible browser unless asked not to.
    headless: bool = False
    offering_id: Optional[str] = None


_RUN_KIND = {"unit": "run_unit", "functional": "run_functional", "api": "run_api"}
_URL = re.compile(r"^https?://[^\s/]+")


@testing_suites_router.post("/{project_id}/suites/{document_id}/run", status_code=202)
async def run_suite(project_id: str, document_id: str, body: RunRequest, request: Request) -> dict:
    """Run the suite in a stored test case workbook; the job files its report."""
    from agents_orchestrator.testing_agent.suites import jobs  # noqa: PLC0415
    from agents_orchestrator.testing_agent.suites.excel import SuiteFormatError, read_suite  # noqa: PLC0415
    from agents_orchestrator.testing_agent.suites.store import document_bytes  # noqa: PLC0415
    from agents_orchestrator.testing_agent.suites.workflows import run_workflow  # noqa: PLC0415

    tenant_id, user_id, user_name = await _may_use_testing(request, project_id)
    try:
        _row, data = await document_bytes(tenant_id, project_id, document_id)
        meta, cases, _problems = read_suite(data)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SuiteFormatError as exc:
        raise HTTPException(status_code=422, detail=f"This document is not a runnable test case suite: {exc}") from exc
    if not cases:
        raise HTTPException(status_code=422, detail="This suite has no runnable test case.")
    base_url = (body.base_url or "").strip().rstrip("/")
    if meta.kind in ("functional", "api") and not _URL.match(base_url):
        raise HTTPException(status_code=422, detail="Enter the running application's URL, e.g. http://localhost:8080.")
    kind = _RUN_KIND[meta.kind]
    if jobs.active_job(project_id, kind):
        raise HTTPException(status_code=409, detail=f"A {meta.kind} run is already in progress for this project — follow that run.")
    job = jobs.start_job(
        kind=kind, project_id=project_id, tenant_id=tenant_id, user_id=user_id, user_name=user_name,
        params={"document_id": document_id, "base_url": base_url, "headless": body.headless, "suite_kind": meta.kind},
        work=lambda job: run_workflow(job, document_id=document_id, base_url=base_url or None,
                                      headless=body.headless, offering_id=body.offering_id),
    )
    return job.public()


@testing_suites_router.get("/{project_id}/suites/jobs")
async def list_suite_jobs(project_id: str, kind: Optional[str] = None, limit: int = 20) -> dict:
    from agents_orchestrator.testing_agent.suites import jobs  # noqa: PLC0415

    return {"jobs": [j.public() for j in jobs.list_jobs(project_id, kind=kind, limit=max(1, min(limit, 50)))]}


@testing_suites_router.get("/{project_id}/suites/jobs/{job_id}")
async def get_suite_job(project_id: str, job_id: str) -> dict:
    from agents_orchestrator.testing_agent.suites import jobs  # noqa: PLC0415

    job = jobs.get_job(project_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such run for this project.")
    return job.public()


@testing_suites_router.get("/{project_id}/suites/history")
async def list_history(project_id: str, limit: int = 20, offset: int = 0) -> dict:
    """What was generated and run before, one entry per generation, latest activity first."""
    from agents_orchestrator.testing_agent.suites import jobs  # noqa: PLC0415
    from agents_orchestrator.testing_agent.suites.history import build_history  # noqa: PLC0415

    entries = build_history(jobs.all_jobs(project_id))
    limit, offset = max(1, min(limit, 50)), max(0, offset)
    return {"entries": entries[offset:offset + limit], "total": len(entries)}


@testing_suites_router.get("/{project_id}/suites/history/{entry_id}")
async def get_history_entry(project_id: str, entry_id: str) -> dict:
    """One history entry — a generation and its runs — to open in the page."""
    from agents_orchestrator.testing_agent.suites import jobs  # noqa: PLC0415
    from agents_orchestrator.testing_agent.suites.history import build_history  # noqa: PLC0415

    entry = next((e for e in build_history(jobs.all_jobs(project_id)) if e["id"] == entry_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Nothing in this project's testing history has that id.")
    return entry


@testing_suites_router.get("/{project_id}/suites/{document_id}")
async def read_suite_document(project_id: str, document_id: str, request: Request) -> dict:
    """The suite in a stored test case workbook — what a run of it would execute."""
    from agents_orchestrator.testing_agent.suites.excel import SuiteFormatError, read_suite  # noqa: PLC0415
    from agents_orchestrator.testing_agent.suites.store import document_bytes  # noqa: PLC0415

    tenant_id = str(getattr(request.state, "tenant_id", "") or "")
    try:
        row, data = await document_bytes(tenant_id, project_id, document_id)
        meta, cases, problems = read_suite(data)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SuiteFormatError as exc:
        raise HTTPException(status_code=422, detail=f"This document is not a runnable test case suite: {exc}") from exc
    return {
        "documentId": str(row.id), "status": row.approval_status or "draft",
        "meta": meta.model_dump(), "cases": [c.model_dump() for c in cases], "problems": problems,
    }
