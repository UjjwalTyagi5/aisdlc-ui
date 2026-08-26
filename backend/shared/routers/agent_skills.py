"""Agent Skills lifecycle API — vendor+custom skill catalog, authoring, toggles (Phase 4).

A "skill" is a named, LLM-facing capability snippet (SKILL.md convention) attached to one
of the 8 pipeline agents and resolved org -> workspace -> project. Two origins coexist:

  vendor  — packaged on disk (shared/skills/registry.py); surfaced read-only + toggleable.
  custom  — org-authored content persisted in agent_skills; versioned, lint-gated,
            soft-deletable, and per-scope enable/disable.

This router owns the HTTP contract only. All persistence lives in the sibling store
(shared/services/skill_store.py) and cache-invalidation in shared/services/skill_runtime.py
— imported lazily so this module never hard-fails if the store is still landing in a
parallel branch. The router lints/validates, then delegates.

Serialization convention: snake_case in responses, matching the sibling agent_profiles
router (whose lint style + violation shape + RBAC layering this mirrors). The frontend BFF
reads these keys verbatim.

RBAC (mirrors agent_profiles, extended by sub-project 2, then made real by
sub-project 3): reads gate on the "artifact:view" floor (router-level) PLUS, at the
personal ("user") scope only, `assert_own_user_scope` (imported from agent_profiles)
on `list`/detail — without it, `scope=user&scope_id=<anyone>` would let any
authenticated caller read another user's personal skill catalog. create/update/
toggle/delete/activate use the in-body, scope-aware `assert_can_write_agent_scope`
check (also imported from agent_profiles) instead of a route-level Depends();
`propose_skill` calls the `resolve_actor_tier_access` helper underneath that check
directly (it has already ruled out scope=="user" itself, so it does not need that
wrapper's personal-scope branch — same pattern as `AgentProfile.propose()`). For the
personal scope, `assert_can_write_agent_scope` allows any role except org_admin/
bu_admin to write ONLY their own scope_id; for org/workspace/project scope, both
paths defer to `resolve_actor_tier_access` (also imported from agent_profiles — see
its docstring for the full per-scope rules) for a real per-resource tier-ownership
lookup instead of a blanket permission string. "activate" requires ownership;
create/update/toggle/delete and `propose_skill` accept ownership OR
propose-eligibility (action="draft" for the first four; the same owns-or-may_propose
test inline for `propose_skill`).

`create_skill`/`update_skill` activate their write CONDITIONALLY rather than always:
each re-derives `owns` via `resolve_actor_tier_access` (short-circuited to True at the
personal scope, which that helper does not model) and passes `activate=owns` into the
store. This is a deliberate divergence from `AgentProfile.create_draft`, which always
inserts `is_active=False` and requires a separate `/publish` call regardless of who
made the draft: an owner's skill write goes live immediately (no extra publish step),
while a non-owner's write lands as an inactive version with nothing served until an
owner activates it directly or approves that non-owner's `propose_skill` request.

`propose_skill` is the Skills counterpart to `AgentProfile.propose()` — ask the
tier's owner to activate a non-owner's inactive draft, instead of activating it
yourself. It reuses the exact same `agent_default_org`/`agent_default_workspace`/
`agent_default_project` governance request types and approver-routing/self-approval/
audit machinery Behavior's `propose()` already used; no `skill_default_*` type family
was introduced (considered and rejected — see the sub-project 3 design doc). Skills
has no single-row-UUID path param elsewhere in this API, so `propose_skill` resolves
its `target_ref` via `get_latest_draft_version` (the newest inactive version of that
skill_key at that scope) rather than a body-supplied id.

Every route still carries a require_permission sentinel (the router-level floor) so
the process_api D-05 boot scan stays green.

EVALUATION GATE (sub-project 4, mirrors agent_profiles above): the same second,
independent axis layered ALONGSIDE the tier-ownership RBAC above — authorization
answers "may this actor act here at all"; evaluation answers "has this specific
draft passed a quality gate" — applied here to Skills' propose-to-approval path.
`propose_skill()` requires a PASSING `AgentDefaultEvaluation` row for the EXACT
draft version it resolves via `get_latest_draft_version`, checked via
`latest_passing_evaluation(tenant_id, "skill", draft["id"])`
(shared/services/eval_gate.py); missing or failing, it refuses with 422
`EVALUATION_REQUIRED` before filing the governance request. `evaluate_skill()`
(POST .../evaluate) is the only way to close that gate: it runs the same
deterministic, no-LLM `evaluate_agent_default` rubric
(shared/eval/agent_studio_scoring.py — identical scoring logic to Behavior's
`evaluate()`) via `run_evaluation` and records a PASS/FAIL row. scope=="user" is
rejected outright (422 `NOT_A_SHARED_TIER`) — a personal draft is never proposed,
so it is never evaluated. For scope=="org" (R3 — every workspace/project in the
tenant inherits from an org default), the evaluator may not be the draft's own
author (403 `SELF_EVALUATION_BLOCKED`); workspace/project scope (R2) has no such
restriction and may self-evaluate. Because R3 requires a DIFFERENT evaluator than
the author, `get_latest_draft_version`'s `created_by` filter (skill_store.py) is now
OPTIONAL — default `None`, meaning no filter on that column at all: `evaluate_skill`
passes none, so it can find ANY pending draft at that scope+key, not just the
caller's own. `propose_skill`'s existing call is UNCHANGED — it still always passes
its own actor id explicitly, preserving sub-project 3's Critical #4 fix that a
non-owner's proposal must only ever target their own draft. The owner's direct
`activate_version` path is deliberately NOT gated by any of this, same as
`publish()` in agent_profiles — only the propose-to-approval path is.
`evaluate_skill()`'s callers all need router-level standing too, not just
`artifact:view` — it defers to `resolve_actor_tier_access` and requires
`owns or may_propose` at the target scope, same as `propose_skill`, so a bare
tenant member with no standing at that tier cannot supply R3's "someone other
than the author" blessing for a draft they have no relationship to (final
whole-branch review, sub-project 4, Important #1, fixed).

KNOWN LIMITATION, DISCLOSED (final whole-branch review, sub-project 4,
Important #3): `evaluate_skill()`'s unfiltered `get_latest_draft_version` lookup
and `propose_skill()`'s caller-filtered one can resolve DIFFERENT rows when
more than one non-owner has a pending draft of the SAME skill_key at the SAME
scope simultaneously — evaluate_skill() always finds the highest-versioned
pending draft tenant-wide (by design, for R3), while propose_skill() finds only
the caller's own. A proposer in that collision could see their Evaluate call
report PASS (because it silently evaluated a DIFFERENT contributor's draft) yet
have their own Propose call still 422 with EVALUATION_REQUIRED. Not a security
issue — propose_skill()'s own-draft filter is untouched, so nobody can ever
propose a draft they didn't write, and the failure is a confusing dead end, not
a silent wrong action — but it is a real correctness gap for the multi-
contributor case this propose flow exists to serve. Closing it needs either the
evaluate response to name the specific draft it targeted so the client can
reason about a mismatch, or `propose_skill()` accepting the evaluated draft's
own resolution — a genuine design question, not a one-line fix, left as
follow-up rather than silently unaddressed.

Role resolution here uses `resolve_platform_role_for_user` rather than
`effective_platform_role` (agent_profiles.py's routes all take a `db` param already;
these routes don't, so this variant opens its own tenant-scoped session) — both
delegate to the same `platform_role_for`, so "highest standing wins" is one
implementation shared by both call shapes, not two that could drift.

Note: like agent_profiles.py, `assert_can_write_agent_scope`/`assert_own_user_scope`
do not emit the RBAC_DENIALS metric or an access-denied audit row the removed
route-level Depends() gates used to on a 403 (disclosed, accepted gap). A published
personal-tier skill is fully persisted and readable/writable per the rules above, but
is NOT yet applied at actual agent-run time — `skill_runtime.py`'s resolver has its own
separate cascade logic, untouched by this sub-project, tracked as follow-up work.

ROUTE ORDER: the literal-suffix routes (/toggle, /{skill_key}/versions,
/{skill_key}/activate/{version}) and the single-segment authoring routes are declared BEFORE
the two-segment /{origin}/{skill_key} detail route, and `origin` is constrained to
vendor|custom — so /toggle and /{key}/versions can never be swallowed by the {origin} slot.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel

from config.agent_registry import AGENT_REGISTRY
from shared.audit.models import AuditEventPayload
from shared.audit.service import audit_service
from shared.authz.dependency import require_permission
from shared.skills.registry import get_vendor_skill

# Reuse the agent_profiles guardrail regexes + scope vocabulary + presentation order —
# a single source of truth for both authoring surfaces (do NOT copy the patterns).
from shared.routers.agent_profiles import (
    FORBIDDEN_PATTERNS,
    SCOPE_VALUES,
    _project_workspace_id,
    ancestor_chain,
    assert_can_write_agent_scope,
    assert_own_user_scope,
    resolve_actor_tier_access,
)
from shared.authz.effective_role import resolve_platform_role_for_user

# ── Lint rules (module-level so tests + create/update reuse them) ────────────────────
SKILL_KEY_RE = re.compile(r"^[a-z0-9-]{2,64}$")

MAX_DISPLAY_NAME = 200
MAX_DESCRIPTION = 500
MAX_WHEN_TO_USE = 500
MAX_BODY = 8000

SKILL_FIELD_CAPS: dict[str, int] = {
    "display_name": MAX_DISPLAY_NAME,
    "description": MAX_DESCRIPTION,
    "when_to_use": MAX_WHEN_TO_USE,
    "body": MAX_BODY,
}

# Fields that must be present and non-empty on create/update.
REQUIRED_SKILL_FIELDS: tuple[str, ...] = ("display_name", "body")


class Origin(str, Enum):
    vendor = "vendor"
    custom = "custom"


# ── Pure helpers (unit-testable without a DB) ────────────────────────────────────────

def validate_skill_key(skill_key: str | None) -> list[dict]:
    """Return an [invalid_key] violation list when skill_key breaks ^[a-z0-9-]{2,64}$."""
    if not SKILL_KEY_RE.match(skill_key or ""):
        return [{
            "field": "skill_key",
            "code": "invalid_key",
            "message": "skill_key must match ^[a-z0-9-]{2,64}$ (lowercase letters, digits, hyphens).",
        }]
    return []


def lint_skill_fields(
    display_name: str | None,
    description: str | None,
    when_to_use: str | None,
    body: str | None,
) -> list[dict]:
    """Return ALL content-lint violations (never short-circuit on the first).

    Each violation is {"field", "code", "message"}. Codes: "required", "too_long",
    "forbidden_pattern". Required fields are checked first, then caps per field, then every
    agent_profiles deny-regex against every field.
    """
    fields = {
        "display_name": display_name or "",
        "description": description or "",
        "when_to_use": when_to_use or "",
        "body": body or "",
    }
    violations: list[dict] = []
    for field in REQUIRED_SKILL_FIELDS:
        if not fields[field].strip():
            violations.append({
                "field": field,
                "code": "required",
                "message": f"{field} is required and must be non-empty.",
            })
    for field, cap in SKILL_FIELD_CAPS.items():
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


# ── Lazy store / runtime accessors ───────────────────────────────────────────────────
# Indirection so `import shared.routers.agent_skills` never hard-fails while the parallel
# skill_store / skill_runtime modules are still landing. Tests patch these accessors on
# the router module to inject fakes — no live DB, no real store module required.

def _store():
    from shared.services import skill_store
    return skill_store


def _runtime():
    from shared.services import skill_runtime
    return skill_runtime


# ── Request / response helpers ─────────────────────────────────────────────────────────

def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


def _user_id(request: Request) -> str:
    return getattr(request.state, "user_id", "") or ""


def _validate_agent(agent_id: str) -> None:
    if agent_id not in AGENT_REGISTRY:
        raise HTTPException(status_code=404, detail="Unknown agent")


def _validate_scope(scope: str, scope_id: str | None) -> None:
    if scope not in SCOPE_VALUES:
        raise HTTPException(status_code=422, detail=f"scope must be one of {SCOPE_VALUES}")
    if scope in ("workspace", "project", "user") and not scope_id:
        raise HTTPException(status_code=422, detail=f"scope_id is required for {scope} scope")


async def _emit(request: Request, tenant_id: str, event_type: str, skill_key: str, payload: dict) -> None:
    """Fire-and-forget audit emit (audit_service.emit schedules + never raises)."""
    await audit_service.emit(AuditEventPayload(
        tenant_id=str(tenant_id),
        event_type=event_type,
        actor_id=_user_id(request) or None,
        resource_type="agent_skill",
        resource_id=skill_key,
        payload=payload,
    ))


# ── Request bodies ─────────────────────────────────────────────────────────────────────

class CreateSkillIn(BaseModel):
    agent_id: str
    scope: str
    scope_id: Optional[str] = None
    skill_key: str
    display_name: str
    description: Optional[str] = None
    when_to_use: Optional[str] = None
    body: str


class UpdateSkillIn(BaseModel):
    agent_id: str
    scope: str
    scope_id: Optional[str] = None
    display_name: str
    description: Optional[str] = None
    when_to_use: Optional[str] = None
    body: str


class ToggleIn(BaseModel):
    agent_id: str
    scope: str
    scope_id: Optional[str] = None
    origin: str
    skill_key: str
    enabled: bool
    # Ancestor-chain context (see ancestor_chain) — a custom skill toggled at this
    # scope may actually live at an ancestor tier (an inherited skill); this lets
    # the existence check find it there while the toggle itself still writes at
    # `scope` (a BU toggling an org skill off for itself never touches org's row).
    # project_id is needed alongside workspace_id specifically for a personal
    # (user) scope's ancestor chain, which is two hops deep (project, then
    # workspace, then org) — matches list_skills' identical pair of params.
    workspace_id: Optional[str] = None
    project_id: Optional[str] = None


class ProposeSkillIn(BaseModel):
    agent_id: str
    scope: str
    scope_id: Optional[str] = None


class EvaluateSkillIn(BaseModel):
    agent_id: str
    scope: str
    scope_id: Optional[str] = None


agent_skills_router = APIRouter(
    prefix="/agent-skills",
    dependencies=[Depends(require_permission("artifact:view"))],
)


# ── List / create (no path param) ──────────────────────────────────────────────────────

@agent_skills_router.get("")
async def list_skills(
    request: Request,
    agent_id: str,
    scope: str,
    scope_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    project_id: Optional[str] = None,
):
    tenant_id = _tenant_id(request)
    _validate_agent(agent_id)
    _validate_scope(scope, scope_id)
    assert_own_user_scope(scope, scope_id, _user_id(request))
    ancestor = ancestor_chain(scope, scope_id, workspace_id, project_id)
    skills = await _store().list_skills_merged(tenant_id, agent_id, scope, scope_id, ancestor=ancestor)
    return {"skills": skills}


@agent_skills_router.post("")
async def create_skill(body: CreateSkillIn, request: Request):
    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    perms = getattr(request.state, "permissions", []) or []
    role = await resolve_platform_role_for_user(_user_id(request), tenant_id, perms)
    await assert_can_write_agent_scope(tenant_id, perms, role, body.scope, body.scope_id, _user_id(request), action="draft")
    # resolve_actor_tier_access only knows org/workspace/project (it returns
    # (False, False) for any other scope); assert_can_write_agent_scope above
    # already proved a personal-scope write is the caller's own scope_id — that
    # IS ownership of a personal tier, so it is short-circuited here rather than
    # asking a function that would answer "no" for a scope it doesn't model.
    if body.scope == "user":
        owns = True
    else:
        owns, _ = await resolve_actor_tier_access(tenant_id, _user_id(request), perms, body.scope, body.scope_id)

    violations = validate_skill_key(body.skill_key) + lint_skill_fields(
        body.display_name, body.description, body.when_to_use, body.body
    )
    if violations:
        raise HTTPException(status_code=422, detail={"violations": violations})

    # Collision with a packaged vendor skill_key for this agent, OR an existing live custom
    # skill at this scope -> duplicate_key (checked after lint so the key is known valid).
    store = _store()
    vendor_hit = get_vendor_skill(body.agent_id, body.skill_key) is not None
    custom_hit = await store.get_skill_detail(
        tenant_id, body.agent_id, body.scope, body.scope_id, "custom", body.skill_key
    ) is not None
    if vendor_hit or custom_hit:
        raise HTTPException(status_code=422, detail={"violations": [{
            "field": "skill_key",
            "code": "duplicate_key",
            "message": (
                f"skill_key '{body.skill_key}' already exists as a "
                f"{'vendor' if vendor_hit else 'custom'} skill for this agent."
            ),
        }]})

    try:
        detail = await store.create_custom_skill(
            tenant_id, body.agent_id, body.scope, body.scope_id, body.skill_key,
            body.display_name, body.description, body.when_to_use, body.body,
            _user_id(request) or "system", activate=owns,
        )
    except ValueError:
        # The pre-check above only sees ACTIVE rows (via get_skill_detail); the
        # store's own guard matches any non-deleted row regardless of active
        # state, so a second create against a key that already has an inactive
        # draft (e.g. from an earlier non-owner proposal) reaches here instead —
        # translate to the same duplicate_key shape rather than letting it 500.
        raise HTTPException(status_code=422, detail={"violations": [{
            "field": "skill_key",
            "code": "duplicate_key",
            "message": f"skill_key '{body.skill_key}' already exists for this agent.",
        }]})
    _runtime().invalidate_skills_cache(tenant_id, body.agent_id)
    await _emit(request, tenant_id, "skill.created", body.skill_key, {
        "agent_id": body.agent_id, "scope": body.scope, "scope_id": body.scope_id,
        "skill_key": body.skill_key, "origin": "custom",
    })
    return detail


# ── Literal-suffix routes (declared BEFORE {origin}/{skill_key}) ─────────────────────────

class ImportSourceIn(BaseModel):
    kind: str  # "same_tenant_bu" | "external"
    workspace_id: Optional[str] = None
    url: Optional[str] = None


class ImportSkillIn(BaseModel):
    agent_id: str
    scope: str
    scope_id: Optional[str] = None
    skill_key: str
    display_name: str
    description: Optional[str] = None
    when_to_use: Optional[str] = None
    body: str
    source: ImportSourceIn


@agent_skills_router.post("/import")
async def import_skill(body: ImportSkillIn, request: Request):
    """Bring in Skill content from another BU the caller administers, or a
    declared external source, through three screens before it lands via the
    SAME create_custom_skill path an ordinary create already uses — import is
    a different front door onto the existing write path, not a parallel one
    (sub-project 5 spec §3.1). See create_skill for the sequence this mirrors.
    """
    from shared.authz.read_scope import administered_workspace_ids  # noqa: PLC0415
    from shared.eval.import_screening import scan_for_credentials  # noqa: PLC0415
    from sqlalchemy import select as _select  # noqa: PLC0415
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415
    from shared.models.orm import ImportSourceAllowlist  # noqa: PLC0415

    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    perms = getattr(request.state, "permissions", []) or []
    role = await resolve_platform_role_for_user(_user_id(request), tenant_id, perms)
    await assert_can_write_agent_scope(tenant_id, perms, role, body.scope, body.scope_id, _user_id(request), action="draft")
    if body.scope == "user":
        owns = True
    else:
        owns, _ = await resolve_actor_tier_access(tenant_id, _user_id(request), perms, body.scope, body.scope_id)

    # ── Screen 3: provenance ──────────────────────────────────────────────
    if body.source.kind == "same_tenant_bu":
        if not body.source.workspace_id:
            raise HTTPException(status_code=422, detail={
                "code": "SOURCE_NOT_ALLOWED",
                "message": "A same-BU import must name the source workspace.",
            })
        async with get_db_session_for_tenant(tenant_id) as session:
            administered = await administered_workspace_ids(session, request)
        if administered is not None and body.source.workspace_id not in administered:
            raise HTTPException(status_code=422, detail={
                "code": "SOURCE_NOT_ALLOWED",
                "message": "You do not administer the source business unit.",
            })
    elif body.source.kind == "external":
        url = (body.source.url or "").strip()
        if not url:
            raise HTTPException(status_code=422, detail={
                "code": "SOURCE_NOT_ALLOWED",
                "message": "An external import must declare a source URL.",
            })
        async with get_db_session_for_tenant(tenant_id) as session:
            rows = (await session.execute(
                _select(ImportSourceAllowlist.source_pattern).where(
                    ImportSourceAllowlist.tenant_id == tenant_id,
                )
            )).scalars().all()
        if not any(url.startswith(p) for p in rows):
            raise HTTPException(status_code=422, detail={
                "code": "SOURCE_NOT_ALLOWED",
                "message": "This source is not on the organization's approved import list.",
            })
    else:
        raise HTTPException(status_code=422, detail={
            "code": "SOURCE_NOT_ALLOWED",
            "message": "source.kind must be 'same_tenant_bu' or 'external'.",
        })

    # ── Screen 2: credential leakage ──────────────────────────────────────
    credential_hits = scan_for_credentials(
        "\n".join(filter(None, [body.display_name, body.description, body.when_to_use, body.body]))
    )
    if credential_hits:
        raise HTTPException(status_code=422, detail={
            "code": "CREDENTIAL_DETECTED",
            "message": f"Possible credential detected ({', '.join(credential_hits)}); remove it before importing.",
        })

    # ── Screen 1: prompt-injection (same lint create_skill already runs) ──
    violations = validate_skill_key(body.skill_key) + lint_skill_fields(
        body.display_name, body.description, body.when_to_use, body.body
    )
    if violations:
        raise HTTPException(status_code=422, detail={"violations": violations})

    store = _store()
    vendor_hit = get_vendor_skill(body.agent_id, body.skill_key) is not None
    custom_hit = await store.get_skill_detail(
        tenant_id, body.agent_id, body.scope, body.scope_id, "custom", body.skill_key
    ) is not None
    if vendor_hit or custom_hit:
        raise HTTPException(status_code=422, detail={"violations": [{
            "field": "skill_key", "code": "duplicate_key",
            "message": f"skill_key '{body.skill_key}' already exists for this agent.",
        }]})

    try:
        detail = await store.create_custom_skill(
            tenant_id, body.agent_id, body.scope, body.scope_id, body.skill_key,
            body.display_name, body.description, body.when_to_use, body.body,
            _user_id(request) or "system", activate=owns,
        )
    except ValueError:
        raise HTTPException(status_code=422, detail={"violations": [{
            "field": "skill_key", "code": "duplicate_key",
            "message": f"skill_key '{body.skill_key}' already exists for this agent.",
        }]})
    _runtime().invalidate_skills_cache(tenant_id, body.agent_id)
    await _emit(request, tenant_id, "skill.imported", body.skill_key, {
        "agent_id": body.agent_id, "scope": body.scope, "scope_id": body.scope_id,
        "skill_key": body.skill_key, "origin": "custom", "source_kind": body.source.kind,
    })
    return detail


@agent_skills_router.post("/toggle")
async def toggle_skill(body: ToggleIn, request: Request):
    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    perms = getattr(request.state, "permissions", []) or []
    role = await resolve_platform_role_for_user(_user_id(request), tenant_id, perms)
    # action="publish", not "draft": a toggle takes effect immediately (there is
    # no inactive/pending form of a toggle to propose), so it requires actual
    # ownership, not merely propose-eligibility — matching activate_version below
    # (final whole-branch review, sub-project 3, Critical #3).
    await assert_can_write_agent_scope(tenant_id, perms, role, body.scope, body.scope_id, _user_id(request), action="publish")
    if body.origin not in (Origin.vendor.value, Origin.custom.value):
        raise HTTPException(status_code=422, detail="origin must be one of ('vendor', 'custom')")

    store = _store()
    if body.origin == Origin.vendor.value:
        exists = get_vendor_skill(body.agent_id, body.skill_key) is not None
    else:
        # A custom skill toggled here may be inherited (surfaced by list_skills_merged
        # from an ancestor tier) rather than owned at this exact scope — search the
        # same chain the list already walks before giving up. Existence only; the
        # toggle write below always stays at `body.scope`.
        exists = await store.get_skill_detail(
            tenant_id, body.agent_id, body.scope, body.scope_id, "custom", body.skill_key
        ) is not None
        if not exists:
            for anc_scope, anc_scope_id in ancestor_chain(
                body.scope, body.scope_id, body.workspace_id, body.project_id,
            ):
                exists = await store.get_skill_detail(
                    tenant_id, body.agent_id, anc_scope, anc_scope_id, "custom", body.skill_key
                ) is not None
                if exists:
                    break
    if not exists:
        raise HTTPException(status_code=404, detail="Not found")

    await store.set_skill_enabled(
        tenant_id, body.agent_id, body.scope, body.scope_id, body.origin,
        body.skill_key, body.enabled, _user_id(request) or "system",
    )
    _runtime().invalidate_skills_cache(tenant_id, body.agent_id)
    await _emit(request, tenant_id, "skill.toggled", body.skill_key, {
        "agent_id": body.agent_id, "scope": body.scope, "scope_id": body.scope_id,
        "skill_key": body.skill_key, "origin": body.origin, "enabled": body.enabled,
    })
    return {"origin": body.origin, "skill_key": body.skill_key, "enabled": body.enabled}


@agent_skills_router.post("/{skill_key}/propose", status_code=201)
async def propose_skill(skill_key: str, body: ProposeSkillIn, request: Request):
    """Ask the tier's owner to activate a non-owner's inactive draft, instead of
    activating it yourself. The Skills counterpart to `AgentProfile.propose()` —
    see that function's docstring for why the target must be resolved server-side
    rather than accepted from the request body: `target_ref` is what approving
    activates, so a client that could name it could point a proposal at any skill
    row in the tenant. Skills has no single-row-UUID path param anywhere else in
    this API, so the target is resolved here via `get_latest_draft_version` — the
    newest INACTIVE version of this skill_key at this scope, i.e. exactly the row
    a preceding non-owner create/update just inserted.
    """
    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    if body.scope == "user":
        raise HTTPException(status_code=422, detail={
            "code": "NOT_A_SHARED_TIER",
            "message": "A personal default is yours alone; there is nobody to propose it to.",
        })
    perms = getattr(request.state, "permissions", []) or []
    owns, may_propose = await resolve_actor_tier_access(
        tenant_id, _user_id(request), perms, body.scope, body.scope_id,
    )
    if not (owns or may_propose):
        raise HTTPException(status_code=403, detail="Forbidden")

    draft = await _store().get_latest_draft_version(
        tenant_id, body.agent_id, body.scope, body.scope_id, skill_key,
        created_by=_user_id(request),
    )
    if draft is None:
        raise HTTPException(status_code=404, detail="Nothing to propose")

    from shared.services.eval_gate import latest_passing_evaluation  # noqa: PLC0415

    passing = await latest_passing_evaluation(tenant_id, "skill", draft["id"])
    if passing is None:
        raise HTTPException(status_code=422, detail={
            "code": "EVALUATION_REQUIRED",
            "message": "Run an evaluation before proposing this change.",
        })

    from shared.authz.effective_role import effective_platform_role, actor_display_name  # noqa: PLC0415
    from shared.authz.workspace import active_workspace_for_request  # noqa: PLC0415
    from shared.services import governance_requests as governance_service  # noqa: PLC0415
    from shared.services.governance_requests import GovernanceError  # noqa: PLC0415
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    scope_label = {"org": "organization", "workspace": "business unit", "project": "project"}[body.scope]
    request_type = f"agent_default_{body.scope}"
    async with get_db_session_for_tenant(tenant_id) as db:
        role = await effective_platform_role(db, request)
        name = await actor_display_name(db, request)
        # A project-scope's scope_id is the PROJECT id, not a workspace id — must
        # be resolved through the project row (see _project_workspace_id's
        # docstring; mirrors the identical fix in AgentProfile.propose()).
        if body.scope == "project":
            workspace_id = await _project_workspace_id(tenant_id, str(body.scope_id))
        elif body.scope_id:
            workspace_id = body.scope_id
        else:
            workspace_id = await active_workspace_for_request(request, tenant_id)
        if not workspace_id:
            raise HTTPException(status_code=422, detail={
                "code": "NO_WORKSPACE",
                "message": "Choose a business unit before proposing an organization default.",
            })
        try:
            return await governance_service.create_request(
                db, tenant_id=tenant_id, initiator_id=_user_id(request), initiator_name=name,
                initiator_role=role, request_type=request_type,
                title=f"{body.agent_id} skill '{skill_key}' change ({scope_label})",
                description=f"{name} proposed a change to the '{skill_key}' skill for the {body.agent_id} agent ({scope_label} default), version {draft['version']}.",
                workspace_id=workspace_id, project_id=body.scope_id if body.scope == "project" else None,
                target_ref=draft["id"], payload={
                    "agentId": body.agent_id, "skillKey": skill_key, "scope": body.scope,
                    "version": draft["version"],
                },
                system_raised=True,
            )
        except GovernanceError as exc:
            raise HTTPException(status_code=exc.http_status, detail={"code": exc.code, "message": str(exc)})


@agent_skills_router.post("/{skill_key}/evaluate", status_code=201)
async def evaluate_skill(skill_key: str, body: EvaluateSkillIn, request: Request):
    """Run the deterministic golden-task rubric against the newest pending draft
    of this skill_key at this scope and record the result — a precondition for
    propose_skill() (see the sub-project 4 spec). Unlike propose_skill, this is NOT
    restricted to the caller's own draft: for scope=="org" (R3), the evaluator must
    be someone OTHER than the draft's author, so this must be able to find and
    evaluate ANY pending draft at this scope+key — it calls get_latest_draft_version
    with no `created_by`, i.e. unfiltered by author (see that function's docstring).
    """
    from shared.services.eval_gate import run_evaluation  # noqa: PLC0415
    from shared.authz.effective_role import resolve_platform_role_for_user  # noqa: PLC0415

    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    if body.scope == "user":
        raise HTTPException(status_code=422, detail={
            "code": "NOT_A_SHARED_TIER",
            "message": "A personal skill has nothing to evaluate against.",
        })

    actor_id = _user_id(request)
    perms = getattr(request.state, "permissions", []) or []
    owns, may_propose = await resolve_actor_tier_access(
        tenant_id, actor_id, perms, body.scope, body.scope_id,
    )
    # Router-level artifact:view alone would let ANY tenant member evaluate ANY
    # draft, including one they have no standing on at all — see the identical
    # comment in agent_profiles.py's evaluate() (final whole-branch review,
    # sub-project 4, Important #1).
    if not (owns or may_propose):
        raise HTTPException(status_code=403, detail="Forbidden")

    draft = await _store().get_latest_draft_version(
        tenant_id, body.agent_id, body.scope, body.scope_id, skill_key,
    )
    if draft is None:
        raise HTTPException(status_code=404, detail="Nothing to evaluate")

    if body.scope == "org" and draft.get("created_by") == actor_id:
        raise HTTPException(status_code=403, detail={
            "code": "SELF_EVALUATION_BLOCKED",
            "message": "An organization-wide skill must be evaluated by someone other than its author.",
        })

    detail = await _store().get_skill_detail_by_version(
        tenant_id, body.agent_id, body.scope, body.scope_id, skill_key, draft["version"],
    )
    role = await resolve_platform_role_for_user(actor_id, tenant_id, perms)
    row = await run_evaluation(
        tenant_id=tenant_id, target_type="skill", target_id=draft["id"],
        agent_id=body.agent_id, scope=body.scope, body=(detail or {}).get("body") or "",
        evaluator_id=actor_id, evaluator_role=role,
    )
    return row


@agent_skills_router.get("/{skill_key}/versions")
async def list_versions(
    request: Request,
    skill_key: str,
    agent_id: str,
    scope: str,
    scope_id: Optional[str] = None,
):
    tenant_id = _tenant_id(request)
    _validate_agent(agent_id)
    _validate_scope(scope, scope_id)
    assert_own_user_scope(scope, scope_id, _user_id(request))
    versions = await _store().list_custom_versions(tenant_id, agent_id, scope, scope_id, skill_key)
    return {"versions": versions}


@agent_skills_router.post("/{skill_key}/activate/{version}")
async def activate_version(
    request: Request,
    skill_key: str,
    version: int,
    agent_id: str,
    scope: str,
    scope_id: Optional[str] = None,
):
    tenant_id = _tenant_id(request)
    _validate_agent(agent_id)
    _validate_scope(scope, scope_id)
    perms = getattr(request.state, "permissions", []) or []
    role = await resolve_platform_role_for_user(_user_id(request), tenant_id, perms)
    await assert_can_write_agent_scope(tenant_id, perms, role, scope, scope_id, _user_id(request), action="publish")
    detail = await _store().activate_custom_version(
        tenant_id, agent_id, scope, scope_id, skill_key, version
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Not found")
    _runtime().invalidate_skills_cache(tenant_id, agent_id)
    await _emit(request, tenant_id, "skill.activated", skill_key, {
        "agent_id": agent_id, "scope": scope, "scope_id": scope_id,
        "skill_key": skill_key, "origin": "custom", "version": version,
    })
    return detail


# ── Single-segment authoring routes ──────────────────────────────────────────────────────

@agent_skills_router.put("/{skill_key}")
async def update_skill(skill_key: str, body: UpdateSkillIn, request: Request):
    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    perms = getattr(request.state, "permissions", []) or []
    role = await resolve_platform_role_for_user(_user_id(request), tenant_id, perms)
    await assert_can_write_agent_scope(tenant_id, perms, role, body.scope, body.scope_id, _user_id(request), action="draft")
    # See the matching comment in create_skill: resolve_actor_tier_access does not
    # model the personal scope, and assert_can_write_agent_scope above already
    # proved a personal-scope write is the caller's own — that is ownership.
    if body.scope == "user":
        owns = True
    else:
        owns, _ = await resolve_actor_tier_access(tenant_id, _user_id(request), perms, body.scope, body.scope_id)

    violations = lint_skill_fields(
        body.display_name, body.description, body.when_to_use, body.body
    )
    if violations:
        raise HTTPException(status_code=422, detail={"violations": violations})

    detail = await _store().update_custom_skill(
        tenant_id, body.agent_id, body.scope, body.scope_id, skill_key,
        body.display_name, body.description, body.when_to_use, body.body,
        _user_id(request) or "system", activate=owns,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Not found")
    _runtime().invalidate_skills_cache(tenant_id, body.agent_id)
    await _emit(request, tenant_id, "skill.updated", skill_key, {
        "agent_id": body.agent_id, "scope": body.scope, "scope_id": body.scope_id,
        "skill_key": skill_key, "origin": "custom",
    })
    return detail


@agent_skills_router.delete("/{skill_key}")
async def delete_skill(
    request: Request,
    skill_key: str,
    agent_id: str,
    scope: str,
    scope_id: Optional[str] = None,
):
    tenant_id = _tenant_id(request)
    _validate_agent(agent_id)
    _validate_scope(scope, scope_id)
    perms = getattr(request.state, "permissions", []) or []
    role = await resolve_platform_role_for_user(_user_id(request), tenant_id, perms)
    # action="publish", not "draft": a delete takes effect immediately and has no
    # draft/approval form — requires actual ownership (see the matching comment
    # in toggle_skill; final whole-branch review, sub-project 3, Critical #3).
    await assert_can_write_agent_scope(tenant_id, perms, role, scope, scope_id, _user_id(request), action="publish")
    ok = await _store().soft_delete_custom_skill(tenant_id, agent_id, scope, scope_id, skill_key)
    if not ok:
        raise HTTPException(status_code=404, detail="Not found")
    _runtime().invalidate_skills_cache(tenant_id, agent_id)
    await _emit(request, tenant_id, "skill.deleted", skill_key, {
        "agent_id": agent_id, "scope": scope, "scope_id": scope_id,
        "skill_key": skill_key, "origin": "custom",
    })
    return {"deleted": True}


# ── Two-segment detail route (declared LAST; origin constrained to vendor|custom) ────────

@agent_skills_router.get("/{origin}/{skill_key}")
async def get_skill(
    request: Request,
    agent_id: str,
    scope: str,
    origin: Origin = Path(...),
    skill_key: str = Path(...),
    scope_id: Optional[str] = None,
):
    tenant_id = _tenant_id(request)
    _validate_agent(agent_id)
    _validate_scope(scope, scope_id)
    assert_own_user_scope(scope, scope_id, _user_id(request))
    detail = await _store().get_skill_detail(
        tenant_id, agent_id, scope, scope_id, origin.value, skill_key
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Not found")
    return detail
