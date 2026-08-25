"""Agent Profile lifecycle API — versioned draft / publish / rollback (Phase 2, design §3.5).

An Agent Profile is the org-editable behavior layer for one of the 8 pipeline agents,
resolved org -> workspace -> project on top of the in-code base prompt (the floor). This
router owns the *authoring* lifecycle:

  draft     — lint, then insert a new version row (is_active=false).
  publish   — flip the active pointer to a version (rollback = publishing an older one).
  unpublish — clear the active pointer (agent falls back to lower scopes / base).
  preview   — show how a draft's slots stack on the vendor base + active lower scopes,
              WITHOUT leaking the vendor base prompt content.

Serialization convention: snake_case in responses, matching the sibling capabilities
router (shared/routers/capabilities.py) and the resource routers. The frontend BFF reads
these keys verbatim.

RBAC (design §3.5): reads gate on the "artifact:view" floor (router-level, matching the
capabilities router). draft/preview require "skill:edit"; publish/unpublish require
"workspace:manage". Every route therefore carries a require_permission sentinel so the
process_api D-05 boot scan stays green.
"""
from __future__ import annotations

import re
import uuid
from typing import Iterable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.agent_registry import AGENT_REGISTRY
from shared.audit.models import AuditEventPayload
from shared.audit.service import audit_service
from shared.authz.dependency import require_permission
from shared.authz.workspace import active_workspace_for_request
from shared.db import get_db_session
from shared.models.orm import AgentProfile
from shared.services.prompt_runtime import invalidate_profile_cache

# ── Fixed presentation order (frozen API contract). Differs from AGENT_REGISTRY
#    dict order — the contract puts code_review + security BEFORE testing — so it is
#    declared explicitly here rather than derived from pipeline_position. ────────────
PIPELINE_ORDER: tuple[str, ...] = (
    "requirements", "design", "development", "code_review",
    "security", "testing", "deployment", "documentation",
)

SCOPE_VALUES: tuple[str, ...] = ("org", "workspace", "project")
SCOPE_ORDER: dict[str, int] = {"org": 0, "workspace": 1, "project": 2}


def ancestor_chain(scope: str, scope_id: str | None, workspace_id: str | None) -> list[tuple[str, str | None]]:
    """Nearest-first ancestor (scope, scope_id) pairs above `scope`, for inheritance
    resolution. `workspace_id` is the project's own parent BU — required to resolve a
    project's ancestors; omitted, a project-scope request simply gets no ancestors
    back (degrades to no-inheritance behavior, never errors). Shared with
    skill_store.py's list_skills_merged, which needs the identical chain shape.
    """
    if scope == "org":
        return []
    if scope == "workspace":
        return [("org", None)]
    if scope == "project":
        return [("workspace", workspace_id), ("org", None)] if workspace_id else []
    return []


VENDOR_LAYER_NAME = "Vendor base prompt (identity, tools, safety, HANDOFF contract)"

# ── Lint rules (module-level so tests + preview reuse them; design §3.5 guardrails) ──
MAX_PROMPT_PREPEND = 4000
MAX_PROMPT_APPEND = 4000
MAX_OUTPUT_CONTRACT_EXTRA = 2000

FIELD_CAPS: dict[str, int] = {
    "prompt_prepend": MAX_PROMPT_PREPEND,
    "prompt_append": MAX_PROMPT_APPEND,
    "output_contract_extra": MAX_OUTPUT_CONTRACT_EXTRA,
}

_FORBIDDEN_SOURCES: tuple[str, ...] = (
    # Stacked qualifiers ("ignore all previous instructions") must match too — allow
    # up to three words between the verb and "instructions".
    r"ignore\s+(?:\w+\s+){0,3}instructions",
    r"disregard\s+.{0,30}(system|previous)\s+prompt",
    r"reveal\s+.{0,40}prompt",
    r"HANDOFF::",
    r"you\s+are\s+no\s+longer",
    r"forget\s+(all|everything|your)\s+(instructions|rules)",
)
FORBIDDEN_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in _FORBIDDEN_SOURCES]


# ── Pure helpers (unit-testable without a DB) ───────────────────────────────────────

def lint_profile_fields(
    prompt_prepend: str | None,
    prompt_append: str | None,
    output_contract_extra: str | None,
) -> list[dict]:
    """Return ALL lint violations (never short-circuit on the first).

    Each violation is {"field", "code", "message"}. Codes: "too_long", "forbidden_pattern".
    Caps are checked per field first, then every deny-regex against every field.
    """
    fields = {
        "prompt_prepend": prompt_prepend or "",
        "prompt_append": prompt_append or "",
        "output_contract_extra": output_contract_extra or "",
    }
    violations: list[dict] = []
    for field, cap in FIELD_CAPS.items():
        length = len(fields[field])
        if length > cap:
            violations.append({
                "field": field,
                "code": "too_long",
                "message": f"{field} is {length} characters; the maximum is {cap}.",
            })
    for field, value in fields.items():
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(value):
                violations.append({
                    "field": field,
                    "code": "forbidden_pattern",
                    "message": (
                        f"{field} contains a forbidden instruction-override pattern "
                        f"(/{pattern.pattern}/)."
                    ),
                })
    return violations


def next_version(existing_versions: Iterable[int]) -> int:
    """Next version number: max(existing) + 1, or 1 when there are no prior rows."""
    vals = list(existing_versions)
    return (max(vals) + 1) if vals else 1


def apply_publish_flip(rows: Iterable, target_id) -> Optional[int]:
    """Mutate is_active so ONLY the target row is active. Return the previously-active
    version among the siblings (excluding the target), or None.

    Pure over any objects exposing .id / .version / .is_active — the route calls it on
    ORM rows inside one transaction; tests call it on stand-ins.
    """
    rows = list(rows)
    prior = [
        r.version for r in rows
        if r.is_active and str(r.id) != str(target_id)
    ]
    previous_active_version = max(prior) if prior else None
    for r in rows:
        r.is_active = str(r.id) == str(target_id)
    return previous_active_version


def _active_content(row) -> dict:
    return {
        "prompt_prepend": row.prompt_prepend or "",
        "prompt_append": row.prompt_append or "",
        "output_contract_extra": row.output_contract_extra or "",
    }


def _nearest_ancestor_active(ancestor_active: list[tuple[str, object]]) -> tuple[str | None, object | None]:
    for anc_scope, row in ancestor_active:
        if row is not None:
            return anc_scope, row
    return None, None


def build_agent_summary(
    agent_id: str, rows: Iterable, ancestor_active: list[tuple[str, object]] | None = None,
) -> dict:
    """Summarize all version rows for one agent+scope into the summary[] shape.

    `ancestor_active` (nearest-first) is consulted ONLY when this tier has no active
    row of its own — draft_count/latest_version always describe THIS tier's own
    history, never an ancestor's; only `active`/`inherited_from` fall through.
    """
    rows = list(rows)
    ancestor_active = ancestor_active or []
    if not rows:
        inherited_from, inherited_row = _nearest_ancestor_active(ancestor_active)
        return {
            "agent_id": agent_id, "active_version": None, "latest_version": None,
            "draft_count": 0, "updated_at": None,
            "active": _active_content(inherited_row) if inherited_row is not None else None,
            "inherited_from": inherited_from,
        }
    active_rows = [r for r in rows if r.is_active]
    active = max(active_rows, key=lambda r: r.version) if active_rows else None
    updated_candidates = [r.updated_at for r in rows if r.updated_at is not None]

    inherited_from = None
    active_content = None
    if active is not None:
        active_content = _active_content(active)
    else:
        inherited_from, inherited_row = _nearest_ancestor_active(ancestor_active)
        if inherited_row is not None:
            active_content = _active_content(inherited_row)

    return {
        "agent_id": agent_id,
        "active_version": active.version if active else None,
        "latest_version": max(r.version for r in rows),
        "draft_count": sum(1 for r in rows if not r.is_active),
        "updated_at": _iso(max(updated_candidates)) if updated_candidates else None,
        "active": active_content,
        "inherited_from": inherited_from,
    }


def _layer(name: str, source: str, locked: bool, content: str | None) -> dict:
    return {
        "name": name,
        "source": source,
        "locked": locked,
        "content": content,
        "chars": len(content) if content else 0,
    }


def build_preview_layers(
    lower_rows: Iterable,
    draft_prepend: str,
    draft_append: str,
    draft_output_contract_extra: str,
) -> list[dict]:
    """Compose the preview layer stack mirroring agent_profile_store.inject_prompt order:
    all prepends (scope order, then draft) -> vendor base -> contracts -> appends.

    The vendor base layer is locked with content=None (its prompt is never leaked). A
    lower-scope row that has both a prepend and an append appears twice — once before the
    vendor base, once after — exactly as inject_prompt concatenates them.
    """
    ordered = sorted(lower_rows, key=lambda r: SCOPE_ORDER.get(r.scope, 99))
    layers: list[dict] = []

    for r in ordered:
        if r.prompt_prepend:
            layers.append(_layer(f"{r.scope.title()} prompt prepend", r.scope, False, r.prompt_prepend))
    if draft_prepend:
        layers.append(_layer("Draft prompt prepend", "draft", False, draft_prepend))

    layers.append(_layer(VENDOR_LAYER_NAME, "vendor", True, None))

    for r in ordered:
        if r.output_contract_extra:
            layers.append(_layer(f"{r.scope.title()} output-contract additions", r.scope, False, r.output_contract_extra))
    if draft_output_contract_extra:
        layers.append(_layer("Draft output-contract additions", "draft", False, draft_output_contract_extra))

    for r in ordered:
        if r.prompt_append:
            layers.append(_layer(f"{r.scope.title()} prompt append", r.scope, False, r.prompt_append))
    if draft_append:
        layers.append(_layer("Draft prompt append", "draft", False, draft_append))

    return layers


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def _version_dict(row: AgentProfile) -> dict:
    return {
        "id": str(row.id),
        "version": row.version,
        "is_active": row.is_active,
        "prompt_prepend": row.prompt_prepend or "",
        "prompt_append": row.prompt_append or "",
        "output_contract_extra": row.output_contract_extra or "",
        "created_by": row.created_by,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


# ── Request/response helpers ─────────────────────────────────────────────────────────

def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


def _user_id(request: Request) -> str:
    return getattr(request.state, "user_id", "") or ""


def _validate_scope(scope: str, scope_id: str | None) -> None:
    if scope not in SCOPE_VALUES:
        raise HTTPException(status_code=422, detail=f"scope must be one of {SCOPE_VALUES}")
    if scope in ("workspace", "project") and not scope_id:
        raise HTTPException(status_code=422, detail=f"scope_id is required for {scope} scope")


def _validate_agent(agent_id: str) -> None:
    if agent_id not in AGENT_REGISTRY:
        raise HTTPException(status_code=404, detail="Unknown agent")


def _scope_filters(scope: str, scope_id: str | None) -> list:
    filters = [AgentProfile.scope == scope]
    if scope == "org":
        filters.append(AgentProfile.scope_id.is_(None))
    else:
        filters.append(AgentProfile.scope_id == uuid.UUID(str(scope_id)))
    return filters


class DraftIn(BaseModel):
    agent_id: str
    scope: str
    scope_id: Optional[str] = None
    prompt_prepend: str = ""
    prompt_append: str = ""
    output_contract_extra: str = ""


agent_profiles_router = APIRouter(
    prefix="/agent-profiles",
    dependencies=[Depends(require_permission("artifact:view"))],
)


@agent_profiles_router.get("/summary")
async def get_summary(
    request: Request,
    scope: str,
    scope_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
):
    _tenant_id(request)
    _validate_scope(scope, scope_id)
    stmt = select(AgentProfile).where(
        AgentProfile.agent_id.in_(PIPELINE_ORDER),
        *_scope_filters(scope, scope_id),
    )
    rows = list((await db.execute(stmt)).scalars().all())
    by_agent: dict[str, list] = {a: [] for a in PIPELINE_ORDER}
    for r in rows:
        by_agent.setdefault(r.agent_id, []).append(r)

    ancestor_by_agent: dict[str, list[tuple[str, object]]] = {a: [] for a in PIPELINE_ORDER}
    for anc_scope, anc_scope_id in ancestor_chain(scope, scope_id, workspace_id):
        anc_rows = list((await db.execute(
            select(AgentProfile).where(
                AgentProfile.agent_id.in_(PIPELINE_ORDER),
                AgentProfile.is_active.is_(True),
                *_scope_filters(anc_scope, anc_scope_id),
            )
        )).scalars().all())
        for r in anc_rows:
            ancestor_by_agent.setdefault(r.agent_id, []).append((anc_scope, r))

    return {"agents": [
        build_agent_summary(a, by_agent.get(a, []), ancestor_by_agent.get(a))
        for a in PIPELINE_ORDER
    ]}


@agent_profiles_router.get("/versions")
async def get_versions(
    request: Request,
    agent_id: str,
    scope: str,
    scope_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
):
    _tenant_id(request)
    _validate_agent(agent_id)
    _validate_scope(scope, scope_id)
    stmt = (
        select(AgentProfile)
        .where(AgentProfile.agent_id == agent_id, *_scope_filters(scope, scope_id))
        .order_by(AgentProfile.version.desc())
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return {"versions": [_version_dict(r) for r in rows]}


@agent_profiles_router.post(
    "/draft",
    dependencies=[Depends(require_permission("skill:edit"))],
)
async def create_draft(
    body: DraftIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)

    violations = lint_profile_fields(
        body.prompt_prepend, body.prompt_append, body.output_contract_extra
    )
    if violations:
        raise HTTPException(status_code=422, detail={"violations": violations})

    existing = list((await db.execute(
        select(AgentProfile.version).where(
            AgentProfile.agent_id == body.agent_id, *_scope_filters(body.scope, body.scope_id)
        )
    )).scalars().all())

    row = AgentProfile(
        tenant_id=uuid.UUID(str(tenant_id)),
        agent_id=body.agent_id,
        scope=body.scope,
        scope_id=uuid.UUID(str(body.scope_id)) if body.scope != "org" else None,
        version=next_version(existing),
        is_active=False,
        prompt_prepend=body.prompt_prepend or None,
        prompt_append=body.prompt_append or None,
        output_contract_extra=body.output_contract_extra or None,
        created_by=_user_id(request) or "system",
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return _version_dict(row)


@agent_profiles_router.post(
    "/{profile_id}/publish",
    dependencies=[Depends(require_permission("workspace:manage"))],
)
async def publish(
    profile_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    tenant_id = _tenant_id(request)
    target = await _load_or_404(db, profile_id)

    siblings = list((await db.execute(
        select(AgentProfile).where(
            AgentProfile.agent_id == target.agent_id,
            *_scope_filters(target.scope, str(target.scope_id) if target.scope_id else None),
        )
    )).scalars().all())

    previous_active_version = apply_publish_flip(siblings, target.id)
    await db.flush()
    await db.refresh(target)

    invalidate_profile_cache(tenant_id, target.agent_id)
    await audit_service.emit(AuditEventPayload(
        tenant_id=str(tenant_id),
        event_type="agent_profile.published",
        actor_id=_user_id(request) or None,
        resource_type="agent_profile",
        resource_id=str(target.id),
        payload={
            "agent_id": target.agent_id,
            "scope": target.scope,
            "scope_id": str(target.scope_id) if target.scope_id else None,
            "version": target.version,
            "previous_active_version": previous_active_version,
        },
    ))
    return _version_dict(target)


@agent_profiles_router.post(
    "/{profile_id}/unpublish",
    dependencies=[Depends(require_permission("workspace:manage"))],
)
async def unpublish(
    profile_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    tenant_id = _tenant_id(request)
    target = await _load_or_404(db, profile_id)

    target.is_active = False
    await db.flush()
    await db.refresh(target)

    invalidate_profile_cache(tenant_id, target.agent_id)
    await audit_service.emit(AuditEventPayload(
        tenant_id=str(tenant_id),
        event_type="agent_profile.unpublished",
        actor_id=_user_id(request) or None,
        resource_type="agent_profile",
        resource_id=str(target.id),
        payload={
            "agent_id": target.agent_id,
            "scope": target.scope,
            "scope_id": str(target.scope_id) if target.scope_id else None,
            "version": target.version,
        },
    ))
    return _version_dict(target)


@agent_profiles_router.post(
    "/{profile_id}/propose",
    status_code=201,
    dependencies=[Depends(require_permission("skill:edit"))],
)
async def propose(
    profile_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Ask the tier's owner to publish this draft, instead of publishing it yourself.

    The counterpart to `/publish` for someone who does not own the tier. Agent
    Studio's cascade is Org → Business Unit → Project → Personal, and each shared
    tier has exactly one role that may change its default; anyone else proposes.

    A DEDICATED FILING POINT rather than `POST /governance-approvals`, for the same
    reason as the budget one next door: the request's `target_ref` is what approving
    PUBLISHES, so it must be set from a version the server loaded, not from a body.
    A client that could name it could point a proposal at any profile row in the
    tenant and have the approval publish that instead.

    The tier's own owner does not come here — they hold `/publish`. Sending a
    proposal to yourself would be a request you are then blocked from deciding by
    the self-approval rule, which is a dead end rather than a safeguard.
    """
    from shared.authz.effective_role import (  # noqa: PLC0415 - avoids an import cycle
        actor_display_name,
        effective_platform_role,
    )
    from shared.services import governance_requests as governance_service  # noqa: PLC0415
    from shared.services.governance_requests import GovernanceError  # noqa: PLC0415

    tenant_id = _tenant_id(request)
    target = await _load_or_404(db, profile_id)

    if target.scope == "user":
        # A personal override is nobody else's to approve — it is one person's own
        # setting, outside the cascade everyone else inherits from.
        raise HTTPException(
            status_code=422,
            detail={
                "code": "NOT_A_SHARED_TIER",
                "message": "A personal default is yours alone; there is nobody to propose it to.",
            },
        )

    request_type = f"agent_default_{target.scope}"
    scope_label = {"org": "organization", "workspace": "business unit", "project": "project"}[
        target.scope
    ]
    role = await effective_platform_role(db, request)
    name = await actor_display_name(db, request)

    # The unit the proposal is filed against. An org-scoped profile has no
    # workspace of its own, so it is filed against the caller's active unit — the
    # request still has to belong somewhere for the queue's scope filter to work.
    workspace_id = str(target.scope_id) if target.scope_id else await active_workspace_for_request(
        db, request
    )
    if not workspace_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "NO_WORKSPACE",
                "message": "Choose a business unit before proposing an organization default.",
            },
        )

    try:
        return await governance_service.create_request(
            db,
            tenant_id=tenant_id,
            initiator_id=_user_id(request),
            initiator_name=name,
            initiator_role=role,
            request_type=request_type,
            title=f"{target.agent_id} default change ({scope_label})",
            description=(
                f"{name} proposed a {target.agent_id} behavior change for the "
                f"{scope_label} default, version {target.version}."
            ),
            workspace_id=workspace_id,
            project_id=str(target.scope_id) if target.scope == "project" else None,
            # The DRAFT version's id. Approving publishes exactly this — which is
            # why the proposal carries an id rather than the prompt text: the
            # approver agrees to a specific draft, and re-reading the text at
            # decision time would publish whatever it had since become.
            target_ref=str(target.id),
            payload={"agentId": target.agent_id, "scope": target.scope, "version": target.version},
            system_raised=True,
        )
    except GovernanceError as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"code": exc.code, "message": str(exc)}
        )


@agent_profiles_router.post(
    "/preview",
    dependencies=[Depends(require_permission("skill:edit"))],
)
async def preview(
    body: DraftIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)

    # Active layers from every ancestor tier the draft would stack on. `DraftIn`
    # doesn't carry workspace_id/project_id (draft-create genuinely doesn't need
    # them — a draft only ever belongs to its own tier), but preview's frontend
    # caller (behavior-tab.tsx) already sends them via a superset body; FastAPI
    # ignores fields DraftIn doesn't declare, so read them off the raw request
    # body instead of widening DraftIn's contract for every other caller.
    raw = await request.json()
    workspace_id = raw.get("workspace_id")
    lower_rows: list = []
    for anc_scope, anc_scope_id in ancestor_chain(body.scope, body.scope_id, workspace_id):
        anc_rows = list((await db.execute(
            select(AgentProfile).where(
                AgentProfile.agent_id == body.agent_id,
                AgentProfile.is_active.is_(True),
                *_scope_filters(anc_scope, anc_scope_id),
            )
        )).scalars().all())
        lower_rows.extend(anc_rows)

    layers = build_preview_layers(
        lower_rows,
        body.prompt_prepend or "",
        body.prompt_append or "",
        body.output_contract_extra or "",
    )
    warnings = lint_profile_fields(
        body.prompt_prepend, body.prompt_append, body.output_contract_extra
    )
    return {"layers": layers, "warnings": warnings}


async def _load_or_404(db: AsyncSession, profile_id: str) -> AgentProfile:
    """Load a profile by id (RLS scopes to the caller's tenant -> cross-tenant = 404)."""
    try:
        pid = uuid.UUID(str(profile_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="Not found")
    row = (await db.execute(
        select(AgentProfile).where(AgentProfile.id == pid)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row
