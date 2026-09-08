"""Runs resource router.

Exposes read and create operations for the Run model plus step derivation and
approval recording. All routes are JWT-protected (NOT in _EXEMPT_PATHS) and
scope every query by request.state.tenant_id.

Routes:
  POST /runs                   — create run + start SDLCWorkflow (SC-01, D-09)
  GET  /runs                   — paginated list (query: project_id, status, page, page_size)
  GET  /runs/{id}              — detail (surfaces the run's awaiting state)
  GET  /runs/{id}/steps        — derive Step[] from JSONB columns (no steps table — ORM-gap 4)
  POST /runs/{id}/approvals    — record an AuditEvent approval decision

Threat mitigations (T-M4-01, T-M4-02, T-M4-03, T-M5-21, T-M5-23):
  - All queries filtered by tenant_id (no cross-tenant reads)
  - Routes not in _EXEMPT_PATHS (JWT middleware enforces 401 without token)
  - Approval mutation scoped by tenant_id + 404 guard
  - POST /runs scopes Run to tenant_id (T-M5-21)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, field_validator
from sqlalchemy import delete, false as sa_false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.can_perform import can_perform, visible_project_ids
from shared.authz.dependency import require_permission
from shared.db import get_db_session
from shared.models.orm import Artifact, AuditEvent, Project, Run
from shared.services.attachment_store import (
    AttachmentError,
    list_attachments,
    save_attachment,
    validate_attachment,
)

logger = logging.getLogger(__name__)
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

runs_router = APIRouter()

# Aliases so the scope helpers read the same way here as in the other routers.
_uuid = uuid


def _user_id(request: Request) -> str:
    return getattr(request.state, "user_id", "") or ""


class ApprovalIn(BaseModel):
    #: "approve" or "reject". Normalised below, because the gate UI has always sent the
    #: past tense ("approved"/"rejected") — that is what `advanceCopilotRun` posted to
    #: the retired copilot route, and rejecting those spellings here would break the
    #: three screens that decide gates rather than teach them a new vocabulary.
    decision: str
    reason: Optional[str] = None
    idempotencyKey: Optional[str] = None

    @field_validator("decision")
    @classmethod
    def _normalise(cls, v: str) -> str:
        v = (v or "").strip().lower()
        canonical = {
            "approve": "approve", "approved": "approve",
            "reject": "reject", "rejected": "reject",
        }.get(v)
        if canonical is None:
            raise ValueError("decision must be 'approve' or 'reject'")
        return canonical


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
    """Create a Run.

    EVERY RUN IS CONVERSATIONAL NOW. The Orchestrator Copilot drives each stage's
    agent and gate approvals advance the run via POST /runs/{id}/copilot/advance.

    The alternative used to be a Temporal workflow, and it is gone. It was disabled
    (`ENABLE_TEMPORAL=false`) and this endpoint answered 503 for any non-
    conversational run, so removing it changes nothing that was working — it
    removes a 503 and the machinery behind it.

    `conversational` is therefore no longer a fork in the road; it is accepted and
    ignored, kept on the request body only so an older client does not start
    failing schema validation mid-deploy.
    """
    tenant_id = request.state.tenant_id

    # Hierarchical budget gate (org ⊇ workspace ⊇ project): reject a new run at dispatch
    # when any scope's monthly spend is already at/over its budget (409 with the scope).
    from shared.services.budget_guard import (
        BudgetExceededError,
        BudgetWindowClosedError,
        check_budgets,
    )
    try:
        await check_budgets(tenant_id, str(body.project_id) if body.project_id else None)
    except (BudgetExceededError, BudgetWindowClosedError) as e:
        # Same 409 for both: the run is refused on budget grounds either way, and the
        # message says which — spent out, or outside the period it was funded for.
        raise HTTPException(status_code=409, detail=str(e))

    # `active_agents` picks which agents this run actually exercises (build_execution_
    # plan reads it downstream) — nothing validated it against the caller's per-agent
    # access before now (design doc §4.1/§4.3), so a caller with no reach to, say, the
    # Security agent could still start a run naming it in active_agents and have it
    # execute, bypassing every router-level require_agent_access/assert_agent_access
    # gate entirely. Checked once here, up front, before the Run row is created.
    if body.active_agents:
        from shared.authz.agent_access import assert_agent_access_for_chat
        for _agent_id in body.active_agents:
            await assert_agent_access_for_chat(
                db, tenant_id=str(tenant_id), project_id=str(body.project_id),
                user_id=_user_id(request), agent_id=_agent_id,
            )

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
        except (BudgetExceededError, BudgetWindowClosedError) as e:
            raise HTTPException(status_code=409, detail=str(e))
        resolved_model_id = resolved.model
        resolved_offering_id = resolved.offering_id
        resolved_display_name = resolved.display_name

    # The run starts already "running" at the first stage with no gate pending. The
    # gate appears later, once the active stage's agent produces its artifact and the
    # Copilot's WebSocket detects it.
    run = Run(
        project_id=body.project_id,
        tenant_id=tenant_id,
        status="running",
        stage="requirements",
        current_stage="requirements",
        gate_pending=False,
        model_id=resolved_model_id,
        offering_id=resolved_offering_id,
        model_display_name=resolved_display_name,
        # The initiator, so the gate can refuse to let this person approve their own
        # run's output (0038, §1.5). Leaving it unset would silently exempt every run
        # created here from that rule.
        created_by=_user_id(request),
    )
    db.add(run)
    await db.commit()
    return RunCreateOut(runId=str(run.id))


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


@runs_router.get("/{run_id}/deliverables")
async def get_run_deliverables(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Panel-ready deliverables for an Orchestrator run (reload/replay). Tenant-scoped.

    NOT `/artifacts`. That endpoint reads the run's `*_artifacts` columns, which the
    STANDALONE agents write and which carry an approval concept. The Orchestrator's
    agents share their names and capability and are a different thing; their output
    lives in `orchestrator_deliverables` and is never gated, because the person
    driving it is a Project Admin who already owns all nine agents.

    Resolved through `_get_run_or_404` like every other route in this module, so the
    tenant filter and the caller-scope check are not things this endpoint could
    forget on its own. The deliverables read is scoped to the CALLER's tenant, not
    the run's: reading with `run.tenant_id` would make the query agree with whatever
    row came back, laundering a scoping bug upstream into a successful cross-tenant
    read instead of an empty one.
    """
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)

    from agents_orchestrator.orchestrator2.deliverables import (
        deliverables_for_run,
        pointers_for_run,
    )
    from agents_orchestrator.orchestrator2.registry import AGENT_IDS

    dev_artifacts = getattr(run, "development_artifacts", None)

    # Which agents actually wrote files to disk. Checked HERE rather than inside
    # `pointers_for_run`, which stays pure: this is the only layer that can look at
    # the disk. An agent that generated nothing gets no tree, because an empty tree
    # reads as a pull that failed rather than as a stage with no files.
    dir_by_stage: dict[str, str] = {}
    for agent_id in AGENT_IDS:
        if agent_id == "development":
            continue  # already covered by its own code-tree pointer
        try:
            directory = await _run_stage_output_dir(
                str(run.id), agent_id,
                development_artifacts=dev_artifacts,
                tenant_id=str(tenant_id),
                project_id=str(run.project_id) if run.project_id else None,
            )
        except Exception:  # noqa: BLE001 - a missing tree must not fail the whole read
            continue
        if directory and os.path.isdir(directory) and os.listdir(directory):
            dir_by_stage[agent_id] = directory

    # Several stages resolve one shared directory, and it may be shown only once.
    # The run's own stage owns the shared directory when it is one of the agents
    # that write there — see `_dedupe_by_directory`.
    stages_with_files = _dedupe_by_directory(
        dir_by_stage, prefer=getattr(run, "stage", None),
    )

    stored = await deliverables_for_run(str(run.id), str(tenant_id))
    pointers = pointers_for_run(dev_artifacts, stages_with_files)
    return {"deliverables": stored + pointers}


@runs_router.post("/{run_id}/attachments")
async def upload_run_attachments(
    run_id: str,
    request: Request,
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db_session),
):
    """Store files the user attached to an Orchestrator run, and return their refs.

    NOT `POST /conversations/{session_id}/attachments`, even though the Orchestrator's
    session id IS its run id. That route authorises through
    `conversation_service.session_owner`, and the Orchestrator's conversation row is
    created by the SOCKET on the first turn (`orchestrator2.sessions.ensure_session`) —
    so attaching a file before sending the first message would 404 against a run that
    genuinely exists. This one resolves through `_get_run_or_404`, the same
    tenant-and-scope chokepoint the run's other routes use, which holds from the moment
    the run row exists.

    THE FILE IS FILED UNDER THE AUTHENTICATED CALLER, never a value from the request.
    `attachment_store` keys uploads by uploader and
    `orchestrator2.attachments.attachment_context` reads them back under the socket
    TICKET's user; letting a caller name the owner would be a way to plant a document
    into somebody else's agent prompt.

    THE WHOLE BATCH IS VALIDATED BEFORE ANYTHING IS WRITTEN. Rejecting as it wrote would
    leave the run holding files the user was told were not accepted — and an
    Orchestrator run reads every stored attachment into every later turn, so those files
    would go on reaching agents after the upload had reported failure.
    """
    tenant_id = request.state.tenant_id
    await _get_run_or_404(db, run_id, tenant_id, request=request)

    user_id = _user_id(request)
    payloads: list[tuple[str, bytes]] = []
    for f in files:
        data = await f.read()
        try:
            validate_attachment(f.filename or "upload", data)
        except AttachmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        payloads.append((f.filename or "upload", data))

    refs = [
        save_attachment(user_id, run_id, filename, data)
        for filename, data in payloads
    ]
    return {"attachments": refs}


@runs_router.get("/{run_id}/attachments")
async def get_run_attachments(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """The files this caller attached to the run, so a reopened chat can show them.

    Without this the chips vanish on reload while the attachments keep reaching every
    turn — the user can no longer see what the agent is being given, which is its own
    kind of lie about what the agent knows.

    Scoped to the CALLER's own uploads, matching what the socket actually reads: a run
    driven by two Project Admins gives each turn only its own driver's files, and a
    listing that showed both would describe a prompt that is never assembled.
    """
    tenant_id = request.state.tenant_id
    await _get_run_or_404(db, run_id, tenant_id, request=request)
    return {"attachments": list_attachments(_user_id(request), run_id)}


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
            # The AGENT, not `author_id` — which is the user who typed at it, and
            # was what every replayed agent turn used to be labelled with. Null for a
            # turn from before migration 0045, which is honest: nothing recorded it.
            "stage": r.get("agent_id") if role == "agent" else None,
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


# Stages that export into the shared per-run `output/` directory. All three write
# through different tools — the Project Manager's plan export, `architecture.py`, and
# Testing's `finalize.py` — but land in the same place, because `session_id` is the
# run id for every agent the Orchestrator dispatches.
#
# ORDERED, because they share a directory and only one of them may show it. See
# `_dedupe_by_directory`. The order is the one these agents typically run in, so the
# earliest contributor owns the heading.
_ORCHESTRATOR_OUTPUT_STAGES: tuple[str, ...] = ("design", "plan", "testing")


def _dedupe_by_directory(
    by_stage: dict[str, str], *, prefer: str | None = None,
) -> set[str]:
    """Keep one stage per distinct directory, `prefer` first and then
    `_ORCHESTRATOR_OUTPUT_STAGES` order.

    `plan`, `design` and `testing` all resolve the SAME path, so mapping all three
    made the panel render the identical files once per heading — the Testing agent's
    `test_plan.xlsx` showing up under Design. Wrong attribution is worse than none: an
    empty heading reads as "nothing produced yet", a populated one reads as evidence.

    `prefer` is the run's own stage. It is right whenever a run exercised one of the
    sharing agents — which is the common case — and it is what stops the Project
    Manager's delivery plan being filed under Design purely because Design sorts
    first.

    WHAT THIS DOES NOT DO is attribute correctly in general. Nothing on disk records
    which agent wrote which file; the agents share the directory, and `runs.stage` is
    set once at creation and never updated, because orchestrator2 deliberately writes
    no position back to a run. So a long conversation where Design and the Project
    Manager both exported shows both documents under whichever the run was opened for.
    Exact attribution needs the agents writing into per-agent subdirectories, which is
    a change to the agents rather than to this read path.

    The order is a fixed tuple rather than set iteration order: the winner has to be
    the same on every read, or the same run files its documents under a different
    agent each time the panel refreshes.
    """
    seen: dict[str, str] = {}
    ordered = [prefer] if prefer and prefer in by_stage else []
    ordered += [s for s in _ORCHESTRATOR_OUTPUT_STAGES if s in by_stage]
    ordered += sorted(
        s for s in by_stage
        if s not in _ORCHESTRATOR_OUTPUT_STAGES and s != prefer
    )
    for stage in ordered:
        # Normalised because the same directory reached through two stages can come
        # back spelled differently — `glob` preserves whatever case and separators the
        # caller handed it, and on Windows both vary.
        key = os.path.normcase(os.path.normpath(by_stage[stage]))
        seen.setdefault(key, stage)
    return set(seen.values())

# Stages whose generated output isn't in its own dedicated location (dev workspace,
# the shared `output/` above, docs' own root) yet — populated under a shared
# `generated/<stage>` tree once each agent starts writing there (later task).
# Fail-soft: None until then.
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
    plan | design | testing
                  -> `{FILES}/<user>/orchestrator/<run_id>/output` — the Project
                     Manager's plan export, `architecture.py`'s HLD/LLD docx, and
                     Testing's `Nodes/finalize.py` all write here.

                     `plan` and `design` were MISSING from this mapping and returned
                     None. Found by running all nine agents against the real stack:
                     the Project Manager produced
                     `Coffee_Ordering_App_Delivery_Plan.pdf`, wrote it to exactly the
                     path `testing` resolves fine, and it never appeared in
                     Deliverables. Silent by construction — a stage with no directory
                     is indistinguishable from a stage that generated nothing.
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

    if stage in _ORCHESTRATOR_OUTPUT_STAGES:
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
    # Stopping a run is not the same authority as starting one, and the role matrix
    # already says so — `run:cancel` is granted to project_admin alone while
    # `run:create` reaches every delivery role that raises work. Gating the stop on
    # the start permission made the distinct permission decorative and let anyone who
    # could begin a run end somebody else's.
    dependencies=[Depends(require_permission("run:cancel"))],
)
async def cancel_run(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Cancel a running run.

    Tenant-scoped (T-M4-03). Terminal runs are left as-is — cancelling something
    already finished is a no-op, not an error.

    There is no workflow to terminate any more: a conversational run advances only
    when the Copilot is asked to advance it, so marking the row cancelled is the
    whole of stopping it.
    """
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)

    if run.status in ("approved", "rejected", "failed", "merged", "cancelled"):
        return RunOut.from_orm_run(run)  # already terminal — no-op

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

    Tenant-scoped (T-M4-03). Destructive — gated on workspace:manage.
    """
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)

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
    """Return a synthetic Step[] describing what this run produced.

    No 'steps' table exists in the ORM. Steps are derived from two places: the
    populated JSONB stage columns (the pipeline hand-off) and the run's artifact rows
    (every file it generated).

    THE ARTIFACTS ARE LOADED HERE, and that is what makes chat-driven work visible.
    `register_generated_file` writes an `artifacts` row and never touches the JSONB
    columns, so a run whose whole output came through the chat drawer derived no steps
    at all and the Activity panel read "No activity yet" beside a document the user had
    just downloaded.

    The query is tenant-safe without an explicit filter: `_get_run_or_404` has already
    resolved the run against `request.state.tenant_id`, so `Artifact.run_id == run.id`
    cannot reach another tenant's rows — and RLS applies to this session regardless.
    """
    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)
    artifacts = (
        await db.execute(
            select(Artifact)
            .where(Artifact.run_id == run.id)
            .order_by(Artifact.created_at)
        )
    ).scalars().all()
    steps = derive_steps_from_run(run, artifacts)
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

    GATED ON THE STAGE'S APPROVE PERMISSION, mirroring copilot_advance. This route had
    NO permission check at all beyond the router's `artifact:view` floor — which every
    role holds, including `contributor`, whose entire purpose is holding nothing yet. So
    anyone signed in could write `run.approved`, under their own name, against any run
    in the tenant.

    That it does not advance the run is exactly why it went unnoticed and exactly why it
    matters: nothing visibly changed, and `audit_events` is append-only by privilege, so
    the forged row could not be cleaned up afterwards. Recording that a human approved
    something is the same authority as approving it.
    """
    from shared.authz.permissions import _PHASE_PERMISSION, has_permission  # noqa: PLC0415

    tenant_id = request.state.tenant_id
    run = await _get_run_or_404(db, run_id, tenant_id, request=request)

    # The run's OWN stage decides the permission — not a caller-supplied one. Taking it
    # from the body would let the caller name a stage they can approve and record the
    # decision against a run sitting at a different one.
    stage = run.current_stage or run.stage or "requirements"
    actor_permissions: list[str] = getattr(request.state, "permissions", []) or []
    required_permission = _PHASE_PERMISSION.get(stage)
    if not required_permission or not has_permission(actor_permissions, required_permission):
        # Unknown/uncovered stage OR missing permission ⇒ 403 before anything is
        # written (no permission-name leak, consistent with copilot_advance).
        raise HTTPException(
            status_code=403,
            detail="Forbidden: actor lacks the required approval permission for this phase",
        )

    actor_id = getattr(request.state, "user_id", "system")

    # §1.5: WHOEVER RAN THE AGENT IS NEVER THE ONE WHO ACCEPTS ITS OWN OUTPUT.
    #
    # The permission check above answers "may this ROLE approve this stage". That is a
    # different question, and it passes for exactly the person this rule exists to
    # stop: a BA who starts a Requirements run holds artifact:approve_requirements by
    # definition. Both checks are needed.
    #
    # This lived in copilot_api._handle_gate_decision until Phase 5 retired that
    # engine, which left the rule enforced NOWHERE and `runs.created_by` — migration
    # 0038, added to serve it — with no consumer. This is where it lives now.
    #
    # A run with no recorded initiator is NOT blocked: `created_by` is nullable
    # (webhook runs, and rows predating 0038), and refusing there would make every
    # historical run permanently unapprovable. The rule cannot be applied, so it is
    # not — deliberately, rather than by omission.
    initiator = getattr(run, "created_by", None)
    if initiator and actor_id and str(initiator) == str(actor_id):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: the person who started a run cannot approve its output",
        )

    now = datetime.now(timezone.utc)

    # THE GATE IS CLOSED HERE, and until now nothing closed it anywhere.
    #
    # A gate is DERIVED — `approvals._pending_gates` selects runs with
    # `gate_pending = true` — and `_handle_artifact_ready` is what sets the flag when an
    # agent finishes a stage. Clearing it lived in `copilot_advance`, which Phase 5A
    # deleted along with the rest of the Copilot. Nothing inherited the job, so every
    # gate ever raised stayed in every eligible queue permanently, and this route
    # recorded a decision beside a run that went on looking undecided.
    #
    # Both decisions close it. A rejection does not leave the run paused waiting for the
    # same person to answer again — the stage is sent back, and re-running it is what
    # raises the next gate.
    run.gate_pending = False

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
