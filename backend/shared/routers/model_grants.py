"""The org → BU → project model grant cascade.

One table (`org_model_grants`) and four derived views of it:

  GET/PUT /model/allowed/org      the Organization Admin's catalogue policy
  GET     /model/allowed/bu       what one unit may use    — DERIVED
  GET     /model/allowed/project  what one project may use — DERIVED
  GET     /model/availability     the BU view plus "does anyone still owe a key?"
  GET     /model/grant-matrix     every model × every unit, for the grants screen

Only the org list is writable. A unit's entitlement is a consequence of the org's
grants, never a list its own admin curates: a BU Admin narrowing their own grant
would be indistinguishable from the Org Admin revoking it, and only one of those
should be possible. That is also why the write gate is the org_admin ROLE and not
the `model:manage` permission — a BU Admin holds model:manage for their own unit,
so permission alone would let them widen what the organization permits.

Credentials are a separate axis from grants and deliberately not on the grant row.
Granting a model with no key makes it visible and inert, not usable; the
`centrallyCredentialed` / `locallyCredentialed` flags are what tell a downstream
admin whether they still have to supply something.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.dependency import require_permission
from shared.authz.read_scope import is_org_wide
from shared.db import get_db_session

logger = logging.getLogger(__name__)

model_grants_router = APIRouter(prefix="/model")


# ── schemas ──────────────────────────────────────────────────────────────────

class OrgModelGrantIn(BaseModel):
    provider: str
    model_id: str
    credentialId: Optional[str] = None
    visibility: str = Field(default="global", pattern="^(global|specific)$")
    businessUnitIds: list[str] = Field(default_factory=list)


class OrgModelGrantOut(OrgModelGrantIn):
    pass


class GrantsIn(BaseModel):
    entries: list[OrgModelGrantIn] = Field(default_factory=list)


class ModelAllowEntryOut(BaseModel):
    provider: str
    model_id: str
    credentialId: Optional[str] = None
    credentialName: Optional[str] = None


class ModelAvailabilityOut(ModelAllowEntryOut):
    visibility: str
    centrallyCredentialed: bool
    locallyCredentialed: bool


class ModelUnitAccessOut(BaseModel):
    id: str
    name: str
    hasAccess: bool
    locallyCredentialed: bool


class ModelGrantMatrixRowOut(BaseModel):
    provider: str
    model_id: str
    credentialId: Optional[str]
    credentialName: Optional[str]
    credentialHasKey: Optional[bool]
    granted: bool
    visibility: Optional[str]
    centrallyCredentialed: bool
    units: list[ModelUnitAccessOut]


class ModelGrantMatrixOut(BaseModel):
    rows: list[ModelGrantMatrixRowOut]


# ── helpers ──────────────────────────────────────────────────────────────────

def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or ""
    if not tid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid


def _require_org_admin(request: Request, action: str = "change model grants") -> None:
    """Only an org-wide caller may set — or see the whole of — the catalogue policy."""
    if not is_org_wide(request):
        raise HTTPException(
            status_code=403,
            detail=f"Only an Organization Admin can {action}.",
        )


async def _load_grants(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(
        text(
            "SELECT provider, model_id, credential_id, visibility, business_unit_ids "
            "FROM org_model_grants ORDER BY provider, model_id"
        )
    )).fetchall()
    return [
        {
            "provider": r.provider,
            "model_id": r.model_id,
            "credentialId": r.credential_id,
            "visibility": r.visibility,
            "businessUnitIds": [str(u) for u in (r.business_unit_ids or [])],
        }
        for r in rows
    ]


def _reaches(grant: dict, workspace_id: str | None) -> bool:
    """Mirror of the frontend's grantReaches() — one rule, stated in both tiers."""
    if grant["visibility"] == "global":
        return True
    if not workspace_id:
        return False
    return str(workspace_id) in grant["businessUnitIds"]


async def _provider_offerings(db: AsyncSession) -> dict[tuple[str, str], list[dict]]:
    """(provider, model_id) -> the connections offering it, with key + scope facts."""
    rows = (await db.execute(
        text(
            "SELECT mp.id, mp.provider, mp.display_name, mp.workspace_id, mp.secret_ref, "
            "       mp.status, mo.model_id, mo.enabled "
            "FROM model_providers mp "
            "JOIN model_offerings mo ON mo.provider_id = mp.id"
        )
    )).fetchall()
    out: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        out.setdefault((r.provider, r.model_id), []).append({
            "credentialId": str(r.id),
            "credentialName": r.display_name,
            "workspaceId": str(r.workspace_id) if r.workspace_id else None,
            # A provider may now be registered with no key at all, so naming a
            # subscription is not proof anything can run.
            "hasKey": bool(r.secret_ref),
            "enabled": bool(r.enabled),
        })
    return out


# ── org: the writable catalogue policy ───────────────────────────────────────

@model_grants_router.get(
    "/allowed/org",
    response_model=list[OrgModelGrantOut],
    dependencies=[Depends(require_permission("artifact:view"))],
)
async def get_org_grants(
    request: Request, db: AsyncSession = Depends(get_db_session)
) -> list[OrgModelGrantOut]:
    _tenant_id(request)
    return [OrgModelGrantOut(**g) for g in await _load_grants(db)]


@model_grants_router.put(
    "/allowed/org",
    response_model=list[OrgModelGrantOut],
    dependencies=[Depends(require_permission("artifact:view"))],
)
async def put_org_grants(
    request: Request, body: GrantsIn, db: AsyncSession = Depends(get_db_session)
) -> list[OrgModelGrantOut]:
    tenant_id = _tenant_id(request)
    _require_org_admin(request)

    # Replace-the-whole-set, in one transaction. The screen edits the grant list as
    # a whole, and a delete+insert makes duplicate (model, credential) pairs
    # unrepresentable rather than merely rejected by a constraint that nullable
    # credential_id would defeat anyway.
    await db.execute(text("DELETE FROM org_model_grants"))
    for entry in body.entries:
        unit_ids = entry.businessUnitIds if entry.visibility == "specific" else []
        try:
            parsed = [str(_uuid.UUID(u)) for u in unit_ids]
        except ValueError:
            raise HTTPException(
                status_code=422, detail="businessUnitIds must be UUIDs"
            )
        await db.execute(
            text(
                "INSERT INTO org_model_grants "
                "  (id, tenant_id, provider, model_id, credential_id, visibility, business_unit_ids) "
                "VALUES (:id, :t, :p, :m, :c, :v, CAST(:u AS uuid[]))"
            ),
            {
                "id": str(_uuid.uuid4()),
                "t": tenant_id,
                "p": entry.provider,
                "m": entry.model_id,
                "c": entry.credentialId,
                "v": entry.visibility,
                "u": parsed,
            },
        )
    # No explicit commit. get_db_session sets app.current_tenant_id with
    # set_config(..., true) — TRANSACTION-local — and commits after the handler
    # returns. Committing here would end that transaction, drop the GUC, and make
    # the read-back below return zero rows through RLS: the write succeeds and the
    # response says nothing happened.
    logger.info("org model grants replaced: %d entries (tenant=%s)", len(body.entries), tenant_id)
    return [OrgModelGrantOut(**g) for g in await _load_grants(db)]


# ── business unit + project: derived, read-only ──────────────────────────────

async def _allowed_for_workspace(db: AsyncSession, workspace_id: str) -> list[dict]:
    offerings = await _provider_offerings(db)
    out = []
    for grant in await _load_grants(db):
        if not _reaches(grant, workspace_id):
            continue
        name = None
        if grant["credentialId"]:
            for o in offerings.get((grant["provider"], grant["model_id"]), []):
                if o["credentialId"] == grant["credentialId"]:
                    name = o["credentialName"]
                    break
        out.append({
            "provider": grant["provider"],
            "model_id": grant["model_id"],
            "credentialId": grant["credentialId"],
            "credentialName": name,
        })
    return out


@model_grants_router.get(
    "/allowed/bu",
    response_model=list[ModelAllowEntryOut],
    dependencies=[Depends(require_permission("artifact:view"))],
)
async def get_bu_allowed(
    request: Request,
    workspaceId: str,
    db: AsyncSession = Depends(get_db_session),
) -> list[ModelAllowEntryOut]:
    _tenant_id(request)
    return [ModelAllowEntryOut(**e) for e in await _allowed_for_workspace(db, workspaceId)]


@model_grants_router.put(
    "/allowed/bu",
    response_model=list[ModelAllowEntryOut],
    dependencies=[Depends(require_permission("artifact:view"))],
)
async def put_bu_allowed(
    request: Request,
    workspaceId: str,
    body: GrantsIn,
    db: AsyncSession = Depends(get_db_session),
) -> list[ModelAllowEntryOut]:
    """Set what ONE unit may use — the Org Admin's per-unit control.

    Still the Org Admin's write, not the unit's: a BU Admin narrowing their own
    grant would be indistinguishable from the Org Admin revoking it.

    Expressed as membership of `specific` grants rather than a per-unit list, so
    there remains exactly one table and one rule. GLOBAL grants are deliberately
    untouched: a global grant reaches every unit by definition, and quietly
    converting it to `specific` to exclude one unit would silently rewrite the
    organization's policy for every other unit as a side effect of editing this one.
    """
    tenant_id = _tenant_id(request)
    _require_org_admin(request)
    try:
        unit = str(_uuid.UUID(workspaceId))
    except ValueError:
        raise HTTPException(status_code=422, detail="workspaceId must be a UUID")

    wanted = {(e.provider, e.model_id, e.credentialId) for e in body.entries}
    grants = await _load_grants(db)

    covered_globally = {
        (g["provider"], g["model_id"], g["credentialId"])
        for g in grants if g["visibility"] == "global"
    }

    for g in grants:
        if g["visibility"] != "specific":
            continue
        key = (g["provider"], g["model_id"], g["credentialId"])
        members = set(g["businessUnitIds"])
        if key in wanted:
            members.add(unit)
        else:
            members.discard(unit)
        await db.execute(
            text(
                "UPDATE org_model_grants SET business_unit_ids = CAST(:u AS uuid[]) "
                "WHERE provider = :p AND model_id = :m "
                "  AND credential_id IS NOT DISTINCT FROM :c AND visibility = 'specific'"
            ),
            {"u": sorted(members), "p": g["provider"], "m": g["model_id"], "c": g["credentialId"]},
        )

    existing_specific = {
        (g["provider"], g["model_id"], g["credentialId"])
        for g in grants if g["visibility"] == "specific"
    }
    for provider, model_id, credential_id in wanted:
        key = (provider, model_id, credential_id)
        if key in covered_globally or key in existing_specific:
            continue
        await db.execute(
            text(
                "INSERT INTO org_model_grants "
                "  (id, tenant_id, provider, model_id, credential_id, visibility, business_unit_ids) "
                "VALUES (:id, :t, :p, :m, :c, 'specific', CAST(:u AS uuid[]))"
            ),
            {
                "id": str(_uuid.uuid4()), "t": tenant_id, "p": provider,
                "m": model_id, "c": credential_id, "u": [unit],
            },
        )

    # A specific grant that now reaches nobody is not policy, it is litter — and it
    # would reappear in the matrix as a granted-to-no-one row.
    await db.execute(
        text(
            "DELETE FROM org_model_grants "
            "WHERE visibility = 'specific' AND cardinality(business_unit_ids) = 0"
        )
    )
    # As above: no explicit commit, or the tenant GUC goes with the transaction and
    # this read-back comes back empty.
    return [ModelAllowEntryOut(**e) for e in await _allowed_for_workspace(db, unit)]


@model_grants_router.get(
    "/allowed/project",
    response_model=list[ModelAllowEntryOut],
    dependencies=[Depends(require_permission("artifact:view"))],
)
async def get_project_allowed(
    request: Request,
    projectId: str,
    db: AsyncSession = Depends(get_db_session),
) -> list[ModelAllowEntryOut]:
    """A project inherits its unit's set whole. There is no project-level grant.

    Narrowing per project would be a fourth tier nobody administers: the Org Admin
    governs the catalogue, the unit receives it, and a project runs inside the unit.
    """
    _tenant_id(request)
    row = (await db.execute(
        text("SELECT workspace_id FROM projects WHERE id = CAST(:p AS uuid)"),
        {"p": projectId},
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return [
        ModelAllowEntryOut(**e)
        for e in await _allowed_for_workspace(db, str(row.workspace_id))
    ]


@model_grants_router.get(
    "/availability",
    response_model=list[ModelAvailabilityOut],
    dependencies=[Depends(require_permission("artifact:view"))],
)
async def get_availability(
    request: Request,
    workspaceId: str,
    db: AsyncSession = Depends(get_db_session),
) -> list[ModelAvailabilityOut]:
    """The unit's set, plus whether anyone still owes a key for each entry."""
    _tenant_id(request)
    offerings = await _provider_offerings(db)
    out: list[ModelAvailabilityOut] = []
    for grant in await _load_grants(db):
        if not _reaches(grant, workspaceId):
            continue
        serving = offerings.get((grant["provider"], grant["model_id"]), [])
        if grant["credentialId"]:
            serving = [o for o in serving if o["credentialId"] == grant["credentialId"]]
        # Central = an ORG-WIDE connection (workspace_id NULL) that actually holds a
        # key. A unit-scoped key is not central, however good it is: it leaves every
        # other unit still owing one.
        central = any(o["workspaceId"] is None and o["hasKey"] and o["enabled"] for o in serving)
        local = any(o["workspaceId"] == workspaceId and o["hasKey"] and o["enabled"] for o in serving)
        out.append(ModelAvailabilityOut(
            provider=grant["provider"],
            model_id=grant["model_id"],
            credentialId=grant["credentialId"],
            credentialName=next((o["credentialName"] for o in serving), None),
            visibility=grant["visibility"],
            centrallyCredentialed=central,
            locallyCredentialed=local,
        ))
    return out


# ── the org admin's whole-organization view ──────────────────────────────────

@model_grants_router.get(
    "/grant-matrix",
    response_model=ModelGrantMatrixOut,
    dependencies=[Depends(require_permission("artifact:view"))],
)
async def get_grant_matrix(
    request: Request, db: AsyncSession = Depends(get_db_session)
) -> ModelGrantMatrixOut:
    """Every (model, subscription) against every unit.

    Org-admin only, and not merely because the screen is theirs: this names every
    unit's standing against every model — who has what, and who has quietly keyed
    something themselves. That is the whole organization's posture in one payload,
    and it belongs to the tier that sets it.
    """
    _tenant_id(request)
    _require_org_admin(request, "view the organization-wide grant matrix")

    units = (await db.execute(
        text("SELECT id, display_name FROM workspaces ORDER BY display_name")
    )).fetchall()
    offerings = await _provider_offerings(db)
    grants = await _load_grants(db)

    # One row per (provider, model, credential): every pair a connection offers,
    # unioned with every pair already granted — a grant whose connection was deleted
    # must still appear, or it becomes invisible and un-revokable.
    keys: set[tuple[str, str, Optional[str]]] = set()
    for (provider, model_id), servers in offerings.items():
        for o in servers:
            keys.add((provider, model_id, o["credentialId"]))
    for g in grants:
        keys.add((g["provider"], g["model_id"], g["credentialId"]))

    rows: list[ModelGrantMatrixRowOut] = []
    for provider, model_id, credential_id in sorted(keys, key=lambda k: (k[0], k[1], k[2] or "")):
        serving = offerings.get((provider, model_id), [])
        offering = next((o for o in serving if o["credentialId"] == credential_id), None)
        grant = next(
            (g for g in grants
             if g["provider"] == provider
             and g["model_id"] == model_id
             and g["credentialId"] == credential_id),
            None,
        )
        central = any(o["workspaceId"] is None and o["hasKey"] and o["enabled"] for o in serving)
        rows.append(ModelGrantMatrixRowOut(
            provider=provider,
            model_id=model_id,
            credentialId=credential_id,
            credentialName=offering["credentialName"] if offering else None,
            credentialHasKey=offering["hasKey"] if offering else None,
            granted=grant is not None,
            visibility=grant["visibility"] if grant else None,
            centrallyCredentialed=central,
            units=[
                ModelUnitAccessOut(
                    id=str(u.id),
                    name=u.display_name,
                    hasAccess=bool(grant) and _reaches(grant, str(u.id)),
                    locallyCredentialed=any(
                        o["workspaceId"] == str(u.id) and o["hasKey"] and o["enabled"]
                        for o in serving
                    ),
                )
                for u in units
            ],
        ))
    return ModelGrantMatrixOut(rows=rows)
