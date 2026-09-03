"""Pydantic v2 response models mapping ORM snake_case -> Zod camelCase.

These models are the canonical contract between the FastAPI resource routers and
the apps/web/ client (Zod schemas in apps/web/lib/schemas/). The field mappings
are intentional and documented inline for each ORM-gap decision.

ORM-gap decisions implemented here (see PLAN.md <orm_gap_decisions>):
  1. Project.archived: added in migration 0003 — direct mapped_column.
  2. Project.slug/template/owners/pipeline/lastActivityAt: derived from existing fields.
  3. Run fields title/agent/phase/trigger/cost/pendingApprovers: mapped or constant-filled.
  4. Step: derived from Run JSONB columns, not a separate ORM table.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from shared.models.workflow_models import ChangeRequest


# ── Generic paginated envelope ──────────────────────────────────────────────
# Matches apps/web/lib/schemas/primitives.ts paginated() shape:
#   { items: T[]; pagination: { page, pageSize, total } }

class Pagination(BaseModel):
    page: int
    pageSize: int
    total: int


T = TypeVar("T")


class Paginated(BaseModel, Generic[T]):
    """Generic paginated response envelope matching apps/web Zod paginated() shape."""

    items: List[T]
    pagination: Pagination


# ── Helper: slugify ──────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    """Convert a display_name to a URL-safe slug. e.g. 'My Project' -> 'my-project'."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    return slug.strip("-") or "project"


# ── ProjectOut ──────────────────────────────────────────────────────────────

class BudgetIncreaseIn(BaseModel):
    """Shared by workspaces.py's and projects.py's budget-increase-request routes —
    same shape at both tiers, so one schema rather than two copies to keep in sync."""

    requestedAmountUsd: float = Field(gt=0)
    reason: Optional[str] = Field(default=None, max_length=2000)


class ProjectOut(BaseModel):
    """ORM Project -> Zod Project shape.

    Field mappings:
      display_name   -> name       (Zod Project.name)
      tenant_id      -> tenantId   (UUID str)
      archived       -> archived   (new ORM column via migration 0003)
      created_at     -> createdAt
      updated_at     -> lastActivityAt (Zod lastActivityAt = last real activity proxy)

    Derived fields (ORM-gap decision 2):
      slug           = slugify(display_name) — no slug column in ORM
      template       = "blank"               — no template column in ORM
      owners         = []                    — no owners relation in ORM
      pipeline       = []                    — no pipeline data in ORM (renders idle state)
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    tenantId: str
    # The Business Unit this project belongs to. The Projects screen GROUPS by this,
    # so omitting it does not degrade to "no badge" — every project falls into the
    # trailing "Unassigned" bucket instead, which reads as a data problem rather than
    # a serialization one. The Zod counterpart is `.nullable().optional()`, so a
    # missing field parses cleanly to undefined and nothing errors on the way.
    workspaceId: Optional[str] = None
    name: str
    slug: str
    description: Optional[str] = None
    template: str
    track: str
    archived: bool
    # Project-creation approval gate (migration 0028) — see
    # shared/governance/effects.py for what flips these.
    approvalStatus: str = "active"
    approvalDecidedBy: Optional[str] = None
    approvalDecidedAt: Optional[str] = None
    approvalReason: Optional[str] = None
    owners: List[Any]
    pipeline: List[Any]
    # Per-project stage→MCP-server mapping {agent_id: [mcp_server_id, ...]}.
    mcpServers: dict[str, List[str]] = {}
    # Per-project stage→connector-kind mapping {agent_id: [connector_kind, ...]}.
    connectors: dict[str, List[str]] = {}
    # Per-(stage, tool) read/write mode, "{agent_id}::{connector|mcp}::{ref}" ->
    # "read" | "write" | "both". Absent key = "both" (migration 0024).
    toolAccessModes: dict[str, str] = {}
    # TOTAL cost budget (0032). None = inherit workspace / unlimited. The field name
    # is historical — spend is accumulated over the project's life, not per month
    # (shared/services/budget_store.py).
    monthlyBudgetUsd: Optional[float] = None
    # Lifetime spend from the durable usage rollup (0 unless injected).
    monthlySpendUsd: float = 0.0
    # How long the budget is authorised for (0035), `YYYY-MM-DD` or null. The
    # frontend has rendered and validated this pair for some time
    # (lib/schemas/budget-window.ts); returning it is what finally lets the cost and
    # settings pages show the window a project was actually created with.
    budgetStartDate: Optional[str] = None
    budgetEndDate: Optional[str] = None
    # Set when a settings edit was QUEUED rather than applied — a Project Admin's
    # edit becomes a request for their Business Unit Admin. The project in this
    # response is therefore unchanged, and the client must say "sent for approval"
    # rather than "saved". Absent/false on every other response.
    pendingApproval: bool = False
    pendingRequestId: Optional[str] = None
    pendingApproverRole: Optional[str] = None
    lastActivityAt: str
    createdAt: str

    @classmethod
    def from_orm_project(cls, project: Any, spend_usd: float = 0.0) -> "ProjectOut":
        """Build a ProjectOut from a shared.models.orm.Project instance."""
        _budget = getattr(project, "monthly_budget_usd", None)
        _ws = getattr(project, "workspace_id", None)
        return cls(
            id=str(project.id),
            tenantId=str(project.tenant_id),
            workspaceId=str(_ws) if _ws is not None else None,
            name=project.display_name,
            slug=_slugify(project.display_name),
            description=getattr(project, "description", None),
            template="blank",
            track=getattr(project, "track", None) or "greenfield",
            archived=project.archived,
            budgetStartDate=(
                d.isoformat() if (d := getattr(project, "budget_start_date", None)) else None
            ),
            budgetEndDate=(
                d.isoformat() if (d := getattr(project, "budget_end_date", None)) else None
            ),
            approvalStatus=getattr(project, "approval_status", None) or "active",
            approvalDecidedBy=getattr(project, "approval_decided_by", None),
            approvalDecidedAt=(
                _iso(dat) if (dat := getattr(project, "approval_decided_at", None)) else None
            ),
            approvalReason=getattr(project, "approval_reason", None),
            owners=[],
            pipeline=[],
            mcpServers=getattr(project, "mcp_servers", None) or {},
            connectors=getattr(project, "connectors", None) or {},
            toolAccessModes=getattr(project, "tool_access_modes", None) or {},
            monthlyBudgetUsd=float(_budget) if _budget is not None else None,
            monthlySpendUsd=round(float(spend_usd or 0.0), 4),
            lastActivityAt=_iso(project.updated_at),
            createdAt=_iso(project.created_at),
        )


# ── RunOut ────────────────────────────────────────────────────────────────────

class CostOut(BaseModel):
    """Matches apps/web/lib/schemas/primitives.ts Cost shape."""
    usd: float
    inputTokens: int
    outputTokens: int


# The frontend Status / Phase / AgentType enums (lib/schemas/enums.ts) are a closed
# vocabulary; the backend run lifecycle uses a richer one (stage-specific gate states
# like awaiting_requirements_approval, plus complete/completed). A single out-of-enum
# value makes the whole list fail Zod parsing on the client ("unexpected shape"), so we
# normalize here — the serializer's documented job.
_FE_STATUS = {
    "draft", "queued", "running", "awaiting_approval", "awaiting_clarification",
    "approved", "rejected", "failed", "cancelled", "merged", "paused",
}
_RUN_STATUS_MAP = {
    "pending": "queued",
    "in_progress": "running",
    "complete": "approved",
    "completed": "approved",
    "canceled": "cancelled",
}
_TERMINAL_RAW = {"approved", "rejected", "failed", "merged", "cancelled", "complete", "completed"}
_FE_PHASE = {"requirements", "design", "development", "review", "testing", "deployment"}
_FE_AGENT = {"orchestrator", "requirements", "design", "development", "review", "testing", "deployment"}


def _map_run_status(raw: Optional[str]) -> str:
    if not raw:
        return "queued"
    if raw in _FE_STATUS:
        return raw
    if raw.startswith("awaiting_"):
        # awaiting_requirements_approval / awaiting_design_approval / ... -> awaiting_approval
        return "awaiting_approval"
    return _RUN_STATUS_MAP.get(raw, "running")


def _run_title(run: Any) -> str:
    """Human-friendly run label: '<board project> · <Stage>' once the requirements
    payload names a project, else the id-prefixed fallback. Reads current_stage so the
    label tracks progression (e.g. 'Carelon · Requirements' → 'Carelon · Design')."""
    payload = getattr(run, "requirements_payload", None)
    project = payload.get("project") if isinstance(payload, dict) else None
    stage = (getattr(run, "current_stage", None) or getattr(run, "stage", None) or "requirements")
    stage_label = str(stage).replace("_", " ").title()
    if project:
        return f"{project} · {stage_label}"
    return f"Run {str(run.id)[:8]}"


class RunOut(BaseModel):
    """ORM Run -> Zod Run shape.

    Field mappings (ORM-gap decision 3):
      id                   -> id
      project_id           -> projectId
      stage                -> phase     (closest ORM equivalent to Zod Run.phase)
      current_stage        -> agent     (active agent type; falls back to stage)
      status               -> status
      created_at           -> startedAt
      updated_at           -> completedAt (null if not terminal; approximation)
      gate_pending         -> pendingApprovers (["admin","member"] when True, else [])

    Constant-filled fields (no real ORM data yet):
      title          = f"Run {id[:8]}"   — no title column in ORM
      startedBy      = null               — no startedBy relation in ORM
      cost           = {usd:0, inputTokens:0, outputTokens:0} — real cost via AgentCallLog
                       aggregation; zero is the correct ED-5 display
                       ("show only real data") until real cost binding lands
      durationMs     = null               — no duration column in ORM
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    projectId: str
    title: str
    agent: str
    phase: str
    status: str
    trigger: str
    startedBy: Optional[str]
    startedAt: str
    completedAt: Optional[str]
    durationMs: Optional[int]
    cost: CostOut
    pendingApprovers: List[str]

    @classmethod
    def from_orm_run(cls, run: Any) -> "RunOut":
        """Build a RunOut from a shared.models.orm.Run instance."""
        is_terminal = run.status in _TERMINAL_RAW
        agent_raw = run.current_stage or run.stage or "orchestrator"
        return cls(
            id=str(run.id),
            projectId=str(run.project_id) if run.project_id else "",
            title=_run_title(run),
            agent=agent_raw if agent_raw in _FE_AGENT else "orchestrator",
            phase=run.stage if run.stage in _FE_PHASE else "requirements",
            status=_map_run_status(run.status),
            trigger=getattr(run, "trigger", None) or "manual",
            startedBy=None,
            startedAt=_iso(run.created_at),
            completedAt=_iso(run.updated_at) if is_terminal else None,
            durationMs=None,
            cost=CostOut(usd=0.0, inputTokens=0, outputTokens=0),
            pendingApprovers=["admin", "member"] if run.gate_pending else [],
        )


# ── StepOut ────────────────────────────────────────────────────────────────────

class StepModelOut(BaseModel):
    """Matches apps/web/lib/schemas/step.ts Step.model optional field."""
    provider: str
    id: str


class StepOut(BaseModel):
    """Synthetic Step derived from Run JSONB columns (ORM-gap decision 4).

    No 'steps' table exists in the ORM. Steps are DERIVED from the populated
    JSONB columns on the Run row: requirements_payload, design_artifacts,
    development_artifacts, testing_artifacts. One synthetic Step per populated
    stage column. There is no steps table; a step is derived from the run.

    Deterministic synthetic StepId: f"{run_id}:{stage}"
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    runId: str
    index: int
    kind: str
    agent: str
    title: str
    status: str
    startedAt: str
    completedAt: Optional[str]
    durationMs: Optional[int]
    cost: Optional[CostOut]
    model: Optional[StepModelOut]
    summary: Optional[str]
    error: Optional[dict]


_STAGE_TO_AGENT = {
    "requirements": "requirements",
    "design": "design",
    "plan": "plan",
    "development": "development",
    "testing": "testing",
    "deployment": "deployment",
}

_JSONB_COLUMNS = [
    ("requirements", "requirements_payload"),
    ("design", "design_artifacts"),
    ("plan", "plan_artifacts"),
    ("development", "development_artifacts"),
    ("testing", "testing_artifacts"),
]


def derive_steps_from_run(run: Any, artifacts: Optional[List[Any]] = None) -> List[StepOut]:
    """Derive a Step[] from what the run actually produced.

    TWO SOURCES, because the run has two ways of producing things and only one of them
    used to be visible here.

      1. THE JSONB STAGE COLUMNS — the PIPELINE hand-off, how one agent passes
         structured output to the next. One step per populated column.

      2. THE ARTIFACT ROWS — every file the run generated. THIS IS THE ADDITION.
         Chat-driven work never touches the JSONB columns: `register_generated_file`
         writes an `artifacts` row and nothing else. So a run where somebody asked the
         Design agent for a PDF, approved it and downloaded it reported "No activity
         yet" — the panel looked broken standing next to a document that plainly
         existed. The artifacts are the evidence the work happened; not reading them
         was the bug.

    Steps are ordered by when they happened and indexed afterwards, so the two sources
    interleave by time rather than appearing as two blocks.

    StepIds stay deterministic — `{run_id}:{stage}` for a stage, `{run_id}:artifact:{id}`
    for a file — so a client can key on them across refetches.
    """
    steps: List[StepOut] = []
    run_id = str(run.id)
    for index, (stage, col_name) in enumerate(_JSONB_COLUMNS):
        payload = getattr(run, col_name, None)
        if payload is None:
            continue
        step_id = f"{run_id}:{stage}"
        steps.append(
            StepOut(
                id=step_id,
                runId=run_id,
                index=index,
                kind="artifact_write",
                agent=_STAGE_TO_AGENT.get(stage, stage),
                title=f"{stage.capitalize()} stage completed",
                status="approved",
                startedAt=_iso(run.created_at),
                completedAt=_iso(run.updated_at),
                durationMs=None,
                cost=None,
                model=None,
                summary=f"Artifacts persisted for {stage} stage",
                error=None,
            )
        )

    run_agent = _STAGE_TO_AGENT.get(getattr(run, "stage", "") or "", "orchestrator")
    for artifact in artifacts or []:
        # The FILENAME is the leaf of the stored path. A row with no path (blob storage
        # unconfigured, or an upload that never got that far) still describes a real
        # event, so it becomes a step named by its type rather than being dropped.
        stored_path = getattr(artifact, "blob_path", None) or ""
        leaf = stored_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        atype = getattr(artifact, "artifact_type", "artifact")
        created = getattr(artifact, "created_at", None)

        # SAY WHETHER THE BYTES ARRIVED. "Generated" reads as success, and this is the
        # one place a reader would otherwise never learn that an upload failed.
        stored = bool(getattr(artifact, "blob_url", None))
        size = getattr(artifact, "size_bytes", None)
        detail = f"{size:,} bytes" if size else atype
        summary = (
            f"{detail} stored" if stored
            else f"{detail} — recorded, but the file did not reach storage"
        )

        steps.append(
            StepOut(
                id=f"{run_id}:artifact:{artifact.id}",
                runId=run_id,
                index=0,  # replaced below, once everything is in time order
                kind="artifact_write",
                agent=run_agent,
                title=leaf or f"{atype} generated",
                status="approved" if stored else "failed",
                startedAt=_iso(created),
                completedAt=_iso(created),
                durationMs=None,
                cost=None,
                model=None,
                summary=summary,
                error=None,
            )
        )

    # Time order, then index — so stage steps and file steps interleave as they
    # happened rather than appearing as two blocks.
    steps.sort(key=lambda s: s.startedAt)
    for position, step in enumerate(steps):
        step.index = position
    return steps


# ── AuditEventOut ──────────────────────────────────────────────────────────────

class AuditActorOut(BaseModel):
    id: str
    name: str


class AuditResourceOut(BaseModel):
    type: str
    id: str
    name: Optional[str] = None


class AuditEventOut(BaseModel):
    """ORM AuditEvent -> Zod AuditEvent shape.

    Field mappings:
      actor_id       -> actor.id   (str; "system" when null)
      event_type     -> action     (must be a valid AuditAction enum value)
      resource_type  -> resource.type
      resource_id    -> resource.id
      payload        -> detail     (free-form; passed through as-is)
      created_at     -> at

    Derived fields (no ORM equivalents):
      projectId      = payload.get("project_id") if payload else null
      actor.name     = payload.get("actor_name", "system") if payload else "system"
      resource.name  = payload.get("resource_name") if payload else null
      ip             = payload.get("ip") if payload else null
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    tenantId: str
    projectId: Optional[str]
    action: str
    actor: AuditActorOut
    resource: AuditResourceOut
    at: str
    detail: Optional[dict]
    ip: Optional[str]

    @classmethod
    def from_orm_audit(cls, event: Any) -> "AuditEventOut":
        """Build an AuditEventOut from a shared.models.orm.AuditEvent instance."""
        payload = event.payload or {}
        actor_id = event.actor_id or "system"
        return cls(
            id=str(event.id),
            tenantId=str(event.tenant_id),
            projectId=str(payload["project_id"]) if payload.get("project_id") else None,
            action=event.event_type,
            actor=AuditActorOut(
                id=actor_id,
                name=payload.get("actor_name", actor_id),
            ),
            resource=AuditResourceOut(
                type=event.resource_type or "unknown",
                id=event.resource_id or "unknown",
                name=payload.get("resource_name"),
            ),
            at=_iso(event.created_at),
            detail={k: v for k, v in payload.items()
                    if k not in ("project_id", "actor_name", "resource_name", "ip")}
                   or None,
            ip=payload.get("ip"),
        )


# ── Approval response ────────────────────────────────────────────────────────

class ApprovalOut(BaseModel):
    """Response for POST /runs/{id}/approvals — records an AuditEvent."""
    runId: str
    decision: str
    reason: Optional[str]
    idempotencyKey: Optional[str]
    recordedAt: str


# ── ArtifactOut ───────────────────────────────────────────────────────────────

class ArtifactBodyRaw(BaseModel):
    """ORM-gap decision 1: body is always raw-kind referencing the blob download URL.

    The ORM Artifact stores a blob URL/path — it cannot reconstitute a typed
    discriminated body (story/c4_diagram/openapi_spec/…) without downloading and
    parsing the blob. In M4 we return a raw body with a markdown download link.
    Future milestones may add a parsed body cache column.
    """
    kind: str = "raw"
    markdown: str


class ArtifactOut(BaseModel):
    """ORM Artifact -> Zod Artifact shape.

    Field mappings (ORM-gap decision 1 in PLAN.md):
      artifact_type -> type
      run.stage     -> phase  (owning Run's stage; loaded by router via join)
      blob filename -> title   (the LEAF only — the path's leading segments are the
                                tenant/run routing, not a label anybody reads)
      1             -> version  (immutable blobs; version increments are deferred)
      sha256(blob_path||blob_url) -> contentHash
      "approved"    -> status  (blobs are approved artifacts)
      {kind:"document", filename, contentType, sizeBytes, stored} -> body
      "agent"       -> createdBy
      created_at    -> updatedAt  (immutable — no updated_at column on Artifact)

    ORM-gap decision 2: artifact is immutable. PATCH /artifacts/{id} returns 200
    with the unchanged ArtifactOut; mutations are accepted-but-ignored (logged).
    """
    model_config = ConfigDict(from_attributes=True)

    id: str
    projectId: str
    runId: Optional[str]
    type: str
    phase: str
    title: str
    version: int
    contentHash: str
    status: str
    # Loosened from ArtifactBodyRaw to a dict so synthesized artifacts can carry a
    # typed body (e.g. a "story" body for board-ingested work items), not just the
    # raw blob-download body. The frontend validates the discriminated union.
    body: dict[str, Any]
    # Absolute URL of the generated blob (docx/pptx/png/…), when the artifact has
    # one. The frontend list/panel download buttons link straight to this instead
    # of reconstructing a path — synthesized (story) artifacts leave it None.
    downloadUrl: Optional[str] = None
    createdBy: str
    createdAt: str
    updatedAt: str

    @classmethod
    def from_orm_artifact(cls, artifact: Any, run_stage: str, project_id: str) -> "ArtifactOut":
        """Build an ArtifactOut from an ORM Artifact + owning run_stage + project_id."""
        import hashlib
        from shared.services.artifact_store import is_blob_path  # noqa: PLC0415

        raw = (artifact.blob_path or "") + (artifact.blob_url or "")
        content_hash = hashlib.sha256(raw.encode()).hexdigest()

        stored_path = artifact.blob_path or ""
        # THE FILENAME, NOT THE PATH. The title used to be the whole stored path —
        # "document (81a736f4-…/67fbd232-…/document/sdlc-password-reset-design.pdf)" —
        # which is two UUIDs of tenant/run routing in front of the only part a person
        # reads. Those UUIDs are the tenant boundary, not a label.
        filename = stored_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        title = filename or artifact.artifact_type

        # THE RAW BLOB URL IS NOT A DOWNLOAD LINK. `blob_url` points straight at
        # `https://<account>.blob.core.windows.net/…`, and the account has public access
        # disabled — following it gets a 409/404 from Azure, never the file. The one
        # authorised path is `/artifacts/{id}/download`, which resolves the id through a
        # join on Run.tenant_id and streams the bytes; the BFF proxies it at the same
        # path under /api. Legacy rows, whose blob_path is a local filesystem path, keep
        # the static `/generated/…` URL because that endpoint deliberately refuses them.
        # THE STATUS IS NOW A FACT, not a constant. It was the literal string "approved"
        # for every artifact, because the table had no approval column — a placeholder
        # that read like a decision. Migration 0040 gave it one.
        _approval = getattr(artifact, "approval_status", None) or "approved"
        status = {
            "pending": "awaiting_approval",
            "approved": "approved",
            "rejected": "rejected",
        }.get(_approval, "awaiting_approval")
        _is_approved = _approval == "approved"

        is_blob = is_blob_path(stored_path, str(artifact.tenant_id))
        if is_blob:
            # NO URL WHEN THE BYTES ARE NOT THERE. store_artifact degrades on an upload
            # failure by writing the row with blob_url = None while still recording
            # blob_path, so the path alone proves nothing. Offering a link anyway is how
            # the list ends up with a download icon that 404s.
            # NO LINK BEFORE APPROVAL. A pending artifact's bytes sit under the tenant's
            # `_pending` prefix, not at this path — offering a download would 404, and
            # offering it at all would make the gate look decorative.
            download_path = (
                f"/api/artifacts/{artifact.id}/download"
                if (artifact.blob_url and _is_approved)
                else ""
            )
        elif stored_path:
            download_path = f"/generated/{stored_path}"
        else:
            download_path = ""

        return cls(
            id=str(artifact.id),
            projectId=project_id,
            runId=str(artifact.run_id),
            type=artifact.artifact_type,
            phase=run_stage or "requirements",
            title=title,
            version=1,
            contentHash=content_hash,
            status=status,
            # A DOCUMENT BODY, NOT A MARKDOWN LINK. This used to be
            # `{"kind": "raw", "markdown": "[Download artifact](…)"}`, which produced the
            # SAME link the list row already renders as an icon — two controls for one
            # file — and rendered through AdrViewer, so every PDF was captioned
            # "ADR · Architecture Decision Record". The fields below let the client show
            # what the file IS and offer exactly one way to fetch it.
            body={
                "kind": "document",
                "filename": filename,
                "contentType": artifact.content_type or None,
                "sizeBytes": artifact.size_bytes,
                # Whether there is a file to fetch RIGHT NOW. False while pending —
                # the bytes exist but under the pending prefix, and nobody has agreed
                # they belong to the project yet — and false when an approved
                # artifact's upload failed. `awaitingApproval` separates those two, so
                # the card can say "waiting for sign-off" rather than "not stored",
                # which would read as a fault.
                "stored": bool(download_path),
                "awaitingApproval": _approval == "pending",
                "rejected": _approval == "rejected",
            },
            downloadUrl=download_path or None,
            createdBy="agent",
            createdAt=_iso(artifact.created_at),
            updatedAt=_iso(artifact.created_at),  # immutable — no updated_at column
        )


def story_artifacts_from_run(run: Any, project_id: str) -> List["ArtifactOut"]:
    """Synthesize "story"-typed ArtifactOuts from a run's requirements_payload.

    Board ingestion (POST /projects/{id}/ingest-board) writes the pulled work items
    into Run.requirements_payload["stories"]; there is no structured-story table, so
    the Requirements page's story list is materialized here in the shape the frontend
    Artifact/StoryBody schema expects. Deterministic ids per (run, source_key).
    """
    import hashlib

    payload = getattr(run, "requirements_payload", None) or {}
    stories = payload.get("stories") if isinstance(payload, dict) else None
    if not stories:
        return []
    created = _iso(getattr(run, "created_at", None))
    out: List[ArtifactOut] = []
    for s in stories:
        if not isinstance(s, dict):
            continue
        source_key = str(s.get("source_key") or s.get("id") or "")
        title = s.get("title") or f"Work item {source_key}"
        description = s.get("description") or ""
        ac_raw = s.get("acceptance_criteria") or []
        ac_rows = [
            {"given": "", "when": "", "then": str(a)}
            for a in ac_raw
            if str(a).strip()
        ]
        body = {
            "kind": "story",
            "title": title,
            "description": description,
            "acceptanceCriteria": ac_rows,
            # THE BOARD'S OWN TYPE, carried through rather than flattened away.
            # ingest_board pulls EVERY work item on the board, so this list routinely
            # holds Epics, Tasks and Bugs alongside real stories — one project's
            # "stories" were an Epic and three Tasks about configuring the board
            # itself. Dropping the type presented all four to the Design agent as user
            # stories to design a system for, which is how a board-setup chore became
            # an eight-section architecture document.
            #
            # `kind` stays "story" because that is the ARTIFACT shape the frontend
            # renders; workItemType is what the item actually is on the board.
            "workItemType": s.get("work_item_type") or s.get("type") or "",
            # `boardUrl` is the browsable link to the item, resolved at ingest because
            # that is the only place holding both the connector and the item. Without it
            # the Requirements page rendered the work-item key as inert text: nothing in
            # the payload said which Jira site or ADO organisation the key belonged to.
            # Absent on items ingested before that, and on providers with no known URL
            # template — the key still shows, it just does not link.
            "traceability": (
                {
                    "jiraIssueKey": source_key,
                    **({"boardUrl": s["url"]} if s.get("url") else {}),
                }
                if source_key
                else {}
            ),
        }
        content_hash = hashlib.sha256(
            (source_key + title + description).encode()
        ).hexdigest()
        out.append(
            ArtifactOut(
                id=f"{run.id}:story:{source_key}",
                projectId=project_id,
                runId=str(run.id),
                type="story",
                phase="requirements",
                title=title,
                version=1,
                contentHash=content_hash,
                status="draft",
                body=body,
                createdBy="agent",
                createdAt=created,
                updatedAt=created,
            )
        )
    return out


# ── ConnectorOut ──────────────────────────────────────────────────────────────

class ConnectorCapability(BaseModel):
    """A single connector capability (matches Zod Capability shape)."""
    key: str
    description: str
    mode: str


class ConnectorOut(BaseModel):
    """Connector-health cache entry -> Zod Connector shape.

    Field mappings (ORM-gap decision 3 in PLAN.md):
      connector_name -> kind and name
      status         -> health   (mapped: "ok"/"healthy" -> "healthy", "error*" -> "degraded", else -> "disconnected")
      last_checked   -> lastCheckedAt
      True           -> installed  (entries in cache are installed connectors)
      []             -> capabilities  (capability enumeration deferred to M6)
      org_url/account_name -> account (if present in cache entry)

    Symbol is ConnectorOut (not ConnectorHealth) to avoid collision with the
    config.connectors types and the Zod ConnectorHealth enum.
    """

    id: str
    tenantId: str
    kind: str
    name: str
    installed: bool
    health: str
    capabilities: List[Any]
    lastCheckedAt: Optional[str]
    account: Optional[str] = None
    # Whether the requesting Business Unit was granted this kind (integration_grants).
    # Only set when the caller resolved a workspace (query param or header) — absent
    # otherwise, since "granted" has no meaning without a unit to check it against.
    granted: Optional[bool] = None

    @classmethod
    def from_health_entry(cls, connector_name: str, entry: dict, tenant_id: str) -> "ConnectorOut":
        """Reshape a connector_health_cache dict entry into ConnectorOut."""
        raw_status = str(entry.get("status", "")).lower()
        if raw_status in ("ok", "healthy"):
            health = "healthy"
        elif raw_status.startswith("error") or raw_status == "degraded":
            health = "degraded"
        else:
            health = "disconnected"
        last_checked = entry.get("last_checked") or entry.get("checked_at")
        last_checked_str: Optional[str] = None
        if last_checked:
            try:
                from datetime import datetime, timezone
                if isinstance(last_checked, (int, float)):
                    last_checked_str = datetime.fromtimestamp(last_checked, tz=timezone.utc).isoformat()
                else:
                    last_checked_str = str(last_checked)
            except Exception:
                last_checked_str = str(last_checked)
        # Canonicalise the internal provider key to the UI connector kind the
        # frontend contract (Zod ConnectorKind) and OAuth install service expect.
        # The GitHub Issues provider is keyed "github_issues" internally but is the
        # "github" connector to the UI.
        canonical_kind = {"github_issues": "github"}.get(connector_name, connector_name)
        return cls(
            id=canonical_kind,
            tenantId=tenant_id,
            kind=canonical_kind,
            name=entry.get("connector_name", canonical_kind),
            installed=True,
            health=health,
            capabilities=[],
            lastCheckedAt=last_checked_str,
            account=entry.get("org_url") or entry.get("account_name"),
        )


# ── Run create request / response ────────────────────────────────────────────

class RunCreateIn(BaseModel):
    """Request body for POST /runs — creates a Run and starts SDLCWorkflow (SC-01)."""
    project_id: str
    trigger: str = "manual"
    work_item_id: Optional[str] = None
    model_id: Optional[str] = None
    # The exact provider connection + model to run against (from /model/options).
    # Preferred over model_id — unambiguous when two keys expose the same model.
    offering_id: Optional[str] = None
    # Data-driven orchestration: optional agent filters forwarded to build_execution_plan.
    active_agents: Optional[List[str]] = None
    skip_agents: Optional[List[str]] = None
    # D1: direct/standalone mode — user issues a change instruction bypassing Design.
    # Absent in pipeline-mode runs; additive + optional (backward-compatible).
    change_request: Optional[ChangeRequest] = None
    # Chat-driven progression (Orchestrator Copilot): when true the run is created
    # The Copilot drives each stage conversationally and
    # gate approvals advance it via POST /runs/{id}/copilot/advance.
    conversational: bool = False


class RunCreateOut(BaseModel):
    """Response for POST /runs."""
    runId: str


# ── SignalAckOut ──────────────────────────────────────────────────────────────

class SignalAckOut(BaseModel):
    """Zod SignalAck shape — acknowledgement of a signal dispatch.

    This is a stub response.
    The ack records an AuditEvent so there is an audit trail even without
    The accepted=True response indicates the request was received and
    logged; it does NOT guarantee the signal was delivered to a running workflow.
    """
    accepted: bool
    signalName: str
    runId: str
    idempotencyKey: str


# ── Cursor-paginated audit response ──────────────────────────────────────────
# Cursor pagination for audit events: response carries the next cursor (an opaque
# ISO-8601 string encoding the last row's created_at) rather than a total count.
# This avoids a full-table COUNT on a high-frequency append-only write table and
# is consistent with the M4 infinite-scroll frontend pattern (D-04 Planner decision).

class CursorPage(BaseModel, Generic[T]):
    """Cursor-paginated response envelope for audit event streams.

    items      — page of results
    nextCursor — opaque ISO-8601 string; null when no more rows (T-M8-13 pagination stop)
    """

    items: List[T]
    nextCursor: Optional[str] = None


# ── EvalRecordOut (REQ-M9-14) ────────────────────────────────────────────────

class EvalRecordOut(BaseModel):
    """ORM EvalRecord -> Zod EvalRecord shape (REQ-M9-14).

    Field mappings:
      id           -> id       (UUID str)
      tenant_id    -> tenantId (UUID str)
      run_id       -> runId    (str | null)
      agent_type   -> agentType
      score        -> score    (float | null — Numeric(5,4) stored; float on the wire)
      signals      -> signals  (JSONB dict | null)
      created_at   -> createdAt (ISO-8601)
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    tenantId: str
    runId: Optional[str]
    agentType: str
    score: Optional[float]
    signals: Optional[dict]
    createdAt: str

    @classmethod
    def from_orm_eval(cls, record: Any) -> "EvalRecordOut":
        """Build an EvalRecordOut from a shared.models.orm.EvalRecord instance."""
        return cls(
            id=str(record.id),
            tenantId=str(record.tenant_id),
            runId=record.run_id,
            agentType=record.agent_type,
            score=float(record.score) if record.score is not None else None,
            signals=record.signals,
            createdAt=_iso(record.created_at),
        )


# ── CostBreakdownOut (REQ-M9-07, REQ-M9-09) ──────────────────────────────────

class CostBreakdownRow(BaseModel):
    """One per-model aggregate row in the GET /cost response.

    Grouped by model only — the agent that made the call is not a meaningful cost
    dimension (the same agent runs across projects and can use different models)."""

    model: str
    inputTokens: int
    outputTokens: int
    costUsd: float
    callCount: int


class CostBreakdownOut(BaseModel):
    """GET /cost response envelope — per-tenant spend over a time window.

    REQ-M9-07: rows grouped by (agent_type, model) plus grand totals, scoped to
    the requesting tenant via request.state.tenant_id (RLS + defense-in-depth WHERE).

    REQ-M9-09: budgetUsd/utilization/breached80 carry the computed budget-breach
    signal (tenant_llm_budget_utilization gauge); alert DELIVERY is deferred (DLT-9).
    """

    windowDays: int
    totalCostUsd: float
    totalInputTokens: int
    totalOutputTokens: int
    rows: List[CostBreakdownRow]
    generatedAt: str
    budgetUsd: float
    utilization: float
    breached80: bool


# ── Traces (GET /traces, /traces/metrics, /traces/{id}) ───────────────────────
# Mirror apps/web/lib/schemas/trace.ts exactly (Langfuse-sourced projection). The
# traces_router maps the self-hosted Langfuse Public API onto these shapes.
# NOTE: reuses the existing CostOut (defined above, used by RunOut/StepOut) — do
# NOT redefine it here or it shadows the original and breaks RunOut validation.

class TraceScoreOut(BaseModel):
    name: str
    value: float
    comment: Optional[str] = None


class SpanModelOut(BaseModel):
    provider: str
    id: str


class SpanOut(BaseModel):
    """One node in the trace tree (a Langfuse observation)."""

    id: str
    traceId: str
    parentId: Optional[str] = None
    name: str
    type: str  # generation | tool | retrieval | span | event
    level: str  # debug | default | warning | error
    startedAt: str
    startOffsetMs: int
    latencyMs: int
    status: str
    statusMessage: Optional[str] = None
    model: Optional[SpanModelOut] = None
    cost: Optional[CostOut] = None
    inputPreview: Optional[str] = None
    outputPreview: Optional[str] = None


class TraceListItemOut(BaseModel):
    """Row in the traces table (list projection — no spans)."""

    id: str
    runId: Optional[str] = None
    projectId: str
    projectName: str
    name: str
    agentType: str
    status: str
    startedAt: str
    latencyMs: int
    cost: CostOut
    model: str
    spanCount: int
    environment: str
    worstLevel: str
    scores: List[TraceScoreOut] = []


class TraceOut(TraceListItemOut):
    """Full trace detail — list item + spans + a deep-link to the Langfuse trace."""

    spans: List[SpanOut] = []
    langfuseUrl: Optional[str] = None
    release: Optional[str] = None
    userId: Optional[str] = None


class TraceMetricsByAgentOut(BaseModel):
    agentType: str
    traceCount: int
    errorRate: float
    latencyP50Ms: int
    latencyP95Ms: int
    costUsd: float


class TraceMetricsOut(BaseModel):
    windowDays: int
    totalTraces: int
    errorRate: float
    latencyP50Ms: int
    latencyP95Ms: int
    totalCostUsd: float
    byAgent: List[TraceMetricsByAgentOut] = []
    generatedAt: str


class ProjectCostSummaryOut(BaseModel):
    """Project-scoped LLM spend + token totals over a window (Langfuse-sourced)."""

    projectId: str
    windowDays: int
    totalCostUsd: float
    inputTokens: int
    outputTokens: int
    totalTokens: int
    generatedAt: str


# ── Shared helpers ────────────────────────────────────────────────────────────

def _iso(dt: Optional[datetime]) -> str:
    """Convert a datetime to ISO-8601 UTC string; return epoch string if None."""
    if dt is None:
        return "1970-01-01T00:00:00+00:00"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
