"""Model Provider (BYOK) API. Admin config endpoints are model:manage-gated;
the run-creator options endpoint is run:create-gated. Keys are never returned."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.can_perform import can_perform
from shared.authz.connector_grants import granted_target_refs
from shared.authz.dependency import require_any_permission, require_permission
from shared.authz.read_scope import is_org_wide
from shared.authz.workspace import active_workspace_for_request
from shared.db import get_db_session
from shared.services import model_config as mc
from shared.services import model_grants as mg
from shared.services.model_catalog import list_providers as catalog_providers

model_router = APIRouter(
    prefix="/model", dependencies=[Depends(require_permission("model:manage"))]
)
model_options_router = APIRouter(
    prefix="/model", dependencies=[Depends(require_permission("run:create"))]
)
# GET /availability has two legitimate consumer groups gated by different permissions:
# a Business Unit Admin's own governance view (model:manage) and the run-time model
# picker / create-project dialog (run:create). Neither single-permission router fits —
# see require_any_permission's docstring.
model_availability_router = APIRouter(
    prefix="/model", dependencies=[Depends(require_any_permission("model:manage", "run:create"))]
)


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


def _user_id(request: Request) -> str:
    return getattr(request.state, "user_id", "") or ""


async def _require_scoped(
    db: AsyncSession, request: Request, *, permission: str, resource_kind: str, resource_id: str,
    deny_status: int = 403,
) -> None:
    """Resource-scoped check layered on top of the router's flat permission floor.

    Closes design-doc gap #1 (docs/superpowers/specs/2026-08-11-model-gateway-bu-cascade-design.md
    §8.1): the router-level require_permission only asks "does this caller hold the
    permission ANYWHERE in the tenant" — every BU/Project Admin does. This asks the
    resource-aware question via can_perform (now that scoped RBAC exists), so a BU
    Admin can no longer edit a business unit or project that isn't theirs. An
    Organization Admin's organization-scope role binding still passes, since
    can_perform treats an ancestor scope as reaching every resource beneath it.

    deny_status defaults to 403 (BU governance routes — workspace ids aren't treated
    as sensitive elsewhere in this router). Project routes pass 404 instead, matching
    shared/routers/projects.py:get_project's own precedent: a project the caller may
    not act on must not be confirmed to exist via a differently-coded response.
    """
    if not await can_perform(
        db, user_id=_user_id(request), permission=permission,
        tenant_id=_tenant_id(request), resource_kind=resource_kind, resource_id=resource_id,
    ):
        raise HTTPException(status_code=deny_status, detail="Forbidden" if deny_status == 403 else "Not found")


async def _active_ws(request: Request) -> str | None:
    """The active workspace for model scoping; None (org-wide) if unresolved."""
    try:
        return str(await active_workspace_for_request(request, _tenant_id(request)))
    except Exception:
        return None


def _to_camel(d: dict, *keys: tuple[str, str]) -> dict:
    """Return a copy of `d` with each `snake` key renamed to its `camel` counterpart.

    model_grants.py's service functions return plain dicts in snake_case (kept that way
    deliberately — see the module docstring there); frontend/lib/schemas/model.ts's Zod
    schemas expect camelCase for a specific subset of fields (credentialId,
    credentialName, businessUnitIds, ...). Since those routes have no Pydantic
    response_model translating keys, the rename has to happen explicitly at the route.
    """
    out = dict(d)
    for snake, camel in keys:
        if snake in out:
            out[camel] = out.pop(snake)
    return out


_CRED_KEYS = (("credential_id", "credentialId"), ("credential_name", "credentialName"))
_BU_ALLOWED_KEYS = _CRED_KEYS + (("allow_project_key", "allowProjectKey"),)


# ---- schemas (no secret fields) ----
class OfferingOut(BaseModel):
    id: str
    provider_id: str
    model_id: str
    enabled: bool
    is_default: bool
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    # Per-model usage limits (NULL = no limit).
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    cost_limit_usd: float | None = None


class ProviderOut(BaseModel):
    id: str
    provider: str
    display_name: str
    status: str
    api_base: str | None = None
    is_custom: bool = False
    last_verified_at: str | None
    created_at: str | None
    offerings: list[OfferingOut]
    # null = onboarded org-wide; set = scoped to that Business Unit (see
    # frontend/lib/schemas/model.ts's ModelProvider — camelCase names match it directly
    # since this is an actual Pydantic response_model, not a bare dict route).
    workspaceId: str | None = None
    # set = this exact project's own key (PRD §371/§1640) — distinct from workspaceId
    # scoping, which is the shared BU-level connection.
    projectId: str | None = None
    # Org-level guardrail (PRD §376/§563: "guardrails such as max cost per call").
    # Distinct from OfferingOut.cost_limit_usd, which is the existing monthly budget.
    max_cost_per_call_usd: float | None = None
    # Synthetic: whether a secret is actually stored (secret_ref is not None), not a
    # real column. A provider can be onboarded keyless (spec §2.3) — a BU/project
    # supplies its own key later.
    hasKey: bool = True
    approvalStatus: str = "active"
    approvalDecidedBy: str | None = None
    approvalDecidedAt: str | None = None
    approvalReason: str | None = None


def _to_provider_out(d: dict) -> "ProviderOut":
    # strip secret_ref; it never crosses the API boundary
    return ProviderOut(
        id=d["id"], provider=d["provider"], display_name=d["display_name"],
        status=d["status"], api_base=d.get("api_base"), is_custom=d.get("is_custom", False),
        last_verified_at=d["last_verified_at"], created_at=d["created_at"],
        offerings=[OfferingOut(**o) for o in d["offerings"]],
        workspaceId=d.get("workspace_id"),
        projectId=d.get("project_id"),
        max_cost_per_call_usd=d.get("max_cost_per_call_usd"),
        hasKey=d.get("has_key", True),
        approvalStatus=d.get("approval_status") or "active",
        approvalDecidedBy=d.get("approval_decided_by"),
        approvalDecidedAt=d.get("approval_decided_at"),
        approvalReason=d.get("approval_reason"),
    )


class ModelIn(BaseModel):
    model_id: str = Field(min_length=1, max_length=100)
    # USD per 1M tokens — required for custom providers (validated in the service).
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)
    # Per-model usage limits (optional; NULL = no limit).
    rpm_limit: int | None = Field(default=None, ge=0)
    tpm_limit: int | None = Field(default=None, ge=0)
    cost_limit_usd: float | None = Field(default=None, ge=0)


class CreateProviderIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # Any LiteLLM provider slug — onboarding is dynamic, gated by model:manage RBAC.
    provider: str = Field(min_length=1, max_length=64, pattern="^[a-z0-9][a-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=512)
    # Optional custom endpoint (OpenAI-compatible / self-hosted / gateway).
    api_base: str | None = Field(default=None, max_length=512)
    # Preferred: full model specs with pricing. Back-compat: bare model ids.
    models: list[ModelIn] = Field(default_factory=list)
    enabled_models: list[str] = Field(default_factory=list)
    # frontend/lib/api/models.ts's addModelProvider sends workspaceId/businessUnitIds
    # (camelCase); accept those via alias while keeping the internal snake_case name so
    # .model_dump() still yields what create_provider()/set_org_grants() expect.
    workspace_id: str | None = Field(default=None, alias="workspaceId")
    visibility: str | None = None
    business_unit_ids: list[str] = Field(default_factory=list, alias="businessUnitIds")
    max_cost_per_call_usd: float | None = Field(default=None, ge=0, alias="maxCostPerCallUsd")


class ProbeProviderIn(BaseModel):
    """Same field names as `CreateProviderIn`'s credential fields — deliberately, so a
    frontend caller can send the same shape it already builds for onboarding without a
    separate mapping for this pre-save check."""

    provider: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=1, max_length=512)
    api_base: str | None = Field(default=None, max_length=512)
    # The model to probe with — normally whichever the caller picked first.
    model: str | None = Field(default=None, max_length=200)


class UpdateProviderIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    display_name: str | None = Field(default=None, max_length=255)
    enabled_models: list[str] | None = None
    max_cost_per_call_usd: float | None = Field(default=None, ge=0, alias="maxCostPerCallUsd")
    clear_max_cost_per_call_usd: bool = Field(default=False, alias="clearMaxCostPerCallUsd")


class SetDefaultIn(BaseModel):
    offering_id: str


class GrantEntryIn(BaseModel):
    # frontend/lib/schemas/model.ts's OrgModelGrant sends credentialId/businessUnitIds
    # (camelCase); accept via alias, keep the internal name snake_case so
    # .model_dump(by_alias=False) still gives model_grants.py the keys it expects.
    model_config = ConfigDict(populate_by_name=True)

    provider: str
    model_id: str
    credential_id: str | None = Field(default=None, alias="credentialId")
    visibility: str = "global"
    business_unit_ids: list[str] = Field(default_factory=list, alias="businessUnitIds")


class AllowEntryIn(BaseModel):
    # frontend/lib/schemas/model.ts's ModelAllowEntry sends credentialId (camelCase).
    model_config = ConfigDict(populate_by_name=True)

    provider: str
    model_id: str
    credential_id: str | None = Field(default=None, alias="credentialId")
    # BU-allowed entries only (ignored on project-selection entries): does this BU let
    # its projects bring their own key for this model? PRD §371/§1640 — off by default.
    allow_project_key: bool = Field(default=False, alias="allowProjectKey")


class SetOrgGrantsIn(BaseModel):
    entries: list[GrantEntryIn] = Field(default_factory=list)


class SetBuGrantsIn(BaseModel):
    entries: list[AllowEntryIn] = Field(default_factory=list)


class CreateProjectProviderIn(BaseModel):
    """A Project Admin bringing their own key (PRD §371/§1640) — a full connection
    like CreateProviderIn, but always scoped to exactly one project and never
    carrying visibility/business_unit_ids (a project key reaches only that project)."""
    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(alias="projectId")
    provider: str = Field(min_length=1, max_length=64, pattern="^[a-z0-9][a-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=255, alias="displayName")
    api_key: str | None = Field(default=None, max_length=512, alias="apiKey")
    api_base: str | None = Field(default=None, max_length=512, alias="apiBase")
    models: list[ModelIn] = Field(default_factory=list)
    enabled_models: list[str] = Field(default_factory=list, alias="enabledModels")


class SetProjectSelectionIn(BaseModel):
    # frontend/lib/api/models.ts's setProjectModelSelection sends defaultKey (camelCase).
    model_config = ConfigDict(populate_by_name=True)

    selected: list[AllowEntryIn] = Field(default_factory=list)
    default_key: str | None = Field(default=None, alias="defaultKey")


@model_router.get("/catalog")
async def get_catalog() -> list[dict]:
    return catalog_providers()


@model_router.get("/providers", response_model=list[ProviderOut])
async def list_providers_route(
    request: Request, scope: str | None = None, workspaceId: str | None = None,
    db: AsyncSession = Depends(get_db_session),
) -> list[ProviderOut]:
    ws = workspaceId or await _active_ws(request)
    rows = await mc.list_providers(_tenant_id(request), workspace_id=ws, scope=scope)
    if ws and scope != "all":
        # mc.list_providers only filters by OWNERSHIP (org-wide-or-mine) — never by
        # whether this business unit was actually granted the provider. An org-wide
        # ("centrally keyed") connection used to show up for every unit unconditionally,
        # which is exactly backwards from "the Org Admin decides which providers a unit
        # may reach at all" (PUT /model/providers/grants). Org Admin's own scope="all"
        # view stays unfiltered — they define the grants, they aren't subject to them.
        granted = await granted_target_refs(db, tenant_id=_tenant_id(request), workspace_id=ws, kind="model_provider")
        rows = [d for d in rows if d["provider"] in granted]
    return [_to_provider_out(d) for d in rows]


@model_router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider_route(
    request: Request, body: CreateProviderIn, db: AsyncSession = Depends(get_db_session),
) -> ProviderOut:
    if body.workspace_id is not None:
        # Ownership first, same order as every other resource-scoped route in this
        # file (see get_bu_allowed_route above): a caller who doesn't administer this
        # business unit at all should not learn anything further about it, including
        # whether it holds a grant.
        await _require_scoped(
            db, request, permission="model:manage", resource_kind="business_unit", resource_id=body.workspace_id,
        )
        if not (body.api_key or "").strip():
            raise HTTPException(
                status_code=422,
                detail="api_key is required when adding a key to a business unit's provider.",
            )
        granted = await granted_target_refs(
            db, tenant_id=_tenant_id(request), workspace_id=body.workspace_id, kind="model_provider",
        )
        if body.provider not in granted:
            raise HTTPException(
                status_code=403,
                detail=f"Your organization has not granted this business unit access to {body.provider!r}.",
            )

    # Accept either rich `models` (with pricing) or back-compat bare `enabled_models`.
    models: list[dict] = [m.model_dump() for m in body.models]
    if not models and body.enabled_models:
        models = [{"model_id": m} for m in body.enabled_models]
    try:
        d = await mc.create_provider(
            _tenant_id(request), provider=body.provider, display_name=body.display_name,
            api_key=body.api_key, models=models, api_base=body.api_base,
            created_by=_user_id(request), workspace_id=body.workspace_id,
            max_cost_per_call_usd=body.max_cost_per_call_usd,
        )
    except mc.DuplicateProviderNameError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except mc.InvalidModelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if body.workspace_id is None and (body.visibility or body.business_unit_ids):
        # Org-wide onboarding writes the matching grant in the same act (spec §2.3) — a
        # key can't land without anyone being able to use what it unlocks.
        entries = [
            {"provider": body.provider, "model_id": m["model_id"], "credential_id": d["id"],
             "visibility": body.visibility or "global", "business_unit_ids": body.business_unit_ids or []}
            for m in models
        ]
        existing = await mg.get_org_grants(_tenant_id(request))
        await mg.set_org_grants(_tenant_id(request), existing + entries, created_by=_user_id(request))
    return _to_provider_out(d)


@model_router.post("/providers/{provider_id}/assign")
async def assign_provider_to_project_route(
    request: Request, provider_id: str, body: dict, db: AsyncSession = Depends(get_db_session),
) -> dict:
    """A BU Admin pushes a provider connection they already created (Task 4's flow) onto
    one of their own projects — populates that project's ProjectModelSelection.selected
    (mg.assign_provider_to_project) so the project's own admin can later pick it as their
    default/master key (Task 12)."""
    project_id = body.get("projectId")
    if not project_id:
        raise HTTPException(status_code=422, detail="projectId is required")
    # Ownership first, same order/precedent as every other resource-scoped route in this
    # file: a caller who doesn't administer this project's business unit at all must not
    # be told anything further, including whether the provider exists. 404 (not 403)
    # matches shared/routers/projects.py:get_project's precedent for project routes.
    await _require_scoped(
        db, request, permission="model:manage", resource_kind="project", resource_id=project_id,
        deny_status=404,
    )
    try:
        selection = await mg.assign_provider_to_project(
            _tenant_id(request), provider_id=provider_id, project_id=project_id,
            actor_id=_user_id(request),
        )
    except mc.ProviderNotFoundError:
        raise HTTPException(status_code=404, detail="Provider not found")
    except mg.ProjectOutsideUnitError:
        raise HTTPException(status_code=403, detail="That project is not in your business unit.")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _camel_selection(selection)


@model_router.post("/providers/probe")
async def probe_provider_route(body: ProbeProviderIn) -> dict:
    """Stateless pre-save credential check — the BU Admin's "Test" button (spec §5,
    Task 10). Unlike POST /providers + POST /providers/{id}/verify, nothing is created,
    read or written for any tenant or business unit here, so no `_require_scoped` call
    is needed beyond the router's flat `model:manage` floor: there is no resource yet
    to scope the check against."""
    try:
        return await mc.probe_provider(body.provider, body.api_key, body.api_base, body.model)
    except mc.InvalidModelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@model_router.post("/providers/{provider_id}/verify")
async def verify_provider_route(request: Request, provider_id: str) -> dict:
    try:
        return await mc.verify_provider(_tenant_id(request), provider_id)
    except mc.ProviderNotFoundError:
        raise HTTPException(status_code=404, detail="Provider not found")
    except mc.InvalidModelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@model_router.patch("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider_route(request: Request, provider_id: str, body: UpdateProviderIn) -> ProviderOut:
    _cost_kwargs = {}
    if body.clear_max_cost_per_call_usd:
        _cost_kwargs["max_cost_per_call_usd"] = None
    elif body.max_cost_per_call_usd is not None:
        _cost_kwargs["max_cost_per_call_usd"] = body.max_cost_per_call_usd
    try:
        d = await mc.update_provider(
            _tenant_id(request), provider_id,
            display_name=body.display_name, enabled_models=body.enabled_models,
            **_cost_kwargs,
        )
    except mc.ProviderNotFoundError:
        raise HTTPException(status_code=404, detail="Provider not found")
    except mc.DuplicateProviderNameError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except mc.InvalidModelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _to_provider_out(d)


@model_router.delete("/providers/{provider_id}", status_code=204, response_model=None)
async def delete_provider_route(request: Request, provider_id: str) -> None:
    try:
        await mc.delete_provider(_tenant_id(request), provider_id, workspace_id=await _active_ws(request))
    except mc.ProviderNotFoundError:
        raise HTTPException(status_code=404, detail="Provider not found")


@model_router.put("/default", status_code=204, response_model=None)
async def set_default_route(request: Request, body: SetDefaultIn) -> None:
    try:
        await mc.set_default(_tenant_id(request), body.offering_id)
    except mc.OfferingNotFoundError:
        raise HTTPException(status_code=404, detail="Offering not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@model_options_router.get("/options")
async def get_options_route(request: Request, projectId: str | None = None) -> dict:
    """The model picker's list, scoped to the PROJECT and nothing else.

    `projectId` is what decides the answer. Once an organization holds any
    org_model_grants row, `effective_project_offerings` fails CLOSED without a project
    context — an empty list, on purpose, rather than silently offering everything — so
    a caller that omits it renders "Connect a model provider" on a project whose unit
    has models granted.

    No active-workspace selector is consulted. It was, and the value went unused by
    `get_options`; worse, the selector names the org's oldest unit whenever the
    X-Workspace-Id cookie is absent (it usually is), which is not the unit the project
    belongs to.
    """
    return await mc.get_options(_tenant_id(request), project_id=projectId)


@model_router.get("/allowed/org")
async def get_org_grants_route(request: Request) -> list[dict]:
    entries = await mg.get_org_grants(_tenant_id(request))
    return [_to_camel(e, *_CRED_KEYS, ("business_unit_ids", "businessUnitIds")) for e in entries]


@model_router.put("/allowed/org")
async def set_org_grants_route(request: Request, body: SetOrgGrantsIn) -> list[dict]:
    try:
        entries = await mg.set_org_grants(
            _tenant_id(request), [e.model_dump() for e in body.entries], created_by=_user_id(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [_to_camel(e, *_CRED_KEYS, ("business_unit_ids", "businessUnitIds")) for e in entries]


@model_router.get("/allowed/bu")
async def get_bu_allowed_route(
    request: Request, workspaceId: str, db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    await _require_scoped(
        db, request, permission="model:manage", resource_kind="business_unit", resource_id=workspaceId,
    )
    entries = await mg.get_bu_allowed(_tenant_id(request), workspaceId)
    return [_to_camel(e, *_BU_ALLOWED_KEYS) for e in entries]


@model_router.put("/allowed/bu")
async def set_bu_grants_route(
    request: Request, workspaceId: str, body: SetBuGrantsIn, db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    await _require_scoped(
        db, request, permission="model:manage", resource_kind="business_unit", resource_id=workspaceId,
    )
    try:
        entries = await mg.set_bu_grants(
            _tenant_id(request), workspaceId, [e.model_dump() for e in body.entries],
            updated_by=_user_id(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [_to_camel(e, *_BU_ALLOWED_KEYS) for e in entries]


@model_availability_router.get("/availability")
async def get_availability_route(request: Request, workspaceId: str) -> list[dict]:
    entries = await mg.get_availability(_tenant_id(request), workspaceId)
    return [_to_camel(e, *_CRED_KEYS) for e in entries]


@model_router.get("/grant-matrix")
async def get_grant_matrix_route(request: Request) -> dict:
    matrix = await mg.get_grant_matrix(_tenant_id(request))
    matrix["rows"] = [_to_camel(r, *_CRED_KEYS) for r in matrix["rows"]]
    return matrix


def _camel_selection(sel: dict) -> dict:
    sel = dict(sel)
    sel["inherited"] = [_to_camel(e, *_CRED_KEYS) for e in sel.get("inherited", [])]
    sel["selected"] = [_to_camel(e, *_CRED_KEYS) for e in sel.get("selected", [])]
    return sel


@model_options_router.get("/allowed/project")
async def get_project_selection_route(
    request: Request, projectId: str, db: AsyncSession = Depends(get_db_session),
) -> dict:
    # run:create, not model:manage — this router's floor permission (see design doc §4).
    await _require_scoped(
        db, request, permission="run:create", resource_kind="project", resource_id=projectId,
        deny_status=404,
    )
    try:
        selection = await mg.get_project_selection(_tenant_id(request), projectId)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _camel_selection(selection)


@model_options_router.put("/allowed/project")
async def set_project_selection_route(
    request: Request, projectId: str, body: SetProjectSelectionIn, db: AsyncSession = Depends(get_db_session),
) -> dict:
    await _require_scoped(
        db, request, permission="run:create", resource_kind="project", resource_id=projectId,
        deny_status=404,
    )
    try:
        selection = await mg.set_project_selection(
            _tenant_id(request), projectId,
            [e.model_dump() for e in body.selected], body.default_key,
        )
    except mg.NotAllowedForUnitError as exc:
        raise HTTPException(status_code=400, detail={"code": "not_allowed_for_unit", "message": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _camel_selection(selection)


# ---------------------------------------------------------------------------
# Project-level BYOK (PRD §371/§1640/§1692/§1698): a Project Admin's own key,
# reachable only when their Business Unit has explicitly opted a (provider,
# model_id) into project-level keys via allow_project_key. run:create-gated,
# matching /model/allowed/project — a Project Admin doesn't hold model:manage.
# ---------------------------------------------------------------------------

@model_options_router.post("/project-providers", response_model=ProviderOut, status_code=201)
async def create_project_provider_route(request: Request, body: CreateProjectProviderIn, db: AsyncSession = Depends(get_db_session)) -> ProviderOut:
    await _require_scoped(
        db, request, permission="run:create", resource_kind="project", resource_id=body.project_id,
        deny_status=404,
    )
    models: list[dict] = [m.model_dump() for m in body.models]
    if not models and body.enabled_models:
        models = [{"model_id": m} for m in body.enabled_models]
    if not models:
        raise HTTPException(status_code=422, detail="at least one model is required")

    tenant_id = _tenant_id(request)
    workspace_id = None
    for m in models:
        try:
            workspace_id = await mg.assert_project_key_allowed(tenant_id, body.project_id, body.provider, m["model_id"])
        except mg.ProjectKeyNotAllowedError as exc:
            raise HTTPException(status_code=422, detail={"code": "project_key_not_allowed", "message": str(exc)})
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    try:
        d = await mc.create_provider(
            tenant_id, provider=body.provider, display_name=body.display_name,
            api_key=body.api_key, models=models, api_base=body.api_base,
            created_by=_user_id(request), workspace_id=workspace_id, project_id=body.project_id,
        )
    except mc.DuplicateProviderNameError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except mc.InvalidModelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _to_provider_out(d)


@model_options_router.get("/project-providers", response_model=list[ProviderOut])
async def list_project_providers_route(request: Request, projectId: str, db: AsyncSession = Depends(get_db_session)) -> list[ProviderOut]:
    await _require_scoped(
        db, request, permission="run:create", resource_kind="project", resource_id=projectId,
        deny_status=404,
    )
    return [
        _to_provider_out(d)
        for d in await mc.list_providers(_tenant_id(request), project_id=projectId)
    ]


@model_options_router.delete("/project-providers/{provider_id}", status_code=204, response_model=None)
async def delete_project_provider_route(request: Request, provider_id: str, projectId: str, db: AsyncSession = Depends(get_db_session)) -> None:
    await _require_scoped(
        db, request, permission="run:create", resource_kind="project", resource_id=projectId,
        deny_status=404,
    )
    # Ownership check: this route is the ONLY way a Project Admin (no model:manage)
    # can delete a model_providers row, so it must never delete one that belongs to
    # a different project than the one just scope-checked above.
    owned = await mc.list_providers(_tenant_id(request), project_id=projectId)
    if not any(d["id"] == provider_id for d in owned):
        raise HTTPException(status_code=404, detail="Provider not found")
    try:
        await mc.delete_provider(_tenant_id(request), provider_id)
    except mc.ProviderNotFoundError:
        raise HTTPException(status_code=404, detail="Provider not found")


# ---------------------------------------------------------------------------
# Model-provider grants (which Business Unit may USE which onboarded provider) —
# same integration_grants rows Task 2's generic POST/DELETE /integrations/access
# routes write (kind='model_provider'), through a purpose-built shape mirroring
# list_connector_grants/set_connector_grants in integration_access.py. Unlike
# connectors there is no whole-policy replace mode: the Org Admin's grant UI always
# acts per business unit for providers, so PUT always takes a workspaceId.
# ---------------------------------------------------------------------------

@model_router.get("/providers/grants")
async def list_model_provider_grants_route(request: Request, db: AsyncSession = Depends(get_db_session)) -> list[dict]:
    """Which providers are granted to which business units, as {provider, businessUnitIds[]}."""
    tenant_id = _tenant_id(request)
    rows = (
        await db.execute(
            text(
                "SELECT target_ref, workspace_id FROM integration_grants "
                "WHERE tenant_id = CAST(:t AS uuid) AND kind = 'model_provider'"
            ),
            {"t": tenant_id},
        )
    ).fetchall()
    by_provider: dict[str, list[str]] = {}
    for target, ws in rows:
        by_provider.setdefault(target, []).append(str(ws))
    return [
        {"provider": p, "businessUnitIds": sorted(v)}
        for p, v in sorted(by_provider.items())
    ]


@model_router.put("/providers/grants")
async def set_model_provider_grants_route(
    request: Request, workspaceId: str, body: dict, db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Replace one business unit's whole model-provider grant set (delete-then-insert).

    Org Admin only — a Business Unit Admin holds model:manage for configuring their
    own unit's provider connections, but a unit that could grant itself use of a
    provider has no grant (same rule as connectors; see integration_access.py's
    _require_org_admin).
    """
    if not is_org_wide(request):
        raise HTTPException(
            status_code=403,
            detail="Only an Organization Admin decides which providers a business unit may use.",
        )
    tenant_id = _tenant_id(request)
    actor = _user_id(request)
    # Filtered against the presented catalog, matching set_connector_grants's own
    # _CATALOG_KINDS filter (integration_access.py) — the grantable universe must be
    # a real provider slug, not an arbitrary string a caller happens to send.
    catalog = {p["provider"] for p in catalog_providers()}
    providers = [
        p for p in (body.get("providers") or [])
        if isinstance(p, str) and p.strip() and p in catalog
    ]

    await db.execute(
        text(
            "DELETE FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
            "  AND kind = 'model_provider' AND workspace_id = CAST(:w AS uuid)"
        ),
        {"t": tenant_id, "w": workspaceId},
    )
    for p in providers:
        await db.execute(
            text(
                "INSERT INTO integration_grants "
                "  (tenant_id, kind, target_ref, workspace_id, granted_by) "
                "VALUES (CAST(:t AS uuid), 'model_provider', :r, CAST(:w AS uuid), :by)"
            ),
            {"t": tenant_id, "r": p, "w": workspaceId, "by": actor},
        )
    await db.flush()
    return await list_model_provider_grants_route(request, db=db)
