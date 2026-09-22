"""Agent Studio tech stacks — Business Unit alternatives and each project's choice.

WHO MAY DO WHAT is Agent Studio's own tier rule, `resolve_actor_tier_access(...).owns`: a
Business Unit's stacks and its default belong to that BU's admin; a project's selection and
its own stacks belong to that project's admin. An organisation admin's `admin:*` owns every
tier, as everywhere in Agent Studio. Reading is open to members, like every shared Agent
Studio tier; a project's view also requires seeing the project.

Every write invalidates the resolver's cache for the tenant, so the next agent turn follows
the change, and is audited. Rules live in shared/services/tech_stack.py; storage in
shared/services/tech_stack_store.py.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from shared.audit.models import AuditEventPayload
from shared.audit.service import audit_service
from shared.authz.dependency import require_permission
from shared.routers.agent_profiles import resolve_actor_tier_access
from shared.services import tech_stack_store as store
from shared.services.tech_stack import EffectiveTechStack, TechStack, validate_stack
from shared.services.tech_stack_catalog import catalog

logger = logging.getLogger(__name__)

tech_stacks_router = APIRouter(dependencies=[Depends(require_permission("artifact:view"))])

_OWNER_WORDS = {
    "workspace": "a Business Unit admin of this Business Unit",
    "project": "a project admin of this project",
}


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return str(tid)


def _user_id(request: Request) -> str:
    return str(getattr(request.state, "user_id", "") or "")


def _perms(request: Request) -> list:
    return list(getattr(request.state, "permissions", []) or [])


def _out(s: TechStack) -> dict:
    return {"id": s.id, "scope": s.scope, "workspace_id": s.workspace_id, "project_id": s.project_id,
            "name": s.name, "description": s.description, "categories": s.categories, "notes": s.notes,
            "is_default": s.is_default}


def _effective_out(eff: EffectiveTechStack) -> dict:
    return {"stack": _out(eff.stack) if eff.stack else None, "source": eff.source, "warning": eff.warning}


def _violations(violations: list) -> HTTPException:
    return HTTPException(status_code=422, detail={"violations": violations})


async def _owns(request: Request, scope: str, scope_id) -> bool:
    owns, _ = await resolve_actor_tier_access(_tenant_id(request), _user_id(request), _perms(request),
                                              scope, str(scope_id))
    return bool(owns)


async def _assert_owner(request: Request, scope: str, scope_id) -> None:
    if not await _owns(request, scope, scope_id):
        raise HTTPException(status_code=403, detail=f"Only {_OWNER_WORDS[scope]} can change its tech stacks.")


async def _assert_project_visible(request: Request, project_id) -> None:
    """404, like the sibling guards: a project the caller cannot reach is not confirmed to exist."""
    from shared.authz.can_perform import visible_project_ids  # noqa: PLC0415
    from shared.authz.read_scope import is_org_wide  # noqa: PLC0415
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    if is_org_wide(request):
        return
    async with get_db_session_for_tenant(_tenant_id(request)) as db:
        visible = await visible_project_ids(db, user_id=_user_id(request), tenant_id=_tenant_id(request))
    if visible is not None and str(project_id) not in visible:
        raise HTTPException(status_code=404, detail="not found")


async def _emit(request: Request, event_type: str, resource_id: str, payload: dict) -> None:
    """Fire-and-forget audit emit (audit_service.emit schedules and never raises)."""
    await audit_service.emit(AuditEventPayload(
        tenant_id=_tenant_id(request), event_type=event_type, actor_id=_user_id(request) or None,
        resource_type="tech_stack", resource_id=str(resource_id), payload=payload,
    ))


def _changed(request: Request) -> None:
    store.invalidate_tech_stack_cache(_tenant_id(request))


class TechStackFields(BaseModel):
    # Generous transport limits; the real ones are validate_stack's, with named reasons.
    name: str = Field(default="", max_length=400)
    description: Optional[str] = Field(default="", max_length=4000)
    categories: dict[str, list[str]] = Field(default_factory=dict)
    notes: Optional[str] = Field(default="", max_length=20000)


class CreateTechStackIn(TechStackFields):
    scope: str
    scope_id: str


class DefaultIn(BaseModel):
    is_default: bool


class SelectionIn(BaseModel):
    tech_stack_id: Optional[str] = None


# ── reads ───────────────────────────────────────────────────────────────────────

@tech_stacks_router.get("/tech-stacks/catalog")
async def get_tech_stack_catalog():
    return catalog()


@tech_stacks_router.get("/tech-stacks")
async def list_business_unit_tech_stacks(request: Request, workspace_id: str):
    stacks = await store.list_workspace_stacks(_tenant_id(request), workspace_id)
    return {"items": [_out(s) for s in stacks], "can_manage": await _owns(request, "workspace", workspace_id)}


@tech_stacks_router.get("/projects/{project_id}/tech-stack")
async def get_project_tech_stack(project_id: str, request: Request):
    tenant = _tenant_id(request)
    exists, ws = await store.project_scope(tenant, project_id)
    if not exists:
        raise HTTPException(status_code=404, detail="not found")
    await _assert_project_visible(request, project_id)
    business_unit = await store.list_workspace_stacks(tenant, ws) if ws else []
    own = await store.list_project_stacks(tenant, project_id)
    selection = await store.get_selection(tenant, project_id)
    # Uncached: the admin looking at this sees the truth now, not what a turn cached.
    effective = await store.resolve_project_tech_stack(tenant, project_id)
    return {
        "project_id": str(project_id),
        "workspace_id": ws,
        "options": {"business_unit": [_out(s) for s in business_unit], "project": [_out(s) for s in own]},
        "selection": {"tech_stack_id": selection},
        "effective": _effective_out(effective),
        "can_manage": await _owns(request, "project", project_id),
        "can_manage_business_unit": bool(ws) and await _owns(request, "workspace", ws),
    }


# ── writes ──────────────────────────────────────────────────────────────────────

@tech_stacks_router.post("/tech-stacks", status_code=201)
async def create_tech_stack(body: CreateTechStackIn, request: Request):
    tenant = _tenant_id(request)
    if body.scope not in ("workspace", "project"):
        raise _violations([{"field": "scope", "code": "unknown_scope",
                            "message": "A tech stack belongs to a Business Unit or a project."}])
    if body.scope == "workspace":
        if not await store.workspace_exists(tenant, body.scope_id):
            raise HTTPException(status_code=404, detail="Business Unit not found")
    else:
        exists, _ = await store.project_scope(tenant, body.scope_id)
        if not exists:
            raise HTTPException(status_code=404, detail="Project not found")
        await _assert_project_visible(request, body.scope_id)
    await _assert_owner(request, body.scope, body.scope_id)
    fields, violations = validate_stack(body.name, body.description, body.categories, body.notes)
    if violations:
        raise _violations(violations)
    try:
        stack = await store.create_stack(tenant, scope=body.scope, scope_id=body.scope_id, fields=fields,
                                         actor=_user_id(request) or "system")
    except store.DuplicateStackName:
        raise _violations([{"field": "name", "code": "duplicate_name",
                            "message": f"A tech stack named '{fields['name']}' already exists here."}])
    _changed(request)
    await _emit(request, "tech_stack.created", stack.id,
                {"scope": stack.scope, "scope_id": body.scope_id, "name": stack.name})
    return _out(stack)


async def _existing(request: Request, stack_id: str) -> TechStack:
    stack = await store.get_stack(_tenant_id(request), stack_id)
    if stack is None:
        raise HTTPException(status_code=404, detail="Tech stack not found")
    return stack


def _tier_id(stack: TechStack) -> Optional[str]:
    return stack.workspace_id if stack.scope == "workspace" else stack.project_id


@tech_stacks_router.patch("/tech-stacks/{stack_id}")
async def update_tech_stack(stack_id: str, body: TechStackFields, request: Request):
    stack = await _existing(request, stack_id)
    await _assert_owner(request, stack.scope, _tier_id(stack))
    fields, violations = validate_stack(body.name, body.description, body.categories, body.notes)
    if violations:
        raise _violations(violations)
    try:
        updated = await store.update_stack(_tenant_id(request), stack_id, fields, _user_id(request) or "system")
    except store.DuplicateStackName:
        raise _violations([{"field": "name", "code": "duplicate_name",
                            "message": f"A tech stack named '{fields['name']}' already exists here."}])
    if updated is None:
        raise HTTPException(status_code=404, detail="Tech stack not found")
    _changed(request)
    await _emit(request, "tech_stack.updated", stack_id, {"name": updated.name})
    return _out(updated)


@tech_stacks_router.delete("/tech-stacks/{stack_id}")
async def delete_tech_stack(stack_id: str, request: Request):
    stack = await _existing(request, stack_id)
    await _assert_owner(request, stack.scope, _tier_id(stack))
    await store.delete_stack(_tenant_id(request), stack_id, _user_id(request) or "system")
    _changed(request)
    await _emit(request, "tech_stack.deleted", stack_id, {"name": stack.name, "was_default": stack.is_default})
    return {"deleted": True, "id": stack_id}


@tech_stacks_router.put("/tech-stacks/{stack_id}/default")
async def set_tech_stack_default(stack_id: str, body: DefaultIn, request: Request):
    stack = await _existing(request, stack_id)
    if stack.scope != "workspace":
        raise _violations([{"field": "is_default", "code": "not_business_unit",
                            "message": "Only a Business Unit's tech stacks can be its default."}])
    await _assert_owner(request, "workspace", stack.workspace_id)
    updated = await store.set_default(_tenant_id(request), stack_id, body.is_default,
                                      _user_id(request) or "system")
    if updated is None:
        raise HTTPException(status_code=404, detail="Tech stack not found")
    _changed(request)
    await _emit(request, "tech_stack.default_set", stack_id, {"is_default": body.is_default, "name": stack.name})
    return _out(updated)


@tech_stacks_router.put("/projects/{project_id}/tech-stack")
async def select_project_tech_stack(project_id: str, body: SelectionIn, request: Request):
    tenant = _tenant_id(request)
    exists, ws = await store.project_scope(tenant, project_id)
    if not exists:
        raise HTTPException(status_code=404, detail="not found")
    await _assert_project_visible(request, project_id)
    await _assert_owner(request, "project", project_id)
    if body.tech_stack_id:
        stack = await store.get_stack(tenant, body.tech_stack_id)
        if stack is None:
            raise HTTPException(status_code=404, detail="That tech stack no longer exists.")
        allowed = stack.project_id == str(project_id) or (stack.scope == "workspace" and stack.workspace_id == ws)
        if not allowed:
            raise HTTPException(status_code=409,
                                detail="That tech stack belongs to another Business Unit or project.")
    await store.set_selection(tenant, project_id, body.tech_stack_id, _user_id(request) or "system")
    _changed(request)
    await _emit(request, "project.tech_stack_selected", project_id, {"tech_stack_id": body.tech_stack_id})
    return await get_project_tech_stack(project_id, request)
