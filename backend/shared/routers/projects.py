"""Projects resource router.

Exposes CRUD operations for the Project model. All routes are JWT-protected
(NOT in _EXEMPT_PATHS) and scope every query by request.state.tenant_id.

Routes:
  GET    /projects               — paginated list (query: page, page_size, archived, search)
  GET    /projects/{id}          — detail
  POST   /projects               — create
  POST   /projects/{id}/archive  — soft-delete (sets archived=True)
  POST   /projects/{id}/restore  — un-archive (sets archived=False)
  PATCH  /projects/{id}          — partial update (name, description)

Threat mitigations (T-M4-01, T-M4-02, T-M4-03):
  - All queries filtered by tenant_id (no cross-tenant reads)
  - Routes not in _EXEMPT_PATHS (JWT middleware enforces 401 without token)
  - Mutations scope by tenant_id + 404 guard (no cross-tenant writes)
  - Mutating routes carry a require_permission gate (process_api.py _VIEW_DEP floor
    remains for reads). They are NOT all the same gate, on purpose:
      create           project:create      bu_admin + project_admin
      patch            project:update      bu_admin + project_admin, and additionally
                                           scoped to a project the caller administers
      archive/restore  workspace:manage    bu_admin only — removing a project from the
                                           unit is the unit's call, not the project's
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

import logging

from shared.authz.can_perform import can_perform, visible_project_ids
from shared.authz.connector_access import TOOL_ACCESS_MODES, level_from_mode
from shared.authz.connector_capabilities import unsupported_reason
from shared.authz.dependency import require_permission
from shared.authz.project_scope import assert_can_administer_project, project_admin_tier
from shared.authz.effective_role import actor_display_name, effective_platform_role
from shared.authz.workspace import assert_workspace_in_tenant
from shared.db import get_db_session
from shared.models.orm import Project, Run, Workspace
from shared.services import governance_requests as governance_service
from shared.routers._schemas import BudgetIncreaseIn, Paginated, Pagination, ProjectOut, _slugify

logger = logging.getLogger(__name__)

projects_router = APIRouter()

# Alias so the scope helpers read the same way here as in the other routers.
_uuid = uuid


def _user_id(request: Request) -> str:
    return getattr(request.state, "user_id", "") or ""


def _validated_modes(v: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
    """Reject a tool access mode outside the picker's vocabulary.

    Rejected at the door rather than filtered out, because the runtime treats an
    unrecognised mode as NO access (`level_from_mode` fails closed). Silently dropping
    a typo would hand back a project whose stage looks configured and whose connector
    refuses every call — a 422 naming the bad value is the difference between a
    five-second fix and an afternoon.
    """
    if v is None:
        return None
    bad = sorted({m for m in v.values() if m not in TOOL_ACCESS_MODES})
    if bad:
        raise ValueError(
            f"tool access mode must be one of {', '.join(TOOL_ACCESS_MODES)}; "
            f"got {', '.join(bad)}"
        )

    # A LEVEL THE CONNECTOR CANNOT HONOUR IS REFUSED, not stored. Slack implements no
    # read capabilities, so a stage set to "read" on Slack yields a connector that can
    # do nothing while the picker shows a configured chip. This check used to guard the
    # unit grant; the level moved here in migration 0024, so the check moved with it,
    # and this is now the only place it happens.
    #
    # POSITIVE KNOWLEDGE ONLY: `unsupported_reason` returns None for a connector it
    # cannot introspect, and an unintrospectable connector must stay configurable.
    for key, mode in v.items():
        parts = key.split("::")
        if len(parts) != 3 or parts[1] != "connector":
            # MCP servers have no manifest to check, and a key we cannot parse is left
            # to the resolver — which fails closed on it — rather than guessed at here.
            continue
        reason = unsupported_reason(parts[2], level_from_mode(mode))
        if reason:
            raise ValueError(reason)
    return v


class ContributorIn(BaseModel):
    """An existing person's email plus the role they take on this project.

    Mirrors project_members.py's MemberCreateIn — same shape, same "existing
    person only" rule (see that file's docstring) — because this is the same act,
    just batched at creation instead of one add at a time on the roster.
    """

    email: EmailStr
    roleName: str = Field(min_length=1, max_length=64)
    extraAgents: Optional[list[str]] = None


class ProjectCreateIn(BaseModel):
    name: str
    # Which Business Unit the project belongs to. REQUIRED — there is no default
    # workspace and no fallback. Every project belongs to a unit somebody chose, so a
    # create that does not name one is an error (422), not an invitation to guess.
    #
    # It was previously absent from this model entirely, which meant Pydantic dropped
    # the field the create dialog was already sending and the project was attached to
    # the ambient X-Workspace-Id selector instead.
    workspaceId: str
    template: str = "blank"
    description: Optional[str] = None
    track: Literal[
        "greenfield", "enhancement", "modernization", "rpa_infra", "data_engineering"
    ] = "greenfield"
    # Stage→MCP-server mapping {agent_id: [mcp_server_id, ...]} chosen at creation.
    mcp_servers: Optional[dict[str, list[str]]] = None
    # Stage→connector-kind mapping {agent_id: [connector_kind, ...]} chosen at creation.
    connectors: Optional[dict[str, list[str]]] = None
    # Per-(stage, tool) read/write mode, keyed "{agent_id}::{connector|mcp}::{ref}".
    # THIS IS THE ACCESS DECISION since migration 0024 — the unit grant says whether
    # the connector is reachable, this says what may be done with it. Validated
    # below, because an unrecognised mode resolves to NO access at runtime and a
    # typo would otherwise present as a dead connector rather than a 422.
    tool_access_modes: Optional[dict[str, str]] = None
    # REQUIRED. A project is where spend actually happens, so the person creating one
    # says what it may cost — there is no default to fall back on and no silent
    # inheritance. A business unit's budget stays optional by contrast: a unit that
    # caps nothing is a normal thing to want.
    monthlyBudgetUsd: float = Field(gt=0)
    # How long that budget is authorised for. The create dialog has collected these
    # since before the backend had anywhere to put them, so they arrived and were
    # silently dropped — two dates typed into a form and lost (migration 0035).
    #
    # Either end may be omitted; both omitted is the ordinary "no window" case.
    budgetStartDate: Optional[date] = None
    budgetEndDate: Optional[date] = None
    # Who OWNS the project — bound as `project_admin` at project scope on creation.
    #
    # A project with no owner is the state this field exists to prevent: the Project
    # Admin is the fallback approver on every stage no specialist role owns, so an
    # unowned project has gates nobody can pass. Optional only because an Org Admin may
    # legitimately create one before deciding who runs it; the UI always asks.
    #
    # A user id, not an email: the dialog picks from people already in the unit, and
    # accepting an email here would make this a second, quieter account-creating path.
    ownerId: Optional[str] = None
    # People added to the roster at creation time — the dialog's Contributors
    # section. Was already being sent and silently dropped: Pydantic ignores
    # unrecognised fields by default, so nobody named here ever actually joined
    # the project.
    contributors: Optional[list[ContributorIn]] = None

    @field_validator("tool_access_modes")
    @classmethod
    def _check_modes(cls, v: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
        return _validated_modes(v)

    @model_validator(mode="after")
    def _check_window(self):
        """Mirrors budgetWindowError in frontend/lib/schemas/budget-window.ts.

        Duplicated deliberately: the dialog's check is a hint, this one is the rule.
        A window that ends before it starts can never be active, so a project created
        with one could never run — a 422 naming the field beats a project nobody can
        use and no obvious reason why.
        """
        if (
            self.budgetStartDate is not None
            and self.budgetEndDate is not None
            and self.budgetEndDate < self.budgetStartDate
        ):
            raise ValueError("budgetEndDate must be on or after budgetStartDate")
        return self


class ProjectPatchIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    mcp_servers: Optional[dict[str, list[str]]] = None
    connectors: Optional[dict[str, list[str]]] = None
    # See ProjectCreateIn.tool_access_modes. Sent whole by the stage picker, so it
    # replaces rather than merges — a mode removed in the UI must disappear here.
    tool_access_modes: Optional[dict[str, str]] = None
    # 0 clears the cap (inherit workspace / unlimited); positive sets it.
    monthlyBudgetUsd: Optional[float] = None

    @field_validator("tool_access_modes")
    @classmethod
    def _check_modes(cls, v: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
        return _validated_modes(v)


class IngestBoardIn(BaseModel):
    # Which board project to import from. When omitted, the project's remembered
    # board (Project.external_ref) is used, else the first board project.
    board_project: Optional[str] = None
    # Which board provider to pull from when the stage has more than one
    # (e.g. azure_devops + jira). Omitted → the first available board provider.
    provider: Optional[str] = None


@projects_router.get("", response_model=Paginated[ProjectOut])
async def list_projects(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    archived: bool = False,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
):
    """Paginated projects across every unit the caller may see.

    NOT narrowed to the active workspace any more. That filter predates
    `visible_project_ids` and now actively contradicts it: an Organization Admin's
    reach is every project in the tenant, but pinning the query to one workspace meant
    the Projects screen — which GROUPS BY Business Unit — could never show more than
    one group, and a project created in any other unit simply vanished from the list.

    `visible_project_ids` already encodes the right reach per binding scope
    (organization -> everything, business_unit -> that unit's projects, project -> the
    one), including the union across several units for someone who administers more
    than one. Two filters answering the same question is how they come to disagree.
    """
    tenant_id = request.state.tenant_id

    stmt = select(Project).where(
        Project.tenant_id == tenant_id,
        Project.archived == archived,
    )

    # Scope filter, applied BEFORE paging. RLS confines this to the tenant and the
    # selector confines it to one unit, but neither answers "which projects in that
    # unit are this person's". Until now nothing did on this side: the Next.js tier
    # filtered the rows after the fact, so removing that filter widened the endpoint
    # from "my projects" to "every project in the unit".
    #
    # Filtering after paging would leak the real total and hand back short pages, so
    # the count in "12 projects" would describe a set the viewer cannot open.
    visible = await visible_project_ids(db, user_id=_user_id(request), tenant_id=str(tenant_id))
    if visible is not None:
        # [] is a real answer — a user with no bindings sees nothing — so it must not
        # be folded into "no filter".
        stmt = stmt.where(Project.id.in_([_uuid.UUID(p) for p in visible]))

    if search:
        stmt = stmt.where(Project.display_name.ilike(f"%{search}%"))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total: int = (await db.execute(count_stmt)).scalar_one()

    stmt = stmt.order_by(Project.updated_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    return Paginated(
        items=[ProjectOut.from_orm_project(p) for p in rows],
        pagination=Pagination(page=page, pageSize=page_size, total=total),
    )


@projects_router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Return a single project by ID, if this caller may see that project."""
    tenant_id = request.state.tenant_id
    project = await _get_or_404(db, project_id, tenant_id)

    # Resource-aware check. The router-level view floor only asks whether the caller
    # holds artifact:view SOMEWHERE in the tenant, which every role does — so it let
    # anyone open any project by guessing or pasting an id.
    #
    # 404, not 403: a project the caller may not see must not be confirmed to exist,
    # and _get_or_404 already answers with 404 for a project in another tenant. Two
    # different codes for "you can't have this" is itself a disclosure.
    if not await can_perform(
        db,
        user_id=_user_id(request),
        permission="artifact:view",
        tenant_id=str(tenant_id),
        resource_kind="project",
        resource_id=str(project.id),
    ):
        raise HTTPException(status_code=404, detail="Project not found")
    # Spend is financial data — only cost:view holders see the dollar figure.
    _perms = getattr(request.state, "permissions", []) or []
    spend = 0.0
    if "admin:*" in _perms or "cost:view" in _perms:
        from shared.services.budget_store import read_scope_spend  # noqa: PLC0415
        spend = await read_scope_spend(str(tenant_id), "project", str(project.id))
    return ProjectOut.from_orm_project(project, spend)


@projects_router.post(
    "",
    response_model=ProjectOut,
    status_code=201,
    # `project:create`, not `workspace:manage`. The two are not the same authority:
    # administering a business unit is the Org Admin's grant to a unit's admin, while
    # creating a project inside a unit is the unit's own act. `workspace:manage` is held
    # by bu_admin alone, so a Project Admin — who holds `project:create` and whom
    # `canCreateProject` shows the "New project" button to — got a 403 on submit.
    #
    # Pure widening: every holder of `workspace:manage` (bu_admin) also holds
    # `project:create`, so nobody loses the ability to create.
    #
    # Archive/restore/patch below deliberately KEEP `workspace:manage`. Creating a
    # project is a delivery act; removing one is unit administration.
    dependencies=[Depends(require_permission("project:create"))],
)
async def create_project(
    body: ProjectCreateIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Create a new project in the Business Unit the caller named.

    EVERY PROJECT BELONGS TO A UNIT SOMEBODY CHOSE. There is no default workspace to
    fall back to and no ambient selector consulted here: `workspaceId` is required, and
    a unit this organization does not own is a 422.

    Both halves of that used to be untrue. The field was not declared on
    ProjectCreateIn, so Pydantic dropped the value the create dialog was already
    sending, and the project was attached to whatever the X-Workspace-Id selector named
    — or, when that header was absent (it usually is: nothing sets its cookie except
    the create-BU dialog, since the BU switcher was removed from the chrome), to the
    org's oldest workspace. That is how projects ended up filed under a "Default
    Workspace" nobody picked.
    """
    tenant_id = request.state.tenant_id
    tenant_uuid = uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id

    ws_id = await assert_workspace_in_tenant(str(tenant_id), body.workspaceId)
    request.state.workspace_id = ws_id

    # A Project Admin's own creation needs the owning Business Unit Admin's sign-off
    # (PRD-aligned governance addition) — an Org Admin or BU Admin creating is already
    # at or above that tier, so theirs go live directly. Computed from the CALLER's
    # actual role, never from the client's speculative `approvalStatus` in the create
    # payload (ProjectCreateIn declares no such field) — a client that could name its
    # own approval status would not need an approver at all.
    creator_role = await effective_platform_role(db, request)
    pending = creator_role == "project_admin"

    # Default budget + hierarchical guard (0032): a new project defaults to the project
    # budget and must fit under its workspace's remaining budget, else 409 "Budget low"
    # (blocks creation until the workspace cap is raised).
    from shared.services.budget_alloc import assert_project_fits  # noqa: PLC0415
    # NO DEFAULT, and unlike a business unit this one is REQUIRED — see
    # ProjectCreateIn.monthlyBudgetUsd. Whoever creates a project states what it may
    # spend; falling back to DEFAULT_PROJECT_BUDGET_USD gave it a cap nobody picked
    # and no reason to trust.
    _proj_budget = float(body.monthlyBudgetUsd)
    await assert_project_fits(db, tenant_id, str(ws_id), _proj_budget, on_create=True)

    project = Project(
        workspace_id=ws_id,
        tenant_id=tenant_uuid,
        display_name=body.name,
        archived=False,
        track=body.track,
        mcp_servers=body.mcp_servers or None,
        connectors=body.connectors or None,
        tool_access_modes=body.tool_access_modes or None,
        monthly_budget_usd=_proj_budget,
        budget_start_date=body.budgetStartDate,
        budget_end_date=body.budgetEndDate,
        approval_status="pending_approval" if pending else "active",
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)

    # ── the owner ────────────────────────────────────────────────────────────
    # Granted AFTER the project row exists, because the binding's scope_id is the
    # project's id. A Business Unit Admin naming themselves is the ordinary case and is
    # NOT a tier conflict: their bu_admin binding sits at business_unit scope and this
    # one at project scope, and `_assert_no_tier_conflict` is per-scope precisely so
    # that somebody can govern a unit and deliver inside one of its projects.
    if body.ownerId:
        owner_ok = (
            await db.execute(
                text("SELECT 1 FROM users WHERE id = :u AND tenant_id = CAST(:t AS uuid)"),
                {"u": body.ownerId, "t": str(tenant_id)},
            )
        ).first()
        if owner_ok is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "unknown_owner",
                    "message": "That person is not in this organization.",
                },
            )
        from shared.authz.grant import grant_role  # noqa: PLC0415 - import cycle

        await grant_role(
            body.ownerId, str(project.id), "project_admin",
            tenant_id=str(tenant_id), scope_kind="project",
            granted_by=getattr(request.state, "user_id", None),
        )
    else:
        # NOBODY WAS NAMED, SO THE CREATOR OWNS IT — but only when they cannot already
        # reach it, which keeps this from quietly handing a delivery-tier binding to
        # every Business Unit Admin who makes a project (their unit binding covers it,
        # and an extra one would be noise on their /my-access page).
        #
        # A Project Admin is the case that needs this: their reach is the projects they
        # are bound to, so without a binding they created a project and immediately
        # could not open it. That was survivable only because a leftover `contributor`
        # placeholder used to grant unit-wide reach — once that placeholder correctly
        # grants nothing, the missing binding is the whole difference between a project
        # and a 404.
        creator = getattr(request.state, "user_id", None)
        if creator and not await can_perform(
            db,
            user_id=creator,
            permission="artifact:view",
            tenant_id=str(tenant_id),
            resource_kind="project",
            resource_id=str(project.id),
        ):
            from shared.authz.grant import grant_role  # noqa: PLC0415 - import cycle

            await grant_role(
                creator, str(project.id), "project_admin",
                tenant_id=str(tenant_id), scope_kind="project",
                granted_by=creator,
            )

    # ── contributors ─────────────────────────────────────────────────────────
    # Same rules as POST /projects/{id}/members (project_members.py): an existing
    # person only, a role from ALL_ROLES, and a grant no higher than the creator's
    # own reach. Validated in a first pass so a bad row 422s the whole creation
    # atomically rather than leaving a project with some contributors added and
    # others silently missing.
    #
    # NOT SEATED YET if the project itself is pending — a contributor added to a
    # project that doesn't officially exist would have working access to it before
    # anyone agreed it should run. Their resolved (user_id, role, extras) go into
    # the governance request's payload instead, and _apply_project_creation
    # (shared/governance/effects.py) seats + notifies them at approval time.
    pending_contributors: list[dict] = []
    if body.contributors:
        from shared.authz.grant import grant_role  # noqa: PLC0415 - import cycle
        from shared.authz.grant_guard import assert_can_grant_role  # noqa: PLC0415
        from shared.authz.permissions import ALL_ROLES  # noqa: PLC0415

        resolved: list[tuple[str, ContributorIn]] = []
        for contributor in body.contributors:
            if contributor.roleName not in ALL_ROLES:
                raise HTTPException(
                    status_code=422, detail=f"Unknown role '{contributor.roleName}'"
                )
            await assert_can_grant_role(db, request, contributor.roleName)
            user = (
                await db.execute(
                    text(
                        "SELECT id FROM users WHERE lower(email) = :e "
                        "AND tenant_id = CAST(:t AS uuid)"
                    ),
                    {"e": contributor.email.lower(), "t": str(tenant_id)},
                )
            ).first()
            if user is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "no_such_person",
                        "message": (
                            f"Nobody in this organisation uses {contributor.email}. "
                            "They have to be onboarded before they can join a project."
                        ),
                    },
                )
            resolved.append((user.id, contributor))

        if pending:
            pending_contributors = [
                {
                    "userId": user_id,
                    "roleName": contributor.roleName,
                    "extraAgents": contributor.extraAgents or [],
                }
                for user_id, contributor in resolved
            ]
        else:
            actor = getattr(request.state, "user_id", None)
            for user_id, contributor in resolved:
                await grant_role(
                    user_id, str(project.id), contributor.roleName,
                    tenant_id=str(tenant_id), scope_kind="project", granted_by=actor,
                )
                if contributor.extraAgents:
                    await db.execute(
                        text(
                            "UPDATE role_bindings SET extra_agents = CAST(:a AS jsonb) "
                            "WHERE user_id = :u AND scope_kind = 'project' AND scope_id = :p "
                            "  AND role_name = :r"
                        ),
                        {
                            "a": json.dumps(contributor.extraAgents),
                            "u": user_id, "p": project.id, "r": contributor.roleName,
                        },
                    )
            await db.flush()

    # ── file the approval request ────────────────────────────────────────────
    if pending:
        await governance_service.create_request(
            db,
            tenant_id=str(tenant_id),
            initiator_id=getattr(request.state, "user_id", "") or "",
            initiator_name=await actor_display_name(db, request),
            initiator_role=creator_role,
            request_type="project_creation",
            title=f"Create {project.display_name}",
            description=(
                f"{project.display_name} was proposed by its Project Admin and "
                "needs this business unit's sign-off before it goes live."
            ),
            workspace_id=str(ws_id),
            project_id=str(project.id),
            target_ref=str(project.id),
            payload={"contributors": pending_contributors},
            # NOT system_raised: unlike project_archive (SYSTEM_RAISED,
            # filed on someone else's behalf), `project_creation` is already in
            # `_PROJECT_ADMIN_RAISABLE` — a Project Admin filing this about their
            # own creation is exactly the raisable act the type describes, just
            # triggered by POST /projects instead of the manual picker.
        )
        await db.flush()
        await db.refresh(project)

    # DP6 warn: compute config-time capability-gap and log any shortfalls.
    # Best-effort only — never blocks project creation.
    try:
        from shared.capabilities.config_check import config_capability_report  # noqa: PLC0415
        assignment: dict = {"agents": {}}
        gap_report = config_capability_report(assignment)
        for agent_id, gap in gap_report.items():
            if gap:
                logger.warning(
                    "Project %s: agent %s missing capabilities %s",
                    str(project.id), agent_id, gap,
                )
    except Exception:  # noqa: BLE001 — reporting must never break project creation
        pass

    return ProjectOut.from_orm_project(project)


@projects_router.post("/{project_id}/budget-increase-request", status_code=201)
async def request_project_budget_increase(
    project_id: str,
    request: Request,
    body: BudgetIncreaseIn,  # shared with workspaces.py's own budget-increase-request route
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """The Project Admin's half of the budget cascade (mirrors
    workspaces.py::request_budget_increase, the Business Unit Admin's half).

    THE PROJECT, NOT THE WORKSPACE. `_apply_budget_increase` (shared/governance/effects.py)
    has always supported a project-scoped target — "A Project Admin whose project has
    exhausted its total budget raises this against their PROJECT" — but nothing has ever
    called create_request with a project_id for this type, so that branch has been dead
    code since it shipped (sub-project A, Task 1, parked finding). This is that caller.

    The floor is cost:view, not project:update, matching workspaces.py's own reasoning
    exactly: asking is not changing, and whoever can see a cap about to bind is who should
    be able to raise it.
    """
    tenant_id = request.state.tenant_id
    project = await _get_or_404(db, project_id, tenant_id)
    if not await can_perform(
        db, user_id=_user_id(request), permission="cost:view",
        tenant_id=str(tenant_id), resource_kind="project", resource_id=str(project.id),
    ):
        raise HTTPException(status_code=404, detail="Project not found")

    from shared.services.governance_requests import GovernanceError  # noqa: PLC0415

    amount = body.requestedAmountUsd
    current = float(project.monthly_budget_usd) if project.monthly_budget_usd is not None else None
    detail = (
        f"{project.display_name} is asking to move its monthly cap "
        + (f"from {current:.0f} " if current is not None else "")
        + f"to {amount:.0f} USD."
        + (f" {body.reason}" if body.reason else "")
    )

    try:
        return await governance_service.create_request(
            db,
            tenant_id=str(tenant_id),
            initiator_id=getattr(request.state, "user_id", "") or "",
            initiator_name=await actor_display_name(db, request),
            initiator_role=await effective_platform_role(db, request),
            request_type="budget_increase",
            title=f"Budget increase: {project.display_name} — {amount:.0f} USD/month",
            description=detail,
            workspace_id=str(project.workspace_id),
            project_id=str(project.id),
            target_ref=str(project.id),
            payload={"requestedAmountUsd": amount, "previousAmountUsd": current},
            priority="high",
        )
    except GovernanceError as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"code": exc.code, "message": str(exc)}
        )


# Connector kinds that are work-item BOARDS (vs MCP servers / other connectors that
# can also be assigned to a stage). Only these are offered as a "pull stories" source.
_BOARD_KINDS = ("azure_devops", "jira", "github_issues", "linear")


def _board_providers(project) -> list[str]:
    """Board connector kinds assigned to the Requirements stage, in order.

    Filters Project.connectors["requirements"] to real board kinds (drops MCP/others),
    falling back to the legacy project-wide provider_kind, then azure_devops.
    """
    conns = getattr(project, "connectors", None) or {}
    req = [k for k in (conns.get("requirements") or []) if k in _BOARD_KINDS]
    if req:
        return req
    legacy = getattr(project, "provider_kind", None)
    return [legacy] if legacy in _BOARD_KINDS else ["azure_devops"]


def _board_kind(project, requested: Optional[str] = None) -> str:
    """The board provider to pull from — the requested one when it's available to this
    project's Requirements stage, else the first available."""
    providers = _board_providers(project)
    if requested and requested in providers:
        return requested
    return providers[0]


async def _connector_or_409(project, tenant_id: str, kind: Optional[str] = None):
    """Resolve the tenant's board connector for a project, or 409 fail-closed."""
    from config.connector_factory import get_connector_for_session  # noqa: PLC0415

    kind = kind or _board_kind(project)
    try:
        return await get_connector_for_session(
            kind=kind, tenant_id=tenant_id, project_id=str(project.id),
        )
    except Exception:
        raise HTTPException(
            status_code=409,
            detail="No board connector is configured. Connect Azure DevOps or Jira on the Integrations page.",
        )


@projects_router.get(
    "/{project_id}/board-projects",
    dependencies=[Depends(require_permission("run:create"))],
)
async def list_board_projects(
    project_id: str,
    request: Request,
    provider: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
):
    """Discover the board projects available on the chosen provider.

    `provider` (query) selects which board connector to read when the stage has more
    than one (e.g. azure_devops + jira); defaults to the first available. Returns
    {projects, selected, provider, available_providers}. 409 no connector; 502 board error.
    """
    tenant_id = request.state.tenant_id
    project = await _get_or_404(db, project_id, tenant_id)
    kind = _board_kind(project, provider)
    connector = await _connector_or_409(project, tenant_id, kind=kind)
    try:
        boards = await connector.read_adapter("list_projects")
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_board_projects: list_projects failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail=f"Couldn't reach the board ({type(exc).__name__}). Check the connector credentials on Integrations.",
        )
    projects = [
        {"name": b.get("name") or b.get("key") or "", "key": b.get("key") or ""}
        for b in (boards or [])
        if (b.get("name") or b.get("key"))
    ]
    return {
        "projects": projects,
        "selected": getattr(project, "external_ref", None),
        "provider": kind,
        "available_providers": _board_providers(project),
    }


@projects_router.post(
    "/{project_id}/ingest-board",
    dependencies=[Depends(require_permission("run:create"))],
)
async def ingest_board(
    project_id: str,
    request: Request,
    body: IngestBoardIn = IngestBoardIn(),
    db: AsyncSession = Depends(get_db_session),
):
    """Pull work items from a chosen board project into structured stories.

    Deterministic board ingestion: resolves the tenant's connector, picks the board
    project (request body → project's remembered external_ref → first project),
    lists its work items, fetches each item's detail, and stores them as stories in a
    requirements Run's requirements_payload. The chosen board is remembered on
    Project.external_ref so the picker pre-selects it next time. The Requirements page
    renders the stories (see story_artifacts_from_run). 409 no connector / 502 board error.
    """
    tenant_id = request.state.tenant_id
    tenant_uuid = uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
    project = await _get_or_404(db, project_id, tenant_id)
    connector = await _connector_or_409(project, tenant_id, kind=_board_kind(project, body.provider))

    try:
        boards = await connector.read_adapter("list_projects")
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingest_board: list_projects failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail=f"Couldn't reach the board ({type(exc).__name__}). Check the connector credentials on Integrations.",
        )
    if not boards:
        raise HTTPException(status_code=409, detail="No projects found on the connected board.")

    names = [b.get("name") or b.get("key") or "" for b in boards if (b.get("name") or b.get("key"))]
    requested = body.board_project or getattr(project, "external_ref", None)
    board_name = requested if (requested and requested in names) else names[0]
    try:
        items = await connector.read_adapter("list_all_items", project=board_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingest_board: list_all_items failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail=f"Couldn't list work items ({type(exc).__name__}).")

    stories: list[dict] = []
    for it in (items or [])[:25]:
        item_id = it.get("id") or it.get("source_key")
        detail = it
        try:
            detail = await connector.read_adapter(
                "fetch_item_detail", project=board_name, item_id=item_id
            )
        except Exception:  # noqa: BLE001 — fall back to the summary row
            detail = it
        stories.append(
            {
                "id": str(detail.get("id") or item_id or ""),
                "source_key": str(detail.get("source_key") or item_id or ""),
                "title": detail.get("title") or "",
                "description": detail.get("description") or "",
                "acceptance_criteria": detail.get("acceptance_criteria") or [],
                "state": detail.get("state") or "",
                "work_item_type": detail.get("work_item_type") or "",
            }
        )

    run = Run(
        project_id=project.id,
        tenant_id=tenant_uuid,
        stage="requirements",
        status="completed",
        trigger="manual",
        requirements_payload={"stories": stories, "board_project": board_name},
        # See runs.py — the initiator is what the no-self-approval gate compares against.
        created_by=getattr(request.state, "user_id", "") or None,
    )
    db.add(run)
    # Remember the chosen board on the local project so the picker pre-selects it.
    project.external_ref = board_name
    await db.flush()
    await db.commit()
    logger.info(
        "ingest_board: tenant=%s project=%s board=%s ingested=%d",
        tenant_id, str(project.id), board_name, len(stories),
    )
    return {"ingested": len(stories), "board_project": board_name, "run_id": str(run.id)}


@projects_router.post(
    "/{project_id}/archive",
    response_model=ProjectOut,
    dependencies=[Depends(require_permission("workspace:manage"))],
)
async def archive_project(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Soft-delete a project, or ASK to — which depends on who is asking.

    Scoped by tenant_id to prevent cross-tenant tampering (T-M4-03).

    A Project Admin archives their own project and an Org Admin archives anything;
    a Business Unit Admin archiving a project they do not run is a different act,
    and it files a `project_archive` governance request routed to the Org Admin
    rather than doing it. Ending someone else's delivery work is exactly the kind
    of decision the request lane exists for — `workspace:manage` says a BU Admin
    may administer their unit, not that this particular call is theirs to make.

    THE RESPONSE SHAPE IS THE SAME EITHER WAY, and deliberately: the project comes
    back with `archived` still false when a request was filed, and the client reads
    "sent for approval" from that. A second response shape would mean every caller
    branching on which one it got.
    """
    tenant_id = request.state.tenant_id
    project = await _get_or_404(db, project_id, tenant_id)

    role = await effective_platform_role(db, request)
    if role == "bu_admin":
        await governance_service.create_request(
            db,
            tenant_id=str(tenant_id),
            initiator_id=getattr(request.state, "user_id", "") or "",
            initiator_name=await actor_display_name(db, request),
            initiator_role=role,
            request_type="project_archive",
            title=f"Archive {project.display_name}",
            description=(
                f"{project.display_name} was proposed for archiving by its "
                "business unit's admin."
            ),
            workspace_id=str(project.workspace_id),
            project_id=str(project.id),
            target_ref=str(project.id),
            system_raised=True,
        )
        await db.flush()
        await db.refresh(project)
        return ProjectOut.from_orm_project(project)

    project.archived = True
    await db.flush()
    await db.refresh(project)
    return ProjectOut.from_orm_project(project)


@projects_router.post(
    "/{project_id}/restore",
    response_model=ProjectOut,
    dependencies=[Depends(require_permission("workspace:manage"))],
)
async def restore_project(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Un-archive a project (sets archived=False).

    Scoped by tenant_id to prevent cross-tenant tampering (T-M4-03).
    """
    tenant_id = request.state.tenant_id
    project = await _get_or_404(db, project_id, tenant_id)
    project.archived = False
    await db.flush()
    await db.refresh(project)
    return ProjectOut.from_orm_project(project)


# Payload keys the settings request carries, mapped from the PATCH body's field
# names. Mirrors _SETTINGS_FIELDS in shared/governance/effects.py, which does the
# writing — the request stores what was ASKED FOR in the API's own vocabulary so an
# approver's queue and the applied change describe the same thing.
#
# `description` is DELIBERATELY ABSENT, mirroring _SETTINGS_FIELDS: `projects` has no
# such column, so queuing it here only bought a request that crashed with a 500 on
# approval and never got a chance to close (live baseline audit, 2026-08-29). Leaving
# a description edit off the queued payload entirely makes it consistent with the
# direct-write branch below, where the same dead column already makes an edit a
# silent no-op rather than an error.
_SETTINGS_PAYLOAD_KEYS: dict[str, str] = {
    "name": "name",
    "monthlyBudgetUsd": "monthlyBudgetUsd",
    "connectors": "connectors",
    "mcp_servers": "mcpServers",
    "tool_access_modes": "toolAccessModes",
}

_SETTINGS_FIELD_LABEL: dict[str, str] = {
    "name": "name",
    "monthlyBudgetUsd": "budget",
    "connectors": "connectors",
    "mcp_servers": "MCP servers",
    "tool_access_modes": "tool access",
}


async def _queue_settings_change(
    db: AsyncSession, request: Request, project: Any, changes: dict[str, Any]
):
    """Turn a Project Admin's settings edit into a request for their BU Admin.

    NOTHING IS WRITTEN TO THE PROJECT HERE. The response carries the project as it
    still is, plus `pendingApproval`, so the client says "sent for approval" rather
    than showing an edit that has not happened. Applying it and marking it pending
    would be the worst of both — the change live, and the approver asked about
    something already done.

    Raised with `system_raised=True`: it is filed by saving the form, not chosen
    from the request picker, so `can_raise_type` is not the gate (see
    shared/governance/routing.py::SYSTEM_RAISED).
    """
    from shared.services import governance_requests as gov  # noqa: PLC0415

    payload_changes = {
        _SETTINGS_PAYLOAD_KEYS[k]: v
        for k, v in changes.items()
        if k in _SETTINGS_PAYLOAD_KEYS
    }
    if not payload_changes:
        await db.refresh(project)
        return ProjectOut.from_orm_project(project)

    edited = ", ".join(
        _SETTINGS_FIELD_LABEL.get(k, k) for k in changes if k in _SETTINGS_PAYLOAD_KEYS
    )
    user_id = getattr(request.state, "user_id", "") or ""
    req = await gov.create_request(
        db,
        tenant_id=str(request.state.tenant_id),
        initiator_id=user_id,
        initiator_name=getattr(request.state, "user_name", "") or user_id,
        initiator_role="project_admin",
        request_type="project_settings_change",
        title=f"Settings change for {project.display_name}",
        description=f"Requested changes to: {edited}.",
        workspace_id=str(project.workspace_id) if project.workspace_id else None,
        project_id=str(project.id),
        target_ref=str(project.id),
        payload={"changes": payload_changes},
        system_raised=True,
    )

    await db.refresh(project)
    out = ProjectOut.from_orm_project(project)
    out.pendingApproval = True
    out.pendingRequestId = str(req["id"])
    out.pendingApproverRole = req.get("currentApproverRole")
    return out


@projects_router.patch(
    "/{project_id}",
    response_model=ProjectOut,
    dependencies=[Depends(require_permission("project:update"))],
)
async def patch_project(
    project_id: str,
    body: ProjectPatchIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Partially update a project's name, wiring or budget.

    Gated on `project:update`, not `workspace:manage`. The Project Admin is made to
    choose a budget when they create the project, and `workspace:manage` is held only
    by the Business Unit Admin — so the person who set the figure could not change it.

    Widening WHO may edit makes WHICH project mandatory: `_get_or_404` scopes by tenant
    alone, so on its own this would have let any Project Admin patch every project in
    the organisation. `assert_can_administer_project` is the other half.
    """
    tenant_id = request.state.tenant_id
    project = await _get_or_404(db, project_id, tenant_id)

    # WHICH TIER is administering, not merely whether they may. A Business Unit
    # Admin or an Org Admin applies the edit; the project's OWN admin proposes it,
    # and their Business Unit Admin decides. `project_admin_tier` returns None for
    # anyone else, which is the same refusal assert_can_administer_project makes.
    tier = await project_admin_tier(db, request, project)
    if tier is None:
        raise HTTPException(status_code=404, detail="not found")

    # Only what was actually sent. `exclude_unset` matters: without it every
    # unspecified field arrives as None and an edit to the name would read as a
    # request to blank the budget and unwire every connector.
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        await db.refresh(project)
        return ProjectOut.from_orm_project(project)

    if tier == "project":
        return await _queue_settings_change(db, request, project, changes)

    if body.name is not None:
        project.display_name = body.name
    if body.description is not None:
        project.description = body.description
    if body.mcp_servers is not None:
        project.mcp_servers = body.mcp_servers or None
    if body.tool_access_modes is not None:
        project.tool_access_modes = body.tool_access_modes or None
    if body.connectors is not None:
        project.connectors = body.connectors or None
    if body.monthlyBudgetUsd is not None:
        project.monthly_budget_usd = body.monthlyBudgetUsd or None  # 0 clears the cap
    await db.flush()
    await db.refresh(project)
    from shared.services.budget_guard import clear_budget_cache  # noqa: PLC0415
    clear_budget_cache()
    from shared.services.budget_store import read_scope_spend  # noqa: PLC0415
    spend = await read_scope_spend(str(tenant_id), "project", str(project.id))
    return ProjectOut.from_orm_project(project, spend)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


async def _get_or_404(db: AsyncSession, project_id: str, tenant_id: str) -> Project:
    """Fetch a Project by UUID id OR derived slug, scoped to tenant_id (404 if none).

    `slug` is a derived, non-queryable display field (Plan 02: slug =
    slugify(display_name); no slug column in the ORM). The frontend detail/phase
    routes are slug-based, so when `project_id` is not a UUID we treat it as a slug
    and match it against the in-tenant projects' derived slugs. Passing a non-UUID
    straight into `Project.id ==` makes Postgres raise `invalid input syntax for
    type uuid` (500); this resolver yields a clean 404 instead.

    The tenant_id filter prevents cross-tenant reads (T-M4-01) — a valid JWT for
    tenant B cannot read or mutate tenant A's projects.
    """
    if _is_uuid(project_id):
        result = await db.execute(
            select(Project).where(
                Project.id == project_id,
                Project.tenant_id == tenant_id,
            )
        )
        project = result.scalar_one_or_none()
    else:
        # Slug path — derive slug per in-tenant project and match.
        result = await db.execute(
            select(Project).where(Project.tenant_id == tenant_id)
        )
        project = next(
            (p for p in result.scalars() if _slugify(p.display_name) == project_id),
            None,
        )

    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project
