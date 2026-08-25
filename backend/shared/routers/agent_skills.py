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

RBAC (mirrors agent_profiles, extended by sub-project 2): reads gate on the
"artifact:view" floor (router-level) PLUS, at the personal ("user") scope only,
`assert_own_user_scope` (imported from agent_profiles) on `list`/detail — without it,
`scope=user&scope_id=<anyone>` would let any authenticated caller read another user's
personal skill catalog. create/update/toggle/delete and activate all use the in-body,
scope-aware `assert_can_write_agent_scope` check (also imported from agent_profiles)
instead of a route-level Depends(): for org/workspace/project scope it requires
"skill:edit" (create/update/toggle/delete) or "workspace:manage" (activate) exactly as
before; for the personal scope, any role except org_admin/bu_admin may write ONLY their
own scope_id. Every route still carries a require_permission sentinel (the router-level
floor) so the process_api D-05 boot scan stays green.

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
    ancestor_chain,
    assert_can_write_agent_scope,
    assert_own_user_scope,
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
    assert_can_write_agent_scope(perms, role, body.scope, body.scope_id, _user_id(request), action="draft")

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

    detail = await store.create_custom_skill(
        tenant_id, body.agent_id, body.scope, body.scope_id, body.skill_key,
        body.display_name, body.description, body.when_to_use, body.body,
        _user_id(request) or "system",
    )
    _runtime().invalidate_skills_cache(tenant_id, body.agent_id)
    await _emit(request, tenant_id, "skill.created", body.skill_key, {
        "agent_id": body.agent_id, "scope": body.scope, "scope_id": body.scope_id,
        "skill_key": body.skill_key, "origin": "custom",
    })
    return detail


# ── Literal-suffix routes (declared BEFORE {origin}/{skill_key}) ─────────────────────────

@agent_skills_router.post("/toggle")
async def toggle_skill(body: ToggleIn, request: Request):
    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    perms = getattr(request.state, "permissions", []) or []
    role = await resolve_platform_role_for_user(_user_id(request), tenant_id, perms)
    assert_can_write_agent_scope(perms, role, body.scope, body.scope_id, _user_id(request), action="draft")
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
    assert_can_write_agent_scope(perms, role, scope, scope_id, _user_id(request), action="publish")
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
    assert_can_write_agent_scope(perms, role, body.scope, body.scope_id, _user_id(request), action="draft")

    violations = lint_skill_fields(
        body.display_name, body.description, body.when_to_use, body.body
    )
    if violations:
        raise HTTPException(status_code=422, detail={"violations": violations})

    detail = await _store().update_custom_skill(
        tenant_id, body.agent_id, body.scope, body.scope_id, skill_key,
        body.display_name, body.description, body.when_to_use, body.body,
        _user_id(request) or "system",
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
    assert_can_write_agent_scope(perms, role, scope, scope_id, _user_id(request), action="draft")
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
