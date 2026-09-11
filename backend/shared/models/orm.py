"""SQLAlchemy 2.x ORM models for agentic_app.

Defines all 7 entities: Organization, Workspace, Project, Run, Artifact, AuditEvent, AgentCallLog.

Do NOT import from shared.db here â€” models are pure schema definitions with no connection dependency.
Do NOT import from config.env â€” no connection strings belong in model definitions.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # added in migration 0014 â€” server_default false keeps existing rows at false
    suspended: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False, server_default="false")
    # Monthly USD cost budget (0032). NULL = no org budget. Top of the org⊇workspace⊇project
    # hierarchy enforced by shared.services.budget_guard.
    monthly_budget_usd: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    workspaces: Mapped[list["Workspace"]] = relationship(back_populates="organization")


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Business-unit metadata + lifecycle â€” added in 0017 to make workspace a real,
    # self-describing boundary (each owns its own projects/members/models).
    business_unit: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cost_center: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Monthly USD cost budget (0032). NULL = inherit org / unlimited.
    monthly_budget_usd: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    data_classification: Mapped[str] = mapped_column(
        String(20), nullable=False, default="internal", server_default="internal"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    # The Langfuse organization this business unit owns (0058). BU = Langfuse org, so
    # every unit gets its own and traces cannot mix across units.
    #
    # THE ID IS THE BINDING, NOT THE NAME. Langfuse organization names are not unique and
    # the instance is shared with a sibling product — it already holds orgs called
    # `Payments` and `Lending` that are not ours. Matching by name would let a unit named
    # `Payments` silently adopt theirs and write this platform's traces into it. Recording
    # the id we created ourselves is what makes rename, grant and teardown unambiguous,
    # and what lets provisioning refuse to adopt an org it did not create.
    #
    # NULL means not provisioned yet: Langfuse was unreachable when the unit was created
    # (every hook is fail-soft) or the unit predates 0058. `scripts/sync_langfuse_orgs.py`
    # converges those.
    langfuse_org_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Kept only to detect drift — somebody renaming the org in the Langfuse UI. The id
    # above is what every operation actually addresses.
    langfuse_org_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    organization: Mapped["Organization"] = relationship(back_populates="workspaces")
    projects: Mapped[list["Project"]] = relationship(back_populates="workspace")


class Project(Base):
    """Maps to an ADO/Jira project. tenant_id = organization_id for RLS."""
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    # RLS anchor: no FK intentional â€” tenant_id is a policy column, not a relational FK
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    external_ref: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_kind: Mapped[str] = mapped_column(String(50), nullable=False, default="azure_devops", server_default="azure_devops")
    track: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # added in migration 0003 â€” server_default false keeps existing rows at false
    archived: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False, server_default="false")
    # Project-creation approval gate (migration 0028). 'active' | 'pending_approval' |
    # 'rejected' â€” see shared/governance/effects.py for what flips it.
    approval_status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    approval_decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approval_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    # Per-project stageâ†’MCP-server mapping {agent_id: [mcp_server_id, ...]} (migration 0024).
    # Chosen at project creation from the creator's MCP servers; threaded into each run.
    mcp_servers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Per-project stageâ†’connector-kind mapping {agent_id: [connector_kind, ...]} (migration 0025).
    connectors: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Per-(stage, tool) read/write mode, keyed "{agent_id}::{connector|mcp}::{ref}"
    # (migration 0024). THE access decision for connectors — see
    # shared/authz/connector_grants.py. NULL / a missing key means "both".
    tool_access_modes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Whether agents on this project may read ONLY published artifact versions
    # (migration 0046). FALSE means today's behaviour exactly: consumers take the
    # latest working payload, published or not.
    #
    # DEFAULT FALSE AND NOT NULL ON PURPOSE. Enabling it for a project whose stages
    # have never published makes every agent correctly report "no approved upstream",
    # which is the right answer and is indistinguishable from an outage to whoever is
    # looking. That has to be a switch somebody throws, not a migration that lands.
    enforce_artifact_publication: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Where the project stands as DELIVERY WORK, set by a person (0054) — distinct
    # from `approval_status`, which is the creation gate, and from whatever the agents
    # happen to be running right now. Constrained to _DELIVERY_STATUSES by
    # ck_project_delivery_status; see the migration for why that CHECK exists.
    delivery_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_started",
        server_default=text("'not_started'"),
    )
    # TOTAL USD cost budget (0032). NULL = inherit workspace / unlimited. The name is
    # historical: spend accumulates over the project's life and never resets (see
    # shared/services/budget_store.py).
    monthly_budget_usd: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    # How long that total is authorised for (0035). Either end may be NULL — a start
    # with no end and an end with no start are both meaningful. Outside the window a
    # project with a budget cannot spend: see budget_guard._window_state.
    budget_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    budget_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="projects")
    runs: Mapped[list["Run"]] = relationship(back_populates="project")


class Run(Base):
    """One SDLC pipeline execution (requirements -> design -> dev -> test -> deploy)."""
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Nullable since 0005: webhook-triggered runs carry a provider project key, not a local project UUID.
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(50), nullable=False, default="requirements", server_default="requirements")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending", server_default="pending")
    # Phase 1 (model provider): the model chosen for this run; NULL â†’ org default at dispatch.
    model_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The exact provider connection + model used (unambiguous when two keys expose the
    # same model_id). NULL â†’ resolved to the org default at dispatch. Added in 0016.
    offering_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Audit snapshot of the provider connection name at dispatch â€” survives later
    # rename/delete of the offering so run history stays legible. Added in 0016.
    model_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # How the run was initiated: 'manual' (POST /runs) or 'webhook' (inbound delivery). Added in 0005.
    trigger: Mapped[str] = mapped_column(String(50), nullable=False, default="manual", server_default="manual")
    # Typed artifacts â€” populated by each agent as it completes its stage
    requirements_payload: Mapped[dict | None] = mapped_column(JSONB)
    requirements_artifacts: Mapped[dict | None] = mapped_column(JSONB)
    design_artifacts: Mapped[dict | None] = mapped_column(JSONB)
    # The PM agent's output (0041): tasks, schedule, assignments, risks, baseline.
    # Sits between design and development because that is where it runs.
    plan_artifacts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    development_artifacts: Mapped[dict | None] = mapped_column(JSONB)
    testing_artifacts: Mapped[dict | None] = mapped_column(JSONB)
    code_review_artifacts: Mapped[dict | None] = mapped_column(JSONB)
    security_artifacts: Mapped[dict | None] = mapped_column(JSONB)
    deployment_artifacts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    documentation_artifacts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Orchestrator state â€” tracks which SDLC stage is active and whether a human gate is pending
    current_stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    gate_pending: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False, server_default="false")
    # Who started this run. The left-hand side of the no-self-approval rule (0038):
    # whoever ran the agent may not be the one who accepts its output. NULL means
    # "unknown initiator" (webhook runs, and every run predating 0038) and is treated
    # as not-proven-self rather than as a denial.
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    project: Mapped["Project"] = relationship(back_populates="runs")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="run")


class ArtifactConsumptionGrant(Base):
    """Permission for one consuming stage to read one non-published version (0047).

    NARROW ON PURPOSE: one version, one consumer. Not "design may read drafts", which
    would be a standing licence indistinguishable from turning enforcement off. The
    next draft is a new question.

    Revoked, never deleted — a run that read under this grant has to stay explicable.
    """
    __tablename__ = "artifact_consumption_grants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifact_versions.id", ondelete="CASCADE"), nullable=False
    )
    consumer_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        UniqueConstraint("version_id", "consumer_stage",
                         name="uq_consumption_grant_version_consumer"),
    )


class ArtifactConsumption(Base):
    """Which published version a run read, and when (migration 0046).

    THE QUESTION THIS ANSWERS is "what did this deployment actually build on", a month
    later, when something has gone wrong. Today it has no answer at all — every
    consumer takes the latest non-null payload ordered by `created_at desc`, so there
    is no record that a particular run read a particular thing.

    ONE ROW PER READ, not per (run, version) pair. An agent may legitimately call its
    upstream-read tool more than once in a turn, and collapsing those would discard the
    fact that it did. `consumed_at` separates them.

    `producing_stage` and `version` are denormalised deliberately: the evidence view
    answers without a join, and the row still means something if the version it points
    at is ever removed.
    """
    __tablename__ = "artifact_consumptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    #: NULLABLE since 0053 — a document read has no version. Exactly one of
    #: `version_id` / `artifact_id` is set, enforced by a CHECK.
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifact_versions.id", ondelete="CASCADE"), index=True
    )
    #: Set when a run read a DOCUMENT directly — a project-level one, which belongs to
    #: no stage and so has no version to point at, or a covered one fetched on its own.
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    #: Denormalised from the version. None for a project-level document, which belongs
    #: to no stage by definition.
    producing_stage: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int | None] = mapped_column(Integer)
    consumer_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    consumer_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), index=True
    )
    consumed_by: Mapped[str | None] = mapped_column(String(255))
    # True when the read was allowed by an owner-issued grant rather than by the
    # version being published (0050). An exception listed identically to routine
    # approved work is how a reviewer learns to stop reading the column.
    via_grant: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArtifactVersion(Base):
    """A frozen, numbered snapshot of one stage's artifact payload (migration 0045).

    THE SECOND ARTIFACT SHAPE, made approvable. `Artifact` above is a blob document
    and already carries its own approval columns. The actual HAND-OFF between agents
    is `runs.{stage}_artifacts` — a JSONB column written IN PLACE, with no history —
    so approving it would be theatre: the approval record survives, the thing it
    approved does not.

    `runs.{stage}_artifacts` stays the agent's working draft. Publishing COPIES it
    here; a new run creates version N+1 and never edits N.

    IMMUTABILITY IS ENFORCED BY A DATABASE TRIGGER (`artifact_versions_freeze`), not
    by this class. Do not add setters for `payload`, `content_hash`, `covers` or the
    identity columns expecting them to work — an UPDATE touching any of them raises.
    Only the lifecycle columns move.
    """
    __tablename__ = "artifact_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # A BACKEND stage name (progression.STAGE_ORDER) — `code_review`, never the UI's
    # `review`. Those two diverging is what left the owner map wrong for months.
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    # SET NULL, not CASCADE: a version outlives the run that made it, because deleting
    # a run must not destroy the evidence of what was approved.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), index=True
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # `artifacts.id` values this version signs off, so a design document and its
    # diagram publish as one unit. Deliberately not a FK: it is a list, and those rows
    # keep their own independent approval_status.
    covers: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    # draft | published | rejected | superseded. Defaults to the UNPUBLISHED value so
    # a writer that has not been taught about this column fails closed.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )
    # NOT NULL because it is half of the self-publication check, which the schema
    # enforces: published_by <> produced_by.
    produced_by: Mapped[str] = mapped_column(String(255), nullable=False)
    published_by: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("project_id", "stage", "version",
                         name="uq_artifact_versions_project_stage_version"),
    )


class Artifact(Base):
    """A blob-backed output file (DOCX, PDF, diagram PNG, etc.). Immutable after creation.

    SCOPE (migration 0052). A document belongs to a PROJECT, and optionally to one
    AGENT:

        stage IS NULL     project-level — a policy, a standard. Every agent may read
                          it once approved.
        stage = 'design'  agent-level — filed under that agent, and readable by every
                          agent once approved. This used to additionally require a
                          published version to name it in `artifact_versions.covers`;
                          approval is the whole gate now.

    Before 0052 both facts were recovered by joining to `Run`, so a document was only
    "the Design agent's" by accident of which run produced it, and a project-wide one
    could not exist. `blob_path_for` had emitted the project/agent path all along.
    """
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    #: The producing agent, or None for a project-level document. A BACKEND stage name
    #: (`code_review`, never the UI's `review`).
    stage: Mapped[str | None] = mapped_column(String(32))
    #: NULLABLE since 0052: a document outlives the run that made it, and a
    #: hand-uploaded one never had one. SET NULL, so deleting a run cannot destroy an
    #: approved document.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    #: Who put it here — deliberately distinct from `approved_by`. Collapsing the two
    #: would make self-approval invisible.
    uploaded_by: Mapped[str | None] = mapped_column(String(255))
    #: Why this was put forward, in the uploader's words. Nullable and staying that
    #: way — requiring one gets "." typed into a box, and a meaningless note is worse
    #: than none because the approver still has to read it.
    upload_note: Mapped[str | None] = mapped_column(Text)
    artifact_type: Mapped[str] = mapped_column(String(100), nullable=False)
    blob_url: Mapped[str | None] = mapped_column(Text)
    blob_path: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    # Approval gate (migration 0040). "pending" until whoever runs the project accepts
    # it; the bytes sit under the tenant's _pending prefix until then. The default is
    # deliberately the UNAPPROVED value so a writer that has not been taught about this
    # column fails closed rather than adding to the project's shared record silently.
    approval_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    # No updated_at â€” the CONTENT is immutable after creation; the approval columns
    # above are the one thing that changes, and they are decisions about the artifact
    # rather than edits to it.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped["Run | None"] = relationship(back_populates="artifacts")


class OrchestratorDeliverable(Base):
    """One document an Orchestrator agent produced. Append-only.

    NOT an `Artifact`. `artifacts` rows carry `approval_status` because a STANDALONE
    agent wrote them and a human accepts them. The Orchestrator's agents share the
    standalone agents' names and capability and are a different thing: the runner is a
    Project Admin who already owns all nine, and there are no gates. So its output is a
    separate concept with no approval column anywhere in its shape.

    APPEND-ONLY BY DESIGN. Any agent can run at any time here, and the same agent can
    run repeatedly in one conversation. Every run is kept and the panel shows them
    newest-first, so a re-run never destroys the document it replaces.
    """
    __tablename__ = "orchestrator_deliverables"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Nullable to match `runs.project_id` (nullable since 0005 for webhook runs).
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )

    agent_id: Mapped[str] = mapped_column(String(50), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(50))
    source: Mapped[str | None] = mapped_column(String(50))

    # No `updated_at`. There is no update: a re-run is a new row.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Deployment(Base):
    """One requested deployment action, and the human decision about it.

    NOTHING REACHES AN ENVIRONMENT WITHOUT A NAMED HUMAN APPROVING IT. Generating
    deployment files is free and needs no row here. Creating a pipeline, starting a run,
    or applying to a cluster each change something outside the platform, and each one
    becomes a row that starts `pending`.

    THE REQUEST IS FROZEN AT APPROVAL. `request` holds exactly what was asked for -
    which pipeline, which branch, which environment, which variables. It is what the
    approver read. Letting it change after approval would mean somebody approves a
    staging deploy and a production one runs, which is the whole attack this table
    exists to prevent.

    ONE APPROVAL IS ONE DEPLOYMENT. `executed_at` is set the moment the action fires,
    and an already-executed row can never fire again. Without that, a single approval is
    a standing licence to deploy for as long as the row exists.

    `approval_status` defaults to the UNAPPROVED value so a writer that has not been
    taught about this column fails closed - the same reasoning as Artifact.
    """
    __tablename__ = "deployments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    #: The agent run that proposed this. Nullable: a deploy triggered from the UI has
    #: no agent run behind it, and inventing one would corrupt the run's history.
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id"), index=True)

    #: "create_pipeline" | "run_pipeline" | "direct_apply"
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    #: "azure_pipelines" | "github_actions" | "kubernetes"
    target_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    environment: Mapped[str] = mapped_column(String(100), nullable=False)
    #: Exactly what was asked for - what the approver read. Immutable once approved.
    request: Mapped[dict] = mapped_column(JSONB, nullable=False)

    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    approval_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    #: "not_started" | "running" | "succeeded" | "failed" | "canceled" | "error".
    #: Kept apart from approval_status: an approved deployment that failed is not a
    #: rejected one, and collapsing the two loses the fact a human said yes.
    execution_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="not_started", server_default="not_started"
    )
    #: Set the instant the action fires. Non-null means this approval is spent.
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_id: Mapped[str | None] = mapped_column(String(100))
    external_url: Mapped[str | None] = mapped_column(Text)
    #: What actually happened - failing stage, error, timings.
    outcome: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditEvent(Base):
    """Append-only audit log â€” no updates, no deletes."""
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(100))
    resource_id: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    # No updated_at â€” append-only table; rows are never modified after insert
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class AgentCallLog(Base):
    """Per-LLM-call telemetry log. High-frequency write table; immutable after insert."""
    __tablename__ = "agent_call_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # run_id stored as string to avoid strict FK during M1; M2 adds proper FK after data migration
    run_id: Mapped[str | None] = mapped_column(String(255), index=True)
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    # Numeric(10,6) â€” exact decimal required for financial aggregates; Float would introduce rounding errors
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    # No updated_at â€” high-frequency write table; immutable after insert
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class EvalRecord(Base):
    """Per-run quality eval record. Append-only quality log; immutable after insert (REQ-M9-10)."""
    __tablename__ = "eval_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # run_id stored as string â€” matches AgentCallLog convention (Run.id is a string in this codebase)
    run_id: Mapped[str | None] = mapped_column(String(255), index=True)
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Numeric(5,4) â€” exact decimal for a 0.0000-1.0000 quality score; Float would drift
    score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    signals: Mapped[dict | None] = mapped_column(JSONB)
    # No updated_at â€” append-only quality log, immutable after insert
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class UsageMonthly(Base):
    """Durable per-scope monthly LLM spend rollup (migration 0032).

    One row per (tenant_id, scope, scope_id, month); UPSERT-ed by the usage meter
    on every LLM completion. Authoritative source for budget reporting/enforcement
    and the seed for the hot Redis cost counters. Tenant-private (FORCE RLS)."""
    __tablename__ = "usage_monthly"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # RLS anchor: no FK intentional — tenant_id is a policy column.
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)  # org | workspace | project
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    month: Mapped[str] = mapped_column(String(6), nullable=False)  # 'YYYYMM' (UTC)
    cost_usd: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False, server_default="0")
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "scope", "scope_id", "month", name="uq_usage_monthly_scope_month"),
    )


# ---------------------------------------------------------------------------
# Tenant-scoped FORCE-RLS table registry â€” single source of truth (REQ-M9-10).
#
# Every table here ships with all four statements: ENABLE ROW LEVEL SECURITY,
# CREATE POLICY tenant_isolation (USING), CREATE POLICY tenant_isolation_insert
# (WITH CHECK), and FORCE ROW LEVEL SECURITY. FORCE on its own leaves a table
# wide open, so none of the four may be skipped.
#
# This list was previously 14 entries while the live database had 21 protected
# tables. Anything generating DDL from this constant would have silently shipped
# seven tenant-scoped tables — including app_secrets, model_providers and the
# role-assignment table — with no isolation at all. Keep it in step with the
# tables that actually carry a tenant_id column; test_rls_coverage guards it.
# ---------------------------------------------------------------------------

class LangfuseBinding(Base):
    """Which Langfuse project one SDLC project's traces go to, and the key that reaches it.

    THE ROW THAT MAKES ISOLATION REAL. Traces used to land in one shared Langfuse project
    and be told apart by a `project:` tag — an application-level promise, where PRD §45
    asks for project-level isolation as a release gate. Each SDLC project now owns a
    Langfuse project, under a Langfuse organization per business unit, and this table is
    the map. A key pair only reaches its own project, so a wrong filter returns nothing
    instead of another unit's prompts.

    ONE ACTIVE BINDING PER PROJECT, enforced by a partial unique index rather than a
    plain constraint: re-provisioning after a Langfuse rebuild must be able to leave the
    old row behind for forensics while only one binding is live.

    KEYS ARE STORED ENCRYPTED with the platform's existing secret-store key, not in
    plaintext. They are credentials to a system holding every prompt and completion this
    platform produces; a database dump should not be enough to read them.
    """

    __tablename__ = "langfuse_bindings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # RLS anchor, same convention as every other tenant-scoped table.
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # The business unit -> becomes the Langfuse ORGANIZATION.
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    # The SDLC project -> becomes the Langfuse PROJECT.
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)

    langfuse_org_id: Mapped[str] = mapped_column(String(255), nullable=False)
    langfuse_project_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    langfuse_project_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Stored per row, not read from config: a binding must keep working after the
    # platform is pointed at a different Langfuse, or it silently reads the wrong host.
    langfuse_host: Mapped[str] = mapped_column(String(512), nullable=False)

    public_key_encrypted: Mapped[str] = mapped_column(Text(), nullable=False)
    secret_key_encrypted: Mapped[str] = mapped_column(Text(), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index(
            "ix_langfuse_binding_active_project",
            "project_id",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
    )


# BASELINE-ERA TABLES ONLY. Migration 0001 imports this list and applies RLS to every
# entry, so a name here that 0001 does not also CREATE breaks every FRESH database —
# `relation "x" does not exist` — while an already-migrated one carries on fine, because
# 0001 ran long ago. That is exactly how `langfuse_bindings` slipped in and passed
# locally: it is created in 0057, and the local database was already past 0001.
#
# A table added after the baseline applies its own RLS in its own migration, which is
# what `workstreams`, `org_model_grants`, `approval_requests`, `org_settings` and the
# rest already do. Do not add to this tuple.
_RLS_TABLES: tuple[str, ...] = (
    "agent_call_logs",
    "agent_profiles",
    "agent_skill_toggles",
    "agent_skills",
    "app_secrets",
    "artifacts",
    "audit_events",
    "conversation_messages",
    "conversation_sessions",
    "custom_role_permissions",
    "custom_roles",
    "dev_workspaces",
    "eval_records",
    "mcp_servers",
    "model_offerings",
    "model_providers",
    "projects",
    "role_bindings",
    "runs",
    "usage_monthly",
    "workspace_connectors",
)


# ---------------------------------------------------------------------------
# RBAC catalog tables (GLOBAL â€” no tenant_id, no RLS per D-03)
# ---------------------------------------------------------------------------

class Role(Base):
    """Named platform role. Global catalog â€” no tenant scope (D-03)."""
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str | None] = mapped_column(String(255))


class Permission(Base):
    """Granular permission string. Global catalog â€” no tenant scope (D-03)."""
    __tablename__ = "permissions"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)


class RolePermission(Base):
    """Association between a role and a permission. Global catalog edge (D-03)."""
    __tablename__ = "role_permissions"

    role_name: Mapped[str] = mapped_column(ForeignKey("roles.name"), primary_key=True)
    permission_name: Mapped[str] = mapped_column(ForeignKey("permissions.name"), primary_key=True)


# ---------------------------------------------------------------------------
# RBAC assignment table (TENANT-SCOPED â€” forced RLS added in migration 0007)
# ---------------------------------------------------------------------------

class RoleBinding(Base):
    """Grants a user one role at one scope: an organization, a business unit, or a project.

    Replaces the old user_workspace_roles, which could only ever bind at the business-unit
    level (its `workspace_id` column). The frontend's model is a three-level cascade —
    organization -> business unit -> project — so the scope is now expressed as a
    (scope_kind, scope_id) pair rather than a single FK. A person holds a *set* of
    bindings; the role is a property of the binding, never of the person.

    `scope_id` deliberately carries no foreign key: it points at organizations.id,
    workspaces.id or projects.id depending on scope_kind, and no single FK can express
    that. Referential integrity for it is enforced in shared/authz/grant.py on write.

    `tier` separates governance (approves work) from delivery (does the work). The
    invariant the UI relies on is that nobody holds both tiers *within one scope* —
    that would let a person approve their own work — while the same person may well be
    governance in one scope and delivery in another. Cross-scope combinations are legal,
    so this cannot be a table constraint; grant.py enforces it per scope.

    No relationship/back_populates into this table from Role or Workspace — a lazy-load
    from the non-RLS catalog side would cross the tenant RLS boundary (Pitfall 1).
    """
    __tablename__ = "role_bindings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # WHICH scope this binding applies to. See the class docstring for why scope_id has no FK.
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    role_name: Mapped[str | None] = mapped_column(ForeignKey("roles.name"), nullable=True)
    custom_role_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("custom_roles.id", ondelete="CASCADE"), nullable=True, index=True
    )

    tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )

    # Added to the DATABASE by migration 0003 and never mirrored here, which is how a
    # temporary elevation came to be unenforceable on the login path: `resolve_
    # permissions_for_user` builds its query from this model, so a column the model does
    # not declare is a column that query cannot filter on. `can_perform` used raw SQL and
    # therefore honoured the expiry, giving the two permission readers different answers
    # about the same binding.
    #
    # `expires_at IS NULL` means permanent. A non-null value in the past makes the
    # assignment inert immediately — enforced by the clock, with no sweep job, because
    # `status` is a flag somebody has to remember to flip.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    granted_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # RLS anchor: no FK intentional â€” tenant_id is a policy column, not a relational FK (mirrors Project.tenant_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "user_id", "scope_kind", "scope_id", "role_name", name="uq_role_binding_scope_role"
        ),
        CheckConstraint(
            "(role_name IS NOT NULL AND custom_role_id IS NULL) "
            "OR (role_name IS NULL AND custom_role_id IS NOT NULL)",
            name="ck_role_binding_exactly_one_role",
        ),
        CheckConstraint(
            # Four levels, matching migration 0003 and `can_perform.SCOPE_ORDER`. The
            # model still said three, so a workstream-scoped binding was representable
            # in the database and rejected by the model.
            "scope_kind IN ('organization', 'business_unit', 'project', 'workstream')",
            name="ck_role_binding_scope_kind",
        ),
        CheckConstraint(
            "tier IS NULL OR tier IN ('governance', 'delivery')",
            name="ck_role_binding_tier",
        ),
        CheckConstraint(
            "status IN ('active', 'invited', 'deactivated')",
            name="ck_role_binding_status",
        ),
    )


class WorkspaceConnector(Base):
    """Per-workspace connector enablement. Credentials remain per-tenant in KV;
    this table tracks which workspaces have enabled which connector kinds so the
    Integrations page shows workspace-specific connector state.
    Tenant-scoped under FORCE RLS (migration 0019).
    """
    __tablename__ = "workspace_connectors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("workspace_id", "kind", name="uq_workspace_connector_kind"),)


class CustomRole(Base):
    """Tenant-defined role (D-1 hybrid). Tenant-scoped under FORCE RLS (migration 0012)."""
    __tablename__ = "custom_roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # RLS anchor: no FK intentional â€” policy column (mirrors Project.tenant_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    # Who OWNS this role, and therefore where it may be assigned (migration 0004).
    # "organization" = anywhere in the tenant; "business_unit" = only inside that unit.
    scope_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="organization", server_default="organization"
    )
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Uniqueness is per OWNER scope, not per tenant: two business units may each define
    # a role called "Reviewer" without colliding.
    __table_args__ = (
        UniqueConstraint("tenant_id", "scope_id", "name", name="uq_custom_role_scope_name"),
    )


class CustomRolePermission(Base):
    """Permission grant edge for a custom role. Tenant-scoped under FORCE RLS (0012)."""
    __tablename__ = "custom_role_permissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    custom_role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("custom_roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    permission_name: Mapped[str] = mapped_column(ForeignKey("permissions.name"), nullable=False)
    # RLS anchor
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("custom_role_id", "permission_name", name="uq_custom_role_perm"),
    )


class ModelProvider(Base):
    """A tenant's configured LLM provider connection (BYOK). Tenant-scoped under
    FORCE RLS (migration 0015). The API key itself lives in the secret store â€”
    only a non-secret `secret_ref` is stored here."""
    __tablename__ = "model_providers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # RLS anchor: no FK intentional â€” policy column (mirrors Project.tenant_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Workspace ownership â€” added in 0017. NULL = org-shared (visible to every
    # workspace); a value = owned by that workspace. Dispatch resolves by
    # offering_id so this only governs visibility/selection, never execution.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    # Curated preset (anthropic|openai|google) OR any LiteLLM provider slug when
    # is_custom â€” onboarding is dynamic, gated only by model:manage RBAC.
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # NULLABLE SINCE 0017, and deliberately: a connection can be registered with no
    # key at all so its models are granted centrally while a Business Unit or project
    # supplies one later, and clearing a key (`api_key: ""`) leaves a provider in
    # exactly that state. Every database has the column nullable; this line said
    # otherwise, which is a type that promises a string and hands back None. Nothing
    # reads it through this class today — `model_config` goes via SQL and already
    # tests `secret_ref is not None` — so the declaration was wrong alone, and
    # tightening the column instead would have deleted every keyless provider.
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Optional custom endpoint (OpenAI-compatible / self-hosted / gateway base URL).
    api_base: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # True when not a curated preset â€” drives the "Custom" UI treatment.
    is_custom: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False, server_default="false")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unverified", server_default="unverified")
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ModelOffering(Base):
    """An enabled model for a tenant + the single org default. Tenant-scoped under
    FORCE RLS (migration 0015)."""
    __tablename__ = "model_offerings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True, server_default="true")
    is_default: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False, server_default="false")
    # USD per 1M tokens. Mandatory for custom models (cost attribution rides on
    # the model); NULL for catalog models (priced from LiteLLM's cost map).
    input_price_per_million: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    output_price_per_million: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    # Per-model usage limits (0030). NULL = no limit. Tenant/workspace scoping is
    # inherited from the parent model_providers row. rpm_limit is enforced live in
    # resolve_model_for_run; tpm_limit / cost_limit_usd are stored (enforcement is a
    # follow-up, gated on per-call token/cost accounting).
    rpm_limit: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    tpm_limit: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    cost_limit_usd: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("provider_id", "model_id", name="uq_offering_provider_model"),
    )


class AppSecret(Base):
    """Encrypted secret material for the DB secret-store backend (dev / no-KV).
    Tenant-scoped under FORCE RLS (migration 0015). `ciphertext` is Fernet-encrypted
    with SECRET_STORE_KEY â€” never the plaintext."""
    __tablename__ = "app_secrets"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ref: Mapped[str] = mapped_column(String(255), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class McpServer(Base):
    """A tenant-registered MCP (Model Context Protocol) server, consumed as agent
    tools at run time. Tenant-scoped under FORCE RLS (migration 0023). Credentials
    (env vars / headers) live in the secret store â€” only non-secret refs are stored
    here, mirroring ModelProvider.secret_ref."""
    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    # RLS anchor: no FK intentional â€” policy column (mirrors Project.tenant_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    server_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # streamable_http | sse | stdio
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    # http/sse transports
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # stdio transport
    command: Mapped[str | None] = mapped_column(String(512), nullable=True)
    args: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Non-secret references into the secret store (env vars / HTTP headers).
    env_vars_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    headers_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True, server_default="true")
    # Agent ids this server is allowed to serve; NULL = every stage.
    allowed_stages: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Cached {name, description} from the last successful probe/test-connection.
    tools_snapshot: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Admin-asserted capability tags (decision DP2). NULL/[] = server provides no
    # capability that can satisfy required_capabilities. Validated native-only-free.
    capabilities: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "server_name", name="uq_mcp_server_tenant_name"),
    )


class AgentProfile(Base):
    """Org-editable, versioned behavior layer for an agent (decisions D5, D6, DP3).

    Resolved org -> workspace -> project (the in-code base role prompt is the floor).
    Tenant-scoped under FORCE RLS. Owns prompt layers, enabled/disabled tools,
    thresholds, pinned reference-doc summaries, and output-contract additions.
    """
    __tablename__ = "agent_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)        # org | workspace | project
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True, server_default="true")
    prompt_prepend: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_append: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled_capabilities: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    disabled_curated: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    primary_overrides: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    thresholds: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reference_doc_summaries: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    output_contract_extra: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "scope", "scope_id", "version",
                         name="uq_agent_profile_scope_version"),
    )


class AgentSkill(Base):
    """Tenant-scoped, versioned skill content for an agent (Phase 4 skills platform).

    Holds custom (org-authored) skill content. Vendor skills live on disk (see
    shared.skills.registry) and are surfaced read-only; only overrides/toggles and
    custom skills are persisted here. Resolved org -> workspace -> project.
    Tenant-scoped under FORCE RLS, mirroring AgentProfile.
    """
    __tablename__ = "agent_skills"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)        # org | workspace | project
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    skill_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True, server_default="true")
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    when_to_use: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    runtime: Mapped[str] = mapped_column(String(8), nullable=False, default="llm", server_default="llm")
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="custom", server_default="custom")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "scope", "scope_id", "skill_key", "version",
                         name="uq_agent_skill_version"),
    )


class AgentDefaultEvaluation(Base):
    """Durable PASS/FAIL record for one evaluation run against one specific
    AgentProfile or AgentSkill draft VERSION (Phase 4 skills platform,
    sub-project 4). Append-only — an evaluation is never edited, only superseded
    by a fresh run (e.g. after the draft is edited into a new version, which gets
    its own row here). Tenant-scoped under FORCE RLS, mirroring AgentSkill.
    """
    __tablename__ = "agent_default_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # 'profile' -> AgentProfile.id, 'skill' -> AgentSkill.id — mirrors effects.py's
    # existing target_ref dual-resolution convention rather than a new one.
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(50), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)  # org | workspace | project
    result: Mapped[str] = mapped_column(String(8), nullable=False)  # pass | fail
    # Numeric(5,4), not Float — same "0.0000-1.0000 quality score, Float would
    # drift" reasoning already documented on EvalRecord.score in this same file.
    score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    signals: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    evaluator_id: Mapped[str] = mapped_column(String(255), nullable=False)
    evaluator_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportSourceAllowlist(Base):
    """Org-Admin-governed allowlist of approved external import sources
    (Agent Studio sub-project 5). A declared import source matches a pattern
    via `agent_skills.py`'s `_matches_import_source` — a BOUNDARY-AWARE prefix
    match (exact equality, or a prefix match landing on a real `/` boundary),
    never a bare `str.startswith()` (that was a real subdomain-confusion
    bypass, found and fixed during review — a no-trailing-slash pattern like
    `https://trusted.example.com` would otherwise also match
    `https://trusted.example.com.evil.com/...`) and never a regex (an
    admin-typed pattern must never become a regex-injection surface). Mirrors
    OrgModelGrant's "the Org Admin governs the catalogue" doctrine: a BU Admin
    cannot self-approve their own import source, the same way they cannot
    self-grant a model. Tenant-scoped under FORCE RLS, mirroring
    AgentDefaultEvaluation.
    """
    __tablename__ = "import_source_allowlist"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    source_pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentSkillToggle(Base):
    """Per-scope enable/disable state for a skill (vendor or custom origin).

    Separated from AgentSkill so toggling a vendor skill (whose content lives on
    disk) needs no content row. Tenant-scoped under FORCE RLS.
    """
    __tablename__ = "agent_skill_toggles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(50), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)        # org | workspace | project
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)       # vendor | custom
    skill_key: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "scope", "scope_id", "origin", "skill_key",
                         name="uq_agent_skill_toggle"),
    )


# ---------------------------------------------------------------------------
# Identity table (GLOBAL â€” non-RLS; login lookup precedes tenant context, D-08)
# ---------------------------------------------------------------------------

class User(Base):
    """Platform user identity. Global (non-RLS) â€” lookup by sub before tenant GUC is set (D-08)."""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    # SCIM linkage for future 7.4 provisioning (D-08 / A1)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # Local email+password auth (Phase 3). NULL for SCIM/OIDC-only or not-yet-set users.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Informational only â€” not an RLS anchor; login resolves sub before tenant context exists
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # M7.4 Wave A: SCIM soft-deactivate flag (D-01). False = deprovisioned; future logins blocked.
    # DB constraint uq_users_external_id_tenant is migration-authoritative (migration 0008).
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)


class PlatformUser(Base):
    """Application-team (vendor) tier identity. Global â€” NOT tenant-scoped (Phase 3)."""
    __tablename__ = "platform_users"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    platform_role: Mapped[str] = mapped_column(String(32), nullable=False, default="platform_admin", server_default="platform_admin")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentSession(Base):
    """Per-agent-session typed artifact store. Session-keyed (high-entropy hash or
    project-scoped id), GLOBAL (non-RLS) â€” faithful replacement for the legacy Django
    `agent_session` table. tenant_id is recorded for future RLS hardening but is not an
    RLS anchor (mirrors Django, which had no isolation here)."""
    __tablename__ = "agent_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False, default="requirements", server_default="requirements")
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    # Typed artifacts â€” strict superset of Django's columns (adds code_review/security/deployment)
    requirements_payload: Mapped[dict | None] = mapped_column(JSONB)
    design_artifacts: Mapped[dict | None] = mapped_column(JSONB)
    plan_artifacts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    development_artifacts: Mapped[dict | None] = mapped_column(JSONB)
    testing_artifacts: Mapped[dict | None] = mapped_column(JSONB)
    code_review_artifacts: Mapped[dict | None] = mapped_column(JSONB)
    security_artifacts: Mapped[dict | None] = mapped_column(JSONB)
    deployment_artifacts: Mapped[dict | None] = mapped_column(JSONB)
    documentation_artifacts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_handoff_event: Mapped[dict | None] = mapped_column(JSONB)
    current_stage: Mapped[str | None] = mapped_column(String(50), default="ingestion", server_default="ingestion")
    artifact_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OrchestratorState(Base):
    """Per-chat-session orchestrator routing state. Chat-session-keyed, GLOBAL (non-RLS)
    â€” faithful replacement for the legacy Django `orchestrator_state_sdlc` table."""
    __tablename__ = "orchestrator_state"

    chat_session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    current_active_agent: Mapped[str | None] = mapped_column(String(50))
    current_batch_id: Mapped[str | None] = mapped_column(String(64))
    pending_user_gate: Mapped[str | None] = mapped_column(String(50))
    last_handoff_event: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------------------
# Conversation store (F2 — §11A chat transcript rail)
# Tenant-scoped under FORCE RLS. Distinct from LangGraph checkpoints (execution
# state) and orchestrator_state (routing). This is the human-facing transcript.
# ---------------------------------------------------------------------------

class ConversationSession(Base):
    """One conversation context per scope (run|stage|gate|copilot).

    UniqueConstraint on (tenant_id, scope_type, scope_id) enforces one active
    session per scope — create_session is idempotent against this constraint.
    RLS anchor: tenant_id (no FK — policy column, mirrors Project.tenant_id).
    """
    __tablename__ = "conversation_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)   # run|stage|gate|copilot|agent
    scope_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # agent_id powers the per-agent session list on each stage page (scope_type="agent");
    # null for the blueprint's run/stage/gate scopes.
    agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Display label for the session rail — auto-set from the first user turn.
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "scope_type", "scope_id", name="uq_conversation_scope"),
        # Serves the per-user/per-agent/per-project session list ordered by recency.
        Index(
            "ix_conv_user_agent",
            "tenant_id", "created_by", "agent_id", "project_id", "status", "updated_at",
        ),
    )


class ConversationMessage(Base):
    """One chat turn within a ConversationSession.

    seq is 1-based and monotonically increasing per session — the service
    computes max(seq)+1 at write time (no DB sequence needed).
    dedup_key allows callers to make append_message idempotent (agent retries).
    RLS anchor: tenant_id (duplicated from session for direct-table policy eval).
    """
    __tablename__ = "conversation_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_sessions.id"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)          # user|agent|orchestrator|system|tool
    # WHICH of the nine replied, when `role` is `agent` (migration 0045). Null on
    # user turns, on `orchestrator` turns — the router answering directly is not one
    # of the nine — and on every row written before the column existed, where the
    # information was never recorded and there is nothing to backfill from.
    #
    # Without it a transcript fed to the next agent reads as one undifferentiated
    # voice, which is how "the Development agent already did this" became invisible
    # to Requirements.
    agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    author_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="markdown", server_default="markdown"
    )
    tool_calls: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    artifact_refs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    citations: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    dedup_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_message_session_seq"),
    )


class DevWorkspace(Base):
    """One cloned repo workspace per platform-project.

    UniqueConstraint on (tenant_id, project_id) enforces one current workspace
    per project — a re-pull REPLACES the existing row via upsert.
    RLS anchor: tenant_id (no FK — policy column, mirrors Project.tenant_id).
    """
    __tablename__ = "dev_workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    ado_project: Mapped[str] = mapped_column(String(200), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(200), nullable=False)
    branch: Mapped[str] = mapped_column(String(200), nullable=False)
    remote_url: Mapped[str] = mapped_column(String(500), nullable=False)
    work_dir: Mapped[str] = mapped_column(String(500), nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pulling", server_default="pulling")
    pulled_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "project_id", name="uq_dev_workspace_project"),
    )
