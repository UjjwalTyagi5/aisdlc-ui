"""Model Provider (BYOK) API. Admin config endpoints are model:manage-gated;
the run-creator options endpoint is run:create-gated. Keys are never returned."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from shared.authz.dependency import require_any_permission, require_permission
from shared.authz.workspace import active_workspace_for_request
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


class UpdateProviderIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    enabled_models: list[str] | None = None


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


class SetOrgGrantsIn(BaseModel):
    entries: list[GrantEntryIn] = Field(default_factory=list)


class SetBuGrantsIn(BaseModel):
    entries: list[AllowEntryIn] = Field(default_factory=list)


class SetProjectSelectionIn(BaseModel):
    # frontend/lib/api/models.ts's setProjectModelSelection sends defaultKey (camelCase).
    model_config = ConfigDict(populate_by_name=True)

    selected: list[AllowEntryIn] = Field(default_factory=list)
    default_key: str | None = Field(default=None, alias="defaultKey")


@model_router.get("/catalog")
async def get_catalog() -> list[dict]:
    return catalog_providers()


@model_router.get("/providers", response_model=list[ProviderOut])
async def list_providers_route(request: Request, scope: str | None = None, workspaceId: str | None = None) -> list[ProviderOut]:
    ws = workspaceId or await _active_ws(request)
    return [
        _to_provider_out(d)
        for d in await mc.list_providers(_tenant_id(request), workspace_id=ws, scope=scope)
    ]


@model_router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider_route(request: Request, body: CreateProviderIn) -> ProviderOut:
    # Accept either rich `models` (with pricing) or back-compat bare `enabled_models`.
    models: list[dict] = [m.model_dump() for m in body.models]
    if not models and body.enabled_models:
        models = [{"model_id": m} for m in body.enabled_models]
    try:
        d = await mc.create_provider(
            _tenant_id(request), provider=body.provider, display_name=body.display_name,
            api_key=body.api_key, models=models, api_base=body.api_base,
            created_by=_user_id(request), workspace_id=body.workspace_id,
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
    try:
        d = await mc.update_provider(
            _tenant_id(request), provider_id,
            display_name=body.display_name, enabled_models=body.enabled_models,
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
    return await mc.get_options(
        _tenant_id(request), workspace_id=await _active_ws(request), project_id=projectId,
    )


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
async def get_bu_allowed_route(request: Request, workspaceId: str) -> list[dict]:
    entries = await mg.get_bu_allowed(_tenant_id(request), workspaceId)
    return [_to_camel(e, *_CRED_KEYS) for e in entries]


@model_router.put("/allowed/bu")
async def set_bu_grants_route(request: Request, workspaceId: str, body: SetBuGrantsIn) -> list[dict]:
    try:
        entries = await mg.set_bu_grants(
            _tenant_id(request), workspaceId, [e.model_dump() for e in body.entries],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [_to_camel(e, *_CRED_KEYS) for e in entries]


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
async def get_project_selection_route(request: Request, projectId: str) -> dict:
    try:
        selection = await mg.get_project_selection(_tenant_id(request), projectId)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _camel_selection(selection)


@model_options_router.put("/allowed/project")
async def set_project_selection_route(request: Request, projectId: str, body: SetProjectSelectionIn) -> dict:
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
