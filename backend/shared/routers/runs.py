"""Runs resource router.

Exposes read and create operations for the Run model plus step derivation and
approval recording. All routes are JWT-protected (NOT in _EXEMPT_PATHS) and
scope every query by request.state.tenant_id.

Routes:
  POST /runs                   — create run + start SDLCWorkflow (SC-01, D-09)
  GET  /runs                   — paginated list (query: project_id, status, page, page_size)
  GET  /runs/{id}              — detail (SC-03: surfaces temporalWorkflowId + awaiting state)
  GET  /runs/{id}/steps        — derive Step[] from JSONB columns (no steps table — ORM-gap 4)
  POST /runs/{id}/approvals    — record an AuditEvent approval decision

Threat mitigations (T-M4-01, T-M4-02, T-M4-03, T-M5-21, T-M5-23):
  - All queries filtered by tenant_id (no cross-tenant reads)
  - Routes not in _EXEMPT_PATHS (JWT middleware enforces 401 without token)
  - Approval mutation scoped by tenant_id + 404 guard
  - POST /runs scopes Run to tenant_id (T-M5-21)
  - Uses cached app.state.temporal_client — no per-request Client.connect() (T-M5-23)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import delete, false as sa_false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.env import ENABLE_TEMPORAL, TEMPORAL_TASK_QUEUE
from shared.authz.can_perform import can_perform, visible_project_ids
from shared.authz.dependency import require_permission
from shared.db import get_db_session
from shared.models.orm import Artifact, AuditEvent, Project, Run

logger = logging.getLogger(__name__)
from shared.models.workflow_models import SDLCWorkflowInput
from shared.routers._schemas import (
    ApprovalOut,
    Paginated,
    Pagination,
    RunCreateIn,
    RunCreateOut,
    RunOut,
    StepOut,
    _iso,
    derive_steps_from_run,
)
from workflows.sdlc_workflow import SDLCWorkflow

runs_router = APIRouter()

# Aliases so the scope helpers read the same way here as in the other routers.
_uuid = uuid


def _user_id(request: Request) -> str:
    return getattr(request.state, "user_id", "") or ""


class ApprovalIn(BaseModel):
    decision: str
    reason: Optional[str] = None
    idempotencyKey: Optional[str] = None


@runs_router.post(
    "",
    response_model=RunCreateOut,
    status_code=201,
    dependencies=[Depends(require_permission("run:create"))],
)
async def create_run(
    body: RunCreateIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Create a Run and start the SDLCWorkflow via Temporal (SC-01).

    When ENABLE_TEMPORAL=false (D-09 drain path), returns 503 — no new runs
    are created via this endpoint; the old in-process execution path handles
    them through the agent WebSocket routes.

    When enabled:
      1. Creates a Run row scoped to the requesting tenant (T-M5-21).
      2. Starts SDLCWorkflow via the CACHED Temporal client — no per-request
         Client.connect() (T-M5-23).
      3. Persists temporal_workflow_id + temporal_run_id on the Run row.
      4. Returns RunCreateOut(runId, temporalWorkflowId) with HTTP 201.
    """
    # Conversational runs don't touch Temporal — they must NOT 503 when it's disabled.
    if not ENABLE_TEMPORAL and not body.conversational:
        raise HTTPException(
            status_code=503,
            detail="Temporal not enabled (ENABLE_TEMPORAL=false) — drain-only mode",
        )

    tenant_id = request.state.tenant_id

    # Hierarchical budget gate (org ⊇ workspace ⊇ project): reject a new run at dispatch
    # when any scope's monthly spend is already at/over its budget (409 with the scope).
    from shared.services.budget_guard import BudgetExceededError, check_budgets
    try:
        await check_budgets(tenant_id, str(body.project_id) if body.project_id else None)
    except BudgetExceededError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # Resolve the exact offering up front so the whole run dispatches against ONE
    # provider connection + key (never first-match), and record it for audit.
    resolved_model_id = body.model_id
    resolved_offering_id = body.offering_id
    resolved_display_name = None
    if body.offering_id or body.model_id:
        from shared.services.model_resolver import (
            resolve_model_for_run, NoModelConfiguredError, ModelNotEnabledError)
        try:
            resolved = await resolve_model_for_run(
                tenant_id, body.model_id, offering_id=body.offering_id,
                project_id=str(body.project_id) if body.project_id else None)
        except ModelNotEnabledError:
            raise HTTPException(status_code=422, detail="Selected model is not enabled for your organization")
        except NoModelConfiguredError:
            raise HTTPException(status_code=409, detail="No model provider configured for your organization")
        except BudgetExceededError as e:
            raise HTTPException(status_code=409, detail=str(e))
        resolved_model_id = resolved.model
        resolved_offering_id = resolved.offering_id
        resolved_display_name = resolved.display_name

    # ── Conversational (chat-driven) run — NO Temporal ────────────────────────
    # The Orchestrator Copilot drives each stage's agent conversationally; gate
    # approvals advance the run via POST /runs/{id}/copilot/advance. We create the
    # row already "running" at the first stage with no gate pending, and skip the
    # workflow start entirely. The gate appears later, once the active stage's
    # agent produces its artifact (Copilot WS gate detection).
    if body.conversational:
        conv_run = Run(
            project_id=body.project_id,
            tenant_id=tenant_id,
            status="running",
            stage="requirements",
            current_stage="requirements",
            gate_pending=False,
            model_id=resolved_model_id,
            offering_id=resolved_offering_id,
            model_display_name=resolved_display_name,
        )
        db.add(conv_run)
        await db.commit()
        # No temporalWorkflowId for a conversational run — empty string keeps the
        # response schema stable (the Copilot never signals Temporal for this run).
        return RunCreateOut(runId=str(conv_run.id), temporalWorkflowId="")

    client = request.app.state.temporal_client

    run = Run(
        project_id=body.project_id,
        tenant_id=tenant_id,
        status="queued",
        stage="requirements",
        model_id=resolved_model_id,
        offering_id=resolved_offering_id,
        model_display_name=resolved_display_name,
    )
    db.add(run)
    await db.flush()  # populate run.id without committing

    # Thread the project's stage→MCP mapping into the run so each agent activity
    # injects exactly the servers chosen for its stage at project creation.
    project_mcp = None
    project_connectors = None
    if run.project_id:
        _proj = await db.get(Project, run.project_id)
        project_mcp = (_proj.mcp_servers if _proj else None) or None
        project_connectors = (_proj.connectors if _proj else None) or None

    from workflows.execution_plan import build_execution_plan

    plan = build_execution_plan(
        run_id=str(run.id),
        project_id=str(run.project_id),
        mode="pipeline",
        active_agents=body.active_agents,
        skip_agents=body.skip_agents,
    )

    workflow_id = f"sdlc-run-{run.id}"
    handle = await client.start_workflow(
        SDLCWorkflow.run,
        SDLCWorkflowInput(
            run_id=str(run.id),
            project_id=str(run.project_id),
            tenant_id=str(tenant_id),
            trigger=body.trigger,
            work_item_id=body.work_item_id,
            model_id=resolved_model_id,
            offering_id=resolved_offering_id,
            execution_plan=plan.model_dump(),
            mcp_servers=project_mcp,
            connectors=project_connectors,
            change_request=body.change_request,
        ),
        id=workflow_id,
        task_queue=TEMPORAL_TASK_QUEUE,
    )

    run.temporal_workflow_id = workflow_id
    run.temporal_run_id = handle.first_execution_run_id
    run.status = "running"
    await db.commit()

    return RunCreateOut(runId=str(run.id), temporalWorkflowId=workflow_id)


@runs_router.get("", response_model=Paginated[RunOut])
async def list_runs(
    request: Request,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db_session),
):
    """Paginated runs, narrowed to the projects this caller may see."""
    tenant_id = request.state.tenant_id

    stmt = select(Run).where(Run.tenant_id == tenant_id)

    # Runs were scoped by tenant ALONE — this module did not reference role_bindings
    # at all, so any authenticated person could list every run in the organization,
    # including their stages, statuses and the projects they belong to. The Next.js
    # tier filtered them by project afterwards; nothing did on this side.
    #
    # Applied before paging, for the same reason as the projects list: filtering after
    # would return short pages and a total describing runs the caller cannot open.
    visible = await visible_project_ids(
        db, user_id=_user_id(request), tenant_id=str(tenant_id)
    )
    if visible is not None:
        if not visible:
            # No bindings: no runs. Expressed as a false predicate rather than an
            # empty IN (), which some planners treat as unconstrained.
            stmt = stmt.where(sa_false())
        else:
            stmt = stmt.where(Run.project_id.in_([_uuid.UUID(p) for p in visible]))

    if project_id:
        stmt = stmt.where(Run.project_id == project_id)
    if status:
        stmt = stmt.where(Run.status == status)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total: int = (await db.execute(count_stmt)).scalar_one()

    stmt = stmt.order_by(Run.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    return Paginated(
        items=[RunOut.from_orm_run(r) for r in rows],
        pagination=Pagination(page=page, pageSize=page_size, total=total),
    )


@runs_router.get("/worker-status")
async def worker_status(request: Request):
    """Report whether a Temporal worker is polling the task queue.

    The UI uses this to warn when a run can't progress because no worker is
    running (the dominant cause of a run stuck at "running" with no activity).
    Defined BEFORE GET /{run_id} so the literal path isn't captured as a run id.
    Returns worker_available=None when the state can't be determined (probe failed
    / Temporal disabled) so the UI shows no false warning.
    """
    if not ENABLE_TEMPORAL:
        return {"worker_available": None, "pollers": 0, "reason": "temporal_disabled"}
    client = getattr(request.app.state, "temporal_client", None)
    if client is None:
        return {"worker_available": None, "pollers": 0, "reason": "no_client"}
    try:
        from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
        from temporalio.api.taskqueue.v1 import TaskQueue
        from temporalio.api.enums.v1 import TaskQueueType

        resp = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=client.namespace,
                task_queue=TaskQueue(name=TEMPORAL_TASK_QUEUE),
                task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
            )
        )
        pollers = len(resp.pollers)
        return {"worker_available": pollers > 0, "pollers": pollers}
    except Exception as exc:  # noqa: BLE001
        logger.warning("worker_status: describe_task_queue failed: %s", type(exc).__name__)
        return {"worker_available": None, "pollers": 0, "reason": "probe_failed"}


@runs_router.get("/{run_id}", response_model=RunOut)
async def get_run(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Return a single run by ID, scoped to the requesting tenant."""
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)
    return RunOut.from_orm_run(run)


@runs_router.get("/{run_id}/artifacts")
async def get_run_artifacts(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Panel-ready artifact list for a run (reload/replay). Tenant-scoped."""
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)
    from shared.services.orchestrator.artifacts_view import sections_from_run
    return {"artifacts": sections_from_run(run)}


@runs_router.get("/{run_id}/transcript")
async def get_run_transcript(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Persisted Copilot conversation for a run (session_id == run_id), so reopening a
    run replays its chat. Tenant-scoped; returns [] on any miss."""
    tenant_id = request.state.tenant_id
    await _get_run_or_404(db, run_id, tenant_id, request=request)  # 404 + tenant guard
    from shared.services.conversation_service import get_transcript
    rows = await get_transcript(run_id, tenant_id=tenant_id)
    messages = []
    for r in rows:
        role = "user" if r.get("role") == "user" else "agent"
        messages.append({
            "role": role,
            "content": r.get("content") or "",
            "stage": r.get("author_id") if role == "agent" else None,
        })
    return {"messages": messages}


async def _run_dev_work_dir(
    run_id: str,
    dev_artifacts: Optional[dict] = None,
    tenant_id: str = "",
) -> Optional[str]:
    """The dev agent clones into a run-keyed workspace (session_id == run_id).

    Prefer the in-memory session work_dir; fall back to the on-disk path
    files/<user>/orchestrator/<run_id>/project so the code panel survives a backend
    restart (the session is in-memory but the clone persists on disk). If both of
    those are gone (e.g. a restart wiped the legacy `files/` clone too, or this run
    only ever cloned into the Bridge's run-keyed workspace), fall back to the Bridge's
    `<DEV_WORKSPACE_ROOT>/run/<run_id>/repo` clone. As a last resort — nothing on disk
    at all — re-clone it from the persisted `runs.development_artifacts`
    (repo_url/branch_name/base_sha), the same info the Bridge used to prepare it the
    first time. *dev_artifacts* / *tenant_id* are passed in by the caller (already has
    the Run row + tenant from `_get_run_or_404`) rather than re-read here. Fail-soft:
    None if nothing is cloned and nothing can be re-cloned."""
    try:
        from agents_orchestrator.development_agent.config.session_state import get_session
        wd = getattr(get_session(run_id), "work_dir", None)
        if wd and os.path.isdir(wd):
            return wd
    except Exception:  # noqa: BLE001
        pass
    try:
        import glob
        from agents_orchestrator.development_agent.tools.file_tools import _FILES_DIR
        hits = glob.glob(os.path.join(_FILES_DIR, "*", "orchestrator", run_id, "project"))
        for h in hits:
            if os.path.isdir(os.path.join(h, ".git")) or os.listdir(h):
                return h
    except Exception:  # noqa: BLE001
        pass
    try:
        from shared.services.run_workspace import _is_existing_clone, _run_dir
        run_dir = _run_dir(run_id)
        if _is_existing_clone(run_dir):
            return str(run_dir)
    except Exception:  # noqa: BLE001
        pass
    if not dev_artifacts:
        return None
    repo_url = dev_artifacts.get("repo_url")
    if not repo_url:
        return None
    try:
        from shared.services import ado_repos
        from shared.services.run_workspace import prepare_run_workspace
        _org, pat = await ado_repos.resolve_auth(tenant_id or "")
        ws = await prepare_run_workspace(
            run_id,
            repo_url,
            ref=dev_artifacts.get("branch_name"),
            base=dev_artifacts.get("base_sha") or dev_artifacts.get("target_branch"),
            pat=pat,
        )
        return ws.work_dir
    except Exception:  # noqa: BLE001
        return None


def _stage_files_dir() -> str:
    """Root all per-user orchestrator artifacts live under (`platform/backend/files`).

    Thin indirection over the same constant `_run_dev_work_dir` globs against, so
    tests can monkeypatch a tmp root instead of touching the real FILES dir.
    """
    from agents_orchestrator.development_agent.tools.file_tools import _FILES_DIR
    return _FILES_DIR


def _docs_output_root() -> str:
    """Root the Documentation agent saves generated docs under. Indirection for tests."""
    from config.env import DOCS_OUTPUT_ROOT
    return DOCS_OUTPUT_ROOT


def _glob_user_scoped_dir(run_id: str, rel_suffix: str, *, segment: str = "orchestrator") -> Optional[str]:
    """Resolve `{FILES}/<user>/<segment>/<run_id>/<rel_suffix>` for whichever user
    populated it (mirrors the glob `_run_dev_work_dir` uses for the dev clone) — the
    stage endpoints don't know the acting user, only the run_id. Returns the first
    existing directory hit, or None."""
    try:
        import glob
        hits = glob.glob(os.path.join(_stage_files_dir(), "*", segment, run_id, *rel_suffix.split("/")))
        for h in hits:
            if os.path.isdir(h):
                return h
    except Exception:  # noqa: BLE001
        pass
    return None


# Stages whose generated output isn't in its own dedicated location (dev workspace,
# testing's `output/`, docs' own root) yet — populated under a shared `generated/<stage>`
# tree once each agent starts writing there (later task). Fail-soft: None until then.
_GENERATED_STAGE_DIRS = {"security", "code_review", "deployment"}


async def _run_stage_output_dir(
    run_id: str,
    stage: str,
    development_artifacts: Optional[dict] = None,
    tenant_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve the on-disk directory holding *stage*'s generated files for this run,
    so the Copilot artifacts panel can browse ANY downstream agent's output the same
    way it already browses Development's workspace (reuses `workspace_fs`).

    development   -> delegates to `_run_dev_work_dir` (cloned repo, not a "generated"
                     dir — has its own richer fallback chain incl. re-clone).
    requirements  -> `{FILES}/<user>/requirements_agent/<run_id>/output` (planning.py
                     markdown_to_docx / markdowntodoc — BRD/MoM/Risk Register docx).
    testing       -> `{FILES}/<user>/orchestrator/<run_id>/output` (Nodes/finalize.py).
    documentation -> `{DOCS_OUTPUT_ROOT}/<project_id>/<run_id>` (doc_tools._output_dir).
    security | code_review | deployment
                  -> `{FILES}/<user>/orchestrator/<run_id>/generated/<stage>` (a later
                     task wires these agents to write here — None until they do).
    unknown stage or nothing generated yet -> None. Fail-soft throughout.
    """
    if stage == "development":
        return await _run_dev_work_dir(run_id, development_artifacts, tenant_id or "")

    if stage == "requirements":
        return _glob_user_scoped_dir(run_id, "output", segment="requirements_agent")

    if stage == "testing":
        return _glob_user_scoped_dir(run_id, "output")

    if stage == "documentation":
        if not project_id:
            return None
        d = os.path.join(_docs_output_root(), project_id, run_id)
        return d if os.path.isdir(d) else None

    if stage in _GENERATED_STAGE_DIRS:
        return _glob_user_scoped_dir(run_id, f"generated/{stage}")

    return None


def _run_dev_base_sha(run_id: str, work_dir: str) -> Optional[str]:
    """Diff base for the changed-files/lines decorations: the commit the feature branch
    forked from. Prefer the base recorded at branch time; fall back to the merge-base
    with the remote default branch (for runs branched before base tracking existed)."""
    try:
        from agents_orchestrator.development_agent.config.session_state import get_session
        base = getattr(get_session(run_id), "base_sha", None)
        if base:
            return base
    except Exception:  # noqa: BLE001
        pass
    import subprocess
    for ref in ("origin/HEAD", "origin/main", "origin/master"):
        try:
            r = subprocess.run(["git", "merge-base", "HEAD", ref], cwd=work_dir,
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:  # noqa: BLE001
            continue
    return None


@runs_router.get("/{run_id}/workspace/changes")
async def get_run_workspace_changes(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Files the dev agent changed vs the branch base — for tree change decorations."""
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)
    import asyncio
    from shared.services import workspace_fs
    wd = await _run_dev_work_dir(run_id, run.development_artifacts, tenant_id)
    if not wd:
        return {"base": None, "files": []}
    base = _run_dev_base_sha(run_id, wd)
    return await asyncio.to_thread(workspace_fs.list_changes, wd, base)


@runs_router.get("/{run_id}/workspace/file/changed-lines")
async def get_run_workspace_file_changed_lines(
    run_id: str,
    request: Request,
    path: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db_session),
):
    """New-file line numbers changed for *path* vs the branch base (for code highlighting)."""
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)
    import asyncio
    from shared.services import workspace_fs
    wd = await _run_dev_work_dir(run_id, run.development_artifacts, tenant_id)
    if not wd:
        return {"added_lines": []}
    base = _run_dev_base_sha(run_id, wd)
    try:
        return await asyncio.to_thread(workspace_fs.changed_lines, wd, base, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@runs_router.get("/{run_id}/workspace/tree")
async def get_run_workspace_tree(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Flat file list of the run's Development workspace (the cloned repo), so the
    Copilot panel can show the code + structure. {ready:false} when nothing cloned."""
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)
    import asyncio
    from shared.services import workspace_fs
    wd = await _run_dev_work_dir(run_id, run.development_artifacts, tenant_id)
    if not wd:
        return {"ready": False, "paths": [], "truncated": False}
    tree = await asyncio.to_thread(workspace_fs.list_tree, wd)
    return {"ready": True, **tree}


@runs_router.get("/{run_id}/workspace/file")
async def get_run_workspace_file(
    run_id: str,
    request: Request,
    path: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db_session),
):
    """Read one repo-relative file from the run's Development workspace (traversal-guarded)."""
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)
    import asyncio
    from shared.services import workspace_fs
    wd = await _run_dev_work_dir(run_id, run.development_artifacts, tenant_id)
    if not wd:
        raise HTTPException(status_code=404, detail="No workspace for this run")
    try:
        return await asyncio.to_thread(workspace_fs.read_file, wd, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@runs_router.get("/{run_id}/stage-files/{stage}/tree")
async def get_run_stage_tree(
    run_id: str,
    stage: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Flat file list of *stage*'s generated output dir, so the Copilot artifacts
    panel can browse any downstream agent's files the same way it browses Development's
    workspace. {ready:false} when nothing has been generated for this stage yet."""
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)
    import asyncio
    from shared.services import workspace_fs
    wd = await _run_stage_output_dir(
        run_id,
        stage,
        development_artifacts=run.development_artifacts,
        tenant_id=tenant_id,
        project_id=str(run.project_id) if run.project_id else None,
    )
    if not wd:
        return {"ready": False, "paths": [], "truncated": False}
    tree = await asyncio.to_thread(workspace_fs.list_tree, wd)
    return {"ready": True, **tree}


@runs_router.get("/{run_id}/stage-files/{stage}/file")
async def get_run_stage_file(
    run_id: str,
    stage: str,
    request: Request,
    path: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db_session),
):
    """Read one file from *stage*'s generated output dir (traversal-guarded)."""
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)
    import asyncio
    from shared.services import workspace_fs
    wd = await _run_stage_output_dir(
        run_id,
        stage,
        development_artifacts=run.development_artifacts,
        tenant_id=tenant_id,
        project_id=str(run.project_id) if run.project_id else None,
    )
    if not wd:
        raise HTTPException(status_code=404, detail=f"No generated output for stage: {stage}")
    try:
        return await asyncio.to_thread(workspace_fs.read_file, wd, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@runs_router.post(
    "/{run_id}/cancel",
    response_model=RunOut,
    dependencies=[Depends(require_permission("run:create"))],
)
async def cancel_run(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Cancel a running run: terminate its Temporal workflow and mark it cancelled.

    Tenant-scoped (T-M4-03). Best-effort on the workflow cancel — if the workflow is
    already gone/complete we still mark the row cancelled. Terminal runs are left as-is.
    """
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)

    if run.status in ("approved", "rejected", "failed", "merged", "cancelled"):
        return RunOut.from_orm_run(run)  # already terminal — no-op

    if ENABLE_TEMPORAL and run.temporal_workflow_id:
        client = request.app.state.temporal_client
        try:
            handle = client.get_workflow_handle(run.temporal_workflow_id)
            await handle.cancel()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "cancel_run: workflow cancel failed for run=%s: %s", run_id, type(exc).__name__
            )

    run.status = "cancelled"
    await db.commit()
    await db.refresh(run)
    return RunOut.from_orm_run(run)


@runs_router.delete(
    "/{run_id}",
    status_code=204,
    dependencies=[Depends(require_permission("workspace:manage"))],
)
async def delete_run(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Hard-delete a run (and its cascade) for the requesting tenant.

    If the run is still active, its Temporal workflow is terminated first so no
    orphaned workflow keeps running. Tenant-scoped (T-M4-03). Destructive —
    gated on workspace:manage (admin/delivery_lead).
    """
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)

    if ENABLE_TEMPORAL and run.temporal_workflow_id and run.status in ("queued", "running", "awaiting_approval", "awaiting_clarification"):
        client = request.app.state.temporal_client
        try:
            handle = client.get_workflow_handle(run.temporal_workflow_id)
            await handle.terminate()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "delete_run: workflow terminate failed for run=%s: %s", run_id, type(exc).__name__
            )

    # artifacts FK runs.id (NOT NULL, no DB cascade) — delete them first or the
    # run delete violates the FK. Audit events key off resource_id (string, no FK)
    # so they're intentionally left as an immutable trail.
    await db.execute(delete(Artifact).where(Artifact.run_id == run.id))
    await db.delete(run)
    await db.commit()
    return None


@runs_router.get("/{run_id}/steps", response_model=List[StepOut])
async def get_run_steps(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Return a synthetic Step[] derived from the Run's JSONB stage columns.

    No 'steps' table exists in the ORM. Steps are derived from the populated
    JSONB columns: requirements_payload, design_artifacts, development_artifacts,
    testing_artifacts. One synthetic Step per populated stage column. Deterministic
    StepId: f"{run_id}:{stage}". Real steps table deferred to M5 Temporal work.
    """
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)
    steps = derive_steps_from_run(run)
    return steps


@runs_router.post("/{run_id}/approvals", response_model=ApprovalOut)
async def record_approval(
    run_id: str,
    body: ApprovalIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Record a human approval decision for a run, persisted as an AuditEvent.

    The approval is scoped by tenant_id to prevent cross-tenant tampering (T-M4-03).
    The AuditEvent payload carries the decision + reason for the audit trail.
    """
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)

    actor_id = getattr(request.state, "user_id", "system")
    now = datetime.now(timezone.utc)

    audit = AuditEvent(
        tenant_id=uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id,
        actor_id=actor_id,
        event_type=f"run.{body.decision}d" if body.decision in ("approve", "reject") else "run.approval_recorded",
        resource_type="run",
        resource_id=str(run.id),
        payload={
            "decision": body.decision,
            "reason": body.reason,
            "idempotency_key": body.idempotencyKey,
            "actor_name": actor_id,
            "project_id": str(run.project_id),
        },
    )
    db.add(audit)
    await db.flush()

    return ApprovalOut(
        runId=str(run.id),
        decision=body.decision,
        reason=body.reason,
        idempotencyKey=body.idempotencyKey,
        recordedAt=_iso(now),
    )


class CopilotAdvanceIn(BaseModel):
    """Request body for POST /runs/{run_id}/copilot/advance."""
    decision: str
    stage: str
    reason: Optional[str] = None


class CopilotAdvanceOut(BaseModel):
    current_stage: Optional[str]
    status: str


@runs_router.post("/{run_id}/copilot/advance", response_model=CopilotAdvanceOut)
async def copilot_advance(
    run_id: str,
    body: CopilotAdvanceIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Advance (or re-run) a CONVERSATIONAL run at a gate — no Temporal signal.

    Chat-driven progression: for a Copilot-driven run there is no workflow, so the
    gate decision mutates run state directly here. RBAC mirrors signals.py — the
    caller MUST hold the stage's approve permission (_PHASE_PERMISSION), enforced
    server-side (NO self-approval bypass). A caller lacking it gets 403 BEFORE any
    state change.

      approved → current_stage = progression.next_stage(stage); None ⇒ status=complete,
                 current_stage=complete; else status=running, gate_pending=False.
      rejected → current_stage stays = stage, gate_pending=False, status=running
                 (re-run this stage conversationally).

    All state is tenant-scoped (T-M4-03). The approval is recorded as a best-effort
    AuditEvent.
    """
    from shared.authz.permissions import _PHASE_PERMISSION, has_permission
    from shared.services.orchestrator import progression

    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)

    stage = body.stage or run.current_stage or run.stage or "requirements"
    decision = (body.decision or "").lower()
    if decision not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="decision must be 'approved' or 'rejected'")

    # ── Phase-permission gate (mirrors signals.py — fail-closed) ──────────────
    actor_permissions: list[str] = getattr(request.state, "permissions", []) or []
    required_permission = _PHASE_PERMISSION.get(stage)
    if not required_permission or not has_permission(actor_permissions, required_permission):
        # Unknown/uncovered phase OR missing permission ⇒ 403 before any mutation
        # (no permission-name leak, no self-approval bypass).
        raise HTTPException(
            status_code=403,
            detail="Forbidden: actor lacks the required approval permission for this phase",
        )

    actor_id = getattr(request.state, "user_id", "system")

    if decision == "approved":
        nxt = progression.next_stage(stage)
        if nxt is None:
            run.status = "complete"
            run.current_stage = "complete"
        else:
            run.current_stage = nxt
            run.status = "running"
        run.gate_pending = False
    else:  # rejected — re-run the same stage conversationally
        run.current_stage = stage
        run.gate_pending = False
        run.status = "running"

    # Best-effort audit trail — never blocks the advance.
    try:
        audit = AuditEvent(
            tenant_id=uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id,
            actor_id=actor_id,
            event_type="run.approved" if decision == "approved" else "run.rejected",
            resource_type="run",
            resource_id=str(run.id),
            payload={
                "decision": decision,
                "stage": stage,
                "reason": body.reason,
                "conversational": True,
                "actor_name": actor_id,
                "project_id": str(run.project_id) if run.project_id else None,
            },
        )
        db.add(audit)
    except Exception as exc:  # noqa: BLE001 — audit is best-effort
        logger.warning("copilot_advance audit skipped (run=%s): %s", run_id, type(exc).__name__)

    await db.commit()
    await db.refresh(run)
    return CopilotAdvanceOut(current_stage=run.current_stage, status=run.status)


class CopilotSetStageIn(BaseModel):
    """Request body for POST /runs/{run_id}/copilot/set-stage."""
    stage: str


# Reuses CopilotAdvanceOut's shape (current_stage, status) — same response contract.
CopilotSetStageOut = CopilotAdvanceOut


@runs_router.post("/{run_id}/copilot/set-stage", response_model=CopilotSetStageOut)
async def copilot_set_stage(
    run_id: str,
    body: CopilotSetStageIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Repoint a run's ACTIVE stage to ANY known pipeline stage (Copilot left-rail jump).

    Lets the user click a prior stage (e.g. Development) in the Copilot left rail
    to re-activate that agent even after the pipeline has moved on / completed —
    the Copilot WS re-reads run.current_stage on the next chat turn (`_active_stage`
    -> `_graph_for`) and routes to that agent's run-keyed checkpoint. This endpoint
    only repoints state; it does NOT touch the WS routing or copilot_advance.

    RBAC mirrors copilot_advance: the caller MUST hold the TARGET stage's approve
    permission (via can_user_approve, same _PHASE_PERMISSION + has_permission
    primitives — admin:*/org_admin wildcard passes), enforced BEFORE any mutation
    (fail-closed, no self-approval bypass). Unknown stage -> 400 before the RBAC
    check even runs (no need to leak permission requirements for a bogus stage).

    All state is tenant-scoped (T-M4-03). The change is recorded as a best-effort
    AuditEvent, same pattern as copilot_advance.
    """
    from shared.services.orchestrator import progression
    from shared.services.orchestrator.gate_routing import can_user_approve

    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)

    stage = body.stage
    if stage not in progression.STAGE_ORDER:
        raise HTTPException(status_code=400, detail=f"Unknown stage: {stage!r}")

    # ── Phase-permission gate (mirrors copilot_advance — fail-closed) ─────────
    actor_permissions: list[str] = getattr(request.state, "permissions", []) or []
    if not can_user_approve(actor_permissions, stage):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: actor lacks the required approval permission for this stage",
        )

    actor_id = getattr(request.state, "user_id", "system")

    run.current_stage = stage
    run.status = "running"
    run.gate_pending = False

    # Best-effort audit trail — never blocks the stage jump.
    try:
        audit = AuditEvent(
            tenant_id=uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id,
            actor_id=actor_id,
            event_type="run.stage_set",
            resource_type="run",
            resource_id=str(run.id),
            payload={
                "stage": stage,
                "conversational": True,
                "actor_name": actor_id,
                "project_id": str(run.project_id) if run.project_id else None,
            },
        )
        db.add(audit)
    except Exception as exc:  # noqa: BLE001 — audit is best-effort
        logger.warning("copilot_set_stage audit skipped (run=%s): %s", run_id, type(exc).__name__)

    await db.commit()
    await db.refresh(run)
    return CopilotSetStageOut(current_stage=run.current_stage, status=run.status)


class CopilotCancelTurnOut(BaseModel):
    """Response for POST /runs/{run_id}/copilot/cancel-turn."""
    ok: bool


@runs_router.post(
    "/{run_id}/copilot/cancel-turn",
    response_model=CopilotCancelTurnOut,
    dependencies=[Depends(require_permission("run:create"))],
)
async def copilot_cancel_turn(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Stop the Copilot's in-flight conversational turn for this run.

    Unlike POST /{run_id}/cancel (which terminates the whole Temporal run), this
    only signals the live WS turn to stop streaming — the run stays open and the
    user can keep chatting. The Copilot WS processes a turn synchronously per
    frame, so Stop can't come over the same socket; it flips a cooperative flag
    the streaming loop checks between chunks (see copilot_api.request_turn_cancel).

    Tenant-scoped (T-M4-03); best-effort — a no-op if no turn is in flight.
    """
    tenant_id = request.state.tenant_id
    # Tenant-scoped existence check (also gives a clean 404 for a bogus id).
    await _get_run_or_404(db, run_id, tenant_id, request=request)
    try:
        from agents_orchestrator.orchestrator.copilot_api import request_turn_cancel
        request_turn_cancel(run_id)
    except Exception as exc:  # noqa: BLE001 — signalling is best-effort
        logger.warning("copilot_cancel_turn signal failed (run=%s): %s", run_id, type(exc).__name__)
    return CopilotCancelTurnOut(ok=True)


async def _get_run_or_404(
    db: AsyncSession, run_id: str, tenant_id: str, *, request: Request
) -> Run:
    """Fetch a Run by id, scoped to the tenant AND to what this caller may see.

    The tenant_id filter prevents cross-tenant reads (T-M4-01). The scope check is
    the second half: every route in this module reached a run through here with the
    tenant filter alone, so any authenticated person could read any run in the
    organization — its stages, artifacts, transcript and workspace files — by id.

    `request` is REQUIRED and keyword-only on purpose. This is the chokepoint for
    sixteen routes; an optional parameter here is a check that will eventually be
    forgotten at one call site, and that one site is the hole.

    404 for a run outside the caller's scope, matching the cross-tenant answer — two
    different codes for "you cannot have this" tells the caller which runs exist.
    """
    result = await db.execute(
        select(Run).where(
            Run.id == run_id,
            Run.tenant_id == tenant_id,
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # A run with no project (webhook-triggered against a provider key, project_id is
    # nullable) has no scope chain to walk. Org-wide callers still see it; nobody else
    # does, because there is no unit or project that could make it theirs.
    if run.project_id is None:
        visible = await visible_project_ids(
            db, user_id=_user_id(request), tenant_id=str(tenant_id)
        )
        if visible is not None:
            raise HTTPException(status_code=404, detail="Run not found")
        return run

    if not await can_perform(
        db,
        user_id=_user_id(request),
        permission="artifact:view",
        tenant_id=str(tenant_id),
        resource_kind="project",
        resource_id=str(run.project_id),
    ):
        raise HTTPException(status_code=404, detail="Run not found")
    return run
