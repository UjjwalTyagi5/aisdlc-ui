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

RBAC (design §3.5, extended by sub-project 2, then made real by sub-project 3): reads
gate on the "artifact:view" floor (router-level, matching the capabilities router)
PLUS, at the personal ("user") scope only, `assert_own_user_scope` — the same
tenant-wide `GET .../summary`/`.../versions` reads that anyone can run against
org/workspace/project also accept `scope=user`, and without this extra check any
authenticated caller could read another user's personal default by supplying their
`scope_id`. draft/preview/publish/unpublish use the in-body, scope-aware
`assert_can_write_agent_scope` check instead of a route-level Depends(); `propose()`
calls the `resolve_actor_tier_access` helper underneath that check directly (it has
already ruled out scope=="user" itself, so it does not need that wrapper's personal-
scope branch). For the personal scope, `assert_can_write_agent_scope` allows any role
except org_admin/bu_admin to write ONLY their own scope_id; for org/workspace/project
scope, both paths defer to `resolve_actor_tier_access` (see that function's docstring
for the full per-scope rules) for a real per-resource tier-ownership lookup instead of
a blanket permission string — at a high level: org is owned via the admin:* wildcard,
workspace via a live bu_admin binding scoped to that workspace, project via a live
project_admin binding scoped to that project, and each tier also reports "may_propose"
for the role one tier up (bu_admin/project_admin/any project member respectively).
"publish"/"unpublish" require ownership; "draft" and `propose()` accept ownership OR
propose-eligibility — a non-owner may draft something and then file it via `propose()`
for the owner to publish, using the `agent_default_org`/`agent_default_workspace`/
`agent_default_project` governance request types and approval machinery that predate
this sub-project (sub-project 3 only changed WHO is allowed to call `propose()`, via
`resolve_actor_tier_access`, not the request types or approval flow it files into).
`propose()` has no route-level "skill:edit" gate — it never did; it is, and was,
just another scope-aware in-body caller. Every route still carries a
require_permission sentinel (the router-level floor) so the process_api D-05 boot
scan stays green.

Note: `assert_can_write_agent_scope`/`assert_own_user_scope` do NOT emit the
RBAC_DENIALS metric or an access-denied audit row that the route-level
`Depends(require_permission(...))` gates they replaced used to on a 403 — a disclosed,
accepted gap (see the sub-project 2 final review), not an oversight. Also: a published
personal-tier default is fully persisted and readable/writable per the rules above, but
is NOT yet applied at actual agent-run time — `resolve_profile` in
`agent_profile_store.py` only resolves org/workspace/project. Wiring the runtime is
tracked as separate follow-up work, not part of this sub-project's scope.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Iterable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config.agent_registry import AGENT_REGISTRY
from shared.audit.models import AuditEventPayload
from shared.audit.service import audit_service
from shared.authz.dependency import require_permission
from shared.authz.read_scope import live_binding
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

SCOPE_VALUES: tuple[str, ...] = ("org", "workspace", "project", "user")
SCOPE_ORDER: dict[str, int] = {"org": 0, "workspace": 1, "project": 2}


def ancestor_chain(
    scope: str, scope_id: str | None, workspace_id: str | None, project_id: str | None = None,
) -> list[tuple[str, str | None]]:
    """Nearest-first ancestor (scope, scope_id) pairs above `scope`, for inheritance
    resolution. `workspace_id` is the project's own parent BU — required to resolve a
    project's WORKSPACE ancestor specifically; omitted, a project-scope request still
    resolves its org ancestor, just not its workspace ancestor. `project_id` is
    additionally needed to resolve a PERSONAL (user) scope's project ancestor — the
    only scope whose full chain is longer than one hop. Never errors on a missing id.
    Shared with skill_store.py's list_skills_merged, which needs the identical chain
    shape.
    """
    if scope == "org":
        return []
    if scope == "workspace":
        return [("org", None)]
    if scope == "project":
        return [("workspace", workspace_id), ("org", None)] if workspace_id else [("org", None)]
    if scope == "user":
        chain: list[tuple[str, str | None]] = []
        if project_id:
            chain.append(("project", project_id))
        if workspace_id:
            chain.append(("workspace", workspace_id))
        chain.append(("org", None))
        return chain
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
    if scope in ("workspace", "project", "user") and not scope_id:
        raise HTTPException(status_code=422, detail=f"scope_id is required for {scope} scope")


def _same_actor(a: str | None, b: str | None) -> bool:
    """True when both ids are present and, compared as strings, identical.

    Both the write-side ownership check (below) and the read-side one
    (`assert_own_user_scope`) need the exact same "is this really you" test —
    centralized so the two can't silently drift into different normalization
    rules (e.g. one comparing raw strings, the other comparing parsed UUIDs).
    """
    return bool(a) and bool(b) and str(a) == str(b)


async def assert_can_write_agent_scope(
    tenant_id: str,
    perms: list[str],
    role: str | None,
    scope: str,
    scope_id: str | None,
    actor_user_id: str,
    *,
    action: Literal["draft", "publish"],
) -> None:
    """Scope-aware authorization for an Agent Studio write (Behavior draft/publish/
    propose; Skills create/update/delete/toggle/activate/propose). Raises
    HTTPException(403) on denial.

    user: self-service, unchanged from sub-project 2 — allowed only when `role` is
    neither "org_admin" nor "bu_admin" AND `scope_id` equals the caller's own user id.

    org/workspace/project: real tier ownership + "propose one tier up," via
    `resolve_actor_tier_access` — NOT the old blanket permission-string check
    (sub-project 3 replaces it deliberately; see the sub-project 3 spec's
    "Existing state" section for why the old check was a real bug, not just
    incomplete). "publish" requires ownership. "draft" requires ownership OR
    propose-eligibility — a non-owner may still draft, to have something to
    propose.
    """
    if scope == "user":
        if role is None or role in ("org_admin", "bu_admin"):
            raise HTTPException(status_code=403, detail="Forbidden")
        if not _same_actor(scope_id, actor_user_id):
            raise HTTPException(status_code=403, detail="Forbidden")
        return
    owns, may_propose = await resolve_actor_tier_access(
        tenant_id, actor_user_id, perms, scope, scope_id,
    )
    if action == "publish":
        if not owns:
            raise HTTPException(status_code=403, detail="Forbidden")
        return
    if not (owns or may_propose):
        raise HTTPException(status_code=403, detail="Forbidden")


def assert_own_user_scope(scope: str, scope_id: str | None, actor_user_id: str) -> None:
    """Read-side twin of `assert_can_write_agent_scope`'s personal-tier ownership rule.

    The GET routes (`summary`, `versions`, and Skills' `list`/`detail`) have no
    permission gate to piggyback on for this — only the router's blanket
    `artifact:view` floor, which every tenant member holds. Without this check,
    `scope=user&scope_id=<anyone>` would let any authenticated caller read another
    user's personal Behavior/Skills content (final whole-branch review finding C1).
    Every OTHER scope is left exactly as broadly readable as before — org/workspace/
    project are deliberately SHARED tiers everyone in the cascade needs to see (that
    is the entire point of the inheritance-visibility work in sub-project 1); `user`
    is the one tier that is genuinely private to a single person, which is why it
    alone needs this extra check.
    """
    if scope == "user" and not _same_actor(scope_id, actor_user_id):
        raise HTTPException(status_code=403, detail="Forbidden")


async def resolve_actor_tier_access(
    tenant_id: str, actor_user_id: str, perms: list[str], scope: str, scope_id: str | None,
) -> tuple[bool, bool]:
    """(owns, may_propose) for `actor_user_id` on this EXACT (scope, scope_id) — a
    real per-resource lookup, never the global "highest standing" role
    (`effective_platform_role`/`resolve_platform_role_for_user` are scope-blind and
    must not be reused here — a bu_admin on Workspace X must not pass an ownership
    check for Workspace Y just because they're "a bu_admin" tenant-wide).

    owns: may publish/unpublish/activate this tier directly.
    may_propose: may draft-and-file-for-approval at this tier. Irrelevant once
    `owns` is True, but reported independently — callers decide precedence.

    org: owns via the admin:* wildcard alone (org_admin always carries it; no
    role_bindings lookup needed for a role that IS the wildcard). may_propose via
    a live bu_admin binding ANYWHERE in the tenant — org is the tenant's one
    instance, so "one tier up from workspace" needs no specific workspace id.

    workspace: owns via the admin:* wildcard (an org_admin owns every tier — see
    below) OR a live bu_admin binding scoped to this exact workspace. may_propose
    via a live project_admin binding on ANY project whose workspace_id is this
    workspace (one tier up from "some project in this BU").

    project: owns via the admin:* wildcard OR a live project_admin binding scoped
    to this exact project. may_propose via ANY live role_binding scoped to this
    exact project, excluding role_name='contributor' (via `grants_scope()`,
    read_scope.py's canonical "confers reach, not merely membership" predicate —
    NOT a bare `!=`, which drops every custom role: `role_name` is NULL for one,
    and `NULL != 'contributor'` is NULL, not true) — contributor is documented
    elsewhere as "not enough to open an agent"; membership alone earns propose
    access for every other role.

    The admin:* shortcut at every tier (not just org) is deliberate, not an
    oversight: an org_admin's role_bindings row is written at
    scope_kind='organization', which never matches the workspace/project
    branches' own scope_kind predicates — omitting the shortcut here would 403 an
    org_admin on every workspace/project-tier write, contradicting this sub-
    project's own spec ("Org Admin: unaffected — wildcard already covered every
    case") and silently revoking access `has_permission`'s wildcard shortcut used
    to grant everywhere before this function existed (final whole-branch review,
    sub-project 3, Critical #1).
    """
    from shared.authz.permissions import has_permission as _has_perm  # noqa: PLC0415 - kept local for symmetry with other lazy imports here (the module-level import this comment used to describe was removed by sub-project 3 Task 2)
    from shared.authz.read_scope import grants_scope  # noqa: PLC0415
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    is_org_admin = _has_perm(perms, "admin:*")

    if scope == "org":
        async with get_db_session_for_tenant(tenant_id) as session:
            hit = (await session.execute(
                text(
                    f"SELECT 1 FROM role_bindings rb WHERE {live_binding()} "
                    f"AND rb.scope_kind = 'business_unit' AND rb.role_name = 'bu_admin' LIMIT 1"
                ),
                {"u": actor_user_id, "now": datetime.now(tz=timezone.utc)},
            )).first()
        return is_org_admin, hit is not None

    if scope == "workspace":
        async with get_db_session_for_tenant(tenant_id) as session:
            owns_hit = (await session.execute(
                text(
                    f"SELECT 1 FROM role_bindings rb WHERE {live_binding()} "
                    f"AND rb.scope_kind = 'business_unit' AND rb.scope_id = :w "
                    f"AND rb.role_name = 'bu_admin' LIMIT 1"
                ),
                {"u": actor_user_id, "w": scope_id, "now": datetime.now(tz=timezone.utc)},
            )).first()
            propose_hit = (await session.execute(
                text(
                    f"SELECT 1 FROM role_bindings rb WHERE {live_binding()} "
                    f"AND rb.scope_kind = 'project' AND rb.role_name = 'project_admin' "
                    f"AND rb.scope_id IN (SELECT id FROM projects WHERE workspace_id = CAST(:w AS uuid)) "
                    f"LIMIT 1"
                ),
                {"u": actor_user_id, "w": scope_id, "now": datetime.now(tz=timezone.utc)},
            )).first()
        owns = is_org_admin or owns_hit is not None
        return owns, owns or propose_hit is not None

    if scope == "project":
        async with get_db_session_for_tenant(tenant_id) as session:
            owns_hit = (await session.execute(
                text(
                    f"SELECT 1 FROM role_bindings rb WHERE {live_binding()} "
                    f"AND rb.scope_kind = 'project' AND rb.scope_id = :p "
                    f"AND rb.role_name = 'project_admin' LIMIT 1"
                ),
                {"u": actor_user_id, "p": scope_id, "now": datetime.now(tz=timezone.utc)},
            )).first()
            propose_hit = (await session.execute(
                text(
                    f"SELECT 1 FROM role_bindings rb WHERE {live_binding()} "
                    f"AND rb.scope_kind = 'project' AND rb.scope_id = :p "
                    f"AND {grants_scope()} LIMIT 1"
                ),
                {"u": actor_user_id, "p": scope_id, "now": datetime.now(tz=timezone.utc)},
            )).first()
        owns = is_org_admin or owns_hit is not None
        return owns, owns or propose_hit is not None

    return False, False


async def _project_workspace_id(tenant_id: str, project_id: str) -> Optional[str]:
    """The workspace a project belongs to — for filing a project-scope proposal
    against the RIGHT unit. `target.scope_id`/`body.scope_id` at project scope is
    the PROJECT id, not the workspace id; using it directly as `workspace_id`
    (the bug this fixes) files the request under an id that is never a real
    workspace, so `allowed_workspace_ids`' `IN (:allowed)` filter never matches it
    — the request becomes invisible in every approver's queue except the
    initiator's own (final whole-branch review, sub-project 3, Important #5)."""
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    async with get_db_session_for_tenant(tenant_id) as session:
        row = (await session.execute(
            text("SELECT workspace_id FROM projects WHERE id = :p"),
            {"p": project_id},
        )).first()
    return str(row[0]) if row else None


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
    project_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
):
    _tenant_id(request)
    _validate_scope(scope, scope_id)
    assert_own_user_scope(scope, scope_id, _user_id(request))
    stmt = select(AgentProfile).where(
        AgentProfile.agent_id.in_(PIPELINE_ORDER),
        *_scope_filters(scope, scope_id),
    )
    rows = list((await db.execute(stmt)).scalars().all())
    by_agent: dict[str, list] = {a: [] for a in PIPELINE_ORDER}
    for r in rows:
        by_agent.setdefault(r.agent_id, []).append(r)

    ancestor_by_agent: dict[str, list[tuple[str, object]]] = {a: [] for a in PIPELINE_ORDER}
    for anc_scope, anc_scope_id in ancestor_chain(scope, scope_id, workspace_id, project_id):
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
    assert_own_user_scope(scope, scope_id, _user_id(request))
    stmt = (
        select(AgentProfile)
        .where(AgentProfile.agent_id == agent_id, *_scope_filters(scope, scope_id))
        .order_by(AgentProfile.version.desc())
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return {"versions": [_version_dict(r) for r in rows]}


@agent_profiles_router.post("/draft")
async def create_draft(
    body: DraftIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    from shared.authz.effective_role import effective_platform_role  # noqa: PLC0415 - avoids an import cycle, matches propose()'s existing pattern

    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    role = await effective_platform_role(db, request)
    await assert_can_write_agent_scope(
        tenant_id, getattr(request.state, "permissions", []) or [], role,
        body.scope, body.scope_id, _user_id(request), action="draft",
    )

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


@agent_profiles_router.post("/{profile_id}/publish")
async def publish(
    profile_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    from shared.authz.effective_role import effective_platform_role  # noqa: PLC0415

    tenant_id = _tenant_id(request)
    target = await _load_or_404(db, profile_id)
    role = await effective_platform_role(db, request)
    await assert_can_write_agent_scope(
        tenant_id, getattr(request.state, "permissions", []) or [], role,
        target.scope, str(target.scope_id) if target.scope_id else None,
        _user_id(request), action="publish",
    )

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


@agent_profiles_router.post("/{profile_id}/unpublish")
async def unpublish(
    profile_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    from shared.authz.effective_role import effective_platform_role  # noqa: PLC0415

    tenant_id = _tenant_id(request)
    target = await _load_or_404(db, profile_id)
    role = await effective_platform_role(db, request)
    await assert_can_write_agent_scope(
        tenant_id, getattr(request.state, "permissions", []) or [], role,
        target.scope, str(target.scope_id) if target.scope_id else None,
        _user_id(request), action="publish",
    )

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

    perms = getattr(request.state, "permissions", []) or []
    owns, may_propose = await resolve_actor_tier_access(
        tenant_id, _user_id(request), perms, target.scope,
        str(target.scope_id) if target.scope_id else None,
    )
    if not (owns or may_propose):
        raise HTTPException(status_code=403, detail="Forbidden")

    from shared.services.eval_gate import latest_passing_evaluation  # noqa: PLC0415

    passing = await latest_passing_evaluation(tenant_id, "profile", str(target.id))
    if passing is None:
        raise HTTPException(status_code=422, detail={
            "code": "EVALUATION_REQUIRED",
            "message": "Run an evaluation before proposing this change.",
        })

    request_type = f"agent_default_{target.scope}"
    scope_label = {"org": "organization", "workspace": "business unit", "project": "project"}[
        target.scope
    ]
    role = await effective_platform_role(db, request)
    name = await actor_display_name(db, request)

    # The unit the proposal is filed against. An org-scoped profile has no
    # workspace of its own, so it is filed against the caller's active unit — the
    # request still has to belong somewhere for the queue's scope filter to work.
    # A project-scoped profile's scope_id is the PROJECT id, not a workspace id —
    # it must be resolved through the project row, not used as-is (see
    # _project_workspace_id's docstring).
    if target.scope == "project":
        workspace_id = await _project_workspace_id(tenant_id, str(target.scope_id))
    elif target.scope_id:
        workspace_id = str(target.scope_id)
    else:
        workspace_id = await active_workspace_for_request(request, tenant_id)
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


@agent_profiles_router.post("/{profile_id}/evaluate", status_code=201)
async def evaluate(
    profile_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Run the deterministic golden-task rubric against this draft and record the
    result — a precondition for propose() (see Global Constraints and the
    sub-project 4 spec). For scope=="org" (R3 — every workspace/project in the
    tenant inherits from an org default), the evaluator must not be the draft's
    own author (SELF_EVALUATION_BLOCKED) — R2 (workspace/project) may self-evaluate.
    """
    from shared.authz.effective_role import effective_platform_role  # noqa: PLC0415 - avoids an import cycle, matches propose()'s existing pattern
    from shared.services.eval_gate import run_evaluation  # noqa: PLC0415

    tenant_id = _tenant_id(request)
    target = await _load_or_404(db, profile_id)
    if target.scope == "user":
        raise HTTPException(status_code=422, detail={
            "code": "NOT_A_SHARED_TIER",
            "message": "A personal default has nothing to evaluate against.",
        })

    actor_id = _user_id(request)
    if target.scope == "org" and target.created_by == actor_id:
        raise HTTPException(status_code=403, detail={
            "code": "SELF_EVALUATION_BLOCKED",
            "message": "An organization-wide default must be evaluated by someone other than its author.",
        })

    role = await effective_platform_role(db, request)
    body = "\n".join(filter(None, [
        target.prompt_prepend, target.prompt_append, target.output_contract_extra,
    ]))
    row = await run_evaluation(
        tenant_id=tenant_id, target_type="profile", target_id=str(target.id),
        agent_id=target.agent_id, scope=target.scope, body=body,
        evaluator_id=actor_id, evaluator_role=role,
    )
    return row


@agent_profiles_router.post("/preview")
async def preview(
    body: DraftIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    from shared.authz.effective_role import effective_platform_role  # noqa: PLC0415

    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    role = await effective_platform_role(db, request)
    await assert_can_write_agent_scope(
        tenant_id, getattr(request.state, "permissions", []) or [], role,
        body.scope, body.scope_id, _user_id(request), action="draft",
    )

    # Active layers from every ancestor tier the draft would stack on. `DraftIn`
    # doesn't carry workspace_id/project_id (draft-create genuinely doesn't need
    # them — a draft only ever belongs to its own tier), but preview's frontend
    # caller (behavior-tab.tsx) already sends them via a superset body; FastAPI
    # ignores fields DraftIn doesn't declare, so read them off the raw request
    # body instead of widening DraftIn's contract for every other caller.
    raw = await request.json()
    workspace_id = raw.get("workspace_id")
    project_id = raw.get("project_id")
    lower_rows: list = []
    for anc_scope, anc_scope_id in ancestor_chain(body.scope, body.scope_id, workspace_id, project_id):
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
